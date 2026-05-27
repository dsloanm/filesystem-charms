# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Kernel pinning logic for Lustre compatibility.

This module exists because the current Lustre .deb packages are built against a
specific kernel. Once DKMS-based Lustre packages are available this whole module
can be deleted and the call-site in charm.py simplified to remove the
``ensure_required_kernel`` call.
"""

import logging
import pathlib
import subprocess

import ops

logger = logging.getLogger(__name__)

REQUIRED_KERNEL = "6.8.0-111-generic"


def ensure_required_kernel(unit: ops.Unit) -> bool:
    """Ensure the unit is running the required kernel, rebooting if necessary.

    Returns True when the correct kernel is already running and the caller may
    proceed.  Returns False when a reboot has been triggered; the caller should
    return immediately and let the hook re-run after the reboot.
    """
    current_kernel = subprocess.check_output(["uname", "-r"], text=True).strip()
    if current_kernel == REQUIRED_KERNEL:
        logger.info("Running kernel %s matches required version; proceeding", current_kernel)
        return True

    unit.status = ops.MaintenanceStatus(f"Installing kernel {REQUIRED_KERNEL}")
    logger.info(
        "Current kernel %s != %s; installing required kernel",
        current_kernel,
        REQUIRED_KERNEL,
    )
    try:
        subprocess.run(
            [
                "apt-get",
                "install",
                "-y",
                f"linux-image-{REQUIRED_KERNEL}",
                f"linux-headers-{REQUIRED_KERNEL}",
                f"linux-modules-{REQUIRED_KERNEL}",
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error("Failed to install kernel %s: %s", REQUIRED_KERNEL, e)
        unit.status = ops.BlockedStatus("Kernel installation failed")
        return False

    _pin_grub_kernel(REQUIRED_KERNEL)
    unit.status = ops.MaintenanceStatus("Rebooting to apply new kernel")
    logger.info("Kernel installed; rebooting unit")
    unit.reboot(now=True)
    return False


def _pin_grub_kernel(kernel_version: str) -> None:
    """Configure GRUB to boot a specific kernel version on next (and subsequent) boots."""
    grub_entry = f"Advanced options for Ubuntu>Ubuntu, with Linux {kernel_version}"
    grub_default_line = f'GRUB_DEFAULT="{grub_entry}"\n'

    grub_cfg = pathlib.Path("/etc/default/grub")
    content = grub_cfg.read_text()

    new_lines = []
    replaced = False
    for line in content.splitlines(keepends=True):
        if line.startswith("GRUB_DEFAULT="):
            new_lines.append(grub_default_line)
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(grub_default_line)

    grub_cfg.write_text("".join(new_lines))
    logger.info("Pinned GRUB default to: %s", grub_entry)
    subprocess.run(["update-grub"], check=True)
