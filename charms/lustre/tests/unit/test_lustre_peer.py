import ops
import pytest
from pytest_mock import mocker

import lustre_peer
from constants import LUSTRE_FSNAME

class TestLustrePeerAppData:
    """LustrePeerAppData model tests."""

    def test_defaults(self):
        data = lustre_peer.LustrePeerAppData()
        assert data.mgs_nid is None
        assert data.mgs_unit_name is None

    def test_populated(self):
        data = lustre_peer.LustrePeerAppData(mgs_nid="10.0.0.5@tcp", mgs_unit_name="lustre/0")
        assert data.mgs_nid == "10.0.0.5@tcp"
        assert data.mgs_unit_name == "lustre/0"


class TestInit:
    """__init__() tests."""

    def test_observes_relation_changed(self, mocker):
        mock_charm = mocker.MagicMock()
        mock_framework = mock_charm.framework

        lustre_peer.LustrePeerObserver(mock_charm)

        mock_framework.observe.assert_called_once_with(
            mock_charm.on["lustre-peer"].relation_changed,
            mocker.ANY,
        )


class TestGetRelationChecked:
    """_get_relation_checked() tests."""

    def test_relation_exists(self, mocker):
        mock_relation = mocker.MagicMock()
        mock_model = mocker.MagicMock()
        mock_model.get_relation.return_value = mock_relation
        mocker.patch(
            "lustre_peer.LustrePeerObserver.model",
            new_callable=mocker.PropertyMock,
            return_value=mock_model
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer._get_relation_checked()

        assert result is mock_relation
        mock_model.get_relation.assert_called_once_with(lustre_peer.PEER_RELATION)

    def test_relation_missing(self, mocker):
        mock_model = mocker.MagicMock()
        mock_model.get_relation.return_value = None
        mocker.patch(
            "lustre_peer.LustrePeerObserver.model",
            new_callable=mocker.PropertyMock,
            return_value=mock_model
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        with pytest.raises(lustre_peer.LustrePeerError, match="Peer relation not yet created"):
            observer._get_relation_checked()


class TestGetAppData:
    """get_app_data() tests."""

    def test_returns_loaded_data(self, mocker):
        mock_relation = mocker.MagicMock()
        mock_model = mocker.MagicMock()
        mock_model.get_relation.return_value = mock_relation
        mocker.patch(
            "lustre_peer.LustrePeerObserver.model",
            new_callable=mocker.PropertyMock,
            return_value=mock_model
        )

        expected = lustre_peer.LustrePeerAppData(mgs_nid="10.0.0.5@tcp", mgs_unit_name="lustre/0")
        mock_relation.load.return_value = expected

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.get_app_data()

        assert result is expected

    def test_returns_default_when_empty(self, mocker):
        mock_relation = mocker.MagicMock()
        mock_model = mocker.MagicMock()
        mock_model.get_relation.return_value = mock_relation
        mocker.patch(
            "lustre_peer.LustrePeerObserver.model",
            new_callable=mocker.PropertyMock,
            return_value=mock_model
        )

        mock_relation.load.return_value = None

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.get_app_data()

        assert result == lustre_peer.LustrePeerAppData()


class TestSetAppData:
    """set_app_data() tests."""

    def test_saves_data(self, mocker):
        mock_relation = mocker.MagicMock()
        mock_model = mocker.MagicMock()
        mock_model.get_relation.return_value = mock_relation
        mocker.patch(
            "lustre_peer.LustrePeerObserver.model",
            new_callable=mocker.PropertyMock,
            return_value=mock_model
        )

        data = lustre_peer.LustrePeerAppData(mgs_nid="10.0.0.5@tcp", mgs_unit_name="lustre/0")
        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer.set_app_data(data)

        mock_relation.save.assert_called_once_with(data, mock_relation.app)


class TestMgsNidPublished:
    """mgs_nid_published() tests."""

    def test_leader_publishes_nid(self, mocker):
        expected_unit_name = "lustre/0"
        expected_ip = "10.0.0.5"
        expected_nid = f"{expected_ip}@tcp"

        mock_model = mocker.MagicMock()
        mock_model.unit.is_leader.return_value = True
        mock_model.unit.name = expected_unit_name
        mock_model.get_binding.return_value.network.bind_address = expected_ip
        mocker.patch(
            "lustre_peer.LustrePeerObserver.model",
            new_callable=mocker.PropertyMock,
            return_value=mock_model
        )

        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=lustre_peer.LustrePeerAppData())
        mock_set_app_data = mocker.patch("lustre_peer.LustrePeerObserver.set_app_data")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.mgs_nid_published()

        assert result == expected_nid
        mock_set_app_data.assert_called_once()
        saved_data = mock_set_app_data.call_args[0][0]
        assert saved_data.mgs_nid == expected_nid
        assert saved_data.mgs_unit_name == expected_unit_name

    def test_non_leader_raises(self, mocker):
        mock_model = mocker.MagicMock()
        mock_model.unit.is_leader.return_value = False
        mocker.patch(
            "lustre_peer.LustrePeerObserver.model",
            new_callable=mocker.PropertyMock,
            return_value=mock_model
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        with pytest.raises(lustre_peer.LustrePeerError, match="Non-leader"):
            observer.mgs_nid_published()

    def test_already_published(self, mocker):
        existing = lustre_peer.LustrePeerAppData(mgs_nid="10.0.0.1@tcp", mgs_unit_name="lustre/0")
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=existing)
        mock_set_app_data = mocker.patch("lustre_peer.LustrePeerObserver.set_app_data")

        mock_model = mocker.MagicMock()
        mock_model.unit.is_leader.return_value = True
        mocker.patch(
            "lustre_peer.LustrePeerObserver.model",
            new_callable=mocker.PropertyMock,
            return_value=mock_model
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.mgs_nid_published()

        assert result == existing.mgs_nid
        mock_set_app_data.assert_not_called()


class TestOnRelationChanged:
    """_on_relation_changed() tests."""

    def test_oss_unit_successful_setup(self, mocker):
        mgs_unit_name = "lustre/0"
        mgs_nid = "10.0.0.1@tcp"
        oss_unit_name = "lustre/1"
        existing = lustre_peer.LustrePeerAppData(mgs_nid=mgs_nid, mgs_unit_name=mgs_unit_name)
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=existing)

        mock_model = mocker.MagicMock()
        mock_model.unit.name = oss_unit_name
        mocker.patch(
            "lustre_peer.LustrePeerObserver.model",
            new_callable=mocker.PropertyMock,
            return_value=mock_model
        )
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup")

        expected_status = ops.ActiveStatus()
        mocker.patch("lustre_peer.check_lustre", return_value=expected_status)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss.assert_called_once_with(LUSTRE_FSNAME, oss_unit_name, mgs_nid)
        assert mock_model.unit.status == expected_status

    def test_app_data_error(self, mocker):
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", side_effect=lustre_peer.LustrePeerError())
        mock_oss_setup = mocker.patch("lustre_peer.lustre_fs.oss_setup")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss_setup.assert_not_called()

    def test_mgs_data_not_yet_published(self, mocker):
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=lustre_peer.LustrePeerAppData())
        mock_oss_setup = mocker.patch("lustre_peer.lustre_fs.oss_setup")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss_setup.assert_not_called()

    def test_mgs_unit_skips_oss_setup(self, mocker):
        mgs_unit_name = "lustre/0"
        existing = lustre_peer.LustrePeerAppData(mgs_nid="10.0.0.1@tcp", mgs_unit_name=mgs_unit_name)
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=existing)

        mock_model = mocker.MagicMock()
        mock_model.unit.name = mgs_unit_name
        mocker.patch(
            "lustre_peer.LustrePeerObserver.model",
            new_callable=mocker.PropertyMock,
            return_value=mock_model
        )
        mock_oss_setup = mocker.patch("lustre_peer.lustre_fs.oss_setup")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss_setup.assert_not_called()
