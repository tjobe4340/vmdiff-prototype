"""Actual DiskDiffer, with byte streams replacing native filesystem I/O."""
import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from dfvfs.file_io.file_io import FileIO
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
spec = importlib.util.spec_from_file_location('disk_test_module', ROOT / 'backend/diskdiff.py')
diskdiff = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, config=SimpleNamespace()):
    spec.loader.exec_module(diskdiff)


class Entry:
    def __init__(self, contents, directory=False):
        self.contents = contents
        self.size = len(contents)
        self.attributes = []
        self.number_of_attributes = 0
        self.directory = directory
        self.path_spec = SimpleNamespace(location='/file')
        for attr in diskdiff.DiskDiffer._TIME_ATTRIBUTES:
            setattr(self, attr, None)

    def GetFileObject(self):
        return io.BytesIO(self.contents)

    def IsFile(self):
        return not self.directory

    def IsDirectory(self):
        return self.directory

    def GetStatAttribute(self):
        return SimpleNamespace(mode=0o100644, type='file', owner_identifier=0, group_identifier=0)


class Lister:
    def __init__(self, entry):
        self.file_entries = {'/file': entry} if entry is not None else {}

    def GetFileEntry(self, path):
        return self.file_entries.get(path)

    def ListFileEntries(self):
        pass


def differ(before, after, **options):
    defaults = dict(use_stat=False, use_times=False, use_attributes=False, ignore_binary=False)
    defaults.update(options)
    return diskdiff.DiskDiffer(Lister(before), Lister(after), **defaults)


class DiskDiffTests(unittest.TestCase):
    def test_include_binary_still_produces_text_diffs(self):
        result = differ(Entry(b'old'), Entry(b'newer')).diff_all()['/file']
        self.assertIn('-old\n', result.diff_lines)
        self.assertIn('+newer\n', result.diff_lines)

    def test_binary_equality_changes_additions_and_deletions(self):
        for before, after in ((Entry(b'\0old'), Entry(b'\0newer')),
                              (None, Entry(b'\0new')), (Entry(b'\0old'), None)):
            with self.subTest(before=before, after=after):
                result = differ(before, after).diff_all()['/file']
                self.assertIn(' Binary files differ\n', result.diff_lines)
        self.assertIsNone(differ(Entry(b'\0same'), Entry(b'\0same')).diff('/file'))

    def test_mixed_text_and_binary_diff(self):
        lines = differ(Entry(b'old'), Entry(b'\0new')).diff_all()['/file'].diff_lines
        self.assertIn(' Binary files differ\n', lines)
        lines = differ(Entry(b'<Binary file>'), Entry(b'\0different binary')).diff_all()['/file'].diff_lines
        self.assertIn(' Binary files differ\n', lines)

    def test_default_binary_exclusion_unchanged(self):
        self.assertEqual(differ(Entry(b'old'), Entry(b'\0new'), ignore_binary=True).diff_all(), {})

    def test_utf8_sampling_boundary_is_not_binary(self):
        for prefix in (509, 510, 511, 512):
            entry = Entry(b'a' * prefix + '€'.encode() + b' tail')
            self.assertFalse(differ(entry, entry)._is_binary(entry))
        for data in (b'invalid\xff', b'truncated\xe2', b'a' * 511 + b'\xe2'):
            entry = Entry(data)
            self.assertTrue(differ(entry, entry)._is_binary(entry))

    def test_unavailable_timestamps_are_displayable(self):
        entry = Entry(b'new')
        entry.modification_time = SimpleNamespace(CopyToDateTimeStringISO8601=lambda: '2026-01-01')
        result = differ(None, entry, show_times=True).diff('/file')
        self.assertTrue(any('added_time: <unavailable>' in line for line in result.diff_lines))
        self.assertTrue(any('modification_time: 2026-01-01' in line for line in result.diff_lines))

    def test_read_errors_fail_explicitly_instead_of_becoming_equality(self):
        entry = Entry(b'\0data')
        for exception in (OSError('unreadable'), diskdiff.dfvfs_errors.BackEndError('unreadable')):
            with patch.object(entry, 'GetFileObject', side_effect=exception):
                with self.assertRaisesRegex(RuntimeError, 'Unable to open file contents'):
                    differ(entry, entry).diff('/file')
                with self.assertRaises(RuntimeError):
                    differ(entry, entry)._compare_binaries(entry, entry)

    def test_real_dfvfs_fileio_contract_and_reused_stream(self):
        class CachedFileIO(FileIO):
            def __init__(self):
                super().__init__(None, object())
                self.data = io.BytesIO(b'text')
            def read(self, size=None):
                return self.data.read(size)
            def seek(self, offset, whence=0):
                return self.data.seek(offset, whence)
            def _Close(self):
                self.data.close()
            def _Open(self, mode='rb'):
                pass
            def get_offset(self):
                return self.data.tell()
            def get_size(self):
                return 4
        stream = CachedFileIO()
        self.assertFalse(hasattr(stream, 'close'))
        entry = Entry(b'text')
        comparator = differ(entry, entry)
        with patch.object(entry, 'GetFileObject', return_value=stream):
            self.assertFalse(comparator._is_binary(entry))
            self.assertEqual(comparator.get_contents_sequence(entry), ['text\n'])
            self.assertTrue(comparator._compare_binaries(entry, entry))
            self.assertEqual(stream.tell(), 0)
            self.assertFalse(stream.data.closed)

    def test_empty_change_set_does_not_relist_disks(self):
        comparator = differ(Entry(b'same'), Entry(b'same'))
        with patch.object(comparator.a_file_lister, 'ListFileEntries') as before, \
                patch.object(comparator.b_file_lister, 'ListFileEntries') as after:
            self.assertEqual(comparator.get_changed_files(), set())
            self.assertEqual(comparator.diff_all(), {})
        before.assert_called_once()
        after.assert_called_once()

    def test_streamless_ntfs_file_is_not_a_read_error(self):
        entry = Entry(b'')
        with patch.object(entry, 'GetFileObject', return_value=None):
            comparator = differ(entry, entry)
            self.assertFalse(comparator._is_binary(entry))
            self.assertEqual(comparator.get_contents_sequence(entry), [])
            self.assertTrue(comparator._compare_binaries(entry, entry))

    def test_unknown_size_is_supported_with_a_bounded_content_read(self):
        entry = Entry(b'newer')
        entry.size = None
        self.assertIsNotNone(differ(Entry(b'old'), entry).diff('/file'))
        entry.contents = b'a' * (diskdiff.DiskDiffer.MAX_SIZE + 1)
        with self.assertRaisesRegex(RuntimeError, 'exceed the diff limit'):
            differ(None, entry).diff('/file')
