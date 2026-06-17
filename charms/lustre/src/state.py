#!/usr/bin/env python3
# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Check the state of the Lustre charmed operator."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import ops
from constants import (
    LUSTRE_MGS_MDT_MOUNTPOINT,
    LUSTRE_OST_DATASET_PREFIX,
    LUSTRE_OST_MOUNT_DIRECTORY,
)
from exceptions import LustrePeerError

if TYPE_CHECKING:
    from charm import LustreCharm

_logger = logging.getLogger(__name__)


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
        message = "OS error"
        _logger.exception("%s: %s", message, e)
        return ops.BlockedStatus(f"{message}: {e}")

    missing = required_modules - loaded_modules
    if missing:
        return ops.BlockedStatus(
            f"Kernel module(s) not loaded: {', '.join(missing)}"
        )
    return None


def mountpoint_status_change(mountpoint: Path) -> ops.BlockedStatus | None:
    """Check if a mountpoint is healthy.

    Returns:
        ``None`` if the mountpoint exists and is mounted, otherwise a ``BlockedStatus`` describing
        the problem.
    """
    if not mountpoint.exists():
        return ops.BlockedStatus(f"{mountpoint} does not exist")
    if not mountpoint.is_mount():
        return ops.BlockedStatus(f"{mountpoint} is not mounted")
    return None


def peer_relation_app_data_status_change(data) -> ops.WaitingStatus | None:
    """Check if MGS peer app data has been published.

    Returns:
        ``None`` if MGS data is present, otherwise a ``WaitingStatus``.
    """
    if data.mgs_unit_name is None or data.mgs_nid is None:
        return ops.WaitingStatus("Waiting for MGS unit to publish NID")
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
        message = "failed to get peer relation app data"
        _logger.exception("%s: %s", message, e)
        return existing_error_status or ops.BlockedStatus(message.capitalize())

    # Perform checks common to both MGS+MDS and OSS units.
    if (status := _common_status_change(peer_app_data)) is not None:
        return existing_error_status or status

    # Perform role-specific checks for MGS+MDS or OSS.
    if charm.model.unit.name == peer_app_data.mgs_unit_name:
        role_specific_status_change = _mgs_mds_status_change
        active_status = ops.ActiveStatus("MGS+MDS ready")
    else:
        role_specific_status_change = _oss_status_change
        active_status = ops.ActiveStatus("OSS ready")

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
        _logger.error("No OST mountpoints found in %s", mount_directory)
        return ops.BlockedStatus(f"No OST mountpoints found in {mount_directory}")

    for mountpoint in osts:
        if (status := mountpoint_status_change(mountpoint)) is not None:
            return status
    return None
