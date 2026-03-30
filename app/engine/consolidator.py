"""Consolidate multiple incidents per partner into one message."""
from __future__ import annotations
from dataclasses import dataclass, field
from engine.classifier import ClassifiedIncident


@dataclass
class PartnerDigest:
    partner_id: int
    total_incidents: int = 0
    total_devices: int = 0

    # Grouped by classification (new topology-based)
    customer_power: list[ClassifiedIncident] = field(default_factory=list)
    customer_issue: list[ClassifiedIncident] = field(default_factory=list)
    secondary_splitter: list[ClassifiedIncident] = field(default_factory=list)
    primary_splitter: list[ClassifiedIncident] = field(default_factory=list)
    olt_backbone: list[ClassifiedIncident] = field(default_factory=list)

    # Aggregate signals
    total_tickets: int = 0
    has_any_ticket: bool = False

    # For map
    center_lat: float | None = None
    center_lng: float | None = None

    # Generated
    message_en: str = ""
    message_hi: str = ""


CLASSIFICATION_GROUPS = {
    "CUSTOMER_POWER": "customer_power",
    "CUSTOMER_ISSUE": "customer_issue",
    "SECONDARY_SPLITTER": "secondary_splitter",
    "PRIMARY_SPLITTER": "primary_splitter",
    "OLT_BACKBONE": "olt_backbone",
}


def consolidate(incidents: list[ClassifiedIncident]) -> dict[int, PartnerDigest]:
    """Group classified incidents by partner and build digest."""
    partner_map: dict[int, PartnerDigest] = {}

    for inc in incidents:
        pid = inc.partner_id
        if pid not in partner_map:
            partner_map[pid] = PartnerDigest(partner_id=pid)

        digest = partner_map[pid]
        digest.total_incidents += 1
        digest.total_devices += inc.device_count or len(inc.devices)

        group_attr = CLASSIFICATION_GROUPS.get(inc.classification, "secondary_splitter")
        getattr(digest, group_attr).append(inc)

        if inc.has_ticket:
            digest.has_any_ticket = True
            digest.total_tickets += inc.ticket_count

    # Compute center for each partner
    for digest in partner_map.values():
        all_incidents = (
            digest.customer_power + digest.customer_issue +
            digest.secondary_splitter + digest.primary_splitter +
            digest.olt_backbone
        )
        lats = [i.center_lat for i in all_incidents if i.center_lat]
        lngs = [i.center_lng for i in all_incidents if i.center_lng]
        if lats:
            digest.center_lat = sum(lats) / len(lats)
            digest.center_lng = sum(lngs) / len(lngs)

    return partner_map
