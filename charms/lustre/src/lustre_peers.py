#!/usr/bin/env python3
# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Peer relation observer for the Lustre charm."""

import logging

import lustre_fs
import ops
import pydantic
from constants import LUSTRE_FSNAME

_logger = logging.getLogger(__name__)

PEER_RELATION = "lustre-peers"


class LustrePeersError(Exception):
    """Exception raised when a Lustre peer relation operation fails."""


class LustrePeersAppData(pydantic.BaseModel):
    """App-level data written by the leader to the peer relation databag."""

    mgs_nid: str | None = pydantic.Field(
        default=None, description="LNet NID of the MGS unit, e.g. '10.0.0.5@tcp'."
    )
    mgs_unit_name: str | None = pydantic.Field(
        default=None, description="Juju name for the MGS unit, e.g. 'lustre/0'."
    )


class LustrePeers(ops.Object):
    """Manages the lustre-peers peer relation."""

    def __init__(self, charm: ops.CharmBase):
        super().__init__(charm, PEER_RELATION)
        self._charm = charm
        charm.framework.observe(charm.on.lustre_peers_relation_changed, self._on_relation_changed)

    def ensure_mgs_nid_published(self) -> None:
        """Publish this unit as the MGS if no MGS has been assigned yet."""
        if not self.model.unit.is_leader():
            raise LustrePeersError("Non-leader attempted to publish MGS NID")

        # Never overwrite. The original MGS unit must remain stable across leader re-elections.
        data = self.get_app_data()
        if data.mgs_unit_name and data.mgs_nid:
            _logger.info(
                "MGS already active on %s with NID %s. skipping publication",
                data.mgs_unit_name,
                data.mgs_nid,
            )
            return

        # NID is <address>@<LND protocol><lnd#>. Example: "10.0.0.5@tcp"
        mgs_nid = str(self.model.get_binding(PEER_RELATION).network.bind_address) + "@tcp"
        data.mgs_nid = mgs_nid
        data.mgs_unit_name = self.model.unit.name

        self.set_app_data(data)
        _logger.info("Published MGS NID %s for unit %s", data.mgs_nid, data.mgs_unit_name)

    def get_app_data(self) -> LustrePeersAppData:
        """Return the application data in the peer relation databag."""
        rel = self._get_relation_checked()
        return rel.load(LustrePeersAppData, rel.app) or LustrePeersAppData()

    def set_app_data(self, data: LustrePeersAppData) -> None:
        """Set the application data in the peer relation databag."""
        rel = self._get_relation_checked()
        rel.save(data, rel.app)

    def _on_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        data = self.get_app_data()

        if data.mgs_unit_name is None or data.mgs_nid is None:
            _logger.warning("MGS data not yet published. cannot configure Lustre services.")
            self.model.unit.status = ops.WaitingStatus("Waiting for MGS unit to publish NID")
            return

        if self.model.unit.name == data.mgs_unit_name:
            # OSS service must not be enabled on MGS+MDS unit
            return

        lustre_fs.ensure_oss_setup(LUSTRE_FSNAME, self.model.unit.name, data.mgs_nid)
        self.model.unit.status = ops.ActiveStatus("OSS ready")

    def _get_relation_checked(self) -> ops.Relation:
        """Return the peer relation, ensuring it exists."""
        rel = self.model.get_relation(PEER_RELATION)
        if rel is None:
            raise LustrePeersError("Peer relation not yet created")
        return rel
