"""Debottlenecking what-ifs, ranked by throughput gain per (illustrative) dollar.

Each option is evaluated twice:
* static  - change in max weekly starts from ``capacity.capacity_table``
            (util cap 1.0, renewal EM accounting, expected litho setups);
* DES     - change in simulated capacity: good lots out per week under CONWIP
            release (WIP held high enough that the line is saturated).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .capacity import FAILURE_MODELS, capacity_table, personnel_load
from .des import SimConfig
from .fab import Fab, load_planning

OPTIONS: dict[str, str] = {
    "add_DIFF": "Buy one more diffusion furnace (DIFF)",
    "add_IMP": "Buy one more implanter (IMP)",
    "add_LITHO": "Buy one more litho tool (LITHO)",
    "halve_litho_setup": "Halve all litho setup times",
    "halve_imp_repair": "Halve implanter repair time",
    "litho_pm_30_to_20": "Cut litho PM from 30 to 20 min per shift",
    "add_operator_imp_litho": "Add a third operator qualified on IMP and LITHO",
    "operators_litho_first": "Operators serve LITHO requests first (policy)",
}


def apply_option(fab: Fab, option: str) -> tuple[Fab, dict[str, Any]]:
    """Return the modified fab and DES-config overrides for an option."""
    if option == "baseline":
        return fab.copy(), {}
    if option not in OPTIONS:
        raise KeyError(f"unknown option {option!r}; choose from {sorted(OPTIONS)}")
    if option.startswith("add_") and option[4:] in fab.groups:
        g = option[4:]
        return fab.with_tools(g, fab.groups[g].tools + 1), {}
    if option == "halve_litho_setup":
        g = fab.groups["LITHO"]
        return fab.with_group("LITHO", setup={k: v / 2.0 for k, v in g.setup.items()}), {}
    if option == "halve_imp_repair":
        em = dict(fab.groups["IMP"].em)
        em["repair_min"] /= 2.0
        em["repair_max"] /= 2.0
        return fab.with_group("IMP", em=em), {}
    if option == "litho_pm_30_to_20":
        return fab.with_group("LITHO", pm_minutes=20.0), {}
    if option == "add_operator_imp_litho":
        f = fab.copy()
        f.personnel = dict(f.personnel)
        ops = dict(f.personnel["operators"])
        ops["PO3"] = {"groups": ["IMP", "LITHO"]}
        f.personnel["operators"] = ops
        return f, {}
    if option == "operators_litho_first":
        return fab.copy(), {"op_priority_group": "LITHO"}
    raise AssertionError(option)  # pragma: no cover


def option_costs() -> dict[str, float]:
    return dict(load_planning()["whatif_costs_musd"])


def static_whatif(fab: Fab, costs: dict[str, float] | None = None) -> dict[str, Any]:
    costs = costs or option_costs()
    base = capacity_table(fab)
    rows = []
    for opt in OPTIONS:
        f, _ = apply_option(fab, opt)
        t = capacity_table(f)
        pl = personnel_load(f)
        tech_by_fm = {fm: personnel_load(f, em_mode=mode, clock=clock, ref=fab)["technician_utilization"]
                      for fm, (clock, mode) in FAILURE_MODELS.items()}
        gain = t["max_starts_per_week"] - base["max_starts_per_week"]
        cost = float(costs[opt])
        rows.append({
            "option": opt,
            "description": OPTIONS[opt],
            "cost_musd": cost,
            "max_starts_per_week": t["max_starts_per_week"],
            "gain_starts_per_week": gain,
            "gain_per_musd": (gain / cost) if cost > 0 else None,
            "bottleneck_after": t["bottleneck"],
            "operator_utilization_after": pl["operator_utilization"],
            "technician_utilization_after": pl["technician_utilization"],
            "technician_utilization_after_by_failure_model": tech_by_fm,
            "technician_utilization_after_range": [min(tech_by_fm.values()), max(tech_by_fm.values())],
        })
    bpl = personnel_load(fab)
    return {"baseline_max_starts_per_week": base["max_starts_per_week"], "baseline_bottleneck": base["bottleneck"],
            "baseline_operator_utilization": bpl["operator_utilization"],
            "baseline_technician_utilization": bpl["technician_utilization"],
            "baseline_technician_utilization_by_failure_model": {
                fm: personnel_load(fab, em_mode=mode, clock=clock)["technician_utilization"]
                for fm, (clock, mode) in FAILURE_MODELS.items()},
            "options": rank(rows, "gain_starts_per_week")}


def rank(rows: list[dict[str, Any]], gain_key: str) -> list[dict[str, Any]]:
    """Zero-cost options with positive gain first (by gain), then by gain per M$, then the rest."""
    def key(r: dict[str, Any]) -> tuple:
        g = r[gain_key]
        if r["cost_musd"] == 0:
            return (0, -g, r["option"]) if g > 0 else (3, 0.0, r["option"])
        if g > 0:
            return (1, -g / r["cost_musd"], r["option"])
        return (2, -g, r["option"])

    out = sorted(rows, key=key)
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def des_option_configs(fab: Fab, base_cfg: SimConfig) -> dict[str, tuple[Fab, SimConfig]]:
    out = {"baseline": (fab.copy(), base_cfg)}
    for opt in OPTIONS:
        f, over = apply_option(fab, opt)
        out[opt] = (f, replace(base_cfg, **over))
    return out
