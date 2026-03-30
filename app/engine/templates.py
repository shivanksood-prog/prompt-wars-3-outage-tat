"""Message templates — English + Hindi with topology-based classification."""
from __future__ import annotations
from engine.classifier import ClassifiedIncident, geo_spread_meters
from engine.consolidator import PartnerDigest


def _gmaps_link(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps?q={lat},{lng}"


def _locality_str(inc: ClassifiedIncident) -> str:
    """Best locality string from devices."""
    localities = [d.locality for d in inc.devices if d.locality]
    if localities:
        return localities[0]
    addresses = [d.address for d in inc.devices if d.address]
    if addresses:
        addr = addresses[0]
        return addr[:60] + "..." if len(addr) > 60 else addr
    return "your network area"


def _incident_line_en(inc: ClassifiedIncident) -> str:
    loc = _locality_str(inc)
    devices = inc.device_count or len(inc.devices)
    ticket_flag = " ⚠️ Customer complained" if inc.has_ticket else ""
    maps = f" 📍 {_gmaps_link(inc.nav_lat, inc.nav_lng)}" if inc.nav_lat else ""
    return f"  • {devices} customers near {loc}{ticket_flag}{maps}"


def _incident_line_hi(inc: ClassifiedIncident) -> str:
    loc = _locality_str(inc)
    devices = inc.device_count or len(inc.devices)
    ticket_flag = " ⚠️ Customer ne complaint ki hai" if inc.has_ticket else ""
    maps = f"\n    📍 Location: {_gmaps_link(inc.nav_lat, inc.nav_lng)}" if inc.nav_lat else ""
    return f"  • {devices} customers, {loc}{ticket_flag}{maps}"


def build_message_en(digest: PartnerDigest, map_url: str) -> str:
    """Build consolidated English message for a partner."""
    lines = []
    lines.append(f"🔔 WIOM OUTAGE ALERT — {digest.total_incidents} issue(s), {digest.total_devices} customers affected\n")

    # OLT/Backbone — most critical, show first
    if digest.olt_backbone:
        total_dev = sum(i.device_count or len(i.devices) for i in digest.olt_backbone)
        lines.append(f"🔴 MAJOR NETWORK ISSUE — {total_dev} customers affected")
        lines.append("   Large part of your network is down.")
        lines.append("   Action: Check ISP connectivity, partner office power & equipment.")
        lines.append("   Contact your ISP immediately if OLT/equipment is fine.")
        lines.append("   ❌ Individual device restarts will NOT help.")
        for inc in digest.olt_backbone:
            lines.append(_incident_line_en(inc))
        lines.append("")

    # Primary splitter
    if digest.primary_splitter:
        n = len(digest.primary_splitter)
        lines.append(f"🟠 {n} PRIMARY SPLITTER ISSUE(S) — Larger area affected")
        lines.append("   Multiple secondary splitters feeding from the same primary splitter are down.")
        lines.append("   Action: Check primary splitter and main fiber trunk in the affected area.")
        for inc in digest.primary_splitter:
            lines.append(_incident_line_en(inc))
        lines.append("")

    # Secondary splitter
    if digest.secondary_splitter:
        n = len(digest.secondary_splitter)
        lines.append(f"🟡 {n} SECONDARY SPLITTER ISSUE(S) — Local cable/fiber")
        lines.append("   Up to 8 customers on the same splitter are down.")
        lines.append("   Action: Send technician to splitter near customer location.")
        lines.append("   ❌ Do NOT restart customer devices — it won't help.")
        for inc in digest.secondary_splitter:
            lines.append(_incident_line_en(inc))
        lines.append("")

    # Customer issue (with ticket)
    if digest.customer_issue:
        n = len(digest.customer_issue)
        lines.append(f"⚪ {n} CUSTOMER DEVICE ISSUE(S) — Customer reported")
        lines.append("   Individual devices — customer has called about the problem.")
        lines.append("   Action: Guide customer to check power and restart router.")
        for inc in digest.customer_issue:
            lines.append(_incident_line_en(inc))
        lines.append("")

    # Customer power (no ticket)
    if digest.customer_power:
        n = len(digest.customer_power)
        lines.append(f"⚪ {n} LIKELY CUSTOMER POWER OFF — No dispatch needed")
        lines.append("   1-3 devices with no complaint — customer may have switched off power.")
        lines.append("")

    # Ticket summary
    if digest.has_any_ticket:
        lines.append(f"📞 {digest.total_tickets} customer(s) have called CS — priority HIGH\n")

    lines.append(f"🗺️ View all outages on map: {map_url}")
    return "\n".join(lines)


def build_message_hi(digest: PartnerDigest, map_url: str) -> str:
    """Build consolidated Hindi/Hinglish message."""
    lines = []
    lines.append(f"🔔 WIOM OUTAGE ALERT — {digest.total_incidents} issue(s), {digest.total_devices} customers affected\n")

    if digest.olt_backbone:
        total_dev = sum(i.device_count or len(i.devices) for i in digest.olt_backbone)
        lines.append(f"🔴 BADA NETWORK ISSUE — {total_dev} customers affected")
        lines.append("   Aapke network ka bada hissa down hai.")
        lines.append("   Action: ISP connectivity check karein, partner office mein power aur equipment dekhein.")
        lines.append("   Agar OLT/equipment sahi hai toh ISP ko turant call karein.")
        lines.append("   ❌ Customer devices restart karne se kuch nahi hoga.")
        for inc in digest.olt_backbone:
            lines.append(_incident_line_hi(inc))
        lines.append("")

    if digest.primary_splitter:
        n = len(digest.primary_splitter)
        lines.append(f"🟠 {n} PRIMARY SPLITTER ISSUE — Bada area affected")
        lines.append("   Ek primary splitter se connected multiple secondary splitters down hain.")
        lines.append("   Action: Primary splitter aur main fiber trunk check karein is area mein.")
        for inc in digest.primary_splitter:
            lines.append(_incident_line_hi(inc))
        lines.append("")

    if digest.secondary_splitter:
        n = len(digest.secondary_splitter)
        lines.append(f"🟡 {n} SECONDARY SPLITTER ISSUE — Local cable/fiber")
        lines.append("   Ek splitter pe 8 tak customer down hain.")
        lines.append("   Action: Technician bhejein customer ke paas wale splitter pe. Fiber check karein.")
        lines.append("   ❌ Customer devices restart MAT karein — kaam nahi karega.")
        for inc in digest.secondary_splitter:
            lines.append(_incident_line_hi(inc))
        lines.append("")

    if digest.customer_issue:
        n = len(digest.customer_issue)
        lines.append(f"⚪ {n} CUSTOMER DEVICE ISSUE — Customer ne report kiya")
        lines.append("   Individual devices — customer ne call kiya hai.")
        lines.append("   Action: Customer ko power check aur router restart karne bolein.")
        for inc in digest.customer_issue:
            lines.append(_incident_line_hi(inc))
        lines.append("")

    if digest.customer_power:
        n = len(digest.customer_power)
        lines.append(f"⚪ {n} CUSTOMER POWER OFF LAGTA HAI — Dispatch ki zaroorat nahi")
        lines.append("   1-3 devices bina complaint ke — shayad customer ne power off kiya hai.")
        lines.append("")

    if digest.has_any_ticket:
        lines.append(f"📞 {digest.total_tickets} customer(s) ne CS pe call kiya hai — HIGH PRIORITY\n")

    lines.append(f"🗺️ Saare outages map pe dekhein: {map_url}")
    return "\n".join(lines)
