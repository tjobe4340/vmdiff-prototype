import re
import logging
import pathlib

import volume_identity


from dfvfs.helpers import volume_scanner
from dfvfs.lib import definitions as dfvfs_definitions
from dfvfs.lib import errors
from dfvfs.resolver import resolver
from dfvfs.path import factory


class FileEntryLister(volume_scanner.VolumeScanner):
    """File entry lister."""

    _NON_PRINTABLE_CHARACTERS = list(range(0, 0x20)) + list(range(0x7f, 0xa0))
    _ESCAPE_CHARACTERS = str.maketrans({
        value: '\\x{0:02x}'.format(value)
        for value in _NON_PRINTABLE_CHARACTERS})

    def __init__(self, source, volume_scanner_options, mediator=None, ignore_dirs=None, allow_dirs=None, selection_reference=None):
        """Initializes a file entry lister.

        Args:
          mediator (VolumeScannerMediator): a volume scanner mediator.
        """
        super(FileEntryLister, self).__init__(mediator=mediator)

        if ignore_dirs is None:
            ignore_dirs = set()
        if allow_dirs is None:
            allow_dirs = set(["/"])

        self.allow_dirs = allow_dirs
        self.ignore_dirs = ignore_dirs

        self._list_only_files = False

        self.selection_reference = selection_reference
        self.partition_inventory = None
        self.select_all = volume_scanner_options.partitions == ["all"]
        self._namespace = False
        self.base_by_identity = {}

        self.base_path_specs = self.GetBasePathSpecs(
            source, options=volume_scanner_options)

        self.source = source

        if mediator is not None and selection_reference is None:
            self.select_all = self.select_all or getattr(mediator, 'selected_all_partitions', False)
        if selection_reference is not None:
            self.select_all = selection_reference.select_all

        for base in self.base_path_specs:
            identity = volume_identity.describe_base(base)
            if identity.startswith('gpt-') and self.partition_inventory is None:
                table = factory.Factory.NewPathSpec('GPT', location='/', parent=base.parent.parent)
                self.partition_inventory = volume_identity.gpt_inventory(table)
            if identity in self.base_by_identity:
                raise ValueError('Multiple or nested filesystems lack distinct supported GPT identities. '
                                 'Select one filesystem/volume explicitly; APFS/LVM/VSS multi-volume pairing is unsupported.')
            self.base_by_identity[identity] = base

        # A one-partition scan can bypass dfVFS's partition selector. Apply the
        # same identity filter here too, not an index-based accidental match.
        if selection_reference is not None and selection_reference.is_gpt and not self.select_all:
            self.base_by_identity = {
                key: base for key, base in self.base_by_identity.items()
                if key in selection_reference.base_by_identity}
            self.base_path_specs = list(self.base_by_identity.values())

        if selection_reference is not None and selection_reference.is_gpt:
            if self.partition_inventory is None:
                raise ValueError('Target is not a comparable GPT partition table; refusing index-based pairing.')
            # A partition still present in the table but not readable is NOT a
            # deleted partition. Do not emit misleading mass file deletions.
            still_present = set(self.partition_inventory.values()) & set(selection_reference.base_by_identity)
            if still_present - set(self.base_by_identity):
                raise ValueError('A matching GPT partition exists but no supported/readable filesystem was found: ' +
                                 ', '.join(sorted(still_present - set(self.base_by_identity))))

        if not self.base_path_specs and not (
                selection_reference is not None and selection_reference.is_gpt
                and self.partition_inventory is not None):
            raise ValueError(f'{source}: No supported file system found in source.')

        self.base_path_spec = self.base_path_specs[0] if self.base_path_specs else None
        self.file_system = (resolver.Resolver.OpenFileSystem(self.base_path_spec)
                            if self.base_path_spec is not None else None)
        self.file_entries = {}

    @property
    def is_gpt(self):
        return bool(self.base_by_identity) and all(key.startswith('gpt-') for key in self.base_by_identity)

    def _GetPartitionIdentifiers(self, scan_node, options):
        if scan_node.type_indicator == 'GPT':
            self.partition_inventory = volume_identity.gpt_inventory(scan_node.path_spec)
            if self.selection_reference is not None and self.selection_reference.is_gpt:
                if self.selection_reference.select_all:
                    return list(self.partition_inventory)
                identities = self.selection_reference.base_by_identity
                return [identifier for identifier, identity in self.partition_inventory.items()
                        if identity in identities]
        return super()._GetPartitionIdentifiers(scan_node, options)

    @staticmethod
    def _guest_path(path):
        # Keep guest path filtering separate from the comparison namespace.
        if path.startswith('\\'):
            path = pathlib.PureWindowsPath(path).as_posix()
        if not path.startswith('/') or '..' in pathlib.PurePosixPath(path).parts:
            raise ValueError('Invalid guest filesystem path: %r' % path)
        return str(pathlib.PurePosixPath(path))

    def _key(self, identity, guest_path):
        guest_path = self._guest_path(guest_path)
        if not self._namespace:
            return guest_path
        return '/Volumes/' + identity + (guest_path if guest_path != '/' else '')

    def _GetDisplayPath(self, path_spec, path_segments, data_stream_name):
        """Retrieves a path to display.

        Args:
          path_spec (dfvfs.PathSpec): path specification of the file entry.
          path_segments (list[str]): path segments of the full path of the file
              entry.
          data_stream_name (str): name of the data stream.

        Returns:
          str: path to display.
        """
        display_path = ''

        if path_spec.HasParent():
            parent_path_spec = path_spec.parent
            if parent_path_spec and parent_path_spec.type_indicator in (
                    dfvfs_definitions.PARTITION_TABLE_TYPE_INDICATORS):
                display_path = ''.join(
                    [display_path, parent_path_spec.location])

        path_segments = [
            segment.translate(self._ESCAPE_CHARACTERS) for segment in path_segments]
        display_path = ''.join([display_path, '/'.join(path_segments)])

        if data_stream_name:
            data_stream_name = data_stream_name.translate(
                self._ESCAPE_CHARACTERS)
            display_path = ':'.join([display_path, data_stream_name])

        return display_path or '/'

    def _ShouldListDir(self, file_entry):

        location = self._guest_path(file_entry.path_spec.location)

        for allow_dir in self.allow_dirs:
            if location.startswith(allow_dir) or allow_dir.startswith(location):
                for ignore_dir in self.ignore_dirs:
                    if re.search(ignore_dir, location):
                        return False
                return True

        return False

    def _ListFileEntry(
            self, file_entry, identity):
        """Lists a file entry.

        Args:
          file_entry (dfvfs.FileEntry): file entry to list.
        """
        def _dedup_backslashes(path):
            return path.replace("\\\\", "\\")

        location = file_entry.path_spec.location
        if location.startswith("\\"):

            location = _dedup_backslashes(location)

        self.file_entries[self._key(identity, location)] = file_entry

        try:
            for sub_file_entry in file_entry.sub_file_entries:

                if not self._ShouldListDir(sub_file_entry):
                    continue

                self._ListFileEntry(sub_file_entry, identity)

        except OSError as e:
            if "unable to read MFT entry:" in str(e):
                logging.error(
                    f"{self.source}: Unable to list subdirectories for {location}: MFT is corrupted. Try chkdsk first?")
            else:
                logging.error(
                    f"{self.source}: Unable to list subdirectories for {location}")
                logging.debug(
                    f"{self.source}: {e}")

    def ListFileEntries(self):
        """Lists file entries in the base path specification."""
        self.file_entries = {}
        for identity, base_path_spec in self.base_by_identity.items():
            self.file_system = resolver.Resolver.OpenFileSystem(base_path_spec)
            file_entry = resolver.Resolver.OpenFileEntry(base_path_spec)

            if file_entry is None:
                logging.warning(
                    'Unable to open base path specification:\n{0:s}'.format(
                        base_path_spec))
                raise RuntimeError("Unable to open selected filesystem: " + identity)

            self._ListFileEntry(file_entry, identity)

    def GetFileEntry(self, path):
        if self._namespace:
            parts = path.split('/', 3)
            if len(parts) < 3 or parts[1] != 'Volumes':
                return None  # synthetic grouping roots
            identity = parts[2]
            guest_path = '/' + parts[3] if len(parts) == 4 else '/'
            base_path_spec = self.base_by_identity.get(identity)
        else:
            guest_path = path
            base_path_spec = self.base_path_spec
        if base_path_spec is None:
            return None
        guest_path = self._guest_path(guest_path)
        # Use the native root's separator (NTFS roots may be backslash-based).
        if (base_path_spec.location or '').startswith('\\'):
            guest_path = guest_path.replace('/', '\\')
        path_spec = factory.Factory.NewPathSpec(
            base_path_spec.type_indicator, location=guest_path, parent=base_path_spec.parent)
        try:
            return resolver.Resolver.OpenFileEntry(path_spec)
        except errors.BackEndError as exc:
            raise RuntimeError('Unable to open selected filesystem path: ' + path) from exc


def AlignVolumes(before, after):
    """Use identical, collision-free scope on both snapshots, never zip order."""
    identities = set(before.base_by_identity) | set(after.base_by_identity)
    if 'legacy' in identities and len(identities) != 1:
        raise ValueError('Cannot pair GPT and legacy/nested filesystem scopes. '
                         'Select comparable single filesystems explicitly.')
    if len(identities) > 1 and not all(key.startswith('gpt-') for key in identities):
        raise ValueError('Multi-filesystem comparison requires unique GPT partition GUIDs.')
    for identity in set(before.base_by_identity) & set(after.base_by_identity):
        if before.base_by_identity[identity].type_indicator != after.base_by_identity[identity].type_indicator:
            raise ValueError('Filesystem type changed for %s; compare separately rather than pairing incompatible roots.' % identity)
    # GPT is always scoped, including a sole partition. This keeps identifiers
    # stable when a later snapshot adds/removes another partition.
    namespaced = any(key.startswith('gpt-') for key in identities)
    before._namespace = after._namespace = namespaced
