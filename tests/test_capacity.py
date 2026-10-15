import math

import pytest

from fabcap import capacity as cap


def test_kempf_published_capacity_table_is_reproduced(mini):
    """Kempf's section 1.9: required/available minutes per shift and utilisation."""
    t = cap.capacity_table(mini, include_setups=False, em_mode="spec")
    shift = cap.per_shift(t, mini)
    expected = {"DIFF": (1200, 1365, 0.879), "IMP": (840, 1080, 0.778), "LITHO": (630, 690, 0.913)}
    for g, (req, avail, u) in expected.items():
        assert shift[g]["required_min_per_shift"] == pytest.approx(req, abs=1e-9)
        assert shift[g]["available_min_per_shift"] == pytest.approx(avail, abs=1e-9)
        assert round(shift[g]["utilization"], 3) == u
    assert t["bottleneck"] == "LITHO"
    assert t["max_starts_per_week"] == pytest.approx(84 * 690 / 630)


def test_toy_loads_availability_and_utilisation(toy):
    t = cap.capacity_table(toy, include_setups=True, em_mode="spec")
    x, y = t["groups"]["X"], t["groups"]["Y"]
    assert x["jobs_per_week"] == pytest.approx(42.0)
    assert x["load_min_per_week"] == pytest.approx(5880.0)
    assert x["available_min_per_week"] == pytest.approx(3 * (10080 - 7 * 60))
    assert x["utilization"] == pytest.approx(0.2028985507246377)
    assert y["setup_min_per_week"] == pytest.approx(42 * 80 / 49)
    assert y["load_min_per_week"] == pytest.approx(1328.5714285714287)
    assert y["em_min_per_tool"] == pytest.approx(900.0)
    assert y["utilization"] == pytest.approx(0.15412661584355322)
    assert t["bottleneck"] == "X"
    assert t["max_starts_per_week"] == pytest.approx(207.0)


def test_renewal_em_accounting(toy, mini):
    y = cap.availability(toy, "Y", "renewal")
    assert y["em_min_per_tool"] == pytest.approx(10080 * 300 / 2300)
    t = cap.capacity_table(toy, em_mode="renewal")
    assert t["groups"]["Y"]["utilization"] == pytest.approx(0.16191788288015505)
    imp = cap.availability(mini, "IMP", "renewal")
    assert imp["em_min_per_tool"] == pytest.approx(10080 * 420 / 3420)
    assert cap.availability(mini, "IMP", "spec")["em_min_per_tool"] == pytest.approx(840.0)


def test_setup_rules_and_expectation(toy, mini):
    g = mini.groups["LITHO"]
    assert cap.setup_between(g, ("Pa", "S3"), ("Pa", "S3")) == 0
    assert cap.setup_between(g, ("Pa", "S3"), ("Pa", "S6")) == 10
    assert cap.setup_between(g, ("Pa", "S3"), ("Pb", "S3")) == 5
    assert cap.setup_between(g, ("Pa", "S3"), ("TW", "S6")) == 12
    assert cap.expected_setup_per_job(toy, "Y") == pytest.approx(80 / 49)
    dist = dict(cap.setup_distribution(toy, "Y"))
    assert dist[4.0] == pytest.approx(20 / 49)
    assert dist[0.0] == pytest.approx(29 / 49)
    assert cap.expected_setup_per_job(toy, "X") == 0.0


def test_max_starts_puts_bottleneck_exactly_at_cap(mini):
    for c in (0.85, 0.6):
        t = cap.capacity_table(mini, util_cap=c)
        scaled = mini.scaled(t["max_starts_per_week"] / mini.total_starts())
        t2 = cap.capacity_table(scaled)
        assert t2["bottleneck_utilization"] == pytest.approx(c)
        assert t2["feasible"] is True
    over = cap.capacity_table(mini.scaled(1.01), util_cap=1.0)
    assert over["feasible"] is False
    with pytest.raises(ValueError):
        cap.capacity_table(mini, util_cap=1.2)


def test_mix_sensitivity_setups_peak_near_even_mix(mini):
    rows = cap.mix_sensitivity_pa_share(mini, [0.0, 0.3, 0.5, 0.8, 1.0])
    s = [r["litho_setup_min_per_job"] for r in rows]
    assert s[2] == max(s)
    assert s[0] < s[1] < s[2] and s[2] > s[3] > s[4]
    assert rows[0]["max_starts_per_week"] > rows[2]["max_starts_per_week"]


def test_new_product_share_moves_bottleneck():
    from fabcap.fab import planning_fab
    pf = planning_fab().with_starts({"Pa": 51, "Pb": 30, "TW": 3, "Pc": 0})
    rows = cap.mix_sensitivity_new_product(pf, "Pc", [0.0, 1.0], total=84.0, fixed={"TW": 3.0})
    assert rows[0]["max_starts_per_week"] > rows[1]["max_starts_per_week"]
    assert rows[1]["bottleneck"] in ("LITHO", "IMP")


def test_kempf_personnel_table_is_reproduced(mini):
    """Kempf 1.9: operators 840 of 1080 min/shift (77.8 %), technician 465 of 600 (77.5 %)."""
    pl = cap.personnel_load(mini, em_mode="spec", include_setups=False)
    assert pl["operator_load_min_per_week"] / 14 == pytest.approx(840.0)
    assert pl["operator_available_min_per_week"] / 14 == pytest.approx(1080.0)
    assert pl["technician_load_min_per_week"] / 14 == pytest.approx(465.0)
    assert pl["technician_available_min_per_week"] / 14 == pytest.approx(600.0)
    assert round(pl["technician_utilization"], 3) == 0.775
    renewal = cap.personnel_load(mini)
    assert renewal["technician_utilization"] > pl["technician_utilization"]
    assert renewal["operator_utilization"] > pl["operator_utilization"]  # setups add operator work


def test_raw_process_time(mini):
    assert cap.raw_process_time(mini, "Pa") == 285 + 60 + 75 + 80 + 315 + 30


def test_bad_em_mode(mini):
    with pytest.raises(ValueError):
        cap.availability(mini, "IMP", "weekly")
    with pytest.raises(ValueError):
        cap.em_minutes_per_tool(mini, "IMP", "renewal", clock="wall")
    with pytest.raises(ValueError):
        cap.des_tbf_scale(mini, "IMP", "renewal", clock="wall")
    with pytest.raises(ValueError):
        cap.em_events_per_tool_week(mini, "IMP", "weekly")


def test_failure_model_rates_and_des_scales(mini):
    """Run minutes per implanter-week at 84 starts: 84 x (30 + 50) / 2 = 3360."""
    assert cap.run_minutes_per_tool(mini, "IMP") == pytest.approx(3360.0)
    assert cap.em_events_per_tool_week(mini, "IMP", "renewal") == pytest.approx(10080 / 3420)
    assert cap.em_events_per_tool_week(mini, "IMP", "spec") == 2.0
    assert cap.em_events_per_tool_week(mini, "LITHO", "spec") == 0.0
    assert cap.des_tbf_scale(mini, "IMP", "renewal", "calendar") == 1.0
    assert cap.des_tbf_scale(mini, "IMP", "spec", "calendar") == pytest.approx((5040 - 420) / 3000)  # 1.54
    assert cap.des_tbf_scale(mini, "IMP", "renewal", "run") == pytest.approx(3360 * 3420 / 10080 / 3000)  # 0.38
    assert cap.des_tbf_scale(mini, "IMP", "spec", "run") == pytest.approx(1680 / 3000)  # 0.56
    # calendar clock: same as the availability accounting; run clock at the reference load: the same
    assert cap.em_minutes_per_tool(mini, "IMP", "renewal") == pytest.approx(10080 * 420 / 3420)
    assert cap.em_minutes_per_tool(mini, "IMP", "spec", "run") == pytest.approx(840.0)


def test_run_clock_failures_follow_run_time_not_tool_count(mini):
    three = mini.with_tools("IMP", 3)
    cal = 3 * cap.em_minutes_per_tool(three, "IMP", "renewal", "calendar", ref=mini)
    run = 3 * cap.em_minutes_per_tool(three, "IMP", "renewal", "run", ref=mini)
    base = 2 * cap.em_minutes_per_tool(mini, "IMP", "renewal", "run", ref=mini)
    assert cal == pytest.approx(1.5 * base)  # calendar: a third implanter fails as often as the others
    assert run == pytest.approx(base)        # run clock: same lots, same run time, same failures
    half = cap.em_minutes_per_tool(mini.scaled(0.5), "IMP", "spec", "run", ref=mini)
    assert half == pytest.approx(420.0)      # half the load, half the failures


def test_technician_load_after_third_implanter_by_failure_model(mini):
    """PM 4830 min/week + repairs; available 600 x 14 = 8400."""
    three = mini.with_tools("IMP", 3)
    u = {k: cap.personnel_load(three, em_mode=m, clock=c, ref=mini)["technician_utilization"]
         for k, (c, m) in cap.FAILURE_MODELS.items()}
    assert u["calendar_dist"] == pytest.approx((4830 + 1680 + 3 * 10080 * 420 / 3420) / 8400)  # 121.7 %
    assert u["run_dist"] == pytest.approx((4830 + 1680 + 2 * 10080 * 420 / 3420) / 8400)       # 107.0 %
    assert u["calendar_spec"] == pytest.approx((4830 + 1680 + 3 * 840) / 8400)                   # 107.5 %
    assert u["run_spec"] == pytest.approx((4830 + 1680 + 2 * 840) / 8400)                        # 97.5 %
