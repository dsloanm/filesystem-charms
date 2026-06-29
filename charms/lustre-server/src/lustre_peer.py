#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Peer relation observer for the Lustre charm."""

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

import lustre_fs
import ops
import pydantic
from charms.filesystem_client.v0.filesystem_info import LustreInfo
from constants import LUSTRE_FSNAME
from errors import LustreFilesystemError, LustrePeerError
from state import check_lustre

if TYPE_CHECKING:
    from charm import LustreCharm

_logger = logging.getLogger(__name__)

PEER_RELATION = "lustre-peer"


class CharmStatuses(StrEnum):
    """Charm status messages."""

    FAILED_OSS_SETUP = "Failed to set up OSS"


class LustrePeerAppData(pydantic.BaseModel):
    """App-level data written by the leader to the peer relation databag.

    Attributes:
        mgs_nid: The LNet NID of the MGS unit. Example: "10.0.0.5@tcp".
        mgs_unit_name: The Juju name for the MGS unit. Example: "lustre/0".
    """

    mgs_nid: str | None = pydantic.Field(
        default=None, description="LNet NID of the MGS unit. Example: '10.0.0.5@tcp'."
    )
    mgs_unit_name: str | None = pydantic.Field(
        default=None, description="Juju name for the MGS unit. Example: 'lustre/0'."
    )


class LustrePeerObserver(ops.Object):
    """Manages the Lustre peer relation."""

    def __init__(self, charm: "LustreCharm"):
        super().__init__(charm, PEER_RELATION)
        self._charm = charm
        charm.framework.observe(
            charm.on[PEER_RELATION].relation_changed, self._on_relation_changed
        )

    def mgs_nid_published(self) -> str:
        """Publish this unit as the MGS if no MGS has been assigned yet.

        Returns:
            The published MGS NID string.
        """
        if not self.model.unit.is_leader():
            raise LustrePeerError("Non-leader attempted to publish MGS NID")

        # Never overwrite. The original MGS unit must remain stable across leader re-elections.
        data = self.get_app_data()
        if data.mgs_unit_name and data.mgs_nid:
            _logger.info(
                "MGS already active on %s with NID %s. skipping publication",
                data.mgs_unit_name,
                data.mgs_nid,
            )
            return data.mgs_nid

        try:
            # TODO: support multiple NIDs. MVP scoped to a single NID.
            mgs_nid = lustre_fs.get_nids()[0]
        except (LustreFilesystemError, IndexError) as e:
            raise LustrePeerError("Failed to determine MGS NID") from e

        data.mgs_nid = mgs_nid
        data.mgs_unit_name = self.model.unit.name

        self.set_app_data(data)
        self._charm.filesystem.set_info(LustreInfo(mgs_ids=[mgs_nid], fs_name=LUSTRE_FSNAME))
        _logger.info("Published MGS NID %s for unit %s", data.mgs_nid, data.mgs_unit_name)
        return data.mgs_nid

    def get_app_data(self) -> LustrePeerAppData:
        """Return the application data in the peer relation databag."""
        rel = self._get_relation_checked()
        return rel.load(LustrePeerAppData, rel.app) or LustrePeerAppData()

    def set_app_data(self, data: LustrePeerAppData) -> None:
        """Set the application data in the peer relation databag."""
        rel = self._get_relation_checked()
        rel.save(data, rel.app)

    def _on_relation_changed(self, _: ops.RelationChangedEvent) -> None:
        """Handle the peer relation changed event.

        Raises:
            LustrePeerError: If the peer relation does not exist.
        """
        try:
            data = self.get_app_data()
        except LustrePeerError as e:
            _logger.warning("Failed to get peer relation data: %s", e)
            return

        if data.mgs_unit_name is None or data.mgs_nid is None:
            _logger.warning("MGS data not yet published. cannot configure Lustre services.")
            return

        if self.model.unit.name == data.mgs_unit_name:
            # OSS service must not be enabled on MGS+MDS unit
            return

        try:
            lustre_fs.oss_setup(LUSTRE_FSNAME, self.model.unit.name, data.mgs_nid)
        except LustreFilesystemError as e:
            _logger.exception("failed to set up OSS: %s", e)
            self.model.unit.status = ops.BlockedStatus(CharmStatuses.FAILED_OSS_SETUP)
            return

        # FIXME: Cannot use @refresh decorator here due to `AttributeError: 'LustrePeer' object
        # has no attribute 'unit'`. Set status directly for now.
        self.model.unit.status = check_lustre(self._charm)

    def _get_relation_checked(self) -> ops.Relation:
        """Return the peer relation, ensuring it exists.

        Raises:
            LustrePeerError: If the peer relation does not exist.
        """
        rel = self.model.get_relation(PEER_RELATION)
        if rel is None:
            raise LustrePeerError("Peer relation not yet created")
        return rel
