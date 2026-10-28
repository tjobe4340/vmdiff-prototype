"""Real lister, path specs, differ, tree and cache; native disk reads mocked."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_partition_selection import backend
from test_disk_diff import Entry, diskdiff
from dfvfs.path import factory as path_factory
import diffcache
import diff_tree
with patch.dict(sys.modules, {'dfvfs.helpers.volume_scanner': backend.volume_scanner}):
    import volume_identity

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('multi_lister', ROOT / 'backend/file_entry_lister.py')
lister = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'dfvfs.helpers.volume_scanner': backend.volume_scanner}):
    spec.loader.exec_module(lister)
lister.volume_identity = volume_identity

A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'
C = '33333333-3333-4333-8333-333333333333'


def base(index, disk='before', fs='TSK'):
    root = path_factory.Factory.NewPathSpec('OS', location='/' + disk + '.vmdk')
    image = path_factory.Factory.NewPathSpec('VMDK', parent=root)
    partition = path_factory.Factory.NewPathSpec('GPT', location='/p' + str(index), parent=image)
    return path_factory.Factory.NewPathSpec(fs, location='/', parent=partition)


def volume_table(mapping):
    volumes = {name: SimpleNamespace(GetAttribute=lambda _, guid=guid: SimpleNamespace(value=guid))
               for name, guid in mapping.items()}
    return SimpleNamespace(volume_identifiers=list(mapping), Open=Mock(),
                           GetVolumeByIdentifier=lambda name: volumes[name])


class MultiPartitionTests(unittest.TestCase):
    def setUp(self):
        self.tables = {'before': {'p1': A, 'p3': B}, 'after': {'p7': B, 'p2': A}}
        self.roots = {}
        def inventory(table_spec):
            name = table_spec.parent.parent.location.split('/')[-1].split('.')[0]
            return {'%s' % key: 'gpt-' + str(value) for key, value in self.tables[name].items()}
        p = patch.object(volume_identity, 'gpt_inventory', side_effect=inventory)
        self.inventory = p.start(); self.addCleanup(p.stop)
        p = patch.object(lister.resolver.Resolver, 'OpenFileSystem', return_value=Mock())
        p.start(); self.addCleanup(p.stop)
        p = patch.object(lister.resolver.Resolver, 'OpenFileEntry', side_effect=self.open_entry)
        p.start(); self.addCleanup(p.stop)

    def open_entry(self, path):
        disk = path.parent.parent.parent.location
        return self.roots.get((disk, path.parent.location, path.location))

    def make(self, disk, indices, reference=None, all=True, filesystem='TSK'):
        options = backend.volume_scanner.VolumeScannerOptions()
        options.partitions = ['all'] if all else ['p' + str(indices[0])]
        specs = [base(index, disk, filesystem) for index in indices]
        with patch.object(lister.FileEntryLister, 'GetBasePathSpecs', return_value=specs):
            return lister.FileEntryLister(disk, options, selection_reference=reference)

    def add_entries(self, disk, index, content=None):
        root = Entry(b'', directory=True)
        root.path_spec = base(index, disk)
        root.sub_file_entries = []
        self.roots[('/' + disk + '.vmdk', '/p' + str(index), '/')] = root
        if content is not None:
            entry = Entry(content)
            entry.path_spec = path_factory.Factory.NewPathSpec('TSK', location='/same.txt', parent=root.path_spec.parent)
            entry.sub_file_entries = []
            root.sub_file_entries.append(entry)
            self.roots[('/' + disk + '.vmdk', '/p' + str(index), '/same.txt')] = entry
        return root

    def compare(self, before, after):
        lister.AlignVolumes(before, after)
        return diskdiff.DiskDiffer(before, after, use_stat=False, use_times=False,
                                   use_attributes=False, ignore_binary=False)

    def test_entry_index_without_location_resolves_guid(self):
        spec = base(1)
        spec.parent.location = None
        spec.parent.entry_index = 0
        self.assertEqual(volume_identity.describe_base(spec), 'gpt-' + A)

    def test_ntfs_native_backslashes_list_and_lookup(self):
        before = self.make('before', [1], filesystem='NTFS')
        after = self.make('after', [2], before, filesystem='NTFS')
        lister.AlignVolumes(before, after)
        before.base_path_spec.location = '\\'
        root = Entry(b'', directory=True)
        root.path_spec = before.base_path_spec
        child = Entry(b'text')
        child.path_spec = path_factory.Factory.NewPathSpec('NTFS', location='\\Windows\\file', parent=root.path_spec.parent)
        child.sub_file_entries = []
        root.sub_file_entries = [child]
        with patch.object(lister.resolver.Resolver, 'OpenFileEntry', return_value=root):
            before.ListFileEntries()
        self.assertIn('/Volumes/gpt-' + A + '/Windows/file', before.file_entries)
        with patch.object(lister.resolver.Resolver, 'OpenFileEntry', return_value=child) as opened:
            before.GetFileEntry('/Volumes/gpt-' + A + '/Windows/file')
        self.assertEqual(opened.call_args.args[0].location, '\\Windows\\file')

    def test_same_paths_different_partitions_and_reordered_table(self):
        before = self.make('before', [1, 3]); after = self.make('after', [7, 2], before)
        self.add_entries('before', 1, b'old A'); self.add_entries('before', 3, b'old B')
        self.add_entries('after', 2, b'newer A'); self.add_entries('after', 7, b'newest B')
        differ = self.compare(before, after)
        results = differ.diff_all()
        for guid, old, new in ((A, 'old A', 'newer A'), (B, 'old B', 'newest B')):
            key = '/Volumes/gpt-' + guid + '/same.txt'
            self.assertIn('-' + old + '\n', results[key].diff_lines)
            self.assertIn('+' + new + '\n', results[key].diff_lines)
        self.assertEqual(len(results), 2)
        with tempfile.TemporaryDirectory() as directory:
            cache = diffcache.DiffCache(Path(directory)/'disk', Path(directory)/'tree')
            cache.cache_results(results)
            for key, result in results.items():
                self.assertEqual(cache.get_diff(key).diff_lines, result.diff_lines)
            tree = diff_tree.DiffTree(differ)
            self.assertIn('/Volumes/gpt-' + A, tree.nodes)
            self.assertIn('p1', tree.nodes['/Volumes/gpt-' + A]['title'])
            self.assertIn('p2', tree.nodes['/Volumes/gpt-' + A]['title'])

    def test_added_removed_empty_partitions_visible(self):
        self.tables['after'] = {'p2': A, 'p8': C}
        before = self.make('before', [1, 3]); after = self.make('after', [2, 8], before)
        for disk, indices in (('before', [1, 3]), ('after', [2, 8])):
            for index in indices: self.add_entries(disk, index)
        result = self.compare(before, after).diff_all()
        self.assertEqual(result['/Volumes/gpt-' + B].status, 'removed')
        self.assertEqual(result['/Volumes/gpt-' + C].status, 'added')

    def test_same_position_new_guid_is_not_compared_as_same_partition(self):
        self.tables['after'] = {'p1': C}
        before = self.make('before', [1]); after = self.make('after', [1], before)
        self.add_entries('before', 1, b'before'); self.add_entries('after', 1, b'after')
        result = self.compare(before, after).diff_all()
        self.assertEqual(result['/Volumes/gpt-' + A + '/same.txt'].status, 'removed')
        self.assertEqual(result['/Volumes/gpt-' + C + '/same.txt'].status, 'added')

    def test_lookup_never_falls_back_to_another_partition(self):
        before = self.make('before', [1, 3]); after = self.make('after', [7, 2], before)
        self.compare(before, after)
        self.add_entries('before', 3, b'not on A')
        self.assertIsNone(before.GetFileEntry('/Volumes/gpt-' + A + '/same.txt'))
        self.assertIsNotNone(before.GetFileEntry('/Volumes/gpt-' + B + '/same.txt'))
        self.assertIsNone(before.GetFileEntry('/Volumes/gpt-' + C + '/same.txt'))

    def test_explicit_selection_follows_guid_not_index(self):
        before = self.make('before', [3], all=False)
        after = self.make('after', [7, 2], before)
        self.assertEqual(set(after.base_by_identity), {'gpt-' + B})
        self.compare(before, after)
        self.assertTrue(before._namespace and after._namespace)
        node = SimpleNamespace(type_indicator='GPT', path_spec=base(7, 'after').parent)
        self.assertEqual(after._GetPartitionIdentifiers(node, Mock()), ['p7'])

    def test_all_independently_includes_new_target_ids(self):
        self.tables['after']['p99'] = C
        before = self.make('before', [1, 3]); after = self.make('after', [7, 2, 99], before)
        node = SimpleNamespace(type_indicator='GPT', path_spec=base(7, 'after').parent)
        self.assertEqual(after._GetPartitionIdentifiers(node, Mock()), ['p7', 'p2', 'p99'])

    def test_selected_removed_partition_allows_empty_target(self):
        before = self.make('before', [3], all=False)
        self.tables['after'] = {'p2': A}
        after = self.make('after', [2], before)
        self.assertEqual(after.base_path_specs, [])
        self.add_entries('before', 3, b'gone')
        result = self.compare(before, after).diff_all()
        self.assertEqual(result['/Volumes/gpt-' + B + '/same.txt'].status, 'removed')

    def test_no_base_paths_after_selected_guid_removed(self):
        before = self.make('before', [3], all=False)
        self.tables['after'] = {'p2': A}
        options = backend.volume_scanner.VolumeScannerOptions()
        node = SimpleNamespace(type_indicator='GPT', path_spec=base(2, 'after').parent)
        def scan(instance, source, options):
            self.assertEqual(instance._GetPartitionIdentifiers(node, options), [])
            return []
        with patch.object(lister.FileEntryLister, 'GetBasePathSpecs', scan):
            after = lister.FileEntryLister('after', options, selection_reference=before)
        self.assertEqual(after.base_by_identity, {})
        self.compare(before, after)

    def test_present_but_unreadable_partition_is_not_reported_deleted(self):
        before = self.make('before', [1, 3])
        with self.assertRaisesRegex(ValueError, 'no supported/readable filesystem'):
            self.make('after', [2], before)

    def test_same_guid_filesystem_type_change_rejected(self):
        before = self.make('before', [1]); after = self.make('after', [2], before, filesystem='EXT')
        with self.assertRaisesRegex(ValueError, 'Filesystem type changed'):
            lister.AlignVolumes(before, after)

    def test_multiple_legacy_or_nested_filesystems_rejected(self):
        specs = [path_factory.Factory.NewPathSpec('OS', location='/a'),
                 path_factory.Factory.NewPathSpec('OS', location='/b')]
        with patch.object(lister.FileEntryLister, 'GetBasePathSpecs', return_value=specs):
            with self.assertRaisesRegex(ValueError, 'nested filesystems'):
                lister.FileEntryLister('source', backend.volume_scanner.VolumeScannerOptions())

    def test_single_legacy_paths_unchanged(self):
        spec = path_factory.Factory.NewPathSpec('OS', location='/')
        with patch.object(lister.FileEntryLister, 'GetBasePathSpecs', return_value=[spec]):
            before = lister.FileEntryLister('source', backend.volume_scanner.VolumeScannerOptions())
            after = lister.FileEntryLister('source', backend.volume_scanner.VolumeScannerOptions())
        lister.AlignVolumes(before, after)
        self.assertFalse(before._namespace)
        self.assertEqual(before._key('legacy', '/a'), '/a')

    def test_guest_filters_and_windows_paths_precede_namespace(self):
        before = self.make('before', [1]); before._namespace = True
        before.allow_dirs = ['/allowed']; before.ignore_dirs = [r'ignored\d+']
        self.assertTrue(before._ShouldListDir(SimpleNamespace(path_spec=SimpleNamespace(location='/allowed/file'))))
        self.assertFalse(before._ShouldListDir(SimpleNamespace(path_spec=SimpleNamespace(location='/allowed/ignored123'))))
        self.assertEqual(before._key('gpt-' + A, '\\Windows\\file'), '/Volumes/gpt-' + A + '/Windows/file')


class GPTMetadataTests(unittest.TestCase):
    def test_real_gpt_volume_metadata_protocol(self):
        # Load the genuine pinned GPTVolume + VolumeSystem implementation with
        # native resolver replaced. This exercises lazy GUID attribute parsing.
        import dfvfs
        from types import ModuleType
        package = ModuleType('dfvfs.volume'); package.__path__ = [str(Path(dfvfs.__file__).parent/'volume')]
        with patch.dict(sys.modules, {'dfvfs.volume': package}):
            from dfvfs.volume.gpt_volume_system import GPTVolume
        volume = GPTVolume(SimpleNamespace(name='p3', GetGPTPartition=lambda: SimpleNamespace(identifier=A, volume_offset=1048576, size=209715200)))
        self.assertEqual(volume.GetAttribute('identifier').value, A)
        table = SimpleNamespace(Open=Mock(), volume_identifiers=['p3'], GetVolumeByIdentifier=lambda _: volume)
        with patch.object(volume_identity.volume_factory.Factory, 'NewVolumeSystem', return_value=table):
            self.assertEqual(volume_identity.gpt_inventory(base(3).parent), {'p3': 'gpt-' + A})

    def test_duplicate_missing_and_invalid_guids_rejected(self):
        for mapping in ({'p1': A, 'p2': A}, {'p1': None}, {'p1': 'bad'}, {'p1': str(__import__('uuid').UUID(int=0))}):
            with self.subTest(mapping=mapping):
                with patch.object(volume_identity.volume_factory.Factory, 'NewVolumeSystem', return_value=volume_table(mapping)):
                    with self.assertRaisesRegex(ValueError, 'GUID'):
                        volume_identity.gpt_inventory(base(1).parent)
