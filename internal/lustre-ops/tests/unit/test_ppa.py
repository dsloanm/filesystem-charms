# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details

"""Unit tests for `lustre_ops.ppa`."""

from subprocess import CalledProcessError
from unittest.mock import MagicMock

import pytest
from charmlibs import apt
from lustre_ops import ppa
from lustre_ops.errors import RepositoryError
from pytest_mock import MockerFixture


class TestSetupLustreRepository:
    """setup_lustre_repository() tests."""

    @pytest.fixture
    def mock_os_release(self, mocker: MockerFixture) -> MagicMock:
        """Mock platform.freedesktop_os_release."""
        return mocker.patch(
            "lustre_ops.ppa.platform.freedesktop_os_release",
            return_value={"VERSION_CODENAME": "noble"},
        )

    def test_success(self, mocker: MockerFixture, mock_os_release: MagicMock) -> None:
        """Repository is configured and the APT index refreshed."""
        mock_repo = mocker.patch("lustre_ops.ppa.apt.DebianRepository")
        mock_mapping = mocker.patch("lustre_ops.ppa.apt.RepositoryMapping").return_value
        mock_update = mocker.patch("lustre_ops.ppa.apt.update")

        ppa.setup_lustre_repository()

        mock_repo.assert_called_once()
        mock_repo.return_value.import_key.assert_called_once()
        mock_mapping.add.assert_called_once_with(mock_repo.return_value)
        mock_update.assert_called_once()

    def test_missing_version_codename(
        self, mocker: MockerFixture, mock_os_release: MagicMock
    ) -> None:
        """KeyError retrieving the OS codename raises RepositoryError."""
        mock_os_release.return_value = {}
        mocker.patch("lustre_ops.ppa.apt.DebianRepository")

        with pytest.raises(RepositoryError) as excinfo:
            ppa.setup_lustre_repository()
        assert isinstance(excinfo.value.__cause__, KeyError)

    def test_gpg_key_error(self, mocker: MockerFixture, mock_os_release: MagicMock) -> None:
        """GPG key import failure raises RepositoryError."""
        mock_repo = mocker.patch("lustre_ops.ppa.apt.DebianRepository").return_value
        mock_repo.import_key.side_effect = apt.GPGKeyError("bad key")

        with pytest.raises(RepositoryError) as excinfo:
            ppa.setup_lustre_repository()
        assert isinstance(excinfo.value.__cause__, apt.GPGKeyError)

    def test_repo_add_error(self, mocker: MockerFixture, mock_os_release: MagicMock) -> None:
        """Repository add/update failure raises RepositoryError."""
        mocker.patch("lustre_ops.ppa.apt.DebianRepository")
        mock_mapping = mocker.patch("lustre_ops.ppa.apt.RepositoryMapping").return_value
        mock_mapping.add.side_effect = CalledProcessError(1, "apt")

        with pytest.raises(RepositoryError) as excinfo:
            ppa.setup_lustre_repository()
        assert isinstance(excinfo.value.__cause__, CalledProcessError)

    def test_update_error(self, mocker: MockerFixture, mock_os_release: MagicMock) -> None:
        """apt.update() failure raises RepositoryError."""
        mocker.patch("lustre_ops.ppa.apt.DebianRepository")
        mocker.patch("lustre_ops.ppa.apt.RepositoryMapping")
        mocker.patch("lustre_ops.ppa.apt.update", side_effect=CalledProcessError(1, "apt update"))

        with pytest.raises(RepositoryError) as excinfo:
            ppa.setup_lustre_repository()
        assert isinstance(excinfo.value.__cause__, CalledProcessError)
