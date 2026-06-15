#!/usr/bin/env python3
# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Charm for the Lustre file system."""

import logging
import platform
from subprocess import CalledProcessError

import lustre_fs
import ops
from charmed_hpc_libs.ops import refresh
from charmlibs import apt
from charms.filesystem_client.v0.filesystem_info import FilesystemProvides, LustreInfo
from constants import (
    FILESYSTEM_PEER_RELATION,
    FILESYSTEM_RELATION,
    LUSTRE_FSNAME,
    LUSTRE_PACKAGES,
    LUSTRE_REPOSITORY_KEY,
    LUSTRE_REPOSITORY_URI,
)
from exceptions import LustreFilesystemError, LustrePeerError, LustreRepositoryError
from lustre_peer import LustrePeerObserver
from state import check_lustre

logger = logging.getLogger(__name__)
refresh = refresh(hook=check_lustre)

class LustreCharm(ops.CharmBase):
    """Charm for the Lustre file system."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.peers = LustrePeerObserver(self)
        self.filesystem = FilesystemProvides(self, FILESYSTEM_RELATION, FILESYSTEM_PEER_RELATION)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.update_status, self._on_update_status)

    def _on_install(self, _: ops.InstallEvent):
        """Install Lustre packages."""
        # Lustre packages are not in the Ubuntu archive. Add an external repository.
        self.unit.status = ops.MaintenanceStatus("Setting up package repository")
        try:
            self._setup_lustre_repository()
        except LustreRepositoryError as e:
            logger.exception("failed to set up package repository: %s", e)
            self.unit.status = ops.BlockedStatus(str(e))
            return

        self.unit.status = ops.MaintenanceStatus("Installing Lustre packages")
        try:
            apt.add_package(LUSTRE_PACKAGES)
        except (apt.PackageNotFoundError, apt.PackageError) as e:
            message = f"failed to install packages {LUSTRE_PACKAGES}"
            logger.exception("%s: %s", message, e)
            self.unit.status = ops.BlockedStatus(message.capitalize())
            return

        self.unit.status = ops.MaintenanceStatus("Initializing LNet")
        try:
            lustre_fs.init()
        except LustreFilesystemError as e:
            message = "Lustre filesystem initialization failed"
            logger.exception("%s: %s", message, e)
            self.unit.status = ops.BlockedStatus(message)
            return

        self.unit.status = ops.MaintenanceStatus("Preparing to start Lustre services")

    @refresh
    def _on_start(self, _: ops.StartEvent):
        """Set up Lustre services."""
        self.unit.status = ops.MaintenanceStatus("Starting Lustre services")

        data = self.peers.get_app_data()
        mgs_unit = data.mgs_unit_name
        mgs_nid = data.mgs_nid

        if mgs_unit is None or mgs_nid is None:
            # No MGS has been published yet. This is initial deployment.
            if self.unit.is_leader():
                # Initial leader is MGS+MDS for lifetime of deployment.
                lustre_fs.mgs_mds_setup(LUSTRE_FSNAME)
                mgs_nid = self.peers.mgs_nid_published()
                self.filesystem.set_info(LustreInfo(mgs_ids=[mgs_nid], fs_name=LUSTRE_FSNAME))

            # Initial non-leaders are OSSes and must wait for leader to publish MGS info in the peer
            # relation before starting.
            return

        # MGS is already published. This is a restart or a slow OSS initial deployment.
        if self.model.unit.name == mgs_unit:
            lustre_fs.mgs_mds_setup(LUSTRE_FSNAME)
        else:
            # OSS can start immediately if MGS info is already available. No need to wait for a peer
            # relation event.
            lustre_fs.oss_setup(LUSTRE_FSNAME, self.model.unit.name, mgs_nid)

    @refresh
    def _on_update_status(self, _: ops.UpdateStatusEvent) -> None:
        """Check the health of Lustre services and update unit status."""

    def _setup_lustre_repository(self) -> None:
        """Set up the Lustre package repository."""
        try:
            release = platform.freedesktop_os_release()["VERSION_CODENAME"]
        except KeyError as e:
            raise LustreRepositoryError("Failed to determine OS version codename") from e

        logger.debug("detected OS release codename: %s", release)

        repo = apt.DebianRepository(
            enabled=True,
            repotype="deb",
            uri=LUSTRE_REPOSITORY_URI,
            release=release,
            groups=["main"],
            filename="lustre-repo",
        )
        repositories = apt.RepositoryMapping()

        try:
            repo.import_key(LUSTRE_REPOSITORY_KEY)
        except apt.GPGKeyError as e:
            raise LustreRepositoryError(f"Failed to import GPG key for Lustre package repository") from e

        try:
            repositories.add(repo)
            apt.update()
        except CalledProcessError as e:
            raise LustreRepositoryError(f"Failed to add Lustre package repository") from e


if __name__ == "__main__":  # pragma: nocover
    ops.main(LustreCharm)
