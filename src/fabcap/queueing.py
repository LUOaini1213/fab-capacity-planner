"""Queueing approximation of cycle time per tool group.

* Queue time at an m-tool group: the basic G/G/m approximation in the form used by
  Hopp & Spearman (Schelasin 2013 eq. 1; Etman et al. 2011 eq. 1 attribute it to
  Sakasegawa and Whitt; see docs/sources.md [S4]-[S8]):

      CTq = ((ca2 + ce2) / 2) * (u ** (sqrt(2 (m + 1)) - 1) / (m (1 - u))) * te

  with m = 1 this is Kingman's (1961) G/G/1 formula. Schelasin reports (citing
  Whitt 1993) that this basic form loses accuracy at low utilisation and uses
  Whitt's improved form instead; the improved form is NOT implemented here.
* Effective process time te / ce2 for non-preemptive outages (PM, and - as an
  approximation - EM repairs) follows Etman et al. 2011 eq. 2:

      te = t0 + ts / Ns,   ce2 = (s0^2 + ss^2 / Ns + (Ns - 1) / Ns^2 * ts^2) / te^2

  where Ns is the mean number of jobs between outages. EM is really pre-emptive
  (it interrupts a run and scraps the lot) and PM waits for a free technician; both
  are only approximated by these non-preemptive outages of fixed mean length.
* Setups (litho) are an additive random component per job whose distribution
  is the independent-sequence setup mix from ``capacity.setup_distribution``.
* Batch formation (DIFF, full batches of k): each lot waits on average for
  (k - 1) / 2 further arrivals of its batch family (linearity of expectation),
  and batch inter-arrival SCV is ca2 / k (sum of k i.i.d. inter-arrivals). With
  several batch families merged into one diffusion queue the merged batch stream
  is not renewal, so ca2 / k is itself an approximation.

``u`` here is the fraction of calendar time a tool is not idle (processing,
setup, PM or repair). It reaches 1 at exactly the same load as the static
model's ``load / available`` utilisation, but the two numbers differ below 1.
"""
from __future__ import annotations

import math
from typing import Any

from .capacity import availability, setup_distribution, visit_types
from .fab import Fab


def gg_m_queue_time(ca2: float, ce2: float, u: float, m: int, te: float) -> float:
    """Mean time in queue (excluding service) for a G/G/m station."""
    if m < 1:
        raise ValueError("m must be >= 1")
    if u < 0:
        raise ValueError("u must be >= 0")
    if u >= 1:
        return math.inf
    if u == 0:
        return 0.0
    return ((ca2 + ce2) / 2.0) * (u ** (math.sqrt(2.0 * (m + 1)) - 1.0) / (m * (1.0 - u))) * te


def kingman(ca2: float, ce2: float, u: float, te: float) -> float:
    """Kingman's G/G/1 approximation (= gg_m_queue_time with m = 1)."""
    if u >= 1:
        return math.inf
    return ((ca2 + ce2) / 2.0) * (u / (1.0 - u)) * te


def nonpreemptive_outage(t0: float, var0: float, ns: float, ts: float, var_s: float) -> tuple[float, float]:
    """Etman et al. (2011) eq. 2: returns (te, variance of te)."""
    if ns <= 0:
        raise ValueError("Ns must be positive")
    te = t0 + ts / ns
    var_e = var0 + var_s / ns + max(ns - 1.0, 0.0) / ns**2 * ts**2
    return te, var_e


def _uniform_var(lo: float, hi: float) -> float:
    return (hi - lo) ** 2 / 12.0


def group_queue(fab: Fab, group: str, ca2: float = 1.0, em_mode: str = "renewal", include_setups: bool = True) -> dict[str, Any]:
    g = fab.groups[group]
    m, k = g.tools, g.batch_size
    week = fab.week
    types = visit_types(fab, group)
    visits = sum(types.values())
    if visits <= 0:
        return {"group": group, "u": 0.0, "ctq": 0.0, "te": 0.0, "ce2": 0.0, "t0": 0.0, "jobs_per_min": 0.0}
    lam_lot = visits / week
    lam_job = lam_lot / k
    # natural job-time mixture over steps (each step's time is deterministic)
    by_step: dict[str, float] = {}
    for (_, s), v in types.items():
        by_step[s] = by_step.get(s, 0.0) + v
    t0 = sum(v / visits * fab.steps[s].occupancy for s, v in by_step.items())
    m2 = sum(v / visits * fab.steps[s].occupancy ** 2 for s, v in by_step.items())
    var0 = m2 - t0**2
    t_nat = t0
    setup_mean = 0.0
    if include_setups and g.setup:
        dist = setup_distribution(fab, group)
        setup_mean = sum(t * p for t, p in dist)
        setup_var = sum(t * t * p for t, p in dist) - setup_mean**2
        t0 += setup_mean
        var0 += setup_var
    jobs_per_tool = lam_job / m
    # PM: every pm_period of calendar time per tool
    ns_pm = jobs_per_tool * fab.period_minutes(g.pm_period)
    te, var_e = nonpreemptive_outage(t0, var0, ns_pm, g.pm_minutes, 0.0)
    if g.em:
        mean_rep = (g.em["repair_min"] + g.em["repair_max"]) / 2.0
        var_rep = _uniform_var(g.em["repair_min"], g.em["repair_max"])
        av = availability(fab, group, em_mode)
        events_per_week = av["em_min_per_tool"] / mean_rep
        ns_em = jobs_per_tool * week / events_per_week
        te, var_e = nonpreemptive_outage(te, var_e, ns_em, mean_rep, var_rep)
    u = lam_job * te / m
    ce2 = var_e / te**2
    ca2_job = ca2 / k
    ctq = gg_m_queue_time(ca2_job, ce2, u, m, te)
    # batch formation, by family
    batch_wait: dict[tuple[str, str], float] = {}
    if k > 1:
        families: dict[tuple[str, str], float] = {}
        fam_of: dict[tuple[str, str], tuple[str, str]] = {}
        test_visits: dict[str, float] = {}
        for (p, s), v in types.items():
            rule = fab.steps[s].batch_rule
            kind = fab.products[p].kind
            if rule == "no_product_mix" and kind == "test":
                test_visits[s] = test_visits.get(s, 0.0) + v
                continue
            fam = (s, p) if rule == "no_product_mix" else (s, "*")
            families[fam] = families.get(fam, 0.0) + v
            fam_of[(p, s)] = fam
        # test lots join no-mix families in proportion to family size
        for s, tv in test_visits.items():
            fams = {f: v for f, v in families.items() if f[0] == s}
            tot = sum(fams.values())
            for f in fams:
                families[f] += tv * fams[f] / tot
        for (p, s), v in types.items():
            if (p, s) in fam_of:
                lam_f = families[fam_of[(p, s)]] / week
            else:  # test lot on a no-mix step: average over the families it can join
                fams = {f: v2 for f, v2 in families.items() if f[0] == s}
                lam_f = sum(fams.values()) / len(fams) / week
            batch_wait[(p, s)] = (k - 1) / (2.0 * lam_f)
    return {
        "group": group,
        "tools": m,
        "batch_size": k,
        "lot_arrivals_per_min": lam_lot,
        "jobs_per_min": lam_job,
        "t_natural": t_nat,
        "setup_mean": setup_mean,
        "t0": t0,
        "te": te,
        "ce2": ce2,
        "ca2_job": ca2_job,
        "u": u,
        "ctq": ctq,
        "batch_wait": batch_wait,
    }


def group_visit_wait(fab: Fab, ca2: float = 1.0, em_mode: str = "renewal", include_setups: bool = True) -> dict[str, float]:
    """Mean wait per lot visit (batch formation + queue) by group - comparable to the DES ``mean_wait_min``."""
    out = {}
    for g in fab.groups:
        q = group_queue(fab, g, ca2, em_mode, include_setups)
        types = visit_types(fab, g)
        tot = sum(types.values())
        if tot <= 0:
            out[g] = 0.0
            continue
        bw = sum(v * q["batch_wait"].get(t, 0.0) for t, v in types.items()) / tot
        out[g] = q["ctq"] + bw
    return out


def route_cycle_time(fab: Fab, ca2: float = 1.0, em_mode: str = "renewal", include_setups: bool = True) -> dict[str, Any]:
    """Approximate mean cycle time per product and the start-weighted X-factor."""
    gq = {g: group_queue(fab, g, ca2, em_mode, include_setups) for g in fab.groups}
    per_product: dict[str, Any] = {}
    for p in fab.products.values():
        if p.starts_per_week <= 0:
            continue
        ct = 0.0
        raw = 0.0
        visits = []
        for s in p.route:
            st = fab.steps[s]
            q = gq[st.group]
            outage_and_setup = q["te"] - q["t_natural"]  # mean extra time per job beyond natural occupancy
            wait = q["ctq"] + q["batch_wait"].get((p.name, s), 0.0)
            visit_ct = wait + st.occupancy + outage_and_setup
            ct += visit_ct
            raw += st.occupancy
            visits.append({"step": s, "group": st.group, "wait": wait, "ct": visit_ct})
        per_product[p.name] = {"cycle_time_min": ct, "raw_process_time_min": raw, "x_factor": ct / raw, "visits": visits}
    w = {n: fab.products[n].starts_per_week for n in per_product}
    tot = sum(w.values())
    ct_avg = sum(per_product[n]["cycle_time_min"] * w[n] for n in w) / tot
    raw_avg = sum(per_product[n]["raw_process_time_min"] * w[n] for n in w) / tot
    return {
        "ca2": ca2,
        "em_mode": em_mode,
        "include_setups": include_setups,
        "groups": {g: {k: v for k, v in q.items() if k != "batch_wait"} for g, q in gq.items()},
        "products": per_product,
        "cycle_time_min": ct_avg,
        "raw_process_time_min": raw_avg,
        "x_factor": ct_avg / raw_avg,
    }


def x_factor_curve(fab: Fab, bottleneck_utils: list[float], ca2: float = 1.0, em_mode: str = "renewal",
                   include_setups: bool = True) -> list[dict[str, Any]]:
    """Scale starts so that the static bottleneck utilisation hits each target; return X-factor."""
    from .capacity import capacity_table

    base = capacity_table(fab, include_setups=include_setups, em_mode=em_mode)
    out = []
    for target in bottleneck_utils:
        scale = target / base["bottleneck_utilization"]
        f = fab.scaled(scale)
        rc = route_cycle_time(f, ca2, em_mode, include_setups)
        out.append({
            "bottleneck_utilization": target,
            "starts_per_week": f.total_starts(),
            "cycle_time_min": rc["cycle_time_min"],
            "x_factor": rc["x_factor"],
            "group_u": {g: q["u"] for g, q in rc["groups"].items()},
        })
    return out
