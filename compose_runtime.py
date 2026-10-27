"""Docker Compose discovery shared by all CLI operations."""
import subprocess
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def find_compose():
    """Prefer the plugin, but support existing standalone Compose installs."""
    for command in (["docker", "compose"], ["docker-compose"]):
        try:
            result = subprocess.run(
                command + ["version"], capture_output=True, text=True,
                timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return command
    raise RuntimeError(
        "Docker Compose is unavailable. Install/start Docker Desktop, or install "
        "the Docker Compose plugin for Docker Engine on Linux. Verify that "
        "'docker compose version' succeeds (legacy 'docker-compose version' is "
        "also supported). See https://docs.docker.com/compose/install/"
    )


def run_compose(command, env):
    # Both Compose variants automatically load .env from the project directory.
    # Argument lists also preserve spaces/metacharacters in snapshot paths.
    subprocess.run(command, cwd=PROJECT_DIR, env=env, check=True)
