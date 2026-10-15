"""All result-producing experiments. Each ``run_*`` function writes files under results/.

Entry point: ``scripts/reproduce.py``.
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from typing import Any

from . import capacity as cap
from . import queueing as qm
from .des import SimConfig, simulate
from .fab import Fab, load_fab, load_planning, planning_fab
from .io import write_csv, write_json
from .planning import solve_plan
from .stats import mean_ci
from .validate import check_plan
from .whatif import OPTIONS, des_option_configs, option_costs, rank, static_whatif

KEMPF_PUBLISHED = {"DIFF": 0.879, "IMP": 0.778, "LITHO": 0.913}  # Kempf 1.9, rounded to 0.1 %
KEMPF_PER_SHIFT = {"DIFF": (1200, 1365), "IMP": (840, 1080), "LITHO": (630, 690)}


class Settings:
    def __init__(self, quick: bool = False):
        self.quick = quick
        self.reps = 3 if quick else 10
        self.warmup = 5.0 if quick else 20.0
        self.run = 20.0 if quick else 100.0
        self.workers = int(os.environ.get("FABCAP_WORKERS", "4"))

    def seeds(self) -> list[int]:
        return [101 + i for i in range(self.reps)]

    def cfg(self, **kw: Any) -> SimConfig:
        return SimConfig(warmup_weeks=self.warmup, run_weeks=self.run, **kw)


MACHINES_ONLY = dict(operators=False, technician=False, transport=False)


def _sim_task(args: tuple[Fab, SimConfig, int]) -> dict[str, Any]:
    fab, cfg, seed = args
    return simulate(fab, cfg, seed)


def run_many(tasks: list[tuple[Fab, SimConfig, int]], workers: int) -> list[dict[str, Any]]:
    if workers <= 1:
        return [_sim_task(t) for t in tasks]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_sim_task, tasks, chunksize=1))


def summarise(runs: list[dict[str, Any]]) -> dict[str, Any]:
    def ci(key_fn):
        return mean_ci([key_fn(r) for r in runs])

    groups = runs[0]["groups"].keys()
    return {
        "config": runs[0]["config"],
        "seeds": [r["seed"] for r in runs],
        "unstable_runs": sum(1 for r in runs if r["unstable"]),
        "throughput_per_week": ci(lambda r: r["throughput_per_week"]),
        "throughput_by_seed": [r["throughput_per_week"] for r in runs],
        "throughput_range": [min(r["throughput_per_week"] for r in runs), max(r["throughput_per_week"] for r in runs)],
        "good_plus_scrapped_per_week": ci(lambda r: r["throughput_per_week"] + r["scrapped_per_week"]),
        "failures_per_imp_machine_week": ci(lambda r: r["groups"]["IMP"]["failures_per_week"] / r["groups"]["IMP"]["tools"]),
        "starts_per_week": ci(lambda r: r["starts_per_week"]),
        "scrapped_per_week": ci(lambda r: r["scrapped_per_week"]),
        "cycle_time_min": ci(lambda r: r["cycle_time_min"]),
        "x_factor": ci(lambda r: r["x_factor"]),
        "wip_mean": ci(lambda r: r["wip_mean"]),
        "litho_setup_min_per_week": ci(lambda r: r["litho_setup_min_per_week"]),
        "groups": {
            g: {
                "productive_fraction": ci(lambda r, g=g: r["groups"][g]["productive_fraction"]),
                "non_idle_fraction": ci(lambda r, g=g: r["groups"][g]["non_idle_fraction"]),
                "mean_wait_min": ci(lambda r, g=g: r["groups"][g]["mean_wait_min"]),
                "failures_per_week": ci(lambda r, g=g: r["groups"][g]["failures_per_week"]),
                "max_stocker_lots": max(r["groups"][g]["max_stocker_lots"] for r in runs),
                "state_fraction": {
                    s: ci(lambda r, g=g, s=s: r["groups"][g]["state_fraction"][s]).get("mean")
                    for s in runs[0]["groups"][g]["state_fraction"]
                },
            }
            for g in groups
        },
    }


def _batch(settings: Settings, cases: dict[str, tuple[Fab, SimConfig]]) -> dict[str, dict[str, Any]]:
    tasks, keys = [], []
    for name, (fab, cfg) in cases.items():
        for s in settings.seeds():
            tasks.append((fab, cfg, s))
            keys.append(name)
    runs = run_many(tasks, settings.workers)
    out: dict[str, list] = {}
    for k, r in zip(keys, runs):
        out.setdefault(k, []).append(r)
    return {k: summarise(v) for k, v in out.items()}


# ============================================================================ static
def run_capacity() -> dict[str, Any]:
    fab = load_fab()
    kempf = cap.capacity_table(fab, include_setups=False, em_mode="spec")
    shift = cap.per_shift(kempf, fab)
    reproduction = {
        g: {
            "model_required_min_per_shift": shift[g]["required_min_per_shift"],
            "model_available_min_per_shift": shift[g]["available_min_per_shift"],
            "model_utilization": shift[g]["utilization"],
            "kempf_required_min_per_shift": KEMPF_PER_SHIFT[g][0],
            "kempf_available_min_per_shift": KEMPF_PER_SHIFT[g][1],
            "kempf_utilization_published": KEMPF_PUBLISHED[g],
            "matches_to_0_1_pct": round(shift[g]["utilization"], 3) == KEMPF_PUBLISHED[g],
        }
        for g in fab.groups
    }
    variants = {
        f"{em}_{'setups' if su else 'nosetups'}": cap.capacity_table(fab, include_setups=su, em_mode=em)
        for em in cap.EM_MODES
        for su in (False, True)
    }
    main = variants["renewal_setups"]
    at_cap = cap.capacity_table(fab, include_setups=True, em_mode="renewal", util_cap=0.85)
    out = {
        "fab": fab.name,
        "kempf_reproduction": reproduction,
        "variants": variants,
        "main": {
            "description": "renewal EM accounting + expected litho setups (independent sequence)",
            "bottleneck": main["bottleneck"],
            "bottleneck_utilization": main["bottleneck_utilization"],
            "max_starts_per_week": main["max_starts_per_week"],
            "max_starts_per_week_at_85pct_cap": at_cap["max_starts_per_week"],
            "utilization": {g: r["utilization"] for g, r in main["groups"].items()},
        },
        "personnel_at_84_starts": cap.personnel_load(fab),
        "litho_setup_distribution": cap.setup_distribution(fab, "LITHO"),
        "litho_expected_setup_min_per_job": cap.expected_setup_per_job(fab, "LITHO"),
    }
    write_json("capacity.json", out)
    write_csv("capacity_by_group.csv",
              ["variant", "group", "tools", "load_min_per_week", "available_min_per_week", "utilization", "max_starts_per_week"],
              [[v, g, r["tools"], r["load_min_per_week"], r["available_min_per_week"], r["utilization"], r["max_starts_per_week"]]
               for v, t in variants.items() for g, r in t["groups"].items()])
    # mix sensitivity
    shares = [i / 10 for i in range(11)]
    pa = cap.mix_sensitivity_pa_share(fab, shares)
    pf = planning_fab()
    pf = pf.with_starts({"Pa": 51, "Pb": 30, "TW": 3, "Pc": 0})
    pc = cap.mix_sensitivity_new_product(pf, "Pc", [i / 10 for i in range(0, 11)], total=84.0, fixed={"TW": 3.0})
    write_json("capacity_mix_sensitivity.json", {
        "pa_share_of_commercial": pa,
        "pc_share_illustrative": pc,
        "note": "Pc is the illustrative extra product of data/planning.json (not part of Kempf's Mini-Fab)",
    })
    return out


def run_queueing() -> dict[str, Any]:
    fab = load_fab()
    base = qm.route_cycle_time(fab)
    targets = [0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.98]
    curve = qm.x_factor_curve(fab, targets)
    curve_det = qm.x_factor_curve(fab, targets, ca2=0.0)
    # machines-only static operating point at Kempf starts can be infeasible (u > 1)
    out = {"at_kempf_starts": base, "curve_ca2_1": curve, "curve_ca2_0": curve_det}
    write_json("queueing.json", out)
    write_csv("xfactor_curve_model.csv", ["bottleneck_utilization", "starts_per_week", "x_factor_ca2_1", "x_factor_ca2_0"],
              [[a["bottleneck_utilization"], a["starts_per_week"], a["x_factor"], b["x_factor"]] for a, b in zip(curve, curve_det)])
    return out


def run_planning() -> dict[str, Any]:
    planning = load_planning()
    out = {}
    for sc in planning["scenarios"]:
        plan = solve_plan(sc, planning=planning)
        plan["validation"] = check_plan(plan)
        out[sc] = plan
        write_json(f"plan_{sc}.json", plan)
    rows = []
    for sc, p in out.items():
        for pur in p["purchases"]:
            rows.append([sc, pur["quarter_ordered"], pur["quarter_installed"], pur["group"], pur["tools"], pur["cost_musd"]])
    write_csv("plan_purchases.csv", ["scenario", "quarter_ordered", "quarter_installed", "group", "tools", "cost_musd"], rows)
    write_csv("plan_hires.csv", ["scenario", "quarter_hired", "quarter_available", "role", "people"],
              [[sc, h["quarter_hired"], h["quarter_available"], h["role"], h["people"]]
               for sc, p in out.items() for h in p["hires"]])
    return out


# ============================================================================ DES
def run_des_validation(settings: Settings) -> dict[str, Any]:
    """Machines-only DES at Kempf's 84 starts/week vs the static and queueing models."""
    fab = load_fab()
    cases = {}
    for rule in ("FIFO", "CR", "SA"):
        cases[f"{rule}_deterministic"] = (fab, settings.cfg(rule=rule, **MACHINES_ONLY))
    cases["FIFO_poisson"] = (fab, settings.cfg(rule="FIFO", release="poisson", **MACHINES_ONLY))
    res = _batch(settings, cases)
    static = cap.capacity_table(fab)
    q = qm.route_cycle_time(fab)
    compare = {}
    fifo = res["FIFO_deterministic"]
    for g, r in static["groups"].items():
        compare[g] = {
            "static_process_fraction_of_calendar": r["process_min_per_week"] / (r["tools"] * fab.week),
            "des_productive_fraction": fifo["groups"][g]["productive_fraction"]["mean"],
            "static_utilization_load_over_available": r["utilization"],
            "static_em_min_per_tool_week": r["em_min_per_tool"],
            "queueing_u_nonidle": q["groups"][g]["u"],
            "des_nonidle_fraction": fifo["groups"][g]["non_idle_fraction"]["mean"],
            "des_failures_per_week": fifo["groups"][g]["failures_per_week"]["mean"],
        }
    em = fab.groups["IMP"].em
    out = {
        "settings": {"reps": settings.reps, "warmup_weeks": settings.warmup, "run_weeks": settings.run},
        "runs": res,
        "static_vs_des": compare,
        "litho_setup_min_per_week": {
            "static_independent_sequence": static["groups"]["LITHO"]["setup_min_per_week"],
            **{k: v["litho_setup_min_per_week"]["mean"] for k, v in res.items()},
        },
        "imp_failures_per_machine_week": {
            "kempf_capacity_table": em["spec_events_per_week"],
            "renewal_from_distribution": fab.week / ((em["tbf_min"] + em["tbf_max"]) / 2 + (em["repair_min"] + em["repair_max"]) / 2),
            "des_FIFO": fifo["groups"]["IMP"]["failures_per_week"]["mean"] / fab.groups["IMP"].tools,
        },
        "queueing_cycle_time_min_ca2_1": q["cycle_time_min"],
        "queueing_cycle_time_min_ca2_0": qm.route_cycle_time(fab, ca2=0.0)["cycle_time_min"],
        "static_max_starts_per_week": static["max_starts_per_week"],
    }
    write_json("des_validation.json", out)
    return out


def run_des_layers(settings: Settings) -> dict[str, Any]:
    """Capacity (CONWIP, saturated) as realism layers are added, FIFO."""
    fab = load_fab()
    layers = {
        "1_machines_only": dict(operators=False, technician=False, transport=False),
        "2_plus_transport": dict(operators=False, technician=False, transport=True),
        "3_plus_technician": dict(operators=False, technician=True, transport=True),
        "4_plus_operators_full": dict(operators=True, technician=True, transport=True),
    }
    spec = failure_model_configs(fab)["calendar_spec"]
    cases = {k: (fab, settings.cfg(rule="FIFO", release="conwip", conwip_wip=40, **v)) for k, v in layers.items()}
    cases["0_no_setups_no_failures"] = (fab, settings.cfg(rule="FIFO", release="conwip", conwip_wip=40, setups=False,
                                                          failures=False, **MACHINES_ONLY))
    for k, v in layers.items():
        cases["spec_" + k] = (fab, settings.cfg(rule="FIFO", release="conwip", conwip_wip=40, **v, **spec))
    res = _batch(settings, cases)
    static = {
        "kempf_no_setups_spec_em": cap.capacity_table(fab, include_setups=False, em_mode="spec")["max_starts_per_week"],
        "renewal_em_with_setups": cap.capacity_table(fab)["max_starts_per_week"],
        "spec_em_with_setups": cap.capacity_table(fab, em_mode="spec")["max_starts_per_week"],
        "no_em_no_setups": cap.capacity_table(fab.with_group("IMP", em=None), include_setups=False)["max_starts_per_week"],
    }
    out = {"settings": {"reps": settings.reps, "conwip_wip": 40},
           "failure_readings": {"layers": "calendar clock, U(24 h, 76 h) after each repair = 2.95 failures/machine-week",
                                "layers_2_per_week": "calendar clock, TBF bounds x " + f"{spec['tbf_scale']:.4f}"
                                                     + " = 2 failures/machine-week (the specification's arithmetic)"},
           "static_max_starts_per_week": static,
           "layers": {k: v for k, v in res.items() if not k.startswith("spec_")},
           "layers_2_per_week": {k[5:]: v for k, v in res.items() if k.startswith("spec_")}}
    write_json("des_layers.json", out)
    return out


# ============================================================================ failure models
FAILURE_MODELS = cap.FAILURE_MODELS
FAILURE_MODEL_TEXT = {
    "calendar_dist": "calendar clock, 2.95/machine-week (the stated distribution; default)",
    "calendar_spec": "calendar clock, 2/machine-week (the specification's arithmetic)",
    "run_dist": "run-time clock, 2.95/machine-week at 84 starts",
    "run_spec": "run-time clock, 2/machine-week at 84 starts",
}


def failure_model_configs(fab: Fab) -> dict[str, dict[str, Any]]:
    """SimConfig overrides per failure model, calibrated on ``fab`` (Kempf's 84 starts, 2 implanters)."""
    return {k: {"failure_clock": clock, "tbf_scale": cap.des_tbf_scale(fab, "IMP", mode, clock)}
            for k, (clock, mode) in FAILURE_MODELS.items()}


def run_des_failure_models(settings: Settings) -> dict[str, Any]:
    """Every what-if option and the 84-starts open release under four failure models (FIFO).

    Also the numbers comparable with van den Berk (S3): output + lots lost to breakdowns at 84
    released (his throughput definition, App. D) and the CONWIP release rate (his max release rate)."""
    fab = load_fab()
    fms = failure_model_configs(fab)
    cases = {}
    for fm, over in fms.items():
        base_cfg = settings.cfg(rule="FIFO", release="conwip", conwip_wip=40, **over)
        for opt, (f, c) in des_option_configs(fab, base_cfg).items():
            cases[f"{fm}|{opt}"] = (f, c)
        cases[f"{fm}|open84"] = (fab, settings.cfg(rule="FIFO", **over))
    res = _batch(settings, cases)
    out: dict[str, Any] = {"settings": {"reps": settings.reps, "conwip_wip": 40, "rule": "FIFO"}, "models": {}}
    for fm, over in fms.items():
        base = res[f"{fm}|baseline"]
        gains = {}
        for opt in OPTIONS:
            paired = mean_ci([a - b for a, b in zip(res[f"{fm}|{opt}"]["throughput_by_seed"], base["throughput_by_seed"])])
            r = res[f"{fm}|{opt}"]
            gains[opt] = {"gain_lots_per_week": paired["mean"], "gain_half_width_paired": paired["half_width"],
                          "scrapped_per_week": r["scrapped_per_week"]["mean"],
                          "starts_per_week": r["starts_per_week"]["mean"],
                          "imp_failures_per_machine_week": r["failures_per_imp_machine_week"]["mean"],
                          "litho_wait_tech_fraction": r["groups"]["LITHO"]["state_fraction"]["wait_tech"]}
        op = res[f"{fm}|open84"]
        out["models"][fm] = {
            "description": FAILURE_MODEL_TEXT[fm],
            "failure_clock": over["failure_clock"],
            "tbf_scale": over["tbf_scale"],
            "baseline_capacity_lots_per_week": base["throughput_per_week"],
            "baseline_release_rate_per_week": base["starts_per_week"],
            "baseline_scrapped_per_week": base["scrapped_per_week"]["mean"],
            "baseline_imp_failures_per_machine_week": base["failures_per_imp_machine_week"]["mean"],
            "baseline_litho_wait_tech_fraction": base["groups"]["LITHO"]["state_fraction"]["wait_tech"],
            "options": gains,
            "open84": {"good_lots_per_week_avg": op["throughput_per_week"]["mean"],
                       "scrapped_per_week_avg": op["scrapped_per_week"]["mean"],
                       "good_plus_scrapped_per_week_avg": op["good_plus_scrapped_per_week"]["mean"],
                       "imp_failures_per_machine_week": op["failures_per_imp_machine_week"]["mean"],
                       "unstable_runs": op["unstable_runs"]},
        }
    add_imp = [m["options"]["add_IMP"]["gain_lots_per_week"] for m in out["models"].values()]
    out["add_IMP_gain_range"] = [min(add_imp), max(add_imp)]
    out["van_den_berk_S3"] = {"fifo_throughput_at_84_released": 47.4, "fifo_max_release_rate": 57.5,
                              "failures_per_machine_week": 2.0,
                              "note": "S3 throughput = lots finished + lots lost to breakdowns (App. D); "
                                      "S3 tunes failures to 400 downs per 100 weeks, only while processing (App. B)"}
    write_json("des_failure_models.json", out)
    return out


# ============================================================================ plan vs DES
def plan_quarter_fab(plan: dict[str, Any], q: int, planning: dict[str, Any] | None = None) -> Fab:
    """The planning fab in quarter q of a plan: that quarter's demand, installed tools and staff."""
    planning = planning or load_planning()
    f = planning_fab(planning=planning).with_starts({p: v[q] for p, v in plan["demand"].items()})
    for g, n in plan["installed"].items():
        f = f.with_tools(g, n[q])
    f.personnel = dict(f.personnel)
    ops = dict(f.personnel["operators"])
    k = len(ops)
    while len(ops) < plan["staff"]["operators"][q]:
        k += 1
        ops[f"PO{k}"] = {"groups": list(planning["labour"]["new_operator_groups"])}
    techs = dict(f.personnel.get("technicians", {"MT1": {}}))
    k = len(techs)
    while len(techs) < plan["staff"]["technicians"][q]:
        k += 1
        techs[f"MT{k}"] = {}
    f.personnel["operators"], f.personnel["technicians"] = ops, techs
    return f


def run_plan_vs_des(settings: Settings, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the base plan's Q4 and Q8 configurations (tools + people) through the full DES (FIFO)."""
    planning = load_planning()
    plan = plan or solve_plan("base", planning=planning)
    quarters = {"Q4": 3, "Q8": 7}
    cases = {}
    for name, q in quarters.items():
        f = plan_quarter_fab(plan, q, planning)
        cases[f"{name}_open"] = (f, settings.cfg(rule="FIFO"))
        cases[f"{name}_conwip60"] = (f, settings.cfg(rule="FIFO", release="conwip", conwip_wip=60))
    # what the DES needs beyond the plan in Q4: the same configuration with one more operator
    extra = dict(plan, staff={**plan["staff"], "operators": [n + 1 for n in plan["staff"]["operators"]]})
    f4 = plan_quarter_fab(extra, quarters["Q4"], planning)
    cases["Q4_plus_operator_open"] = (f4, settings.cfg(rule="FIFO"))
    cases["Q4_plus_operator_conwip60"] = (f4, settings.cfg(rule="FIFO", release="conwip", conwip_wip=60))
    res = _batch(settings, cases)
    out: dict[str, Any] = {"settings": {"reps": settings.reps, "rule": "FIFO", "conwip_wip": 60}, "quarters": {}}
    op, cw = res["Q4_plus_operator_open"], res["Q4_plus_operator_conwip60"]
    out["Q4_plus_one_operator"] = {
        "operators": len(f4.personnel["operators"]),
        "des_open_release": {"good_lots_per_week_avg": op["throughput_per_week"]["mean"],
                             "unstable_runs": op["unstable_runs"]},
        "des_capacity_conwip60": {"release_rate_per_week": cw["starts_per_week"],
                                  "good_lots_per_week": cw["throughput_per_week"]},
    }
    for name, q in quarters.items():
        f = plan_quarter_fab(plan, q, planning)
        st = cap.capacity_table(f)
        op, cw = res[f"{name}_open"], res[f"{name}_conwip60"]
        out["quarters"][name] = {
            "demand_starts_per_week": f.total_starts(),
            "tools": {g: grp.tools for g, grp in f.groups.items()},
            "operators": len(f.personnel["operators"]),
            "technicians": len(f.personnel["technicians"]),
            "plan_tool_utilization": {g: plan["utilization"][g][q] for g in plan["utilization"]},
            "plan_operator_utilization": plan["personnel_utilization"]["operators"][q],
            "plan_technician_utilization": plan["personnel_utilization"]["technicians"][q],
            "static_max_starts_per_week": st["max_starts_per_week"],
            "des_open_release": {"good_lots_per_week_avg": op["throughput_per_week"]["mean"],
                                 "scrapped_per_week_avg": op["scrapped_per_week"]["mean"],
                                 "released_per_week": op["starts_per_week"]["mean"],
                                 "unstable_runs": op["unstable_runs"]},
            "des_capacity_conwip60": {"release_rate_per_week": cw["starts_per_week"],
                                      "good_lots_per_week": cw["throughput_per_week"],
                                      "scrapped_per_week": cw["scrapped_per_week"]["mean"],
                                      "wait_operator_fraction": {g: v["state_fraction"]["wait_operator"]
                                                                 for g, v in cw["groups"].items()},
                                      "wait_tech_fraction": {g: v["state_fraction"]["wait_tech"]
                                                             for g, v in cw["groups"].items()}},
        }
        out["quarters"][name]["gap_release_rate_minus_demand"] = (cw["starts_per_week"]["mean"] - f.total_starts())
    write_json("plan_vs_des.json", out)
    return out


def run_des_rules(settings: Settings) -> dict[str, Any]:
    """Full model: dispatching rules at Kempf's open-loop 84 starts/week and at saturation (CONWIP)."""
    fab = load_fab()
    cases = {}
    for rule in ("FIFO", "CR", "SA"):
        cases[f"{rule}_open84"] = (fab, settings.cfg(rule=rule))
        cases[f"{rule}_conwip40"] = (fab, settings.cfg(rule=rule, release="conwip", conwip_wip=40))
    cases["FIFO_conwip40_litho_first"] = (fab, settings.cfg(rule="FIFO", release="conwip", conwip_wip=40,
                                                            op_priority_group="LITHO"))
    res = _batch(settings, cases)
    write_json("des_rules.json", {"settings": {"reps": settings.reps}, "runs": res})
    return res


def run_des_xfactor(settings: Settings) -> dict[str, Any]:
    """X-factor vs load: DES (Poisson release) against the queueing model (ca2 = 1)."""
    fab = load_fab()
    base = cap.capacity_table(fab)
    mo_targets = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9] if not settings.quick else [0.6, 0.85]
    full_scales = [0.3, 0.4, 0.5, 0.55, 0.6] if not settings.quick else [0.4]
    cases = {}
    for t in mo_targets:
        s = t / base["bottleneck_utilization"]
        cases[f"machines_only_{t}"] = (fab, settings.cfg(rule="FIFO", release="poisson", start_scale=s, **MACHINES_ONLY))
    for s in full_scales:
        cases[f"full_{s}"] = (fab, settings.cfg(rule="FIFO", release="poisson", start_scale=s))
    res = _batch(settings, cases)
    points = []
    for k, r in res.items():
        scale = r["config"]["start_scale"]
        f = fab.scaled(scale)
        model = qm.route_cycle_time(f)
        st = cap.capacity_table(f)
        points.append({
            "case": k,
            "model_variant": "machines_only" if k.startswith("machines") else "full",
            "start_scale": scale,
            "starts_per_week": f.total_starts(),
            "static_bottleneck_utilization": st["bottleneck_utilization"],
            "queueing_x_factor": model["x_factor"],
            "queueing_cycle_time_min": model["cycle_time_min"],
            "des_x_factor": r["x_factor"],
            "des_cycle_time_min": r["cycle_time_min"],
            "des_throughput_per_week": r["throughput_per_week"],
            "unstable_runs": r["unstable_runs"],
            "des_group_wait_min": {g: v["mean_wait_min"]["mean"] for g, v in r["groups"].items()},
            "queueing_group_wait_min": qm.group_visit_wait(f),
        })
    out = {"settings": {"reps": settings.reps}, "points": points}
    write_json("des_xfactor.json", out)
    return out


def run_whatif(settings: Settings) -> dict[str, Any]:
    fab = load_fab()
    costs = option_costs()
    static = static_whatif(fab, costs)
    base_cfg = settings.cfg(rule="FIFO", release="conwip", conwip_wip=40)
    res = _batch(settings, des_option_configs(fab, base_cfg))
    base_th = res["baseline"]["throughput_per_week"]["mean"]
    rows = []
    for opt in OPTIONS:
        th = res[opt]["throughput_per_week"]
        paired = mean_ci([a - b for a, b in zip(res[opt]["throughput_by_seed"], res["baseline"]["throughput_by_seed"])])
        gain = paired["mean"]
        cost = float(costs[opt])
        rows.append({
            "option": opt,
            "description": OPTIONS[opt],
            "cost_musd": cost,
            "des_capacity_lots_per_week": th["mean"],
            "des_capacity_half_width": th["half_width"],
            "gain_lots_per_week": gain,
            "gain_half_width_paired": paired["half_width"],
            "scrapped_per_week": res[opt]["scrapped_per_week"]["mean"],
            "starts_per_week": res[opt]["starts_per_week"]["mean"],
            "imp_failures_per_week": res[opt]["groups"]["IMP"]["failures_per_week"]["mean"],
            "litho_wait_tech_fraction": res[opt]["groups"]["LITHO"]["state_fraction"]["wait_tech"],
            "litho_wait_operator_fraction": res[opt]["groups"]["LITHO"]["state_fraction"]["wait_operator"],
            "gain_per_musd": (gain / cost) if cost > 0 else None,
        })
    des_ranked = rank(rows, "gain_lots_per_week")
    out = {
        "settings": {"reps": settings.reps, "conwip_wip": 40, "rule": "FIFO"},
        "static": static,
        "des": {"baseline_capacity_lots_per_week": res["baseline"]["throughput_per_week"],
                "baseline_scrapped_per_week": res["baseline"]["scrapped_per_week"]["mean"],
                "baseline_starts_per_week": res["baseline"]["starts_per_week"]["mean"],
                "baseline_litho_wait_tech_fraction": res["baseline"]["groups"]["LITHO"]["state_fraction"]["wait_tech"],
                "baseline_litho_wait_operator_fraction": res["baseline"]["groups"]["LITHO"]["state_fraction"]["wait_operator"],
                "options": des_ranked},
    }
    write_json("whatif.json", out)
    return out
