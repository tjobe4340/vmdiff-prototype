"""Real filesystem/cache and Flask route regressions; no VM drivers required."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from diffcache import DiffCache
from unified_diff import UnifiedDiff


def diff(label, is_dir=False):
    return UnifiedDiff(['diff --git x x\n', '--- x\n', '+++ x\n',
                        '@@ -1 +1 @@\n', '-' + label + '\n', '+new\n'], is_dir)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = DiffCache(self.root / 'disk', self.root / 'tree', self.root / 'memory')

    def test_guest_paths_cannot_escape_cache(self):
        outside = self.root / 'outside.txt'
        outside.write_text('first\nsecond\n')
        for key in ('/../outside.txt', '/folder/../../outside.txt', '\\..\\outside.txt', '', None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.cache.get_diff(key)
        with self.assertRaises(ValueError):
            self.cache.cache_results({'/../outside.txt': diff('bad')})
        self.assertEqual(outside.read_text(), 'first\nsecond\n')

    def test_symlink_escape_is_rejected(self):
        self.cache.run_path.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (self.cache.run_path / 'entries').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.cache.get_diff('/file')
        with self.assertRaises(ValueError):
            self.cache.cache_results({'/file': diff('bad')})

    def test_path_transitions_and_reserved_guest_names_do_not_collide(self):
        expected = {'/x': diff('parent'), '/x/child': diff('child'),
                    '/dir': diff('directory', True),
                    '/dir/.__this_directory__': diff('real guest file'), '/': diff('root', True)}
        self.cache.cache_results(expected)
        for key, value in expected.items():
            actual = self.cache.get_diff(key)
            self.assertEqual(actual.diff_lines, value.diff_lines)
            self.assertEqual(actual.is_dir, value.is_dir)
        self.cache.cache_tree(SimpleNamespace(get_tree=lambda: [], get_children_map=lambda: {}))
        self.cache.mark_complete()
        self.assertEqual(set(self.cache.get_cached_results()), set(expected))

    def test_windows_rooted_paths_round_trip(self):
        self.cache.cache_results({'\\Windows\\file': diff('windows')})
        self.assertEqual(self.cache.get_diff('/Windows/file').diff_lines, diff('windows').diff_lines)

    def test_regeneration_removes_old_entries(self):
        self.cache.cache_results({'/old': diff('old')})
        self.cache.cache_process_results({'123': diff('process')})
        self.cache.cache_results({})
        self.cache.cache_process_results({})
        self.assertIsNone(self.cache.get_diff('/old'))
        self.assertIsNone(self.cache.get_diff('123'))

    def test_empty_or_corrupt_cache_is_not_a_diff(self):
        self.cache.cache_results({'/file': diff('test')})
        path = self.cache._disk_path('/file')
        for data in ('', '{}', '{"path":"/file", "diff_lines":[]}'):
            path.write_text(data)
            self.assertIsNone(self.cache.get_diff('/file'))
        self.cache.run_process_path.mkdir()
        (self.cache.run_process_path / '123').write_text('')
        self.assertIsNone(self.cache.get_diff('123'))

    def test_disabled_process_cache_and_partial_tree(self):
        cache = DiffCache(self.root / 'disk', self.root / 'tree')
        self.assertIsNone(cache.run_process_path)
        self.assertFalse(cache.process_cache_exists())
        self.assertIsNone(cache.get_diff('123'))
        cache.tree_path.mkdir()
        (cache.tree_path / 'tree.json').write_text('[]')
        self.assertFalse(cache.tree_cache_exists())


class ViewerRouteTests(unittest.TestCase):
    setUp = CacheTests.setUp
    def load_server(self, complete=True):
        self.cache.cache_tree(SimpleNamespace(get_tree=lambda: [], get_children_map=lambda: {'/': []}))
        if complete:
            self.cache.mark_complete()
        config = SimpleNamespace(dev=False, REACT_BUILD_DIR=str(self.root),
                                 RUN_TREE_PATH=str(self.cache.tree_path),
                                 RUN_DISK_PATH=str(self.cache.run_path),
                                 RUN_MEMORY_PATH=str(self.cache.run_process_path))
        spec = importlib.util.spec_from_file_location('test_viewer_server', ROOT / 'server.py')
        server = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"config": config, spec.name: server}):
            spec.loader.exec_module(server)
        return server

    def test_routes_validate_input_and_do_not_allow_cross_origin_reads(self):
        server = self.load_server()
        (self.root / 'outside.txt').write_text('first\nsecond\n')
        with server.app.test_client() as client:
            for route in ('/diff', '/diff?key=', '/children', '/children?key='):
                self.assertEqual(client.get(route).status_code, 400)
            self.assertEqual(client.get('/children?key=unknown').status_code, 404)
            response = client.get('/diff?key=/../outside.txt')
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('Access-Control-Allow-Origin', response.headers)
            self.assertEqual(client.get('/diff?key=/').get_json(), None)
            self.assertEqual(client.get('/children?key=/').get_json(), [])

    def test_viewer_rejects_unmarked_results_after_interrupted_generation(self):
        with self.assertRaisesRegex(RuntimeError, 'missing or incomplete'):
            self.load_server(complete=False)
