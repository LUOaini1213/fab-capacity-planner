"""Deterministic answer templates. Every number printed is a computed value of the tool output:
tool arguments and argument-derived fields (``arg_derived``) are not printed, so the templates
pass the validator without help from the question."""
from __future__ import annotations

from typing import Any

REFUSAL = ("That question is outside the scope of this capacity-planning assistant. I can answer questions "
           "about the Mini-Fab model: utilization and bottlenecks, maximum starts, cycle time, capex plans, "
           "debottlenecking options and simulation results.")
ERROR = "The model could not answer this request (the tool rejected its arguments)."


def _derived(out: dict[str, Any], field: str) -> bool:
    return field in out.get("arg_derived", [])


def render(tool: str, out: dict[str, Any]) -> str:
    if "error" in out:
        return ERROR
    if tool == "capacity_status":
        head = "At the requested demand" if _derived(out, "starts_per_week") else f"At {out['starts_per_week']:g} starts/week"
        if out["extra_tools_group"]:
            head += f" with the added {out['extra_tools_group']} tools"
        u = out["utilization_pct"]
        s = (f"{head}: utilization DIFF {u['DIFF']:g}%, IMP {u['IMP']:g}%, LITHO {u['LITHO']:g}%. "
             f"Bottleneck: {out['bottleneck']} at {out['bottleneck_utilization_pct']:g}%")
        s += " (over capacity)." if out["over_capacity"] else "."
        s += (f" Maximum starts: {out['max_starts_per_week']:g} lots/week "
              f"({out['max_starts_per_week_at_85pct_cap']:g} with a {out['cap_pct']:g}% utilization cap).")
        return s
    if tool == "cycle_time_estimate":
        head = "At the requested demand" if _derived(out, "starts_per_week") else f"At {out['starts_per_week']:g} starts/week"
        if not out["stable"]:
            return (f"{head} the line is over capacity, so the queueing model gives no finite cycle time "
                    f"(raw process time {out['raw_process_time_hours']:g} h).")
        q = out["queue_time_hours"]
        w = out["wait_per_visit_hours"]
        return (f"{head}: estimated mean cycle time {out['cycle_time_hours']:g} h, raw process time "
                f"{out['raw_process_time_hours']:g} h, X-factor {out['x_factor']:g}. Queue time per visit "
                f"(waiting for a free tool): DIFF {q['DIFF']:g} h, IMP {q['IMP']:g} h, LITHO {q['LITHO']:g} h. "
                f"Total wait per visit including batch formation: DIFF {w['DIFF']:g} h, IMP {w['IMP']:g} h, "
                f"LITHO {w['LITHO']:g} h.")
    if tool == "capex_plan":
        fq = f"Q{out['from_quarter']}"
        starts = "" if _derived(out, "starts_per_week_in_from_quarter") else \
            f", {out['starts_per_week_in_from_quarter']:g} starts/week in {fq}"
        s = (f"Capex plan (scenario {out['scenario']}{starts}): before any purchase, the bottleneck in {fq} is "
             f"{out['bottleneck_before_purchases']} at {out['bottleneck_before_purchases_utilization_pct']:g}%. ")
        if out["purchases"]:
            buys = "; ".join(f"order {p['tools']} {p['group']} in {p['order_in']} (in service {p['in_service']}, "
                             f"${p['cost_musd']:g}M)" for p in out["purchases"])
            s += f"Buy: {buys}. "
        else:
            s += "No purchases are needed. "
        s += f"Total capex ${out['total_capex_musd']:g}M at a {out['target_utilization_pct']:g}% utilization cap."
        if out["hires"]:
            hires = "; ".join(f"{h['people']} {h['role']}(s) in {h['hire_in']}" for h in out["hires"])
            s += f" Hire: {hires} (labour cost ${out['labour_cost_musd']:g}M over the horizon)."
        if out["unserved_lots_per_week_total"]:
            s += f" Unserved demand remains: {out['unserved_lots_per_week_total']:g} lots/week summed over quarters."
        return s
    if tool == "debottleneck_ranking":
        key = "gain_starts_per_week" if out["method"] == "static" else "gain_lots_per_week"
        parts = []
        for r in out["options"]:
            gpm = f", {r['gain_per_musd']:g} per $M" if r["gain_per_musd"] is not None else ""
            parts.append(f"{r['rank']}. {r['option']} (cost ${r['cost_musd']:g}M, gain {r[key]:g} lots/week{gpm})")
        return (f"Ranking by {out['method']} model (baseline {out['baseline']:g} lots/week): " + "; ".join(parts)
                + f". Top option: {out['top_option']}; best paid option: {out['best_paid_option']}.")
    if tool == "simulation_summary":
        return (f"Simulation, {out['rule']} dispatching: capacity {out['capacity_good_lots_per_week']:g} "
                f"+/- {out['capacity_ci95_half_width']:g} good lots/week ({out['replications']} replications). "
                f"With {out['open_loop_starts_per_week']:g} starts/week released, output averages "
                f"{out['at_84_starts_output_per_week']:g} lots/week ({out['at_84_starts_unstable_runs']} of "
                f"{out['replications']} runs unstable, so this is not a capacity). "
                f"Best rule by capacity: {out['best_rule_by_capacity']}.")
    return "Unknown tool."
