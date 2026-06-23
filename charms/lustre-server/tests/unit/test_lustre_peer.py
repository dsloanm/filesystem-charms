# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre peer relation observer unit tests."""

import lustre_peer
import ops
import pytest
from constants import LUSTRE_FSNAME
from errors import LustreFilesystemError

MGS_UNIT = "lustre/0"
MGS_NID = "10.0.0.5@tcp"
OSS_UNIT = "lustre/1"
BIND_IP = "10.0.0.5"


@pytest.fixture(scope="function")
def mock_model(mocker):
    """Mock LustrePeerObserver.model."""
    model = mocker.MagicMock()
    mocker.patch.object(
        lustre_peer.LustrePeerObserver,
        "model",
        new_callable=mocker.PropertyMock,
        return_value=model,
    )
    return model


@pytest.fixture(scope="function")
def mock_model_with_relation(mock_model, mocker):
    """_model with a mocked get_relation."""
    rel = mock_model.get_relation.return_value = mocker.MagicMock()
    return mock_model, rel


class TestMgsNidPublished:
    """mgs_nid_published() tests."""

    def test_leader_publishes_nid(self, mocker, mock_model_with_relation):
        """Leader unit publishes MGS NID to relation data."""
        model, rel = mock_model_with_relation
        model.unit.is_leader.return_value = True
        model.unit.name = MGS_UNIT
        model.get_binding.return_value.network.bind_address = BIND_IP
        rel.load.return_value = None

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.mgs_nid_published()

        assert result == f"{BIND_IP}@tcp"
        rel.save.assert_called_once()
        saved = rel.save.call_args[0][0]
        assert saved.mgs_nid == f"{BIND_IP}@tcp"
        assert saved.mgs_unit_name == MGS_UNIT

    def test_non_leader_raises(self, mocker, mock_model):
        """Non-leader unit raises an error when publishing MGS NID."""
        mock_model.unit.is_leader.return_value = False

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        with pytest.raises(lustre_peer.LustrePeerError, match="Non-leader"):
            observer.mgs_nid_published()

    def test_nid_already_published(self, mocker, mock_model_with_relation):
        """Leader unit does not overwrite existing MGS NID in relation data."""
        model, rel = mock_model_with_relation
        model.unit.is_leader.return_value = True
        existing = lustre_peer.LustrePeerAppData(mgs_nid=MGS_NID, mgs_unit_name=MGS_UNIT)
        rel.load.return_value = existing

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.mgs_nid_published()

        assert result == MGS_NID
        rel.save.assert_not_called()


class TestOnRelationChanged:
    """_on_relation_changed() tests."""

    def test_oss_unit_setup(self, mocker, mock_model_with_relation):
        """OSS unit sets up correctly when relation data is available."""
        model, rel = mock_model_with_relation
        model.unit.name = OSS_UNIT
        rel.load.return_value = lustre_peer.LustrePeerAppData(
            mgs_nid=MGS_NID,
            mgs_unit_name=MGS_UNIT,
        )

        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)
        expected_status = ops.ActiveStatus()
        mocker.patch("lustre_peer.check_lustre", return_value=expected_status)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss.assert_called_once_with(LUSTRE_FSNAME, OSS_UNIT, MGS_NID)
        assert model.unit.status == expected_status

    def test_app_data_error(self, mocker, mock_model):
        """OSS unit does not set up when relation data is unavailable."""
        mock_model.get_relation.return_value = None
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss.assert_not_called()

    def test_mgs_data_not_published(self, mocker, mock_model_with_relation):
        """OSS unit does not set up when MGS NID is not published."""
        _, rel = mock_model_with_relation
        rel.load.return_value = None
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss.assert_not_called()

    def test_mgs_unit_skips_oss(self, mocker, mock_model_with_relation):
        """MGS unit does not attempt to set up OSS."""
        model, rel = mock_model_with_relation
        model.unit.name = MGS_UNIT
        rel.load.return_value = lustre_peer.LustrePeerAppData(
            mgs_nid=MGS_NID,
            mgs_unit_name=MGS_UNIT,
        )
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss.assert_not_called()

    def test_oss_setup_failure(self, mocker, mock_model_with_relation):
        """OSS unit service setup fails."""
        model, rel = mock_model_with_relation
        model.unit.name = OSS_UNIT
        rel.load.return_value = lustre_peer.LustrePeerAppData(
            mgs_nid=MGS_NID,
            mgs_unit_name=MGS_UNIT,
        )
        mocker.patch(
            "lustre_peer.lustre_fs.oss_setup", side_effect=LustreFilesystemError("setup failed")
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        assert model.unit.status == ops.BlockedStatus(lustre_peer.CharmStatuses.FAILED_OSS_SETUP)
