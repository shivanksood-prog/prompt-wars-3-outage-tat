"""Fetch incident data from Metabase/Snowflake."""
import requests
import json
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import METABASE_URL, METABASE_API_KEY, METABASE_DB_ID

BATCH_SIZE = 200  # Larger batches, fewer round-trips
MAX_WORKERS = 6   # Parallel Metabase queries


def run_query(sql: str) -> dict[str, Any]:
    """Execute a native SQL query against Metabase and return parsed result."""
    resp = requests.post(
        f"{METABASE_URL}/api/dataset",
        headers={
            "x-api-key": METABASE_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "database": METABASE_DB_ID,
            "type": "native",
            "native": {"query": sql},
        },
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    cols = [c["name"] for c in data["data"]["cols"]]
    rows = data["data"]["rows"]
    return {"cols": cols, "rows": [dict(zip(cols, r)) for r in rows]}


def _batched_ids(ids: list[int], batch_size: int = BATCH_SIZE):
    """Yield batches of IDs."""
    for i in range(0, len(ids), batch_size):
        yield ids[i:i + batch_size]


def _run_batched_query(sql: str) -> list[dict]:
    """Run a single query and return rows. Used as thread target."""
    return run_query(sql)["rows"]


def fetch_incidents(lookback_hours: int = 6) -> list[dict]:
    """Fetch recent incidents with severity LOCAL/MAJOR."""
    sql = f"""
    WITH latest AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY ID ORDER BY UPDATED_AT DESC) AS rn
        FROM PROD_DB.BUSINESS_EFFICIENCY_ROUTER_OUTAGE_DETECTION_PUBLIC.INCIDENTS
        WHERE partner_id NOT IN (SELECT lco_account_id FROM TEST_LCO_ACCOUNT_ID)
        AND CREATED_AT >= DATEADD(HOUR, -{lookback_hours}, CURRENT_TIMESTAMP())
    )
    SELECT ID, PARTNER_ID, SEVERITY, SIZE_BUCKET, DURATION_BUCKET,
        DEVICE_COUNT, STATUS, DURATION_MINUTES,
        DATEADD(MINUTE, 330, FIRST_FAIL_TIMESTAMP) as FIRST_FAIL_IST,
        DATEADD(MINUTE, 330, CREATED_AT) as CREATED_IST,
        DATEADD(MINUTE, 330, CLOSED_AT) as CLOSED_IST,
        RECOVERY_PERCENTAGE
    FROM latest
    WHERE rn = 1 AND SEVERITY IN ('LOCAL', 'MAJOR')
    ORDER BY CREATED_AT DESC
    """
    return run_query(sql)["rows"]


def fetch_incident_devices(incident_ids: list[int]) -> list[dict]:
    """Fetch devices for given incident IDs with lat/lng. Batched + parallel."""
    if not incident_ids:
        return []

    def _query_batch(batch):
        ids_str = ",".join(str(i) for i in batch)
        sql = f"""
        WITH latest_customer AS (
            SELECT device_id, mobile, name, lco_account_id, google_address_id,
                ROW_NUMBER() OVER (PARTITION BY device_id ORDER BY plan_expiry_time DESC) as rn
            FROM PROD_DB.PUBLIC.T_WG_CUSTOMER
            WHERE device_id IS NOT NULL
        )
        SELECT
            d.INCIDENT_ID,
            d.DEVICE_ID,
            d.STATUS as DEVICE_STATUS,
            DATEADD(MINUTE, 330, d.CREATED_AT) as DEVICE_DOWN_IST,
            DATEADD(MINUTE, 330, d.RECOVERY_AT) as DEVICE_RECOVERY_IST,
            a.LAT,
            a.LNG,
            a.ADDRESS,
            a.LOCALITY,
            c.LCO_ACCOUNT_ID as PARTNER_ID,
            c.MOBILE as CUSTOMER_MOBILE,
            c.NAME as CUSTOMER_NAME
        FROM PROD_DB.BUSINESS_EFFICIENCY_ROUTER_OUTAGE_DETECTION_PUBLIC.INCIDENT_IMPACTED_DEVICE d
        JOIN latest_customer c ON d.DEVICE_ID = c.DEVICE_ID AND c.rn = 1
        LEFT JOIN PROD_DB.PUBLIC.T_ADDRESS a ON c.GOOGLE_ADDRESS_ID = a.ID
        WHERE d._FIVETRAN_DELETED = FALSE
        AND d.INCIDENT_ID IN ({ids_str})
        """
        return run_query(sql)["rows"]

    all_rows = []
    batches = list(_batched_ids(incident_ids))
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_query_batch, b): b for b in batches}
        for f in as_completed(futures):
            all_rows.extend(f.result())
    return all_rows


def fetch_concurrent_counts(partner_ids: list[int], lookback_hours: int = 6) -> dict[int, dict]:
    """For each incident, count concurrent incidents on same partner. Batched + parallel."""
    if not partner_ids:
        return {}
    unique_pids = list(set(partner_ids))

    def _query_batch(batch):
        pids = ",".join(str(p) for p in batch)
        sql = f"""
        WITH recent AS (
            SELECT ID, PARTNER_ID, SEVERITY,
                DATEADD(MINUTE, 330, CREATED_AT) as start_ist,
                DATEADD(MINUTE, 330, COALESCE(CLOSED_AT, UPDATED_AT)) as end_ist
            FROM PROD_DB.BUSINESS_EFFICIENCY_ROUTER_OUTAGE_DETECTION_PUBLIC.INCIDENTS
            WHERE CREATED_AT >= DATEADD(HOUR, -{lookback_hours}, CURRENT_TIMESTAMP())
            AND PARTNER_ID IN ({pids})
            AND partner_id NOT IN (SELECT lco_account_id FROM TEST_LCO_ACCOUNT_ID)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ID ORDER BY UPDATED_AT DESC) = 1
        )
        SELECT a.ID as INCIDENT_ID, a.PARTNER_ID,
            COUNT(DISTINCT b.ID) as CONCURRENT_COUNT
        FROM recent a
        JOIN recent b ON a.PARTNER_ID = b.PARTNER_ID AND a.ID != b.ID
            AND b.start_ist <= a.end_ist AND b.end_ist >= a.start_ist
        WHERE a.SEVERITY IN ('LOCAL','MAJOR')
        GROUP BY 1, 2
        """
        return run_query(sql)["rows"]

    all_results: dict[int, dict] = {}
    batches = list(_batched_ids(unique_pids, batch_size=100))
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_query_batch, b): b for b in batches}
        for f in as_completed(futures):
            for r in f.result():
                all_results[r["INCIDENT_ID"]] = r
    return all_results


def fetch_tickets_for_incidents(incident_ids: list[int]) -> dict[int, list[dict]]:
    """Fetch service tickets matching incident devices during outage window. Batched + parallel."""
    if not incident_ids:
        return {}

    def _query_batch(batch):
        ids_str = ",".join(str(i) for i in batch)
        sql = f"""
        WITH latest_customer AS (
            SELECT device_id, mobile, name, lco_account_id,
                ROW_NUMBER() OVER (PARTITION BY device_id ORDER BY plan_expiry_time DESC) as rn
            FROM PROD_DB.PUBLIC.T_WG_CUSTOMER
            WHERE device_id IS NOT NULL
        )
        SELECT d.INCIDENT_ID,
            stm.KAPTURE_TICKET_ID,
            stm.last_title as TICKET_TITLE,
            DATEADD(MINUTE, 330, stm.ticket_added_time) as TICKET_TIME,
            stm.customer_mobile as TICKET_MOBILE
        FROM PROD_DB.BUSINESS_EFFICIENCY_ROUTER_OUTAGE_DETECTION_PUBLIC.INCIDENT_IMPACTED_DEVICE d
        JOIN latest_customer c ON d.DEVICE_ID = c.DEVICE_ID AND c.rn = 1
        INNER JOIN PROD_DB.PUBLIC.SERVICE_TICKET_MODEL stm
            ON RIGHT(stm.customer_mobile, 10) = RIGHT(c.MOBILE, 10)
            AND DATEADD(MINUTE, 330, stm.ticket_added_time) >= d.CREATED_AT
            AND (d.RECOVERY_AT IS NULL OR DATEADD(MINUTE, 330, stm.ticket_added_time) <= DATEADD(MINUTE, 60, d.RECOVERY_AT))
            AND stm.ticket_type = 3
            AND stm.last_title ILIKE '%Internet Supply Down%'
        WHERE d._FIVETRAN_DELETED = FALSE
        AND d.INCIDENT_ID IN ({ids_str})
        """
        return run_query(sql)["rows"]

    tickets_by_incident: dict[int, list[dict]] = {}
    batches = list(_batched_ids(incident_ids, batch_size=100))
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_query_batch, b): b for b in batches}
        for f in as_completed(futures):
            for r in f.result():
                iid = r["INCIDENT_ID"]
                tickets_by_incident.setdefault(iid, []).append(r)
    return tickets_by_incident
