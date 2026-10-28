"""Run the real POSIX entrypoint; only the external Volatility program is faked."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MemoryScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        binary = self.root / 'volatility3'
        binary.write_text('#!/bin/sh\necho call >> "$CALL_LOG"\nprintf "%s" "${PLUGIN_OUTPUT:-[]}"\nexit "${PLUGIN_EXIT:-0}"\n')
        binary.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'],
                        CALL_LOG=str(self.root / 'calls'), RESULTS_DIR=str(self.root),
                        SNAPSHOT_DIR=str(self.root), MEMORY_PLUGINS='windows.pslist.PsList',
                        FROM_MEMORY_IMAGE_FILENAME='before.vmem', TO_MEMORY_IMAGE_FILENAME='after.vmem',
                        VMDIFF_MEMORY_RUN_ID='memory-v2-test', USE_CACHE='True')
        self.outputs = self.root / 'memory/memory-v2-test'

    def run_script(self, **options):
        return subprocess.run(['sh', str(ROOT / 'memory-processing/memdiff.sh')],
                              cwd=self.root, env=dict(self.env, **options),
                              capture_output=True, text=True)

    def calls(self):
        return len((self.root / 'calls').read_text().splitlines())

    def test_plugin_failure_propagates_and_is_not_cached(self):
        result = self.run_script(PLUGIN_EXIT='23', PLUGIN_OUTPUT='not json')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.outputs.glob('*.json')), [])
        self.assertEqual(list(self.outputs.glob('*.tmp.*')), [])

    def test_invalid_json_and_non_array_results_are_rejected(self):
        for value in ('not json', 'null', '{}'):
            result = self.run_script(PLUGIN_OUTPUT=value)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(self.outputs.glob('*.json')), [])

    def test_cache_reuse_and_no_cache_refresh(self):
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.calls(), 2)
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.calls(), 2)
        self.assertEqual(self.run_script(USE_CACHE='False').returncode, 0)
        self.assertEqual(self.calls(), 4)
        self.assertEqual(self.run_script(VMDIFF_MEMORY_RUN_ID='memory-v2-other').returncode, 0)
        self.assertEqual(self.calls(), 6)

    def test_invalid_existing_cache_is_reprocessed(self):
        self.assertEqual(self.run_script().returncode, 0)
        (self.outputs / 'from-windows.pslist.PsList.json').write_text('corrupt')
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.calls(), 3)

    def test_failed_refresh_does_not_leave_old_success(self):
        self.assertEqual(self.run_script().returncode, 0)
        self.assertNotEqual(self.run_script(USE_CACHE='False', PLUGIN_EXIT='23').returncode, 0)
        self.assertFalse((self.outputs / 'from-windows.pslist.PsList.json').exists())
