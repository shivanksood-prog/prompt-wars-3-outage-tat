import os

METABASE_URL = "https://metabase.wiom.in"
METABASE_API_KEY = os.environ.get("METABASE_API_KEY", "")
METABASE_DB_ID = 113

# Classification thresholds (based on physical network topology)
# Topology: Customer → Secondary Splitter (1:8, ~100m) → Primary Splitter (1:64, 2-3km) → OLT (1:256) → ISP
# WIOM visibility ~60% of total connections on a splitter/OLT

# Geo thresholds
GEO_SECONDARY_SPLITTER_DEG = 0.001   # ~100m — secondary splitter radius
GEO_PRIMARY_SPLITTER_DEG = 0.03      # ~3km — primary splitter radius

# Device count thresholds (visible WIOM devices)
CUSTOMER_MAX_DEVICES = 3              # 1-3 = customer-level
SECONDARY_SPLITTER_MAX_DEVICES = 8    # Up to 8 = secondary splitter (physical limit: 8)
PRIMARY_SPLITTER_MAX_DEVICES = 64     # Up to 64 = primary splitter (physical: 8×8=64)
# Above 64 = OLT/backbone level (physical: 64×4=256 per OLT)

# Concurrent incident thresholds (other incidents on same partner)
CONCURRENT_ESCALATE_TO_OLT = 5       # 5+ concurrent incidents = likely OLT/backbone

# Refresh
REFRESH_INTERVAL_SEC = 300   # 5 min
DEFAULT_LOOKBACK_HOURS = 6
