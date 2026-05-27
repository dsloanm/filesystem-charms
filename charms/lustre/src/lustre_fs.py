#!/usr/bin/env python3
# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Lustre filesystem operations."""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def init() -> None:
    """Load Lustre kernel modules and bring up LNet."""
    subprocess.run(["modprobe", "lustre"], check=True)
    # Skip lnetctl configure if LNet already up. Avoids errors on subsequent calls.
    if subprocess.run(["lctl", "network", "up"], capture_output=True).returncode != 0:
        subprocess.run(["lnetctl", "lnet", "configure"], check=True)


def create_target(
    pool: str,
    dataset: str,
    device: Path,
    device_size: str,
    quota: str,
    index: int,
    mkfs_flags: list[str],
    fsname: str = "lustrefs",
    backfstype: str = "zfs",
) -> None:
    """Format a Lustre ZFS target. Idempotent."""
    full_dataset_name = f"{pool}/{dataset}"

    if subprocess.run(["zfs", "list", full_dataset_name], capture_output=True).returncode == 0:
        logger.info("Dataset %s already exists, skipping creation", full_dataset_name)
        return

    logger.info("Creating Lustre target on device: %s, dataset: %s", device, full_dataset_name)

    flags = [
        *mkfs_flags,
        f"--backfstype={backfstype}",
        f"--fsname={fsname}",
        f"--device-size={device_size}",
        f"--index={index}",
    ]
    subprocess.run(["mkfs.lustre", *flags, full_dataset_name, str(device)], check=True)
    subprocess.run(["zfs", "set", f"quota={quota}", full_dataset_name], check=True)


def mount(pool: str, dataset: str, mountpoint: Path) -> None:
    """Mount a ZFS Lustre target at mountpoint. Idempotent."""
    if mountpoint.is_mount():
        logger.info("%s already mounted, skipping mount attempt", mountpoint)
        return

    full_dataset_name = f"{pool}/{dataset}"
    mountpoint.mkdir(parents=True, exist_ok=True)
    logger.info("Mounting %s at %s", full_dataset_name, mountpoint)
    subprocess.run(["mount", "-t", "lustre", full_dataset_name, str(mountpoint)], check=True)
