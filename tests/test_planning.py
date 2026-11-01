import copy

import pytest

from fabcap.fab import load_planning
from fabcap.planning import scenario_demand, solve_plan
from fabcap.validate import check_plan, greedy_plan, independent_loads


@pytest.fixture(scope="module")
def plans():
    return {s: solve_plan(s) for s in ("base", "demand_plus_20", "mix_shift_pc")}


def test_scenario_demand_transforms():
    p = load_planning()
    base = scenario_demand(p, "base")
    up = scenario_demand(p, "demand_plus_20")
    assert up["Pa"][0] == base["Pa"][0]  # Q1 untouched
    assert up["Pa"][1] == pytest.approx(base["Pa"][1] * 1.2)
    assert up["TW"] == base["TW"]
    mix = scenario_demand(p, "mix_shift_pc")
    for q in range(8):
        assert sum(mix[k][q] for k in mix) == pytest.approx(sum(base[k][q] for k in base))
    assert mix["Pc"][7] == pytest.approx(2 * base["Pc"][7])
    custom = scenario_demand(p, "base", multiplier=1.5, from_quarter=4)
    assert custom["Pb"][2] == base["Pb"][2] and custom["Pb"][3] == pytest.approx(base["Pb"][3] * 1.5)
    with pytest.raises(KeyError):
        scenario_demand(p, "doom")


def test_plans_are_optimal_feasible_and_validated(plans):
    for name, plan in plans.items():
        assert plan["status"] == "Optimal"
        rep = check_plan(plan)
        assert rep["ok"], (name, rep["problems"])
        assert rep["max_utilization"] <= plan["target_utilization"] + 1e-9
        assert rep["greedy_capex_pv_musd"] == pytest.approx(plan["capex_pv_musd"])


def test_base_plan_content(plans):
    b = plans["base"]
    assert b["purchases"][0]["group"] == "LITHO"
    assert b["purchases"][0]["quarter_ordered"] == "Q2"
    assert b["capex_musd"] == pytest.approx(18.5)
    up = plans["demand_plus_20"]
    assert up["capex_musd"] > b["capex_musd"]
    assert up["purchases"][0]["quarter_ordered"] == "Q1"


def test_validator_rejects_broken_plans(plans):
    bad = copy.deepcopy(plans["base"])
    bad["buy"]["LITHO"] = [0] * 8
    bad["installed"]["LITHO"] = [1] * 8
    bad["capex_musd"] -= 12.0
    rep = check_plan(bad)
    assert not rep["ok"]
    assert any("LITHO" in p and "cap" in p for p in rep["problems"])
    bad2 = copy.deepcopy(plans["base"])
    bad2["capex_musd"] += 1.0
    assert any("capex mismatch" in p for p in check_plan(bad2)["problems"])
    bad3 = copy.deepcopy(plans["base"])
    bad3["buy"]["DIFF"][7] = 1
    bad3["capex_musd"] += 2.5
    assert any("after the horizon" in p for p in check_plan(bad3)["problems"])


def test_independent_loads_match_model():
    from fabcap.capacity import group_load
    from fabcap.fab import planning_fab
    starts = {"Pa": 40.0, "Pb": 17.0, "TW": 3.0, "Pc": 9.0}
    ind = independent_loads(starts)
    f = planning_fab().with_starts(starts)
    for g in ind:
        assert ind[g] == pytest.approx(group_load(f, g)["load_min_per_week"])


def test_shortfall_when_demand_outruns_lead_time():
    plan = solve_plan("base", multiplier=2.0, from_quarter=1)
    assert plan["shortfall_lots_per_week"][0] > 0
    assert check_plan(plan)["ok"]
    assert greedy_plan(plan["demand"], plan["target_utilization"])["feasible"] is False


def test_labour_constraints_hire_people_and_couple_tool_groups(plans):
    b = plans["base"]
    # one technician (PM + repairs = 87.0 % of 8400 min/week) is over the 85 % people cap from Q1
    assert b["staff"]["technicians"][0] == 2 and b["hire"]["technicians"][0] == 1
    assert max(b["personnel_utilization"]["technicians"]) <= 0.85 + 1e-9
    assert max(b["personnel_utilization"]["operators"]) <= 0.85 + 1e-9
    assert b["staff"]["operators"][-1] > b["staff"]["operators"][0]
    # the technician constraint couples groups: in +20 % the Q8 tool purchases force a third technician
    up = plans["demand_plus_20"]
    assert up["staff"]["technicians"][7] > up["staff"]["technicians"][6]
    assert up["installed"]["IMP"][7] > up["installed"]["IMP"][6]
    assert b["labour_cost_musd"] > 0 and b["labour_cost_pv_musd"] < b["labour_cost_musd"]
    # labour adds hires but does not change the tool purchases
    assert solve_plan("base", labour=False)["buy"] == b["buy"]


def test_validator_catches_understaffing(plans):
    bad = copy.deepcopy(plans["base"])
    bad["hire"]["technicians"] = [0] * 8
    bad["staff"]["technicians"] = [1] * 8
    rep = check_plan(bad)
    assert not rep["ok"] and any("technicians utilisation" in p for p in rep["problems"])
    bad2 = copy.deepcopy(plans["base"])
    bad2["staff"]["operators"][5] = 9
    assert any("staff count mismatch" in p for p in check_plan(bad2)["problems"])


def test_greedy_with_labour_matches_milp(plans):
    for plan in plans.values():
        g = greedy_plan(plan["demand"], plan["target_utilization"], labour=True)
        assert g["feasible"]
        assert g["hire"] == plan["hire"]
        assert g["total_pv_musd"] == pytest.approx(plan["capex_pv_musd"] + plan["labour_cost_pv_musd"])
        assert check_plan(plan)["greedy_equals_milp"] is True


def test_independent_operator_load_matches_model():
    from fabcap.capacity import personnel_load
    from fabcap.fab import planning_fab
    from fabcap.validate import independent_operator_load
    starts = {"Pa": 40.0, "Pb": 17.0, "TW": 3.0, "Pc": 9.0}
    assert independent_operator_load(starts) == pytest.approx(
        personnel_load(planning_fab().with_starts(starts))["operator_load_min_per_week"])


def test_lower_cap_costs_more():
    a = solve_plan("base", target_utilization=0.85)
    b = solve_plan("base", target_utilization=0.75)
    assert b["capex_pv_musd"] > a["capex_pv_musd"]


@pytest.mark.parametrize("kwargs", [
    {"multiplier": 1.2, "from_quarter": 9},
    {"multiplier": 1.2, "from_quarter": True},
])
def test_invalid_scenario_inputs_are_rejected_before_solving(kwargs):
    with pytest.raises(ValueError):
        scenario_demand(load_planning(), **kwargs)


@pytest.mark.parametrize("cap", [0, -1, 1.1, float("nan"), float("inf")])
def test_invalid_target_utilization_is_rejected(cap):
    with pytest.raises(ValueError, match="target_utilization"):
        solve_plan(target_utilization=cap)


@pytest.mark.parametrize("status", [0, -1, -2, -3])
def test_solver_failure_cannot_be_presented_as_a_plan(monkeypatch, status):
    import pulp

    monkeypatch.setattr(pulp.LpProblem, "solve", lambda *args, **kwargs: status)
    with pytest.raises(RuntimeError, match="did not return an optimal plan"):
        solve_plan()


def test_sum_of_finite_demands_cannot_overflow_the_validation_report(plans):
    import json

    plan = copy.deepcopy(plans["base"])
    for values in plan["demand"].values():
        values[0] = 10 ** 308
    report = check_plan(plan)
    assert report["ok"] is False
    assert any("demand" in issue and "finite" in issue for issue in report["problems"])
    json.dumps(report, allow_nan=False)
