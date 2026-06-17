"""Unit tests for state.py — Lustre health-check functions."""

from pathlib import Path

import ops
import pytest

from state import (
    kernel_modules_status_change,
    mountpoint_status_change,
    peer_relation_app_data_status_change,
    check_lustre,
    _common_status_change,
    _mgs_mds_status_change,
    _oss_status_change,
)
from lustre_peer import LustrePeerAppData
from exceptions import LustrePeerError


class TestKernelModulesStatus:
    """Kernel module status tests."""

    def test_both_modules_loaded(self, tmp_path):
        proc_modules = tmp_path / "proc_modules"
        proc_modules.write_text("lustre 123 0\nlnet 456 0\nother 789 0\n")
        assert kernel_modules_status_change(modules_path=str(proc_modules)) is None

    def test_one_module_missing(self, tmp_path):
        proc_modules = tmp_path / "proc_modules"
        proc_modules.write_text("lustre 123 0\nother 789 0\n")
        status_change = kernel_modules_status_change(modules_path=str(proc_modules))
        assert isinstance(status_change, ops.BlockedStatus)
        assert "lnet" in status_change.message

    def test_both_modules_missing(self, tmp_path):
        proc_modules = tmp_path / "proc_modules"
        proc_modules.write_text("other 789 0\n")
        status_change = kernel_modules_status_change(modules_path=str(proc_modules))
        assert isinstance(status_change, ops.BlockedStatus)
        assert "lustre" in status_change.message
        assert "lnet" in status_change.message

    def test_empty_file(self, tmp_path):
        proc_modules = tmp_path / "proc_modules"
        proc_modules.write_text("")
        status_change = kernel_modules_status_change(modules_path=str(proc_modules))
        assert isinstance(status_change, ops.BlockedStatus)
        assert "lustre" in status_change.message
        assert "lnet" in status_change.message

    def test_file_not_found(self):
        status_change = kernel_modules_status_change(modules_path="/nonexistent/path")
        assert isinstance(status_change, ops.BlockedStatus)
        assert "OS error" in status_change.message


class TestMountpointStatus:
    """Mountpoint status tests."""

    def test_healthy(self, mocker):
        mocker.patch("pathlib.Path.exists", return_value=True)
        mocker.patch("pathlib.Path.is_mount", return_value=True)
        assert mountpoint_status_change(Path("/mnt/test")) is None

    def test_does_not_exist(self, mocker):
        mocker.patch("pathlib.Path.exists", return_value=False)
        status_change = mountpoint_status_change(Path("/mnt/test"))
        assert isinstance(status_change, ops.BlockedStatus)
        assert "not exist" in status_change.message

    def test_not_mounted(self, mocker):
        mocker.patch("pathlib.Path.exists", return_value=True)
        mocker.patch("pathlib.Path.is_mount", return_value=False)
        status_change = mountpoint_status_change(Path("/mnt/test"))
        assert isinstance(status_change, ops.BlockedStatus)
        assert "not mounted" in status_change.message


class TestPeerRelationAppDataStatus:
    """Peer relation app data status tests."""

    def test_all_data_present(self):
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid="10.0.0.5@tcp")
        assert peer_relation_app_data_status_change(data) is None

    def test_mgs_unit_name_missing(self):
        data = LustrePeerAppData(mgs_unit_name=None, mgs_nid="10.0.0.5@tcp")
        status_change = peer_relation_app_data_status_change(data)
        assert isinstance(status_change, ops.WaitingStatus)

    def test_mgs_nid_missing(self):
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid=None)
        status_change = peer_relation_app_data_status_change(data)
        assert isinstance(status_change, ops.WaitingStatus)

    def test_both_missing(self):
        data = LustrePeerAppData(mgs_unit_name=None, mgs_nid=None)
        status_change = peer_relation_app_data_status_change(data)
        assert isinstance(status_change, ops.WaitingStatus)


class TestCommonStatus:
    """Common checks for all Lustre unit types."""

    def test_all_pass(self, mocker):
        mocker.patch("state.kernel_modules_status_change", return_value=None)
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid="10.0.0.5@tcp")
        assert _common_status_change(data) is None

    def test_peer_data_missing(self):
        data = LustrePeerAppData(mgs_unit_name=None, mgs_nid=None)
        status_change = _common_status_change(data)
        assert isinstance(status_change, ops.WaitingStatus)

    def test_kernel_modules_missing(self, mocker):
        mocker.patch("state.kernel_modules_status_change", return_value=ops.BlockedStatus())
        data = LustrePeerAppData(mgs_unit_name="lustre/0", mgs_nid="10.0.0.5@tcp")
        status_change = _common_status_change(data)
        assert isinstance(status_change, ops.BlockedStatus)


class TestMgsMdsStatus:
    """Checks specific to MGS+MDS units."""

    def test_mgsmds_healthy(self, mocker):
        mocker.patch("state.mountpoint_status_change", return_value=None)
        assert _mgs_mds_status_change() is None

    def test_mgsmds_unhealthy(self, mocker):
        mocker.patch("state.mountpoint_status_change", return_value=ops.BlockedStatus())
        status_change = _mgs_mds_status_change()
        assert isinstance(status_change, ops.BlockedStatus)


class TestOssStatus:
    """Checks specific to OSS units."""

    def test_osts_healthy(self, mocker, tmp_path):
        (tmp_path / "ost0").mkdir()
        mocker.patch("state.mountpoint_status_change", return_value=None)
        assert _oss_status_change(mount_directory=str(tmp_path)) is None

    def test_one_ost_unhealthy(self, mocker, tmp_path):
        (tmp_path / "ost0").mkdir()
        (tmp_path / "ost1").mkdir()

        def _mock_mountpoint_status(mp):
            if mp.name == "ost1":
                return ops.BlockedStatus()
            return None

        mocker.patch("state.mountpoint_status_change", side_effect=_mock_mountpoint_status)
        status_change = _oss_status_change(mount_directory=str(tmp_path))
        assert isinstance(status_change, ops.BlockedStatus)

    def test_no_osts_exist(self, mocker, tmp_path):
        status_change = _oss_status_change(mount_directory=str(tmp_path))
        assert isinstance(status_change, ops.BlockedStatus)
        assert "No OST mountpoints found" in status_change.message


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

        result = check_lustre(mock_charm)
        assert result == ops.ActiveStatus("MGS+MDS ready")

    def test_oss_healthy(self, mocker, mock_charm):
        """OSS unit, all checks pass."""
        # Does NOT match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/1"

        mocker.patch("state._common_status_change", return_value=None)
        mocker.patch("state._oss_status_change", return_value=None)

        result = check_lustre(mock_charm)
        assert result == ops.ActiveStatus("OSS ready")

    def test_peer_error(self, mocker, mock_charm):
        mock_charm.peers.get_app_data.side_effect = LustrePeerError()

        result = check_lustre(mock_charm)
        assert isinstance(result, ops.BlockedStatus)

    def test_peer_error_preserve_existing(self, mocker, mock_charm):
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mock_charm.peers.get_app_data.side_effect = LustrePeerError()

        result = check_lustre(mock_charm)
        assert result is existing

    def test_common_error_preserve_existing(self, mocker, mock_charm):
        """When common checks fail and unit already Blocked, keep old status."""
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mocker.patch("state._common_status_change", return_value=ops.WaitingStatus())

        result = check_lustre(mock_charm)
        assert result is existing

    def test_mds_mgs_error_preserve_existing(self, mocker, mock_charm):
        """When checks pass and unit was Blocked, clear to ActiveStatus."""
        # Match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/0"
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mocker.patch("state._common_status_change", return_value=None)
        mocker.patch("state._mgs_mds_status_change", return_value=ops.BlockedStatus())

        result = check_lustre(mock_charm)
        assert result is existing

    def test_mds_mgs_clears_blocked_when_all_healthy(self, mocker, mock_charm):
        """When checks pass and unit was Blocked, clear to ActiveStatus."""
        # Match mgs_unit_name in peer data.
        mock_charm.model.unit.name = "lustre/0"
        existing = ops.BlockedStatus("existing error")
        mock_charm.unit.status = existing
        mocker.patch("state._common_status_change", return_value=None)
        mocker.patch("state._mgs_mds_status_change", return_value=None)

        result = check_lustre(mock_charm)
        assert result == ops.ActiveStatus("MGS+MDS ready")
