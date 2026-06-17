"""Unit tests for state.py — Lustre health-check functions."""

from pathlib import Path

import ops
import pytest
from errors import LustrePeerError
from lustre_peer import LustrePeerAppData
import state
from state import CharmStatuses


class TestKernelModulesStatus:
    """Kernel module status tests."""

    @pytest.fixture(scope="function")
    def proc_modules_tmp(self, tmp_path):
        """Fixture to create a temporary /proc/modules file."""
        return tmp_path / "proc_modules"

    def test_both_modules_loaded(self, proc_modules_tmp):
        proc_modules_tmp.write_text("lustre 123 0\nlnet 456 0\nother 789 0\n")
        assert state.kernel_modules_status_change(modules_path=str(proc_modules_tmp)) is None

    def test_one_module_missing(self, proc_modules_tmp):
        proc_modules_tmp.write_text("lustre 123 0\nother 789 0\n")
        status_change = state.kernel_modules_status_change(modules_path=str(proc_modules_tmp))
        assert status_change == ops.BlockedStatus(CharmStatuses.modules_missing(["lnet"]))

    def test_both_modules_missing(self, proc_modules_tmp):
        proc_modules_tmp.write_text("other 789 0\n")
        status_change = state.kernel_modules_status_change(modules_path=str(proc_modules_tmp))
        assert status_change == ops.BlockedStatus(
            CharmStatuses.modules_missing(["lnet", "lustre"])
        )

    def test_empty_file(self, proc_modules_tmp):
        proc_modules_tmp.write_text("")
        status_change = state.kernel_modules_status_change(modules_path=str(proc_modules_tmp))
        assert status_change == ops.BlockedStatus(
            CharmStatuses.modules_missing(["lnet", "lustre"])
        )

    def test_newline_file(self, proc_modules_tmp):
        proc_modules_tmp.write_text("\n")
        status_change = state.kernel_modules_status_change(modules_path=str(proc_modules_tmp))
        assert status_change == ops.BlockedStatus(
            CharmStatuses.modules_missing(["lnet", "lustre"])
        )

    def test_file_not_found(self):
        modules_path = "/nonexistent/path"
        status_change = state.kernel_modules_status_change(modules_path=modules_path)
        assert status_change == ops.BlockedStatus(CharmStatuses.modules_path_failure(modules_path))


class TestMountpointStatus:
    """Mountpoint status tests."""

    def test_healthy(self, mocker):
        mocker.patch("pathlib.Path.exists", return_value=True)
        mocker.patch("pathlib.Path.is_mount", return_value=True)
        assert state.mountpoint_status_change(Path("/mnt/test")) is None

    def test_does_not_exist(self, mocker):
        mocker.patch("pathlib.Path.exists", return_value=False)
        mountpoint = Path("/mnt/test")
        status_change = state.mountpoint_status_change(mountpoint)
        assert status_change == ops.BlockedStatus(CharmStatuses.mountpoint_missing(mountpoint))

    def test_not_mounted(self, mocker):
        mocker.patch("pathlib.Path.exists", return_value=True)
        mocker.patch("pathlib.Path.is_mount", return_value=False)
        mountpoint = Path("/mnt/test")
        status_change = state.mountpoint_status_change(mountpoint)
        assert status_change == ops.BlockedStatus(CharmStatuses.mountpoint_not_mounted(mountpoint))


class TestPeerRelationAppDataStatus:
    """Peer relation app data status tests."""

    def test_all_data_present(self):
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid="10.0.0.5@tcp")
        assert state.peer_relation_app_data_status_change(data) is None

    def test_mgs_unit_name_missing(self):
        data = LustrePeerAppData(mgs_unit_name=None, mgs_nid="10.0.0.5@tcp")
        status_change = state.peer_relation_app_data_status_change(data)
        assert status_change == ops.WaitingStatus(CharmStatuses.WAITING_PEER_DATA)

    def test_mgs_nid_missing(self):
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid=None)
        status_change = state.peer_relation_app_data_status_change(data)
        assert status_change == ops.WaitingStatus(CharmStatuses.WAITING_PEER_DATA)

    def test_both_missing(self):
        data = LustrePeerAppData(mgs_unit_name=None, mgs_nid=None)
        status_change = state.peer_relation_app_data_status_change(data)
        assert status_change == ops.WaitingStatus(CharmStatuses.WAITING_PEER_DATA)


class TestCommonStatus:
    """Common checks for all Lustre unit types."""

    def test_all_pass(self, mocker):
        mocker.patch("state.kernel_modules_status_change", return_value=None)
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid="10.0.0.5@tcp")
        assert state._common_status_change(data) is None

    def test_peer_data_missing(self):
        data = LustrePeerAppData(mgs_unit_name=None, mgs_nid=None)
        status_change = state._common_status_change(data)
        assert status_change == ops.WaitingStatus(CharmStatuses.WAITING_PEER_DATA)

    def test_kernel_modules_missing(self, mocker):
        expected_change = ops.BlockedStatus("test modules missing status")
        mocker.patch("state.kernel_modules_status_change", return_value=expected_change)
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid="10.0.0.5@tcp")
        status_change = state._common_status_change(data)
        assert status_change == expected_change


class TestMgsMdsStatus:
    """Checks specific to MGS+MDS units."""

    def test_mgsmds_healthy(self, mocker):
        mocker.patch("state.mountpoint_status_change", return_value=None)
        assert state._mgs_mds_status_change() is None

    def test_mgsmds_unhealthy(self, mocker):
        expected_change = ops.BlockedStatus("test mgsmds unhealthy status")
        mocker.patch("state.mountpoint_status_change", return_value=expected_change)
        status_change = state._mgs_mds_status_change()
        assert status_change == expected_change


class TestOssStatus:
    """Checks specific to OSS units."""

    def test_osts_healthy(self, mocker, tmp_path):
        (tmp_path / "ost0").mkdir()
        mocker.patch("state.mountpoint_status_change", return_value=None)
        assert state._oss_status_change(mount_directory=str(tmp_path)) is None

    def test_one_ost_unhealthy(self, mocker, tmp_path):
        (tmp_path / "ost0").mkdir()
        (tmp_path / "ost1").mkdir()

        expected_change = ops.BlockedStatus("test ost1 unhealthy status")

        def _mock_mountpoint_status(mp):
            if mp.name == "ost1":
                return expected_change
            return None

        mocker.patch("state.mountpoint_status_change", side_effect=_mock_mountpoint_status)
        status_change = state._oss_status_change(mount_directory=str(tmp_path))
        assert status_change == expected_change

    def test_no_osts_exist(self, mocker, tmp_path):
        status_change = state._oss_status_change(mount_directory=str(tmp_path))
        assert status_change == ops.BlockedStatus(
            CharmStatuses.osts_missing(mount_directory=str(tmp_path))
        )


class TestCheckLustre:
    """Tests for top-level check_lustre."""

    @pytest.fixture
    def mock_charm(self, mocker):
        charm = mocker.MagicMock()
        charm.unit.status = ops.ActiveStatus()
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid="10.0.0.5@tcp")
        charm.peers.get_app_data.return_value = data
        return charm

    def test_mgs_mds_healthy(self, mocker, mock_charm):
        """MGS+MDS unit, all checks pass."""
        # Match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/0"

        mocker.patch("state._common_status_change", return_value=None)
        mocker.patch("state._mgs_mds_status_change", return_value=None)

        result = state.check_lustre(mock_charm)
        assert result == ops.ActiveStatus(CharmStatuses.MGS_MDS_READY)

    def test_oss_healthy(self, mocker, mock_charm):
        """OSS unit, all checks pass."""
        # Does NOT match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/1"

        mocker.patch("state._common_status_change", return_value=None)
        mocker.patch("state._oss_status_change", return_value=None)

        result = state.check_lustre(mock_charm)
        assert result == ops.ActiveStatus(CharmStatuses.OSS_READY)

    def test_peer_error(self, mocker, mock_charm):
        mock_charm.peers.get_app_data.side_effect = LustrePeerError()

        result = state.check_lustre(mock_charm)
        assert result == ops.BlockedStatus(CharmStatuses.FAILED_PEER_DATA)

    def test_peer_error_preserve_existing(self, mocker, mock_charm):
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mock_charm.peers.get_app_data.side_effect = LustrePeerError()

        result = state.check_lustre(mock_charm)
        assert result is existing

    def test_common_error_preserve_existing(self, mocker, mock_charm):
        """When common checks fail and unit already Blocked, keep old status."""
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mocker.patch("state._common_status_change", return_value=ops.WaitingStatus())

        result = state.check_lustre(mock_charm)
        assert result is existing

    def test_mds_mgs_error_preserve_existing(self, mocker, mock_charm):
        """When checks pass and unit was Blocked, clear to ActiveStatus."""
        # Match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/0"
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mocker.patch("state._common_status_change", return_value=None)
        mocker.patch("state._mgs_mds_status_change", return_value=ops.BlockedStatus())

        result = state.check_lustre(mock_charm)
        assert result is existing

    def test_mds_mgs_clears_blocked_when_all_healthy(self, mocker, mock_charm):
        """When checks pass and unit was Blocked, clear to ActiveStatus."""
        # Match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/0"
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mocker.patch("state._common_status_change", return_value=None)
        mocker.patch("state._mgs_mds_status_change", return_value=None)

        result = state.check_lustre(mock_charm)
        assert result == ops.ActiveStatus(CharmStatuses.MGS_MDS_READY)
