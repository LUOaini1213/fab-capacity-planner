"""Evaluation questions with gold tool calls.

Expected answers are not hand-typed: for each question the gold tool call is
executed and the listed fields of its output become the expected numbers /
keywords. Splits:
* dev (24) - used to build the router and the LLM prompt (the only split used since the
  2026-10-02 rebuild);
* test (24) - each a reworded sibling of a dev question. NOT held out any more: a review found
  that the first router contained patterns matching only test questions and the LLM prompt
  mirrored one; both were rebuilt from dev only, but the test questions had been seen;
* hard (12) - written after the first systems were frozen, deliberately avoiding the first
  router's keywords; seen before the rebuild, so also no longer held out;
* heldout (36, data/agent_heldout.json) - fresh, written after the rebuild, frozen and hashed.

expect: list of (tool_index, path) - numeric leaves must appear in the answer.
keywords: list of (tool_index, path) - string leaves must appear (case-insensitive).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

Q = []


def q(qid, split, text, calls=None, expect=(), keywords=(), out_of_scope=False):
    Q.append({"id": qid, "split": split, "question": text, "calls": calls or [], "expect": list(expect),
              "keywords": list(keywords), "out_of_scope": out_of_scope})


cs = "capacity_status"
ct = "cycle_time_estimate"
cp = "capex_plan"
db = "debottleneck_ranking"
ss = "simulation_summary"

# ---------------------------------------------------------------- capacity
q("c01", "dev", "Which tool group is the bottleneck at current demand, and how utilized is it?",
  [(cs, {})], expect=[(0, ("bottleneck_utilization_pct",))], keywords=[(0, ("bottleneck",))])
q("c02", "test", "What's the bottleneck right now and its utilization?",
  [(cs, {})], expect=[(0, ("bottleneck_utilization_pct",))], keywords=[(0, ("bottleneck",))])
q("c03", "dev", "What is the utilization of the implant tools?",
  [(cs, {})], expect=[(0, ("utilization_pct", "IMP"))])
q("c04", "test", "How busy are the diffusion furnaces at today's volume?",
  [(cs, {})], expect=[(0, ("utilization_pct", "DIFF"))])
q("c05", "dev", "How many lots per week can the fab start at most?",
  [(cs, {})], expect=[(0, ("max_starts_per_week",))])
q("c06", "test", "What is the maximum weekly start rate the line can sustain?",
  [(cs, {})], expect=[(0, ("max_starts_per_week",))])
q("c07", "dev", "How many starts per week can we load if no tool group may exceed an 85% utilization cap?",
  [(cs, {})], expect=[(0, ("max_starts_per_week_at_85pct_cap",))])
q("c08", "test", "With an 85% utilization ceiling, what's the max starts per week?",
  [(cs, {})], expect=[(0, ("max_starts_per_week_at_85pct_cap",))])
q("c09", "dev", "If demand rises 20%, where is the bottleneck and how loaded is it?",
  [(cs, {"demand_change_pct": 20})], expect=[(0, ("bottleneck_utilization_pct",))], keywords=[(0, ("bottleneck",))])
q("c10", "test", "Demand goes up by 15%. Which group becomes the constraint and at what utilization?",
  [(cs, {"demand_change_pct": 15})], expect=[(0, ("bottleneck_utilization_pct",))], keywords=[(0, ("bottleneck",))])
q("c11", "dev", "If demand falls 25%, what is the litho utilization?",
  [(cs, {"demand_change_pct": -25})], expect=[(0, ("utilization_pct", "LITHO"))])
q("c12", "test", "What would implant utilization be if volume dropped by 10%?",
  [(cs, {"demand_change_pct": -10})], expect=[(0, ("utilization_pct", "IMP"))])
q("c13", "dev", "If we add one litho tool, what is the maximum starts per week and the new bottleneck?",
  [(cs, {"extra_tools_group": "LITHO", "extra_tools": 1})], expect=[(0, ("max_starts_per_week",))],
  keywords=[(0, ("bottleneck",))])
q("c14", "test", "Suppose we buy another lithography scanner. What becomes the max weekly starts, and which group is then the bottleneck?",
  [(cs, {"extra_tools_group": "LITHO", "extra_tools": 1})], expect=[(0, ("max_starts_per_week",))],
  keywords=[(0, ("bottleneck",))])
q("c15", "dev", "With one more implanter, what would implant utilization be?",
  [(cs, {"extra_tools_group": "IMP", "extra_tools": 1})], expect=[(0, ("utilization_pct", "IMP"))])
q("c16", "test", "If we add 2 diffusion furnaces, what is the diffusion utilization?",
  [(cs, {"extra_tools_group": "DIFF", "extra_tools": 2})], expect=[(0, ("utilization_pct", "DIFF"))])
# ---------------------------------------------------------------- cycle time
q("t01", "dev", "What is the estimated cycle time at 70% of current demand?",
  [(ct, {"demand_change_pct": -30})], expect=[(0, ("cycle_time_hours",)), (0, ("x_factor",))])
q("t02", "test", "Estimate the X-factor and cycle time if volume is cut by 40%.",
  [(ct, {"demand_change_pct": -40})], expect=[(0, ("cycle_time_hours",)), (0, ("x_factor",))])
q("t03", "dev", "How long do lots queue at litho if demand drops 20%?",
  [(ct, {"demand_change_pct": -20})], expect=[(0, ("queue_time_hours", "LITHO"))])
q("t04", "test", "If demand decreases by 20%, how many hours do lots wait at the implanters?",
  [(ct, {"demand_change_pct": -20})], expect=[(0, ("queue_time_hours", "IMP"))])
q("t05", "dev", "What is the raw process time of a lot?",
  [(ct, {})], expect=[(0, ("raw_process_time_hours",))])
q("t06", "test", "How many hours of pure processing does a lot need, without any waiting?",
  [(ct, {})], expect=[(0, ("raw_process_time_hours",))])
# ---------------------------------------------------------------- capex
q("p01", "dev", "If demand rises 20% next quarter, where is the bottleneck and what should we buy?",
  [(cp, {"demand_change_pct": 20, "from_quarter": 2})],
  expect=[(0, ("total_capex_musd",))], keywords=[(0, ("bottleneck_before_purchases",)), (0, ("purchases", 0, "group"))])
q("p02", "test", "Demand jumps 20% starting next quarter. What tools should we order and what is the total capex?",
  [(cp, {"demand_change_pct": 20, "from_quarter": 2})],
  expect=[(0, ("total_capex_musd",))], keywords=[(0, ("purchases", 0, "group"))])
q("p03", "dev", "What is the total capex of the base capacity plan?",
  [(cp, {})], expect=[(0, ("total_capex_musd",))])
q("p04", "test", "How much do we need to spend on tools over the planning horizon under the base forecast?",
  [(cp, {})], expect=[(0, ("total_capex_musd",))])
q("p05", "dev", "Under the base plan, which tool do we buy first and when do we order it?",
  [(cp, {})], keywords=[(0, ("purchases", 0, "group")), (0, ("purchases", 0, "order_in"))])
q("p06", "test", "In the base forecast, what is the first purchase and in which quarter is it ordered?",
  [(cp, {})], keywords=[(0, ("purchases", 0, "group")), (0, ("purchases", 0, "order_in"))])
q("p07", "dev", "If the product mix shifts toward Pc, what is the total capex?",
  [(cp, {"scenario": "mix_shift_pc"})], expect=[(0, ("total_capex_musd",))])
q("p08", "test", "With the mix shift to product Pc, how much capex is needed?",
  [(cp, {"scenario": "mix_shift_pc"})], expect=[(0, ("total_capex_musd",))])
q("p09", "dev", "If demand is 10% higher from quarter 3, what is the total capex?",
  [(cp, {"demand_change_pct": 10, "from_quarter": 3})], expect=[(0, ("total_capex_musd",))])
q("p10", "test", "Total tool spend if demand rises 30% from Q4 onward?",
  [(cp, {"demand_change_pct": 30, "from_quarter": 4})], expect=[(0, ("total_capex_musd",))])
# ---------------------------------------------------------------- debottleneck
q("d01", "dev", "Which improvement gives the most throughput per dollar according to the static model?",
  [(db, {"method": "static"})], keywords=[(0, ("top_option",))])
q("d02", "test", "Using the capacity model, what's the best debottlenecking option per dollar?",
  [(db, {"method": "static"})], keywords=[(0, ("top_option",))])
q("d03", "dev", "According to the simulation, which improvement is the best value for money?",
  [(db, {"method": "des"})], keywords=[(0, ("top_option",))])
q("d04", "test", "Which debottlenecking option does the simulation rank first?",
  [(db, {"method": "des"})], keywords=[(0, ("top_option",))])
# ---------------------------------------------------------------- simulation
q("s01", "dev", "What capacity does the simulation give under FIFO dispatching?",
  [(ss, {"rule": "FIFO"})], expect=[(0, ("capacity_good_lots_per_week",))])
q("s02", "test", "What is the simulated capacity with the critical ratio rule?",
  [(ss, {"rule": "CR"})], expect=[(0, ("capacity_good_lots_per_week",))])
q("s03", "dev", "Which dispatching rule gives the highest simulated capacity?",
  [(ss, {"rule": "FIFO"})], keywords=[(0, ("best_rule_by_capacity",))])
q("s04", "test", "In the simulation, how many good lots per week come out at 84 starts with setup-avoidance dispatching?",
  [(ss, {"rule": "SA"})], expect=[(0, ("at_84_starts_output_per_week",))])
# ---------------------------------------------------------------- out of scope
q("o01", "dev", "What will TSMC's stock price be next month?", out_of_scope=True)
q("o02", "test", "What is Samsung's DRAM market share this year?", out_of_scope=True)
q("o03", "dev", "Write a short poem about silicon wafers.", out_of_scope=True)
q("o04", "test", "Can you translate 'cycle time' into French?", out_of_scope=True)
q("o05", "dev", "What is the weather in Singapore tomorrow?", out_of_scope=True)
q("o06", "test", "How many wafers per month does Intel's Fab 52 produce?", out_of_scope=True)
q("o07", "dev", "Who invented the transistor?", out_of_scope=True)
q("o08", "test", "Book a meeting with the maintenance team for Friday.", out_of_scope=True)

# ---------------------------------------------------------------- "hard" held-out split
# Written after the router and LLM prompt were frozen (and after the dev/test results were seen),
# deliberately with wording that does not reuse the keywords of the dev questions. No system was
# tuned on these.
q("h01", "hard", "Is any tool set going to choke if we take 15% more orders, and which one?",
  [(cs, {"demand_change_pct": 15})], expect=[(0, ("bottleneck_utilization_pct",))], keywords=[(0, ("bottleneck",))])
q("h02", "hard", "What's our throughput ceiling in lots a week?",
  [(cs, {})], expect=[(0, ("max_starts_per_week",))])
q("h03", "hard", "Orders drop by a third, 33%. How loaded is the scanner then?",
  [(cs, {"demand_change_pct": -33})], expect=[(0, ("utilization_pct", "LITHO"))])
q("h04", "hard", "Would a second scanner lift the weekly start ceiling? To what?",
  [(cs, {"extra_tools_group": "LITHO", "extra_tools": 1})], expect=[(0, ("max_starts_per_week",))])
q("h05", "hard", "End to end, how many hours does a lot spend in the fab if we run 25% below today's volume?",
  [(ct, {"demand_change_pct": -25})], expect=[(0, ("cycle_time_hours",))])
q("h06", "hard", "Sales says volumes go up 25% from Q3. What equipment do we need and what does it cost in total?",
  [(cp, {"demand_change_pct": 25, "from_quarter": 3})], expect=[(0, ("total_capex_musd",))],
  keywords=[(0, ("purchases", 0, "group"))])
q("h07", "hard", "If we do nothing special, how much money goes into new tools over the next eight quarters?",
  [(cp, {})], expect=[(0, ("total_capex_musd",))])
q("h08", "hard", "Ignoring the simulation, what's the cheapest lever to get more lots out?",
  [(db, {"method": "static"})], keywords=[(0, ("top_option",))])
q("h09", "hard", "Under setup-avoidance sequencing, how many good lots per week can the line deliver at most in the simulation?",
  [(ss, {"rule": "SA"})], expect=[(0, ("capacity_good_lots_per_week",))])
# h10: the gold originally pointed at queue_time_hours.DIFF (queue for a free furnace, 3.4 h), which leaves out
# the wait for the batch to fill; the question asks for the whole wait, so the gold was corrected on 2026-10-02
# to wait_per_visit_hours.DIFF (queue + batch formation) after a review. The question text is unchanged.
q("h10", "hard", "How many hours does a lot wait in front of the furnaces at today's loading?",
  [(ct, {})], expect=[(0, ("wait_per_visit_hours", "DIFF"))])
q("h11", "hard", "What did Intel pay for its last EUV scanner?", out_of_scope=True)
q("h12", "hard", "Summarise the plot of a movie about chip fabs.", out_of_scope=True)

# ---------------------------------------------------------------- fresh "heldout" split (data/agent_heldout.json)
# Written on 2026-10-02 after the router and the LLM prompt were rebuilt from the dev questions only and
# frozen; scored once. The file is frozen: its SHA-256 is pinned here and checked by the tests.
HELDOUT_PATH = Path(__file__).resolve().parents[3] / "data" / "agent_heldout.json"
HELDOUT_SHA256 = "31c447a5697d6620a264089a86cfc8e88fd57b1a0d82bb8994e18fd3850fe7db"


def _load_heldout() -> list[dict]:
    raw = json.loads(HELDOUT_PATH.read_text(encoding="utf-8"))
    out = []
    for h in raw["questions"]:
        out.append({"id": h["id"], "split": "heldout", "question": h["question"],
                    "calls": [(n, a) for n, a in h.get("calls", [])],
                    "expect": [(i, tuple(p)) for i, p in h.get("expect", [])],
                    "keywords": [(i, tuple(p)) for i, p in h.get("keywords", [])],
                    "out_of_scope": bool(h.get("out_of_scope", False)),
                    "known_before_rules": bool(h.get("known_before_rules", False))})
    return out


def heldout_sha256() -> str:
    """SHA-256 of the held-out file with LF line endings (so a CRLF checkout gives the same hash)."""
    return hashlib.sha256(HELDOUT_PATH.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


Q.extend(_load_heldout())
QUESTIONS = Q
SPLITS = ("dev", "test", "hard", "heldout")
