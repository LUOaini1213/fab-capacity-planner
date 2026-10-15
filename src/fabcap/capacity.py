"""Static (deterministic, long-run average) capacity model.

Definitions used throughout the repository
------------------------------------------
* load (min/week)       = sum over products p and route steps s at the group of
                          starts_p * (load_s + run_s + unload_s) / batch_size
                          (+ expected setup minutes, if ``include_setups``).
                          Full batches are assumed (Kempf's convention).
* available (min/week)  = tools * (calendar week - PM minutes - EM minutes).
* utilization u         = load / available ("utilisation / availability" in
                          Kempf's basic capacity analysis).
* bottleneck            = group with the highest u.
* max starts            = largest total weekly starts, at the current mix, such
                          that every group has u <= util_cap.

EM (unscheduled downtime) has two accounting modes:
* ``"spec"``    - the count used in Kempf's own capacity table (2 EM events per
                  machine-week x mean repair time);
* ``"renewal"`` - the fraction implied by the stated distributions:
                  mean_repair / (mean_time_to_failure + mean_repair).
"""
from __future__ import annotations

from itertools import product as iproduct
from typing import Any

from .fab import Fab, Group

EM_MODES = ("spec", "renewal")


# --------------------------------------------------------------------------- setups
def visit_types(fab: Fab, group: str) -> dict[tuple[str, str], float]:
    """Weekly lot visits at ``group`` by (product, step) type."""
    out: dict[tuple[str, str], float] = {}
    for p in fab.products.values():
        for s in p.route:
            if fab.steps[s].group == group and p.starts_per_week > 0:
                out[(p.name, s)] = out.get((p.name, s), 0.0) + p.starts_per_week
    return out


def setup_between(g: Group, a: tuple[str, str], b: tuple[str, str]) -> float:
    """Kempf's sequence-dependent setup time on the lithography-like tool."""
    if g.setup is None or a == b:
        return 0.0
    same_product = a[0] == b[0]
    same_step = a[1] == b[1]
    if same_product:
        return float(g.setup["step_change"])
    if same_step:
        return float(g.setup["product_change"])
    return float(g.setup["both_change"])


def setup_distribution(fab: Fab, group: str) -> list[tuple[float, float]]:
    """(setup minutes, probability) per job if consecutive job types are independent
    draws from the visit mix (a model of an un-managed FIFO sequence)."""
    g = fab.groups[group]
    types = visit_types(fab, group)
    total = sum(types.values())
    if g.setup is None or total <= 0:
        return [(0.0, 1.0)]
    probs: dict[float, float] = {}
    for a, b in iproduct(types, types):
        pa, pb = types[a] / total, types[b] / total
        st = setup_between(g, a, b)
        probs[st] = probs.get(st, 0.0) + pa * pb
    return sorted(probs.items())


def expected_setup_per_job(fab: Fab, group: str) -> float:
    return sum(t * p for t, p in setup_distribution(fab, group))


# --------------------------------------------------------------------------- availability
def availability(fab: Fab, group: str, em_mode: str = "renewal") -> dict[str, float]:
    if em_mode not in EM_MODES:
        raise ValueError(f"em_mode must be one of {EM_MODES}")
    g = fab.groups[group]
    week = fab.week
    pm = g.pm_minutes * week / fab.period_minutes(g.pm_period)
    em = 0.0
    if g.em:
        mean_repair = (g.em["repair_min"] + g.em["repair_max"]) / 2.0
        if em_mode == "spec":
            em = g.em["spec_events_per_week"] * mean_repair
        else:
            mean_ttf = (g.em["tbf_min"] + g.em["tbf_max"]) / 2.0
            em = week * mean_repair / (mean_ttf + mean_repair)
    per_tool = week - pm - em
    return {
        "calendar_min_per_tool": week,
        "pm_min_per_tool": pm,
        "em_min_per_tool": em,
        "available_min_per_tool": per_tool,
        "availability": per_tool / week,
    }


# --------------------------------------------------------------------------- failure models
FAILURE_CLOCKS = ("calendar", "run")
# name -> (failure clock, rate reading); run-clock rates are calibrated at Kempf's 84 starts / 2 implanters
FAILURE_MODELS = {
    "calendar_dist": ("calendar", "renewal"),
    "calendar_spec": ("calendar", "spec"),
    "run_dist": ("run", "renewal"),
    "run_spec": ("run", "spec"),
}


def run_minutes_per_tool(fab: Fab, group: str) -> float:
    """Pure run minutes (no load/unload) per tool per week at the fab's starts."""
    g = fab.groups[group]
    run = sum(p.starts_per_week * fab.steps[s].run / g.batch_size
              for p in fab.products.values() for s in p.route if fab.steps[s].group == group)
    return run / g.tools


def em_events_per_tool_week(fab: Fab, group: str, em_mode: str = "renewal") -> float:
    """Failure rate per machine-week under a reading of the specification, calendar clock.

    ``renewal``: implied by the stated distributions (week / (mean TBF + mean repair) = 2.95);
    ``spec``: the count the specification's own arithmetic targets (2 per machine-week)."""
    if em_mode not in EM_MODES:
        raise ValueError(f"em_mode must be one of {EM_MODES}")
    em = fab.groups[group].em
    if not em:
        return 0.0
    if em_mode == "spec":
        return float(em["spec_events_per_week"])
    return fab.week / ((em["tbf_min"] + em["tbf_max"]) / 2.0 + (em["repair_min"] + em["repair_max"]) / 2.0)


def em_minutes_per_tool(fab: Fab, group: str, em_mode: str = "renewal", clock: str = "calendar",
                        ref: Fab | None = None) -> float:
    """Expected repair minutes per tool per week.

    calendar clock: rate x mean repair, independent of load.
    run clock: failures happen per run minute; the rate is calibrated so that at the reference
    fab (default: ``fab`` itself; use Kempf's 84 starts / 2 implanters) the count equals the
    calendar reading. Total failures then scale with total run time, not with the tool count."""
    if clock not in FAILURE_CLOCKS:
        raise ValueError(f"clock must be one of {FAILURE_CLOCKS}")
    em = fab.groups[group].em
    if not em:
        return 0.0
    mean_repair = (em["repair_min"] + em["repair_max"]) / 2.0
    ref = ref or fab
    rate = em_events_per_tool_week(ref, group, em_mode)
    if clock == "run":
        rate *= run_minutes_per_tool(fab, group) / run_minutes_per_tool(ref, group)
    return rate * mean_repair


def des_tbf_scale(fab: Fab, group: str, em_mode: str = "renewal", clock: str = "calendar") -> float:
    """Factor on the specification's U(24 h, 76 h) time-between-failures bounds that makes the DES
    produce ``em_events_per_tool_week(fab, group, em_mode)`` failures per machine-week at ``fab``'s load.

    calendar: (week / rate - mean repair) / mean TBF  (1.0 for the renewal reading, 1.54 for 2/week);
    run:      (run minutes per tool-week / rate) / mean TBF, i.e. the TBF is measured in run minutes."""
    if clock not in FAILURE_CLOCKS:
        raise ValueError(f"clock must be one of {FAILURE_CLOCKS}")
    em = fab.groups[group].em
    mean_tbf = (em["tbf_min"] + em["tbf_max"]) / 2.0
    rate = em_events_per_tool_week(fab, group, em_mode)
    if clock == "calendar":
        if em_mode == "renewal":
            return 1.0
        return (fab.week / rate - (em["repair_min"] + em["repair_max"]) / 2.0) / mean_tbf
    return run_minutes_per_tool(fab, group) / rate / mean_tbf


# --------------------------------------------------------------------------- loads
def group_load(fab: Fab, group: str, include_setups: bool = True) -> dict[str, float]:
    g = fab.groups[group]
    visits = 0.0
    process = 0.0
    for p in fab.products.values():
        for s in p.route:
            st = fab.steps[s]
            if st.group != group:
                continue
            visits += p.starts_per_week
            process += p.starts_per_week * st.occupancy / g.batch_size
    jobs = visits / g.batch_size
    setup = jobs * expected_setup_per_job(fab, group) if include_setups else 0.0
    return {
        "lot_visits_per_week": visits,
        "jobs_per_week": jobs,
        "process_min_per_week": process,
        "setup_min_per_week": setup,
        "load_min_per_week": process + setup,
    }


def capacity_table(
    fab: Fab, include_setups: bool = True, em_mode: str = "renewal", util_cap: float = 1.0
) -> dict[str, Any]:
    """Per-group load, availability, utilisation; bottleneck; max weekly starts."""
    if not 0 < util_cap <= 1.0:
        raise ValueError("util_cap must be in (0, 1]")
    rows: dict[str, Any] = {}
    total = fab.total_starts()
    for name, g in fab.groups.items():
        ld = group_load(fab, name, include_setups)
        av = availability(fab, name, em_mode)
        available = g.tools * av["available_min_per_tool"]
        u = ld["load_min_per_week"] / available if available > 0 else float("inf")
        # load is linear in total starts at fixed mix (setup per job depends only on mix)
        max_starts = (total * util_cap / u) if u > 0 else float("inf")
        rows[name] = {
            "tools": g.tools,
            **ld,
            **av,
            "available_min_per_week": available,
            "utilization": u,
            "loaded_fraction_of_calendar": ld["load_min_per_week"] / (g.tools * fab.week),
            "max_starts_per_week": max_starts,
        }
    bottleneck = max(rows, key=lambda k: (rows[k]["utilization"], k))
    max_starts = min(r["max_starts_per_week"] for r in rows.values())
    return {
        "starts_per_week": {n: p.starts_per_week for n, p in fab.products.items()},
        "total_starts_per_week": total,
        "include_setups": include_setups,
        "em_mode": em_mode,
        "util_cap": util_cap,
        "groups": rows,
        "bottleneck": bottleneck,
        "bottleneck_utilization": rows[bottleneck]["utilization"],
        "max_starts_per_week": max_starts,
        "feasible": all(r["utilization"] <= util_cap + 1e-12 for r in rows.values()),
    }


def per_shift(table: dict[str, Any], fab: Fab) -> dict[str, dict[str, float]]:
    """Express loads per shift, the unit Kempf uses in his capacity table."""
    k = fab.week / fab.calendar["shift"]
    return {
        g: {
            "required_min_per_shift": r["load_min_per_week"] / k,
            "available_min_per_shift": r["available_min_per_week"] / k,
            "utilization": r["utilization"],
        }
        for g, r in table["groups"].items()
    }


# --------------------------------------------------------------------------- mix sensitivity
def mix_sensitivity_pa_share(fab: Fab, shares: list[float], em_mode: str = "renewal") -> list[dict[str, Any]]:
    """Vary Pa's share of commercial lots (Pa+Pb fixed total, TW fixed)."""
    commercial = fab.products["Pa"].starts_per_week + fab.products["Pb"].starts_per_week
    out = []
    for s in shares:
        starts = {n: p.starts_per_week for n, p in fab.products.items()}
        starts["Pa"] = commercial * s
        starts["Pb"] = commercial * (1 - s)
        t = capacity_table(fab.with_starts(starts), em_mode=em_mode)
        out.append({
            "pa_share": s,
            "litho_setup_min_per_job": expected_setup_per_job(fab.with_starts(starts), "LITHO"),
            "bottleneck": t["bottleneck"],
            "bottleneck_utilization": t["bottleneck_utilization"],
            "max_starts_per_week": t["max_starts_per_week"],
            "utilization": {g: r["utilization"] for g, r in t["groups"].items()},
        })
    return out


def mix_sensitivity_new_product(
    fab: Fab, product: str, shares: list[float], total: float, fixed: dict[str, float], em_mode: str = "renewal"
) -> list[dict[str, Any]]:
    """Vary the share of ``product`` within the non-fixed lots; other products keep their relative ratio."""
    base = {n: p.starts_per_week for n, p in fab.products.items() if n != product and n not in fixed}
    base_total = sum(base.values())
    free = total - sum(fixed.values())
    out = []
    for s in shares:
        starts = dict(fixed)
        starts[product] = free * s
        for n, v in base.items():
            starts[n] = free * (1 - s) * v / base_total
        t = capacity_table(fab.with_starts(starts), em_mode=em_mode)
        out.append({
            "share": s,
            "bottleneck": t["bottleneck"],
            "bottleneck_utilization": t["bottleneck_utilization"],
            "max_starts_per_week": t["max_starts_per_week"],
            "utilization": {g: r["utilization"] for g, r in t["groups"].items()},
        })
    return out


def personnel_load(fab: Fab, em_mode: str = "renewal", include_setups: bool = True, clock: str = "calendar",
                   ref: Fab | None = None) -> dict[str, Any]:
    """Pooled operator and technician workload (min/week) vs availability.

    Operators do setup, load and unload; the technician does PM and repairs
    (Kempf 1.5-1.7). Travel time is ignored, so this is a lower bound on load.
    Repairs follow ``em_minutes_per_tool(fab, g, em_mode, clock, ref)``.
    """
    week = fab.week
    shifts = week / fab.calendar["shift"]
    ops = fab.personnel.get("operators", {})
    op_avail_shift = fab.calendar["shift"] - sum(fab.personnel.get("operator_off_time_per_shift", []))
    tech_avail_shift = fab.calendar["shift"] - sum(fab.personnel.get("technician_off_time_per_shift", []))
    n_tech = len(fab.personnel.get("technicians", {})) or 1
    op_load = 0.0
    tech_load = 0.0
    by_group = {}
    for name, g in fab.groups.items():
        ld = group_load(fab, name, include_setups)
        handling = 0.0
        for p in fab.products.values():
            for s in p.route:
                st = fab.steps[s]
                if st.group == name:
                    handling += p.starts_per_week * (st.load + st.unload) / g.batch_size
        handling += ld["setup_min_per_week"]
        av = availability(fab, name, em_mode)
        maint = g.tools * (av["pm_min_per_tool"] + em_minutes_per_tool(fab, name, em_mode, clock, ref))
        op_load += handling
        tech_load += maint
        by_group[name] = {"operator_min_per_week": handling, "technician_min_per_week": maint}
    op_avail = len(ops) * op_avail_shift * shifts
    tech_avail = n_tech * tech_avail_shift * shifts
    return {
        "operators": len(ops),
        "operator_load_min_per_week": op_load,
        "operator_available_min_per_week": op_avail,
        "operator_utilization": op_load / op_avail if op_avail else float("inf"),
        "technician_load_min_per_week": tech_load,
        "technician_available_min_per_week": tech_avail,
        "technician_utilization": tech_load / tech_avail if tech_avail else float("inf"),
        "by_group": by_group,
    }


def raw_process_time(fab: Fab, product: str) -> float:
    """Sum of machine occupancy (load+run+unload) along the route, no waiting, no setup."""
    return sum(fab.steps[s].occupancy for s in fab.products[product].route)
