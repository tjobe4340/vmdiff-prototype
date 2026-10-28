#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Script to list file entries."""

from dfvfs.helpers import command_line
from dfvfs.helpers import volume_scanner
import memdiff
import diff_tree
import diffcache
import diskdiff
import file_entry_lister
import logging
import sys
import os
import json
import inspect

import hashlib
import shutil


# Hacks to import the config from the parent directory.
currentdir = os.path.dirname(os.path.abspath(
    inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

import config  # noqa

logging.basicConfig(
    format='[%(asctime)s]:%(levelname)s:%(message)s', level=config.LOG_LEVEL)


class CachingVolumeScannerMediator(command_line.CLIVolumeScannerMediator):
    """Remembers validated partition choices, not unrelated scanner input."""

    def __init__(self, input_reader=None, output_writer=None):
        super().__init__(input_reader=input_reader, output_writer=output_writer)
        # None means no partition prompt occurred (for example, a single-volume
        # image). An empty list is a distinct, explicit selection of no partitions.
        self.selected_partition_identifiers = None

    def GetPartitionIdentifiers(self, volume_system, volume_identifiers):
        selected = super().GetPartitionIdentifiers(
            volume_system, volume_identifiers)
        self.selected_partition_identifiers = list(selected)
        return selected

    def ParsePartitionIdentifiersString(self, identifiers):
        """Accept the same numeric/prefixed syntax as dfVFS's partition prompt."""
        return self._ParseVolumeIdentifiersString(
            (identifiers or '').strip(), prefix='p')


def load_memory_results():
    memory_run_name = config.MEMORY_RUN_ID
    memory_run_path = os.path.join(
        config.RESULTS_DIR, "memory", memory_run_name)
    results = {}
    for plugin in config.MEMORY_PLUGINS:

        from_plugin_path = os.path.join(memory_run_path, f"from-{plugin}.json")
        to_plugin_path = os.path.join(memory_run_path, f"to-{plugin}.json")
        with open(from_plugin_path) as f:
            from_plugin = json.load(f)
        with open(to_plugin_path) as f:
            to_plugin = json.load(f)

        if not isinstance(from_plugin, list) or not isinstance(to_plugin, list):
            raise ValueError(f'Memory plugin {plugin} did not produce JSON arrays')
        results[plugin] = (from_plugin, to_plugin)

    return results


def dump_api_data(cache):

    run_path = cache.tree_path

    dump_dir = run_path / "json"
    children_dir = dump_dir / "children"
    diff_dir = dump_dir / "diff"

    if dump_dir.exists():
        shutil.rmtree(dump_dir)

    logging.info(f"Generating API data for static site: {dump_dir}")

    os.makedirs(dump_dir, exist_ok=True)
    os.makedirs(children_dir, exist_ok=True)
    os.makedirs(diff_dir, exist_ok=True)

    logging.info(f"Dumping API data to {dump_dir}")

    tree, children_map = cache.get_tree_data_from_cache()

    cache._atomic_json(dump_dir / "changed_files", tree)

    # Dump all the data with url encoded keys, so we can serve it statically later
    for key, children in children_map.items():

        # Make sure to also encode the "/" character.
        filename = hashlib.sha1(key.encode("utf8")).hexdigest()

        path = children_dir / filename
        cache._atomic_json(path, children)

        # Get the diff and dump it too.
        diff = cache.get_diff(key)
        if diff is None:
            result = None
        else:
            result = diff.diff_lines

        path = diff_dir / filename
        cache._atomic_json(path, result or [])


def Main():

    if not config.USE_DISK and not config.USE_MEMORY:
        raise RuntimeError('Enable disk or memory comparison')

    used_partition_prompt = False
    if config.USE_DISK:
        # Leave Blank or invalid for interactive prompt
        partition = config.PARTITION
        VOLUMES = "all"

        logging.basicConfig(
            level=logging.INFO, format='[%(levelname)s] %(message)s')

        mediator = CachingVolumeScannerMediator()

        volume_scanner_options = volume_scanner.VolumeScannerOptions()
        volume_scanner_options.partitions = mediator.ParsePartitionIdentifiersString(
            partition)

        volume_scanner_options.volumes = mediator.ParseVolumeIdentifiersString(
            VOLUMES)

        # Init disk file listers.
        parent_lister = file_entry_lister.FileEntryLister(
            config.FROM_DISK_PATH, volume_scanner_options, mediator=mediator, ignore_dirs=config.ignore_dirs, allow_dirs=config.allow_dirs)

        # dfVFS has already stripped whitespace, expanded ranges/"all", and checked
        # the prompted choice against the source image. Reuse that result directly:
        # its public generic parser rejects prefixed IDs and unstripped "all\n".
        # VSS, APFS, or credential prompts must not overwrite the partition choice.
        if mediator.selected_partition_identifiers is not None:
            volume_scanner_options.partitions = list(
                mediator.selected_partition_identifiers)

        delta_lister = file_entry_lister.FileEntryLister(
            config.TO_DISK_PATH, volume_scanner_options, mediator=mediator, ignore_dirs=config.ignore_dirs, allow_dirs=config.allow_dirs)

        # ls parition to make sure it's the right one:
        if not partition:
            entries = list(
                parent_lister.file_system.GetRootFileEntry().sub_file_entries)
            ls_root = [e.name for e in entries]
            logging.info(f"Partition {partition} root files: {ls_root}")

        used_partition_prompt = mediator.selected_partition_identifiers is not None
        diff_config = config.diff_config
        differ = diskdiff.DiskDiffer(
            parent_lister, delta_lister,
            **diff_config
        )

    # Interactive selection has no stable key visible to --show. Always
    # regenerate that run so a new choice cannot reuse another partition's data.
    USE_CACHE = config.USE_CACHE and not (config.USE_DISK and (not config.PARTITION or used_partition_prompt))

    run_process_path = config.RUN_MEMORY_PATH if config.USE_MEMORY else None

    cache = diffcache.DiffCache(
        config.RUN_DISK_PATH, config.RUN_TREE_PATH, run_process_path)

    if (USE_CACHE and cache.tree_cache_exists()
            and (not config.USE_DISK or cache.cache_exists())
            and (not config.USE_MEMORY or cache.process_cache_exists())):
        # Slice off the leading "/" and trailing "/disk"
        results_dir = os.path.join(*cache.run_path.parts[1:-1])
        logging.info(f"Results already cached at: {str(results_dir)}")
        # The diffs can be accessed via cache.get_diff_from_cache(path)
    else:
        cache.invalidate()
        logging.info("No cache found, diffing... ")

        if config.USE_DISK:
            logging.info("Diffing disk... ")

            # Get results and cache them.
            differ.get_changed_files()
            results = differ.diff_all()

            if not results:
                logging.info("No disk differences found.")
            cache.cache_results(results)

            # Now render the tree
            disk_tree = diff_tree.DiffTree(differ)

        if config.USE_MEMORY:
            logging.info("Diffing memory... ")

            plugin_results = load_memory_results()
            if 'windows.pslist.PsList' not in plugin_results:
                raise RuntimeError('Memory comparison requires windows.pslist.PsList')
            from_pslist, to_pslist = plugin_results['windows.pslist.PsList']
            from_envars, to_envars = plugin_results.get(
                "windows.envars.Envars", ([], []))
            from_cmdline, to_cmdline = plugin_results.get(
                "windows.cmdline.CmdLine", ([], []))

            # Load pslists already provided by memory-processing.
            mem_differ = memdiff.MemoryDiffer(from_pslist,
                                              to_pslist,
                                              from_envars=from_envars,
                                              to_envars=to_envars,
                                              from_cmdline=from_cmdline,
                                              to_cmdline=to_cmdline,
                                              ignore_regex=config.IGNORE_PROCESSES_REGEX)

            memdiffs = mem_differ.diff_all()
            if not memdiffs:
                logging.info("No memory differences found.")
            cache.cache_process_results(memdiffs)

            mem_tree = diff_tree.DiffTree(mem_differ)

        if config.USE_DISK and config.USE_MEMORY:
            merged_tree = disk_tree.merge(mem_tree)
        elif config.USE_DISK:
            merged_tree = disk_tree
        elif config.USE_MEMORY:
            merged_tree = mem_tree
        else:
            raise RuntimeError(
                "Must set either USE_DISK or USE_MEMORY, otherwise what am I supposed to diff, huh wise guy")

        logging.debug(merged_tree.children_map)

        cache.cache_tree(merged_tree)
        dump_api_data(cache)
        cache.mark_complete()

        logging.info(f"Saved results to {cache.run_path}")

    return cache


if __name__ == '__main__':
    Main()
