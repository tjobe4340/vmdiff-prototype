"""Diagnostics use read-only input mounts and keep failures separate from caches."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('memory_diagnose', ROOT / 'memory-processing/diagnose.py')
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class DiagnosticsTests(unittest.TestCase):
    def test_checks_both_dumps_after_failure_with_read_only_mount(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vm = root / 'VM with spaces'
            vm.mkdir()
            for name in ('before.vmem', 'before.vmsn', 'after.vmem'):
                (vm / name).write_bytes(b'test')
            commands = []
            def run(command, **kwargs):
                commands.append(command)
                kwargs['stdout'].write('plugin diagnostics\n')
                return subprocess.CompletedProcess(command, 1 if len(commands) == 1 else 0)
            with patch.object(doctor, 'PROJECT_DIR', root), patch.object(doctor, 'find_compose', return_value=['docker', 'compose']), patch.object(doctor.subprocess, 'run', side_effect=run), patch.object(sys, 'argv', ['diagnose.py', str(vm), 'before.vmem', 'after.vmem']), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(doctor.main(), 1)
            self.assertEqual(len(commands), 2)
            for command in commands:
                self.assertIn(str(vm) + ':/snapshots:ro', command)
                self.assertIn('--rm', command)
                self.assertIn('vmdiff-volatility3', command)
                self.assertEqual(command[-1], 'windows.info.Info')
            logs = list((root / 'results/memory-diagnostics').glob('*/*.log'))
            self.assertEqual(len(logs), 2)
            self.assertIn('before.vmsn', output.getvalue())
            self.assertIn('NOT FOUND', output.getvalue())
            self.assertTrue(any('Command exit status: 1' in log.read_text() for log in logs))

    def test_rejects_path_outside_snapshot_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vm = root / 'vm'
            vm.mkdir()
            (root / 'outside.vmem').write_bytes(b'test')
            with patch.object(sys, 'argv', ['diagnose.py', str(vm), '../outside.vmem']), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                doctor.main()
            self.assertEqual(raised.exception.code, 2)
