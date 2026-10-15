"""Independent feasibility / optimality check of a capex-and-staffing plan.

Deliberately does NOT import capacity.py or planning.py: loads are recomputed
here directly from the JSON data files with separate code, so a bug in the
model code cannot silently validate its own output.
"""
from __future__ import annotations

import json
import math
from typing import Any

from .fab import DATA_DIR

TOL = 1e-9
ROLES = ("operators", "technicians")


def _raw() -> tuple[dict[str, Any], dict[str, Any]]:
    fab = json.loads((DATA_DIR / "minifab.json").read_text(encoding="utf-8"))
    plan = json.loads((DATA_DIR / "planning.json").read_text(encoding="utf-8"))
    steps = dict(fab["steps"])
    steps.update(plan.get("extra_steps", {}))
    routes = {p: v["route"] for p, v in fab["products"].items()}
    routes.update({p: v["route"] for p, v in plan.get("extra_products", {}).items()})
    return {"fab": fab, "steps": steps, "routes": routes}, plan


def _setup(cfg: dict[str, float], a: tuple[str, str], b: tuple[str, str]) -> float:
    if a == b:
        return 0.0
    if a[0] == b[0]:
        return cfg["step_change"]
    if a[1] == b[1]:
        return cfg["product_change"]
    return cfg["both_change"]


def _walk(starts: dict[str, float]) -> dict[str, dict[str, float]]:
    """Route walk: machine minutes, operator handling minutes (load+unload+setup) per group."""
    data, _ = _raw()
    fab, steps, routes = data["fab"], data["steps"], data["routes"]
    machine: dict[str, float] = {g: 0.0 for g in fab["groups"]}
    handling: dict[str, float] = {g: 0.0 for g in fab["groups"]}
    visits: dict[str, dict[tuple[str, str], float]] = {g: {} for g in fab["groups"]}
    for prod, n in starts.items():
        if n == 0:
            continue
        for st in routes[prod]:
            s = steps[st]
            g = s["group"]
            k = fab["groups"][g].get("batch_size", 1)
            machine[g] += n * (s["load"] + s["run"] + s["unload"]) / k
            handling[g] += n * (s["load"] + s["unload"]) / k
            visits[g][(prod, st)] = visits[g].get((prod, st), 0.0) + n
    for g, spec in fab["groups"].items():
        if "setup" not in spec:
            continue
        tot = sum(visits[g].values())
        if tot == 0:
            continue
        exp = 0.0
        for a, va in visits[g].items():
            for b, vb in visits[g].items():
                exp += (va / tot) * (vb / tot) * _setup(spec["setup"], a, b)
        su = tot / fab["groups"][g].get("batch_size", 1) * exp
        machine[g] += su
        handling[g] += su
    return {"machine": machine, "handling": handling}


def independent_loads(starts: dict[str, float]) -> dict[str, float]:
    """Weekly machine minutes per group for the given starts (route walk + independent-sequence setups)."""
    return _walk(starts)["machine"]


def independent_operator_load(starts: dict[str, float]) -> float:
    """Weekly operator minutes (setup + load + unload, all groups pooled)."""
    return sum(_walk(starts)["handling"].values())


def _pm_em(group: str) -> tuple[float, float]:
    data, _ = _raw()
    spec = data["fab"]["groups"][group]
    cal = data["fab"]["calendar"]
    week = cal["week"]
    pm = spec["pm"]["minutes"] * week / cal[spec["pm"]["period"]]
    em = 0.0
    if "em" in spec:
        e = spec["em"]
        rep = (e["repair_min"] + e["repair_max"]) / 2
        ttf = (e["tbf_min"] + e["tbf_max"]) / 2
        em = week * rep / (ttf + rep)
    return pm, em


def available_per_tool(group: str) -> float:
    data, _ = _raw()
    pm, em = _pm_em(group)
    return data["fab"]["calendar"]["week"] - pm - em


def technician_min_per_tool(group: str) -> float:
    return sum(_pm_em(group))


def person_minutes(role: str) -> float:
    data, _ = _raw()
    cal, pers = data["fab"]["calendar"], data["fab"]["personnel"]
    off = pers["operator_off_time_per_shift"] if role == "operators" else pers["technician_off_time_per_shift"]
    return (cal["shift"] - sum(off)) * cal["week"] / cal["shift"]


def base_staff() -> dict[str, int]:
    data, _ = _raw()
    pers = data["fab"]["personnel"]
    return {"operators": len(pers["operators"]), "technicians": len(pers.get("technicians", {})) or 1}


def check_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a report; ``ok`` is True only if every check passes."""
    data, planning = _raw()
    groups = list(data["fab"]["groups"])
    quarters = plan["quarters"]
    T = len(quarters)
    L = int(planning["lead_time_quarters"])
    cap = float(plan["target_utilization"])
    lab = planning["labour"]
    cap_p = float(lab["target_utilization"])
    LH = int(lab["hiring_lead_time_quarters"])
    labour = bool(plan.get("labour_constraints", False))
    price = planning["tool_price_musd"]
    base = base_staff()
    problems: list[str] = []
    for g in groups:
        for q in range(T):
            b = plan["buy"][g][q]
            if not isinstance(b, int) or b < 0:
                problems.append(f"non-integer or negative purchase {g} {quarters[q]}: {b}")
            if b and q + L >= T:
                problems.append(f"order {g} in {quarters[q]} arrives after the horizon")
    worst = {}
    worst_people = {}
    for q in range(T):
        starts = {p: plan["demand"][p][q] for p in plan["demand"]}
        total = sum(starts.values())
        served = 1.0 - plan["shortfall_lots_per_week"][q] / total
        loads = independent_loads(starts)
        tools = {}
        for g in groups:
            tools[g] = data["fab"]["groups"][g]["tools"] + sum(plan["buy"][g][qq] for qq in range(T) if qq + L <= q)
            if tools[g] != plan["installed"][g][q]:
                problems.append(f"installed count mismatch {g} {quarters[q]}")
            u = loads[g] * served / (tools[g] * available_per_tool(g))
            worst[(g, q)] = u
            if u > cap + 1e-7:
                problems.append(f"utilisation {u:.4f} > cap {cap} for {g} in {quarters[q]}")
        if labour:
            staff = {}
            for r in ROLES:
                for qq in range(T):
                    hv = plan["hire"][r][qq]
                    if q == 0 and (not isinstance(hv, int) or hv < 0):
                        problems.append(f"non-integer or negative hire {r} {quarters[qq]}: {hv}")
                staff[r] = base[r] + sum(plan["hire"][r][qq] for qq in range(T) if qq + LH <= q)
                if staff[r] != plan["staff"][r][q]:
                    problems.append(f"staff count mismatch {r} {quarters[q]}")
            uo = independent_operator_load(starts) * served / (person_minutes("operators") * staff["operators"])
            ut = (sum(technician_min_per_tool(g) * tools[g] for g in groups)
                  / (person_minutes("technicians") * staff["technicians"]))
            worst_people[("operators", q)] = uo
            worst_people[("technicians", q)] = ut
            for r, u in (("operators", uo), ("technicians", ut)):
                if u > cap_p + 1e-7:
                    problems.append(f"{r} utilisation {u:.4f} > cap {cap_p} in {quarters[q]}")
    capex = sum(price[g] * plan["buy"][g][q] for g in groups for q in range(T))
    if abs(capex - plan["capex_musd"]) > 1e-6:
        problems.append(f"capex mismatch {capex} vs {plan['capex_musd']}")
    report = {
        "ok": not problems,
        "problems": problems,
        "max_utilization": max(worst.values()) if worst else 0.0,
        "max_operator_utilization": max((v for (r, _), v in worst_people.items() if r == "operators"), default=None),
        "max_technician_utilization": max((v for (r, _), v in worst_people.items() if r == "technicians"), default=None),
        "capex_musd_recomputed": capex,
    }
    if all(s <= TOL for s in plan["shortfall_lots_per_week"]):
        greedy = greedy_plan(plan["demand"], cap, labour=labour)
        report["greedy_capex_pv_musd"] = greedy["capex_pv_musd"]
        report["greedy_labour_pv_musd"] = greedy["labour_pv_musd"]
        report["greedy_feasible"] = greedy["feasible"]
        milp = plan["capex_pv_musd"] + (plan.get("labour_cost_pv_musd", 0.0) if labour else 0.0)
        report["greedy_equals_milp"] = greedy["feasible"] and abs(greedy["total_pv_musd"] - milp) <= 1e-6
        if greedy["feasible"] and greedy["total_pv_musd"] < milp - 1e-6:
            problems.append(f"MILP PV cost {milp} worse than greedy plan {greedy['total_pv_musd']}")
            report["ok"] = False
        elif greedy["feasible"] and not report["greedy_equals_milp"]:
            problems.append(f"MILP PV cost {milp} != greedy optimum {greedy['total_pv_musd']}")
            report["ok"] = False
    return report


def _running_need(need: list[int], have: int, lead: int, unit_cost: float, d: float) -> tuple[list[int], float, bool]:
    """Install/hire exactly the running-maximum need, as late as the lead time allows (no removals)."""
    T = len(need)
    buys = [0] * T
    pv = 0.0
    feasible = True
    for q in range(T):
        if need[q] > have:
            if q - lead < 0:
                feasible = False
                continue
            buys[q - lead] += need[q] - have
            pv += unit_cost * (need[q] - have) / (1 + d) ** (q - lead)
            have = need[q]
    return buys, pv, feasible


def greedy_plan(demand: dict[str, list[float]], cap: float, labour: bool = False) -> dict[str, Any]:
    """Without shortfall, tools separate by group: with a positive discount rate the cheapest way is
    to install exactly the tools needed in each quarter, ordered as late as the lead time allows.
    These tool counts are pointwise minimal, so the technician need (PM + repairs per installed tool)
    is pointwise minimal too, and hiring the running-maximum need of each role (no layoffs) with the
    labour cost of every extra head-quarter minimises labour cost. Hence the greedy plan is optimal
    for the no-shortfall case. Infeasible if a need arises within a lead time."""
    data, planning = _raw()
    groups = list(data["fab"]["groups"])
    T = len(planning["quarters"])
    L = int(planning["lead_time_quarters"])
    d = float(planning["discount_rate_per_quarter"])
    price = planning["tool_price_musd"]
    lab = planning["labour"]
    cap_p = float(lab["target_utilization"])
    LH = int(lab["hiring_lead_time_quarters"])
    feasible = True
    pv = 0.0
    buys: dict[str, list[int]] = {}
    installed: dict[str, list[int]] = {}
    for g in groups:
        have = data["fab"]["groups"][g]["tools"]
        need = []
        for q in range(T):
            starts = {p: demand[p][q] for p in demand}
            need.append(math.ceil(independent_loads(starts)[g] / (cap * available_per_tool(g)) - 1e-9))
        buys[g], gpv, ok = _running_need(need, have, L, price[g], d)
        feasible &= ok
        pv += gpv
        installed[g] = [have + sum(buys[g][qq] for qq in range(T) if qq + L <= q) for q in range(T)]
    labour_pv = 0.0
    hires: dict[str, list[int]] = {r: [0] * T for r in ROLES}
    if labour:
        base = base_staff()
        wage = {"operators": float(lab["operator_cost_musd_per_quarter"]),
                "technicians": float(lab["technician_cost_musd_per_quarter"])}
        need = {"operators": [], "technicians": []}
        for q in range(T):
            starts = {p: demand[p][q] for p in demand}
            need["operators"].append(math.ceil(independent_operator_load(starts)
                                               / (cap_p * person_minutes("operators")) - 1e-9))
            tech = sum(technician_min_per_tool(g) * installed[g][q] for g in groups)
            need["technicians"].append(math.ceil(tech / (cap_p * person_minutes("technicians")) - 1e-9))
        for r in ROLES:
            hires[r], _, ok = _running_need(need[r], base[r], LH, 0.0, d)
            feasible &= ok
            staff = [base[r] + sum(hires[r][qq] for qq in range(T) if qq + LH <= q) for q in range(T)]
            labour_pv += sum(wage[r] * (staff[q] - base[r]) / (1 + d) ** q for q in range(T))
    return {"feasible": feasible, "capex_pv_musd": pv, "labour_pv_musd": labour_pv, "total_pv_musd": pv + labour_pv,
            "buy": buys, "hire": hires}
