#!/usr/bin/env python3
# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Lustre filesystem operations."""

import json
import logging
import subprocess
from pathlib import Path

from constants import (
    LUSTRE_MGS_MDT_MOUNTPOINT,
    LUSTRE_OST_DATASET_PREFIX,
    LUSTRE_OST_MOUNT_DIRECTORY,
)
from exceptions import LustreFilesystemError

_logger = logging.getLogger(__name__)


def init() -> None:
    """Load Lustre kernel modules and bring up LNet. Idempotent."""
    # This is idempotent. "modprobe will succeed (and do nothing) if told to insert a module which
    # is already present" - https://man7.org/linux/man-pages/man8/modprobe.8.html
    try:
        subprocess.run(["modprobe", "lustre"], check=True)
    except subprocess.CalledProcessError as e:
        raise LustreFilesystemError("Failed to load Lustre kernel module") from e

    # Enable LNet on default network interface if not already enabled.
    # TODO: Add more robust detection. Multi-rail setup must be considered.
    # TODO: Add InfiniBand support. MVP is scoped to TCP for now.
    interface = _get_default_interface()
    if not _nid_exists(f"{interface}@tcp"):
        try:
            subprocess.run(
                ["lnetctl", "net", "add", "--net", "tcp", "--if", interface], check=True
            )
            # Persist changes.
            result = subprocess.run(
                ["lnetctl", "export", "--backup"], capture_output=True, text=True, check=True
            )
        except subprocess.CalledProcessError as e:
            raise LustreFilesystemError("Failed to configure LNet") from e

        Path("/etc/lnet.conf").write_text(result.stdout)


def mgs_mds_setup(fsname: str) -> None:
    """Set up the MGT and MDT on this unit. Idempotent."""
    pool = "lustrefs-mgsmdt0-pool"
    dataset = "mgsmdt0"

    _logger.info(
        "Ensuring this unit is running MGS+MDS on pool '%s' and dataset '%s'", pool, dataset
    )

    devices = _detect_devices()
    _mgt_mdt_zpool(pool, devices)
    _lustre_target(fsname, pool, dataset, 0, mkfs_flags=["--mgs", "--mdt"])
    _mount(pool, dataset, Path(LUSTRE_MGS_MDT_MOUNTPOINT))

    _logger.info("MGS+MDS on pool '%s' and dataset '%s' ready", pool, dataset)


def oss_setup(fsname: str, unit_name: str, mgs_nid: str) -> None:
    """Set up an OSS on this unit. Idempotent."""
    # Derive OST index from unit name and a fixed stride.
    # TODO confirm appropriate number of disks per vdev, vdev type(s) (mirror/RAIDZ2), vdevs per
    # pool, and pools per OSS (zpools and OSTs are 1:1). See:
    # https://wiki.lustre.org/ZFS_System_Design
    max_osts_per_oss = 1
    unit_num = int(unit_name.split("/")[1])
    ost_index = unit_num * max_osts_per_oss  # + ost_num

    dataset = f"{LUSTRE_OST_DATASET_PREFIX}{ost_index}"
    pool = f"{fsname}-{dataset}-pool"

    _logger.info(
        "Ensuring this unit is running OSS on pool '%s' and dataset '%s' with MGS NID: '%s'",
        pool,
        dataset,
        mgs_nid,
    )

    devices = _detect_devices()
    _ost_zpool(pool, devices)
    _lustre_target(fsname, pool, dataset, ost_index, mkfs_flags=["--ost", f"--mgsnode={mgs_nid}"])
    _mount(pool, dataset, Path(f"{LUSTRE_OST_MOUNT_DIRECTORY}/{dataset}"))

    _logger.info("OST index '%s' for MGS NID '%s' ready", ost_index, mgs_nid)


def _detect_devices() -> list[str]:
    """Detect available block devices for use in pools. Placeholder for actual device detection logic."""
    # TODO: For MVP, return a fixed list of image files as block devices.
    devices = []
    for num in range(4):
        image = Path(f"/root/disk{num}.img")
        if not image.exists():
            subprocess.run(["truncate", "-s", "1G", image], check=True)
        devices.append(str(image))

    return devices


def _mgt_mdt_zpool(pool: str, devices: list[str]) -> None:
    """Create a zpool composed of mirror vdevs for the MGT and MDT. Idempotent."""
    if _pool_exists(pool):
        _logger.info("ZFS pool '%s' already exists. Skipping creation.", pool)
        return

    if len(devices) < 2:
        raise ValueError("MGT/MDT mirror pool requires at least 2 devices.")
    if len(devices) % 2 != 0:
        raise ValueError("MGT/MDT mirror pool requires an even number of devices for mirroring.")

    cmd = ["zpool", "create", "-O", "canmount=off", pool]

    # Break devices into mirror pairs. Example: ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd"]
    # becomes ["mirror", "/dev/sda", "/dev/sdb", "mirror", "/dev/sdc", "/dev/sdd"]
    for i in range(0, len(devices), 2):
        pair = devices[i : i + 2]
        cmd.extend(["mirror", pair[0], pair[1]])

    _logger.info("Creating MGT/MDT zpool with command: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _ost_zpool(pool: str, devices: list[str]) -> None:
    """Create a zpool composed of a RAIDZ2 vdev for OSTs. Idempotent."""
    if _pool_exists(pool):
        _logger.info("ZFS pool '%s' already exists. Skipping creation.", pool)
        return

    if len(devices) < 3:
        raise ValueError("OST pool requires at least 3 devices for RAIDZ2.")

    cmd = ["zpool", "create", "-O", "canmount=off", pool, "raidz2"] + devices

    _logger.info("Creating OST zpool with command: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _lustre_target(
    fsname: str,
    pool: str,
    dataset: str,
    index: int,
    mkfs_flags: list[str],
) -> None:
    """Format a Lustre target on top of an existing ZFS pool. Idempotent."""
    full_dataset_name = f"{pool}/{dataset}"

    if subprocess.run(["zfs", "list", full_dataset_name], capture_output=True).returncode == 0:
        _logger.info("Dataset %s already exists, skipping creation", full_dataset_name)
        return

    _logger.info("Formatting Lustre target: %s", full_dataset_name)

    flags = [
        *mkfs_flags,
        "--backfstype=zfs",
        f"--fsname={fsname}",
        f"--index={index}",
    ]
    subprocess.run(["mkfs.lustre", *flags, full_dataset_name], check=True)


def _mount(pool: str, dataset: str, mountpoint: Path) -> None:
    """Mount a ZFS Lustre target at mountpoint. Idempotent."""
    if mountpoint.is_mount():
        _logger.info("%s already mounted, skipping mount attempt", mountpoint)
        return

    full_dataset_name = f"{pool}/{dataset}"
    mountpoint.mkdir(parents=True, exist_ok=True)
    _logger.info("Mounting %s at %s", full_dataset_name, mountpoint)
    subprocess.run(["mount", "-t", "lustre", full_dataset_name, str(mountpoint)], check=True)


def _get_default_interface():
    """Return the default network interface name for this unit."""
    result = subprocess.run(
        ["ip", "-json", "route", "show", "default"], capture_output=True, text=True, check=True
    )
    routes = json.loads(result.stdout)
    return routes[0]["dev"]


def _nid_exists(nid):
    result = subprocess.run(["lctl", "list_nids"], capture_output=True, text=True, check=True)
    configured_nids = [line for line in result.stdout.splitlines() if line]
    return nid in configured_nids


def _pool_exists(pool: str) -> bool:
    """Return True if a zpool with the given name already exists, False otherwise."""
    return subprocess.run(["zpool", "list", pool], capture_output=True).returncode == 0
