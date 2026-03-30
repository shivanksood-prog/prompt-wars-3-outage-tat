"""Main pipeline: fetch → enrich → classify → consolidate → generate messages."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from engine.fetch import (
    fetch_incidents, fetch_incident_devices,
    fetch_concurrent_counts, fetch_tickets_for_incidents,
)
from engine.classifier import ClassifiedIncident, DeviceInfo, classify
from engine.consolidator import PartnerDigest, consolidate
from engine.templates import build_message_en, build_message_hi


def run_pipeline(lookback_hours: int = 6, base_url: str = "") -> dict:
    """Run the full pipeline and return partner digests with messages."""

    # 1. Fetch incidents (must complete first — other queries depend on IDs)
    raw_incidents = fetch_incidents(lookback_hours)
    if not raw_incidents:
        return {"partners": {}, "incidents": [], "stats": {"total_incidents": 0}}

    incident_ids = [r["ID"] for r in raw_incidents]
    partner_ids = [r["PARTNER_ID"] for r in raw_incidents]

    # 2. Fetch devices, concurrent counts, and tickets IN PARALLEL
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_devices = pool.submit(fetch_incident_devices, incident_ids)
        fut_concurrent = pool.submit(fetch_concurrent_counts, partner_ids, lookback_hours)
        fut_tickets = pool.submit(fetch_tickets_for_incidents, incident_ids)

        raw_devices = fut_devices.result()
        concurrent_map = fut_concurrent.result()
        ticket_map = fut_tickets.result()

    # 3. Build ClassifiedIncident objects
    # Group devices by incident
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

        # Attach devices
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

        # Attach concurrency
        conc = concurrent_map.get(iid, {})
        inc.concurrent_count = conc.get("CONCURRENT_COUNT", 0)

        # Attach tickets
        tix = ticket_map.get(iid, [])
        inc.tickets = tix
        inc.ticket_count = len(tix)
        inc.has_ticket = len(tix) > 0

        # Classify
        classify(inc)
        classified.append(inc)

    # 4. Consolidate per partner
    partner_digests = consolidate(classified)

    # 5. Generate messages
    for pid, digest in partner_digests.items():
        map_url = f"{base_url}/partner/{pid}/map" if base_url else f"/partner/{pid}/map"
        digest.message_en = build_message_en(digest, map_url)
        digest.message_hi = build_message_hi(digest, map_url)

    # Stats
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
