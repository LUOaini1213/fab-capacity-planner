import pytest

from fabcap.capacity import capacity_table
from fabcap.des import FabSim, Lot, Machine, SimConfig, simulate

SHORT = dict(warmup_weeks=3, run_weeks=12)
BARE = dict(operators=False, technician=False, transport=False)


def test_deterministic_limit_matches_static_capacity(mini):
    """No setups, no failures, no personnel: saturated DES output = static max starts (Kempf PM)."""
    cfg = SimConfig(release="conwip", conwip_wip=40, setups=False, failures=False, **BARE, **SHORT)
    r = simulate(mini, cfg, 11)
    static = capacity_table(mini.with_group("IMP", em=None), include_setups=False)["max_starts_per_week"]
    assert static == pytest.approx(92.0)
    assert r["throughput_per_week"] == pytest.approx(static, rel=0.01)
    assert r["scrapped_per_week"] == 0


def test_same_seed_same_result_different_seed_differs(mini):
    cfg = SimConfig(**BARE, **SHORT)
    a, b, c = simulate(mini, cfg, 5), simulate(mini, cfg, 5), simulate(mini, cfg, 6)
    assert a == b
    assert a["cycle_time_min"] != c["cycle_time_min"]


def test_lot_conservation_and_failures(mini):
    r = simulate(mini, SimConfig(**SHORT), 3)
    t = r["totals"]
    assert t["released"] == t["completed"] + t["scrapped"] + t["wip_end"]
    assert t["scrapped"] > 0
    assert r["groups"]["IMP"]["failures_per_week"] * 12 == pytest.approx(r["scrapped_per_week"] * 12)
    assert r["groups"]["DIFF"]["failures_per_week"] == 0


def test_littles_law_on_stable_run(mini):
    r = simulate(mini, SimConfig(rule="FIFO", **BARE, warmup_weeks=5, run_weeks=40), 21)
    assert not r["unstable"]
    lam = r["throughput_per_week"] / 10080.0  # completed lots per minute
    # scrapped lots also occupy WIP for part of their life; bound instead of equality
    assert lam * r["cycle_time_min"] <= r["wip_mean"] <= (lam + r["scrapped_per_week"] / 10080.0) * r["cycle_time_min"] * 1.05


def _sim(mini, **kw):
    return FabSim(mini, SimConfig(start_scale=1e-9, **BARE, **SHORT, **kw), 1)


def _lot(i, product, step_idx, kind="product", arrive=0.0):
    route = ("S1", "S2", "S3", "S4", "S5", "S6")
    lot = Lot(i, product, kind, route, 0.0, 1e9, [0.0] * 7)
    lot.idx = step_idx
    lot.arrive = arrive
    return lot


def test_s5_batches_never_mix_products_and_hold_one_test_lot(mini):
    sim = _sim(mini)
    ma = next(m for m in sim.machines if m.name == "Ma")
    q = [_lot(1, "Pa", 4, arrive=1), _lot(2, "Pb", 4, arrive=2), _lot(3, "TW", 4, "test", arrive=3),
         _lot(4, "TW", 4, "test", arrive=4), _lot(5, "Pa", 4, arrive=5)]
    sim.queues["DIFF"] = q
    batch = sim._select(ma)
    assert [l.id for l in batch] == [1, 3, 5]


def test_s1_batches_may_mix_products_but_not_steps(mini):
    sim = _sim(mini)
    ma = next(m for m in sim.machines if m.name == "Ma")
    sim.queues["DIFF"] = [_lot(1, "Pa", 0, arrive=1), _lot(2, "Pb", 4, arrive=2), _lot(3, "Pb", 0, arrive=3),
                          _lot(4, "Pa", 4, arrive=4)]
    assert sim._select(ma) is None  # only two S1 and two S5 lots: no full batch
    sim.queues["DIFF"].append(_lot(5, "Pb", 0, arrive=5))
    assert [l.id for l in sim._select(ma)] == [1, 3, 5]


def test_test_lot_avoids_machine_it_used_before(mini):
    sim = _sim(mini)
    mc = next(m for m in sim.machines if m.name == "Mc")
    md = next(m for m in sim.machines if m.name == "Md")
    tw = _lot(1, "TW", 3, "test")
    tw.hist["IMP"] = "Mc"
    sim.queues["IMP"] = [tw]
    assert sim._select(mc) is None
    assert sim._select(md) == [tw]


def test_dispatch_rules_order(mini):
    sim = _sim(mini, rule="SA")
    me = next(m for m in sim.machines if m.name == "Me")
    me.last_type = ("Pb", "S6")
    a = _lot(1, "Pa", 2, arrive=1)   # product+step change: 12
    b = _lot(2, "Pb", 2, arrive=2)   # step change: 10
    c = _lot(3, "Pa", 5, arrive=3)   # product change: 5
    sim.queues["LITHO"] = [a, b, c]
    assert sim._select(me) == [c]
    fifo = _sim(mini, rule="FIFO")
    me2 = next(m for m in fifo.machines if m.name == "Me")
    fifo.queues["LITHO"] = [c, b, a]
    assert fifo._select(me2) == [a]
    cr = _sim(mini, rule="CR")
    me3 = next(m for m in cr.machines if m.name == "Me")
    late = _lot(4, "Pa", 2, arrive=9)
    late.due = 50.0
    cr.queues["LITHO"] = [a, late]
    assert cr._select(me3) == [late]


def test_pm_window_scheduling(mini):
    sim = _sim(mini)
    me = next(m for m in sim.machines if m.name == "Me")
    me.pm_due = 0.0
    sim.env.run(until=700)  # PM done early in the first shift
    assert me.pm_due >= 720
    m = Machine("LITHO", 0, "X")
    m.pm_due = 720.0
    sim.env._now = 1000.0
    sim._schedule_next_pm(m)
    assert m.pm_due == 1440.0 and m.pm_close == 2160.0
    m.pm_due = 720.0
    sim.env._now = 1200.0  # late PM: the 6-hour gap rule binds
    sim._schedule_next_pm(m)
    assert m.pm_due == 1560.0 and m.pm_close == 2160.0


def _bare_window_sim(mini, **kw):
    """Machines only, no PM, statistics window starting at t = 0, no automatic releases."""
    return FabSim(mini, SimConfig(start_scale=1e-9, warmup_weeks=0, run_weeks=1, pm=False, **BARE, **kw), 1)


def test_failure_mid_run_scraps_lot_and_books_partial_run(mini):
    sim = _bare_window_sim(mini)
    mc = next(m for m in sim.machines if m.name == "Mc")
    md = next(m for m in sim.machines if m.name == "Md")
    mc.next_fail, md.next_fail = 22.0, float("inf")
    sim.queues["IMP"] = [_lot(1, "Pa", 1, arrive=0.0)]  # S2: load 15, run 30
    sim.env.run(until=2000)
    assert sim.scrapped == 1
    assert sim.state["IMP"]["failed_run"] == pytest.approx(7.0)  # run 15 -> 22
    assert 360 <= sim.state["IMP"]["repair"] <= 480
    assert sim.state["IMP"]["run"] == 0.0
    assert mc.next_fail >= 22.0 + 360 + 1440


def test_litho_setups_follow_sequence_and_time_is_booked_by_state(mini):
    sim = _bare_window_sim(mini)
    sim.queues["LITHO"] = [_lot(1, "Pa", 2, arrive=1.0), _lot(2, "Pa", 2, arrive=2.0), _lot(3, "Pb", 2, arrive=3.0),
                           _lot(4, "Pb", 5, arrive=4.0)]
    sim.env.run(until=1000)
    # Pa/S3 -> Pa/S3 (0) -> Pb/S3 (product change 5) -> Pb/S6 (step change 10)
    assert sim.setups_window == 2
    assert sim.setup_min_window == pytest.approx(15.0)
    st = sim.state["LITHO"]
    assert st["run"] == pytest.approx(3 * 55 + 10)
    assert st["load"] == pytest.approx(40.0) and st["unload"] == pytest.approx(40.0)
    assert st["setup"] == pytest.approx(15.0)


def test_stocker_peak_excludes_lots_on_the_machine(mini):
    sim = _bare_window_sim(mini)
    for i in range(3):
        sim.env.process(sim._move(_lot(i + 1, "Pa", 2, arrive=0.0), None, "LITHO"))
    sim.env.run(until=1000)
    assert sim.max_stocker["LITHO"] == 3  # all three wait before the first load starts
    assert sim.in_proc["LITHO"] == 0 and sim.in_cell["LITHO"] == 0


def test_pm_postponed_while_technician_busy(mini):
    """Ma takes the technician for its PM at t=0; Me's PM is due too but it keeps working."""
    sim = FabSim(mini, SimConfig(start_scale=1e-9, warmup_weeks=0, run_weeks=1, operators=False, transport=False), 1)
    sim.queues["LITHO"] = [_lot(1, "Pa", 2, arrive=0.0)]
    sim.env.run(until=70)
    assert sim.tech.res.count == 1 and sim.tech.loc == sim.cell["DIFF"]  # Ma's 75-min PM in progress
    assert sim.state["LITHO"]["run"] == pytest.approx(55.0)  # Me ran its lot instead of idling
    assert sim.state["LITHO"]["pm"] == 0.0
    sim.env.run(until=719)
    assert sim.state["LITHO"]["pm"] == pytest.approx(30.0)  # done later in the same shift


def test_conwip_keeps_wip_constant_and_replaces_scrap(mini):
    r = simulate(mini, SimConfig(release="conwip", conwip_wip=17, **BARE, **SHORT), 4)
    t = r["totals"]
    assert t["wip_end"] == 17
    assert t["scrapped"] > 0
    assert t["released"] == t["completed"] + t["scrapped"] + 17


def test_unstable_flag(mini):
    over = simulate(mini, SimConfig(start_scale=1.3, **BARE, **SHORT), 8)
    under = simulate(mini, SimConfig(start_scale=0.6, **BARE, **SHORT), 8)
    assert over["unstable"] is True and under["unstable"] is False
    assert over["wip_by_third"][2] > 1.5 * over["wip_by_third"][0]


def test_operators_reduce_capacity(mini):
    bare = simulate(mini, SimConfig(release="conwip", conwip_wip=30, **BARE, **SHORT), 2)
    full = simulate(mini, SimConfig(release="conwip", conwip_wip=30, **SHORT), 2)
    assert full["throughput_per_week"] < bare["throughput_per_week"] - 10


def test_config_validation(mini):
    with pytest.raises(ValueError):
        FabSim(mini, SimConfig(rule="LIFO"), 1)
    with pytest.raises(ValueError):
        FabSim(mini, SimConfig(release="weekly"), 1)
    with pytest.raises(ValueError):
        FabSim(mini, SimConfig(failure_clock="wall"), 1)
    with pytest.raises(ValueError):
        FabSim(mini, SimConfig(tbf_scale=0.0), 1)


def test_run_clock_ages_only_while_running(mini):
    """Run clock: 40 run-minutes left survive a long idle spell and a 30-min run, then fail 10 min into the next run."""
    sim = _bare_window_sim(mini, failure_clock="run")
    mc = next(m for m in sim.machines if m.name == "Mc")
    md = next(m for m in sim.machines if m.name == "Md")
    mc.next_fail, md.next_fail = 40.0, float("inf")
    sim.env.run(until=1000)  # idle: nothing ages
    assert mc.next_fail == 40.0
    sim.queues["IMP"] = [_lot(1, "Pa", 1, arrive=1000.0)]  # S2: load 15, run 30
    sim._notify("IMP")
    sim.env.run(until=1100)
    assert sim.scrapped == 0 and mc.next_fail == pytest.approx(10.0)
    assert sim.state["IMP"]["run"] == pytest.approx(30.0)
    sim.queues["IMP"] = [_lot(2, "Pa", 3, arrive=1100.0)]  # S4: load 15, run 50 -> fails after 10
    sim._notify("IMP")
    sim.env.run(until=1200)
    assert sim.scrapped == 1
    assert sim.state["IMP"]["failed_run"] == pytest.approx(10.0)
    sim.env.run(until=2000)  # repair (360-480 min) done
    assert 1440 * 1.0 <= mc.next_fail <= 4560  # fresh draw, in run minutes (scale 1)


def test_calendar_clock_expires_while_idle(mini):
    sim = _bare_window_sim(mini)
    mc = next(m for m in sim.machines if m.name == "Mc")
    md = next(m for m in sim.machines if m.name == "Md")
    mc.next_fail, md.next_fail = 40.0, float("inf")
    sim.env.run(until=1000)
    sim.queues["IMP"] = [_lot(1, "Pa", 1, arrive=1000.0)]
    sim._notify("IMP")
    sim.env.run(until=1100)
    assert sim.scrapped == 1  # clock expired at t=40 while idle: fails at the start of the next run
    assert sim.state["IMP"]["failed_run"] == 0.0


def test_tbf_scale_scales_failure_draws(mini):
    a = FabSim(mini, SimConfig(**BARE, **SHORT), 7)
    b = FabSim(mini, SimConfig(tbf_scale=0.5, **BARE, **SHORT), 7)
    for ma, mb in zip(a.machines, b.machines):
        if ma.group == "IMP":
            assert mb.next_fail == pytest.approx(ma.next_fail * 0.5, abs=1e-5)
    fewer = simulate(mini, SimConfig(tbf_scale=1.54, **BARE, **SHORT), 7)
    more = simulate(mini, SimConfig(**BARE, **SHORT), 7)
    assert fewer["groups"]["IMP"]["failures_per_week"] < more["groups"]["IMP"]["failures_per_week"]


def test_operator_held_from_setup_through_load(mini):
    """S1: a setup is done immediately prior to the run - nobody may take the operator in between."""
    sim = FabSim(mini, SimConfig(start_scale=1e-9, warmup_weeks=0, run_weeks=1, pm=False, technician=False,
                                 transport=False, failures=False), 1)
    me = next(m for m in sim.machines if m.name == "Me")
    po2 = next(p for p in sim.people if p.name == "PO2")
    sim.ops_for["LITHO"] = [po2]
    got = {}

    def rival():
        yield sim.env.timeout(5)
        req = po2.res.request(priority=2)
        yield req
        got["t"] = sim.env.now
        po2.res.release(req)

    sim.env.process(sim._with_operator(me, [(12.0, "setup"), (10.0, "load")]))
    sim.env.process(rival())
    sim.env.run(until=100)
    # 1 min travel (IMP cell -> LITHO cell), 12 setup, 10 load: the rival gets PO2 only at t = 23
    assert got["t"] == pytest.approx(23.0)
    assert sim.state["LITHO"]["setup"] == pytest.approx(12.0) and sim.state["LITHO"]["load"] == pytest.approx(10.0)


def test_process_passes_setup_and_load_as_one_operator_hold(mini, monkeypatch):
    sim = _bare_window_sim(mini)
    calls = []
    orig = sim._with_operator

    def spy(m, segments):
        calls.append([s for _, s in segments])
        yield from orig(m, segments)

    monkeypatch.setattr(sim, "_with_operator", spy)
    me = next(m for m in sim.machines if m.name == "Me")
    me.last_type = ("Pb", "S6")
    sim.queues["LITHO"] = [_lot(1, "Pa", 2, arrive=0.0)]
    sim.env.run(until=200)
    assert calls[:2] == [["setup", "load"], ["unload"]]  # then the lot moves on to IMP (load, unload)


def test_two_technicians_share_work(mini):
    f = mini.copy()
    f.personnel = dict(f.personnel)
    f.personnel["technicians"] = {"MT1": {}, "MT2": {}}
    sim = FabSim(f, SimConfig(start_scale=1e-9, warmup_weeks=0, run_weeks=1, operators=False, transport=False), 1)
    assert [t.name for t in sim.techs] == ["MT1", "MT2"]
    sim.env.run(until=10)
    # at t=0 every machine's PM is due: with two technicians two PMs run at once
    assert sum(t.res.count for t in sim.techs) == 2
    one = FabSim(mini, SimConfig(start_scale=1e-9, warmup_weeks=0, run_weeks=1, operators=False, transport=False), 1)
    one.env.run(until=10)
    assert len(one.techs) == 1 and one.tech.res.count == 1
