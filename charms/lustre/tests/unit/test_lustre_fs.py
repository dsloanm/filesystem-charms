"""Unit tests for lustre_fs.py — Lustre filesystem operations."""

import json
import subprocess
from pathlib import Path
from unittest.mock import call

import pytest

from constants import (
    LUSTRE_LNET_CONF,
    LUSTRE_MGS_MDT_DATASET_PREFIX,
    LUSTRE_MGS_MDT_MOUNTPOINT,
    LUSTRE_OST_DATASET_PREFIX,
    LUSTRE_OST_MOUNT_DIRECTORY,
)
from errors import LustreFilesystemError
from lustre_fs import (
    _detect_devices,
    _get_default_interface,
    _mgt_mdt_zpool,
    _mount,
    _ost_zpool,
    _pool_exists,
    _target_exists,
    _lustre_target,
    init,
    mgs_mds_setup,
    oss_setup,
)


class TestGetDefaultInterface:
    """_get_default_interface() tests."""

    def test_success(self, mocker):
        mock_run = mocker.patch("lustre_fs.subprocess.run")
        mock_run.return_value.stdout = json.dumps([{"dev": "eth0"}])

        assert _get_default_interface() == "eth0"

    def test_ip_run_error(self, mocker):
        mocker.patch("lustre_fs.subprocess.run", side_effect=subprocess.CalledProcessError(1, "ip"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            _get_default_interface()
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)

    def test_bad_json(self, mocker):
        mock_run = mocker.patch("lustre_fs.subprocess.run")
        mock_run.return_value.stdout = "not json"

        with pytest.raises(LustreFilesystemError) as excinfo:
            _get_default_interface()
        assert isinstance(excinfo.value.__cause__, json.JSONDecodeError)

    def test_missing_json_data(self, mocker):
        mock_run = mocker.patch("lustre_fs.subprocess.run")
        mock_run.return_value.stdout = json.dumps([])

        with pytest.raises(LustreFilesystemError) as excinfo:
            _get_default_interface()
        assert isinstance(excinfo.value.__cause__, IndexError)

    def test_missing_dev_key(self, mocker):
        mock_run = mocker.patch("lustre_fs.subprocess.run")
        mock_run.return_value.stdout = json.dumps([{"not_dev": "eth0"}])

        with pytest.raises(LustreFilesystemError) as excinfo:
            _get_default_interface()
        assert isinstance(excinfo.value.__cause__, KeyError)


class TestPoolExists:
    """_pool_exists() tests."""

    def test_exists(self, mocker):
        mocker.patch("lustre_fs.subprocess.run").return_value.returncode = 0
        assert _pool_exists("mypool") is True

    def test_does_not_exist(self, mocker):
        mocker.patch("lustre_fs.subprocess.run").return_value.returncode = 1
        assert _pool_exists("mypool") is False

    def test_zpool_run_error(self, mocker):
        mocker.patch("lustre_fs.subprocess.run", side_effect=subprocess.CalledProcessError(1, "zpool"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            _pool_exists("mypool")
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestTargetExists:
    """_target_exists() tests."""

    FULL_DATASET = "testfs-mgsmdt0-pool/mgsmdt0"

    def test_exists(self, mocker):
        mocker.patch("lustre_fs.subprocess.run").return_value.returncode = 0
        assert _target_exists(self.FULL_DATASET) is True

    def test_does_not_exist(self, mocker):
        mocker.patch("lustre_fs.subprocess.run").return_value.returncode = 1
        assert _target_exists(self.FULL_DATASET) is False

    def test_zfs_run_error(self, mocker):
        mocker.patch("lustre_fs.subprocess.run",
                      side_effect=subprocess.CalledProcessError(1, "zfs"))
        with pytest.raises(LustreFilesystemError) as excinfo:
            _target_exists(self.FULL_DATASET)
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestDetectDevices:
    """_detect_devices() tests."""
    # TODO: Placeholder tests until actual device detection logic is implemented.

    def test_creates_missing_images(self, mocker, tmp_path):
        # Patch Path to avoid accessing /root.
        mocker.patch("lustre_fs.Path", side_effect=lambda p: Path(tmp_path) / Path(p).name)
        mocker.patch("lustre_fs.subprocess.run")

        devices = _detect_devices()

        assert len(devices) == 4

    def test_truncate_run_error(self, mocker, tmp_path):
        mocker.patch("lustre_fs.Path", side_effect=lambda p: Path(tmp_path) / Path(p).name)
        mocker.patch("lustre_fs.subprocess.run", side_effect=subprocess.CalledProcessError(1, "truncate"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            _detect_devices()
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)

    def test_skip_existing_images(self, mocker, tmp_path):
        # Create existing image files.
        for num in range(4):
            (tmp_path / f"disk{num}.img").touch()

        mocker.patch("lustre_fs.Path", side_effect=lambda p: Path(tmp_path) / Path(p).name)
        mock_run = mocker.patch("lustre_fs.subprocess.run")

        devices = _detect_devices()

        assert len(devices) == 4
        mock_run.assert_not_called()


class TestMgtMdtZpool:
    """_mgt_mdt_zpool() tests."""

    def test_creates_mirror_pool(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=False)
        mock_run = mocker.patch("lustre_fs.subprocess.run")

        devices = ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd"]
        _mgt_mdt_zpool("testpool", devices)

        mock_run.assert_called_once()
        expected_cmd = ["zpool", "create", "-O", "canmount=off", "testpool",
                        "mirror", "/dev/sda", "/dev/sdb",
                        "mirror", "/dev/sdc", "/dev/sdd"]
        actual_cmd = mock_run.call_args[0][0]
        assert actual_cmd == expected_cmd

    def test_skips_when_pool_exists(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=True)
        mock_run = mocker.patch("lustre_fs.subprocess.run")

        _mgt_mdt_zpool("testpool", ["/dev/sda", "/dev/sdb"])

        mock_run.assert_not_called()

    def test_odd_device_count(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=False)

        with pytest.raises(ValueError, match="even number"):
            _mgt_mdt_zpool("testpool", ["/dev/sda", "/dev/sdb", "/dev/sdc"])

    def test_not_enough_devices(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=False)

        with pytest.raises(ValueError, match="at least 2"):
            _mgt_mdt_zpool("testpool", ["/dev/sda"])

    def test_zpool_run_error(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=False)
        mocker.patch("lustre_fs.subprocess.run", side_effect=subprocess.CalledProcessError(1, "zpool"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            _mgt_mdt_zpool("testpool", ["/dev/sda", "/dev/sdb"])
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestOstZpool:
    """_ost_zpool() tests."""

    def test_creates_raidz2_pool(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=False)
        mock_run = mocker.patch("lustre_fs.subprocess.run")

        devices = ["/dev/sda", "/dev/sdb", "/dev/sdc"]
        _ost_zpool("testpool", devices)

        mock_run.assert_called_once()
        actual_cmd = mock_run.call_args[0][0]
        expected_cmd = ["zpool", "create", "-O", "canmount=off", "testpool", "raidz2"] + devices
        assert actual_cmd == expected_cmd

    def test_skips_when_pool_exists(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=True)
        mock_run = mocker.patch("lustre_fs.subprocess.run")

        _ost_zpool("testpool", ["/dev/sda", "/dev/sdb", "/dev/sdc"])

        mock_run.assert_not_called()

    def test_not_enough_devices(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=False)

        with pytest.raises(ValueError, match="at least 3"):
            _ost_zpool("testpool", ["/dev/sda", "/dev/sdb"])

    def test_zpool_run_error(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=False)
        mocker.patch("lustre_fs.subprocess.run", side_effect=subprocess.CalledProcessError(1, "zpool"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            _ost_zpool("testpool", ["/dev/sda", "/dev/sdb", "/dev/sdc"])
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestMount:
    """_mount() tests."""

    @pytest.fixture(scope="function", autouse=True)
    def mountpoint_tmp(self, tmp_path):
        return tmp_path / "mnt"

    def test_mounts_when_not_mounted(self, mocker, mountpoint_tmp):
        mock_run = mocker.patch("lustre_fs.subprocess.run")

        _mount("pool", "dataset", mountpoint_tmp)

        mock_run.assert_called_once_with(
            ["mount", "-t", "lustre", "pool/dataset", str(mountpoint_tmp)], check=True
        )

    def test_skips_when_already_mounted(self, mocker, mountpoint_tmp):
        mountpoint_tmp.mkdir()
        mocker.patch.object(Path, "is_mount", return_value=True)
        mock_run = mocker.patch("lustre_fs.subprocess.run")

        _mount("pool", "dataset", mountpoint_tmp)

        mock_run.assert_not_called()

    def test_mount_failure(self, mocker, mountpoint_tmp):
        mocker.patch("lustre_fs.subprocess.run", side_effect=subprocess.CalledProcessError(1, "mount"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            _mount("pool", "dataset", mountpoint_tmp)
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestInit:
    """init() tests."""

    def test_success_initializes_lnet(self, mocker):
        mocker.patch("lustre_fs._get_default_interface", return_value="eth0")
        mock_write = mocker.patch.object(Path, "write_text")

        # Function makes two subprocess.run and one subprocess.check_output calls to lnetctl
        mock_run = mocker.patch("lustre_fs.subprocess.run")
        net_show_mock = mocker.MagicMock(returncode=1)
        net_add_mock = mocker.MagicMock(returncode=0)
        mock_run.side_effect = [net_show_mock, net_add_mock]
        mock_check_output = mocker.patch(
            "lustre_fs.subprocess.check_output",
            return_value="lnet config data"
        )

        init()

        # Verify correct arguments in run calls.
        assert mock_run.call_count == 2
        net_add_call = mock_run.call_args_list[1]
        assert net_add_call[0][0] == ["lnetctl", "net", "add", "--net", "tcp", "--if", "eth0"]

        # Verify config persisted.
        mock_check_output.assert_called_once_with(["lnetctl", "export", "--backup"], text=True)
        mock_write.assert_called_once_with("lnet config data")

    def test_skip_add_when_nid_exists(self, mocker):
        mocker.patch("lustre_fs._get_default_interface")
        mock_run = mocker.patch("lustre_fs.subprocess.run")
        mock_run.return_value.returncode = 0

        init()

        mock_run.assert_called_once()

    def test_lnetctl_run_failure(self, mocker):
        mocker.patch("lustre_fs._get_default_interface")
        mocker.patch("lustre_fs.subprocess.run", side_effect=subprocess.CalledProcessError(1, "lnetctl"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            init()
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)

    def test_export_failure(self, mocker):
        mocker.patch("lustre_fs._get_default_interface", return_value="eth0")
        mock_run = mocker.patch("lustre_fs.subprocess.run")
        net_show_mock = mocker.MagicMock(returncode=1)
        net_add_mock = mocker.MagicMock(returncode=0)
        mock_run.side_effect = [net_show_mock, net_add_mock]

        mocker.patch("lustre_fs.subprocess.check_output", side_effect=subprocess.CalledProcessError(1, "lnetctl"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            init()
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestMgsMdsSetup:
    """mgs_mds_setup() tests."""

    FSNAME = "testfs"

    def test_success_setup(self, mocker):
        expected_devices = ["/dev/0", "/dev/1", "/dev/2", "/dev/3"]
        expected_pool = f"{self.FSNAME}-{LUSTRE_MGS_MDT_DATASET_PREFIX}0-pool"
        expected_dataset = f"{LUSTRE_MGS_MDT_DATASET_PREFIX}0"

        mocker.patch("lustre_fs._detect_devices", return_value=expected_devices)
        mock_zpool = mocker.patch("lustre_fs._mgt_mdt_zpool")
        mock_target = mocker.patch("lustre_fs._lustre_target")
        mock_mount = mocker.patch("lustre_fs._mount")

        mgs_mds_setup(self.FSNAME)

        mock_zpool.assert_called_once_with(expected_pool, expected_devices)
        mock_target.assert_called_once_with(
            self.FSNAME, expected_pool, expected_dataset, 0, mkfs_flags=["--mgs", "--mdt"]
        )
        mock_mount.assert_called_once_with(expected_pool, expected_dataset, Path(LUSTRE_MGS_MDT_MOUNTPOINT))

    def test_zpool_failure(self, mocker):
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1"])
        mocker.patch("lustre_fs._mgt_mdt_zpool", side_effect=ValueError("failure"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            mgs_mds_setup(self.FSNAME)
        assert isinstance(excinfo.value.__cause__, ValueError)


class TestOssSetup:
    """oss_setup() tests."""

    FSNAME = "testfs"
    MGS_NID = "10.0.0.1@tcp"

    def test_success_setup(self, mocker):
        unit_name = "lustre/2"
        expected_ost_index = 2
        expected_pool = f"{self.FSNAME}-{LUSTRE_OST_DATASET_PREFIX}{expected_ost_index}-pool"
        expected_dataset = f"{LUSTRE_OST_DATASET_PREFIX}{expected_ost_index}"
        expected_devices = ["/dev/0", "/dev/1", "/dev/2"]

        mocker.patch("lustre_fs._detect_devices", return_value=expected_devices)
        mock_zpool = mocker.patch("lustre_fs._ost_zpool")
        mock_target = mocker.patch("lustre_fs._lustre_target")
        mock_mount = mocker.patch("lustre_fs._mount")

        oss_setup(self.FSNAME, unit_name, self.MGS_NID)

        mock_zpool.assert_called_once_with(expected_pool, expected_devices)
        mock_target.assert_called_once_with(
            self.FSNAME, expected_pool, expected_dataset, expected_ost_index,
            mkfs_flags=["--ost", f"--mgsnode={self.MGS_NID}"],
        )
        mock_mount.assert_called_once_with(
            expected_pool, expected_dataset, Path(f"{LUSTRE_OST_MOUNT_DIRECTORY}/{expected_dataset}")
        )

    def test_bad_unit_name_raises(self, mocker):
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1", "/dev/2"])

        with pytest.raises(LustreFilesystemError) as excinfo:
            oss_setup(self.FSNAME, "badname", self.MGS_NID)
        assert isinstance(excinfo.value.__cause__, IndexError)

    def test_zpool_failure(self, mocker):
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1"])
        mocker.patch("lustre_fs._ost_zpool", side_effect=ValueError("failure"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            oss_setup(self.FSNAME, "lustre/0", self.MGS_NID)
        assert isinstance(excinfo.value.__cause__, ValueError)


class TestLustreTarget:
    """_lustre_target() tests."""

    FSNAME = "testfs"
    POOL = "testfs-mgsmdt0-pool"
    DATASET = "mgsmdt0"
    FULL_DATASET = f"{POOL}/{DATASET}"

    def test_success_format(self, mocker):
        mocker.patch("lustre_fs._target_exists", return_value=False)
        mock_run = mocker.patch("lustre_fs.subprocess.run")

        _lustre_target(self.FSNAME, self.POOL, self.DATASET, 0,
                       mkfs_flags=["--mgs", "--mdt"])

        mock_run.assert_called_once()
        actual_cmd = mock_run.call_args[0][0]
        expected_cmd = [
            "mkfs.lustre",
            "--mgs", "--mdt",
            "--backfstype=zfs",
            f"--fsname={self.FSNAME}",
            "--index=0",
            self.FULL_DATASET,
        ]
        assert actual_cmd == expected_cmd

    def test_skips_when_target_exists(self, mocker):
        mocker.patch("lustre_fs._target_exists", return_value=True)
        mock_run = mocker.patch("lustre_fs.subprocess.run")

        _lustre_target(self.FSNAME, self.POOL, self.DATASET, 0,
                       mkfs_flags=["--mgs", "--mdt"])

        mock_run.assert_not_called()

    def test_mkfs_failure(self, mocker):
        mocker.patch("lustre_fs._target_exists", return_value=False)
        mocker.patch("lustre_fs.subprocess.run",
                      side_effect=subprocess.CalledProcessError(1, "mkfs.lustre"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            _lustre_target(self.FSNAME, self.POOL, self.DATASET, 0,
                           mkfs_flags=["--ost"])
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)
