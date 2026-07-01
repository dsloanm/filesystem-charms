# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for lustre_fs.py — Lustre filesystem operations."""

import json
import stat
import subprocess
from pathlib import Path

import lustre_fs
import pytest
from constants import (
    LCTL_EXECUTABLE,
    LNETCTL_EXECUTABLE,
    LUSTRE_MGS_MDT_DATASET_PREFIX,
    LUSTRE_OST_DATASET_PREFIX,
    MKFS_LUSTRE_EXECUTABLE,
    MOUNT_EXECUTABLE,
    ZPOOL_EXECUTABLE,
)
from errors import LustreFilesystemError


@pytest.fixture(scope="function")
def mock_run(mocker):
    """Mock subprocess.run."""
    return mocker.patch("lustre_fs.subprocess.run")


@pytest.fixture
def pool_missing(mocker):
    """Mock _pool_exists to return False."""
    mocker.patch("lustre_fs._pool_exists", return_value=False)


@pytest.fixture
def pool_exists(mocker):
    """Mock _pool_exists to return True."""
    mocker.patch("lustre_fs._pool_exists", return_value=True)


class TestInit:
    """init() tests."""

    def test_successful_init(self, mocker):
        """Successful init() call."""
        mock_ensure = mocker.patch("lustre_fs._ensure_lnet_tcp", autospec=True)
        mock_persist = mocker.patch("lustre_fs._persist_lnet_config", autospec=True)
        mocker.patch("lustre_fs._get_default_interface", return_value="eth0")

        lustre_fs.init()

        mock_ensure.assert_called_once_with("eth0")
        mock_persist.assert_called_once()


class TestGetNids:
    """get_nids() tests."""

    def test_success(self, mocker, mock_run):
        """Returns a list of NIDs from lctl output."""
        mock_run.return_value.stdout = "10.0.0.5@tcp\n10.0.0.6@tcp\n"

        assert lustre_fs.get_nids() == ["10.0.0.5@tcp", "10.0.0.6@tcp"]

    def test_empty_output(self, mocker, mock_run):
        """Returns an empty list when no NIDs are configured."""
        mock_run.return_value.stdout = ""

        assert lustre_fs.get_nids() == []

    def test_lctl_run_error(self, mocker, mock_run):
        """Lctl command fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, LCTL_EXECUTABLE)

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs.get_nids()
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)

    def test_lctl_not_found(self, mocker, mock_run):
        """Lctl executable is not found."""
        mock_run.side_effect = FileNotFoundError(1, "/bad/path/to/lctl")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs.get_nids()
        assert isinstance(excinfo.value.__cause__, FileNotFoundError)


class TestMgsMdsSetup:
    """mgs_mds_setup() tests."""

    FSNAME = "testfs"

    def test_correct_pool_and_dataset(self, mocker):
        """Correct pool and dataset names are used for MGS/MDT setup."""
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1"])
        mocker.patch("lustre_fs._mgt_mdt_zpool")
        mock_target = mocker.patch("lustre_fs._lustre_target", autospec=True)
        mocker.patch("lustre_fs._mount")

        lustre_fs.mgs_mds_setup(self.FSNAME)

        _, pool, dataset, index = mock_target.call_args[0]
        assert (pool, dataset, index) == (
            f"{self.FSNAME}-{LUSTRE_MGS_MDT_DATASET_PREFIX}0-pool",
            f"{LUSTRE_MGS_MDT_DATASET_PREFIX}0",
            0,
        )

    def test_zpool_failure(self, mocker):
        """Zpool creation failure."""
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
        """Correct pool and dataset names are used for OST setup."""
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1", "/dev/2"])
        mocker.patch("lustre_fs._ost_zpool")
        mock_target = mocker.patch("lustre_fs._lustre_target", autospec=True)
        mocker.patch("lustre_fs._mount")

        lustre_fs.oss_setup(self.FSNAME, "lustre/2", self.MGS_NID)

        _, pool, dataset, index = mock_target.call_args[0]
        assert (pool, dataset, index) == (
            f"{self.FSNAME}-{LUSTRE_OST_DATASET_PREFIX}2-pool",
            f"{LUSTRE_OST_DATASET_PREFIX}2",
            2,
        )

    def test_bad_unit_name_raises(self, mocker):
        """Bad unit name raises an error."""
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1", "/dev/2"])

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs.oss_setup(self.FSNAME, "badname", self.MGS_NID)
        assert isinstance(excinfo.value.__cause__, IndexError)

    def test_zpool_failure(self, mocker):
        """Zpool creation failure."""
        mocker.patch("lustre_fs._detect_devices", return_value=["/dev/0", "/dev/1"])
        mocker.patch("lustre_fs._ost_zpool", side_effect=ValueError("failure"))

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs.oss_setup(self.FSNAME, "lustre/0", self.MGS_NID)
        assert isinstance(excinfo.value.__cause__, ValueError)


class TestEnsureLnetTcp:
    """_ensure_lnet_tcp() tests."""

    def test_creates_when_missing(self, mocker, mock_run):
        """Adds interface to TCP network when it is not already present."""
        mock_run.return_value.returncode = 1  # net show fails

        lustre_fs._ensure_lnet_tcp("eth0")

        assert mock_run.call_count == 2
        add_call = mock_run.call_args_list[1]
        assert add_call[0][0] == [LNETCTL_EXECUTABLE, "net", "add", "--net", "tcp", "--if", "eth0"]

    def test_skips_when_exists(self, mocker, mock_run):
        """Skips adding the TCP network when it is already present."""
        mock_run.return_value.returncode = 0
        lustre_fs._ensure_lnet_tcp("eth0")
        mock_run.assert_called_once()  # only net show, no net add

    def test_lnetctl_failure(self, mocker, mock_run):
        """Lnetctl failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, LNETCTL_EXECUTABLE)

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._ensure_lnet_tcp("eth0")
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestPersistLnetConfig:
    """_persist_lnet_config() tests."""

    def test_successful_export(self, mocker, tmp_path):
        """Exports the LNet configuration to a file with 0600 permissions."""
        conf_path = tmp_path / "lnet.conf"
        mocker.patch("lustre_fs.LUSTRE_LNET_CONF", conf_path)
        mocker.patch("lustre_fs.subprocess.check_output", return_value="config data")

        lustre_fs._persist_lnet_config()

        assert conf_path.read_text() == "config data"
        assert stat.S_IMODE(conf_path.stat().st_mode) == 0o600

    def test_export_failure(self, mocker):
        """Lnetctl export failure."""
        mocker.patch(
            "lustre_fs.subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, LNETCTL_EXECUTABLE),
        )

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._persist_lnet_config()
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestMgtMdtZpool:
    """_mgt_mdt_zpool() tests."""

    def test_creates_mirror_pool(self, pool_missing, mock_run):
        """Creates a mirrored zpool with the given devices."""
        devices = ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd"]
        lustre_fs._mgt_mdt_zpool("testpool", devices)

        mock_run.assert_called_once()
        expected_cmd = [
            ZPOOL_EXECUTABLE,
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

    def test_skips_when_pool_exists(self, pool_exists, mock_run):
        """Skips creating the zpool when it already exists."""
        lustre_fs._mgt_mdt_zpool("testpool", ["/dev/sda", "/dev/sdb"])

        mock_run.assert_not_called()

    def test_odd_device_count(self, pool_missing):
        """Error when an odd number of devices is provided for mirroring."""
        with pytest.raises(ValueError, match="even number"):
            lustre_fs._mgt_mdt_zpool("testpool", ["/dev/sda", "/dev/sdb", "/dev/sdc"])

    def test_not_enough_devices(self, pool_missing):
        """Error when fewer than 2 devices are provided for mirroring."""
        with pytest.raises(ValueError, match="at least 2"):
            lustre_fs._mgt_mdt_zpool("testpool", ["/dev/sda"])

    def test_zpool_run_error(self, pool_missing, mock_run):
        """Zpool command fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "zpool")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._mgt_mdt_zpool("testpool", ["/dev/sda", "/dev/sdb"])
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestOstZpool:
    """_ost_zpool() tests."""

    def test_creates_raidz2_pool(self, pool_missing, mock_run):
        """Creates a raidz2 zpool with the given devices."""
        devices = ["/dev/sda", "/dev/sdb", "/dev/sdc"]
        lustre_fs._ost_zpool("testpool", devices)

        mock_run.assert_called_once()
        actual_cmd = mock_run.call_args[0][0]
        expected_cmd = [
            ZPOOL_EXECUTABLE,
            "create",
            "-O",
            "canmount=off",
            "testpool",
            "raidz2",
        ] + devices
        assert actual_cmd == expected_cmd

    def test_skips_when_pool_exists(self, pool_exists, mock_run):
        """Skips creating the zpool when it already exists."""
        lustre_fs._ost_zpool("testpool", ["/dev/sda", "/dev/sdb", "/dev/sdc"])

        mock_run.assert_not_called()

    def test_not_enough_devices(self, pool_missing):
        """Error when fewer than 3 devices are provided for raidz2."""
        with pytest.raises(ValueError, match="at least 3"):
            lustre_fs._ost_zpool("testpool", ["/dev/sda", "/dev/sdb"])

    def test_zpool_run_error(self, pool_missing, mock_run):
        """Zpool command fails."""
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

    @pytest.fixture
    def target_missing(self, mocker):
        """Mock _target_exists to return False."""
        mocker.patch("lustre_fs._target_exists", return_value=False)

    @pytest.fixture
    def target_exists(self, mocker):
        """Mock _target_exists to return True."""
        mocker.patch("lustre_fs._target_exists", return_value=True)

    def test_successful_format(self, target_missing, mock_run):
        """Formats the Lustre target with the correct command and flags."""
        lustre_fs._lustre_target(
            self.FSNAME, self.POOL, self.DATASET, 0, mkfs_flags=["--mgs", "--mdt"]
        )

        mock_run.assert_called_once()
        actual_cmd = mock_run.call_args[0][0]
        expected_cmd = [
            MKFS_LUSTRE_EXECUTABLE,
            "--mgs",
            "--mdt",
            "--backfstype=zfs",
            f"--fsname={self.FSNAME}",
            "--index=0",
            self.FULL_DATASET,
        ]
        assert actual_cmd == expected_cmd

    def test_skips_when_target_exists(self, target_exists, mock_run):
        """Skips formatting the Lustre target when it already exists."""
        lustre_fs._lustre_target(
            self.FSNAME, self.POOL, self.DATASET, 0, mkfs_flags=["--mgs", "--mdt"]
        )

        mock_run.assert_not_called()

    def test_mkfs_failure(self, target_missing, mock_run):
        """mkfs.lustre command fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "mkfs.lustre")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._lustre_target(self.FSNAME, self.POOL, self.DATASET, 0, mkfs_flags=["--ost"])
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestMount:
    """_mount() tests."""

    @pytest.fixture(scope="function", autouse=True)
    def mountpoint_tmp(self, tmp_path):
        """Temporary mountpoint directory."""
        return tmp_path / "mnt"

    def test_mounts_when_not_mounted(self, mocker, mountpoint_tmp, mock_run):
        """Mounts the Lustre filesystem when it is not already mounted."""
        lustre_fs._mount("pool", "dataset", mountpoint_tmp)

        mock_run.assert_called_once_with(
            [MOUNT_EXECUTABLE, "-t", "lustre", "pool/dataset", str(mountpoint_tmp)], check=True
        )

    def test_skips_when_already_mounted(self, mocker, mountpoint_tmp, mock_run):
        """Skips mounting the Lustre filesystem when it is already mounted."""
        mocker.patch.object(Path, "is_mount", return_value=True)

        lustre_fs._mount("pool", "dataset", mountpoint_tmp)

        mock_run.assert_not_called()

    def test_mount_failure(self, mocker, mountpoint_tmp, mock_run):
        """Mount command fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, MOUNT_EXECUTABLE)

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._mount("pool", "dataset", mountpoint_tmp)
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)


class TestDetectDevices:
    """_detect_devices() tests."""

    # TODO: Placeholder tests until actual device detection logic is implemented.

    def test_creates_missing_images(self, mocker, tmp_path, mock_run):
        """Creates missing image files for devices."""
        # Patch Path to avoid accessing /root.
        mocker.patch("lustre_fs.Path", side_effect=lambda p: Path(tmp_path) / Path(p).name)

        devices = lustre_fs._detect_devices("")

        assert len(devices) == 4

    def test_truncate_run_error(self, mocker, tmp_path, mock_run):
        """Truncate command fails."""
        mocker.patch("lustre_fs.Path", side_effect=lambda p: Path(tmp_path) / Path(p).name)
        mock_run.side_effect = subprocess.CalledProcessError(1, "truncate")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._detect_devices("")
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)

    def test_skip_existing_images(self, mocker, tmp_path, mock_run):
        """Skips creating image files that already exist."""
        # Create existing image files.
        prefix = "image-prefix"
        for num in range(4):
            (tmp_path / f"{prefix}-disk{num}.img").touch()

        mocker.patch("lustre_fs.Path", side_effect=lambda p: Path(tmp_path) / Path(p).name)

        devices = lustre_fs._detect_devices(prefix)

        assert len(devices) == 4
        mock_run.assert_not_called()


class TestGetDefaultInterface:
    """_get_default_interface() tests."""

    def test_success(self, mocker, mock_run):
        """Successfully retrieves the default network interface."""
        mock_run.return_value.stdout = json.dumps([{"dev": "eth0"}])

        assert lustre_fs._get_default_interface() == "eth0"

    def test_ip_run_error(self, mocker, mock_run):
        """Ip command fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ip")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._get_default_interface()
        assert isinstance(excinfo.value.__cause__, subprocess.CalledProcessError)

    def test_bad_json(self, mocker, mock_run):
        """Ip command returns invalid JSON."""
        mock_run.return_value.stdout = "not json"

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._get_default_interface()
        assert isinstance(excinfo.value.__cause__, json.JSONDecodeError)

    def test_missing_json_data(self, mocker, mock_run):
        """Ip command returns empty JSON array."""
        mock_run.return_value.stdout = json.dumps([])

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._get_default_interface()
        assert isinstance(excinfo.value.__cause__, IndexError)

    def test_missing_dev_key(self, mocker, mock_run):
        """Ip command returns JSON without 'dev' key."""
        mock_run.return_value.stdout = json.dumps([{"not_dev": "eth0"}])

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._get_default_interface()
        assert isinstance(excinfo.value.__cause__, KeyError)


class TestPoolExists:
    """_pool_exists() tests."""

    @pytest.mark.parametrize("returncode, expected", [(0, True), (1, False)])
    def test_existence(self, mocker, returncode, expected, mock_run):
        """Checks if the pool existence check returns the expected result."""
        mock_run.return_value.returncode = returncode
        assert lustre_fs._pool_exists("mypool") is expected

    def test_zpool_run_error(self, mocker, mock_run):
        """Zpool command fails."""
        mock_run.side_effect = FileNotFoundError(1, "/bad/path/to/zpool")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._pool_exists("mypool")
        assert isinstance(excinfo.value.__cause__, FileNotFoundError)


class TestTargetExists:
    """_target_exists() tests."""

    FULL_DATASET = "testfs-mgsmdt0-pool/mgsmdt0"

    @pytest.mark.parametrize("returncode, expected", [(0, True), (1, False)])
    def test_existence(self, mocker, returncode, expected, mock_run):
        """Checks if target existence check returns the expected result."""
        mock_run.return_value.returncode = returncode
        assert lustre_fs._target_exists(self.FULL_DATASET) is expected

    def test_zfs_run_error(self, mocker, mock_run):
        """Zfs command fails."""
        mock_run.side_effect = FileNotFoundError(1, "/bad/path/to/zfs")

        with pytest.raises(LustreFilesystemError) as excinfo:
            lustre_fs._target_exists(self.FULL_DATASET)
        assert isinstance(excinfo.value.__cause__, FileNotFoundError)
