"""Demand forecast (design doc section 7).

Stockout-corrected recent demand, an optional day-of-week pattern kept only when it
lowers backtest error, matched demand signals, and P10 / P90 bands from pooled
backtest error ratios per category.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import signals as sigmod
from .state import State

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MIN_INSTOCK_DAYS = 7  # below this many in-stock days in the window, the in-stock rate uses all prior history


@dataclass
class History:
    observed: np.ndarray   # units per day 1..90, NaN when the day is missing
    in_stock: np.ndarray   # 1 / 0, NaN when missing
    missing: np.ndarray    # bool
    oos: np.ndarray        # bool


@dataclass
class CellForecast:
    sku: str
    location: str
    base: np.ndarray            # P50 daily rate without signals, plan days 1..H
    m10: float
    m90: float
    uplift: Dict[str, List[float]]  # level -> per-day uplift
    signal_ids: List[str]
    confidence: str
    confidence_detail: Dict[str, Any]
    observed_total: float
    corrected_total: float
    oos_days: List[int]
    imputed_days: List[int]
    corrected_series: np.ndarray
    history: History
    weekly_pattern_used: bool
    extras: Dict[str, Any] = field(default_factory=dict)

    def daily(self, pct: str = "P50", level: str = "likely", with_signals: bool = True) -> np.ndarray:
        up = np.array(self.uplift.get(level, [0.0] * len(self.base))) if with_signals else np.zeros(len(self.base))
        if pct == "P10":
            return self.base * self.m10                 # demand signals change P50 and P90 only (section 8)
        if pct == "P90":
            return self.base * (1 + up) * self.m90
        return self.base * (1 + up)

    def total(self, pct: str = "P50", level: str = "likely", with_signals: bool = True) -> float:
        return float(self.daily(pct, level, with_signals).sum())


# ---------------------------------------------------------------------- history
def history(state: State, sku: str, loc: str) -> History:
    n = state.history_days
    obs = np.full(n, np.nan)
    ins = np.full(n, np.nan)
    rows = state.sales[(state.sales["sku"] == sku) & (state.sales["location"] == loc)]
    for d, u, f in zip(rows["day"], rows["units"], rows["in_stock_flag"]):
        if 1 <= d <= n:
            obs[d - 1] = u
            ins[d - 1] = f
    missing = np.isnan(obs)
    oos = (~missing) & (ins == 0)
    return History(obs, ins, missing, oos)


def instock_rate(h: History, end: int, window: int) -> float:
    """Mean of observed sales on in-stock days in the window ending at `end` (1-based, inclusive)."""
    lo = max(0, end - window)
    sl = slice(lo, end)
    good = (~h.missing[sl]) & (~h.oos[sl])
    if good.sum() >= MIN_INSTOCK_DAYS:
        return float(h.observed[sl][good].mean())
    good_all = (~h.missing[:end]) & (~h.oos[:end])
    return float(h.observed[:end][good_all].mean()) if good_all.any() else 0.0


def corrected(h: History, end: int, window: int, correct: bool = True) -> np.ndarray:
    """Corrected series for days 1..end: OOS days and missing days replaced by the in-stock rate."""
    rate = instock_rate(h, end, window)
    out = h.observed[:end].copy()
    if correct:
        out[h.oos[:end]] = np.maximum(out[h.oos[:end]], rate)
        out[h.missing[:end]] = rate
    return out


def weighted_base(series: np.ndarray, end: int, window: int, skip_nan: bool = False) -> float:
    """Weighted average of the last `window` days; recent weeks weigh more (weights 1, 2, 3, 4 by week)."""
    lo = max(0, end - window)
    vals = series[lo:end]
    idx = np.arange(len(vals))
    w = (idx // 7 + 1).astype(float)
    if skip_nan:
        ok = ~np.isnan(vals)
        vals, w = vals[ok], w[ok]
    return float((vals * w).sum() / w.sum()) if w.sum() else 0.0


def weekday_of(state: State, day: int) -> int:
    return (state.day1.toordinal() + day - 1 - 1) % 7  # date.weekday(): Monday = 0


def dow_pattern(state: State, series: Dict[Tuple[str, str], np.ndarray], end: int) -> np.ndarray:
    """Pooled day-of-week multipliers from corrected history up to `end`."""
    sums = np.zeros(7)
    cnt = np.zeros(7)
    for arr in series.values():
        a = arr[:end]
        m = np.nanmean(a)
        if not m or np.isnan(m):
            continue
        for i, v in enumerate(a):
            if not np.isnan(v):
                wd = weekday_of(state, i + 1)
                sums[wd] += v / m
                cnt[wd] += 1
    mult = np.where(cnt > 0, sums / np.maximum(cnt, 1), 1.0)
    return mult / mult.mean()


# ---------------------------------------------------------------------- backtests
def _origins(state: State) -> List[int]:
    H, W, step = state.horizon, state.params["base_rate_window_days"], state.params["backtest_step_days"]
    o = state.history_days - H
    out = []
    while o >= W:
        out.append(o)
        o -= step
    return sorted(out)


def _window_forecast(state: State, h: History, origin: int, mode: str, dow: Optional[np.ndarray]) -> np.ndarray:
    H, W = state.horizon, state.params["base_rate_window_days"]
    if mode == "baseline":  # recent average of observed sales, uncorrected
        lo = max(0, origin - W)
        base = float(np.nanmean(h.observed[lo:origin])) if (~h.missing[lo:origin]).any() else 0.0
    elif mode == "uncorrected":
        base = weighted_base(h.observed, origin, W, skip_nan=True)
    else:
        base = weighted_base(corrected(h, origin, W), origin, W)
    f = np.full(H, base)
    if dow is not None:
        f = f * np.array([dow[weekday_of(state, origin + t)] for t in range(1, H + 1)])
    return f


def backtest(state: State, histories: Dict[Tuple[str, str], History]) -> Dict[str, Any]:
    """Rolling-origin backtests. Errors are measured on in-stock, non-missing days only."""
    H = state.horizon
    origins = _origins(state)
    modes = ["baseline", "uncorrected", "corrected", "corrected_dow"]
    abs_err = {m: 0.0 for m in modes}
    signed = {m: 0.0 for m in modes}
    actual_sum = 0.0
    ratios: Dict[str, Dict[str, List[float]]] = {m: {} for m in modes}
    W = state.params["base_rate_window_days"]
    dows = {}
    for o in origins:
        series = {k: corrected(h, o, W) for k, h in histories.items()}
        dows[o] = dow_pattern(state, series, o)
    for (sku, loc), h in histories.items():
        cat = state.skus[sku]["category"]
        for o in origins:
            win = slice(o, min(o + H, state.history_days))
            valid = (~h.missing[win]) & (~h.oos[win])
            actual = h.observed[win][valid]
            if not valid.any():
                continue
            first = True
            for m in modes:
                f = _window_forecast(state, h, o, "corrected" if m == "corrected_dow" else m,
                                     dows[o] if m == "corrected_dow" else None)[: win.stop - win.start][valid]
                abs_err[m] += float(np.abs(actual - f).sum())
                signed[m] += float((f - actual).sum())
                if f.sum() > 0:
                    ratios[m].setdefault(cat, []).append(float(actual.sum() / f.sum()))
                if first:
                    actual_sum += float(actual.sum())
                    first = False
    wape = {m: (abs_err[m] / actual_sum if actual_sum else None) for m in modes}
    bias = {m: (signed[m] / actual_sum if actual_sum else None) for m in modes}
    return {"origins": origins, "wape": wape, "bias": bias, "ratios": ratios}


def band_multipliers(ratios: Dict[str, List[float]]) -> Dict[str, Tuple[float, float, int]]:
    out = {}
    for cat, rs in ratios.items():
        arr = np.array(rs)
        out[cat] = (float(np.percentile(arr, 10)), float(np.percentile(arr, 90)), len(rs))
    return out


# ---------------------------------------------------------------------- confidence
def confidence(state: State, h: History) -> Tuple[str, Dict[str, Any]]:
    W = state.params["base_rate_window_days"]
    th = state.params["confidence_thresholds"]
    sl = slice(state.history_days - W, state.history_days)
    bad = int(h.missing[sl].sum() + h.oos[sl].sum())
    share = 100.0 * bad / W
    units = float(np.nansum(h.observed[sl]))
    if share >= th["low_pct"] or units < th["min_units"]:
        badge = "Low"
    elif share >= th["medium_pct"]:
        badge = "Medium"
    else:
        badge = "High"
    return badge, {"window_days": W, "imputed_or_oos_days": bad, "share_pct": round(share, 1),
                   "units_sold": units, "thresholds": th}


# ---------------------------------------------------------------------- full forecast
def forecast_all(state: State) -> Dict[str, Any]:
    H, W = state.horizon, state.params["base_rate_window_days"]
    cells = sorted(state.inventory)
    histories = {c: history(state, *c) for c in cells}
    bt = backtest(state, histories)
    wape = bt["wape"]
    use_correction = wape["corrected"] is not None and wape["uncorrected"] is not None and wape["corrected"] <= wape["uncorrected"]
    use_dow = wape["corrected_dow"] is not None and wape["corrected"] is not None and wape["corrected_dow"] < wape["corrected"]
    chosen = "corrected_dow" if use_dow else ("corrected" if use_correction else "uncorrected")
    bands = band_multipliers(bt["ratios"][chosen])
    widen = state.params["low_confidence_band_widening"] / 100.0

    full_series = {c: corrected(h, state.history_days, W, correct=use_correction) for c, h in histories.items()}
    dow = dow_pattern(state, {c: corrected(h, state.history_days, W) for c, h in histories.items()},
                      state.history_days) if use_dow else None

    out: Dict[Tuple[str, str], CellForecast] = {}
    for (sku, loc), h in histories.items():
        series = full_series[(sku, loc)]
        if use_correction:
            base_rate = weighted_base(series, state.history_days, W)
        else:
            base_rate = weighted_base(h.observed, state.history_days, W, skip_nan=True)
        base = np.full(H, base_rate)
        if dow is not None:
            base = base * np.array([dow[weekday_of(state, state.history_days + t)] for t in range(1, H + 1)])
        cat = state.skus[sku]["category"]
        m10, m90, _n = bands.get(cat, (0.8, 1.2, 0))
        badge, detail = confidence(state, h)
        if badge == "Low":
            m10 = max(0.0, 1 - (1 - m10) * (1 + widen))
            m90 = 1 + (m90 - 1) * (1 + widen)
        uplift, ids = {}, []
        for lvl in sigmod.LEVELS:
            uplift[lvl], ids_l = sigmod.demand_uplift(state, sku, loc, H, lvl)
            ids = ids or ids_l
        out[(sku, loc)] = CellForecast(
            sku=sku, location=loc, base=base, m10=m10, m90=m90, uplift=uplift, signal_ids=ids,
            confidence=badge, confidence_detail=detail,
            observed_total=float(np.nansum(h.observed)), corrected_total=float(np.nansum(series)),
            oos_days=[i + 1 for i in np.where(h.oos)[0]], imputed_days=[i + 1 for i in np.where(h.missing)[0]],
            corrected_series=series, history=h, weekly_pattern_used=use_dow,
        )
    meta = {
        "backtest": {"origins": bt["origins"], "wape": wape, "bias": bt["bias"]},
        "components": {"stockout_correction": use_correction, "day_of_week": use_dow},
        "bands": {cat: {"p10_multiplier": round(v[0], 4), "p90_multiplier": round(v[1], 4), "ratios": v[2]}
                  for cat, v in bands.items()},
        "dow_pattern": dict(zip(WEEKDAYS, [round(float(x), 4) for x in dow])) if dow is not None else None,
        "coverage": _coverage(bt["ratios"][chosen], bands),
        "limitations": LIMITATIONS,
    }
    return {"cells": out, "meta": meta}


def _coverage(ratios: Dict[str, List[float]], bands) -> Optional[float]:
    inside = total = 0
    for cat, rs in ratios.items():
        lo, hi, _ = bands[cat]
        for r in rs:
            total += 1
            inside += lo <= r <= hi
    return round(inside / total, 3) if total else None


LIMITATIONS = [
    "90 days of history can show weekly patterns but cannot establish annual seasonality.",
    "Stockout correction assumes demand on out-of-stock days resembled in-stock days.",
    "Sparse SKUs get wide bands and a Low badge rather than a confident point estimate.",
]
