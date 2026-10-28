import inspect
import os
import sys

import logging

from flask import Flask, jsonify, request, render_template, send_from_directory

import config

# Python Crimes to import from a local module
currentdir = os.path.dirname(os.path.abspath(
    inspect.getfile(inspect.currentframe())))
backend_dir = os.path.join(currentdir, "backend")
sys.path.insert(0, backend_dir)


if config.dev:
    from backend import vmdiff
    vmdiff.Main()

try:
    import diffcache  # noqa
except ImportError:
    from backend import diffcache


REACT_BUILD_DIR = config.REACT_BUILD_DIR

app = Flask(
    __name__, static_folder=f"{REACT_BUILD_DIR}/static", template_folder=f"{REACT_BUILD_DIR}")

logging.info(f"Waiting for results at {config.RUN_TREE_PATH}....")

if not os.path.exists(config.RUN_TREE_PATH):
    logging.critical(
        f"No results found at {config.RUN_TREE_PATH}. Generate results first?")
    sys.exit(1)

cache = diffcache.DiffCache(
    config.RUN_DISK_PATH, config.RUN_TREE_PATH, config.RUN_MEMORY_PATH)
try:
    if not cache.tree_cache_exists():
        raise ValueError('Comparison has no completion marker')
    tree, children_map = cache.get_tree_data_from_cache()
except (OSError, ValueError) as exc:
    raise RuntimeError(
        'Results are missing or incomplete; regenerate the comparison first.') from exc


logging.debug(f"Tree: {len(tree)}, children: {len(children_map)}")


@app.route("/children")
def get_children_handler():
    key = request.args.get("key")

    if not key:
        return jsonify(error='A non-empty key is required'), 400
    if key not in children_map:
        return jsonify(error='Unknown tree key'), 404
    node_children = children_map[key]
    response = jsonify(node_children)

    return response


@app.route("/diff")
def get_diff():

    key = request.args.get("key")

    if not key:
        return jsonify(error='A non-empty key is required'), 400
    try:
        diff = cache.get_diff(key)
    except ValueError:
        return jsonify(error='Invalid diff key'), 400
    if diff is None:
        logging.warning(f"No diff found for {key}")
        result = None
    else:
        result = diff.diff_lines

    response = jsonify(result)

    # to start with, just return the directories, and let the user expand out the files.
    return response


@app.route("/changed_files")
def get_changed_files():
    response = jsonify(tree)

    # To start with, just return the directories, and let the user expand out the files.
    return response


@app.route("/json/<path:path>")
def json(path):
    json_dir = f"{cache.tree_path}/json"
    return send_from_directory(json_dir, path)


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":

    app.run("127.0.0.1", debug=False)
