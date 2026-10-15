"""Tools the agent can call. Every number an agent may say comes out of these functions.

Outputs are pre-rounded to presentation precision (percent 1 dp, lots 1 dp,
hours 1 dp, X-factor 2 dp, M$ 1 dp) so an answer can quote them verbatim and
the number validator can demand exact matches. Inputs are echoed in outputs, and
``arg_derived`` lists the output fields that are a direct transform of the arguments
(the validator accepts those only if the question states the same number).
"""
from __future__ import annotations

import json
from typing import Any, Callable

from ..capacity import capacity_table
from ..fab import load_fab, planning_fab
from .. import io as fio
from ..planning import solve_plan
from ..queueing import group_visit_wait, route_cycle_time

GROUP_ALIASES = {"DIFF": "DIFF", "DIFFUSION": "DIFF", "FURNACE": "DIFF", "IMP": "IMP", "IMPLANT": "IMP",
                 "IMPLANTER": "IMP", "ION IMPLANT": "IMP", "LITHO": "LITHO", "LITHOGRAPHY": "LITHO", "SCANNER": "LITHO"}


class ToolError(ValueError):
    pass


def _pct(x: float) -> float:
    return round(100.0 * x, 1)


def _r1(x: float) -> float:
    return round(float(x), 1)


def _group(name: str | None) -> str | None:
    if name is None or str(name).strip().lower() in ("", "none", "null"):
        return None
    g = GROUP_ALIASES.get(str(name).strip().upper())
    if g is None:
        raise ToolError(f"unknown tool group {name!r}; use DIFF, IMP or LITHO")
    return g


def _num(x: Any, name: str, lo: float, hi: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError) as e:
        raise ToolError(f"{name} must be a number") from e
    if not lo <= v <= hi:
        raise ToolError(f"{name} must be between {lo} and {hi}")
    return v


def capacity_status(demand_change_pct: float = 0.0, extra_tools_group: str | None = None, extra_tools: int = 0) -> dict:
    """Static capacity at Kempf's product mix (84 starts/week scaled by the demand change)."""
    d = _num(demand_change_pct, "demand_change_pct", -90, 300)
    g = _group(extra_tools_group)
    n = int(_num(extra_tools, "extra_tools", 0, 10))
    fab = load_fab().scaled(1 + d / 100.0)
    if g and n:
        fab = fab.with_tools(g, fab.groups[g].tools + n)
    t = capacity_table(fab)
    t85 = capacity_table(fab, util_cap=0.85)
    derived = (["starts_per_week"] if d != 0 else []) + ([f"tools.{g}"] if g and n else [])
    return {
        "arg_derived": derived,
        "demand_change_pct": d,
        "extra_tools_group": g,
        "extra_tools": n if g else 0,
        "starts_per_week": _r1(fab.total_starts()),
        "tools": {k: v.tools for k, v in fab.groups.items()},
        "utilization_pct": {k: _pct(r["utilization"]) for k, r in t["groups"].items()},
        "bottleneck": t["bottleneck"],
        "bottleneck_utilization_pct": _pct(t["bottleneck_utilization"]),
        "over_capacity": t["bottleneck_utilization"] > 1.0,
        "max_starts_per_week": _r1(t["max_starts_per_week"]),
        "max_starts_per_week_at_85pct_cap": _r1(t85["max_starts_per_week"]),
        "cap_pct": 85.0,
    }


def cycle_time_estimate(demand_change_pct: float = 0.0) -> dict:
    """Queueing (G/G/m) cycle-time estimate at Kempf's mix scaled by the demand change."""
    d = _num(demand_change_pct, "demand_change_pct", -90, 300)
    fab = load_fab().scaled(1 + d / 100.0)
    rc = route_cycle_time(fab)
    finite = rc["x_factor"] == rc["x_factor"] and rc["x_factor"] != float("inf")
    waits = group_visit_wait(fab)
    return {
        "arg_derived": ["starts_per_week"] if d != 0 else [],
        "demand_change_pct": d,
        "starts_per_week": _r1(fab.total_starts()),
        "stable": finite,
        "cycle_time_hours": _r1(rc["cycle_time_min"] / 60.0) if finite else None,
        "raw_process_time_hours": _r1(rc["raw_process_time_min"] / 60.0),
        "x_factor": round(rc["x_factor"], 2) if finite else None,
        "queue_time_hours": {g: (_r1(q["ctq"] / 60.0) if q["ctq"] != float("inf") else None)
                             for g, q in rc["groups"].items()},
        # queue for a free tool + (diffusion) waiting for the batch to fill
        "wait_per_visit_hours": {g: (_r1(w / 60.0) if w != float("inf") else None) for g, w in waits.items()},
    }


def capex_plan(demand_change_pct: float = 0.0, from_quarter: int = 1, scenario: str = "base") -> dict:
    """MILP capex plan over the illustrative 8-quarter planning forecast."""
    d = _num(demand_change_pct, "demand_change_pct", -50, 200)
    q0 = int(_num(from_quarter, "from_quarter", 1, 8))
    if scenario not in ("base", "mix_shift_pc"):
        raise ToolError("scenario must be 'base' or 'mix_shift_pc'")
    plan = solve_plan(scenario, multiplier=1 + d / 100.0, from_quarter=q0)
    before = capacity_table(planning_fab().with_starts({p: v[q0 - 1] for p, v in plan["demand"].items()}))
    return {
        "arg_derived": ["starts_per_week_in_from_quarter"] if d != 0 else [],
        "bottleneck_before_purchases": before["bottleneck"],
        "bottleneck_before_purchases_utilization_pct": _pct(before["bottleneck_utilization"]),
        "starts_per_week_in_from_quarter": _r1(sum(v[q0 - 1] for v in plan["demand"].values())),
        "demand_change_pct": d,
        "from_quarter": q0,
        "scenario": scenario,
        "status": plan["status"],
        "purchases": [{"group": p["group"], "tools": p["tools"], "order_in": p["quarter_ordered"],
                       "in_service": p["quarter_installed"], "cost_musd": _r1(p["cost_musd"])} for p in plan["purchases"]],
        "total_capex_musd": _r1(plan["capex_musd"]),
        "hires": [{"role": h["role"].rstrip("s"), "people": h["people"], "hire_in": h["quarter_hired"]}
                  for h in plan["hires"]],
        "labour_cost_musd": round(plan["labour_cost_musd"], 2),
        "bottleneck_after_plan_by_quarter": dict(zip(plan["quarters"], plan["bottleneck"])),
        "unserved_lots_per_week_total": _r1(sum(plan["shortfall_lots_per_week"])),
        "target_utilization_pct": _pct(plan["target_utilization"]),
    }


def _results(name: str) -> Any:
    path = fio.RESULTS_DIR / name
    if not path.exists():
        raise ToolError(f"{name} not found; run scripts/reproduce.py first")
    return json.loads(path.read_text(encoding="ascii"))


def debottleneck_ranking(method: str = "static") -> dict:
    """Improvement options ranked by throughput gain per M$ (static model or simulation)."""
    if method not in ("static", "des"):
        raise ToolError("method must be 'static' or 'des'")
    w = _results("whatif.json")
    if method == "static":
        rows = w["static"]["options"]
        out = [{"rank": r["rank"], "option": r["option"], "cost_musd": _r1(r["cost_musd"]),
                "gain_starts_per_week": _r1(r["gain_starts_per_week"]),
                "gain_per_musd": (_r1(r["gain_per_musd"]) if r["gain_per_musd"] is not None else None)} for r in rows]
        base = _r1(w["static"]["baseline_max_starts_per_week"])
    else:
        rows = w["des"]["options"]
        out = [{"rank": r["rank"], "option": r["option"], "cost_musd": _r1(r["cost_musd"]),
                "gain_lots_per_week": _r1(r["gain_lots_per_week"]),
                "gain_per_musd": (_r1(r["gain_per_musd"]) if r["gain_per_musd"] is not None else None)} for r in rows]
        base = _r1(w["des"]["baseline_capacity_lots_per_week"]["mean"])
    best_paid = next((r for r in out if r["cost_musd"] > 0), None)
    return {"method": method, "baseline": base, "options": out, "top_option": out[0]["option"],
            "best_paid_option": best_paid["option"] if best_paid else None}


def simulation_summary(rule: str = "FIFO") -> dict:
    """Simulated (DES, full model) results for a dispatching rule: capacity and 84-starts behaviour."""
    rule = str(rule).upper()
    aliases = {"CRITICAL RATIO": "CR", "SETUP": "SA", "SETUP AVOIDANCE": "SA", "FCFS": "FIFO"}
    rule = aliases.get(rule, rule)
    if rule not in ("FIFO", "CR", "SA"):
        raise ToolError("rule must be FIFO, CR or SA")
    runs = _results("des_rules.json")["runs"]
    cw = runs[f"{rule}_conwip40"]
    op = runs[f"{rule}_open84"]
    best = max(("FIFO", "CR", "SA"), key=lambda r: (runs[f"{r}_conwip40"]["throughput_per_week"]["mean"], r))
    return {
        "rule": rule,
        "capacity_good_lots_per_week": _r1(cw["throughput_per_week"]["mean"]),
        "capacity_ci95_half_width": _r1(cw["throughput_per_week"]["half_width"]),
        "open_loop_starts_per_week": _r1(op["starts_per_week"]["mean"]),
        "at_84_starts_output_per_week": _r1(op["throughput_per_week"]["mean"]),
        "at_84_starts_unstable_runs": op["unstable_runs"],
        "replications": len(cw["seeds"]),
        "best_rule_by_capacity": best,
    }


TOOLS: dict[str, Callable[..., dict]] = {
    "capacity_status": capacity_status,
    "cycle_time_estimate": cycle_time_estimate,
    "capex_plan": capex_plan,
    "debottleneck_ranking": debottleneck_ranking,
    "simulation_summary": simulation_summary,
}

SCHEMAS = [
    {"type": "function", "function": {
        "name": "capacity_status",
        "description": "Static capacity of the Mini-Fab at its standard product mix (84 lot starts/week). Returns utilization % per tool group (DIFF, IMP, LITHO), the bottleneck, and the maximum weekly starts (at 100% and at an 85% utilization cap). Optional demand change in percent and optional extra tools.",
        "parameters": {"type": "object", "properties": {
            "demand_change_pct": {"type": "number", "description": "Demand change in percent, e.g. 20 for +20%, -10 for -10%. Default 0."},
            "extra_tools_group": {"type": "string", "enum": ["DIFF", "IMP", "LITHO"], "description": "Tool group to add tools to (optional)."},
            "extra_tools": {"type": "integer", "description": "Number of tools to add to extra_tools_group. Default 0."}}}}},
    {"type": "function", "function": {
        "name": "cycle_time_estimate",
        "description": "Queueing-model estimate of the mean cycle time (hours), X-factor, queue time per tool group (waiting for a free tool) and total wait per visit (queue plus, at diffusion, waiting for a full batch), at the standard mix scaled by a demand change in percent.",
        "parameters": {"type": "object", "properties": {
            "demand_change_pct": {"type": "number", "description": "Demand change in percent. Default 0."}}}}},
    {"type": "function", "function": {
        "name": "capex_plan",
        "description": "Optimal tool purchase and hiring plan (MILP) over the 8-quarter planning forecast Q1-Q8: which tools to buy, when, total capex in M$, operators/technicians to hire and their labour cost, bottleneck per quarter. Use demand_change_pct with from_quarter for a demand shock (e.g. +20% from next quarter = demand_change_pct 20, from_quarter 2).",
        "parameters": {"type": "object", "properties": {
            "demand_change_pct": {"type": "number", "description": "Demand change in percent. Default 0."},
            "from_quarter": {"type": "integer", "description": "Quarter number (1-8) from which the change applies. Next quarter = 2. Default 1."},
            "scenario": {"type": "string", "enum": ["base", "mix_shift_pc"], "description": "base forecast or mix shift toward product Pc."}}}}},
    {"type": "function", "function": {
        "name": "debottleneck_ranking",
        "description": "Improvement options (add a tool, cut setup/repair/PM time, add an operator, operator priority) ranked by throughput gain per M$. method 'static' uses the capacity model, 'des' uses simulation.",
        "parameters": {"type": "object", "properties": {
            "method": {"type": "string", "enum": ["static", "des"]}}}}},
    {"type": "function", "function": {
        "name": "simulation_summary",
        "description": "Discrete-event simulation results for a dispatching rule (FIFO, CR = critical ratio, SA = setup avoidance): simulated capacity in good lots/week and behaviour at 84 starts/week.",
        "parameters": {"type": "object", "properties": {
            "rule": {"type": "string", "enum": ["FIFO", "CR", "SA"]}}}}},
]


def call_tool(name: str, args: dict | None) -> dict:
    """Execute a tool by name; errors are returned as data so an LLM can recover."""
    if name not in TOOLS:
        return {"error": f"unknown tool {name!r}"}
    try:
        return TOOLS[name](**(args or {}))
    except (ToolError, TypeError) as e:
        return {"error": str(e)}
