# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre health-check unit tests."""

from pathlib import Path

import ops
import pytest
import state
from errors import LustrePeerError, LustreStateError
from lustre_peer import LustrePeerAppData
from state import CharmStatuses


class TestKernelModulesStatus:
    """Kernel module status tests."""

    @pytest.fixture(scope="function")
    def proc_modules_tmp(self, tmp_path):
        """Temporary /proc/modules file."""
        return tmp_path / "proc_modules"

    def test_both_modules_loaded(self, proc_modules_tmp):
        """Both Lustre and LNet modules are loaded."""
        proc_modules_tmp.write_text("lustre 123 0\nlnet 456 0\nother 789 0\n")
        assert state._kernel_modules_check(modules_path=str(proc_modules_tmp)) is None

    def test_one_module_missing(self, proc_modules_tmp):
        """One of the required modules is missing."""
        proc_modules_tmp.write_text("lustre 123 0\nother 789 0\n")

        with pytest.raises(LustreStateError) as e:
            state._kernel_modules_check(modules_path=str(proc_modules_tmp))
        assert e.value.status == ops.BlockedStatus(CharmStatuses.modules_missing(["lnet"]))

    @pytest.mark.parametrize("file_contents", ["other 789 0\n", "", "\n"])
    def test_both_modules_missing(self, proc_modules_tmp, file_contents):
        """Both Lustre and LNet modules are missing."""
        proc_modules_tmp.write_text(file_contents)

        with pytest.raises(LustreStateError) as e:
            state._kernel_modules_check(modules_path=str(proc_modules_tmp))
        assert e.value.status == ops.BlockedStatus(
            CharmStatuses.modules_missing(["lnet", "lustre"])
        )

    def test_file_not_found(self):
        """Modules file not found."""
        modules_path = "/nonexistent/path"

        with pytest.raises(LustreStateError) as e:
            state._kernel_modules_check(modules_path=modules_path)
        assert e.value.status == ops.BlockedStatus(
            CharmStatuses.modules_path_failure(modules_path)
        )


class TestMountpointStatus:
    """Mountpoint status tests."""

    def test_healthy(self, mocker):
        """Mountpoint exists and is mounted."""
        mocker.patch("pathlib.Path.exists", return_value=True)
        mocker.patch("pathlib.Path.is_mount", return_value=True)
        assert state._mountpoint_check(Path("/mnt/test")) is None

    def test_does_not_exist(self, mocker):
        """Mountpoint does not exist."""
        mocker.patch("pathlib.Path.exists", return_value=False)
        mountpoint = Path("/mnt/test")

        with pytest.raises(LustreStateError) as e:
            state._mountpoint_check(mountpoint)
        assert e.value.status == ops.BlockedStatus(CharmStatuses.mountpoint_missing(mountpoint))

    def test_not_mounted(self, mocker):
        """Mountpoint exists but is not mounted."""
        mocker.patch("pathlib.Path.exists", return_value=True)
        mocker.patch("pathlib.Path.is_mount", return_value=False)
        mountpoint = Path("/mnt/test")

        with pytest.raises(LustreStateError) as e:
            state._mountpoint_check(mountpoint)
        assert e.value.status == ops.BlockedStatus(
            CharmStatuses.mountpoint_not_mounted(mountpoint)
        )


class TestCommonStatus:
    """Common checks for all Lustre unit types."""

    def test_all_pass(self, mocker):
        """All common checks pass, no status change."""
        mocker.patch("state._kernel_modules_check")
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid="10.0.0.5@tcp")
        assert state._common_check(data) is None

    def test_peer_data_missing(self):
        """Peer data is missing."""
        data = LustrePeerAppData(mgs_unit_name=None, mgs_nid=None)

        with pytest.raises(LustreStateError) as e:
            state._common_check(data)
        assert e.value.status == ops.WaitingStatus(CharmStatuses.WAITING_PEER_DATA)

    def test_kernel_modules_missing(self, mocker):
        """One of the required modules is missing."""
        expected_status = ops.BlockedStatus("test modules missing status")
        mocker.patch("state._kernel_modules_check", side_effect=LustreStateError(expected_status))
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid="10.0.0.5@tcp")

        with pytest.raises(LustreStateError) as e:
            state._common_check(data)
        assert e.value.status == expected_status


class TestMgsMdsStatus:
    """Checks specific to MGS+MDS units."""

    def test_mgsmds_unhealthy(self, mocker):
        """One of the MGS+MDS checks fails."""
        expected_status = ops.BlockedStatus("test mgsmds unhealthy status")
        mocker.patch("state._mountpoint_check", side_effect=LustreStateError(expected_status))
        with pytest.raises(LustreStateError) as e:
            state._mgs_mds_check()
        assert e.value.status == expected_status


class TestOssStatus:
    """Checks specific to OSS units."""

    def test_osts_healthy(self, mocker, tmp_path):
        """All OST checks pass."""
        (tmp_path / "ost0").mkdir()
        mocker.patch("state._mountpoint_check")
        assert state._oss_check(mount_directory=str(tmp_path)) is None

    def test_one_ost_unhealthy(self, mocker, tmp_path):
        """One OST's checks fails."""
        (tmp_path / "ost0").mkdir()
        (tmp_path / "ost1").mkdir()

        expected_status = ops.BlockedStatus("test ost1 unhealthy status")

        def _mock_mountpoint_status(mp):
            if mp.name == "ost1":
                raise LustreStateError(expected_status)
            return None

        mocker.patch("state._mountpoint_check", side_effect=_mock_mountpoint_status)

        with pytest.raises(LustreStateError) as e:
            state._oss_check(mount_directory=str(tmp_path))
        assert e.value.status == expected_status

    def test_no_osts_exist(self, mocker, tmp_path):
        """No OST mountpoints exist."""
        expected_status = ops.BlockedStatus(
            CharmStatuses.osts_missing(mount_directory=str(tmp_path))
        )
        mocker.patch("state._mountpoint_check", side_effect=LustreStateError(expected_status))

        with pytest.raises(LustreStateError) as e:
            state._oss_check(mount_directory=str(tmp_path))
        assert e.value.status == expected_status


class TestCheckLustre:
    """Tests for top-level check_lustre."""

    @pytest.fixture
    def mock_charm(self, mocker):
        """Mock charm with unit and peer data."""
        charm = mocker.MagicMock()
        charm.unit.status = ops.ActiveStatus()
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid="10.0.0.5@tcp")
        charm.peers.get_app_data.return_value = data
        return charm

    def test_mgs_mds_healthy(self, mocker, mock_charm):
        """MGS+MDS unit, all checks pass."""
        # Match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/0"

        mocker.patch("state._common_check", return_value=None)
        mocker.patch("state._mgs_mds_check", return_value=None)

        result = state.check_lustre(mock_charm)
        assert result == ops.ActiveStatus(CharmStatuses.MGS_MDS_READY)

    def test_oss_healthy(self, mocker, mock_charm):
        """OSS unit, all checks pass."""
        # Does NOT match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/1"

        mocker.patch("state._common_check", return_value=None)
        mocker.patch("state._oss_check", return_value=None)

        result = state.check_lustre(mock_charm)
        assert result == ops.ActiveStatus(CharmStatuses.OSS_READY)

    def test_peer_error(self, mocker, mock_charm):
        """Peer data retrieval fails."""
        mock_charm.peers.get_app_data.side_effect = LustrePeerError()

        result = state.check_lustre(mock_charm)
        assert result == ops.BlockedStatus(CharmStatuses.FAILED_PEER_DATA)

    def test_peer_error_preserve_existing(self, mocker, mock_charm):
        """When peer data retrieval fails and unit already Blocked, keep existing status."""
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mock_charm.peers.get_app_data.side_effect = LustrePeerError()

        result = state.check_lustre(mock_charm)
        assert result is existing

    def test_common_error_preserve_existing(self, mocker, mock_charm):
        """When common checks fail and unit already Blocked, keep existing status."""
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mocker.patch("state._common_check", return_value=ops.WaitingStatus())

        result = state.check_lustre(mock_charm)
        assert result is existing

    def test_mgs_mds_error_preserve_existing(self, mocker, mock_charm):
        """When a check fails and unit was Blocked, keep existing status."""
        # Match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/0"
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mocker.patch("state._common_check", return_value=None)
        mocker.patch(
            "state._mgs_mds_check", side_effect=LustreStateError(ops.BlockedStatus("new"))
        )

        result = state.check_lustre(mock_charm)
        assert result is existing

    def test_mgs_mds_clears_blocked_when_all_healthy(self, mocker, mock_charm):
        """When checks pass and unit was Blocked, clear to ActiveStatus."""
        # Match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/0"
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mocker.patch("state._common_check", return_value=None)
        mocker.patch("state._mgs_mds_check", return_value=None)

        result = state.check_lustre(mock_charm)
        assert result == ops.ActiveStatus(CharmStatuses.MGS_MDS_READY)
