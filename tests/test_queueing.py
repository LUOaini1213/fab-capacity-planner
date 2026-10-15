import math

import pytest

from fabcap import queueing as qm


def test_gg1_equals_kingman():
    assert qm.kingman(0.5, 1.5, 0.8, 10) == pytest.approx(40.0)
    assert qm.gg_m_queue_time(0.5, 1.5, 0.8, 1, 10) == pytest.approx(40.0)


def test_gg2_sakasegawa_exponent():
    assert qm.gg_m_queue_time(1.0, 0.5, 0.75, 2, 20) == pytest.approx(19.77078154881706)


def test_queue_time_edges():
    assert qm.gg_m_queue_time(1, 1, 0.0, 3, 5) == 0.0
    assert math.isinf(qm.gg_m_queue_time(1, 1, 1.0, 3, 5))
    with pytest.raises(ValueError):
        qm.gg_m_queue_time(1, 1, 0.5, 0, 5)


def test_nonpreemptive_outage_eq2():
    te, var = qm.nonpreemptive_outage(50.0, 16.0, 8.0, 40.0, 100.0)
    assert te == pytest.approx(55.0)
    assert var == pytest.approx(203.5)
    with pytest.raises(ValueError):
        qm.nonpreemptive_outage(1, 0, 0, 1, 0)


def test_group_effective_time_and_batch_wait(toy):
    q = qm.group_queue(toy, "X", ca2=1.0)
    # natural job time: mixture of X1 (120) and X2 (160) with equal weights
    assert q["t_natural"] == pytest.approx(140.0)
    jobs_per_tool = (84 / 2) / 10080 / 3
    ns = jobs_per_tool * 1440
    assert q["te"] == pytest.approx(140.0 + 60.0 / ns)
    assert q["u"] == pytest.approx(42 * 140 / (3 * 10080) + 60 / 1440)
    assert q["ca2_job"] == pytest.approx(0.5)
    # X1 batches mix products: family rate 42/week; X2 no mixing: A at 30/week, B at 12/week
    bw = q["batch_wait"]
    assert bw[("A", "X1")] == pytest.approx(10080 / (2 * 42))
    assert bw[("A", "X2")] == pytest.approx(10080 / (2 * 30))
    assert bw[("B", "X2")] == pytest.approx(10080 / (2 * 12))


def test_setup_adds_mean_and_variance(toy):
    with_s = qm.group_queue(toy, "Y", include_setups=True)
    no_s = qm.group_queue(toy, "Y", include_setups=False)
    assert with_s["setup_mean"] == pytest.approx(80 / 49)
    assert with_s["t0"] - no_s["t0"] == pytest.approx(80 / 49)
    var_with = with_s["ce2"] * with_s["te"] ** 2
    var_no = no_s["ce2"] * no_s["te"] ** 2
    # setup variance of the {0, 4} mix with P(4) = 20/49 adds exactly 16 * p * (1 - p)
    p = 20 / 49
    assert var_with - var_no == pytest.approx(16 * p * (1 - p), rel=1e-6)


def test_saturation_point_matches_static_model(mini):
    """Queueing u (non-idle fraction) reaches 1 exactly where static load/available does."""
    from fabcap.capacity import capacity_table
    t = capacity_table(mini)
    f = mini.scaled(t["max_starts_per_week"] / mini.total_starts())
    q = qm.group_queue(f, t["bottleneck"])
    assert q["u"] == pytest.approx(1.0, abs=1e-9)


def test_xfactor_curve_rises_steeply_near_saturation(mini):
    c = qm.x_factor_curve(mini, [0.7, 0.8, 0.9, 0.95])
    xs = [p["x_factor"] for p in c]
    assert xs[0] < xs[1] < xs[2] < xs[3]
    assert xs[3] - xs[2] > xs[1] - xs[0]


def test_route_cycle_time_is_infinite_when_overloaded(mini):
    r = qm.route_cycle_time(mini)  # litho > 100 % with independent-sequence setups
    assert math.isinf(r["cycle_time_min"])
    ok = qm.route_cycle_time(mini.scaled(0.6))
    assert ok["x_factor"] > 1.0
    assert ok["products"]["Pa"]["raw_process_time_min"] == 845
