"""Versioned, collision-free storage for disk and process diffs."""
import hashlib
import json
import logging
import os
import pathlib
import tempfile

import unified_diff


class DiffCache(object):
    def __init__(self, run_disk_path, run_tree_path, run_process_path=None):
        self.run_path = pathlib.Path(run_disk_path)
        self.tree_path = pathlib.Path(run_tree_path)
        self.run_process_path = (
            pathlib.Path(run_process_path) if run_process_path else None)

    @staticmethod
    def _disk_key(vm_path):
        if not isinstance(vm_path, str) or not vm_path or '\x00' in vm_path:
            raise ValueError('A non-empty guest path is required')
        windows = pathlib.PureWindowsPath(vm_path)
        path = (windows if vm_path.startswith('\\') or windows.drive
                else pathlib.PurePosixPath(vm_path))
        if '..' in path.parts:
            raise ValueError('Parent traversal is not a guest path')
        return path.as_posix()

    @staticmethod
    def _safe_child(root, *parts):
        """Also reject existing cache symlinks that point outside this cache."""
        root = root.resolve()
        path = root.joinpath(*parts).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise ValueError('Cache path escapes its result directory')
        return path

    def _disk_path(self, key):
        digest = hashlib.sha256(key.encode('utf8')).hexdigest()
        return self._safe_child(self.run_path, 'entries', digest + '.json')

    @staticmethod
    def _process_key(pid):
        if not isinstance(pid, str) or not pid.isascii() or not pid.isdigit():
            raise ValueError('A numeric process ID is required')
        return pid

    @staticmethod
    def _read_lines(path):
        try:
            with path.open() as stream:
                lines = stream.readlines()
        except FileNotFoundError:
            return None
        if len(lines) < 2:
            logging.warning('Ignoring incomplete diff cache: %s', path)
            return None
        return lines

    @staticmethod
    def _atomic_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent,
                                             delete=False) as stream:
                temporary = pathlib.Path(stream.name)
                json.dump(value, stream)
                os.chmod(stream.name, 0o644)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def invalidate(self):
        (self.tree_path / 'complete.json').unlink(missing_ok=True)

    def mark_complete(self):
        self._atomic_json(self.tree_path / 'complete.json', {'schema': 2})

    def cache_results(self, results):
        # Each guest path has its own entry. Guest filenames never become host
        # paths, so file/directory transitions and reserved names cannot collide.
        entries = self._safe_child(self.run_path, 'entries')
        entries.mkdir(parents=True, exist_ok=True)
        for old in entries.glob('*.json'):
            old.unlink()
        for path, diff in results.items():
            key = self._disk_key(path)
            self._atomic_json(self._disk_path(key), {
                'path': key, 'is_dir': diff.is_dir, 'diff_lines': diff.diff_lines})

    def cache_process_results(self, results):
        if self.run_process_path is None:
            raise ValueError('Process caching is disabled')
        self.run_process_path.mkdir(parents=True, exist_ok=True)
        for old in self.run_process_path.iterdir():
            if old.name.isascii() and old.name.isdigit():
                old.unlink()
        for pid, diff in results.items():
            path = self._safe_child(self.run_process_path, self._process_key(pid))
            with path.open('w') as stream:
                stream.writelines(diff.diff_lines)

    def get_process_diff_from_cache(self, pid):
        self._process_key(pid)
        if self.run_process_path is None:
            return None
        path = self._safe_child(self.run_process_path, pid)
        lines = self._read_lines(path)
        return unified_diff.UnifiedDiff(lines) if lines is not None else None

    def _read_disk_entry(self, path, expected_key=None):
        try:
            with path.open() as stream:
                entry = json.load(stream)
            key = self._disk_key(entry['path'])
            lines = entry['diff_lines']
            if ((expected_key is not None and key != expected_key)
                    or not isinstance(lines, list) or len(lines) < 2
                    or not all(isinstance(line, str) for line in lines)):
                raise ValueError('Invalid disk diff cache entry')
            return key, unified_diff.UnifiedDiff(lines, entry['is_dir'])
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError):
            logging.warning('Ignoring invalid diff cache: %s', path)
            return None

    def get_diff_from_cache(self, vm_path):
        key = self._disk_key(vm_path)
        entry = self._read_disk_entry(self._disk_path(key), key)
        return entry[1] if entry else None

    def get_diff(self, key):
        if not isinstance(key, str) or not key:
            raise ValueError('A non-empty key is required')
        if key.isascii() and key.isdigit():
            return self.get_process_diff_from_cache(key)
        return self.get_diff_from_cache(key)

    def cache_exists(self):
        return (self.run_path / 'entries').is_dir() and self.tree_cache_exists()

    def process_cache_exists(self):
        return (self.run_process_path is not None
                and self.run_process_path.is_dir() and self.tree_cache_exists())

    def get_cached_results(self):
        if not self.cache_exists():
            raise RuntimeError(f'Cache path {self.run_path} does not exist!')
        results = {}
        logging.info('Loading from diff cache %s', self.run_path)
        entries = self._safe_child(self.run_path, 'entries')
        for path in entries.glob('*.json'):
            path = self._safe_child(self.run_path, 'entries', path.name)
            entry = self._read_disk_entry(path)
            if entry:
                results[entry[0]] = entry[1]
        return results

    def tree_cache_exists(self):
        return all((self.tree_path / name).is_file()
                   for name in ('tree.json', 'children.json', 'complete.json'))

    def cache_tree(self, tree):
        os.makedirs(self.tree_path, exist_ok=True)
        self._atomic_json(self.tree_path / 'tree.json', tree.get_tree())
        self._atomic_json(self.tree_path / 'children.json', tree.get_children_map())

    def get_tree_data_from_cache(self):
        with (self.tree_path / 'tree.json').open() as stream:
            tree = json.load(stream)
        with (self.tree_path / 'children.json').open() as stream:
            children_map = json.load(stream)
        return tree, children_map
