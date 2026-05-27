#!/usr/bin/env python3
# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.

"""Peer relation observer for the Lustre charm."""

import logging
import subprocess
from pathlib import Path

import ops
import pydantic

import lustre_fs

logger = logging.getLogger(__name__)

PEER_RELATION = "lustre-peers"


class LustrePeersAppData(pydantic.BaseModel):
    """App-level data written by the leader to the peer relation databag."""

    mgs_nid: str | None = pydantic.Field(
        default=None, description="LNet NID of the MGS+MDS unit, e.g. '10.0.0.5@tcp'."
    )
    mgs_unit_name: str | None = pydantic.Field(
        default=None, description="Juju unit name of the original MGS+MDS, e.g. 'lustre/0'."
    )


class LustrePeers(ops.Object):
    """Manages the lustre-peers peer relation."""

    def __init__(self, charm: ops.CharmBase):
        super().__init__(charm, PEER_RELATION)
        self._charm = charm
        charm.framework.observe(charm.on.lustre_peers_relation_changed, self._on_relation_changed)

    @property
    def mgs_nid(self) -> str | None:
        """Return the MGS NID published by the leader or `None`, if not available."""
        return self._app_data().mgs_nid

    @property
    def mgs_unit_name(self) -> str | None:
        """Return the MGS unit name published by the leader or `None`, if not available."""
        return self._app_data().mgs_unit_name

    def publish_mgs_nid(self) -> None:
        """Publish this unit as the MGS if no MGS has been assigned yet."""
        rel = self.model.get_relation(PEER_RELATION)
        if rel is None:
            # Peer relation not yet created; nothing to do.
            return

        # First application leader writes its unit name and MGS NID to app databag.
        # Non-leader units read the NID from this data to configure themselves as OSS nodes.
        # Never overwrite. The original MGS+MDS unit must remain stable across leader re-elections.
        existing = rel.load(LustrePeersAppData, rel.app)
        if existing.mgs_unit_name:
            logger.info(
                "MGS already assigned to %s; skipping NID publication",
                existing.mgs_unit_name,
            )
            return

        mgs_nid = str(self.model.get_binding(PEER_RELATION).network.bind_address) + "@tcp"
        rel.save(LustrePeersAppData(mgs_nid=mgs_nid, mgs_unit_name=self.model.unit.name), rel.app)
        logger.info("Published MGS NID %s for unit %s", mgs_nid, self.model.unit.name)

    def _on_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        data = event.relation.load(LustrePeersAppData, event.relation.app)

        if data.mgs_unit_name is None or self.model.unit.name == data.mgs_unit_name:
            # Ensure OSS service is not enabled on the MGS+MDS unit
            return

        if data.mgs_nid is None:
            self.model.unit.status = ops.WaitingStatus("Waiting for MGS to share NID")
            return

        logger.info(
            "MGS is unit %s, NID %s. Setting up this unit as OSS", data.mgs_unit_name, data.mgs_nid
        )
        # TODO: Temporarily using fixed image files for testing
        pool = "ostpool"
        for ost_id in range(2):
            ost = Path(f"/root/ost{ost_id}.img")
            subprocess.run(["truncate", "-s", "10G", str(ost)], check=True)

            dataset = f"ost{ost_id}"
            lustre_fs.create_target(
                pool,
                dataset,
                ost,
                "10240",
                "10G",
                ost_id,
                mkfs_flags=["--ost", f"--mgsnode={data.mgs_nid}"],
            )
            lustre_fs.mount(pool, dataset, Path(f"/mnt/{dataset}"))
            logger.info("Created OST: %s for MGS NID: %s", ost, data.mgs_nid)

        self._charm.unit.status = ops.ActiveStatus("OSS ready")

    def _app_data(self) -> LustrePeersAppData:
        """Return the data published by the leader to the peer relation databag."""
        rel = self.model.get_relation(PEER_RELATION)
        if rel is None:
            raise RuntimeError("Peer relation not yet created")
        return rel.load(LustrePeersAppData, rel.app) or LustrePeersAppData()
