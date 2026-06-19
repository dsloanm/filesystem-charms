#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Check the state of the Lustre charmed operator."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import ops
from constants import (
    LUSTRE_MGS_MDT_MOUNTPOINT,
    LUSTRE_OST_DATASET_PREFIX,
    LUSTRE_OST_MOUNT_DIRECTORY,
)
from errors import LustrePeerError

if TYPE_CHECKING:
    from charm import LustreCharm

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CharmStatuses:
    """Charm status messages."""

    WAITING_PEER_DATA = "Waiting for MGS unit to publish NID"
    FAILED_PEER_DATA = "Failed to get peer relation app data"
    MGS_MDS_READY = "MGS+MDS ready"
    OSS_READY = "OSS ready"

    _MODULES_MISSING_TEMPLATE = "Kernel module(s) not loaded: {modules}"
    _MODULES_PATH_FAILURE_TEMPLATE = "Failed to access path: {modules_path}"
    _MOUNTPOINT_MISSING_TEMPLATE = "{mountpoint} does not exist"
    _MOUNTPOINT_NOT_MOUNTED_TEMPLATE = "{mountpoint} is not mounted"
    _OSTS_MISSING_TEMPLATE = "No OST mountpoints found in {mount_directory}"

    @classmethod
    def modules_missing(cls, modules: list[str]) -> str:
        """Format the kernel modules missing message."""
        return cls._MODULES_MISSING_TEMPLATE.format(modules=", ".join(modules))

    @classmethod
    def modules_path_failure(cls, modules_path: str) -> str:
        """Format the modules path failure message."""
        return cls._MODULES_PATH_FAILURE_TEMPLATE.format(modules_path=modules_path)

    @classmethod
    def mountpoint_missing(cls, mountpoint: Path) -> str:
        """Format the mountpoint missing message."""
        return cls._MOUNTPOINT_MISSING_TEMPLATE.format(mountpoint=mountpoint)

    @classmethod
    def mountpoint_not_mounted(cls, mountpoint: Path) -> str:
        """Format the mountpoint not mounted message."""
        return cls._MOUNTPOINT_NOT_MOUNTED_TEMPLATE.format(mountpoint=mountpoint)

    @classmethod
    def osts_missing(cls, mount_directory: str) -> str:
        """Format the OSTs missing message."""
        return cls._OSTS_MISSING_TEMPLATE.format(mount_directory=mount_directory)


def kernel_modules_status_change(modules_path: str = "/proc/modules") -> ops.BlockedStatus | None:
    """Check if Lustre and LNet kernel modules are installed on the unit.

    Returns:
        ``None`` if all required modules are loaded, otherwise a ``BlockedStatus`` describing which
        modules are missing.
    """
    required_modules = {"lustre", "lnet"}
    loaded_modules = set()
    try:
        with open(modules_path, "r") as f:
            for line in f:
                # First item on each line is module name
                split_line = line.split()
                if split_line:
                    loaded_modules.add(split_line[0])
    except OSError as e:
        _logger.exception("OS error: %s", e)
        return ops.BlockedStatus(CharmStatuses.modules_path_failure(modules_path))

    missing = required_modules - loaded_modules
    if missing:
        # `sorted()` ensures consistent ordering of module names in the status message.
        return ops.BlockedStatus(CharmStatuses.modules_missing(sorted(missing)))
    return None


def mountpoint_status_change(mountpoint: Path) -> ops.BlockedStatus | None:
    """Check if a mountpoint is healthy.

    Returns:
        ``None`` if the mountpoint exists and is mounted, otherwise a ``BlockedStatus`` describing
        the problem.
    """
    if not mountpoint.exists():
        return ops.BlockedStatus(CharmStatuses.mountpoint_missing(mountpoint))
    if not mountpoint.is_mount():
        return ops.BlockedStatus(CharmStatuses.mountpoint_not_mounted(mountpoint))
    return None


def peer_relation_app_data_status_change(data) -> ops.WaitingStatus | None:
    """Check if MGS peer app data has been published.

    Returns:
        ``None`` if MGS data is present, otherwise a ``WaitingStatus``.
    """
    if data.mgs_unit_name is None or data.mgs_nid is None:
        return ops.WaitingStatus(CharmStatuses.WAITING_PEER_DATA)
    return None


def check_lustre(charm: "LustreCharm") -> ops.StatusBase:
    """Check health of Lustre services and update unit status."""
    # If any health check fails and unit is already in BlockedStatus, return the existing status.
    # Avoids overwriting an existing error.
    existing_error_status = None
    if isinstance(charm.unit.status, ops.BlockedStatus):
        existing_error_status = charm.unit.status

    try:
        peer_app_data = charm.peers.get_app_data()
    except LustrePeerError as e:
        _logger.exception("failed to get peer relation app data: %s", e)
        return existing_error_status or ops.BlockedStatus(CharmStatuses.FAILED_PEER_DATA)

    # Perform checks common to both MGS+MDS and OSS units.
    if (status := _common_status_change(peer_app_data)) is not None:
        return existing_error_status or status

    # Perform role-specific checks for MGS+MDS or OSS.
    if charm.model.unit.name == peer_app_data.mgs_unit_name:
        role_specific_status_change = _mgs_mds_status_change
        active_status = ops.ActiveStatus(CharmStatuses.MGS_MDS_READY)
    else:
        role_specific_status_change = _oss_status_change
        active_status = ops.ActiveStatus(CharmStatuses.OSS_READY)

    if (status := role_specific_status_change()) is not None:
        return existing_error_status or status

    if existing_error_status:
        _logger.info("all health checks pass. clearing BlockedStatus: %s", existing_error_status)

    return active_status


def _common_status_change(peer_app_data) -> ops.StatusBase | None:
    """Perform checks common to all Lustre units.

    Returns:
        ``None`` if all checks pass, otherwise the first non-passing status.
    """
    if (status := peer_relation_app_data_status_change(peer_app_data)) is not None:
        return status
    if (status := kernel_modules_status_change()) is not None:
        return status
    return None


def _mgs_mds_status_change() -> ops.StatusBase | None:
    """Perform MGS+MDS unit checks."""
    return mountpoint_status_change(Path(LUSTRE_MGS_MDT_MOUNTPOINT))


def _oss_status_change(mount_directory: str = LUSTRE_OST_MOUNT_DIRECTORY) -> ops.StatusBase | None:
    """Perform OSS unit checks."""
    # Account for multiple OSTs. Example: /mnt/ost0, /mnt/ost1, etc.
    osts = list(Path(mount_directory).glob(f"{LUSTRE_OST_DATASET_PREFIX}*"))
    if len(osts) == 0:
        _logger.error("no OST mountpoints found in %s", mount_directory)
        return ops.BlockedStatus(CharmStatuses.osts_missing(mount_directory=mount_directory))

    for mountpoint in osts:
        if (status := mountpoint_status_change(mountpoint)) is not None:
            return status
    return None
