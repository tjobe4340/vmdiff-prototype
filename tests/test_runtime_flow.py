"""Real orchestration/cache/tree/memory functions with native disk I/O replaced."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_partition_selection import load_backend
import diffcache
import diff_tree
import memdiff

ROOT = Path(__file__).resolve().parents[1]


class RuntimeFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = load_backend()
        self.backend.diffcache = diffcache
        self.backend.diff_tree = diff_tree
        self.backend.memdiff = memdiff
        self.backend.config = SimpleNamespace(
            USE_DISK=False, USE_MEMORY=True, USE_CACHE=True,
            PARTITION='', RUN_DISK_PATH=str(self.root / 'run/disk'),
            RUN_MEMORY_PATH=str(self.root / 'run/memory'),
            RUN_TREE_PATH=str(self.root / 'run/tree'),
            RESULTS_DIR=str(self.root), MEMORY_RUN_ID='raw-memory',
            MEMORY_PLUGINS=['windows.pslist.PsList', 'windows.envars.Envars', 'windows.cmdline.CmdLine'],
            IGNORE_PROCESSES_REGEX='')
        path = self.root / 'memory/raw-memory'
        path.mkdir(parents=True)
        self.proc = {'PID': 4, 'PPID': 0, 'ImageFileName': 'System', 'Threads': 1}
        for name in self.backend.config.MEMORY_PLUGINS:
            (path / ('from-' + name + '.json')).write_text('[]')
            (path / ('to-' + name + '.json')).write_text(json.dumps([self.proc] if 'pslist' in name else []))

    def test_memory_only_generates_and_reuses_complete_cache_without_disk(self):
        cache = self.backend.Main()
        self.backend.file_entry_lister.FileEntryLister.assert_not_called()
        self.assertFalse(cache.run_path.exists())
        self.assertTrue(cache.process_cache_exists())
        tree, children = cache.get_tree_data_from_cache()
        self.assertEqual(tree[0]['key'], 'Processes')
        self.assertIsNotNone(cache.get_diff('4'))
        # Every selectable parent also has valid static diff JSON.
        for key in children:
            digest = hashlib.sha1(key.encode()).hexdigest()
            self.assertTrue((cache.tree_path / 'json/diff' / digest).is_file())
        with patch.object(self.backend, 'load_memory_results', side_effect=AssertionError('cache miss')):
            self.backend.Main()
        self.backend.file_entry_lister.FileEntryLister.assert_not_called()

    def test_failed_generation_invalidates_cache_before_work(self):
        cache = self.backend.Main()
        self.backend.config.USE_CACHE = False
        with patch.object(self.backend, 'dump_api_data', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.backend.Main()
        self.assertFalse(cache.tree_cache_exists())

    def test_no_cache_replaces_static_data_and_processes(self):
        cache = self.backend.Main()
        stale = cache.tree_path / 'json/diff/stale'
        stale.write_text('stale')
        (self.root / 'memory/raw-memory/to-windows.pslist.PsList.json').write_text('[]')
        self.backend.config.USE_CACHE = False
        self.backend.Main()
        self.assertFalse(stale.exists())
        self.assertIsNone(cache.get_diff('4'))

    def test_shared_atomic_json_is_readable_by_viewer_user(self):
        cache = self.backend.Main()
        for path in cache.tree_path.rglob('*'):
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_optional_memory_plugins_are_not_required(self):
        self.backend.config.MEMORY_PLUGINS = ['windows.pslist.PsList']
        self.assertIsNotNone(self.backend.Main().get_diff('4'))

    def test_tree_uses_existing_diffs_without_mutating_differ(self):
        from test_diff_cache import diff
        comparator = SimpleNamespace(diff_type='disk', diffs={'/dir/file': diff('file')}, diff=Mock(side_effect=AssertionError('must not rediff parents')))
        tree = diff_tree.DiffTree(comparator)
        self.assertEqual(tree.get_tree()[0]['key'], '/')
        self.assertEqual(set(comparator.diffs), {'/dir/file'})
        comparator.diff.assert_not_called()


class MemoryDifferTests(unittest.TestCase):
    @staticmethod
    def process(name='app.exe'):
        return {'PID': 10, 'PPID': 0, 'ImageFileName': name, 'Threads': 3}

    def test_absent_process_is_an_empty_line_sequence(self):
        self.assertEqual(memdiff.MemoryDiffer([], [])._to_string(None), [])

    def test_optional_aux_data_and_input_reuse(self):
        before = [self.process()]
        self.assertEqual(memdiff.MemoryDiffer(before, before).diff_all(), {})
        self.assertEqual(before[0]['Threads'], 3)
        self.assertEqual(memdiff.MemoryDiffer(before, before).diff_all(), {})

    def test_missing_null_one_sided_and_orphan_aux_rows(self):
        for args in (None, 'app --new'):
            comparator = memdiff.MemoryDiffer([], [self.process()],
                from_envars=[], to_envars=[{'PID': 10, 'Variable': 'A', 'Value': 'B'}, {'PID': 999, 'Variable': 'orphan', 'Value': 'value'}],
                from_cmdline=[], to_cmdline=[{'PID': 10, 'Args': args}, {'PID': 999, 'Args': 'orphan'}])
            self.assertIn('10', comparator.diff_all())
            self.assertEqual(comparator.to_procs['10']['EnvironmentVariables'], {'A': 'B'})
            self.assertEqual(comparator.to_procs['10']['CommandLine'], args)

    def test_hyphenated_process_name_filter(self):
        comparator = memdiff.MemoryDiffer([], [self.process('my-app.exe')], ignore_regex='my-app')
        self.assertEqual(comparator.diff_all(), {})


class ConfigIdentityTests(unittest.TestCase):
    def config(self, overrides=None):
        env = dict(MEMORY_PLUGINS='windows.pslist.PsList', SNAPSHOT_DIR='/snapshots',
                   RESULTS_DIR='/results', REACT_BUILD_DIR='/frontend', USE_DISK='True',
                   USE_MEMORY='True', USE_CACHE='True', FROM_DISK_IMAGE_FILENAME='before.vmdk',
                   TO_DISK_IMAGE_FILENAME='after.vmdk', FROM_MEMORY_IMAGE_FILENAME='before.vmem',
                   TO_MEMORY_IMAGE_FILENAME='after.vmem', PARTITION_IDENTIFIER='1',
                   DIFF_USE_TIMES='True', DIFF_USE_CONTENTS='False')
        env.update(overrides or {})
        spec = importlib.util.spec_from_file_location('runtime_config_test', ROOT / 'config.py')
        config = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, env, clear=True):
            spec.loader.exec_module(config)
        return config

    def test_named_options_modes_sources_and_filters_have_distinct_keys(self):
        baseline = self.config().RUN_ID
        variants = [dict(PARTITION_IDENTIFIER='2'), dict(USE_MEMORY='False'),
                    dict(FROM_MEMORY_IMAGE_FILENAME='other.vmem'),
                    dict(IGNORE_PROCESSES_REGEX='ignored'), dict(SNAPSHOT_DIR='/another-vm'),
                    dict(DIFF_USE_TIMES='False', DIFF_USE_CONTENTS='True'),
                    dict(FILTER_PATH_JSON='["a", "bc"]'), dict(IGNORE_PATH_JSON='["abc"]'),
                    dict(VMDIFF_SOURCE_ID='different')]
        for values in variants:
            with self.subTest(values=values):
                self.assertNotEqual(baseline, self.config(values).RUN_ID)
        self.assertNotEqual(self.config({'FILTER_PATH_JSON': '["a", "bc"]'}).RUN_ID,
                            self.config({'FILTER_PATH_JSON': '["ab", "c"]'}).RUN_ID)

    def test_run_id_is_not_a_snapshot_path(self):
        config = self.config({'FROM_DISK_IMAGE_FILENAME': '/../../outside'})
        self.assertRegex(config.RUN_ID, r'^v3-[a-f0-9]{24}$')
        self.assertTrue(config.RUN_PATH.startswith('/results/v3-'))

    def test_false_dev_flag_does_not_switch_paths(self):
        self.assertEqual(self.config({'VMDIFF_DEV': 'False'}).RESULTS_DIR, '/results')
