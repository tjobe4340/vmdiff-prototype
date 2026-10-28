"""Partition identities from the pinned dfVFS public volume metadata API.

GPT partition GUIDs are identities, not a guarantee of filesystem continuity.
No fallback to partition-table position is used for multi-filesystem comparisons.
"""
import uuid
from dfvfs.lib import gpt_helper

from dfvfs.path import factory as path_factory
from dfvfs.helpers.volume_scanner import volume_system_factory as volume_factory


def gpt_inventory(table_path_spec):
    system = volume_factory.Factory.NewVolumeSystem('GPT')
    system.Open(table_path_spec)
    result = {}
    seen = set()
    for identifier in system.volume_identifiers:
        volume = system.GetVolumeByIdentifier(identifier)
        attribute = volume.GetAttribute('identifier')
        try:
            guid = uuid.UUID(str(attribute.value)) if attribute else None
        except (ValueError, TypeError, AttributeError):
            guid = None
        if guid is None or guid.int == 0:
            raise ValueError('GPT partition %s has no valid unique GUID; cannot safely match snapshots.' % identifier)
        key = 'gpt-' + str(guid)
        if key in seen:
            raise ValueError('Duplicate GPT partition GUID %s; cannot safely match snapshots.' % guid)
        seen.add(key)
        result[identifier] = key
    return result


def describe_base(base_path_spec):
    """Return stable identity for a direct GPT filesystem, otherwise legacy.

    APFS/LVM/VSS/encryption layers between partition and filesystem are not
    collapsed onto the outer partition GUID: that would collide sub-volumes.
    """
    partition = base_path_spec.parent
    if partition is not None and partition.type_indicator == 'GPT':
        table = path_factory.Factory.NewPathSpec(
            'GPT', location='/', parent=partition.parent)
        inventory = gpt_inventory(table)
        index = gpt_helper.GPTPathSpecGetEntryIndex(partition)
        identifier = 'p%d' % (index + 1) if index is not None else ''
        ancestor = partition.parent
        while ancestor is not None:
            if ancestor.type_indicator in ('GPT', 'TSK_PARTITION', 'APFS', 'APFS_CONTAINER', 'LVM', 'VSHADOW'):
                raise ValueError('Nested volume identity mapping is unsupported; select a single extracted filesystem.')
            ancestor = ancestor.parent
        if identifier not in inventory:
            raise ValueError('Cannot resolve GPT partition identity for %r.' % identifier)
        return inventory[identifier]
    return 'legacy'
