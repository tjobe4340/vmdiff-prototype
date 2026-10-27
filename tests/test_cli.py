import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import typer
from typer.testing import CliRunner

import compose_runtime

loader = importlib.machinery.SourceFileLoader("vmdiff_cli", str(compose_runtime.PROJECT_DIR / "vmdiff"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)
app = typer.Typer()
app.command()(cli.main)


class ComposeTests(unittest.TestCase):
    @patch("compose_runtime.subprocess.run")
    def test_prefers_plugin(self, run):
        run.return_value.returncode = 0
        self.assertEqual(compose_runtime.find_compose(), ["docker", "compose"])
        self.assertEqual(run.call_count, 1)

    @patch("compose_runtime.subprocess.run")
    def test_legacy_fallback(self, run):
        run.side_effect = [subprocess.CompletedProcess([], 125), subprocess.CompletedProcess([], 0)]
        self.assertEqual(compose_runtime.find_compose(), ["docker-compose"])

    @patch("compose_runtime.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_compose_has_actionable_error(self, run):
        with self.assertRaisesRegex(RuntimeError, "docker compose version"):
            compose_runtime.find_compose()

    @patch("compose_runtime.subprocess.run")
    def test_timeout_falls_back(self, run):
        run.side_effect = [subprocess.TimeoutExpired([], 15), subprocess.CompletedProcess([], 0)]
        self.assertEqual(compose_runtime.find_compose(), ["docker-compose"])

    @patch("compose_runtime.subprocess.run")
    def test_project_cwd_and_no_shell(self, run):
        command = ["docker", "compose", "up", "app"]
        compose_runtime.run_compose(command, {"TEST": "value"})
        run.assert_called_once_with(command, cwd=compose_runtime.PROJECT_DIR, env={"TEST": "value"}, check=True)


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="VM snapshots & ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for filename in ("before.vmdk", "after.vmdk", "before.vmem", "after.vmem"):
            (self.root / filename).touch()
        self.runner = CliRunner()

    def disk_args(self):
        return [str(self.root), "--from-disk", str(self.root / "before.vmdk"), "--to-disk", str(self.root / "after.vmdk"), "--no-use-memory"]

    @patch.object(cli, "find_compose", return_value=["docker", "compose"])
    @patch.object(cli, "run_compose")
    def test_disk_only_preserves_path_as_single_argument(self, run, find):
        result = self.runner.invoke(app, self.disk_args())
        self.assertEqual(result.exit_code, 0, result.output)
        command, env = run.call_args.args
        self.assertEqual(command, ["docker", "compose", "run", "-v", f"{self.root}:/snapshots", "vmdiff"])
        self.assertEqual(env["FROM_MEMORY_IMAGE_FILENAME"], "")

    @patch.object(cli, "find_compose", return_value=["docker", "compose"])
    @patch("compose_runtime.subprocess.run")
    def test_relative_input_from_outside_repository(self, run, find):
        original_cwd = Path.cwd()
        # Launch from outside the checkout with paths relative to that directory.
        # Exercise the real run_compose helper, mocking only the OS process launch.
        try:
            os.chdir(self.root.parent)
            relative_root = Path(self.root.name)
            result = self.runner.invoke(app, [
                str(relative_root),
                "--from-disk", str(relative_root / "before.vmdk"),
                "--to-disk", str(relative_root / "after.vmdk"),
                "--no-use-memory",
            ])
        finally:
            os.chdir(original_cwd)
        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], [
            "docker", "compose", "run", "-v",
            f"{self.root.resolve()}:/snapshots", "vmdiff",
        ])
        self.assertEqual(run.call_args.kwargs["cwd"], compose_runtime.PROJECT_DIR)
        self.assertEqual(run.call_args.kwargs["env"]["SNAPSHOT_DIR"], self.root.resolve())
        self.assertEqual(run.call_args.kwargs["env"]["FROM_DISK_IMAGE_FILENAME"], "before.vmdk")
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch.object(cli, "find_compose", return_value=["docker-compose"])
    @patch.object(cli, "run_compose")
    def test_show_uses_selected_compose_without_env_flag(self, run, find):
        result = self.runner.invoke(app, self.disk_args() + ["--show"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(run.call_args.args[0], ["docker-compose", "up", "app"])

    @patch.object(cli, "find_compose", return_value=["docker", "compose"])
    @patch.object(cli, "run_compose")
    def test_memory_only(self, run, find):
        result = self.runner.invoke(app, [str(self.root), "--from-memory", str(self.root / "before.vmem"), "--to-memory", str(self.root / "after.vmem"), "--no-use-disk"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual([call.args[0][-1] for call in run.call_args_list], ["memdiff", "vmdiff"])
        self.assertEqual(run.call_args.args[1]["FROM_DISK_IMAGE_FILENAME"], "")

    @patch.object(cli, "find_compose", return_value=["docker", "compose"])
    @patch.object(cli, "run_compose")
    def test_disk_and_memory_run_in_order(self, run, find):
        args = self.disk_args()[:-1] + ["--from-memory", str(self.root / "before.vmem"), "--to-memory", str(self.root / "after.vmem")]
        result = self.runner.invoke(app, args)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual([call.args[0][-1] for call in run.call_args_list], ["memdiff", "vmdiff"])

    @patch.object(cli, "find_compose")
    def test_list_snapshots_does_not_require_docker(self, find):
        (self.root / "test.vmsd").write_text('snapshot.numSnapshots = "0"\n')
        result = self.runner.invoke(app, [str(self.root), "--list-snapshots"])
        self.assertEqual(result.exit_code, 0, result.output)
        find.assert_not_called()

    @patch.object(cli, "find_compose", side_effect=RuntimeError("Install Docker Compose"))
    def test_missing_compose_is_clean_cli_failure(self, find):
        result = self.runner.invoke(app, self.disk_args())
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Install Docker Compose", result.output)
        self.assertNotIn("Traceback", result.output)

    @patch.object(cli, "find_compose", return_value=["docker", "compose"])
    @patch.object(cli, "run_compose", side_effect=subprocess.CalledProcessError(125, ["docker", "compose"]))
    def test_docker_failure_is_clean_cli_failure(self, run, find):
        result = self.runner.invoke(app, self.disk_args() + ["--show"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Docker Compose failed", result.output)

    @patch.object(cli, "find_compose")
    def test_missing_memory_rejected_before_docker(self, find):
        result = self.runner.invoke(app, self.disk_args()[:-1])
        self.assertEqual(result.exit_code, 2)
        find.assert_not_called()


if __name__ == "__main__":
    unittest.main()
