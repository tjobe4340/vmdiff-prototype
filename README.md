# vmdiff

![logo](https://community.atlassian.com/t5/image/serverpage/image-id/250140i6BA42D04B2F49CE1/image-dimensions/280x210?v=v2)

A prototype for comparing virtual machine snapshots: inspect selected disk-file
changes and Windows process information between two points in time. It is not a
complete record of every change; see [comparison limits](#comparison-limits).

This fork includes CLI/Compose startup fixes and viewer dependency compatibility
fixes. The original project is [vmdiff/vmdiff-prototype](https://github.com/vmdiff/vmdiff-prototype).

## Blog post
There's also a delightful [companion blog post](https://community.atlassian.com/t5/Trust-Security-articles/Introducing-vmdiff-a-tool-to-find-everything-that-changes-on/ba-p/2321969) with more context :))

## Features

* Compares supported Windows or macOS virtual disks (`.vmdk` files), including added and deleted files visible in the snapshots
* Produces line-by-line diffs for selected text files, subject to the limits below
* Compares Windows memory dumps (`.vmem` files) for running processes, command lines, and environment variables
* Makes generated diffs available as local directories to search/process via terminal (think `grep`)
* Uses Docker for processing and the viewer, with a Python CLI on the host

### Comparison limits

* Binary files are excluded by default by the CLI. `--include-binary` opts into binary processing, not a line-by-line binary diff.
* Files larger than 2 MiB receive a “File too large to diff” placeholder rather than a content diff when selected for comparison.
* Change detection first compares metadata and size, not the contents of every file. Same-size content changes with unchanged compared metadata can be missed.
* Filters, unreadable/corrupt filesystem entries, and the default suppression of file metadata-only changes can also omit results. Absence from the output does not prove that nothing changed.
* Memory comparison covers the configured Windows process plugins, not every byte of memory or all activity between snapshots.

![Demo](https://community.atlassian.com/t5/image/serverpage/image-id/250126i9D3D94314406622B/image-dimensions/749x376?v=v2)

![Process tree](https://community.atlassian.com/t5/image/serverpage/image-id/250138iB53029B9F025028D/image-size/large?v=v2&px=999)

![Terminal parsing](https://community.atlassian.com/t5/image/serverpage/image-id/250129i6BE4A67E932C3C34/image-size/large?v=v2&px=999)

## Installation

```shell
git clone https://github.com/tjobe4340/vmdiff-prototype.git
cd vmdiff-prototype
```

### Install Docker

Install and start [Docker Desktop](https://docs.docker.com/compose/install/),
or install Docker Engine and the [Compose plugin on Linux](https://docs.docker.com/compose/install/linux/).
Verify the installation before running vmdiff:

```shell
docker compose version
docker info
```

The CLI prefers `docker compose` and falls back to an existing `docker-compose`
standalone installation. Modern Compose is recommended; the standalone fallback
is for compatibility only. Compose service commands run from the repository
directory, so Compose automatically loads the included `.env` and `docker-compose.yml`, even
when you launch the CLI from another directory.

#### `unknown flag: --env-file` / exit status 125

This commonly means the Docker CLI has no working Compose plugin (or is using an
incompatible/older installation), not that the `.env` file is missing. Update or
install Compose and verify `docker compose version`. The CLI now checks Compose
before starting and does not require the `--env-file` flag. If discovery succeeds
but a command fails, the Docker error is preserved: check `docker info` for daemon
or permissions errors. Do not work around this by deleting the project's `.env`.

For manual commands, run them in this repository:

```shell
docker compose config
docker compose up --build app
```

Use `docker-compose` instead only if that is the installed variant. The viewer
requires previously generated results and the same snapshot/options environment;
for normal use, generate a diff with the CLI, then repeat that command with
`--show`. Merely starting the app does not generate a diff.

### Install Python and CLI dependencies

Install Python on the host as well as Docker. Python 3.12 is one of the versions
covered by this fork's regression CI; see [regression checks](#regression-checks)
for the exact scope and the separate Python 3.15.2 smoke-test results. These are
host CLI/viewer checks, not a guarantee about the Python runtimes or native
libraries inside the Docker images.

From the cloned repository, create and activate a virtual environment. For
macOS/Linux with a POSIX shell:

```shell
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

For Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The usage examples below use POSIX-shell syntax. On Windows, invoke the CLI with
`python .\vmdiff` and supply the actual Windows VM directory; adapt multiline
commands to your shell. If you prefer not to activate the environment, invoke
`.venv\Scripts\python.exe .\vmdiff` directly on Windows. Server dependencies
are only needed on the host for local viewer development/regression checks;
the Docker viewer installs them in its image.

## Usage

### Prepare the snapshot directory

Use a directory containing the complete, readable inputs for both points in time:

* `--list-snapshots` and `--from-snapshot`/`--to-snapshot` require the VM's `.vmsd` snapshot metadata in `INPUT_DIR`.
* Keep the `.vmdk` descriptor files, their referenced extents/data files, and any parent/base disks needed by the snapshot chain together. A delta disk alone is not sufficient. Parent filenames are resolved relative to the descriptor's directory (usually the same directory); preserve referenced names and relative layout.
* Memory comparison requires the corresponding `.vmem` dumps. The snapshot parser derives their names from the `.vmsn` entries in `.vmsd`; the `.vmsn` file itself is not the memory dump used here. For disk-only comparisons, pass `--no-use-memory`.
* Explicit `--from-disk`, `--to-disk`, `--from-memory`, and `--to-memory` paths are checked relative to your current working directory, **not** relative to `INPUT_DIR`. The CLI then passes only their basenames to the containers. Use full paths to selected files directly inside `INPUT_DIR`; selecting a file elsewhere or in a nested subdirectory will not make it available under that basename.

VMware VM directories vary by product and configuration. Common locations
include `C:\Users\<username>\Documents\Virtual Machines\` on Windows,
`~/Virtual Machines.localized/` on macOS, and `~/vmware/` on Linux. For VMware
Fusion, the actual files may be inside a `.vmwarevm` bundle. Use the directory
that directly contains your `.vmsd` and selected image files, not just its parent.

In a POSIX shell, `"~/..."` does **not** expand your home directory. Use
`"$HOME/..."` so paths containing spaces work. For example, replace the VM name
below with yours:

```shell
VM_DIR="$HOME/Virtual Machines.localized/VMName.vmwarevm"
./vmdiff "$VM_DIR" --list-snapshots
```

Use the snapshot IDs returned by that command, not assumed filenames or array
positions. Listing snapshots does not require a running Docker daemon.

### Generate and view a diff

If your listed IDs are `1` and `2`, compare the earlier snapshot to the later one:

```shell
./vmdiff "$VM_DIR" --from-snapshot 1 --to-snapshot 2
```

Both disk and memory processing are enabled by default. For a disk-only run:

```shell
./vmdiff "$VM_DIR" --from-snapshot 1 --to-snapshot 2 --no-use-memory
```

Disk processing prompts for a partition on standard input. To select one, add
`--partition 4` or `--partition p4` (replace `4` with the first snapshot's ID).
Numeric IDs, `p`-prefixed IDs, and ranges/lists are accepted. `--partition all`
(or interactive `all`) compares all supported direct GPT filesystems in both
snapshots, including newly added or removed partitions. Sparse partition IDs
are handled without assuming consecutive numbering.

**GPT multi-partition comparisons:** partitions are matched by unique GPT
partition GUID, not table order, index, drive letter, or filename. An explicit
selection follows the selected first-snapshot GUID into the second snapshot,
even if its displayed `pN` changes. If that GUID disappears, it is a removal;
a newly created GUID is an addition when using `all`. A matching GUID whose
filesystem cannot be opened is an error, not a fabricated deletion.

Files appear under `/Volumes/gpt-<GUID>/...` in the viewer and diff cache, with
partition IDs/GUIDs shown on the volume nodes. Identical paths in different
partitions remain separate. Filters still apply to the original guest path
(e.g. `/Windows`), not the `/Volumes` prefix. Empty added/removed filesystems also
have a visible partition result unless `--only-changed-files` is selected.

**Scope and limits:** this is direct GPT filesystem support, using pinned dfVFS's
preferred GPT backend. MBR/TSK-only multi-partition identity, nested APFS/LVM/VSS
volumes, and nested partition tables are not paired automatically. Ambiguous or
missing/duplicate GUIDs and incompatible filesystem-reader types stop with an
explanation rather than guessed matches. An unchanged GUID is partition identity,
not proof that a filesystem was not reformatted; TSK is a reader type shared by
several filesystem formats. A single legacy/unpartitioned filesystem retains its
existing unprefixed paths and selection behavior, without a GPT identity guarantee.
Reserved/unallocated/unsupported partitions with no filesystem do not produce
file comparisons. Native VM integration is still unverified; see validation below.

After generation finishes, repeat the same input and filtering options with
`--show`. For the disk-only example above:

```shell
./vmdiff "$VM_DIR" --from-snapshot 1 --to-snapshot 2 --no-use-memory --show
```

`--show` starts the viewer for existing results and prints its URL; it does not
regenerate the diff or automatically open a browser. Once the service is ready,
open [http://localhost:5000](http://localhost:5000) yourself. Keep the command
running while using the viewer.

### Compare explicit image files

You can compare VMDK files without `.vmsd` metadata. Both selected image files must
be directly inside the input directory, with all required parent/extent files
available as described above:

```shell
INPUT_DIR="$HOME/dir-with-vmdk-files"
./vmdiff "$INPUT_DIR" \
  --from-disk "$INPUT_DIR/disk1.vmdk" \
  --to-disk "$INPUT_DIR/disk2.vmdk" \
  --no-use-memory
```

The CLI also accepts `--from-memory` and `--to-memory` with full paths to `.vmem`
files directly inside `INPUT_DIR`, plus `--no-use-disk`. Only the CLI command
construction for this memory-only option combination has been tested; actual
memory-only container execution has not been validated.

### Filter guest paths

`--filter-path` uses **literal path prefixes**, not regular expressions. Paths
are inside the guest filesystem, not paths on the host. Repeat the option to
include more than one prefix; traversal also includes ancestor directories needed
to reach those paths. `--ignore-path` uses regular-expression matching and can
also be repeated.

For example, compare only a guest user's home directory and ignore `.log` and
`.txt` files:

```shell
./vmdiff "$VM_DIR" --from-snapshot 1 --to-snapshot 2 --no-use-memory \
  --filter-path "/home/username/" \
  --ignore-path '[.]log$' --ignore-path '[.]txt$'
```

Replace the prefix with one that exists in your guest, such as `/Users/name/`
on macOS or the backslash-style paths reported for a Windows filesystem. Include
the same filter/ignore options when invoking `--show` for these results.

For the full option list:

```shell
./vmdiff --help
```

Some embedded CLI help text is inherited from upstream and is still imprecise:
its quoted-tilde and bare-filename examples, the description of `--filter-path`
as regex, and the claim that `--show` opens a browser. Follow the corrected
examples and behavior notes above.

### Browse the diffs via shell

Generated diffs are stored under this repository's `results/` directory in
versioned run-specific subdirectories. Disk entries are JSON files named by a
SHA-256 hash of the normalized guest path under `disk/entries/`; each stores its
original guest path, directory flag, and diff lines. Guest filenames are not
used as host paths. Process diffs are stored separately by numeric process ID.
Search the JSON contents rather than assuming the old mirrored directory layout.

## How it works

![Diagram](https://community.atlassian.com/t5/image/serverpage/image-id/250141i2CC67D463D148789/image-size/large?v=v2&px=999)

### Tech Stack

* [Typer](https://typer.tiangolo.com/) (CLI)
* docker-compose
* Volatility (to parse memory images)
* [dfvfs](https://github.com/log2timeline/dfvfs) (to parse disk images)
* [pyvmdk/libvmdk](https://github.com/libyal/libvmdk) with a local delta-disk wrapper and patched dfVFS adapter
* React + TypeScript + Ant Design (frontend)
* grep (Searching diffs via command line)

## Contributing

The original upstream author said they did not plan to maintain the prototype
for at least 12 months, possibly indefinitely, and encouraged forks or new
implementations. That is historical upstream context, not a current maintenance
commitment or schedule for this fork. See the original
[contribution notes](https://github.com/vmdiff/vmdiff-prototype/blob/main/CONTRIBUTING.md).

## Future work

* If a Windows disk has corrupted sectors, `dfvfs` can’t read those sectors. This comes up a lot, and while you can run `chkdsk` on the VM to get around it, it would be nice to not have to.
* It would be nice to be able to diff snapshots of your actual computer, not a virtual machine, but this is hard without external storage
  * The two snapshots of your disk may not fit on your disk itself, to say nothing of the memory snapshots

## Regression checks

Install both CLI and server dependencies in a virtual environment, then run:

```shell
python -m pip install -r requirements.txt -r server_requirements.txt
python -m pip check
# Install only dfVFS's Python code for selector tests, not its native drivers.
python -m pip install --no-deps dfvfs==20220816
python -m unittest discover -s tests -v
python -m compileall -q vmdiff compose_runtime.py config.py server.py backend
```

The committed Python suite contains 89 tests. Compose launches, native disk I/O,
and Volatility output are replaced with controlled fixtures where required.
The tests exercise real pinned dfVFS parser/scanner code, CLI validation and
command construction, cache containment/collisions/completion, disk comparison
logic, GPT identity pairing and scoped cache/tree roundtrips, memory-only orchestration, optional process fields, the real POSIX memory
entrypoint, and Flask routes. The test-only `--no-deps` dfVFS install is not a
runnable native disk-processing installation.

Frontend checks (Node 24):

```shell
cd frontend
npm ci
CI=true npm test -- --watchAll=false --runInBand
CI=true npm run build
```

Seven React tests cover empty/unchanged diffs, failed requests and retry,
selection races, and folder-loading completion. They use mocked network/tree
components; a production build is also checked. They are not browser or real
snapshot integration tests. GitHub Actions runs Python **3.8 and 3.12**, plus
these frontend checks. Python 3.15.2 is tested locally, not in the CI matrix.

### Runtime audit update — 2026-10-28

The audit fixes unsafe cache paths, file/directory cache collisions, cache-key
collisions across options/inputs, false-success memory extraction, binary/text
comparison errors, missing Windows timestamps and process fields, snapshot
metadata parsing, memory-only disk initialization, and viewer loading failures.
Memory diffs now pass an empty line list for absent processes, which is required
by Python 3.15's stricter `difflib` input validation.
Failed or interrupted generation has no completion marker and is not reusable.
The viewer binds only to `127.0.0.1:5000` and no longer grants wildcard API CORS.
Treat snapshots and analysis results as sensitive; this is a local viewer, not
an authenticated public service.

**Rebuild after pulling these changes.** Some files (including `config.py` and
the viewer) are copied into images rather than bind-mounted. Existing published
images do not contain these fixes. From the repository:

```shell
mkdir -p results memory-processing/volatilitycache
docker compose build vmdiff memdiff app
```

Then generate the comparison again using the CLI before repeating it with
`--show`. The new `v3-` cache namespace leaves old results untouched; old results
are not migrated. `--no-cache` regenerates extraction and derived results.
Interactive partition runs always regenerate because their selected ID is not
known to the separate viewer invocation. Repeat the same input/options for
`--show`, preferably with an explicit partition ID.

The CLI input identity includes resolved input directory, selected filenames,
sizes and modification times, **not full image hashes or every parent/extent**.
If inputs/parents change without those attributes changing, use `--no-cache`.
Direct Compose use without the CLI does not get the host input fingerprint.

The memory container retains its existing unprivileged user. On Linux, bind
mounts must be writable by that container user and readable by the processor/
viewer users. Creating directories on the host avoids Docker creating root-owned
mountpoints, but does not alone guarantee matching UID permissions. If permission
errors occur, check ownership/UID mapping for these output directories; do not
make them world-writable. Docker builds and these permissions are not integration-
tested here.

Remaining limitations: content-change detection first compares size/metadata and
can miss same-size edits with unchanged metadata; content diffs over 2 MiB use a
placeholder; non-GPT/nested multi-volume pairing is unsupported; native VMDK chain/handle
lifecycle and real VM processing still need integration validation. The audit is
not a guarantee of forensic completeness or a full dependency-security audit.

### Manual Python 3.15.2 check — 2026-10-27

A separate check against fork `main` at commit
[`2dd05d4`](https://github.com/tjobe4340/vmdiff-prototype/commit/2dd05d4ac2a161c6bcccf8806f5749f51c4900db)
used an isolated Linux source build of Python 3.15.2 and a fresh virtual environment:

* CLI and server requirements installed successfully; `python -m pip check` reported no broken requirements.
* All 15 committed unit tests passed, as did the `compileall` command above.
* CLI help (including the executable/shebang entry point), snapshot-listing fixtures, argument validation, and clean missing-Compose failures passed.
* Importing the actual viewer server, six HTTP checks through Flask's test client using temporary result/template fixtures, and Gunicorn's `--check-config server:app` passed. The root-page fixture was not a built React application.

The older Flask/Werkzeug stack emitted deprecation warnings. The source-built
interpreter lacked some optional standard-library extensions; none were required
by the checks above. This is a dated compatibility smoke check, not a declaration
of full Python 3.15 support or a new CI target.

**Not tested by these checks:** Docker image builds or real Compose service
startup, native disk/memory-processing dependencies, actual VM snapshot diffs,
or the built React UI in a browser. A complete end-to-end run with Docker and
real snapshots is still needed to validate those paths.
