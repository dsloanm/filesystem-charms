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

    def _on_start(self, event: ops.StartEvent):
        """Set up Lustre services."""
        mgs_unit = self.peers.mgs_unit_name
        mgs_nid = self.peers.mgs_nid

        if mgs_unit is None or mgs_nid is None:
            # No MGS has been published yet. This is initial deployment.
            if self.unit.is_leader():
                # Initial leader is MGS+MDS for lifetime of deployment.
                lustre_fs.ensure_mgs_mds_setup()
                self.peers.ensure_mgs_nid_published()
                self.unit.status = ops.ActiveStatus("MGS+MDS ready")
            else:
                # Initial non-leaders are OSSes. Must wait for the leader to publish MGS info in the
                # peer relation before setting up OSS.
                self.unit.status = ops.WaitingStatus("Waiting for MGS unit")
            return

        # MGS is already published. This is a restart or a slow OSS initial deployment.
        if self.model.unit.name == mgs_unit:
            # This unit is the MGS. Ensure MGS+MDS are up.
            lustre_fs.ensure_mgs_mds_setup()
            self.unit.status = ops.ActiveStatus("MGS+MDS ready")
        else:
            # This is an OSS unit. Ensure OSS is up.
            lustre_fs.ensure_oss_setup(self.model.unit.name, mgs_nid)
            self.unit.status = ops.ActiveStatus("OSS ready")


if __name__ == "__main__":  # pragma: nocover
    ops.main(LustreCharm)
