"""Signal scoping (design doc section 8).

Supply signals move inbound ETAs and supplier lead times; only demand signals touch
demand. A signal applies only to the SKUs, components, routes and locations it names.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .state import State

LEVELS = ("min", "likely", "max")
COMPONENT_LABELS = {"power_management_ic": "semiconductor"}


def active(state: State) -> List[Dict[str, Any]]:
    return [s for s in state.signals if s.get("include", True)]


def _window(sig: Dict[str, Any], t: int) -> bool:
    return int(sig["start_day"]) <= t <= int(sig["end_day"])


def component_match(sig: Dict[str, Any], sku_row: Dict[str, Any]) -> List[str]:
    return [c for c in sig.get("components") or [] if c in sku_row["components"]]


def po_signal_matches(state: State, sig: Dict[str, Any], po: Dict[str, Any]) -> Optional[str]:
    """Reason string when a supply signal applies to an open PO, else None."""
    if sig["type"] != "supply":
        return None
    comps = component_match(sig, state.skus[po["sku"]])
    if comps:
        return f"{po['sku']} contains {', '.join(comps)}"
    if po.get("route") in (sig.get("routes") or []):
        return f"on route {po['route']}"
    return None


def po_delay(state: State, po: Dict[str, Any], level: str = "likely") -> Tuple[int, List[str]]:
    """Delay in days for an open PO at a delay level. Overlapping signals do not add up: the largest applies."""
    best, ids = 0, []
    for sig in active(state):
        if po_signal_matches(state, sig, po) and _window(sig, max(1, po["eta_day"])):
            d = sig.get(f"delay_{level}")
            if d is not None:
                ids.append(sig["signal_id"])
                best = max(best, int(round(d)))
    return best, ids


def new_order_delay(state: State, sku: str, level: str = "likely") -> Tuple[int, List[str]]:
    """Delay applied to new orders. Only component signals delay new orders (section 8 table)."""
    best, ids = 0, []
    for sig in active(state):
        if sig["type"] == "supply" and component_match(sig, state.skus[sku]):
            d = sig.get(f"delay_{level}")
            if d is not None:
                ids.append(sig["signal_id"])
                best = max(best, int(round(d)))
    return best, ids


def demand_uplift(state: State, sku: str, loc: str, horizon: int, level: str = "likely") -> Tuple[List[float], List[str]]:
    """Fractional uplift per plan day 1..H from matching demand signals."""
    up = [0.0] * horizon
    ids: List[str] = []
    for sig in active(state):
        if sig["type"] != "demand":
            continue
        if sku not in (sig.get("skus") or []) or loc not in (sig.get("locations") or []):
            continue
        u = sig.get(f"uplift_{level}")
        if u is None:
            continue
        ids.append(sig["signal_id"])
        for t in range(1, horizon + 1):
            if _window(sig, t):
                up[t - 1] = max(up[t - 1], float(u))
    return up, ids


def unaffected_reason(state: State, sig: Dict[str, Any], sku: str, loc: Optional[str] = None) -> str:
    if sig["type"] == "demand":
        if sku not in (sig.get("skus") or []):
            return f"{sku} is not named by {sig['signal_id']}"
        return f"{loc} is not a location named by {sig['signal_id']}"
    if sig.get("components"):
        labels = [COMPONENT_LABELS.get(c, c) for c in sig["components"]]
        return f"no {' / '.join(labels)} component ({', '.join(sig['components'])})"
    if sig.get("routes"):
        return f"not on route {', '.join(sig['routes'])}"
    return "No supplied signal affects this SKU."


def impact(state: State, signal_id: str) -> Dict[str, Any]:
    """X2: affected and unaffected SKUs / POs with reasons."""
    sig = next((s for s in state.signals if s["signal_id"] == signal_id), None)
    if sig is None:
        raise KeyError(signal_id)
    skus_out, pos_out = [], []
    if sig["type"] == "demand":
        for sku in sorted(state.skus):
            for loc in sorted({l for (_, l) in state.inventory}):
                hit = sku in (sig.get("skus") or []) and loc in (sig.get("locations") or [])
                skus_out.append({"sku": sku, "location": loc, "affected": hit,
                                 "reason": f"named by {signal_id} (days {sig['start_day']}-{sig['end_day']})" if hit
                                 else unaffected_reason(state, sig, sku, loc)})
        for po in state.inbound:
            pos_out.append({"po_id": po["po_id"], "sku": po["sku"], "affected": False,
                            "reason": "demand signals never move inbound orders"})
    else:
        for sku in sorted(state.skus):
            comps = component_match(sig, state.skus[sku])
            on_route = [p["po_id"] for p in state.inbound if p["sku"] == sku and p.get("route") in (sig.get("routes") or [])]
            if comps:
                skus_out.append({"sku": sku, "affected": True, "reason": f"contains {', '.join(comps)}",
                                 "affects": "open POs and new orders"})
            elif on_route:
                skus_out.append({"sku": sku, "affected": True, "reason": f"{', '.join(on_route)} on route {', '.join(sig['routes'])}",
                                 "affects": "open POs on the route"})
            else:
                skus_out.append({"sku": sku, "affected": False, "reason": unaffected_reason(state, sig, sku)})
        for po in state.inbound:
            reason = po_signal_matches(state, sig, po)
            pos_out.append({"po_id": po["po_id"], "sku": po["sku"], "location": po["location"], "route": po.get("route"),
                            "affected": bool(reason),
                            "reason": reason or unaffected_reason(state, sig, po["sku"]),
                            "eta": str(po["eta"]),
                            "shifted_eta_likely": str(state.plan_date(po["eta_day"] + int(round(sig.get("delay_likely") or 0)))) if reason else None})
    return {"signal": public_signal(sig), "skus": skus_out, "pos": pos_out}


def public_signal(sig: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: (v if not hasattr(v, "isoformat") else v.isoformat()) for k, v in sig.items()}
    out["tag"] = "Data" if sig["status"] == "Confirmed" else "Assumption"
    return out
