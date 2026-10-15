import os

import pytest

from fabcap.agent import tools as T
from fabcap.agent.evaluate import gold, score
from fabcap.agent.questions import QUESTIONS
from fabcap.agent.router import answer, parse_demand_change, parse_extra_tools, parse_from_quarter, route
from fabcap.agent.validator import extract_numbers, validate_answer

needs_results = pytest.mark.skipif(not (T.fio.RESULTS_DIR / "whatif.json").exists(), reason="results/ not generated")


def test_extract_numbers_skips_identifiers_but_not_units():
    text = "In Q2 buy 1 LITHO for $12.5M; S5 waits 3.25 h, G/G/1 queue, 1,250 lots, -7% and 3nm on Mc."
    assert extract_numbers(text) == [1.0, 12.5, 3.25, 1250.0, -7.0, 3.0]
    # digits glued to letters or a leading dot are still numbers
    assert extract_numbers("x12, approx13 and .5 of S3X") == [12.0, 13.0, 0.5]


def test_validator_accepts_tool_numbers_and_rejects_invented_ones():
    out = [{"u": {"LITHO": 103.1}, "max": 81.5, "tools": 2, "label": "Q3"}]
    assert validate_answer("LITHO runs at 103.1%, max 81.5 lots/week with 2 tools in Q3.", out)["ok"]
    bad = validate_answer("LITHO runs at 103.1%, so about 79 lots/week.", out)
    assert not bad["ok"] and bad["unsupported"] == [79.0]
    # rounding a tool number is not allowed either
    assert validate_answer("max 82 lots/week", out)["unsupported"] == [82.0]
    assert validate_answer("no numbers here", [])["ok"]


def test_echoed_tool_arguments_need_the_question():
    out = [{"demand_change_pct": -10.0, "from_quarter": 3, "max": 81.5}]
    # the user said 10 % -> echoing it is fine
    assert validate_answer("If demand falls 10%, max is 81.5.", out, "What if demand falls 10% from Q3?")["ok"]
    # an argument the model chose itself cannot be used to launder a number into the answer
    laundered = validate_answer("Cycle time is 10 hours.", out, "What is the cycle time?")
    assert not laundered["ok"] and laundered["unsupported"] == [10.0]
    assert not validate_answer("Starting in quarter 3 ...", out, "What is the plan?")["ok"]


def test_identifiers_are_narrow():
    # only G/G/1, G/G/m and M/M/1 are names; anything else with digits is a number
    assert extract_numbers("queue behaves like G/G/120 and M/M/58") == [120.0, 58.0]
    assert extract_numbers("a G/G/m and an M/M/1 queue, PO3 and MT2 on S4X") == []
    assert extract_numbers("Q9 Q7") == []  # quarters are checked separately


def test_adversarial_laundering_cases_are_rejected():
    """The review's adversarial cases (all accepted by the first validator)."""
    q = "What is the maximum weekly start rate?"
    # 1) a number derived from a tool argument (starts = 84 x (1 + d/100) = 120.0)
    o = T.call_tool("capacity_status", {"demand_change_pct": (120 / 84 - 1) * 100})
    assert o["starts_per_week"] == 120.0 and "starts_per_week" in o["arg_derived"]
    assert not validate_answer("The fab can start at most 120.0 lots per week.", [o], q)["ok"]
    # ... but the same derived number is fine when the user stated it
    assert validate_answer("At 120 starts/week the bottleneck is LITHO.", [o], "What if we start 120 lots per week?")["ok"]
    # 2) numbers echoed in error messages: unknown group, unknown tool, unexpected argument name
    o2 = T.call_tool("capacity_status", {"extra_tools_group": "LITHO 137.5"})
    assert "error" in o2 and not validate_answer("Max starts is 137.5 lots/week.", [o2], q)["ok"]
    o3 = T.call_tool("tool_999", {})
    assert not validate_answer("Capacity is 999 lots/week.", [o3], q)["ok"]
    o4 = T.call_tool("capacity_status", {"lots_250": 1})
    assert not validate_answer("Capacity is 250 lots/week.", [o4], q)["ok"]
    # 3) a number hidden behind an allowed one with a second dot
    base = T.call_tool("capacity_status", {})
    assert base["max_starts_per_week"] == 81.5
    v = validate_answer("Max starts is 81.5.999 lots", [base], q)
    assert not v["ok"] and v["malformed"] == ["81.5.999"]
    assert not validate_answer("Version 1.2.3 of the plan", [base], q)["ok"]
    assert validate_answer("Max starts is 81.5. That is all.", [base], q)["ok"]
    # 4) G/G/120 and M/M/58 are no longer swallowed as identifiers
    assert not validate_answer("The queue behaves like G/G/120.", [base], q)["ok"]
    assert not validate_answer("The queue behaves like M/M/58.", [base], q)["ok"]
    # 5) tool counts after adding tools are argument-derived
    o8 = T.call_tool("capacity_status", {"extra_tools_group": "LITHO", "extra_tools": 9})
    assert o8["tools"]["LITHO"] == 10 and "tools.LITHO" in o8["arg_derived"]
    assert not validate_answer("You need 10 litho tools.", [o8], "What is the bottleneck?")["ok"]
    assert validate_answer("With 10 litho tools the bottleneck moves.", [o8], "What if we had 10 litho tools?")["ok"]


def test_quarters_must_come_from_tools_or_question():
    cp = T.capex_plan()
    assert validate_answer(f"Order LITHO in {cp['purchases'][0]['order_in']}.", [cp], "What is the plan?")["ok"]
    cs = T.capacity_status()
    v = validate_answer("Buy a litho tool in Q7.", [cs], "Where is the bottleneck?")
    assert not v["ok"] and v["unsupported_quarters"] == ["Q7"]
    assert validate_answer("From Q7 on, the bottleneck is LITHO.", [cs], "What happens from Q7 on?")["ok"]


def test_error_outputs_contribute_nothing():
    from fabcap.agent.validator import allowed_numbers
    assert allowed_numbers([{"error": "x must be between -90 and 300", "value": 5}]) == set()


def test_router_parsers():
    assert parse_demand_change("If demand rises 20% next quarter") == 20.0
    assert parse_demand_change("volume dropped by 12.5%") == -12.5
    assert parse_demand_change("at 70% of current demand") == -30.0
    assert parse_demand_change("with an 85% utilization cap, max starts?") == 0.0
    assert parse_from_quarter("from Q4 onward") == 4
    assert parse_from_quarter("starting next quarter") == 2
    assert parse_from_quarter("over the horizon") == 1
    assert parse_extra_tools("If we add 2 diffusion furnaces") == ("DIFF", 2)
    assert parse_extra_tools("with one more implanter") == ("IMP", 1)
    assert parse_extra_tools("If we add one litho tool") == ("LITHO", 1)
    assert parse_extra_tools("what is litho utilization") == (None, 0)


def test_router_refuses_out_of_scope():
    for qs in QUESTIONS:
        if qs["out_of_scope"] and qs["split"] == "dev":
            assert route(qs["question"])["refused"]
    assert not route("What is the bottleneck?")["refused"]
    # domain words do not make a question about another company's fab in scope (named-entity rule,
    # not a list of company names)
    assert route("What is TSMC's litho capacity?")["refused"]
    assert route("Is Samsung's implant tool utilization higher than ours?")["refused"]
    assert route("How many lots does GlobalFoundries start per week?")["refused"]
    assert route("How many lots per week does Samsung start in Pyeongtaek?")["refused"]
    # the review's coffee-machine example: personal belongings / a unit the model does not have
    assert route("What's the capacity of my coffee machine in cups per week?")["refused"]
    assert route("What capacity in cups per week does the canteen have?")["refused"]
    assert route("What is the capacity of my coffee machine?")["refused"]  # "my" rule alone (no unit, no name)
    assert route("Write a poem about the bottleneck")["refused"]
    # in-scope wording with model names stays in scope
    assert not route("What is the LITHO utilization in Q3 with FIFO?")["refused"]
    assert not route("How many lots per week can the fab start at most?")["refused"]


def test_router_patterns_come_from_dev_questions():
    """Every alternative of the router's domain/intent patterns occurs in a dev question
    (or is a name from the fab's own vocabulary); none may exist only for other splits."""
    import re as _re
    from fabcap.agent import router as R
    dev = [q["question"] for q in QUESTIONS if q["split"] == "dev"]
    vocabulary = {"diffusion"}  # tool-group name of the specification (DIFF), not a dev word
    for alt in R.DOMAIN.pattern.split("|"):
        if alt in vocabulary:
            continue
        assert any(_re.search(alt, d, _re.I) for d in dev), alt
    for alt in R.NEG.pattern.split("|"):
        assert alt == r"\bfell\b" or any(_re.search(alt, d, _re.I) for d in dev), alt


def test_heldout_file_is_frozen():
    from fabcap.agent.questions import HELDOUT_SHA256, heldout_sha256
    assert heldout_sha256() == HELDOUT_SHA256
    held = [q for q in QUESTIONS if q["split"] == "heldout"]
    assert sum(not q["out_of_scope"] for q in held) >= 20 and sum(q["out_of_scope"] for q in held) >= 10
    oos = {q["question"] for q in held if q["out_of_scope"]}
    assert "How many lots does GlobalFoundries start per week?" in oos
    assert "What's the capacity of my coffee machine in cups per week?" in oos


def test_keyword_paraphrase_rule():
    from fabcap.agent.evaluate import keyword_present
    assert keyword_present("halve_litho_setup", "Halving the lithography setup time is best.")
    assert keyword_present("LITHO", "the litho group")
    assert not keyword_present("halve_litho_setup", "hire a lot of staff")
    assert not keyword_present("DIFF", "the implanters")


@needs_results
def test_router_answers_always_pass_validator():
    for qs in QUESTIONS:
        r = answer(qs["question"])
        assert validate_answer(r["answer"], r["outputs"], qs["question"])["ok"], qs["id"]


@needs_results
def test_router_dev_split_is_fully_correct():
    wrong = [qs["id"] for qs in QUESTIONS if qs["split"] == "dev" and not score(qs, answer(qs["question"]))["correct"]]
    assert wrong == []


def test_question_set_shape():
    assert len(QUESTIONS) >= 30
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids))
    splits = {q["split"] for q in QUESTIONS}
    assert splits == {"dev", "test", "hard", "heldout"}
    assert sum(q["out_of_scope"] for q in QUESTIONS if q["split"] == "test") >= 3
    for q in QUESTIONS:
        assert q["out_of_scope"] == (not q["calls"])


@needs_results
def test_gold_answers_come_from_tools():
    g = gold(next(q for q in QUESTIONS if q["id"] == "c01"))
    assert g["keywords"] == ["LITHO"]
    assert g["numbers"] == [T.capacity_status()["bottleneck_utilization_pct"]]


def test_scoring_rules():
    qs = next(q for q in QUESTIONS if q["id"] == "c03")
    out = T.capacity_status()
    ok = {"answer": f"IMP utilization is {out['utilization_pct']['IMP']}%.", "refused": False,
          "calls": [("capacity_status", {})], "outputs": [out]}
    assert score(qs, ok)["correct"]
    wrong = dict(ok, answer="IMP utilization is 82%.")
    assert not score(qs, wrong)["correct"]
    refused = dict(ok, refused=True)
    assert score(qs, refused)["kind"] == "false_refusal"
    oos = next(q for q in QUESTIONS if q["out_of_scope"])
    assert score(oos, {"refused": True})["correct"]
    assert not score(oos, {"refused": False, "answer": "x", "calls": [], "outputs": []})["correct"]


def test_tool_input_validation_and_echo():
    out = T.capacity_status(demand_change_pct=15, extra_tools_group="scanner", extra_tools=1)
    assert out["extra_tools_group"] == "LITHO" and out["tools"]["LITHO"] == 2 and out["demand_change_pct"] == 15.0
    assert T.call_tool("capacity_status", {"demand_change_pct": 1e6})["error"]
    assert T.call_tool("capacity_status", {"extra_tools_group": "etch", "extra_tools": 1})["error"]
    assert T.call_tool("no_such_tool", {})["error"]
    assert T.call_tool("capacity_status", {"bogus": 1})["error"]
    up = T.capacity_status(demand_change_pct=20)
    base = T.capacity_status()
    assert up["bottleneck_utilization_pct"] == pytest.approx(round(base["bottleneck_utilization_pct"] * 1.2, 1), abs=0.11)


def test_cycle_time_tool_reports_total_wait_including_batch_formation():
    out = T.cycle_time_estimate()
    # at diffusion the wait for a full batch comes on top of the queue for a free furnace
    assert out["wait_per_visit_hours"]["DIFF"] > out["queue_time_hours"]["DIFF"]
    assert out["wait_per_visit_hours"]["DIFF"] == 6.4 and out["queue_time_hours"]["DIFF"] == 3.4
    h10 = next(q for q in QUESTIONS if q["id"] == "h10")
    assert gold(h10)["numbers"] == [6.4]


def test_capex_tool_reports_pre_purchase_bottleneck():
    out = T.capex_plan(demand_change_pct=20, from_quarter=2)
    assert out["bottleneck_before_purchases"] == "LITHO"
    assert out["purchases"][0]["group"] == "LITHO" and out["purchases"][0]["order_in"] == "Q1"
    assert out["total_capex_musd"] == pytest.approx(25.0)


class FakeLLM:
    """Scripted stand-in for Ollama: a list of assistant messages returned in order."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def __call__(self, model, messages, tools, host, timeout):
        self.seen.append([dict(m) for m in messages])
        return {"message": self.script.pop(0)}


def _tool_call(name, args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_llm_hallucinated_number_is_repaired_or_replaced(monkeypatch):
    from fabcap.agent import llm
    imp = T.capacity_status()["utilization_pct"]["IMP"]
    # 1) invented number, repair also invents -> deterministic template replaces the answer
    fake = FakeLLM([_tool_call("capacity_status", {}), {"content": "IMP is at 79.9%."},
                    {"content": "IMP is at 80.0%."}])
    monkeypatch.setattr(llm, "_chat", fake)
    r = llm.llm_answer("How busy are the implanters?", "fake")
    assert r["fallback"] and not r["refused"] and not r["first_valid"]
    assert r["first_unsupported"] == [79.9]
    assert f"IMP {imp:g}%" in r["answer"]
    assert "79.9" not in r["answer"] and "80.0" not in r["answer"]
    assert "79.9" in fake.seen[-1][-1]["content"]  # the repair prompt names the bad number
    # 2) repair succeeds -> repaired answer kept
    fake2 = FakeLLM([_tool_call("capacity_status", {}), {"content": "IMP is at 79.9%."},
                     {"content": f"IMP is at {imp}%."}])
    monkeypatch.setattr(llm, "_chat", fake2)
    r2 = llm.llm_answer("How busy are the implanters?", "fake")
    assert r2["repaired"] and not r2["fallback"] and r2["answer"] == f"IMP is at {imp}%."
    # 3) valid first answer passes untouched
    fake3 = FakeLLM([_tool_call("capacity_status", {}), {"content": f"IMP utilization: {imp}%."}])
    monkeypatch.setattr(llm, "_chat", fake3)
    r3 = llm.llm_answer("How busy are the implanters?", "fake")
    assert r3["first_valid"] and r3["answer"] == f"IMP utilization: {imp}%."


def test_llm_numbers_without_tool_call_become_refusal(monkeypatch):
    from fabcap.agent import llm
    fake = FakeLLM([{"content": "Utilization is 85%."}, {"content": "Still 85%."}])
    monkeypatch.setattr(llm, "_chat", fake)
    r = llm.llm_answer("How busy are the implanters?", "fake")
    assert r["refused"] and r["fallback"] and "85" not in r["answer"]
    fake2 = FakeLLM([{"content": "OUT_OF_SCOPE"}])
    monkeypatch.setattr(llm, "_chat", fake2)
    assert llm.llm_answer("Write a poem", "fake")["refused"]
    # a model may look at a tool first and then decline: still a refusal
    fake3 = FakeLLM([_tool_call("capacity_status", {}), {"content": "OUT_OF_SCOPE"}])
    monkeypatch.setattr(llm, "_chat", fake3)
    r3 = llm.llm_answer("What did another company pay for a scanner?", "fake")
    assert r3["refused"] and r3["answer"].startswith("That question is outside")


@pytest.mark.llm
@pytest.mark.skipif(not os.environ.get("FABCAP_LLM_MODEL"), reason="set FABCAP_LLM_MODEL to run against Ollama")
def test_llm_answer_is_validated():
    from fabcap.agent.llm import llm_answer
    r = llm_answer("What is the utilization of the implant tools?", os.environ["FABCAP_LLM_MODEL"])
    assert validate_answer(r["answer"], r["outputs"], "What is the utilization of the implant tools?")["ok"]
