# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.
#
# The integration tests use the Jubilant library. See https://documentation.ubuntu.com/jubilant/
# To learn more about testing, see https://documentation.ubuntu.com/ops/latest/explanation/testing/
#
# Tests declare what state they need via fixtures. Fixtures handle all setup;
# tests only assert. Adding a new test never requires touching existing tests.

import logging
import subprocess
from pathlib import Path

import jubilant
import pytest

logger = logging.getLogger(__name__)

FS_NAME = "lustrefs"
LUSTRE_APP = "lustre"
# TODO: filesystem-client Integration does not exist yet. Use a cluster unit as client for now.
# CLIENT_APP = "filesystem-client"
# CLIENT_UNIT = f"{CLIENT_APP}/0"
CLIENT_UNIT = f"{LUSTRE_APP}/0"
MOUNT_POINT = "/mnt/lustre"
CANARY_CONTENT = "hello world"
EXPECTED_STRIPE_COUNT = 2


def _leader_unit(juju: jubilant.Juju) -> str:
    """Return the name of the leader unit of the lustre application."""
    return next(name for name, unit in juju.status().apps[LUSTRE_APP].units.items() if unit.leader)


def _nonleader_units(juju: jubilant.Juju) -> list[str]:
    """Return the names of all non-leader units of the lustre application."""
    return [name for name, unit in juju.status().apps[LUSTRE_APP].units.items() if not unit.leader]


def _disable_secureboot(model: str):
    """Disable secure boot on the LXD profile for the given Juju model.

    TODO: Remove once Lustre modules do not require secure boot to be disabled.
    """
    model_name = model.split(":")[-1]
    list_result = subprocess.run(
        ["lxc", "profile", "list", "--format=csv", "-c", "n"],
        capture_output=True,
        text=True,
    )
    if list_result.returncode != 0:
        raise RuntimeError(
            f"lxc profile list failed (rc={list_result.returncode}):\nstderr: {list_result.stderr}"
        )
    prefix = f"juju-{model_name}"
    profile_name = next(
        (
            name.strip()
            for name in list_result.stdout.splitlines()
            if name.strip().startswith(prefix)
        ),
        None,
    )
    if profile_name is None:
        raise RuntimeError(
            f"No LXD profile matching prefix '{prefix}' found. "
            f"Available profiles: {list_result.stdout}"
        )
    result = subprocess.run(
        ["lxc", "profile", "set", profile_name, "security.secureboot=false"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"lxc profile set failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lustre_cluster(charm: Path, juju: jubilant.Juju):
    """Deploy a 3-unit Lustre cluster and filesystem-client, then integrate.

    lustre/0 (leader)   → MGS + MDS
    lustre/1            → OSS + OST
    lustre/2            → OSS + OST
    filesystem-client/0 → client node that mounts Lustre at /mnt/lustre

    All tests that need the cluster deployed should request this fixture.
    """
    # TODO: Remove once Lustre modules do not require secure boot to be disabled.
    if juju.model is None:
        raise RuntimeError("juju.model is not set")
    _disable_secureboot(juju.model)

    juju.deploy(
        charm.resolve(),
        app=LUSTRE_APP,
        num_units=3,
        resources={"lustre-packages": "lustre-packages.tar.gz"},
        constraints={"virt-type": "virtual-machine"},
    )
    juju.wait(jubilant.all_active, timeout=600)

    # TODO: filesystem-client Integration does not exist yet. Temp use MGS node as client
    # juju.deploy(CLIENT_APP, app=CLIENT_APP, num_units=1)
    # juju.integrate(f"{LUSTRE_APP}:filesystem-client", f"{CLIENT_APP}:filesystem-client")
    # juju.wait(jubilant.all_active, timeout=600)
    leader = _leader_unit(juju)
    mgs_ip = juju.status().apps[LUSTRE_APP].units[leader].public_address
    juju.exec(f"mkdir -p {MOUNT_POINT}", unit=CLIENT_UNIT)
    mount_result = juju.exec(
        f"mount -t lustre {mgs_ip}@tcp:/{FS_NAME} {MOUNT_POINT}",
        unit=CLIENT_UNIT,
    )
    assert mount_result.return_code == 0, f"Client mount failed: {mount_result.stderr}"


@pytest.fixture(scope="module")
def canary_dir(lustre_cluster, juju: jubilant.Juju) -> str:
    """Create a uniquely named, max-striped directory on the Lustre mount.

    The unique name prevents leftover state from a crashed previous run from
    interfering. lfs setstripe -c -1 means files created inside inherit
    striping across all available OSTs.
    """
    result = juju.exec(f"mktemp -d {MOUNT_POINT}/tmpdir-XXXXXXXX", unit=CLIENT_UNIT)
    assert result.return_code == 0, f"mktemp failed: {result.stderr}"
    dir_path = result.stdout.strip()

    setstripe = juju.exec(f"lfs setstripe -c -1 {dir_path}", unit=CLIENT_UNIT)
    assert setstripe.return_code == 0, f"lfs setstripe failed: {setstripe.stderr}"

    return dir_path


@pytest.fixture(scope="module")
def canary_file(canary_dir: str, juju: jubilant.Juju) -> str:
    """Write the canary file inside the striped directory. Returns the file path.

    Tests that need the file to already exist should request this fixture.
    """
    file_path = f"{canary_dir}/canary.txt"
    result = juju.exec(
        f"printf '%s' '{CANARY_CONTENT}' | tee {file_path}",
        unit=CLIENT_UNIT,
    )
    assert result.return_code == 0, f"Fixture failed to write canary file: {result.stderr}"
    return file_path


# ---------------------------------------------------------------------------
# Cluster topology
# ---------------------------------------------------------------------------


def test_leader_runs_mgs_mds(lustre_cluster, juju: jubilant.Juju):
    """Leader unit hosts the MGS and MDT (combined MGS/MDS node)."""
    leader = _leader_unit(juju)
    result = juju.exec("lctl dl", unit=leader)
    assert result.return_code == 0, f"lctl dl failed on {leader}: {result.stderr}"
    assert "MGS" in result.stdout, f"MGS not found in device list on leader {leader}"
    assert "MDT" in result.stdout, f"MDT not found in device list on leader {leader}"


def test_nonleader_runs_oss(lustre_cluster, juju: jubilant.Juju):
    """Non-leader units host the OSSes and expose 1 OST each (2 total)."""
    nonleaders = _nonleader_units(juju)
    assert nonleaders, "Expected at least one non-leader unit"

    for oss_unit in nonleaders:
        result = juju.exec("lctl dl", unit=oss_unit)
        assert result.return_code == 0, f"lctl dl failed on {oss_unit}: {result.stderr}"

        osd_lines = [line for line in result.stdout.splitlines() if "osd-zfs" in line]
        assert len(osd_lines) == 1, (
            f"Expected 1 OSD on {oss_unit}, got {len(osd_lines)}.\nlctl dl output:\n{result.stdout}"
        )


# ---------------------------------------------------------------------------
# Client mount
# ---------------------------------------------------------------------------


def test_client_mounted(lustre_cluster, juju: jubilant.Juju):
    """filesystem-client mounts Lustre at /mnt/lustre after integration."""
    result = juju.exec(f"mountpoint -q {MOUNT_POINT}", unit=CLIENT_UNIT)
    assert result.return_code == 0, (
        f"{MOUNT_POINT} is not a mountpoint on {CLIENT_UNIT}. "
        "Check that the relation handler mounts Lustre correctly."
    )


# ---------------------------------------------------------------------------
# Data path
# ---------------------------------------------------------------------------


def test_write_canary(canary_file: str, juju: jubilant.Juju):
    """Canary file exists on the Lustre mount and contains the expected content."""
    result = juju.exec(f"cat {canary_file}", unit=CLIENT_UNIT)
    assert result.return_code == 0, f"cat {canary_file} failed: {result.stderr}"
    assert result.stdout.strip() == CANARY_CONTENT, (
        f"Expected '{CANARY_CONTENT}', got '{result.stdout.strip()}'"
    )


def test_stripe_count(canary_file: str, juju: jubilant.Juju):
    """Stripe count of canary.txt reflects the -c -1 striping set on its parent directory."""
    result = juju.exec(f"lfs getstripe {canary_file}", unit=CLIENT_UNIT)
    assert result.return_code == 0, f"lfs getstripe failed: {result.stderr}"

    # lfs getstripe output contains a line like:  lmm_stripe_count:   1
    stripe_line = next(
        (line for line in result.stdout.splitlines() if "lmm_stripe_count" in line),
        None,
    )
    assert stripe_line is not None, (
        f"lmm_stripe_count not found in lfs getstripe output:\n{result.stdout}"
    )
    actual_count = int(stripe_line.split()[-1])
    assert actual_count == EXPECTED_STRIPE_COUNT, (
        f"Stripe count {actual_count} != expected {EXPECTED_STRIPE_COUNT}"
    )


def test_remount_persistence(canary_file: str, juju: jubilant.Juju):
    """Data written before unmount is still present after remounting Lustre.

    NOTE umount is a global operation: this test cannot run concurrently with other tests
    against the same mount.
    """
    unmount = juju.exec(f"umount {MOUNT_POINT}", unit=CLIENT_UNIT)
    assert unmount.return_code == 0, f"umount failed: {unmount.stderr}"

    not_mounted = juju.exec(f"mountpoint -q {MOUNT_POINT}; echo $?", unit=CLIENT_UNIT)
    rc = int(not_mounted.stdout.strip())
    assert rc == 32, f"Expected mountpoint rc=32 (not a mountpoint), got rc={rc}"

    # Remount using the MGS IP obtained from Juju status.
    leader = _leader_unit(juju)
    mgs_ip = juju.status().apps[LUSTRE_APP].units[leader].public_address
    remount = juju.exec(
        f"mount -t lustre {mgs_ip}@tcp:/{FS_NAME} {MOUNT_POINT}",
        unit=CLIENT_UNIT,
    )
    assert remount.return_code == 0, f"Remount failed: {remount.stderr}"

    read_back = juju.exec(f"cat {canary_file}", unit=CLIENT_UNIT)
    assert read_back.return_code == 0, f"cat {canary_file} failed: {read_back.stderr}"
    assert read_back.stdout.strip() == CANARY_CONTENT, (
        f"Expected '{CANARY_CONTENT}', got '{read_back.stdout.strip()}'"
    )
