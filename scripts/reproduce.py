"""Regenerate every file in results/ (except the optional LLM evaluation).

Usage:
    python scripts/reproduce.py                 # everything (about 10-20 min on 4 cores)
    python scripts/reproduce.py --stages static # capacity + queueing + planning only (seconds)
    python scripts/reproduce.py --quick         # short DES runs into a temp dir (smoke test)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fabcap import experiments as ex  # noqa: E402
from fabcap import io as fio  # noqa: E402

STAGES = {
    "static": ["capacity", "queueing", "planning"],
    "des": ["des_validation", "des_layers", "des_rules", "des_xfactor", "whatif", "des_failure_models", "plan_vs_des"],
    "agent": ["agent_router"],
    "figures": ["figures"],
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="static,des,agent,figures")
    ap.add_argument("--quick", action="store_true", help="short DES settings (results NOT comparable to committed ones)")
    ap.add_argument("--out", default=None, help="results directory (default: results/)")
    args = ap.parse_args(argv)
    if args.out:
        fio.RESULTS_DIR = Path(args.out)
        import fabcap.plots as plots
        plots.RESULTS_DIR = Path(args.out)
    settings = ex.Settings(quick=args.quick)
    todo = []
    for s in args.stages.split(","):
        todo += STAGES.get(s.strip(), [s.strip()])
    for step in todo:
        t = time.time()
        if step == "capacity":
            ex.run_capacity()
        elif step == "queueing":
            ex.run_queueing()
        elif step == "planning":
            ex.run_planning()
        elif step == "des_validation":
            ex.run_des_validation(settings)
        elif step == "des_layers":
            ex.run_des_layers(settings)
        elif step == "des_rules":
            ex.run_des_rules(settings)
        elif step == "des_xfactor":
            ex.run_des_xfactor(settings)
        elif step == "whatif":
            ex.run_whatif(settings)
        elif step == "des_failure_models":
            ex.run_des_failure_models(settings)
        elif step == "plan_vs_des":
            ex.run_plan_vs_des(settings)
        elif step == "agent_router":
            from fabcap.agent.evaluate import run_router_eval
            run_router_eval()
        elif step == "figures":
            from fabcap.plots import make_all
            make_all(Path(fio.RESULTS_DIR) / "figures")
        else:
            raise SystemExit(f"unknown stage {step}")
        print(f"[{step}] {time.time() - t:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
