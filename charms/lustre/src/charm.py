#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm for the Lustre file system."""

import logging
import platform
from dataclasses import dataclass
from subprocess import CalledProcessError

import lustre_fs
import ops
from charmed_hpc_libs.ops import StopCharm, refresh
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
from errors import LustreFilesystemError
from lustre_peer import LustrePeerObserver
from state import LustrePeerError, check_lustre

logger = logging.getLogger(__name__)
refresh = refresh(hook=check_lustre)


@dataclass(frozen=True)
class CharmStatuses:
    """Charm status messages."""

    REPO_SETUP = "Setting up package repository"
    FAILED_OS_CODENAME = "Failed to determine OS version codename"
    FAILED_IMPORT_GPG_KEY = "Failed to import GPG key for Lustre package repository"
    FAILED_ADD_REPO = "Failed to add Lustre package repository"
    PACKAGE_INSTALL = "Installing Lustre packages"
    LNET_INIT = "Initializing LNet"
    FAILED_LNET_INIT = "Lustre filesystem initialization failed"
    PREPARING_SERVICES = "Preparing to start Lustre services"
    STARTING_SERVICES = "Starting Lustre services"
    FAILED_PEER_DATA = "Failed to get peer relation app data"
    FAILED_MGS_MDS_SETUP = "Failed to set up MGS+MDS"
    FAILED_SERVICE_SETUP = "Failed to start Lustre services"

    _FAILED_INSTALL_TEMPLATE = "Failed to install packages: {packages}"

    @classmethod
    def failed_install(cls, packages: list[str]) -> str:
        """Format the package failure message."""
        return cls._FAILED_INSTALL_TEMPLATE.format(packages=packages)


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
        self.unit.status = ops.MaintenanceStatus(CharmStatuses.REPO_SETUP)
        success = self._setup_lustre_repository()
        if not success:
            return

        self.unit.status = ops.MaintenanceStatus(CharmStatuses.PACKAGE_INSTALL)
        try:
            apt.add_package(LUSTRE_PACKAGES)
        except (apt.PackageNotFoundError, apt.PackageError) as e:
            logger.exception("failed to install packages: %s. reason: %s", LUSTRE_PACKAGES, e)
            self.unit.status = ops.BlockedStatus(CharmStatuses.failed_install(LUSTRE_PACKAGES))
            return

        self.unit.status = ops.MaintenanceStatus(CharmStatuses.LNET_INIT)
        try:
            lustre_fs.init()
        except LustreFilesystemError as e:
            logger.exception("failed to initialize Lustre filesystem: %s", e)
            self.unit.status = ops.BlockedStatus(CharmStatuses.FAILED_LNET_INIT)
            return

        self.unit.status = ops.MaintenanceStatus(CharmStatuses.PREPARING_SERVICES)

    @refresh
    def _on_start(self, _: ops.StartEvent):
        """Set up Lustre services."""
        self.unit.status = ops.MaintenanceStatus(CharmStatuses.STARTING_SERVICES)

        try:
            data = self.peers.get_app_data()
        except LustrePeerError as e:
            logger.exception("failed to read peer relation data: %s", e)
            raise StopCharm(ops.BlockedStatus(CharmStatuses.FAILED_PEER_DATA))

        mgs_unit = data.mgs_unit_name
        mgs_nid = data.mgs_nid

        if mgs_unit is None or mgs_nid is None:
            # No MGS has been published yet. This is initial deployment.
            if self.unit.is_leader():
                # Initial leader is MGS+MDS for lifetime of deployment.
                try:
                    lustre_fs.mgs_mds_setup(LUSTRE_FSNAME)
                    mgs_nid = self.peers.mgs_nid_published()
                except (LustrePeerError, LustreFilesystemError) as e:
                    logger.exception("failed to set up MGS+MDS: %s", e)
                    raise StopCharm(ops.BlockedStatus(CharmStatuses.FAILED_MGS_MDS_SETUP))

                self.filesystem.set_info(LustreInfo(mgs_ids=[mgs_nid], fs_name=LUSTRE_FSNAME))

            # Initial non-leaders are OSSes and must wait for leader to publish MGS info in the peer
            # relation before starting.
            return

        # MGS is already published. This is a restart or a slow OSS initial deployment.
        try:
            if self.model.unit.name == mgs_unit:
                lustre_fs.mgs_mds_setup(LUSTRE_FSNAME)
            else:
                # OSS can start immediately if MGS info is already available. No need to wait for a peer
                # relation event.
                lustre_fs.oss_setup(LUSTRE_FSNAME, self.model.unit.name, mgs_nid)
        except LustreFilesystemError as e:
            logger.exception("failed to set up Lustre services: %s", e)
            raise StopCharm(ops.BlockedStatus(CharmStatuses.FAILED_SERVICE_SETUP))

    @refresh
    def _on_update_status(self, _: ops.UpdateStatusEvent) -> None:
        """Check the health of Lustre services and update unit status."""

    def _setup_lustre_repository(self) -> bool:
        """Set up the Lustre package repository."""
        try:
            release = platform.freedesktop_os_release()["VERSION_CODENAME"]
        except KeyError as e:
            logger.exception("failed to determine OS version codename: %s", e)
            self.unit.status = ops.BlockedStatus(CharmStatuses.FAILED_OS_CODENAME)
            return False

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
            logger.exception("failed to import GPG key: %s", e)
            self.unit.status = ops.BlockedStatus(CharmStatuses.FAILED_IMPORT_GPG_KEY)
            return False

        try:
            repositories.add(repo)
            apt.update()
        except CalledProcessError as e:
            logger.exception("failed to add repository: %s", e)
            self.unit.status = ops.BlockedStatus(CharmStatuses.FAILED_ADD_REPO)
            return False

        return True


if __name__ == "__main__":  # pragma: nocover
    ops.main(LustreCharm)
