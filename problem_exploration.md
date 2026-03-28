# Prompt Wars 3: Outage Resolution TAT — Problem Exploration

## 1. Problem Statement (One-liner)

Wiom detects outages in ~5 minutes, but **partners don't know WHY something is down or WHAT to do** — so resolution depends on blind escalation rather than guided diagnosis. The gap between detection and resolution is where customers lose internet and trust.

---

## 2. The System Today (V2.1)

### Detection Pipeline
- Ping every 5 minutes per device (~326K devices, ~3,000 partners)
- Outage confirmed after: 3 consecutive failed pings + 15 min duration
- Severity classified via **Size x Duration** matrix:

| Size \ Duration | Short (15-59m) | Medium (60-239m) | Long (≥240m) |
|---|---|---|---|
| **Tiny (1-9 devices)** | MICRO | MICRO | LOCAL |
| **Small (10-25)** | MICRO | LOCAL | MAJOR |
| **Medium (26-100)** | LOCAL | LOCAL | MAJOR |
| **Large (>100)** | MAJOR | MAJOR | MAJOR |

### Communication Rules
- **MICRO**: No user comms. Internal only.
- **LOCAL**: Partner gets 60-min grace window. If unresolved → user notified at ~75 min from start.
- **MAJOR**: Immediate user comms. No partner grace.

### Communication Channels
- WhatsApp (primary), App Push, SMS
- Partner alerts via WhatsApp templates
- Customer alerts: detection, delay-inform (T60), and resolved messages

---

## 3. What the Data Shows (Last 7-15 Days, Live from Metabase)

### 3.1 Scale of the Problem

| Metric (7-day avg) | Value |
|---|---|
| Daily incidents | **~24,000** |
| Daily devices impacted | **~37,000-42,000** |
| Daily partners affected | **~1,200** |
| P50 recovery time (overall) | **56-72 minutes** |
| P80 recovery time | **351-447 minutes (6-7.5 hrs)** |
| P95 recovery time | **847-957 minutes (14-16 hrs)** |

### 3.2 Incident Distribution by Type (7-day window)

| Size Bucket | Duration | Severity | Count | % of Total | Avg Duration |
|---|---|---|---|---|---|
| TINY | LONG (>4hr) | LOCAL | 82,624 | **43.5%** | 1,096 min (~18hr) |
| TINY | SHORT | MICRO | 56,933 | 30.0% | 31 min |
| TINY | MEDIUM | MICRO | 40,650 | 21.4% | 135 min |
| SMALL | SHORT | MICRO | 2,107 | 1.1% | 142 min |
| SMALL | MEDIUM | LOCAL | 898 | 0.5% | 303 min |
| MEDIUM | SHORT | LOCAL | 817 | 0.4% | 297 min |
| SMALL | LONG | MAJOR | 524 | 0.3% | 1,624 min (~27hr!) |
| MEDIUM | MEDIUM | LOCAL | 290 | 0.2% | 473 min |
| MEDIUM | LONG | MAJOR | 126 | 0.07% | 1,043 min |
| LARGE | SHORT | MAJOR | 91 | 0.05% | 605 min |
| LARGE | MEDIUM | MAJOR | 29 | 0.02% | 767 min |
| LARGE | LONG | MAJOR | 11 | 0.006% | 1,333 min |

**Key finding**: The single largest bucket is **TINY + LONG = LOCAL** at 43.5% of all incidents. These are 1-9 device outages lasting >4 hours. Small pockets, massive duration.

### 3.3 Day vs Night — The Killer Gap

| Time | Severity | Incidents | P50 Recovery | P80 Recovery | P95 Recovery |
|---|---|---|---|---|---|
| **DAY** | LOCAL | 21,847 | **61 min** | 351 min | 1,177 min |
| **NIGHT** | LOCAL | 62,782 | **431 min** | 652 min | 1,102 min |
| **DAY** | MAJOR | 355 | **32 min** | 167 min | 437 min |
| **NIGHT** | MAJOR | 426 | **61 min** | 357 min | 757 min |
| **DAY** | MICRO | 61,064 | **31 min** | 81 min | 176 min |
| **NIGHT** | MICRO | 38,626 | **38 min** | 112 min | 203 min |

**Night-time LOCAL P50 is 431 minutes vs Day 61 minutes — a 7x penalty**, far worse than the "2.9x" cited in the problem statement. Night incidents also outnumber day incidents 3:1 for LOCAL severity.

### 3.4 Communication Delivery Funnel (March 2026)

| Template | Audience | Sent | Delivered | Read | Delivery % | Read % |
|---|---|---|---|---|---|---|
| Detection | Customer | 42,432 | 41,479 | 32,304 | 92.9% | **77.9%** |
| Inform Delay | Customer | 5,022 | 4,923 | 3,925 | 66.7% | 79.7% |
| Resolved | Customer | 70,045 | 68,542 | 50,633 | 92.9% | 73.9% |
| Detection Minor | Partner | 2,935 | 2,966 | 2,300 | 99.0% | **77.6%** |
| Detection Major | Partner | 54 | 55 | 42 | 93.2% | 76.4% |
| Attention Minor | Partner | 13 | 13 | 12 | 56.5% | 92.3% |

**Key findings:**
- Partner reminder/attention messages are almost never sent (1-23 total in March). The escalation ladder is barely used.
- ~23% of partner detection messages are NOT read.
- "Inform Delay" messages to customers have only 66.7% delivery — the worst delivery rate.

### 3.5 CleverTap Event Funnel (March 2026)

| Type | Checkpoint | Incidents Reached | Partners Reached | Events Fired |
|---|---|---|---|---|
| Partner Comms | T0 (detection) | 7,399 | 827 | 7,743 |
| Partner Comms | T120 (2hr reminder) | **23** | 19 | 25 |
| Partner Comms | T240 (4hr attention) | 155 | 113 | 163 |
| Customer Comms | T0 | 396 | 170 | 64,393 |
| Customer Comms | T60 | 1,618 | 493 | 50,821 |
| Customer Comms | T240 | 2,573 | 572 | 30,538 |

**Key finding**: Of 7,399 incidents where partners were alerted at T0, only **23 triggered the T120 reminder** (0.3%). Either incidents resolve, or the system doesn't escalate. Given the long-tail recovery times, the escalation pipeline appears broken.

### 3.6 Incident Status (7 days)

| Status | Severity | Size | Count |
|---|---|---|---|
| CLOSED | MICRO | TINY | 95,549 |
| CLOSED | LOCAL | TINY | 74,002 |
| **ACTIVE** | **LOCAL** | **TINY** | **8,622** |
| ACTIVE | MICRO | TINY | 2,034 |
| ACTIVE | MAJOR | SMALL | 70 |

**8,622 LOCAL incidents are still ACTIVE** — mostly tiny pockets that went LOCAL because they crossed 4 hours and were never resolved.

---

## 4. The Five Major Problem Buckets

### BUCKET 1: The "Tiny + Long" Black Hole (43% of all incidents)

**What**: 1-9 devices go down and stay down for 18+ hours on average. These are single-lane, few-household pockets.

**Why it's hard**:
- Too small for the partner to notice or prioritize
- Current alert says "devices are down" but doesn't explain the cause
- Often a single fiber joint, a power issue at one junction, or a corroded splitter
- Partner has 50+ similar micro-pockets — no way to triage

**Evidence**: 82,624 incidents in 7 days. Avg duration 1,096 minutes. These are classified LOCAL only because of duration, not because of size. The partner gets an alert but has zero diagnostic information to act on.

**Core gap**: No root cause + no priority signal = partner ignores it.

---

### BUCKET 2: Night-time Resolution Collapse (7x penalty, not 2.9x)

**What**: Night LOCAL incidents take 431 min P50 vs 61 min during the day. Night incidents outnumber day 3:1 for LOCAL severity.

**Why it's hard**:
- Partners deprioritize Wiom at night, especially if serving multiple ISPs
- No technician availability after hours for many small partners
- No mechanism to auto-escalate or route to alternate resolution paths
- Current comms window (10am-8pm) means ~28% of incidents get no customer communication

**Evidence**: 62,782 night LOCAL incidents vs 21,847 day LOCAL. Night P50 is 7x worse. Night MAJOR P50 is 2x worse (61 vs 32 min).

**Core gap**: No differentiated handling for night-time. Same alerts, same grace windows, same expectations — despite fundamentally different partner capacity.

---

### BUCKET 3: Partner Escalation Ladder is Dead

**What**: The escalation system (T0 → T120 → T240) exists but barely fires. Of 7,399 T0 partner alerts, only 23 triggered T120 reminders.

**Why it's hard**:
- Reminder and attention templates have near-zero volume
- "Attention Minor" messages have 56.5% delivery rate — half don't even arrive
- Partners read 77% of detection alerts but there's no feedback on whether they acted
- No ACK tracking visible in the data — the system can't tell if a partner saw vs ignored

**Evidence**: Partner reminder messages in March: 1-23 total. Meanwhile, 8,622 LOCAL incidents sit ACTIVE. The funnel leaks massively between "partner alerted" and "partner acts."

**Core gap**: Alert ≠ Action. The system notifies but doesn't track engagement, doesn't escalate effectively, and doesn't provide diagnostic intelligence to motivate action.

---

### BUCKET 4: Root Cause Blindness — Partners Don't Know WHY

**What**: Partners receive a generic "X devices are down" alert. They don't know if it's a fiber cut, OLT failure, ISP backbone issue, or power outage — each requiring completely different actions.

**Why it's hard**:
- Fiber cut → dispatch technician to check physical cable
- OLT failure → call ISP NOC with specific OLT ID
- ISP backbone → nothing the partner can do; needs ISP-level escalation
- Power outage → check DG set or wait for power restoration
- Wrong action wastes time (e.g., restarting devices during a fiber cut)

**Evidence from problem statement**: "Partners either wait (hoping it resolves), do the wrong thing (restart devices when it's a fiber issue), or escalate to Wiom CS — adding to call load."

**Available signals not being used**:
- Optical power readings (can differentiate fiber cut vs OLT vs backbone)
- Affected device topology (single OLT port vs entire OLT vs multi-partner)
- Ping loss patterns (gradual degradation vs cliff-drop)
- Historical patterns at that location

**Core gap**: Telemetry data exists but isn't translated into actionable root cause diagnosis for the partner.

---

### BUCKET 5: ISP-Level Faults Create Fragmented Chaos

**What**: When the fault is upstream (ISP backbone, POP failure, city-wide issue), each partner sees only their slice. 15 partners call the ISP independently for the same backbone fault.

**Why it's hard**:
- Individual partners can't see the multi-partner correlation
- V2.1 can detect multi-partner patterns (LARGE bucket = >100 devices) but only 91-131 such incidents in 7 days
- No auto-escalation to ISP with aggregated evidence
- Partners waste time trying to fix something they literally cannot fix
- ISP response is slow when receiving fragmented individual complaints vs one coordinated escalation

**Evidence**: LARGE incidents are rare (131 closed in 7 days) but MAJOR severity with SMALL size bucket (524 incidents, 27hr avg duration) suggests many ISP-level faults get classified as collections of small incidents rather than one correlated major event.

**Core gap**: Correlation engine exists for detection but doesn't translate into coordinated ISP escalation or partner guidance ("this is ISP-side, don't touch anything").

---

## 5. Cross-Cutting Themes

| Theme | Manifestation |
|---|---|
| **Detection works, action doesn't** | V2.1 detects in 5-15 min. But P50 resolution for LOCAL is 61 min (day) to 431 min (night). |
| **Alert ≠ Intelligence** | Partner gets "X devices down" not "fiber cut at junction Y, call ISP NOC at number Z" |
| **Escalation is cosmetic** | T120/T240 reminders barely fire. No real consequence for ignoring alerts. |
| **Night-time is unaddressed** | 7x resolution penalty. No alternate routing, no tighter SLAs, no backup paths. |
| **Small pockets fester** | 43% of incidents are tiny+long. Too small to notice, too many to track manually. |
| **ISP faults look like partner faults** | No automated upstream correlation → wrong responder → wasted time. |

---

## 6. The Opportunity Sizing

### If we could halve resolution times:

| Metric | Current | Target | Customer Impact |
|---|---|---|---|
| P50 LOCAL (Day) | 61 min | ~30 min | 44,700+ devices/day get internet back 30 min faster |
| P50 LOCAL (Night) | 431 min | ~215 min | 50,000+ devices stop being dark for 7+ hours |
| P95 overall | 850-960 min | ~450 min | Long-tail outages cut from 16hr to 8hr |
| Active LOCAL incidents | 8,622 stuck | <2,000 | Chronic unresolved pockets cleared |

### The 24,000 daily incidents break down as:
- ~53% MICRO (self-resolve or too small to act on) — **not the target**
- ~43% LOCAL driven by TINY+LONG — **the biggest opportunity**
- ~0.4% MAJOR — **small count, high impact, needs ISP coordination**
- ~4% other LOCAL — **classic partner-fixable incidents**

**The #1 lever is making TINY+LONG incidents actionable for partners through root cause diagnosis and guided next steps.**

---

*This document is for problem exploration only. No solutions proposed.*
