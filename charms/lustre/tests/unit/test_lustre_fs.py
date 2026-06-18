"""Unit tests for lustre_fs.py — Lustre filesystem operations."""

import json
import subprocess
from pathlib import Path

import lustre_fs
import pytest
from constants import (
    LUSTRE_MGS_MDT_DATASET_PREFIX,
    LUSTRE_OST_DATASET_PREFIX,
)
from errors import LustreFilesystemError


@pytest.fixture(scope="function")
def mock_run(mocker):
    return mocker.patch("lustre_fs.subprocess.run")


class TestInit:
    """init() tests."""

    def test_successful_init(self, mocker):
        mock_ensure = mocker.patch("lustre_fs._ensure_lnet_tcp")
        mock_persist = mocker.patch("lustre_fs._persist_lnet_config")
        mocker.patch("lustre_fs._get_default_interface", return_value="eth0")

        lustre_fs.init()

        mock_ensure.assert_called_once_with("eth0")
        mock_persist.assert_called_once()


class TestMgsMdsSetup:
    """mgs_mds_setup() tests."""

    FSNAME = "testfs"

    def test_correct_pool_and_dataset(self, mocker):
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1"])
        mocker.patch("lustre_fs._mgt_mdt_zpool")
        mock_target = mocker.patch("lustre_fs._lustre_target")
        mocker.patch("lustre_fs._mount")

        lustre_fs.mgs_mds_setup(self.FSNAME)

        _, pool, dataset, index = mock_target.call_args[0]
        assert (pool, dataset, index) == (
            f"{self.FSNAME}-{LUSTRE_MGS_MDT_DATASET_PREFIX}0-pool",
            f"{LUSTRE_MGS_MDT_DATASET_PREFIX}0",
            0,
        )

    def test_zpool_failure(self, mocker):
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1"])
        mocker.patch("lustre_fs._mgt_mdt_zpool", side_effect=ValueError("failure"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs.mgs_mds_setup(self.FSNAME)
        assert isinstance(excinfo.value.__cause__, ValueError)


class TestOssSetup:
    """oss_setup() tests."""

    FSNAME = "testfs"
    MGS_NID = "10.0.0.1@tcp"

    def test_correct_pool_and_index(self, mocker):
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1", "/dev/2"])
        mocker.patch("lustre_fs._ost_zpool")
        mock_target = mocker.patch("lustre_fs._lustre_target")
        mocker.patch("lustre_fs._mount")

        lustre_fs.oss_setup(self.FSNAME, "lustre/2", self.MGS_NID)

        _, pool, dataset, index = mock_target.call_args[0]
        assert (pool, dataset, index) == (
            f"{self.FSNAME}-{LUSTRE_OST_DATASET_PREFIX}2-pool",
            f"{LUSTRE_OST_DATASET_PREFIX}2",
            2,
        )

    def test_bad_unit_name_raises(self, mocker):
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1", "/dev/2"])

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs.oss_setup(self.FSNAME, "badname", self.MGS_NID)
        assert isinstance(excinfo.value.__cause__, IndexError)

    def test_zpool_failure(self, mocker):
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1"])
        mocker.patch("lustre_fs._ost_zpool", side_effect=ValueError("failure"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs.oss_setup(self.FSNAME, "lustre/0", self.MGS_NID)
        assert isinstance(excinfo.value.__cause__, ValueError)


class TestEnsureLnetTcp:
    """_ensure_lnet_tcp() tests."""

    def test_creates_when_missing(self, mocker, mock_run):
        mock_run.return_value.returncode = 1  # net show fails

        lustre_fs._ensure_lnet_tcp("eth0")

        assert mock_run.call_count == 2
        add_call = mock_run.call_args_list[1]
        assert add_call[0][0] == ["lnetctl", "net", "add", "--net", "tcp", "--if", "eth0"]

    def test_skips_when_exists(self, mocker, mock_run):
        mock_run.return_value.returncode = 0
        lustre_fs._ensure_lnet_tcp("eth0")
        mock_run.assert_called_once()  # only net show, no net add

    def test_lnetctl_failure(self, mocker, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "lnetctl")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._ensure_lnet_tcp("eth0")
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestPersistLnetConfig:
    """_persist_lnet_config() tests."""

    def test_successful_export(self, mocker):
        mock_check = mocker.patch("lustre_fs.subprocess.check_output", return_value="config data")
        mock_write = mocker.patch.object(Path, "write_text")

        lustre_fs._persist_lnet_config()

        mock_check.assert_called_once_with(["lnetctl", "export", "--backup"], text=True)
        mock_write.assert_called_once_with("config data")

    def test_export_failure(self, mocker):
        mocker.patch(
            "lustre_fs.subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, "lnetctl"),
        )

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._persist_lnet_config()
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestMgtMdtZpool:
    """_mgt_mdt_zpool() tests."""

    def test_creates_mirror_pool(self, mocker, mock_run):
        mocker.patch("lustre_fs._pool_exists", return_value=False)

        devices = ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd"]
        lustre_fs._mgt_mdt_zpool("testpool", devices)

        mock_run.assert_called_once()
        expected_cmd = [
            "zpool",
            "create",
            "-O",
            "canmount=off",
            "testpool",
            "mirror",
            "/dev/sda",
            "/dev/sdb",
            "mirror",
            "/dev/sdc",
            "/dev/sdd",
        ]
        actual_cmd = mock_run.call_args[0][0]
        assert actual_cmd == expected_cmd

    def test_skips_when_pool_exists(self, mocker, mock_run):
        mocker.patch("lustre_fs._pool_exists", return_value=True)

        lustre_fs._mgt_mdt_zpool("testpool", ["/dev/sda", "/dev/sdb"])

        mock_run.assert_not_called()

    def test_odd_device_count(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=False)

        with pytest.raises(ValueError, match="even number"):
            lustre_fs._mgt_mdt_zpool("testpool", ["/dev/sda", "/dev/sdb", "/dev/sdc"])

    def test_not_enough_devices(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=False)

        with pytest.raises(ValueError, match="at least 2"):
            lustre_fs._mgt_mdt_zpool("testpool", ["/dev/sda"])

    def test_zpool_run_error(self, mocker, mock_run):
        mocker.patch("lustre_fs._pool_exists", return_value=False)
        mock_run.side_effect = subprocess.CalledProcessError(1, "zpool")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._mgt_mdt_zpool("testpool", ["/dev/sda", "/dev/sdb"])
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestOstZpool:
    """_ost_zpool() tests."""

    def test_creates_raidz2_pool(self, mocker, mock_run):
        mocker.patch("lustre_fs._pool_exists", return_value=False)

        devices = ["/dev/sda", "/dev/sdb", "/dev/sdc"]
        lustre_fs._ost_zpool("testpool", devices)

        mock_run.assert_called_once()
        actual_cmd = mock_run.call_args[0][0]
        expected_cmd = ["zpool", "create", "-O", "canmount=off", "testpool", "raidz2"] + devices
        assert actual_cmd == expected_cmd

    def test_skips_when_pool_exists(self, mocker, mock_run):
        mocker.patch("lustre_fs._pool_exists", return_value=True)

        lustre_fs._ost_zpool("testpool", ["/dev/sda", "/dev/sdb", "/dev/sdc"])

        mock_run.assert_not_called()

    def test_not_enough_devices(self, mocker):
        mocker.patch("lustre_fs._pool_exists", return_value=False)

        with pytest.raises(ValueError, match="at least 3"):
            lustre_fs._ost_zpool("testpool", ["/dev/sda", "/dev/sdb"])

    def test_zpool_run_error(self, mocker, mock_run):
        mocker.patch("lustre_fs._pool_exists", return_value=False)
        mock_run.side_effect = subprocess.CalledProcessError(1, "zpool")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._ost_zpool("testpool", ["/dev/sda", "/dev/sdb", "/dev/sdc"])
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestLustreTarget:
    """_lustre_target() tests."""

    FSNAME = "testfs"
    POOL = "testfs-mgsmdt0-pool"
    DATASET = "mgsmdt0"
    FULL_DATASET = f"{POOL}/{DATASET}"

    def test_successful_format(self, mocker, mock_run):
        mocker.patch("lustre_fs._target_exists", return_value=False)

        lustre_fs._lustre_target(
            self.FSNAME, self.POOL, self.DATASET, 0, mkfs_flags=["--mgs", "--mdt"]
        )

        mock_run.assert_called_once()
        actual_cmd = mock_run.call_args[0][0]
        expected_cmd = [
            "mkfs.lustre",
            "--mgs",
            "--mdt",
            "--backfstype=zfs",
            f"--fsname={self.FSNAME}",
            "--index=0",
            self.FULL_DATASET,
        ]
        assert actual_cmd == expected_cmd

    def test_skips_when_target_exists(self, mocker, mock_run):
        mocker.patch("lustre_fs._target_exists", return_value=True)

        lustre_fs._lustre_target(
            self.FSNAME, self.POOL, self.DATASET, 0, mkfs_flags=["--mgs", "--mdt"]
        )

        mock_run.assert_not_called()

    def test_mkfs_failure(self, mocker, mock_run):
        mocker.patch("lustre_fs._target_exists", return_value=False)
        mock_run.side_effect = subprocess.CalledProcessError(1, "mkfs.lustre")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._lustre_target(self.FSNAME, self.POOL, self.DATASET, 0, mkfs_flags=["--ost"])
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestMount:
    """_mount() tests."""

    @pytest.fixture(scope="function", autouse=True)
    def mountpoint_tmp(self, tmp_path):
        return tmp_path / "mnt"

    def test_mounts_when_not_mounted(self, mocker, mountpoint_tmp, mock_run):
        lustre_fs._mount("pool", "dataset", mountpoint_tmp)

        mock_run.assert_called_once_with(
            ["mount", "-t", "lustre", "pool/dataset", str(mountpoint_tmp)], check=True
        )

    def test_skips_when_already_mounted(self, mocker, mountpoint_tmp, mock_run):
        mountpoint_tmp.mkdir()
        mocker.patch.object(Path, "is_mount", return_value=True)

        lustre_fs._mount("pool", "dataset", mountpoint_tmp)

        mock_run.assert_not_called()

    def test_mount_failure(self, mocker, mountpoint_tmp, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "mount")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._mount("pool", "dataset", mountpoint_tmp)
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestDetectDevices:
    """_detect_devices() tests."""

    # TODO: Placeholder tests until actual device detection logic is implemented.

    def test_creates_missing_images(self, mocker, tmp_path, mock_run):
        # Patch Path to avoid accessing /root.
        mocker.patch("lustre_fs.Path", side_effect=lambda p: Path(tmp_path) / Path(p).name)

        devices = lustre_fs._detect_devices()

        assert len(devices) == 4

    def test_truncate_run_error(self, mocker, tmp_path, mock_run):
        mocker.patch("lustre_fs.Path", side_effect=lambda p: Path(tmp_path) / Path(p).name)
        mock_run.side_effect = subprocess.CalledProcessError(1, "truncate")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._detect_devices()
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)

    def test_skip_existing_images(self, mocker, tmp_path, mock_run):
        # Create existing image files.
        for num in range(4):
            (tmp_path / f"disk{num}.img").touch()

        mocker.patch("lustre_fs.Path", side_effect=lambda p: Path(tmp_path) / Path(p).name)

        devices = lustre_fs._detect_devices()

        assert len(devices) == 4
        mock_run.assert_not_called()


class TestGetDefaultInterface:
    """_get_default_interface() tests."""

    def test_success(self, mocker, mock_run):
        mock_run.return_value.stdout = json.dumps([{"dev": "eth0"}])

        assert lustre_fs._get_default_interface() == "eth0"

    def test_ip_run_error(self, mocker, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "ip")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._get_default_interface()
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)

    def test_bad_json(self, mocker, mock_run):
        mock_run.return_value.stdout = "not json"

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._get_default_interface()
        assert isinstance(excinfo.value.__cause__, json.JSONDecodeError)

    def test_missing_json_data(self, mocker, mock_run):
        mock_run.return_value.stdout = json.dumps([])

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._get_default_interface()
        assert isinstance(excinfo.value.__cause__, IndexError)

    def test_missing_dev_key(self, mocker, mock_run):
        mock_run.return_value.stdout = json.dumps([{"not_dev": "eth0"}])

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._get_default_interface()
        assert isinstance(excinfo.value.__cause__, KeyError)


class TestPoolExists:
    """_pool_exists() tests."""

    @pytest.mark.parametrize("returncode, expected", [(0, True), (1, False)])
    def test_existence(self, mocker, returncode, expected, mock_run):
        mock_run.return_value.returncode = returncode
        assert lustre_fs._pool_exists("mypool") is expected

    def test_zpool_run_error(self, mocker, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "zpool")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._pool_exists("mypool")
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestTargetExists:
    """_target_exists() tests."""

    FULL_DATASET = "testfs-mgsmdt0-pool/mgsmdt0"

    @pytest.mark.parametrize("returncode, expected", [(0, True), (1, False)])
    def test_existence(self, mocker, returncode, expected, mock_run):
        mock_run.return_value.returncode = returncode
        assert lustre_fs._target_exists(self.FULL_DATASET) is expected

    def test_zfs_run_error(self, mocker, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "zfs")
        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._target_exists(self.FULL_DATASET)
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)
