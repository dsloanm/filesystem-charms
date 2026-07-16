# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details

"""Lustre package repository setup."""

import platform
from subprocess import CalledProcessError

from charmlibs import apt

from lustre_ops.constants import LUSTRE_REPOSITORY_KEY, LUSTRE_REPOSITORY_URI
from lustre_ops.errors import RepositoryError

_LUSTRE_REPO_FILENAME = "lustre-repo"
_LUSTRE_REPO_GROUPS = ["main"]


def setup_lustre_repository() -> None:
    """Configure the Lustre package repository. Idempotent.

    Raises:
        RepositoryError: If repository setup fails.
    """
    try:
        release = platform.freedesktop_os_release()["VERSION_CODENAME"]
    except KeyError as e:
        raise RepositoryError("Failed to determine OS version codename") from e

    repo = apt.DebianRepository(
        enabled=True,
        repotype="deb",
        uri=LUSTRE_REPOSITORY_URI,
        release=release,
        groups=_LUSTRE_REPO_GROUPS,
        filename=_LUSTRE_REPO_FILENAME,
    )

    try:
        repo.import_key(LUSTRE_REPOSITORY_KEY)
    except apt.GPGKeyError as e:
        raise RepositoryError("Failed to import Lustre repository GPG key") from e

    try:
        apt.RepositoryMapping().add(repo)
        apt.update()
    except CalledProcessError as e:
        raise RepositoryError("Failed to add or refresh Lustre repository") from e
