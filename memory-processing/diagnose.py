#!/usr/bin/env python3
"""Inspect selected memory dumps without changing the VM or comparison caches."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compose_runtime import PROJECT_DIR, find_compose


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog='First rebuild: docker compose build memdiff. Symbol requests send PDB filename, GUID/age and your public IP to Microsoft over HTTPS. Dumps are processed locally, not uploaded. Logs may contain guest details.')
    parser.add_argument('snapshot_dir', type=Path)
    parser.add_argument('memory_files', nargs='+', help='Exact .vmem filenames from --list-snapshots')
    args = parser.parse_args()
    root = args.snapshot_dir.expanduser().resolve()
    if not root.is_dir():
        parser.error('snapshot_dir must be an existing directory')
    files = []
    for filename in args.memory_files:
        path = (root / filename).resolve()
        if path.parent != root or not path.is_file():
            parser.error('Each memory file must be directly inside snapshot_dir')
        files.append(path)
    try:
        compose = find_compose()
    except RuntimeError as exc:
        parser.error(str(exc))
    log_root = PROJECT_DIR / 'results' / 'memory-diagnostics'
    log_root.mkdir(parents=True, exist_ok=True)
    log_dir = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=log_root))
    print('Requires rebuilt memdiff image: docker compose build memdiff', flush=True)
    print('Symbol lookup sends PDB filename, GUID/age and public IP to Microsoft over HTTPS; no dump upload.', flush=True)
    failed = False
    for number, path in enumerate(files, 1):
        print(f'{path.name}: {path.stat().st_size} bytes', flush=True)
        metadata = [path.with_suffix(suffix) for suffix in ('.vmsn', '.vmss')]
        found = [item.name for item in metadata if item.is_file()]
        print('Matching VMware metadata: ' + (', '.join(found) or 'NOT FOUND; may be required for this dump'), flush=True)
        log = log_dir / f'{number}-windows-info.log'
        command = compose + ['run', '--rm', '-T', '-v', f'{root}:/snapshots:ro',
                             '--entrypoint', 'vmdiff-volatility3', 'memdiff', '-vvv',
                             '--cache-path', '/memdiff/volatilitycache',
                             '-f', f'/snapshots/{path.name}', 'windows.info.Info']
        with log.open('w') as output:
            output.write(f'Input: {path.name}\nBytes: {path.stat().st_size}\nMetadata: {found}\n')
            output.flush()
            try:
                result = subprocess.run(command, cwd=PROJECT_DIR, env=os.environ.copy(),
                                        stdout=output, stderr=subprocess.STDOUT, check=False)
            except OSError as exc:
                output.write(str(exc) + '\n')
                failed = True
            else:
                failed = failed or result.returncode != 0
                output.write(f'\nCommand exit status: {result.returncode}\n')
        print(f'Diagnostic log: {log}', flush=True)
    print('Logs remain local and may contain guest details. Review before sharing.')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
