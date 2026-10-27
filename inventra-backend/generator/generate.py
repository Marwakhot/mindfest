"""
StockPilot synthetic dataset generator.

    python3 generate.py            # writes ../data/ (identical output every run)

Days 1-90 are visible history; days 91-120 are hidden and written only to data/judges/answer_key/.
List-valued fields (components, routes, skus, locations) are ';'-separated.
"""
import hashlib
import math
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import config as c

ROOT = Path(__file__).resolve().parent.parent / "data"
SEED_DIR = ROOT / "seed"
INBOX_DIR = ROOT / "inbox_drops"
JUDGE_DIR = ROOT / "judges"
KEY_DIR = JUDGE_DIR / "answer_key"

rng = np.random.default_rng(c.SEED)
DAY90 = date.fromisoformat(c.ANCHOR_DAY90)
DAY1 = DAY90 - timedelta(days=c.HISTORY_DAYS - 1)
LAST_DAY = c.HISTORY_DAYS + c.HORIZON_DAYS
SNAPSHOT = datetime.combine(DAY90, datetime.min.time()).replace(hour=6).isoformat()
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def day_date(n):
    return DAY1 + timedelta(days=n - 1)


def iso(n):
    return day_date(n).isoformat()


def join(xs):
    return ";".join(xs)


def write(df, folder, name, as_of=SNAPSHOT):
    folder.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    for col in df.columns:  # keep whole numbers as integers when a column has blanks
        if df[col].dtype == float and (df[col].dropna() % 1 == 0).all() and df[col].isna().any():
            df[col] = df[col].astype("Int64")
    df["source_file"] = name
    df["loaded_at"] = SNAPSHOT
    if "as_of" not in df.columns:
        df["as_of"] = as_of
    df.to_csv(folder / name, index=False)
    return df


SKU = {s[0]: dict(name=s[1], category=s[2], components=s[3], unit_cost=s[4], unit_price=s[5],
                  pack_size=s[6], moq=s[7], supplier=s[8], inbound_route=s[9]) for s in c.SKUS}


# ---------------------------------------------------------------- cells
def cell(sku, loc):
    d = c.DAILY[sku][c.LOCATIONS.index(loc)]
    o = c.CELL_OVERRIDES.get((sku, loc), {})
    return dict(
        daily=d,
        safety_stock=o.get("safety_stock", max(5, math.ceil(7 * d / 5) * 5)),
        available=o.get("available", int(round(32 * d / 5) * 5)),
        reserved=o.get("reserved", 0),
    )


CELLS = {(s, l): cell(s, l) for s in SKU for l in c.LOCATIONS}


# ---------------------------------------------------------------- true demand, days 1..120
def draw_demand(sku, loc, day, uplift=0.0):
    mean = CELLS[(sku, loc)]["daily"] * (1 + uplift)
    if sku == "CHG-01":                       # [DOC] flat daily demand
        return int(rng.poisson(mean))
    mean *= c.DOW_MULTIPLIER[DOW[day_date(day).weekday()]]
    k = 1 / c.DEMAND_NOISE_CV ** 2          # gamma-Poisson: CV of the rate = DEMAND_NOISE_CV
    return int(rng.poisson(rng.gamma(k, mean / k)))


# realised signal outcomes (answer key only)
sig_out = {}
for s in c.SIGNALS:
    happened = c.SIGNAL_HAPPENED[s["signal_id"]]
    delay = uplift = None
    if happened and s["delay"]:
        lo, mode, hi = s["delay"]
        delay = int(round(rng.triangular(lo, mode, hi)))
    if happened and s["uplift"]:
        lo, mode, hi = s["uplift"]
        uplift = round(float(rng.triangular(lo, mode, hi)), 3)
    sig_out[s["signal_id"]] = dict(happened=happened, delay=delay, uplift=uplift)

sig003 = next(s for s in c.SIGNALS if s["signal_id"] == "SIG-003")

true_demand = {}
for sku in SKU:
    for loc in c.LOCATIONS:
        for day in range(1, LAST_DAY + 1):
            up = 0.0
            plan_day = day - c.HISTORY_DAYS
            if (sig_out["SIG-003"]["happened"] and sku in sig003["skus"] and loc in sig003["locations"]
                    and sig003["start_day"] <= plan_day <= sig003["end_day"]):
                up = sig_out["SIG-003"]["uplift"]
            true_demand[(sku, loc, day)] = draw_demand(sku, loc, day, up)

# ---------------------------------------------------------------- sales_daily (days 1..90)
stockout = {(x["sku"], x["location"]): (x["start"], x["end"]) for x in c.STOCKOUTS}
miss_lo, miss_hi = c.SHARJAH_MISSING_DAYS
rows = []
for sku in SKU:
    for loc in c.LOCATIONS:
        for day in range(1, c.HISTORY_DAYS + 1):
            if loc == "Sharjah" and miss_lo <= day <= miss_hi:
                continue                                         # case 3: rows absent
            dem = true_demand[(sku, loc, day)]
            units, flag = dem, 1
            so = stockout.get((sku, loc))
            if so and so[0] <= day <= so[1]:                     # case 2: censored by zero free stock
                flag = 0
                units = int(dem * 0.4) if day == so[0] else 0   # sells out part-way through day 1
            rows.append(dict(date=iso(day), sku=sku, location=loc, units=units, in_stock_flag=flag))
sales = pd.DataFrame(rows)

# ---------------------------------------------------------------- static tables
sku_master = pd.DataFrame([dict(sku=k, name=v["name"], category=v["category"], components=join(v["components"]),
                                unit_cost=v["unit_cost"], unit_price=v["unit_price"], pack_size=v["pack_size"],
                                moq=v["moq"], supplier=v["supplier"], inbound_route=v["inbound_route"])
                           for k, v in SKU.items()])

inventory = pd.DataFrame([dict(sku=s, location=l, available=v["available"], reserved=v["reserved"], as_of=SNAPSHOT)
                          for (s, l), v in CELLS.items()])

safety = pd.DataFrame([dict(sku=s, location=l, units=v["safety_stock"]) for (s, l), v in CELLS.items()])

# stock_age: batches sum to available
age_rows = []
for (s, l), v in CELLS.items():
    batches = c.BATCH_OVERRIDES.get((s, l))
    if batches is None:
        n = int(rng.integers(1, 4)) if v["available"] >= 30 else 1
        days = sorted(rng.choice(np.arange(50, 89), size=n, replace=False))   # 2-40 days old at day 90
        if n == 1:
            parts = [v["available"]]
        else:
            w = rng.dirichlet(np.ones(n))
            parts = [int(x) for x in np.floor(w * v["available"])]
            parts[-1] += v["available"] - sum(parts)
        batches = list(zip(parts, days))
    assert sum(u for u, _ in batches) == v["available"], (s, l)
    for i, (units, rday) in enumerate(batches, 1):
        age_rows.append(dict(sku=s, location=l, batch_id=f"B-{s}-{l[:3].upper()}-{i:02d}",
                             units=units, received_date=iso(int(rday))))
stock_age = pd.DataFrame(age_rows)

inbound = pd.DataFrame([dict(po_id=p[0], sku=p[1], location=p[2], qty=p[3], eta=iso(c.HISTORY_DAYS + p[4]),
                             supplier=p[5], route=p[6], status=p[7], changeable=p[8],
                             expedite_days=p[9], expedite_cost_aed=p[10]) for p in c.INBOUND])

# supplier lead history: 6 orders per supplier, 10-16 days, mean = lead_mean
lead_rows = []
for sup, info in c.SUPPLIERS.items():
    skus = [k for k, v in SKU.items() if v["supplier"] == sup]
    offsets = rng.permutation([-3, -2, -1, 1, 2, 3])
    order_days = sorted(rng.choice(np.arange(1, 74), size=6, replace=False))
    for od, off in zip(order_days, offsets):
        lead = int(np.clip(info["lead_mean"] + off, 10, 16))
        lead_rows.append(dict(supplier=sup, sku=str(rng.choice(skus)), order_date=iso(int(od)),
                              received_date=iso(int(od) + lead)))
lead_hist = pd.DataFrame(lead_rows)

lanes = pd.DataFrame([dict(from_loc=a, to_loc=b, fixed_cost_aed=f, cost_per_unit_aed=u, lead_days=d)
                      for a, b0, f, u, d in c.LANES for a, b in [(a, b0), (b0, a)]])

holding = pd.DataFrame([dict(location=l, cost_per_unit_day_aed=v) for l, v in c.HOLDING_COST_PER_UNIT_DAY.items()])


def signal_row(s):
    d = s["delay"] or (None, None, None)
    u = s["uplift"] or (None, None, None)
    return dict(signal_id=s["signal_id"], date=iso(s["date_day"]), source=s["source"], type=s["type"],
                components=join(s["components"]), routes=join(s["routes"]), skus=join(s["skus"]),
                locations=join(s["locations"]), delay_min=d[0], delay_likely=d[1], delay_max=d[2],
                uplift_min=u[0], uplift_likely=u[1], uplift_max=u[2],
                start_day=s["start_day"], end_day=s["end_day"], status=s["status"])


signals_seed = pd.DataFrame([signal_row(s) for s in c.SIGNALS if s["seed"]])
signals_drop = pd.DataFrame([signal_row(s) for s in c.SIGNALS if not s["seed"]])

params = pd.DataFrame([dict(name=n, value=v, unit=u, description=d, changed_by="system (seed)",
                            changed_at=SNAPSHOT) for n, v, u, d in c.PLANNING_PARAMETERS])

# stock counts: routine counts match the record; case 12 count disagrees, taken later on day 90
count_rows = []
pool = [k for k in CELLS if k != ("CHG-01", "Abu Dhabi")]
for i in rng.choice(len(pool), size=c.ROUTINE_COUNTS, replace=False):
    s, l = pool[i]
    count_rows.append(dict(sku=s, location=l, counted_units=CELLS[(s, l)]["available"],
                           count_date=f"{DAY90.isoformat()}T05:30:00", counted_by=c.COUNTER_IDS[l]))
count_rows.append(dict(sku="CHG-01", location="Abu Dhabi", counted_units=c.CASE12_TRUE_AVAILABLE,
                       count_date=f"{DAY90.isoformat()}T15:30:00", counted_by=c.COUNTER_IDS["Abu Dhabi"]))
counts = pd.DataFrame(count_rows)

# ---------------------------------------------------------------- write seed
if ROOT.exists():
    shutil.rmtree(ROOT)
for df, name in [(sku_master, "sku_master.csv"), (sales, "sales_daily.csv"), (inventory, "inventory.csv"),
                 (counts, "stock_counts.csv"), (stock_age, "stock_age.csv"), (safety, "safety_stock.csv"),
                 (inbound, "inbound_orders.csv"), (lead_hist, "supplier_lead_history.csv"),
                 (lanes, "transfer_lanes.csv"), (holding, "holding_costs.csv"), (signals_seed, "signals.csv"),
                 (params, "planning_parameters.csv")]:
    write(df, SEED_DIR, name)

RUNTIME = {
    "corrections_log.csv": ["record_id", "field", "old_value", "new_value", "user", "timestamp", "reason", "evidence_record_id"],
    "inbox_events.csv": ["event_id", "file_name", "file_hash", "received_at", "target_table", "status", "rows", "errors", "what_changed"],
    "scenario_trees.csv": ["tree_id", "sku", "location", "assumptions", "created_at"],
    "scenario_nodes.csv": ["node_id", "tree_id", "parent_id", "decision", "source", "question_text", "results", "robustness", "input_hash"],
    "decisions.csv": ["decision_id", "sku", "location", "node_id", "options", "chosen", "approver", "timestamp", "input_hash", "status", "reason"],
}
for name, cols in RUNTIME.items():
    pd.DataFrame(columns=cols + ["source_file", "loaded_at", "as_of"]).to_csv(SEED_DIR / name, index=False)

# ---------------------------------------------------------------- inbox drops
write(signals_drop, INBOX_DIR, "signals_SIG-001.csv")
shutil.copyfile(INBOX_DIR / "signals_SIG-001.csv", INBOX_DIR / "signals_SIG-001_resent.csv")  # same hash -> Duplicate
bad = pd.DataFrame([
    dict(sku="CHG-09", location="Dubai", available=50, reserved=0, as_of=SNAPSHOT),       # unknown SKU
    dict(sku="CBL-01", location="Ajman", available=40, reserved=0, as_of=SNAPSHOT),       # unknown location
    dict(sku="MSE-03", location="Dubai", available=-12, reserved=0, as_of=SNAPSHOT),      # negative stock
    dict(sku="KBD-02", location="Sharjah", available=20, reserved=35, as_of=SNAPSHOT),    # reserved above available
    dict(sku="KBD-02", location="Sharjah", available=20, reserved=35, as_of=SNAPSHOT),    # duplicate row
])
write(bad, INBOX_DIR, "inventory_update_bad.csv")

# ---------------------------------------------------------------- judges: checklist
checklist = pd.DataFrame([
    (1, "CHG-01 imbalance across all three locations (worked example 10.4)",
     "Dubai-only before the disruption; split after it; full Dubai transfer rejected",
     "Top option for CHG-01 Abu Dhabi before and after SIG-001 is loaded; full-need Dubai option shown as rejected/Harmful"),
    (2, "CBL-03 and MSE-02 have stockout periods",
     "Corrected forecast above observed sales, with out-of-stock days shown",
     "F2 shows corrected total > observed total and lists the in_stock_flag=0 days for CBL-03 Dubai and MSE-02 Abu Dhabi"),
    (3, "Sharjah missing days 61 to 67 for all SKUs",
     "Gap flagged, days imputed as Estimate, confidence lowered",
     "Validation report lists the 7 missing Sharjah days; imputed days tagged Estimate; Sharjah confidence badges lower"),
    (4, "KBD-04 batches 90+ days old in Dubai",
     "Flagged Aged; oldest batches picked first in any transfer",
     "N1 cell KBD-04 Dubai is Aged; any transfer leg from it picks the oldest batch_id first"),
    (5, "CBL-02 short in Abu Dhabi; only Sharjah has spare",
     "Transfer rejected: lane cost exceeds margin protected (about AED 600 vs 220)",
     "C3 lists the Sharjah->Abu Dhabi CBL-02 transfer as rejected on the Economic check with both AED values"),
    (6, "PWB-01 short at all three locations",
     "Reorder; order-by date includes the SIG-001 delay; Draft PO",
     "Recommendation is a reorder; order-by uses supplier mean lead + SIG-001 likely delay; Draft PO available"),
    (7, "KBD-03 excess in Sharjah with a changeable inbound PO",
     "Reduce or delay the inbound PO",
     "Recommendation for KBD-03 Sharjah is to reduce or delay PO-107"),
    (8, "SIG-001 semiconductor shortage",
     "Only CHG and PWB SKUs affected",
     "X2 for SIG-001 highlights only CHG-* and PWB-*; all others greyed with a reason"),
    (9, "SIG-002 route delay on R2",
     "Only the CBL-01 and KBD-02 POs on R2 affected",
     "X2 for SIG-002 highlights only PO-105 and PO-106; other POs shown 'not on route R2'"),
    (10, "SIG-003 Abu Dhabi tender",
     "Uplift on MSE-01 and KBD-01 at Abu Dhabi only",
     "F4 shows with/without effect only for MSE-01 and KBD-01 at Abu Dhabi, tagged Assumption"),
    (11, "Unconfirmed 200-unit MSE-01 PO into Sharjah flips the best way to meet the tender",
     "One targeted question",
     "S8 asks one question about PO-108 confirmation when it flips the top option"),
    (12, "CHG-01 Abu Dhabi stock record says 100; a later cycle count says 160",
     "Validation flags the mismatch; planner corrects with the count as evidence; approved decision turns Stale",
     "D2 flags the mismatch; correction cites the stock_counts row; previously approved CHG-01 decision status = Stale"),
], columns=["case_id", "description", "expected_action", "how_checked"])
write(checklist, JUDGE_DIR, "evaluation_checklist_v1.csv")

# ---------------------------------------------------------------- answer key
td = pd.DataFrame([dict(date=iso(d), day=d, plan_day=d - c.HISTORY_DAYS, sku=s, location=l,
                        true_demand=true_demand[(s, l, d)])
                   for s in SKU for l in c.LOCATIONS for d in range(c.HISTORY_DAYS + 1, LAST_DAY + 1)])
write(td, KEY_DIR, "true_demand.csv")


def realised_eta(p):
    po_id, sku, _, _, eta, _, route, status = p[:8]
    if status == "Unconfirmed" and not c.UNCONFIRMED_PO_ARRIVES.get(po_id, False):
        return None
    delay = 0
    if sig_out["SIG-001"]["happened"] and c.SEMICONDUCTOR_COMPONENT in SKU[sku]["components"]:
        delay += sig_out["SIG-001"]["delay"]
    if sig_out["SIG-002"]["happened"] and route in ["R2"]:
        delay += sig_out["SIG-002"]["delay"]
    return eta + delay


po_rows, arrivals = [], {}
for p in c.INBOUND:
    r = realised_eta(p)
    po_rows.append(dict(po_id=p[0], sku=p[1], location=p[2], qty=p[3], planned_eta_plan_day=p[4],
                        arrives=r is not None, realised_eta_plan_day=r,
                        realised_eta=iso(c.HISTORY_DAYS + r) if r and r <= c.HORIZON_DAYS else None))
    if r is not None:
        arrivals.setdefault((p[1], p[2], r), 0)
        arrivals[(p[1], p[2], r)] += p[3]
write(pd.DataFrame(po_rows), KEY_DIR, "inbound_outcomes.csv")

# true stock, Do-nothing path, days 91..120 (true starting stock: CHG-01 Abu Dhabi = 160)
ts_rows, start_rows = [], []
for (s, l), v in CELLS.items():
    avail = c.CASE12_TRUE_AVAILABLE if (s, l) == ("CHG-01", "Abu Dhabi") else v["available"]
    free = avail - v["reserved"]
    start_rows.append(dict(sku=s, location=l, recorded_available=v["available"], true_available=avail,
                           reserved=v["reserved"], true_free=free))
    for t in range(1, c.HORIZON_DAYS + 1):
        free += arrivals.get((s, l, t), 0)
        dem = true_demand[(s, l, c.HISTORY_DAYS + t)]
        sold = min(dem, free)
        free -= sold
        ts_rows.append(dict(date=iso(c.HISTORY_DAYS + t), day=c.HISTORY_DAYS + t, plan_day=t, sku=s, location=l,
                            inbound_received=arrivals.get((s, l, t), 0), true_demand=dem, units_sold=sold,
                            lost_sales=dem - sold, end_free_stock=free, reserved=v["reserved"]))
write(pd.DataFrame(start_rows), KEY_DIR, "true_stock_day90.csv")
write(pd.DataFrame(ts_rows), KEY_DIR, "true_stock_do_nothing.csv")

write(pd.DataFrame([dict(signal_id=k, happened=v["happened"], realised_delay_days=v["delay"],
                         realised_uplift=v["uplift"]) for k, v in sig_out.items()]), KEY_DIR, "signal_outcomes.csv")
write(checklist[["case_id", "description", "expected_action"]], KEY_DIR, "expected_actions.csv")

# ---------------------------------------------------------------- summary
print(f"Dates: day 1 = {DAY1}, day 90 = {DAY90}, day 120 = {day_date(LAST_DAY)}")
print(f"Units available at day 90: {inventory.available.sum()}  (free {(inventory.available - inventory.reserved).sum()})")
print(f"sales_daily rows: {len(sales)} (expected {20*3*90 - 20*7})")
print("Signal outcomes:", sig_out)
for f in sorted(ROOT.rglob("*.csv")):
    h = hashlib.sha256(f.read_bytes()).hexdigest()[:10]
    print(f"  {f.relative_to(ROOT)}  rows={sum(1 for _ in open(f)) - 1}  sha={h}")
