"""Offline regressions for corrupt saved plans and demand-free quarters."""

import copy
import json

import pytest

from fabcap.fab import load_planning
from fabcap.planning import scenario_demand, solve_plan
from fabcap.validate import check_plan, person_minutes, technician_min_per_tool


@pytest.fixture(scope="module")
def base_plan():
    return solve_plan("base")


def replaced(plan, path, value):
    result = copy.deepcopy(plan)
    parent = result
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return result


def assert_rejected(plan):
    report = check_plan(plan)
    assert report["ok"] is False, report
    assert report["problems"] and all(isinstance(item, str) for item in report["problems"])
    # A failed validation still needs to be usable by CLI / web JSON clients.
    json.dumps(report, allow_nan=False)
    return report


@pytest.mark.parametrize("bad", [None, [], {}, "not a plan"])
def test_validator_returns_a_report_for_missing_plan_structure(bad):
    assert_rejected(bad)


@pytest.mark.parametrize("status", ["Infeasible", "Unbounded", "Not Solved", None])
def test_validator_never_approves_unsolved_plans(base_plan, status):
    assert_rejected(replaced(base_plan, ("status",), status))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.25])
@pytest.mark.parametrize("path", [
    ("shortfall_lots_per_week", 0),
    ("demand", "Pc", 0),
    ("total_demand", 0),
    ("capex_musd",),
])
def test_nonfinite_and_negative_quantities_cannot_validate(base_plan, path, value):
    assert_rejected(replaced(base_plan, path, value))


@pytest.mark.parametrize("path", [
    ("capex_pv_musd",), ("labour_cost_musd",), ("labour_cost_pv_musd",),
    ("total_cost_pv_musd",), ("objective",),
    ("utilization", "LITHO", 0), ("personnel_utilization", "technicians", 0),
    ("operator_load_min_per_week", 0),
])
def test_nonfinite_reported_metrics_cannot_validate(base_plan, path):
    assert_rejected(replaced(base_plan, path, float("nan")))


def test_shortfall_cannot_exceed_available_demand(base_plan):
    bad = replaced(base_plan, ("shortfall_lots_per_week", 0), base_plan["total_demand"][0] + 1)
    assert_rejected(bad)


def test_total_demand_must_agree_with_product_quantities(base_plan):
    assert_rejected(replaced(base_plan, ("total_demand", 0), base_plan["total_demand"][0] + 1))


@pytest.mark.parametrize("path", [
    ("buy", "LITHO", 0), ("installed", "LITHO", 0),
    ("hire", "technicians", 0), ("staff", "technicians", 0),
])
@pytest.mark.parametrize("value", [True, 0.5, -1])
def test_tool_and_person_counts_must_be_nonnegative_integers(base_plan, path, value):
    assert_rejected(replaced(base_plan, path, value))


@pytest.mark.parametrize("count", [10**307, 10**400], ids=["derived-float-overflow", "float-conversion-overflow"])
def test_extreme_saved_counts_return_json_errors_instead_of_overflowing(base_plan, count):
    assert_rejected(replaced(base_plan, ("buy", "LITHO", 0), count))


@pytest.mark.parametrize(("path", "value"), [
    (("quarters",), []),
    (("quarters", 0), "Q9"),
    (("buy", "LITHO"), [0]),
    (("installed", "DIFF"), None),
    (("hire",), {}),
    (("staff", "technicians"), "22222222"),
    (("demand", "Pc"), [0]),
    (("demand",), {"unknown_product": [0] * 8}),
    (("shortfall_lots_per_week",), [0] * 9),
    (("utilization",), []),
    (("personnel_utilization", "operators"), [0]),
    (("capex_musd",), "18.5"),
    (("labour_constraints",), "false"),
])
def test_malformed_fields_are_reported_without_arithmetic_crashes(base_plan, path, value):
    assert_rejected(replaced(base_plan, path, value))


@pytest.mark.parametrize("value", [0, 1.01, float("nan"), True])
def test_invalid_capacity_target_cannot_validate(base_plan, value):
    assert_rejected(replaced(base_plan, ("target_utilization",), value))


@pytest.mark.parametrize("field", [
    "capex_pv_musd", "labour_cost_musd", "labour_cost_pv_musd",
    "total_cost_pv_musd", "objective",
])
def test_finite_but_tampered_costs_cannot_validate(base_plan, field):
    assert_rejected(replaced(base_plan, (field,), base_plan[field] + 1))


def test_tools_only_plan_still_checks_reported_staff_counts():
    plan = solve_plan(labour=False)
    assert_rejected(replaced(plan, ("staff", "operators", 0), plan["staff"]["operators"][0] + 1))


@pytest.mark.parametrize("labour", [True, False])
@pytest.mark.parametrize(("path", "value"), [
    (("utilization", "LITHO", 0), 999),
    (("personnel_utilization", "operators", 0), 999),
    (("operator_load_min_per_week", 0), 0),
])
def test_finite_but_tampered_reported_loads_cannot_validate(labour, path, value):
    plan = solve_plan(labour=labour)
    assert_rejected(replaced(plan, path, value))


@pytest.mark.parametrize("zero_quarters", [(0,), (0, 3, 7), tuple(range(8))],
                         ids=["empty-first-quarter", "empty-intermittent-quarters", "empty-horizon"])
@pytest.mark.parametrize("labour", [True, False], ids=["with-labour", "tools-only"])
def test_zero_demand_preserves_maintenance_and_produces_finite_valid_plan(zero_quarters, labour):
    planning = load_planning()
    for values in planning["demand_base"].values():
        for q in zero_quarters:
            values[q] = 0
    original = copy.deepcopy(planning)
    plan = solve_plan(planning=planning, labour=labour)
    assert planning == original
    assert plan["status"] == "Optimal"
    assert check_plan(plan)["ok"], check_plan(plan)
    json.dumps(plan, allow_nan=False)
    json.dumps(check_plan(plan), allow_nan=False)
    for q in zero_quarters:
        assert plan["total_demand"][q] == 0
        assert plan["shortfall_lots_per_week"][q] == 0
        assert all(values[q] == 0 for values in plan["utilization"].values())
        assert plan["operator_load_min_per_week"][q] == 0
        assert plan["personnel_utilization"]["operators"][q] == 0
        # Installed equipment still needs scheduled maintenance and expected repairs.
        maintenance = sum(technician_min_per_tool(group) * values[q]
                          for group, values in plan["installed"].items())
        expected = maintenance / (person_minutes("technicians") * plan["staff"]["technicians"][q])
        assert expected > 0
        assert plan["personnel_utilization"]["technicians"][q] == pytest.approx(expected)
        if labour:
            assert expected <= plan["personnel_target_utilization"] + 1e-9
    if len(zero_quarters) == len(plan["quarters"]):
        assert plan["purchases"] == [] and plan["capex_musd"] == 0
        if labour:
            assert plan["hire"]["technicians"][0] == 1
            assert plan["labour_cost_musd"] > 0


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"multiplier": float("nan")}, "multiplier"),
    ({"multiplier": float("inf")}, "multiplier"),
    ({"pc_ramp_factor": -1}, "pc_ramp_factor"),
    ({"pc_ramp_factor": float("nan")}, "pc_ramp_factor"),
    ({"multiplier": 1.2, "from_quarter": 0}, "from_quarter"),
    ({"multiplier": 1.2, "from_quarter": 1.5}, "from_quarter"),
])
def test_invalid_scenario_overrides_are_rejected_before_optimization(kwargs, message):
    with pytest.raises(ValueError, match=message):
        scenario_demand(load_planning(), **kwargs)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_product_demand_is_rejected_before_optimization(value):
    planning = load_planning()
    planning["demand_base"]["Pc"][0] = value
    with pytest.raises(ValueError, match="demand"):
        solve_plan(planning=planning)


@pytest.mark.parametrize("labour", [True, False])
def test_valid_shortfall_and_regular_plans_remain_accepted_without_mutation(labour):
    for multiplier in (1, 2):
        plan = solve_plan(multiplier=multiplier, from_quarter=1, labour=labour)
        snapshot = json.dumps(plan, sort_keys=True, allow_nan=False)
        report = check_plan(plan)
        assert report["ok"], report
        assert json.dumps(plan, sort_keys=True, allow_nan=False) == snapshot
        assert all(0 <= short <= total for short, total in
                   zip(plan["shortfall_lots_per_week"], plan["total_demand"]))
        assert (plan["shortfall_lots_per_week"][0] > 0) == (multiplier == 2)
        json.dumps(report, allow_nan=False)
