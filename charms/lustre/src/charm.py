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
    LUSTRE_REPOSITORY_KEY,
    LUSTRE_REPOSITORY_URI,
)
from exceptions import LustreFilesystemError, LustrePeerError, LustreRepositoryError
from lustre_peer import LustrePeer
from state import check_lustre

logger = logging.getLogger(__name__)
refresh = refresh(hook=check_lustre)


class LustreCharm(ops.CharmBase):
    """Charm for the Lustre file system."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.peers = LustrePeer(self)
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
            msg = "Lustre repository setup failed"
            logger.exception("%s: %s", msg, e)
            self.unit.status = ops.BlockedStatus(msg)
            return

        self.unit.status = ops.MaintenanceStatus("Installing Lustre packages")
        # zfsutils-linux is needed but is not an explicit dependency of the Lustre debs.
        packages = ["lustre-server-modules-dkms", "lustre-server-utils", "zfsutils-linux"]
        try:
            apt.add_package(packages)
        except (apt.PackageNotFoundError, apt.PackageError) as e:
            msg = f"failed to install packages {packages}"
            logger.exception(msg + ": %s", e)
            self.unit.status = ops.BlockedStatus(msg.capitalize())
            return

        self.unit.status = ops.MaintenanceStatus("Initializing modules and LNet")
        try:
            lustre_fs.init()
        except LustreFilesystemError as e:
            msg = "Lustre filesystem initialization failed"
            logger.exception("%s: %s", msg, e)
            self.unit.status = ops.BlockedStatus(msg)
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
                self.peers.mgs_nid_published()

                # Ensure info provided to filesystem interface matches the published MGS NID.
                try:
                    data = self.peers.get_app_data()
                except LustrePeerError as e:
                    msg = "failed to get peer relation data after publishing MGS NID"
                    logger.exception("%s: %s", msg, e)
                    self.unit.status = ops.BlockedStatus(msg.capitalize())
                    return
                lustre_info = LustreInfo(mgs_ids=[data.mgs_nid], fs_name=LUSTRE_FSNAME)
                self.filesystem.set_info(lustre_info)

            # Initial non-leaders are OSSes and must wait for the leader to publish MGS info in the
            # peer relation before starting.
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
            raise LustreRepositoryError("Failed to determine Ubuntu version codename") from e

        logger.debug("detected OS release codename: %s", release)

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
            raise LustreRepositoryError(
                f"Failed to add {LUSTRE_REPOSITORY_URI} package repository"
            ) from e


if __name__ == "__main__":  # pragma: nocover
    ops.main(LustreCharm)
