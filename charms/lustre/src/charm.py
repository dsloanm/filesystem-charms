#!/usr/bin/env python3
# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Charm for the Lustre file system."""

import logging
import platform
from subprocess import CalledProcessError

import lustre_fs
import ops
from charmed_hpc_libs.ops import StopCharm, refresh
from charmlibs import apt
from constants import LUSTRE_REPOSITORY_KEY, LUSTRE_REPOSITORY_URI
from lustre_peers import LustrePeers

logger = logging.getLogger(__name__)


class LustreCharm(ops.CharmBase):
    """Charm for the Lustre file system."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.peers = LustrePeers(self)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.start, self._on_start)

    @refresh()
    def _on_install(self, event: ops.InstallEvent):
        """Install Lustre packages."""
        self.unit.status = ops.MaintenanceStatus("Installing Lustre packages")

        # Lustre packages are not in the Ubuntu archive. Add an external repository.
        try:
            release = platform.freedesktop_os_release()["VERSION_CODENAME"]
        except KeyError as e:
            msg = "failed to determine Ubuntu version codename to configure Lustre repository"
            logger.error(msg + ": %s", e)
            raise StopCharm(ops.BlockedStatus(msg.capitalize()))

        try:
            repo = apt.DebianRepository(
                enabled=True,
                repotype="deb",
                uri=LUSTRE_REPOSITORY_URI,
                release=release,
                groups=["main"],
                filename="lustre-repo",
            )
            repo.import_key(LUSTRE_REPOSITORY_KEY)
            repositories = apt.RepositoryMapping()
            repositories.add(repo)
            apt.update()
        except (apt.GPGKeyError, CalledProcessError) as e:
            msg = f"failed to add {LUSTRE_REPOSITORY_URI} package repository"
            logger.error(msg + ": %s", e)
            raise StopCharm(ops.BlockedStatus(msg.capitalize()))

        # ZFS packages are needed but are not an explicit dependency of the Lustre debs.
        # FIXME: `zfs-dkms` must be installed first to ensure development headers are present for
        # the Lustre DKMS modules to build without "Error!  Build of osd_zfs.ko failed".
        for install_packages in (
            ["zfs-dkms", "zfsutils-linux"],
            ["lustre-server-modules-dkms", "lustre-server-utils"],
        ):
            try:
                apt.add_package(install_packages)
            except (apt.PackageNotFoundError, apt.PackageError) as e:
                msg = f"failed to install packages {install_packages}"
                logger.error(msg + ": %s", e)
                raise StopCharm(ops.BlockedStatus(msg.capitalize()))

        lustre_fs.init()
        self.unit.status = ops.MaintenanceStatus("Lustre packages installed")

    def _on_start(self, event: ops.StartEvent):
        """Set up Lustre services."""
        self.unit.status = ops.MaintenanceStatus("Starting Lustre services")

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
