"""Memory extractor upgrade invalidates caches and uses HTTPS symbol requests."""
import ast
import hashlib
import json
from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class MemoryVersioningTests(unittest.TestCase):
    def test_wrapper_sets_https_before_cli_main(self):
        framework = ModuleType('volatility3.framework')
        framework.constants = SimpleNamespace(SYMBOL_SERVER_URL='http://old')
        cli = ModuleType('volatility3.cli')
        seen = []
        cli.main = lambda: seen.append(framework.constants.SYMBOL_SERVER_URL)
        with patch.dict(sys.modules, {'volatility3': ModuleType('volatility3'), 'volatility3.framework': framework, 'volatility3.cli': cli}):
            runpy.run_path(str(ROOT / 'memory-processing/volatility_cli.py'), run_name='__main__')
        self.assertEqual(seen, ['https://msdl.microsoft.com/download/symbols'])

    def test_derived_memory_cache_tracks_extractor_version(self):
        tree = ast.parse((ROOT / 'config.py').read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'get_run_id')
        module = ast.Module(body=[function], type_ignores=[])
        namespace = dict(json=json, hashlib=hashlib, SOURCE_ID='source', USE_DISK=False,
                         FROM_DISK_IMAGE_FILENAME='', TO_DISK_IMAGE_FILENAME='', USE_MEMORY=True,
                         FROM_MEMORY_IMAGE_FILENAME='a.vmem', TO_MEMORY_IMAGE_FILENAME='b.vmem',
                         PARTITION='all', diff_config={}, allow_dirs=[], ignore_dirs=[],
                         IGNORE_PROCESSES_REGEX='', MEMORY_PLUGINS=['windows.pslist.PsList'])
        exec(compile(module, str(ROOT / 'config.py'), 'exec'), namespace)
        original_dumps = json.dumps
        captured = []
        def dumps(value, **kwargs):
            captured.append(dict(value))
            return original_dumps(value, **kwargs)
        with patch.object(json, 'dumps', side_effect=dumps):
            namespace['get_run_id']()
            namespace['USE_MEMORY'] = False
            namespace['get_run_id']()
        self.assertEqual(captured[0]['memory_extractor'], 'volatility3-2.28.2')
        self.assertNotIn('memory_extractor', captured[1])

    def test_raw_cache_version_agrees_across_entrypoints(self):
        for name in ('vmdiff', 'config.py', 'memory-processing/memdiff.sh'):
            source = (ROOT / name).read_text()
            self.assertIn('memory-v3-', source)
            self.assertNotIn('memory-v2-', source)
