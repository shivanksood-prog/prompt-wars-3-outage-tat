"""FastAPI app — dashboard + partner map views."""
from __future__ import annotations
import os
import json
import time
import threading
from datetime import datetime

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from engine.pipeline import run_pipeline
from engine.classifier import ClassifiedIncident
from engine.consolidator import PartnerDigest

app = FastAPI(title="Outage Intelligent Payload")

# ── Base URL: set via env var, or auto-detected from first request ──
_base_url: str = os.environ.get("BASE_URL", "")

# ── In-memory cache ──
_cache: dict = {"partners": {}, "incidents": [], "stats": {}, "last_refresh": None}
_lock = threading.Lock()


def _refresh(lookback_hours: int = 6):
    global _cache
    try:
        result = run_pipeline(lookback_hours=lookback_hours, base_url=_base_url)
        with _lock:
            _cache = {**result, "last_refresh": datetime.utcnow().isoformat()}
        print(f"[{datetime.utcnow().isoformat()}] Refreshed: {result['stats']}")
    except Exception as e:
        print(f"[ERROR] Refresh failed: {e}")


# ── Background refresh thread ──
def _bg_refresh():
    while True:
        _refresh(6)
        time.sleep(300)


_thread = threading.Thread(target=_bg_refresh, daemon=True)
_thread.start()


# ── Helper: incident to dict ──
FRIENDLY_LABELS = {
    "CUSTOMER_POWER": "Customer power off — no action needed",
    "CUSTOMER_ISSUE": "Customer device issue — customer reported",
    "SECONDARY_SPLITTER": "Secondary splitter issue — send technician to this splitter",
    "PRIMARY_SPLITTER": "Primary splitter issue — larger area affected",
    "OLT_BACKBONE": "Major network issue — check ISP, partner office & backbone",
}

FRIENDLY_ACTIONS = {
    "CUSTOMER_POWER": "Customer ne power off kiya hoga. Aapko kuch karne ki zaroorat nahi.",
    "CUSTOMER_ISSUE": "Customer ne call kiya hai. Power check aur router restart karne bolein.",
    "SECONDARY_SPLITTER": "Technician bhejein customer ke paas wale splitter pe. Fiber check karein. Devices restart MAT karein.",
    "PRIMARY_SPLITTER": "Primary splitter aur main fiber trunk check karein is area mein. Multiple secondary splitters affected hain.",
    "OLT_BACKBONE": "ISP connectivity check karein, partner office mein power aur equipment dekhein. OLT sahi hai toh ISP ko turant call karein.",
}


def _inc_to_dict(inc: ClassifiedIncident) -> dict:
    return {
        "incident_id": inc.incident_id,
        "partner_id": inc.partner_id,
        "severity": inc.severity,
        "size_bucket": inc.size_bucket,
        "device_count": inc.device_count,
        "status": inc.status,
        "classification": inc.classification,
        "classification_label": FRIENDLY_LABELS.get(inc.classification, inc.classification),
        "action_label": FRIENDLY_ACTIONS.get(inc.classification, ""),
        "confidence": inc.confidence,
        "geo_spread": {
            "SINGLE_POINT": "1 location",
            "WITHIN_100M": "Within 100m (splitter range)",
            "WITHIN_500M": "Within 500m",
            "WITHIN_1KM": "Within 1km",
            "WITHIN_3KM": "Within 3km (primary splitter range)",
            "BEYOND_3KM": "Beyond 3km (OLT/backbone level)",
            "NO_GEO": "Location not available",
        }.get(inc.geo_spread_label, inc.geo_spread_label),
        "concurrent": inc.concurrent_count,
        "has_ticket": inc.has_ticket,
        "ticket_count": inc.ticket_count,
        "center_lat": inc.center_lat,
        "center_lng": inc.center_lng,
        "created_ist": inc.created_ist,
        "first_fail_ist": inc.first_fail_ist,
        "closed_ist": inc.closed_ist,
        "duration_minutes": inc.duration_minutes,
        "devices": [
            {
                "device_id": d.device_id, "lat": d.lat, "lng": d.lng,
                "address": d.address, "locality": d.locality,
                "customer_name": d.customer_name, "status": d.status,
                "down_time": d.down_time, "recovery_time": d.recovery_time,
            }
            for d in inc.devices
        ],
    }


# ── Auto-detect base URL from first request ──
@app.middleware("http")
async def detect_base_url(request: Request, call_next):
    global _base_url
    if not _base_url:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
        if host:
            _base_url = f"{scheme}://{host}"
            # Re-generate messages with correct URL on next refresh
            print(f"[INFO] Base URL detected: {_base_url}")
    return await call_next(request)


def _fix_msg_urls(msg: str) -> str:
    """Replace relative /partner/ URLs with absolute URLs if base_url is known."""
    if _base_url and msg and "/partner/" in msg and "://" not in msg.split("/partner/")[0].split("\n")[-1]:
        msg = msg.replace("/partner/", f"{_base_url}/partner/")
    return msg


# ── API routes ──
@app.get("/api/refresh")
def api_refresh(hours: int = Query(6)):
    _refresh(hours)
    return {"status": "ok", "stats": _cache.get("stats", {})}


@app.get("/api/feed")
def api_feed():
    with _lock:
        partners = _cache.get("partners", {})
        result = []
        for pid, digest in partners.items():
            result.append({
                "partner_id": pid,
                "total_incidents": digest.total_incidents,
                "total_devices": digest.total_devices,
                "total_tickets": digest.total_tickets,
                "olt_backbone": len(digest.olt_backbone),
                "primary_splitter": len(digest.primary_splitter),
                "secondary_splitter": len(digest.secondary_splitter),
                "customer_issue": len(digest.customer_issue),
                "customer_power": len(digest.customer_power),
                "message_en": _fix_msg_urls(digest.message_en),
                "message_hi": _fix_msg_urls(digest.message_hi),
                "center_lat": digest.center_lat,
                "center_lng": digest.center_lng,
            })
        return {
            "partners": sorted(result, key=lambda x: x["total_devices"], reverse=True),
            "stats": _cache.get("stats", {}),
            "last_refresh": _cache.get("last_refresh"),
        }


@app.get("/api/incidents")
def api_incidents():
    """All incidents, for grouping/sorting on dashboard."""
    with _lock:
        incidents = _cache.get("incidents", [])
        return {
            "incidents": [_inc_to_dict(i) for i in incidents],
            "stats": _cache.get("stats", {}),
            "last_refresh": _cache.get("last_refresh"),
        }


@app.get("/api/partner/{partner_id}")
def api_partner(partner_id: int):
    with _lock:
        incidents = _cache.get("incidents", [])
        partner_incidents = [_inc_to_dict(i) for i in incidents if i.partner_id == partner_id]
        digest = _cache.get("partners", {}).get(partner_id)
        return {
            "partner_id": partner_id,
            "incidents": partner_incidents,
            "message_en": _fix_msg_urls(digest.message_en) if digest else "",
            "message_hi": _fix_msg_urls(digest.message_hi) if digest else "",
        }


# ── HTML pages ──
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


@app.get("/partner/{partner_id}/map", response_class=HTMLResponse)
def partner_map(partner_id: int):
    return MAP_HTML.replace("__PARTNER_ID__", str(partner_id))


# ── Dashboard HTML ──
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wiom — Outage Intelligent Payload</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#FAF9FC;--card:#FFF;--border:#D7D3E0;--text:#161021;--muted:#665E75;--accent:#D9008D;--accent-bg:#FFE5F6;--green:#008043;--green-bg:#E1FAED;--red:#E01E00;--red-bg:#FFE9E5;--amber:#FF8000;--amber-bg:#FFE6CC;--purple:#6D17CE;--purple-bg:#F1E5FF;--neutral100:#F1EDF7;--neutral800:#352D42;}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);font-size:13px;-webkit-font-smoothing:antialiased;}

/* Header */
.header{background:var(--neutral800);padding:20px 28px;display:flex;align-items:center;justify-content:space-between;}
.header-left h1{font-size:18px;font-weight:800;color:#FAF9FC;}
.header-left p{font-size:11px;color:#A7A1B2;margin-top:2px;}
.header-right{display:flex;align-items:center;gap:12px;}
.header-right select{padding:6px 10px;border-radius:6px;border:1px solid #443152;background:#443152;color:#FAF9FC;font-size:11px;font-weight:600;cursor:pointer;}
.header-right button{padding:7px 16px;border-radius:6px;border:none;background:var(--accent);color:white;font-size:11px;font-weight:700;cursor:pointer;transition:opacity .15s;}
.header-right button:hover{opacity:.85;}
.refresh-bar{font-size:10px;color:var(--muted);padding:6px 28px;background:var(--neutral100);border-bottom:1px solid var(--border);}

/* Stats */
.stats{display:flex;gap:10px;padding:16px 28px;flex-wrap:wrap;}
.stat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 18px;min-width:110px;box-shadow:0 1px 2px rgba(22,16,33,.04);}
.stat .v{font-size:24px;font-weight:800;line-height:1;}
.stat .l{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-top:4px;font-weight:600;}

/* Feed */
.feed{padding:12px 28px;}
.row{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin-bottom:12px;transition:box-shadow .15s;box-shadow:0 1px 2px rgba(22,16,33,.03);}
.row:hover{box-shadow:0 3px 12px rgba(22,16,33,.08);}
.row-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;}
.row-pid{font-weight:800;font-size:14px;color:var(--text);}
.row-devices{font-size:12px;color:var(--muted);margin-top:1px;}
.tags{display:flex;gap:5px;flex-wrap:wrap;}
.tag{padding:3px 9px;border-radius:100px;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.3px;}
.tag-splitter{background:var(--red-bg);color:var(--red);}
.tag-olt{background:var(--amber-bg);color:var(--amber);}
.tag-trunk{background:var(--accent-bg);color:var(--accent);}
.tag-device{background:var(--neutral100);color:var(--muted);}
.tag-power{background:var(--neutral100);color:var(--muted);}
.tag-ticket{background:var(--green-bg);color:var(--green);}

/* Action summary inside row */
.row-actions{margin:10px 0;display:flex;flex-direction:column;gap:6px;}
.action-line{display:flex;align-items:flex-start;gap:8px;font-size:12px;line-height:1.5;padding:8px 12px;border-radius:8px;}
.action-line.fix{background:var(--red-bg);color:var(--red);}
.action-line.check{background:var(--amber-bg);color:var(--amber);}
.action-line.info{background:var(--neutral100);color:var(--muted);}
.action-line.complaint{background:var(--green-bg);color:var(--green);}
.action-line .act-icon{font-size:14px;flex-shrink:0;margin-top:1px;}
.action-line .act-text{flex:1;}
.action-line strong{color:var(--text);}

.row-btns{display:flex;gap:8px;margin-top:12px;}
.btn-map{padding:7px 16px;background:var(--accent);color:white;border-radius:7px;font-size:11px;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;gap:4px;transition:opacity .15s;}
.btn-map:hover{opacity:.85;}
.btn-msg{padding:7px 14px;background:var(--neutral100);color:var(--muted);border-radius:7px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--border);display:inline-flex;align-items:center;gap:4px;}

/* Message box */
.msg-wrap{display:none;margin-top:12px;}
.msg-wrap.show{display:block;}
.msg-tabs{display:flex;gap:5px;margin-bottom:6px;}
.msg-tab{padding:4px 12px;border-radius:5px;font-size:10px;font-weight:700;cursor:pointer;border:1px solid var(--border);background:var(--card);color:var(--muted);}
.msg-tab.active{background:var(--accent);color:white;border-color:var(--accent);}
.msg-box{padding:14px;background:var(--neutral100);border-radius:8px;white-space:pre-wrap;font-family:'JetBrains Mono',monospace;font-size:11px;line-height:1.7;max-height:360px;overflow-y:auto;display:none;}
.msg-box.show{display:block;}

/* View tabs */
.view-tabs{display:flex;gap:6px;padding:10px 28px;border-bottom:1px solid var(--border);background:var(--bg);}
.vtab{padding:6px 16px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;color:var(--muted);border:1px solid var(--border);background:var(--card);transition:all .15s;}
.vtab.active{background:var(--accent);color:white;border-color:var(--accent);}
.vtab:hover:not(.active){background:var(--neutral100);}

/* Grouped view */
.group-card{background:var(--card);border:1px solid var(--border);border-radius:12px;margin:12px 28px;overflow:hidden;box-shadow:0 1px 3px rgba(22,16,33,.04);}
.group-header{padding:16px 20px;border-bottom:1px solid var(--border);}
.group-title{font-size:15px;font-weight:800;display:flex;align-items:center;gap:8px;}
.group-count{background:var(--neutral100);color:var(--muted);font-size:11px;padding:2px 8px;border-radius:100px;font-weight:700;}
.group-meta{display:flex;gap:12px;margin-top:6px;font-size:11px;flex-wrap:wrap;}
.gm-active{color:var(--red);font-weight:700;}
.gm-resolved{color:var(--green);font-weight:600;}
.gm-devices{color:var(--muted);}
.gm-tickets{color:var(--green);font-weight:600;}
.group-action{margin-top:8px;padding:6px 10px;background:var(--neutral100);border-radius:6px;font-size:11px;color:var(--muted);line-height:1.4;}
.group-incidents{max-height:400px;overflow-y:auto;}

/* Incident row inside group */
.inc-row{display:flex;align-items:center;gap:12px;padding:10px 20px;border-bottom:1px solid #F1EDF7;font-size:12px;transition:background .1s;}
.inc-row:hover{background:rgba(217,0,141,.02);}
.inc-row:last-child{border-bottom:none;}
.inc-status{width:80px;flex-shrink:0;}
.st-active{color:var(--red);font-weight:700;font-size:11px;}
.st-resolved{color:var(--green);font-weight:600;font-size:11px;}
.inc-row.st-resolved{opacity:.65;}
.inc-main{flex:1;min-width:0;}
.inc-top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.inc-devices{font-weight:700;color:var(--text);}
.inc-loc{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px;}
.inc-ticket{color:var(--green);font-weight:600;font-size:11px;}
.inc-time{color:var(--muted);font-size:11px;margin-top:2px;}
.inc-actions{flex-shrink:0;}
.inc-map{padding:4px 10px;background:var(--accent);color:white;border-radius:5px;font-size:10px;font-weight:700;text-decoration:none;}

.empty-state{text-align:center;padding:48px 24px;color:var(--muted);}
.empty-state h3{font-size:16px;margin-bottom:6px;color:var(--text);}

/* Demo section */
.demo{margin:16px 28px;background:var(--card);border:2px solid var(--accent);border-radius:14px;overflow:hidden;box-shadow:0 2px 12px rgba(217,0,141,.08);}
.demo-header{padding:14px 20px;background:linear-gradient(135deg,#443152,#161021);display:flex;align-items:center;justify-content:space-between;cursor:pointer;}
.demo-header h2{color:#FAF9FC;font-size:14px;font-weight:800;display:flex;align-items:center;gap:8px;}
.demo-header h2 span{background:var(--accent);color:white;font-size:9px;padding:2px 8px;border-radius:100px;text-transform:uppercase;letter-spacing:.5px;}
.demo-toggle{color:#A7A1B2;font-size:18px;transition:transform .2s;}
.demo-body{padding:20px;display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap;}
.demo-body.hidden{display:none;}

/* Steps column */
.demo-steps{flex:1;min-width:280px;}
.step{display:flex;gap:12px;align-items:flex-start;margin-bottom:16px;opacity:.4;transition:opacity .3s;}
.step.active{opacity:1;}
.step.done{opacity:.6;}
.step-num{width:28px;height:28px;border-radius:50%;background:var(--neutral100);color:var(--muted);font-size:12px;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:all .3s;}
.step.active .step-num{background:var(--accent);color:white;}
.step.done .step-num{background:var(--green);color:white;}
.step-content h4{font-size:12px;font-weight:700;margin-bottom:3px;}
.step-content p{font-size:11px;color:var(--muted);line-height:1.4;}

/* Step 1: selector */
.demo-select{width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:8px;font-size:12px;font-family:'Inter',sans-serif;margin-top:8px;cursor:pointer;background:var(--card);}
.demo-select:focus{border-color:var(--accent);outline:none;}

/* Step 2: send btn */
.demo-send{margin-top:8px;padding:10px 24px;border:none;border-radius:8px;font-size:12px;font-weight:700;cursor:pointer;background:var(--accent);color:white;display:none;transition:opacity .15s;}
.demo-send:hover{opacity:.85;}
.demo-send:disabled{opacity:.4;cursor:not-allowed;}

/* Phone mockup */
.phone{width:300px;flex-shrink:0;position:relative;}
.phone-frame{background:#161021;border-radius:28px;padding:8px;box-shadow:0 4px 24px rgba(22,16,33,.2);}
.phone-notch{width:100px;height:6px;background:#352D42;border-radius:3px;margin:4px auto 8px;}
.phone-screen{background:#ECE5DD;border-radius:20px;min-height:340px;max-height:400px;overflow-y:auto;padding:12px;display:flex;flex-direction:column;justify-content:flex-end;}
.phone-empty{text-align:center;color:#A7A1B2;font-size:11px;padding:40px 20px;margin:auto;}
.phone-home{width:40px;height:5px;background:#352D42;border-radius:3px;margin:8px auto 4px;}

/* Chat bubble */
.chat-bubble{background:white;border-radius:0 12px 12px 12px;padding:10px 14px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.08);font-size:11px;line-height:1.6;white-space:pre-wrap;max-height:240px;overflow-y:auto;animation:slideIn .4s ease-out;}
.chat-bubble .cb-sender{font-size:10px;font-weight:700;color:var(--accent);margin-bottom:4px;}
.chat-bubble .cb-time{font-size:9px;color:#A7A1B2;text-align:right;margin-top:4px;}
.chat-bubble a{color:var(--accent);text-decoration:underline;font-weight:600;}
@keyframes slideIn{from{opacity:0;transform:translateY(12px);}to{opacity:1;transform:translateY(0);}}

/* CTA in chat */
.chat-cta{display:block;margin-top:10px;padding:10px;background:var(--accent);color:white;border-radius:8px;text-align:center;font-size:12px;font-weight:700;text-decoration:none;animation:pulse 1.5s ease-in-out infinite;}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(217,0,141,.3);}50%{box-shadow:0 0 0 8px rgba(217,0,141,0);}}

@media(max-width:700px){
  .demo-body{flex-direction:column;}
  .phone{width:100%;max-width:320px;margin:0 auto;}
  .demo{margin:12px 12px;}
}
</style>
</head><body>

<div class="header">
  <div class="header-left">
    <h1>Outage Intelligent Payload</h1>
    <p>Live feed — consolidated partner alerts with diagnosis + map links</p>
  </div>
  <div class="header-right">
    <select id="hours"><option value="6" selected>Last 6 hours</option><option value="12">Last 12 hours</option><option value="24">Last 24 hours</option><option value="48">Last 48 hours</option><option value="168">Last 7 days</option></select>
    <button onclick="manualRefresh()">Refresh Now</button>
  </div>
</div>
<div class="refresh-bar" id="refresh-time">Loading data...</div>

<!-- DEMO SECTION -->
<div class="demo">
  <div class="demo-header" onclick="toggleDemo()">
    <h2>🎯 Try the Partner Experience <span>Live Demo</span></h2>
    <span class="demo-toggle" id="demo-arrow">▼</span>
  </div>
  <div class="demo-body" id="demo-body">
    <div class="demo-steps">
      <div class="step active" id="step-1">
        <div class="step-num">1</div>
        <div class="step-content">
          <h4>Select an Outage</h4>
          <p>Pick any active partner outage to see what they would receive.</p>
          <select class="demo-select" id="demo-partner" onchange="onDemoSelect()">
            <option value="">— Select a partner outage —</option>
          </select>
        </div>
      </div>
      <div class="step" id="step-2">
        <div class="step-num">2</div>
        <div class="step-content">
          <h4>Send Alert to Partner</h4>
          <p>Click to simulate sending the intelligent payload message.</p>
          <button class="demo-send" id="demo-send" onclick="onDemoSend()">📩 Send Alert Message</button>
        </div>
      </div>
      <div class="step" id="step-3">
        <div class="step-num">3</div>
        <div class="step-content">
          <h4>Partner Receives Message</h4>
          <p>See the message appear on the partner's phone with diagnosis + map link.</p>
        </div>
      </div>
      <div class="step" id="step-4">
        <div class="step-num">4</div>
        <div class="step-content">
          <h4>Partner Opens Map</h4>
          <p>Partner clicks the link and sees exactly where to go and what to do.</p>
        </div>
      </div>
    </div>
    <div class="phone">
      <div class="phone-frame">
        <div class="phone-notch"></div>
        <div class="phone-screen" id="phone-screen">
          <div class="phone-empty">Select an outage to start the demo →</div>
        </div>
        <div class="phone-home"></div>
      </div>
    </div>
  </div>
</div>

<div class="stats" id="stats"></div>
<div class="view-tabs" id="view-tabs">
  <div class="vtab active" onclick="setView('grouped')">Group by Issue Type</div>
  <div class="vtab" onclick="setView('partners')">Group by Partner</div>
</div>
<div class="feed" id="feed"></div>

<script>
let allIncidents = [];
let allPartners = [];
let currentView = 'grouped';

const CLS_META = {
  OLT_BACKBONE:        {icon:'🔴', label:'OLT / Backbone Issue', color:'var(--red)', bg:'var(--red-bg)', action:'ISP connectivity check karein, partner office mein power aur equipment dekhein. OLT sahi hai toh ISP ko turant call karein.'},
  PRIMARY_SPLITTER:    {icon:'🟠', label:'Primary Splitter Issue', color:'var(--amber)', bg:'var(--amber-bg)', action:'Primary splitter aur main fiber trunk check karein. Multiple secondary splitters is area mein affected hain.'},
  SECONDARY_SPLITTER:  {icon:'🟡', label:'Secondary Splitter Issue', color:'var(--accent)', bg:'var(--accent-bg)', action:'Technician bhejein customer ke paas wale splitter pe. Fiber check karein. Devices restart MAT karein.'},
  CUSTOMER_ISSUE:      {icon:'⚪', label:'Customer Device (Reported)', color:'var(--muted)', bg:'var(--neutral100)', action:'Customer ne call kiya hai. Power check aur router restart karne bolein.'},
  CUSTOMER_POWER:      {icon:'⚪', label:'Customer Power Off', color:'var(--muted)', bg:'var(--neutral100)', action:'Customer ne power off kiya hoga. Aapko kuch karne ki zaroorat nahi.'},
};
const CLS_ORDER = ['OLT_BACKBONE','PRIMARY_SPLITTER','SECONDARY_SPLITTER','CUSTOMER_ISSUE','CUSTOMER_POWER'];

async function loadFeed() {
  try {
    const [feedResp, incResp] = await Promise.all([fetch('/api/feed'), fetch('/api/incidents')]);
    const feedData = await feedResp.json();
    const incData = await incResp.json();
    allPartners = feedData.partners || [];
    allIncidents = incData.incidents || [];
    renderStats(incData.stats);
    render();
    populateDemoSelect();
    const t = feedData.last_refresh;
    document.getElementById('refresh-time').textContent = t
      ? 'Last refresh: ' + new Date(t+'Z').toLocaleTimeString() + ' · Auto-refreshes every 5 min'
      : 'Loading initial data...';
  } catch(e) { console.error(e); }
}

function renderStats(s) {
  if (!s) return;
  const bc = s.by_classification || {};
  const active = allIncidents.filter(i=>i.status!=='CLOSED').length;
  const resolved = allIncidents.filter(i=>i.status==='CLOSED').length;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="v" style="color:var(--accent)">${s.total_incidents||0}</div><div class="l">Total Incidents</div></div>
    <div class="stat"><div class="v" style="color:var(--red)">${active}</div><div class="l">Active</div></div>
    <div class="stat"><div class="v" style="color:var(--green)">${resolved}</div><div class="l">Resolved</div></div>
    <div class="stat"><div class="v" style="color:var(--purple)">${s.total_partners||0}</div><div class="l">Partners</div></div>
    <div class="stat"><div class="v" style="color:var(--text)">${s.total_devices||0}</div><div class="l">Devices</div></div>
    <div class="stat"><div class="v" style="color:var(--red)">${bc.OLT_BACKBONE||0}</div><div class="l">OLT/Backbone</div></div>
    <div class="stat"><div class="v" style="color:var(--amber)">${bc.PRIMARY_SPLITTER||0}</div><div class="l">Primary Splitter</div></div>
    <div class="stat"><div class="v" style="color:var(--accent)">${bc.SECONDARY_SPLITTER||0}</div><div class="l">Secondary Splitter</div></div>
    <div class="stat"><div class="v" style="color:var(--muted)">${(bc.CUSTOMER_ISSUE||0)+(bc.CUSTOMER_POWER||0)}</div><div class="l">Customer</div></div>
  `;
}

function setView(v) {
  currentView = v;
  document.querySelectorAll('.vtab').forEach(t => t.classList.toggle('active', t.textContent.includes(v==='grouped'?'Issue':'Partner')));
  render();
}

function render() {
  if (currentView === 'grouped') renderGrouped();
  else renderPartners();
}

function timeAgo(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  const mins = Math.round((now - d) / 60000);
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.floor(mins/60);
  if (hrs < 24) return hrs + 'h ' + (mins%60) + 'm ago';
  return Math.floor(hrs/24) + 'd ago';
}

function fmtTime(ts) {
  if (!ts) return '?';
  return ts.replace('T',' ').substring(0,16);
}

function renderGrouped() {
  const feed = document.getElementById('feed');
  if (!allIncidents.length) {
    feed.innerHTML = '<div class="empty-state"><h3>No incidents found</h3><p>Try increasing the lookback window.</p></div>';
    return;
  }

  // Group by classification
  const groups = {};
  allIncidents.forEach(inc => {
    const cls = inc.classification;
    if (!groups[cls]) groups[cls] = [];
    groups[cls].push(inc);
  });

  let html = '';
  CLS_ORDER.forEach(cls => {
    const incs = groups[cls];
    if (!incs || !incs.length) return;
    const meta = CLS_META[cls] || {icon:'?',label:cls,color:'var(--muted)',bg:'var(--neutral100)',action:''};

    // Sort: active first, then by time (newest first)
    incs.sort((a,b) => {
      if (a.status === 'CLOSED' && b.status !== 'CLOSED') return 1;
      if (a.status !== 'CLOSED' && b.status === 'CLOSED') return -1;
      return (b.first_fail_ist||b.created_ist||'').localeCompare(a.first_fail_ist||a.created_ist||'');
    });

    const activeCount = incs.filter(i=>i.status!=='CLOSED').length;
    const resolvedCount = incs.filter(i=>i.status==='CLOSED').length;
    const totalDevices = incs.reduce((s,i)=>s+i.device_count,0);
    const withTickets = incs.filter(i=>i.has_ticket).length;

    html += `
    <div class="group-card" style="border-left:4px solid ${meta.color};">
      <div class="group-header">
        <div class="group-title">${meta.icon} ${meta.label} <span class="group-count">${incs.length}</span></div>
        <div class="group-meta">
          ${activeCount ? `<span class="gm-active">● ${activeCount} active</span>` : ''}
          ${resolvedCount ? `<span class="gm-resolved">✓ ${resolvedCount} resolved</span>` : ''}
          <span class="gm-devices">📱 ${totalDevices} devices</span>
          ${withTickets ? `<span class="gm-tickets">📞 ${withTickets} with complaints</span>` : ''}
        </div>
        <div class="group-action">${meta.action}</div>
      </div>
      <div class="group-incidents">
        ${incs.map(inc => {
          const isActive = inc.status !== 'CLOSED';
          const statusCls = isActive ? 'st-active' : 'st-resolved';
          const statusTxt = isActive ? '● Active' : '✓ Resolved';
          const ts = inc.first_fail_ist || inc.created_ist || '';
          const dur = inc.duration_minutes;
          let durStr = '';
          if (dur) { const h=Math.floor(dur/60),m=dur%60; durStr = h>0?h+'h '+m+'m':m+'m'; }
          const loc = (inc.devices && inc.devices.length) ? (inc.devices[0].locality || inc.devices[0].address || '') : '';

          return `<div class="inc-row ${statusCls}">
            <div class="inc-status"><span class="${statusCls}">${statusTxt}</span></div>
            <div class="inc-main">
              <div class="inc-top">
                <span class="inc-devices">${inc.device_count} devices</span>
                <span class="inc-loc">${loc ? loc.substring(0,40) : 'Location N/A'}</span>
                ${inc.has_ticket ? '<span class="inc-ticket">📞 CS complaint</span>' : ''}
              </div>
              <div class="inc-time">
                ⏱ ${fmtTime(ts)} (${durStr || timeAgo(ts)}) · Partner ${inc.partner_id}
                · <span style="color:var(--muted)">${inc.geo_spread}</span>
              </div>
            </div>
            <div class="inc-actions">
              <a href="/partner/${inc.partner_id}/map" target="_blank" class="inc-map">🗺️ Map</a>
            </div>
          </div>`;
        }).join('')}
      </div>
    </div>`;
  });

  feed.innerHTML = html;
}

function renderPartners() {
  const feed = document.getElementById('feed');
  if (!allPartners.length) {
    feed.innerHTML = '<div class="empty-state"><h3>No partners found</h3></div>';
    return;
  }
  feed.innerHTML = allPartners.map((p, i) => {
    let tags = '';
    if (p.olt_backbone) tags += `<span class="tag tag-splitter">${p.olt_backbone} OLT/backbone</span>`;
    if (p.primary_splitter) tags += `<span class="tag tag-olt">${p.primary_splitter} primary</span>`;
    if (p.secondary_splitter) tags += `<span class="tag tag-trunk">${p.secondary_splitter} secondary</span>`;
    if (p.customer_issue) tags += `<span class="tag tag-device">${p.customer_issue} cust</span>`;
    if (p.customer_power) tags += `<span class="tag tag-power">${p.customer_power} power</span>`;
    if (p.total_tickets) tags += `<span class="tag tag-ticket">📞 ${p.total_tickets}</span>`;
    return `
    <div class="row">
      <div class="row-top">
        <div>
          <div class="row-pid">Partner ${p.partner_id}</div>
          <div class="row-devices">${p.total_incidents} issue(s) · ${p.total_devices} devices</div>
        </div>
        <div class="tags">${tags}</div>
      </div>
      <div class="row-btns">
        <a class="btn-map" href="/partner/${p.partner_id}/map" target="_blank">🗺️ View Map</a>
        <div class="btn-msg" onclick="toggleMsg(${i})">💬 Message</div>
      </div>
      <div class="msg-wrap" id="msg-wrap-${i}">
        <div class="msg-tabs">
          <div class="msg-tab active" onclick="showLang(${i},'en')">English</div>
          <div class="msg-tab" onclick="showLang(${i},'hi')">Hindi</div>
        </div>
        <div class="msg-box show" id="msg-en-${i}">${escHtml(p.message_en)}</div>
        <div class="msg-box" id="msg-hi-${i}">${escHtml(p.message_hi)}</div>
      </div>
    </div>`;
  }).join('');
}

function toggleMsg(i) { document.getElementById('msg-wrap-'+i).classList.toggle('show'); }
function showLang(i, lang) {
  document.getElementById('msg-en-'+i).classList.toggle('show', lang==='en');
  document.getElementById('msg-hi-'+i).classList.toggle('show', lang==='hi');
  document.getElementById('msg-wrap-'+i).querySelectorAll('.msg-tab').forEach(t => t.classList.toggle('active', t.textContent.toLowerCase().includes(lang==='en'?'eng':'hin')));
}
function escHtml(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function manualRefresh() {
  const hours = document.getElementById('hours').value;
  document.getElementById('refresh-time').textContent = 'Refreshing...';
  await fetch('/api/refresh?hours='+hours);
  await loadFeed();
}

// ── Demo flow ──
let demoOpen = true;
let demoPartnerData = null;
let demoLang = 'hi';

function toggleDemo() {
  demoOpen = !demoOpen;
  document.getElementById('demo-body').classList.toggle('hidden', !demoOpen);
  document.getElementById('demo-arrow').textContent = demoOpen ? '▼' : '▶';
}

function populateDemoSelect() {
  const sel = document.getElementById('demo-partner');
  // Keep first placeholder option
  while (sel.options.length > 1) sel.remove(1);
  // Add partners sorted by devices (most impactful first), only those with actionable issues
  const actionable = allPartners.filter(p => p.olt_backbone || p.primary_splitter || p.secondary_splitter);
  const sorted = actionable.length ? actionable : allPartners;
  sorted.slice(0, 50).forEach(p => {
    let label = `Partner ${p.partner_id} — ${p.total_devices} devices, ${p.total_incidents} issue(s)`;
    const types = [];
    if (p.olt_backbone) types.push(p.olt_backbone + ' OLT');
    if (p.primary_splitter) types.push(p.primary_splitter + ' primary');
    if (p.secondary_splitter) types.push(p.secondary_splitter + ' secondary');
    if (types.length) label += ' [' + types.join(', ') + ']';
    const opt = document.createElement('option');
    opt.value = p.partner_id;
    opt.textContent = label;
    sel.appendChild(opt);
  });
}

function setDemoStep(n) {
  [1,2,3,4].forEach(i => {
    const el = document.getElementById('step-'+i);
    el.classList.remove('active','done');
    if (i < n) el.classList.add('done');
    else if (i === n) el.classList.add('active');
  });
}

function onDemoSelect() {
  const pid = document.getElementById('demo-partner').value;
  if (!pid) {
    setDemoStep(1);
    document.getElementById('demo-send').style.display = 'none';
    document.getElementById('phone-screen').innerHTML = '<div class="phone-empty">Select an outage to start the demo →</div>';
    return;
  }
  document.getElementById('demo-send').style.display = 'inline-block';
  document.getElementById('demo-send').disabled = false;
  document.getElementById('phone-screen').innerHTML = '<div class="phone-empty">Press "Send Alert Message" to simulate →</div>';
  setDemoStep(2);
  demoPartnerData = null;
}

async function onDemoSend() {
  const pid = document.getElementById('demo-partner').value;
  if (!pid) return;
  const btn = document.getElementById('demo-send');
  btn.disabled = true;
  btn.textContent = '⏳ Sending...';
  setDemoStep(3);

  // Fetch partner data
  try {
    const resp = await fetch('/api/partner/' + pid);
    demoPartnerData = await resp.json();
  } catch(e) {
    btn.textContent = '❌ Error';
    return;
  }

  btn.textContent = '✓ Sent';

  // Show message in phone
  const msg = demoPartnerData.message_hi || demoPartnerData.message_en || 'No message';
  const mapUrl = '/partner/' + pid + '/map';
  const now = new Date();
  const timeStr = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');

  const screen = document.getElementById('phone-screen');
  screen.innerHTML = `
    <div class="chat-bubble">
      <div class="cb-sender">WIOM Outage Alert</div>
      ${escHtml(msg).replace(/🗺️.*?(\/partner\/\d+\/map)/g, '🗺️ <a href="$1" target="_blank">View Outage Map</a>')}
      <a class="chat-cta" href="${mapUrl}" target="_blank">🗺️ Open Outage Map →</a>
      <div class="cb-time">${timeStr} ✓✓</div>
    </div>
  `;

  setTimeout(() => setDemoStep(4), 800);
}

loadFeed();
setInterval(loadFeed, 300000);
</script>
</body></html>"""


# ── Partner Map HTML ──
MAP_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Wiom — Your Outage Map</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{--bg:#FAF9FC;--card:#FFF;--border:#D7D3E0;--text:#161021;--muted:#665E75;--accent:#D9008D;--green:#008043;--green-bg:#E1FAED;--red:#E01E00;--red-bg:#FFE9E5;--amber:#FF8000;--amber-bg:#FFE6CC;--neutral100:#F1EDF7;--neutral800:#352D42;}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased;}
#map{height:45vh;width:100%;border-bottom:2px solid var(--accent);}
.header{padding:12px 16px;background:var(--neutral800);color:#FAF9FC;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px;}
.header h1{font-size:14px;font-weight:800;}
.header p{font-size:10px;color:#A7A1B2;margin-top:1px;}
.header-back{color:#A7A1B2;text-decoration:none;font-size:11px;font-weight:600;}
.header-back:hover{color:#FAF9FC;}

.summary{padding:10px 16px;display:flex;gap:6px;flex-wrap:wrap;}
.sum-card{padding:7px 10px;border-radius:6px;font-size:11px;font-weight:600;display:flex;align-items:center;gap:5px;}
.sum-card .dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;}
.sum-red{background:var(--red-bg);color:var(--red);}.sum-red .dot{background:var(--red);}
.sum-amber{background:var(--amber-bg);color:var(--amber);}.sum-amber .dot{background:var(--amber);}
.sum-grey{background:var(--neutral100);color:var(--muted);}.sum-grey .dot{background:var(--muted);}
.sum-green{background:var(--green-bg);color:var(--green);}.sum-green .dot{background:var(--green);}
.sum-pink{background:#FFE5F6;color:var(--accent);}.sum-pink .dot{background:var(--accent);}
.sum-total{background:var(--neutral800);color:#FAF9FC;font-weight:700;}

.action-panel{padding:10px 16px;}
.action-panel h3{font-size:12px;font-weight:700;margin-bottom:8px;color:var(--text);}
.action-item{display:flex;align-items:flex-start;gap:8px;padding:8px 12px;border-radius:8px;margin-bottom:6px;font-size:11px;line-height:1.5;}
.action-item.fix{background:var(--red-bg);}.action-item.check{background:var(--amber-bg);}
.action-item.info{background:var(--neutral100);}.action-item.complaint{background:var(--green-bg);}
.action-item .ai{font-size:14px;flex-shrink:0;}.action-item strong{color:var(--text);}

/* Message preview */
.msg-section{padding:14px 16px;border-top:1px solid var(--border);background:var(--card);}
.msg-section h3{font-size:12px;font-weight:700;color:var(--muted);margin-bottom:10px;text-transform:uppercase;letter-spacing:.4px;}
.msg-tabs{display:flex;gap:5px;margin-bottom:8px;}
.msg-tab{padding:5px 14px;border-radius:5px;font-size:11px;font-weight:700;cursor:pointer;border:1px solid var(--border);background:var(--card);color:var(--muted);}
.msg-tab.active{background:var(--accent);color:white;border-color:var(--accent);}
.msg-preview{padding:12px;background:var(--neutral100);border-radius:8px;white-space:pre-wrap;font-family:'JetBrains Mono',monospace;font-size:11px;line-height:1.7;max-height:200px;overflow-y:auto;}

/* Leaflet popup */
.leaflet-popup-content{font-family:'Inter',sans-serif;font-size:12px;line-height:1.5;max-width:280px;}
.popup-tag{display:inline-block;padding:3px 10px;border-radius:100px;font-size:10px;font-weight:700;margin-bottom:6px;}
.popup-nav{display:inline-block;margin-top:8px;padding:8px 16px;background:var(--green);color:white;border-radius:6px;font-size:11px;font-weight:700;text-decoration:none;width:100%;text-align:center;}

/* Mobile responsive */
@media(max-width:600px){
  #map{height:40vh;}
  .header{padding:10px 12px;}
  .header h1{font-size:13px;}
  .summary{padding:8px 12px;gap:5px;}
  .sum-card{padding:5px 8px;font-size:10px;}
  .action-panel{padding:8px 12px;}
  .action-item{padding:6px 10px;font-size:10px;}
  .msg-section{padding:10px 12px;}
  .leaflet-popup-content{font-size:11px;max-width:240px;}
}
</style>
</head><body>

<div class="header">
  <div>
    <h1>Your Network — Outage Map</h1>
    <p id="subtitle">Loading...</p>
  </div>
  <a href="/" class="header-back">← Dashboard</a>
</div>
<div id="map"></div>
<div class="summary" id="summary"></div>
<div class="action-panel" id="action-panel"></div>

<div class="msg-section">
  <h3>Partner Alert Message</h3>
  <div class="msg-tabs">
    <div class="msg-tab active" onclick="showMsg('en')">English</div>
    <div class="msg-tab" onclick="showMsg('hi')">Hindi</div>
  </div>
  <div class="msg-preview" id="msg-en"></div>
  <div class="msg-preview" id="msg-hi" style="display:none;"></div>
</div>

<script>
const PARTNER_ID = __PARTNER_ID__;
const map = L.map('map').setView([28.6, 77.2], 12);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap', maxZoom: 19
}).addTo(map);

const COLORS = {
  OLT_BACKBONE: '#E01E00', PRIMARY_SPLITTER: '#FF8000', SECONDARY_SPLITTER: '#D9008D',
  CUSTOMER_ISSUE: '#665E75', CUSTOMER_POWER: '#A7A1B2'
};
const LABELS = {
  OLT_BACKBONE: '🔴 OLT / Backbone issue', PRIMARY_SPLITTER: '🟠 Primary splitter issue',
  SECONDARY_SPLITTER: '🟡 Secondary splitter issue', CUSTOMER_ISSUE: '⚪ Customer device (reported)',
  CUSTOMER_POWER: '⚪ Customer power off'
};
const TAG_BG = {
  OLT_BACKBONE: '#FFE9E5', PRIMARY_SPLITTER: '#FFE6CC', SECONDARY_SPLITTER: '#FFE5F6',
  CUSTOMER_ISSUE: '#F1EDF7', CUSTOMER_POWER: '#F1EDF7'
};

let msgEn = '', msgHi = '';

function circleIcon(color, count) {
  return L.divIcon({
    html: `<div style="background:${color};width:26px;height:26px;border-radius:50%;border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,.3);display:flex;align-items:center;justify-content:center;color:white;font-size:10px;font-weight:800;">${count}</div>`,
    className: '', iconSize: [26, 26], iconAnchor: [13, 13]
  });
}

async function load() {
  const resp = await fetch(`/api/partner/${PARTNER_ID}`);
  const data = await resp.json();

  document.getElementById('subtitle').textContent =
    `${data.incidents.length} active outage(s) in your network`;
  msgEn = data.message_en;
  msgHi = data.message_hi;
  document.getElementById('msg-en').textContent = msgEn;
  document.getElementById('msg-hi').textContent = msgHi;

  const cls_count = {};
  let total_devices = 0, total_tickets = 0;
  data.incidents.forEach(inc => {
    cls_count[inc.classification] = (cls_count[inc.classification]||0) + 1;
    total_devices += inc.device_count;
    total_tickets += inc.ticket_count;
  });

  let sumHtml = `<div class="sum-card sum-total">📱 ${total_devices} customers</div>`;
  if (cls_count.OLT_BACKBONE) sumHtml += `<div class="sum-card sum-red"><span class="dot"></span>${cls_count.OLT_BACKBONE} OLT</div>`;
  if (cls_count.PRIMARY_SPLITTER) sumHtml += `<div class="sum-card sum-amber"><span class="dot"></span>${cls_count.PRIMARY_SPLITTER} primary</div>`;
  if (cls_count.SECONDARY_SPLITTER) sumHtml += `<div class="sum-card sum-pink"><span class="dot"></span>${cls_count.SECONDARY_SPLITTER} secondary</div>`;
  if (cls_count.CUSTOMER_ISSUE || cls_count.CUSTOMER_POWER) sumHtml += `<div class="sum-card sum-grey"><span class="dot"></span>${(cls_count.CUSTOMER_ISSUE||0)+(cls_count.CUSTOMER_POWER||0)} customer</div>`;
  if (total_tickets) sumHtml += `<div class="sum-card sum-green"><span class="dot"></span>${total_tickets} complaints</div>`;
  document.getElementById('summary').innerHTML = sumHtml;

  let actHtml = '<h3>Aapko kya karna hai (What you need to do)</h3>';
  if (cls_count.OLT_BACKBONE) actHtml += `<div class="action-item fix"><span class="ai">🔴</span><div><strong>Major network issue — ${cls_count.OLT_BACKBONE} incident(s)</strong><br>ISP connectivity check karein, partner office mein power aur equipment dekhein. OLT sahi hai toh ISP ko turant call karein.</div></div>`;
  if (cls_count.PRIMARY_SPLITTER) actHtml += `<div class="action-item check"><span class="ai">🟠</span><div><strong>Primary splitter — ${cls_count.PRIMARY_SPLITTER} area(s)</strong><br>Primary splitter aur main fiber trunk check karein.</div></div>`;
  if (cls_count.SECONDARY_SPLITTER) actHtml += `<div class="action-item check"><span class="ai">🟡</span><div><strong>${cls_count.SECONDARY_SPLITTER} jagah pe technician bhejein</strong><br>Splitter pe jaake fiber check karein. Devices restart MAT karein.</div></div>`;
  if (cls_count.CUSTOMER_ISSUE || cls_count.CUSTOMER_POWER) actHtml += `<div class="action-item info"><span class="ai">⚪</span><div>${(cls_count.CUSTOMER_ISSUE||0)+(cls_count.CUSTOMER_POWER||0)} customer device issue — dispatch ki zaroorat nahi.</div></div>`;
  if (total_tickets) actHtml += `<div class="action-item complaint"><span class="ai">📞</span><div><strong>${total_tickets} customer complaints</strong> — priority HIGH.</div></div>`;
  document.getElementById('action-panel').innerHTML = actHtml;

  // Plot on map
  const bounds = [];
  data.incidents.forEach(inc => {
    if (!inc.center_lat || !inc.center_lng) return;
    const color = COLORS[inc.classification] || '#A7A1B2';
    const label = LABELS[inc.classification] || inc.classification;
    const tagBg = TAG_BG[inc.classification] || '#F1EDF7';

    const marker = L.marker([inc.center_lat, inc.center_lng], {
      icon: circleIcon(color, inc.device_count)
    }).addTo(map);
    bounds.push([inc.center_lat, inc.center_lng]);

    let deviceHtml = '';
    (inc.devices||[]).forEach(d => {
      const dTime = d.down_time ? d.down_time.replace('T',' ').substring(11,16) : '';
      const rTime = d.recovery_time ? d.recovery_time.replace('T',' ').substring(11,16) : '';
      deviceHtml += `<div style="font-size:10px;padding:2px 0;border-bottom:1px solid #eee;">
        <strong>${d.customer_name||d.device_id}</strong> — ${(d.locality||d.address||'').substring(0,30)}
        <br>Down: ${dTime||'?'} ${d.recovery_time ? '→ Up: '+rTime+' ✓' : '<span style="color:#E01E00">● Still down</span>'}
      </div>`;
    });

    const ticketHtml = inc.has_ticket
      ? `<div style="margin-top:4px;padding:3px 6px;background:#E1FAED;border-radius:4px;font-size:10px;color:#008043;font-weight:600;">📞 ${inc.ticket_count} complaint(s)</div>` : '';

    const navLink = `https://www.google.com/maps?q=${inc.center_lat},${inc.center_lng}`;
    const downSince = inc.first_fail_ist || inc.created_ist || '';
    const timeDisplay = downSince ? downSince.replace('T',' ').substring(0,16) : '';
    let durationStr = '';
    if (inc.duration_minutes) {
      const h = Math.floor(inc.duration_minutes/60), m = inc.duration_minutes%60;
      durationStr = h>0 ? h+'h '+m+'m' : m+' min';
    } else if (downSince) {
      const diffMin = Math.round((new Date() - new Date(downSince))/60000);
      const h = Math.floor(diffMin/60), m = diffMin%60;
      durationStr = h>0 ? h+'h '+m+'m' : m+' min';
    }
    const statusLabel = inc.status==='CLOSED' ? '<span style="color:#008043;font-weight:700;">✓ Resolved</span>' : '<span style="color:#E01E00;font-weight:700;">● Active</span>';
    const actionLabel = inc.action_label || '';

    marker.bindPopup(`
      <div>
        <span class="popup-tag" style="background:${tagBg};color:${color};">${label}</span>
        <div style="font-weight:700;margin:4px 0;">${inc.device_count} customers affected</div>
        <div style="font-size:10px;color:#665E75;margin-bottom:3px;">
          📍 ${inc.geo_spread} · ${inc.device_count} device${inc.device_count!==1?'s':''}
          ${inc.concurrent>1 ? ' · '+inc.concurrent+' concurrent' : ''}
        </div>
        <div style="font-size:10px;color:#665E75;margin-bottom:3px;">
          ⏱ Down: <strong>${timeDisplay}</strong> (${durationStr}) ${statusLabel}
        </div>
        ${actionLabel ? `<div style="font-size:10px;padding:5px 7px;background:#F1EDF7;border-radius:4px;margin:5px 0;line-height:1.4;"><strong>Action:</strong> ${actionLabel}</div>` : ''}
        ${ticketHtml}
        <div style="margin-top:6px;max-height:100px;overflow-y:auto;">${deviceHtml}</div>
        <a class="popup-nav" href="${navLink}" target="_blank">📍 Navigate (Google Maps)</a>
      </div>
    `, {maxWidth: 280});
  });

  if (bounds.length) map.fitBounds(bounds, {padding: [30, 30], maxZoom: 15});
}

function showMsg(lang) {
  document.getElementById('msg-en').style.display = lang==='en' ? 'block' : 'none';
  document.getElementById('msg-hi').style.display = lang==='hi' ? 'block' : 'none';
  document.querySelectorAll('.msg-tab').forEach(t =>
    t.classList.toggle('active', t.textContent.toLowerCase().includes(lang==='en'?'eng':'hin'))
  );
}

load();
</script>
</body></html>"""
