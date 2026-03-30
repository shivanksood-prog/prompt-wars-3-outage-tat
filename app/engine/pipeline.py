"""Main pipeline: fetch → store → classify → consolidate → generate messages."""
from __future__ import annotations
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from engine.fetch import (
    fetch_incidents, fetch_incident_devices,
    fetch_concurrent_counts, fetch_tickets_for_incidents,
)
from engine.store import (
    save_incidents, save_devices, save_concurrent, save_tickets,
    set_meta, get_meta, has_recent_data,
    load_incidents, load_devices, load_concurrent, load_tickets,
)
from engine.classifier import ClassifiedIncident, DeviceInfo, classify
from engine.consolidator import PartnerDigest, consolidate
from engine.templates import build_message_en, build_message_hi


def _build_result(raw_incidents, raw_devices, concurrent_map, ticket_map, base_url):
    """Common logic: take raw data → classify → consolidate → return result dict."""
    devices_by_incident: dict[int, list[dict]] = {}
    for d in raw_devices:
        iid = d["INCIDENT_ID"]
        devices_by_incident.setdefault(iid, []).append(d)

    classified: list[ClassifiedIncident] = []
    for ri in raw_incidents:
        iid = ri["ID"]
        inc = ClassifiedIncident(
            incident_id=iid,
            partner_id=ri["PARTNER_ID"],
            severity=ri["SEVERITY"],
            size_bucket=ri["SIZE_BUCKET"],
            device_count=ri["DEVICE_COUNT"] or 0,
            status=ri["STATUS"],
            created_ist=str(ri["CREATED_IST"] or ""),
            first_fail_ist=str(ri["FIRST_FAIL_IST"] or ""),
            closed_ist=str(ri["CLOSED_IST"]) if ri["CLOSED_IST"] else None,
            duration_minutes=ri["DURATION_MINUTES"],
        )

        for d in devices_by_incident.get(iid, []):
            inc.devices.append(DeviceInfo(
                device_id=d["DEVICE_ID"],
                lat=d["LAT"],
                lng=d["LNG"],
                address=d["ADDRESS"],
                locality=d["LOCALITY"],
                customer_name=d["CUSTOMER_NAME"],
                customer_mobile=d["CUSTOMER_MOBILE"],
                status=d["DEVICE_STATUS"],
                down_time=str(d["DEVICE_DOWN_IST"] or ""),
                recovery_time=str(d["DEVICE_RECOVERY_IST"]) if d["DEVICE_RECOVERY_IST"] else None,
            ))

        conc = concurrent_map.get(iid, {})
        inc.concurrent_count = conc.get("CONCURRENT_COUNT", 0)

        tix = ticket_map.get(iid, [])
        inc.tickets = tix
        inc.ticket_count = len(tix)
        inc.has_ticket = len(tix) > 0

        classify(inc)
        classified.append(inc)

    partner_digests = consolidate(classified)

    for pid, digest in partner_digests.items():
        map_url = f"{base_url}/partner/{pid}/map" if base_url else f"/partner/{pid}/map"
        digest.message_en = build_message_en(digest, map_url)
        digest.message_hi = build_message_hi(digest, map_url)

    stats = {
        "total_incidents": len(classified),
        "total_partners": len(partner_digests),
        "total_devices": sum(d.total_devices for d in partner_digests.values()),
        "by_classification": {},
    }
    for inc in classified:
        cls = inc.classification
        stats["by_classification"][cls] = stats["by_classification"].get(cls, 0) + 1

    return {
        "partners": partner_digests,
        "incidents": classified,
        "stats": stats,
    }


def run_pipeline_from_db(lookback_hours: int = 6, base_url: str = "") -> dict | None:
    """Fast path: build result from SQLite data. Returns None if no data."""
    if not has_recent_data(max_age_minutes=60):
        return None

    raw_incidents = load_incidents(lookback_hours)
    if not raw_incidents:
        return None

    incident_ids = [r["ID"] for r in raw_incidents]
    raw_devices = load_devices(incident_ids)
    concurrent_map = load_concurrent(incident_ids)
    ticket_map = load_tickets(incident_ids)

    return _build_result(raw_incidents, raw_devices, concurrent_map, ticket_map, base_url)


def run_pipeline(lookback_hours: int = 6, base_url: str = "") -> dict:
    """Full pipeline: fetch from Metabase → save to SQLite → classify → return."""

    # 1. Fetch incidents
    raw_incidents = fetch_incidents(lookback_hours)
    if not raw_incidents:
        return {"partners": {}, "incidents": [], "stats": {"total_incidents": 0}}

    incident_ids = [r["ID"] for r in raw_incidents]
    partner_ids = [r["PARTNER_ID"] for r in raw_incidents]

    # Save incidents to SQLite
    save_incidents(raw_incidents)

    # 2. Fetch devices, concurrent, tickets IN PARALLEL
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_devices = pool.submit(fetch_incident_devices, incident_ids)
        fut_concurrent = pool.submit(fetch_concurrent_counts, partner_ids, lookback_hours)
        fut_tickets = pool.submit(fetch_tickets_for_incidents, incident_ids)

        raw_devices = fut_devices.result()
        concurrent_map = fut_concurrent.result()
        ticket_map = fut_tickets.result()

    # Save to SQLite
    save_devices(raw_devices)
    save_concurrent(concurrent_map)
    save_tickets(ticket_map)
    set_meta("last_refresh", datetime.utcnow().isoformat())

    return _build_result(raw_incidents, raw_devices, concurrent_map, ticket_map, base_url)
