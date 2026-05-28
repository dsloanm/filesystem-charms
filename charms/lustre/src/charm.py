#!/usr/bin/env python3
# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Charm for the Lustre file system."""

import logging
import subprocess
from pathlib import Path

import ops

import kernel_pin
import lustre_fs
import lustre_packages
from lustre_peers import LustrePeers

logger = logging.getLogger(__name__)


class LustreCharm(ops.CharmBase):
    """Charm for the Lustre file system."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.peers = LustrePeers(self)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.leader_elected, self._on_leader_elected)
        framework.observe(self.on.start, self._on_start)

    def _on_install(self, event: ops.InstallEvent):
        """Install Lustre packages."""
        # TODO: Temporarily pin kernel version and install Lustre packages from a resource tarball
        # until DKMS-based Lustre packages are available.
        if not kernel_pin.ensure_required_kernel(self.unit):
            return

        self.unit.status = ops.MaintenanceStatus("Installing Lustre packages")
        # zfsutils-linux is needed but is not an explicit dependency of the Lustre debs
        subprocess.run(["apt-get", "install", "-y", "zfsutils-linux"], check=True)

        resource_path = Path(self.model.resources.fetch("lustre-packages"))
        logger.info("Fetched lustre-packages resource at: %s", resource_path)
        if not lustre_packages.install_from_resource(self.unit, resource_path):
            return

        lustre_fs.init()
        self.unit.status = ops.ActiveStatus()

    def _on_leader_elected(self, event: ops.LeaderElectedEvent) -> None:
        """Handle new leader election."""
        self.peers.publish_mgs_nid()

    def _on_start(self, event: ops.StartEvent):
        """Set up Lustre services."""
        mgs_unit = self.peers.mgs_unit_name
        if mgs_unit is None:
            self.unit.status = ops.WaitingStatus("Waiting for MGS unit")
            return

        if self.model.unit.name != mgs_unit:
            # OSS units wait for signal from the MGS before proceeding with their own setup.
            # TODO: OSSes will become stuck here if they restart after initial deployment.
            self.unit.status = ops.WaitingStatus(f"Waiting for MGS unit {mgs_unit} to be ready")
            return

        mgs_nid = self.peers.mgs_nid
        if mgs_nid is None:
            self.unit.status = ops.WaitingStatus("Waiting for MGS NID")
            return

        # TODO: Temporarily using fixed image files and constants for testing
        pool = "mgsmdtpool"
        dataset = "mgsmdt"
        mgs_mdt_image = Path("/root/mgs_mdt.img")
        subprocess.run(["truncate", "-s", "1G", mgs_mdt_image], check=True)

        try:
            lustre_fs.create_target(
                pool, dataset, mgs_mdt_image, "1024000", "1G", 0, mkfs_flags=["--mgs", "--mdt"]
            )
            lustre_fs.mount(pool, dataset, Path("/mnt/mgs_mdt"))
        except subprocess.CalledProcessError as e:
            logger.error("Lustre setup failed: %s", e)
            cmd_str = " ".join(e.cmd) if isinstance(e.cmd, list) else str(e.cmd)
            self.unit.status = ops.BlockedStatus(f"Lustre setup failed: {cmd_str}")
            return

        self.unit.status = ops.ActiveStatus("MGS+MDS ready")


if __name__ == "__main__":  # pragma: nocover
    ops.main(LustreCharm)
