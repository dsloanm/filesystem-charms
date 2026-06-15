#!/usr/bin/env python3
# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Check the state of the Lustre charmed operator."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import ops
from charmed_hpc_libs.ops import ConditionEvaluation
from constants import (
    LUSTRE_MGS_MDT_MOUNTPOINT,
    LUSTRE_OST_DATASET_PREFIX,
    LUSTRE_OST_MOUNT_DIRECTORY,
)
from exceptions import LustrePeerError

if TYPE_CHECKING:
    from charm import LustreCharm

_logger = logging.getLogger(__name__)


def kernel_modules_installed() -> ConditionEvaluation:
    """Check if Lustre and LNet kernel modules are installed on the unit."""
    required_modules = {"lustre", "lnet"}
    loaded_modules = set()
    try:
        with open("/proc/modules", "r") as f:
            for line in f:
                # First item on each line is module name
                split_line = line.split()
                if split_line:
                    loaded_modules.add(split_line[0])
    except OSError as e:
        return ConditionEvaluation(False, f"OS error: {e}")

    missing = required_modules - loaded_modules
    if missing:
        return ConditionEvaluation(False, f"Kernel module(s) not loaded: {', '.join(missing)}")

    return ConditionEvaluation(True)


def mountpoint_healthy(mountpoint: Path) -> ConditionEvaluation:
    """Check if a mountpoint is healthy."""
    if not mountpoint.exists():
        return ConditionEvaluation(False, f"{mountpoint} does not exist")
    if not mountpoint.is_mount():
        return ConditionEvaluation(False, f"{mountpoint} is not mounted")

    return ConditionEvaluation(True)


def peer_relation_app_data_available(data) -> ConditionEvaluation:
    """Check if the peer relation application data published by the MGS unit is available."""
    if data.mgs_unit_name is None or data.mgs_nid is None:
        return ConditionEvaluation(False, "Waiting for MGS unit to publish NID")

    return ConditionEvaluation(True)


def check_lustre(charm: "LustreCharm") -> ops.StatusBase:
    """Check health of Lustre services and update unit status."""
    # If any health check fails and unit is already in BlockedStatus, return the existing status.
    # Avoids overwriting an existing error.
    existing_error = None
    if isinstance(charm.unit.status, ops.BlockedStatus):
        existing_error = charm.unit.status

    try:
        peer_app_data = charm.peers.get_app_data()
    except LustrePeerError as e:
        message = "failed to get peer relation app data"
        _logger.exception("%s: %s", message, e)
        return existing_error or ops.BlockedStatus(message.capitalize())

    # Perform checks common to both MGS+MDS and OSS units.
    status_change = _common_checks(peer_app_data)
    if status_change is not None:
        return existing_error or status_change

    # Perform role-specific checks for MGS+MDS or OSS.
    if charm.model.unit.name == peer_app_data.mgs_unit_name:
        status_change = _mgs_mds_checks()
        if status_change is not None:
            return existing_error or status_change

        active_status = ops.ActiveStatus("MGS+MDS ready")
    else:
        status_change = _oss_checks()
        if status_change is not None:
            return existing_error or status_change

        active_status = ops.ActiveStatus("OSS ready")

    if existing_error:
        _logger.info("all health checks pass. clearing BlockedStatus: %s", existing_error)

    return active_status


def _common_checks(peer_app_data) -> ops.StatusBase | None:
    """Perform checks common to all Lustre units."""
    ok, message = peer_relation_app_data_available(peer_app_data)
    if not ok:
        return ops.WaitingStatus(message)

    ok, message = kernel_modules_installed()
    if not ok:
        return ops.BlockedStatus(message)


def _mgs_mds_checks() -> ops.StatusBase | None:
    """Perform MGS+MDS unit checks."""
    ok, message = mountpoint_healthy(Path(LUSTRE_MGS_MDT_MOUNTPOINT))
    if not ok:
        return ops.BlockedStatus(message)


def _oss_checks() -> ops.StatusBase | None:
    """Perform OSS unit checks."""
    # Account for multiple OSTs. Example: /mnt/ost0, /mnt/ost1, etc.
    for mountpoint in Path(LUSTRE_OST_MOUNT_DIRECTORY).glob(f"{LUSTRE_OST_DATASET_PREFIX}*"):
        ok, message = mountpoint_healthy(mountpoint)
        if not ok:
            return ops.BlockedStatus(message)
