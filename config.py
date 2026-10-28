import os
import hashlib
import logging
import json


def as_bool(var):
    if var is None:
        return False

    val = var.lower()
    if val == "false":
        return False

    if val == "true":
        return True

    raise RuntimeError(
        f"Environment variable with value {var} is neither True nor False")


# Read config vars dynamically fron environment (set in `.env`)
diff_config_keys = [key for key in os.environ if key.startswith("DIFF_")]

# Convert environment variable format (DIFF_USE_ATTRIBUTES) to variable name format for diskdiff.py (use_attributes)
diff_config = {
    key[5:].lower(): as_bool(os.environ[key])
    for key in diff_config_keys
}
dev = "_DEV" if as_bool(os.environ.get("VMDIFF_DEV")) else ""

filter_path_json = os.environ.get("FILTER_PATH_JSON")
ignore_path_json = os.environ.get("IGNORE_PATH_JSON")

allow_dirs = json.loads(filter_path_json) if filter_path_json else []
ignore_dirs = json.loads(ignore_path_json) if ignore_path_json else []

IGNORE_PROCESSES_REGEX = os.environ.get("IGNORE_PROCESSES_REGEX")

PARTITION = os.environ.get("PARTITION_IDENTIFIER")


MEMORY_PLUGINS = os.environ.get("MEMORY_PLUGINS").split()

FROM_DISK_IMAGE_FILENAME = os.environ.get(
    "FROM_DISK_IMAGE_FILENAME")
TO_DISK_IMAGE_FILENAME = os.environ.get(
    "TO_DISK_IMAGE_FILENAME")

USE_DISK = False
if FROM_DISK_IMAGE_FILENAME and TO_DISK_IMAGE_FILENAME and as_bool(os.environ.get("USE_DISK")):
    USE_DISK = True


USE_CACHE = as_bool(os.environ.get("USE_CACHE"))


SNAPSHOT_DIR = os.environ.get(f"SNAPSHOT_DIR{dev}")

FROM_DISK_PATH = os.path.join(SNAPSHOT_DIR, FROM_DISK_IMAGE_FILENAME)
TO_DISK_PATH = os.path.join(SNAPSHOT_DIR, TO_DISK_IMAGE_FILENAME)


FROM_MEMORY_IMAGE_FILENAME = os.environ.get("FROM_MEMORY_IMAGE_FILENAME")
TO_MEMORY_IMAGE_FILENAME = os.environ.get("TO_MEMORY_IMAGE_FILENAME")

USE_MEMORY = False

if FROM_MEMORY_IMAGE_FILENAME and TO_MEMORY_IMAGE_FILENAME and as_bool(os.environ.get("USE_MEMORY")):
    USE_MEMORY = True

RESULTS_DIR = os.environ[f"RESULTS_DIR{dev}"]
REACT_BUILD_DIR = os.environ[f"REACT_BUILD_DIR{dev}"]

LOG_LEVEL = logging.DEBUG if dev or as_bool(os.environ.get("VMDIFF_DEBUG")) else logging.INFO


# v3 adds GUID-scoped disk keys. Older derived caches are left untouched.
# Raw memory extraction remains v2 because its contents are unchanged.
SOURCE_ID = os.environ.get('VMDIFF_SOURCE_ID') or os.path.abspath(SNAPSHOT_DIR)
MEMORY_RUN_ID = os.environ.get('VMDIFF_MEMORY_RUN_ID') or (
    'memory-v2-' + hashlib.sha256(json.dumps({
        'source': SOURCE_ID, 'from': FROM_MEMORY_IMAGE_FILENAME,
        'to': TO_MEMORY_IMAGE_FILENAME}, sort_keys=True).encode()).hexdigest()[:24])


def get_run_id():
    options = {
        'schema': 3, 'source': SOURCE_ID,
        'disk': [USE_DISK, FROM_DISK_IMAGE_FILENAME, TO_DISK_IMAGE_FILENAME],
        'memory': [USE_MEMORY, FROM_MEMORY_IMAGE_FILENAME, TO_MEMORY_IMAGE_FILENAME],
        'partition': PARTITION, 'diff': diff_config,
        'allow_dirs': sorted(allow_dirs), 'ignore_dirs': sorted(ignore_dirs),
        'ignore_processes': IGNORE_PROCESSES_REGEX,
        'memory_plugins': sorted(MEMORY_PLUGINS),
    }
    digest = hashlib.sha256(json.dumps(
        options, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]
    return 'v3-' + digest


RUN_ID = get_run_id()
RUN_PATH = os.path.join(RESULTS_DIR, RUN_ID)
RUN_DISK_PATH = os.path.join(RUN_PATH, "disk")
RUN_MEMORY_PATH = os.path.join(RUN_PATH, "memory")
RUN_TREE_PATH = os.path.join(RUN_PATH, "tree")
