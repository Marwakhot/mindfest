"""
StockPilot synthetic dataset — generator settings.

Values marked [DOC] come straight from "StockPilot — System Design Document v2".
Values marked [PROPOSED] are not in the doc; they are proposals awaiting review.
Edit anything here and re-run generate.py; the fixed seed makes output reproducible.
"""

SEED = 20261027  # [PROPOSED] any fixed integer

# ---------------------------------------------------------------- calendar
ANCHOR_DAY90 = "2026-10-27"  # [DECIDED] day 90 = planning day; day 1 = 2026-07-30, day 120 = 2026-11-26
HISTORY_DAYS = 90            # [DOC]
HORIZON_DAYS = 30            # [DOC]

LOCATIONS = ["Dubai", "Abu Dhabi", "Sharjah"]  # [DOC]

# ---------------------------------------------------------------- suppliers / routes
# [DOC] lead times 10-16 days, 6 past orders per supplier.
# [PROPOSED] 4 fictional suppliers (codes only, no real company names) and their mean lead time.
SUPPLIERS = {
    "SUP-CHG": {"name": "Charger supplier (fictional)",    "lead_mean": 13, "semiconductor": True},
    "SUP-PWB": {"name": "Power bank supplier (fictional)", "lead_mean": 14, "semiconductor": True},
    "SUP-CBL": {"name": "Cable supplier (fictional)",      "lead_mean": 11, "semiconductor": False},
    "SUP-PER": {"name": "Peripherals supplier (fictional)","lead_mean": 12, "semiconductor": False},
}
# [DOC] route R2 carries only CBL-01 and KBD-02 POs (case 9). [PROPOSED] everything else on R1.
ROUTES = {"R1": "Primary inbound route (fictional)", "R2": "Secondary inbound route (fictional)"}

# ---------------------------------------------------------------- SKU master
# [DOC] sku, name, tag S, category.  [DOC] CHG-01 margin 35, pack 10.  [DOC-derived] CBL-02 margin 5.5
#   (case 5: Sharjah->Abu Dhabi 40 units = 400 + 40*5.00 = AED 600 lane cost vs 40*5.5 = AED 220 margin).
# [PROPOSED] all other costs, prices, pack sizes, MOQs.
# components: SIG-001 matches "power_management_ic"; only CHG and PWB SKUs list it (=> semiconductor tag).
SKUS = [
    # sku,     name,                              category,      components,                                              cost, price, pack, moq, supplier,  route
    ("CHG-01", "USB-C 20W charger",               "Chargers",    ["power_management_ic", "usb_pd_controller", "housing"],  40.0,  75.0, 10, 100, "SUP-CHG", "R1"),
    ("CHG-02", "USB-C 65W charger",               "Chargers",    ["power_management_ic", "usb_pd_controller", "gan_transistor", "housing"], 85.0, 140.0, 10, 100, "SUP-CHG", "R1"),
    ("CHG-03", "Dual-port USB-A charger",         "Chargers",    ["power_management_ic", "housing"],                       25.0,  45.0, 10, 100, "SUP-CHG", "R1"),
    ("CHG-04", "Wireless charging pad",           "Chargers",    ["power_management_ic", "charging_coil", "housing"],      45.0,  80.0, 10, 100, "SUP-CHG", "R1"),
    ("CBL-01", "USB-C to USB-C cable, 1 m",       "Cables",      ["copper_wire", "usb_c_connector"],                       12.0,  22.0, 10, 200, "SUP-CBL", "R2"),
    ("CBL-02", "USB-A to Lightning cable, 1 m",   "Cables",      ["copper_wire", "usb_a_connector", "lightning_connector"], 9.5,  15.0, 10, 200, "SUP-CBL", "R1"),
    ("CBL-03", "USB-C braided cable, 2 m",        "Cables",      ["copper_wire", "usb_c_connector", "braided_jacket"],     18.0,  32.0, 10, 200, "SUP-CBL", "R1"),
    ("CBL-04", "HDMI cable, 2 m",                 "Cables",      ["copper_wire", "hdmi_connector"],                        14.0,  25.0, 10, 200, "SUP-CBL", "R1"),
    ("PWB-01", "Power bank, 10,000 mAh",          "Power banks", ["power_management_ic", "li_ion_cell", "housing"],        55.0,  95.0, 10, 100, "SUP-PWB", "R1"),
    ("PWB-02", "Power bank, 20,000 mAh",          "Power banks", ["power_management_ic", "li_ion_cell", "housing"],        85.0, 145.0, 10, 100, "SUP-PWB", "R1"),
    ("PWB-03", "Slim power bank, 5,000 mAh",      "Power banks", ["power_management_ic", "li_po_cell", "housing"],         45.0,  75.0, 10, 100, "SUP-PWB", "R1"),
    ("PWB-04", "Magnetic power bank, 5,000 mAh",  "Power banks", ["power_management_ic", "li_po_cell", "charging_coil", "magnet_ring"], 70.0, 120.0, 10, 100, "SUP-PWB", "R1"),
    ("MSE-01", "Wireless mouse",                  "Mice",        ["optical_sensor", "rf_receiver", "switches", "housing"], 30.0,  55.0, 10, 100, "SUP-PER", "R1"),
    ("MSE-02", "Wired optical mouse",             "Mice",        ["optical_sensor", "switches", "usb_cable", "housing"],   12.0,  22.0, 10, 100, "SUP-PER", "R1"),
    ("MSE-03", "Ergonomic mouse",                 "Mice",        ["optical_sensor", "rf_receiver", "switches", "housing"], 60.0, 105.0, 10, 100, "SUP-PER", "R1"),
    ("MSE-04", "Gaming mouse",                    "Mice",        ["optical_sensor", "switches", "usb_cable", "housing"],   90.0, 160.0, 10, 100, "SUP-PER", "R1"),
    ("KBD-01", "Wireless keyboard",               "Keyboards",   ["key_switches", "rf_receiver", "housing"],               55.0,  95.0,  5,  50, "SUP-PER", "R1"),
    ("KBD-02", "Wired keyboard",                  "Keyboards",   ["key_switches", "usb_cable", "housing"],                 25.0,  45.0,  5,  50, "SUP-PER", "R2"),
    ("KBD-03", "Keyboard and mouse combo",        "Keyboards",   ["key_switches", "optical_sensor", "rf_receiver", "housing"], 80.0, 135.0, 5, 50, "SUP-PER", "R1"),
    ("KBD-04", "Compact keyboard",                "Keyboards",   ["key_switches", "usb_cable", "housing"],                 65.0, 110.0,  5,  50, "SUP-PER", "R1"),
]
SEMICONDUCTOR_COMPONENT = "power_management_ic"

# ---------------------------------------------------------------- per SKU-location cells
# daily = true mean daily demand (Dubai, Abu Dhabi, Sharjah)        [PROPOSED except CHG-01 = DOC 200/350/150 per 30 days]
# Defaults when not overridden below:                                 [PROPOSED]
#   safety_stock = ceil(7 * daily / 5) * 5, min 5
#   available    = round(32 * daily / 5) * 5   (about 32 days of cover: above safety, below 45-day excess)
#   reserved     = 0
DAILY = {
    "CHG-01": (200/30, 350/30, 150/30),  # [DOC] flat demand
    "CHG-02": (3.0, 2.0, 1.5),
    "CHG-03": (5.0, 3.0, 3.0),
    "CHG-04": (2.0, 1.5, 1.0),
    "CBL-01": (8.0, 5.0, 4.0),
    "CBL-02": (4.0, 3.0, 3.0),
    "CBL-03": (5.0, 3.0, 2.0),
    "CBL-04": (3.0, 2.0, 1.5),
    "PWB-01": (5.0, 4.0, 3.0),
    "PWB-02": (2.0, 1.5, 1.0),
    "PWB-03": (2.0, 1.5, 1.5),
    "PWB-04": (1.5, 1.0, 1.0),
    "MSE-01": (4.0, 3.0, 2.0),
    "MSE-02": (4.0, 3.0, 2.0),
    "MSE-03": (1.5, 1.0, 0.5),
    "MSE-04": (1.5, 1.0, 0.7),
    "KBD-01": (3.0, 2.5, 1.5),
    "KBD-02": (3.0, 2.0, 2.0),
    "KBD-03": (1.5, 1.0, 1.0),
    "KBD-04": (0.5, 0.4, 0.3),           # sparse => Low confidence badge
}

# Overrides: (sku, location) -> dict(available=, reserved=, safety_stock=)
CELL_OVERRIDES = {
    # Case 1 / 10.4 worked example [DOC]: free 500 / 100 / 300, safety 60 / 60 / 40
    ("CHG-01", "Dubai"):     dict(available=520, reserved=20, safety_stock=60),
    ("CHG-01", "Abu Dhabi"): dict(available=100, reserved=0,  safety_stock=60),   # record says 100; true = 160 (case 12)
    ("CHG-01", "Sharjah"):   dict(available=300, reserved=0,  safety_stock=40),
    # Case 5: CBL-02 short in Abu Dhabi, only Sharjah has spare (~40 units)
    ("CBL-02", "Dubai"):     dict(available=150, safety_stock=30),
    ("CBL-02", "Abu Dhabi"): dict(available=30,  safety_stock=20),
    ("CBL-02", "Sharjah"):   dict(available=180, safety_stock=25),
    # Case 6: PWB-01 short at all three locations, no inbound
    ("PWB-01", "Dubai"):     dict(available=40, safety_stock=35),
    ("PWB-01", "Abu Dhabi"): dict(available=30, safety_stock=30),
    ("PWB-01", "Sharjah"):   dict(available=25, safety_stock=25),
    # Case 7: KBD-03 excess in Sharjah (~120 days of cover)
    ("KBD-03", "Sharjah"):   dict(available=120, safety_stock=10),
    # Case 4: KBD-04 aged batches in Dubai (also slow-moving)
    ("KBD-04", "Dubai"):     dict(available=60, safety_stock=5),
    # Some reserved stock elsewhere so Free != Available  [PROPOSED]
    ("CBL-01", "Dubai"):     dict(reserved=30),
    ("MSE-01", "Dubai"):     dict(reserved=10),
    ("KBD-01", "Abu Dhabi"): dict(reserved=10),
    ("CHG-02", "Dubai"):     dict(reserved=10),
    ("PWB-02", "Sharjah"):   dict(reserved=5),
}
CASE12_TRUE_AVAILABLE = 160  # [DOC] CHG-01 Abu Dhabi cycle count

# ---------------------------------------------------------------- demand noise / pattern  [PROPOSED]
DEMAND_NOISE_CV = 0.25          # negative-binomial style day-to-day noise (CHG-01 uses Poisson, flat)
DOW_MULTIPLIER = {              # mild weekly pattern for F7 to find (UAE weekend Sat/Sun); CHG-01 exempt
    "Mon": 1.08, "Tue": 1.08, "Wed": 1.08, "Thu": 1.08, "Fri": 1.08, "Sat": 0.80, "Sun": 0.80,
}

# ---------------------------------------------------------------- injected history defects
SHARJAH_MISSING_DAYS = (61, 67)                 # [DOC] case 3, all SKUs, rows absent from sales_daily
STOCKOUTS = [                                   # [DOC] case 2 SKUs; [PROPOSED] locations and days (inside 28-day base window)
    dict(sku="CBL-03", location="Dubai",     start=74, end=81),
    dict(sku="MSE-02", location="Abu Dhabi", start=66, end=72),
]

# ---------------------------------------------------------------- stock age (batches sum to available)
# Default [PROPOSED]: split into 1-3 batches received within the last ~45 days.
# Specific batches (days are dataset day numbers; negative = before day 1):
BATCH_OVERRIDES = {
    ("KBD-04", "Dubai"):   [(30, -20), (20, -5), (10, 40)],   # [DOC] case 4: 110 & 95 days old = Aged; 50 days = fresh
    ("KBD-03", "Sharjah"): [(70, 15), (50, 60)],              # [PROPOSED] 75 days old = Ageing tier
    ("CBL-04", "Dubai"):   [(40, 25), (55, 70)],              # [PROPOSED] 65 days old = Ageing tier
    ("MSE-04", "Sharjah"): [(10, 18), (10, 75)],              # [PROPOSED] 72 days old = Ageing tier
}

# ---------------------------------------------------------------- inbound orders (eta = plan day 1..30)
INBOUND = [
    # po_id,   sku,      location,    qty, eta, supplier,  route, status,        changeable, expedite_days, expedite_cost_aed
    ("PO-101", "CHG-02", "Dubai",     100, 15, "SUP-CHG", "R1", "Confirmed",   False, None, None),   # SIG-001 affected
    ("PO-102", "PWB-02", "Dubai",     100, 12, "SUP-PWB", "R1", "Confirmed",   False, None, None),   # SIG-001 affected
    ("PO-103", "CHG-03", "Sharjah",   100, 18, "SUP-CHG", "R1", "Confirmed",   False, None, None),   # SIG-001 affected
    ("PO-104", "CHG-01", "Abu Dhabi", 100, 20, "SUP-CHG", "R1", "Confirmed",   False, None, None),   # [DOC] worked example
    ("PO-105", "CBL-01", "Dubai",     200, 10, "SUP-CBL", "R2", "Confirmed",   False, None, None),   # [DOC] case 9, SIG-002
    ("PO-106", "KBD-02", "Abu Dhabi",  50, 14, "SUP-PER", "R2", "Confirmed",   True,  4,    200.0),  # [DOC] case 9, SIG-002
    ("PO-107", "KBD-03", "Sharjah",    50,  9, "SUP-PER", "R1", "Confirmed",   True,  3,    150.0),  # [DOC] case 7 changeable
    ("PO-108", "MSE-01", "Sharjah",   200,  6, "SUP-PER", "R1", "Unconfirmed", False, None, None),   # [DOC] case 11, 200 units
    ("PO-109", "CBL-04", "Abu Dhabi", 200, 16, "SUP-CBL", "R1", "Confirmed",   False, None, None),   # cable NOT on R2 (unaffected)
]

# ---------------------------------------------------------------- holding costs  [PROPOSED]
HOLDING_COST_PER_UNIT_DAY = {"Dubai": 0.04, "Abu Dhabi": 0.035, "Sharjah": 0.03}

# ---------------------------------------------------------------- transfer lanes [DOC] (both ways)
LANES = [
    ("Dubai", "Sharjah", 100, 0.50, 1),
    ("Dubai", "Abu Dhabi", 250, 1.50, 1),
    ("Sharjah", "Abu Dhabi", 400, 5.00, 2),
]

# ---------------------------------------------------------------- signals
# start_day / end_day are plan days (1 = day 91).
SIGNALS = [
    # [DOC] SIG-001 — ships as an inbox drop file, NOT in the seed signals.csv
    dict(signal_id="SIG-001", date_day=90, source="Supplier notice (fictional)", type="supply",
         components=["power_management_ic"], routes=[], skus=[], locations=[],
         delay=(3, 7, 12), uplift=None, start_day=1, end_day=30, status="Unverified", seed=False),
    # [DOC] SIG-002
    dict(signal_id="SIG-002", date_day=88, source="Freight forwarder notice (fictional)", type="supply",
         components=[], routes=["R2"], skus=[], locations=[],
         delay=(3, 7, 12), uplift=None, start_day=1, end_day=30, status="Unverified", seed=True),
    # [DOC] SIG-003 skus/location; [PROPOSED] uplift range (% of base demand) and days
    dict(signal_id="SIG-003", date_day=87, source="Sales team note (fictional)", type="demand",
         components=[], routes=[], skus=["MSE-01", "KBD-01"], locations=["Abu Dhabi"],
         delay=None, uplift=(0.30, 0.50, 0.80), start_day=8, end_day=21, status="Unverified", seed=True),
]
# [PROPOSED] all three signals actually happen; realised delay/uplift drawn (triangular) inside the range.
SIGNAL_HAPPENED = {"SIG-001": True, "SIG-002": True, "SIG-003": True}
# [PROPOSED] the Unconfirmed PO-108 does arrive (at its ETA) in the hidden days; written only to the answer key.
UNCONFIRMED_PO_ARRIVES = {"PO-108": True}

# ---------------------------------------------------------------- planning parameters [DOC 10.5]
PLANNING_PARAMETERS = [
    ("horizon_days", "30", "days", "Planning horizon"),
    ("max_cover_days", "45", "days", "Excess flag, PO cap, guardrail"),
    ("ageing_days", "60", "days", "Ageing tier, ageing cost"),
    ("aged_days", "90", "days", "Aged tier, aged-stock metric"),
    ("ageing_cost_pct_per_30_days", "1.5", "% of unit cost", "Net benefit"),
    ("review_buffer_days", "2", "days", "Order-by date"),
    ("review_period_days", "7", "days", "Reorder quantity"),
    ("base_rate_window_days", "28", "days", "Forecast, baseline"),
    ("low_confidence_band_widening", "25", "%", "Forecast band"),
    ("confidence_thresholds", "10;25;20", "% days imputed or OOS; % ; units sold", "Confidence badge: 10% and 25% of days imputed or out of stock; 20 units sold"),
    ("urgency_soon_days", "5", "days", "Urgency badge"),
    ("freshness_days", "2", "days", "Freshness banner"),
    ("baseline_reorder_days", "30", "days", "Baseline replay"),
    ("backtest_step_days", "15", "days", "Forecast backtests and bands"),
]

# ---------------------------------------------------------------- stock counts [PROPOSED]
# Case 12 [DOC]: CHG-01 Abu Dhabi record 100, later count 160.
# Plus ~10 routine counts on days 80-90 that MATCH the records (so only case 12 is flagged).
ROUTINE_COUNTS = 10
COUNTER_IDS = {"Dubai": "WH-DXB-01", "Abu Dhabi": "WH-AUH-01", "Sharjah": "WH-SHJ-01"}  # staff IDs, no names
