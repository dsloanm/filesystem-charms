"""Temporary: install Lustre packages from a resource tarball.

This module will be replaced once a proper package repository is available.
"""

import logging
import pathlib
import subprocess
import tarfile
import tempfile

import ops

logger = logging.getLogger(__name__)


def install_from_resource(unit: ops.Unit, resource_path: pathlib.Path) -> bool:
    """Install Lustre .deb packages from a tarball resource.

    Returns True on success, False on failure (unit status is set accordingly).
    """
    if not resource_path.exists():
        logger.error("Resource file not found: %s", resource_path)
        unit.status = ops.BlockedStatus("Resource lustre-packages not available")
        return False

    with tempfile.TemporaryDirectory() as tmpdir:
        logger.info("Extracting Lustre packages to: %s", tmpdir)
        with tarfile.open(resource_path, "r:gz") as tar:
            tar.extractall(path=tmpdir)

        deb_files = sorted(pathlib.Path(tmpdir).glob("*.deb"))
        if not deb_files:
            logger.error("No .deb files found in resource")
            unit.status = ops.BlockedStatus("No .deb packages in resource")
            return False

        logger.info("Installing %d Lustre .deb packages", len(deb_files))
        try:
            subprocess.run(
                ["apt-get", "install", "-y"] + [str(f) for f in deb_files],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error("Failed to install Lustre packages: %s", e)
            unit.status = ops.BlockedStatus("Lustre package installation failed")
            return False

    logger.info("Lustre packages installed successfully")
    return True
