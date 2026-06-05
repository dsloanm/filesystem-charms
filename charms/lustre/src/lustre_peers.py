#!/usr/bin/env python3
# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Peer relation observer for the Lustre charm."""

import logging

import lustre_fs
import ops
import pydantic

_logger = logging.getLogger(__name__)

PEER_RELATION = "lustre-peers"


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

    @property
    def app_data(self) -> LustrePeersAppData:
        """Return the data published by the leader to the peer relation databag."""
        rel = self.model.get_relation(PEER_RELATION)
        if rel is None:
            raise RuntimeError("Peer relation not yet created")
        return rel.load(LustrePeersAppData, rel.app) or LustrePeersAppData()

    @property
    def mgs_nid(self) -> str | None:
        """Return the MGS NID published by the leader or `None`, if not available."""
        return self.app_data.mgs_nid

    @property
    def mgs_unit_name(self) -> str | None:
        """Return the MGS unit name published by the leader or `None`, if not available."""
        return self.app_data.mgs_unit_name

    def ensure_mgs_nid_published(self) -> None:
        """Publish this unit as the MGS if no MGS has been assigned yet."""
        rel = self.model.get_relation(PEER_RELATION)
        if rel is None:
            _logger.warning("peer relation not established. cannot publish MGS NID")
            return

        # First application leader writes its unit name and MGS NID to app databag.
        # Non-leader units read the NID from this data to configure themselves as OSS nodes.
        # Never overwrite. The original MGS unit must remain stable across leader re-elections.
        existing = rel.load(LustrePeersAppData, rel.app)
        if existing.mgs_unit_name:
            _logger.info(
                "MGS already active on %s. skipping NID publication",
                existing.mgs_unit_name,
            )
            return

        # NID is <address>@<LND protocol><lnd#>. Example: "10.0.0.5@tcp"
        mgs_nid = str(self.model.get_binding(PEER_RELATION).network.bind_address) + "@tcp"
        rel.save(LustrePeersAppData(mgs_nid=mgs_nid, mgs_unit_name=self.model.unit.name), rel.app)

        _logger.info("Published MGS NID %s for unit %s", mgs_nid, self.model.unit.name)

    def _on_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        data = event.relation.load(LustrePeersAppData, event.relation.app)

        if data.mgs_unit_name is None or data.mgs_nid is None:
            _logger.warning("MGS data not yet published. cannot configure Lustre services.")
            self.model.unit.status = ops.WaitingStatus("Waiting for MGS unit to publish NID")
            return

        if self.model.unit.name == data.mgs_unit_name:
            # OSS service must not be enabled on MGS+MDS unit
            return

        lustre_fs.ensure_oss_setup(self.model.unit.name, data.mgs_nid)
        self.model.unit.status = ops.ActiveStatus("OSS ready")
