"""Regression tests against the same dfVFS Python API as the backend image.

Install dfvfs==20220816 with --no-deps. Only native/deep I/O dependencies and
unrelated diff processing are mocked: the parser, prompt validation/retry loop,
scanner option normalization, and vmdiff.Main wiring are the real code.
"""

import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import dfvfs


ROOT = Path(__file__).resolve().parents[1]


def load_backend():
    # Avoid loading disk drivers just to exercise dfVFS's Python selection API.
    stubs = {name: Mock() for name in (
        'dfdatetime', 'dfvfs.credentials', 'dfvfs.path', 'dfvfs.volume',
        'dfvfs.resolver.resolver', 'dfvfs.helpers.source_scanner',
        'dfvfs.helpers.windows_path_resolver',
        'memdiff', 'diff_tree', 'diffcache', 'diskdiff', 'file_entry_lister',
        'config',
    )}
    stubs['config'].LOG_LEVEL = 20
    spec = importlib.util.spec_from_file_location(
        'partition_test_backend', ROOT / 'backend' / 'vmdiff.py')
    backend = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs), patch.object(sys, 'path', list(sys.path)):
        spec.loader.exec_module(backend)
    return backend


backend = load_backend()
command_line = backend.command_line
volume_scanner = backend.volume_scanner


class PartitionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.identifiers = ['p1', 'p2', 'p3', 'p4']
        self.volume_system = self.make_volume_system(self.identifiers)
        self.output = io.BytesIO()
        self.writer = command_line.FileObjectOutputWriter(self.output)
        self.scan_node = SimpleNamespace(path_spec=object(), type_indicator='GPT')
        factory_patch = patch.object(
            volume_scanner.volume_system_factory.Factory, 'NewVolumeSystem',
            side_effect=lambda _: self.volume_system)
        factory_patch.start()
        self.addCleanup(factory_patch.stop)
        source_patch = patch.object(
            volume_scanner.source_scanner, 'SourceScanner',
            side_effect=lambda: SimpleNamespace(
                GetVolumeIdentifiers=lambda system: system.volume_identifiers))
        source_patch.start()
        self.addCleanup(source_patch.stop)

    @staticmethod
    def make_volume_system(identifiers):
        volumes = {
            identifier: SimpleNamespace(
                identifier=identifier,
                extents=[SimpleNamespace(offset=index * 1024, size=1024)])
            for index, identifier in enumerate(identifiers)
        }
        return SimpleNamespace(
            number_of_volumes=len(identifiers), volume_identifiers=identifiers,
            VOLUME_IDENTIFIER_PREFIX='p', Open=Mock(),
            GetVolumeByIdentifier=lambda identifier: volumes.get(identifier))

    def mediator(self, text):
        return backend.CachingVolumeScannerMediator(
            input_reader=command_line.FileObjectInputReader(io.StringIO(text)),
            output_writer=self.writer)

    def scan(self, mediator, options):
        scanner = volume_scanner.VolumeScanner(mediator=mediator)
        return scanner._GetPartitionIdentifiers(self.scan_node, options)

    def run_main(self, text, configured=None, after_parent=None):
        """Run real Main and dfVFS selection; replace disk I/O and diff/cache work."""
        scans = []
        mediator = self.mediator(text)

        def file_lister(source, options, mediator, **kwargs):
            selected = self.scan(mediator, options)
            scans.append((list(options.partitions), selected))
            if len(scans) == 1 and after_parent:
                after_parent(mediator)
            return SimpleNamespace(file_system=SimpleNamespace(
                GetRootFileEntry=lambda: SimpleNamespace(sub_file_entries=[])))

        config_values = dict(
            PARTITION=configured, USE_CACHE=True, USE_MEMORY=False, USE_DISK=True,
            FROM_DISK_PATH='before.vmdk', TO_DISK_PATH='after.vmdk',
            ignore_dirs=[], allow_dirs=[], diff_config={},
            RUN_DISK_PATH='/results/disk', RUN_TREE_PATH='/results/tree')
        cache = Mock(run_path=Path('/results/disk'))
        cache.cache_exists.return_value = True
        with patch.multiple(backend.config, **config_values), \
                patch.object(backend, 'dump_api_data'), \
                patch.object(backend, 'CachingVolumeScannerMediator',
                             return_value=mediator), \
                patch.object(backend.file_entry_lister, 'FileEntryLister',
                             side_effect=file_lister), \
                patch.object(backend.diffcache, 'DiffCache', return_value=cache):
            self.assertIs(backend.Main(), cache)
        return scans, mediator

    def test_uses_backend_pinned_dfvfs(self):
        self.assertEqual(dfvfs.__version__, '20220816')
        self.assertIn('dfvfs==' + dfvfs.__version__,
                      (ROOT / 'backend' / 'requirements.txt').read_text())
        # These are the upstream implementations, not parser-shaped test stubs.
        self.assertEqual(command_line.__name__, 'dfvfs.helpers.command_line')
        self.assertEqual(volume_scanner.__name__, 'dfvfs.helpers.volume_scanner')

    def test_interactive_choices_are_reused_for_second_snapshot(self):
        cases = (
            ('all\n', self.identifiers),
            ('  all\r\n', self.identifiers),
            ('p3\n', ['p3']),
            ('  p3\r\n', ['p3']),
            ('3\n', ['p3']),
            ('1,3..4\n', ['p1', 'p3', 'p4']),
            ('p1,p3..p4\n', ['p1', 'p3', 'p4']),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                scans, mediator = self.run_main(text)
                self.assertEqual(scans, [([], expected), (expected, expected)])
                self.assertEqual(mediator.selected_partition_identifiers, expected)

    def test_invalid_input_retries_before_caching_success(self):
        scans, mediator = self.run_main('garbage\np99\np2..p99\np3\n')
        self.assertEqual(scans, [([], ['p3']), (['p3'], ['p3'])])
        self.assertEqual(mediator.selected_partition_identifiers, ['p3'])
        self.assertEqual(self.output.getvalue().count(
            b'Unsupported partition identifier(s)'), 3)

    def test_configured_choices_need_no_prompt(self):
        for configured, expected in (
                ('3', ['p3']), ('p3', ['p3']), (' p3\r\n', ['p3']),
                ('all', self.identifiers), ('1,3..4', ['p1', 'p3', 'p4'])):
            with self.subTest(configured=configured):
                scans, mediator = self.run_main('', configured=configured)
                self.assertEqual([selected for _, selected in scans],
                                 [expected, expected])
                self.assertIsNone(mediator.selected_partition_identifiers)
        self.assertEqual(self.output.getvalue(), b'')

    def test_invalid_configured_partition_reuses_corrected_prompt_choice(self):
        scans, mediator = self.run_main('p3\n', configured='99')
        self.assertEqual(scans, [(['p99'], ['p3']), (['p3'], ['p3'])])
        self.assertEqual(mediator.selected_partition_identifiers, ['p3'])

    def test_invalid_configured_syntax_still_raises(self):
        with self.assertRaises(ValueError):
            self.run_main('', configured='bogus')

    def test_sparse_partition_ids_keep_dfvfs_validation_behavior(self):
        self.volume_system = self.make_volume_system(['p1', 'p3'])
        scans, mediator = self.run_main('all\n')
        self.assertEqual(scans, [([], ['p1', 'p3']), (['p1', 'p3'], ['p1', 'p3'])])
        self.assertTrue(mediator.selected_all_partitions)
        self.assertEqual(self.output.getvalue().count(b'Unsupported partition identifier(s)'), 0)
        for configured in ('3', 'p3', 'all'):
            expected = ['p1', 'p3'] if configured == 'all' else ['p3']
            scans, mediator = self.run_main('', configured=configured)
            self.assertEqual([selected for _, selected in scans],
                             [expected, expected])
            self.assertIsNone(mediator.selected_partition_identifiers)

    def test_no_partition_prompt_keeps_options_unchanged(self):
        self.volume_system = self.make_volume_system(['p1'])
        scans, mediator = self.run_main('')
        self.assertEqual(scans, [([], ['p1']), ([], ['p1'])])
        self.assertIsNone(mediator.selected_partition_identifiers)
        self.assertEqual(self.output.getvalue(), b'')

    def test_later_vss_apfs_and_credential_input_does_not_replace_partition(self):
        def other_prompts(mediator):
            with patch.object(mediator, '_PrintVSSStoreIdentifiersOverview'), \
                    patch.object(mediator, '_PrintAPFSVolumeIdentifiersOverview'):
                self.assertEqual(mediator.GetVSSStoreIdentifiers(
                    self.volume_system, ['vss1', 'vss2']), ['vss2'])
                self.assertEqual(mediator.GetAPFSVolumeIdentifiers(
                    self.volume_system, ['apfs1', 'apfs2']), ['apfs1'])
            self.assertFalse(mediator.UnlockEncryptedVolume(
                Mock(), Mock(), SimpleNamespace(type_indicator='BDE'),
                SimpleNamespace(CREDENTIALS=['password'])))

        scans, mediator = self.run_main('p3\nvss2\napfs1\nskip\n',
                                        after_parent=other_prompts)
        self.assertEqual(scans, [([], ['p3']), (['p3'], ['p3'])])
        self.assertEqual(mediator.selected_partition_identifiers, ['p3'])

    def test_non_partition_input_without_partition_prompt_is_not_reused(self):
        self.volume_system = self.make_volume_system(['p1'])
        scans, mediator = self.run_main(
            'skip\n', after_parent=lambda mediator: mediator.UnlockEncryptedVolume(
                Mock(), Mock(), SimpleNamespace(type_indicator='BDE'),
                SimpleNamespace(CREDENTIALS=['password'])))
        self.assertEqual(scans, [([], ['p1']), ([], ['p1'])])
        self.assertIsNone(mediator.selected_partition_identifiers)

    def test_cached_selection_is_a_copy(self):
        mediator = self.mediator('p3\n')
        selected = mediator.GetPartitionIdentifiers(
            self.volume_system, self.identifiers)
        selected.append('p4')
        self.assertEqual(mediator.selected_partition_identifiers, ['p3'])

    def test_empty_selection_is_distinct_from_no_prompt(self):
        for text in ('\n', 'none\n'):
            with self.subTest(text=text):
                mediator = self.mediator(text)
                self.assertIsNone(mediator.selected_partition_identifiers)
                self.assertEqual(mediator.GetPartitionIdentifiers(
                    self.volume_system, self.identifiers), [])
                self.assertEqual(mediator.selected_partition_identifiers, [])

    def test_interrupt_is_not_cached(self):
        mediator = self.mediator('')
        with patch.object(mediator._input_reader, 'Read',
                          side_effect=KeyboardInterrupt), \
                self.assertRaises(volume_scanner.errors.UserAbort):
            self.scan(mediator, volume_scanner.VolumeScannerOptions())
        self.assertIsNone(mediator.selected_partition_identifiers)

    def test_second_snapshot_revalidates_cached_choice(self):
        def different_partition_table(mediator):
            self.volume_system = self.make_volume_system(['p1', 'p2'])

        scans, mediator = self.run_main('p3\np2\n',
                                        after_parent=different_partition_table)
        self.assertEqual(scans, [([], ['p3']), (['p3'], ['p2'])])
        self.assertEqual(mediator.selected_partition_identifiers, ['p2'])
        self.assertIn(b'Volume missing for identifier: p3', self.output.getvalue())


if __name__ == '__main__':
    unittest.main()
