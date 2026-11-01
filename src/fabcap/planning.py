"""Capital-and-staffing planning MILP: which tools to buy, whom to hire, and when.

Decisions (integers >= 0): x[g, q] tools of group g ordered in quarter q (installed
from q + lead_time); h_op[q], h_tech[q] operators / technicians hired in quarter q
(available from q + hiring lead time; nobody is laid off). For every quarter q

    tools:       load[g, q] * served[q]            <= cap   * available_per_tool[g] * tools[g, q]
    operators:   op_load[q] * served[q]            <= cap_p * op_available_per_person * operators[q]
    technician:  sum_g tools[g, q] * maint_per_tool[g] <= cap_p * tech_available_per_person * technicians[q]

with served[q] = 1 - s[q] / D[q]. ``load`` and ``op_load`` (setup + load + unload minutes) come
from the static capacity model at the quarter's product mix; ``maint_per_tool`` = PM + expected
repair minutes per tool (renewal EM accounting). The technician constraint couples the tool
groups: every tool bought adds PM (and, for implanters, repair) work for the technicians.
Objective: discounted capex + discounted labour cost of the extra heads + shortfall penalty.
Solved with PuLP + CBC. All prices, wages, caps and lead times are illustrative
(``data/planning.json``). Operators are pooled (eligibility per group is ignored here; the DES
check in ``experiments.run_plan_vs_des`` keeps it).
"""
from __future__ import annotations

import math
from typing import Any

import pulp

from .capacity import availability, group_load, personnel_load
from .fab import Fab, load_planning, planning_fab


def scenario_demand(planning: dict[str, Any], scenario: str = "base", multiplier: float | None = None,
                    from_quarter: int | None = None, pc_ramp_factor: float | None = None) -> dict[str, list[float]]:
    """Quarterly demand (lots/week) per product for a named scenario or explicit overrides."""
    base = {p: [float(v) for v in vals] for p, vals in planning["demand_base"].items()}
    T = len(planning["quarters"])
    if not T or not base or any(len(vals) != T for vals in base.values()):
        raise ValueError("demand must contain one value per product and planning quarter")
    if any(not math.isfinite(v) or v < 0 for vals in base.values() for v in vals):
        raise ValueError("demand must contain finite nonnegative quantities")
    spec = dict(planning["scenarios"].get(scenario, {})) if scenario else {}
    if scenario and scenario not in planning["scenarios"]:
        raise KeyError(f"unknown scenario {scenario!r}")
    if multiplier is not None:
        spec["multiplier"] = multiplier
        spec.setdefault("from_quarter", 1)
    if from_quarter is not None:
        spec["from_quarter"] = from_quarter
    if pc_ramp_factor is not None:
        spec["pc_ramp_factor"] = pc_ramp_factor
    out = {p: list(v) for p, v in base.items()}
    if "pc_ramp_factor" in spec and "Pc" in out:
        f = float(spec["pc_ramp_factor"])
        if not math.isfinite(f) or f < 0:
            raise ValueError("pc_ramp_factor must be finite and >= 0")
        for i, v in enumerate(base["Pc"]):
            extra = v * (f - 1.0)
            extra = min(extra, out["Pa"][i])
            out["Pc"][i] = v + extra
            out["Pa"][i] = out["Pa"][i] - extra
    if "multiplier" in spec:
        mult = float(spec["multiplier"])
        raw_q0 = spec.get("from_quarter", 1)
        if (isinstance(raw_q0, bool) or not isinstance(raw_q0, (int, float))
                or not math.isfinite(raw_q0) or int(raw_q0) != raw_q0 or not 1 <= raw_q0 <= T):
            raise ValueError("from_quarter must be a whole planning quarter within the horizon")
        q0 = int(raw_q0)
        if not math.isfinite(mult) or mult < 0:
            raise ValueError("multiplier must be finite and >= 0")
        for p in out:
            if p == "TW":
                continue
            out[p] = [v * mult if (i + 1) >= q0 else v for i, v in enumerate(out[p])]
    if any(not math.isfinite(v) or v < 0 for vals in out.values() for v in vals):
        raise ValueError("scenario demand must contain finite nonnegative quantities")
    return out


def quarter_loads(fab: Fab, demand: dict[str, list[float]], n_quarters: int) -> list[dict[str, Any]]:
    rows = []
    for q in range(n_quarters):
        f = fab.with_starts({p: demand[p][q] for p in demand})
        pl = personnel_load(f)
        rows.append({
            "total": f.total_starts(),
            "load": {g: group_load(f, g, include_setups=True)["load_min_per_week"] for g in fab.groups},
            "operator_load": pl["operator_load_min_per_week"],
        })
    return rows


def personnel_capacity(fab: Fab) -> dict[str, float]:
    """Available minutes per person per week, and PM + expected repair minutes per tool per week."""
    shifts = fab.week / fab.calendar["shift"]
    op = (fab.calendar["shift"] - sum(fab.personnel.get("operator_off_time_per_shift", []))) * shifts
    tech = (fab.calendar["shift"] - sum(fab.personnel.get("technician_off_time_per_shift", []))) * shifts
    maint = {}
    for g in fab.groups:
        av = availability(fab, g, "renewal")
        maint[g] = av["pm_min_per_tool"] + av["em_min_per_tool"]
    return {"operator_min_per_person": op, "technician_min_per_person": tech, "maint_min_per_tool": maint}


def solve_plan(scenario: str = "base", multiplier: float | None = None, from_quarter: int | None = None,
               pc_ramp_factor: float | None = None, planning: dict[str, Any] | None = None,
               fab: Fab | None = None, target_utilization: float | None = None,
               labour: bool = True) -> dict[str, Any]:
    planning = planning or load_planning()
    fab = fab or planning_fab(planning=planning)
    quarters = planning["quarters"]
    T = len(quarters)
    L = int(planning["lead_time_quarters"])
    cap = float(target_utilization if target_utilization is not None else planning["target_utilization"])
    if not math.isfinite(cap) or not 0 < cap <= 1:
        raise ValueError("target_utilization must be finite, greater than zero and at most one")
    d = float(planning["discount_rate_per_quarter"])
    price = planning["tool_price_musd"]
    penalty = float(planning["shortfall_penalty_musd_per_lot"]) * planning["weeks_per_quarter"]
    lab = planning["labour"]
    cap_p = float(lab["target_utilization"])
    LH = int(lab["hiring_lead_time_quarters"])
    wage = {"operators": float(lab["operator_cost_musd_per_quarter"]),
            "technicians": float(lab["technician_cost_musd_per_quarter"])}
    base_staff = {"operators": len(fab.personnel.get("operators", {})),
                  "technicians": len(fab.personnel.get("technicians", {})) or 1}
    demand = scenario_demand(planning, scenario, multiplier, from_quarter, pc_ramp_factor)
    loads = quarter_loads(fab, demand, T)
    avail = {g: availability(fab, g, "renewal")["available_min_per_tool"] for g in fab.groups}
    pc = personnel_capacity(fab)
    groups = list(fab.groups)
    roles = ("operators", "technicians")

    prob = pulp.LpProblem("capex_staff_plan", pulp.LpMinimize)
    x = {(g, q): pulp.LpVariable(f"buy_{g}_{q}", lowBound=0, cat="Integer") for g in groups for q in range(T)}
    h = {(r, q): pulp.LpVariable(f"hire_{r}_{q}", lowBound=0, cat="Integer") for r in roles for q in range(T)}
    s = {q: pulp.LpVariable(f"short_{q}", lowBound=0, upBound=loads[q]["total"]) for q in range(T)}
    disc = {q: 1.0 / (1.0 + d) ** q for q in range(T)}

    def tools_expr(g: str, q: int):
        return fab.groups[g].tools + pulp.lpSum(x[g, qq] for qq in range(T) if qq + L <= q)

    def extra_staff_expr(r: str, q: int):
        return pulp.lpSum(h[r, qq] for qq in range(T) if qq + LH <= q)

    labour_cost = pulp.lpSum(wage[r] * disc[q] * extra_staff_expr(r, q) for r in roles for q in range(T))
    prob += (pulp.lpSum(price[g] * disc[q] * x[g, q] for g in groups for q in range(T))
             + pulp.lpSum(penalty * disc[q] * s[q] for q in range(T))
             + (labour_cost if labour else 0))
    for q in range(T):
        D = loads[q]["total"]
        for g in groups:
            ld = loads[q]["load"][g]
            prob += ld - (ld / D if D else 0.0) * s[q] <= cap * avail[g] * tools_expr(g, q), f"cap_{g}_{q}"
        if labour:
            ol = loads[q]["operator_load"]
            prob += (ol - (ol / D if D else 0.0) * s[q] <= cap_p * pc["operator_min_per_person"]
                     * (base_staff["operators"] + extra_staff_expr("operators", q))), f"ops_{q}"
            prob += (pulp.lpSum(pc["maint_min_per_tool"][g] * tools_expr(g, q) for g in groups)
                     <= cap_p * pc["technician_min_per_person"]
                     * (base_staff["technicians"] + extra_staff_expr("technicians", q))), f"tech_{q}"
    # orders / hires that could never arrive inside the horizon are pointless; forbid them for a unique answer
    for q in range(T):
        for g in groups:
            if q + L >= T:
                prob += x[g, q] == 0, f"late_{g}_{q}"
        for r in roles:
            if q + LH >= T or not labour:
                prob += h[r, q] == 0, f"latehire_{r}_{q}"
    status = prob.solve(pulp.PULP_CBC_CMD(msg=0))
    status_name = pulp.LpStatus[status]
    if status_name != "Optimal":
        raise RuntimeError(f"planning solver did not return an optimal plan: {status_name}")
    buys = {g: [int(round(x[g, q].value() or 0)) for q in range(T)] for g in groups}
    hires = {r: [int(round(h[r, q].value() or 0)) for q in range(T)] for r in roles}
    short = [max(0.0, float(s[q].value() or 0.0)) for q in range(T)]
    installed = {g: [fab.groups[g].tools + sum(buys[g][qq] for qq in range(T) if qq + L <= q) for q in range(T)]
                 for g in groups}
    staff = {r: [base_staff[r] + sum(hires[r][qq] for qq in range(T) if qq + LH <= q) for q in range(T)] for r in roles}
    # A quarter with no demand has zero load/shortfall; installed tools still need maintenance.
    served = [1 - short[q] / loads[q]["total"] if loads[q]["total"] else 1.0 for q in range(T)]
    util = {g: [loads[q]["load"][g] * served[q] / (avail[g] * installed[g][q]) for q in range(T)] for g in groups}
    putil = {
        "operators": [loads[q]["operator_load"] * served[q] / (pc["operator_min_per_person"] * staff["operators"][q])
                      for q in range(T)],
        "technicians": [sum(pc["maint_min_per_tool"][g] * installed[g][q] for g in groups)
                        / (pc["technician_min_per_person"] * staff["technicians"][q]) for q in range(T)],
    }
    capex = sum(price[g] * buys[g][q] for g in groups for q in range(T))
    capex_pv = sum(price[g] * disc[q] * buys[g][q] for g in groups for q in range(T))
    extra = {r: [staff[r][q] - base_staff[r] for q in range(T)] for r in roles}
    labour_total = sum(wage[r] * extra[r][q] for r in roles for q in range(T))
    labour_pv = sum(wage[r] * disc[q] * extra[r][q] for r in roles for q in range(T))
    bott = [max(groups, key=lambda g: (util[g][q], g)) for q in range(T)]
    return {
        "scenario": scenario,
        "overrides": {"multiplier": multiplier, "from_quarter": from_quarter, "pc_ramp_factor": pc_ramp_factor},
        "status": status_name,
        "quarters": quarters,
        "target_utilization": cap,
        "labour_constraints": labour,
        "personnel_target_utilization": cap_p,
        "lead_time_quarters": L,
        "hiring_lead_time_quarters": LH,
        "demand": demand,
        "total_demand": [loads[q]["total"] for q in range(T)],
        "buy": buys,
        "installed": installed,
        "hire": hires,
        "staff": staff,
        "utilization": util,
        "personnel_utilization": putil,
        "operator_load_min_per_week": [loads[q]["operator_load"] for q in range(T)],
        "bottleneck": bott,
        "shortfall_lots_per_week": short,
        "capex_musd": capex,
        "capex_pv_musd": capex_pv,
        "labour_cost_musd": labour_total,
        "labour_cost_pv_musd": labour_pv,
        "total_cost_pv_musd": capex_pv + labour_pv,
        "objective": float(pulp.value(prob.objective)),
        "purchases": [{"quarter_ordered": quarters[q], "quarter_installed": quarters[q + L], "group": g,
                       "tools": buys[g][q], "cost_musd": price[g] * buys[g][q]}
                      for q in range(T) for g in groups if buys[g][q] > 0],
        "hires": [{"quarter_hired": quarters[q], "quarter_available": quarters[q + LH], "role": r,
                   "people": hires[r][q]} for q in range(T) for r in roles if hires[r][q] > 0],
    }
