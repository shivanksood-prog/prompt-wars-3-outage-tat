"""Classify incidents based on physical network topology.

Topology (bottom-up):
  Customer device (1 connection)
    → Secondary Splitter: up to 8 devices, ~100m radius
      → Primary Splitter: up to 8 secondary = 64 devices, 2-3km radius
        → OLT: up to 4 primary = 256 devices per OLT
          → ISP backbone

WIOM visibility: ~60% of total connections (partner has ~40% own network).
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field

from config import (
    GEO_SECONDARY_SPLITTER_DEG, GEO_PRIMARY_SPLITTER_DEG,
    CUSTOMER_MAX_DEVICES, SECONDARY_SPLITTER_MAX_DEVICES,
    PRIMARY_SPLITTER_MAX_DEVICES, CONCURRENT_ESCALATE_TO_OLT,
)


@dataclass
class DeviceInfo:
    device_id: str
    lat: float | None
    lng: float | None
    address: str | None
    locality: str | None
    customer_name: str | None
    customer_mobile: str | None
    status: str | None
    down_time: str | None
    recovery_time: str | None


@dataclass
class ClassifiedIncident:
    incident_id: int
    partner_id: int
    severity: str
    size_bucket: str
    device_count: int
    status: str
    created_ist: str
    first_fail_ist: str | None
    closed_ist: str | None
    duration_minutes: int | None

    # Computed
    devices: list[DeviceInfo] = field(default_factory=list)
    geo_devices_with_coords: int = 0
    center_lat: float | None = None
    center_lng: float | None = None
    lat_range: float = 0.0
    lng_range: float = 0.0
    geo_spread_label: str = ""

    concurrent_count: int = 0
    has_ticket: bool = False
    ticket_count: int = 0
    tickets: list[dict] = field(default_factory=list)

    classification: str = ""
    confidence: str = ""      # HIGH, MEDIUM, LOW
    action_en: str = ""
    action_hi: str = ""
    nav_lat: float | None = None
    nav_lng: float | None = None


def compute_geo(incident: ClassifiedIncident) -> None:
    """Compute geo-spread metrics from device coordinates."""
    coords = [(d.lat, d.lng) for d in incident.devices if d.lat and d.lng]
    incident.geo_devices_with_coords = len(coords)

    if not coords:
        incident.geo_spread_label = "NO_GEO"
        return

    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]

    incident.center_lat = sum(lats) / len(lats)
    incident.center_lng = sum(lngs) / len(lngs)
    incident.nav_lat = incident.center_lat
    incident.nav_lng = incident.center_lng

    if len(coords) == 1:
        incident.lat_range = 0
        incident.lng_range = 0
        incident.geo_spread_label = "SINGLE_POINT"
        return

    incident.lat_range = max(lats) - min(lats)
    incident.lng_range = max(lngs) - min(lngs)

    spread = max(incident.lat_range, incident.lng_range)
    if spread < GEO_SECONDARY_SPLITTER_DEG:
        incident.geo_spread_label = "WITHIN_100M"       # Secondary splitter territory
    elif spread < 0.005:
        incident.geo_spread_label = "WITHIN_500M"
    elif spread < 0.01:
        incident.geo_spread_label = "WITHIN_1KM"
    elif spread < GEO_PRIMARY_SPLITTER_DEG:
        incident.geo_spread_label = "WITHIN_3KM"        # Primary splitter territory
    else:
        incident.geo_spread_label = "BEYOND_3KM"        # OLT/backbone territory


def classify(incident: ClassifiedIncident) -> None:
    """Classify based on physical network topology:
    device_count + geo_spread → map to splitter/OLT layer.
    """
    compute_geo(incident)

    dc = incident.device_count or len(incident.devices)
    geo = incident.geo_spread_label
    conc = incident.concurrent_count
    has_ticket = incident.has_ticket

    # ── Rule 1: Customer-level (1-3 devices) ──
    # Few devices, likely individual customer power or device issue
    if dc <= CUSTOMER_MAX_DEVICES:
        if has_ticket:
            incident.classification = "CUSTOMER_ISSUE"
            incident.confidence = "HIGH"
        else:
            incident.classification = "CUSTOMER_POWER"
            incident.confidence = "MEDIUM"
        return

    # ── Rule 2: Check for OLT/backbone first (large-scale) ──
    # >64 visible devices = definitely beyond primary splitter capacity
    if dc > PRIMARY_SPLITTER_MAX_DEVICES:
        incident.classification = "OLT_BACKBONE"
        incident.confidence = "HIGH"
        return

    # Many concurrent incidents on same partner + decent device count = partner-wide
    if conc >= CONCURRENT_ESCALATE_TO_OLT and dc > SECONDARY_SPLITTER_MAX_DEVICES:
        incident.classification = "OLT_BACKBONE"
        incident.confidence = "MEDIUM"
        return

    # Wide geo spread (>3km) with many devices = beyond primary splitter range
    if geo == "BEYOND_3KM" and dc > SECONDARY_SPLITTER_MAX_DEVICES:
        incident.classification = "OLT_BACKBONE"
        incident.confidence = "MEDIUM"
        return

    # ── Rule 3: Secondary splitter (≤8 devices, within 100m) ──
    # Matches physical secondary splitter: up to 8 ports, ~100m radius
    if dc <= SECONDARY_SPLITTER_MAX_DEVICES and geo in ("SINGLE_POINT", "WITHIN_100M", "NO_GEO"):
        incident.classification = "SECONDARY_SPLITTER"
        incident.confidence = "HIGH" if geo == "WITHIN_100M" else "MEDIUM"
        return

    # ── Rule 4: Primary splitter ──
    # >8 devices within 3km = primary splitter territory (8 secondary × 8 devices = 64)
    if dc > SECONDARY_SPLITTER_MAX_DEVICES and geo in ("SINGLE_POINT", "WITHIN_100M", "WITHIN_500M", "WITHIN_1KM", "WITHIN_3KM"):
        incident.classification = "PRIMARY_SPLITTER"
        incident.confidence = "HIGH" if dc > 16 else "MEDIUM"
        return

    # ≤8 devices but spread wider than 100m = crossing secondary splitter boundary
    # Multiple secondary splitters affected → primary splitter issue
    if dc <= SECONDARY_SPLITTER_MAX_DEVICES and geo in ("WITHIN_500M", "WITHIN_1KM", "WITHIN_3KM"):
        incident.classification = "PRIMARY_SPLITTER"
        incident.confidence = "MEDIUM"
        return

    # ── Rule 5: Wide spread but few devices ──
    # ≤8 devices spread >3km = unusual, could be scattered or OLT-level
    if geo == "BEYOND_3KM":
        if conc >= 3:
            incident.classification = "OLT_BACKBONE"
            incident.confidence = "LOW"
        else:
            incident.classification = "PRIMARY_SPLITTER"
            incident.confidence = "LOW"
        return

    # ── Default ──
    incident.classification = "SECONDARY_SPLITTER"
    incident.confidence = "LOW"


# ── Geo spread to approximate meters ──
def geo_spread_meters(lat_range: float, lng_range: float, center_lat: float = 28.6) -> int:
    """Rough conversion from degree spread to meters."""
    lat_m = lat_range * 111_320
    lng_m = lng_range * 111_320 * math.cos(math.radians(center_lat))
    return int(max(lat_m, lng_m))
