"""Evaluate the deterministic router and (optionally) an Ollama LLM on the question set.

Metric "exact numeric match": an in-scope answer is correct iff it is not a
refusal, it passes the number validator, every expected number (from the gold
tool call) appears in it with exactly the same value, and every expected
keyword appears. An out-of-scope question is correct iff it is refused.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path
from typing import Any

from ..io import write_json
from .questions import QUESTIONS
from .router import answer as router_answer
from .tools import call_tool
from .validator import extract_numbers, validate_answer


def _get(obj: Any, path: tuple) -> Any:
    for k in path:
        obj = obj[k]
    return obj


def gold(qspec: dict[str, Any]) -> dict[str, Any]:
    outs = [call_tool(n, a) for n, a in qspec["calls"]]
    nums = [float(_get(outs[i], p)) for i, p in qspec["expect"]]
    kws = [str(_get(outs[i], p)) for i, p in qspec["keywords"]]
    return {"outputs": outs, "numbers": nums, "keywords": kws}


def keyword_present(keyword: str, answer: str) -> bool:
    """Exact (case-insensitive) substring, or - for snake_case option ids such as
    'halve_litho_setup' - every token's first 4 letters start some word of the answer
    ('halving the lithography setup' matches)."""
    a = answer.lower()
    k = keyword.lower()
    if k in a:
        return True
    if "_" not in k:
        return False
    words = re.findall(r"[a-z]+", a)
    return all(any(w.startswith(tok[:4]) for w in words) for tok in k.split("_") if tok)


def score(qspec: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    if qspec["out_of_scope"]:
        return {"correct": bool(res["refused"]), "kind": "out_of_scope"}
    if res["refused"]:
        return {"correct": False, "kind": "false_refusal"}
    g = gold(qspec)
    nums = extract_numbers(res["answer"])
    missing_nums = [x for x in g["numbers"] if not any(x == y for y in nums)]
    missing_kw = [k for k in g["keywords"] if not keyword_present(k, res["answer"])]
    valid = validate_answer(res["answer"], res["outputs"], qspec["question"])["ok"]
    gold_calls = [(n, _norm(a)) for n, a in qspec["calls"]]
    made = [(n, _norm(a)) for n, a in res["calls"]]
    return {
        "correct": not missing_nums and not missing_kw and valid,
        "kind": "in_scope",
        "missing_numbers": missing_nums,
        "missing_keywords": missing_kw,
        "validator_ok": valid,
        "tool_call_match": all(c in made for c in gold_calls),
    }


def _norm(args: dict[str, Any]) -> tuple:
    defaults = {"demand_change_pct": 0.0, "from_quarter": 1, "scenario": "base", "extra_tools": 0,
                "extra_tools_group": None, "method": "static", "rule": "FIFO"}
    full = dict(defaults)
    full.update(args or {})
    if not full.get("extra_tools"):
        full["extra_tools"], full["extra_tools_group"] = 0, None
    out = []
    for k in sorted(full):
        v = full[k]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = float(v)
        out.append((k, v))
    return tuple(out)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for split in ("dev", "test", "hard", "heldout", "heldout_unseen", "all"):
        if split == "heldout_unseen":  # held-out questions not known while the scope rules were written
            rs = [r for r in rows if r["split"] == "heldout" and not r.get("known_before_rules")]
        else:
            rs = [r for r in rows if split == "all" or r["split"] == split]
        if not rs:
            continue
        ins = [r for r in rs if not r["out_of_scope"]]
        oos = [r for r in rs if r["out_of_scope"]]
        out[split] = {
            "n": len(rs),
            "accuracy": sum(r["score"]["correct"] for r in rs) / len(rs),
            "in_scope_n": len(ins),
            "in_scope_exact_match": sum(r["score"]["correct"] for r in ins) / len(ins) if ins else None,
            "in_scope_correct": sum(r["score"]["correct"] for r in ins),
            "false_refusals": sum(r["score"]["kind"] == "false_refusal" for r in ins),
            "tool_call_match": sum(bool(r["score"].get("tool_call_match")) for r in ins),
            "out_of_scope_n": len(oos),
            "out_of_scope_refused": sum(r["score"]["correct"] for r in oos),
        }
    return out


def provenance() -> dict[str, str]:
    """SHA-256 of the frozen held-out file and of the agent sources that define the systems."""
    from . import llm, router
    from .questions import heldout_sha256

    def h(mod) -> str:
        return hashlib.sha256(Path(mod.__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()

    return {"heldout_sha256": heldout_sha256(), "router_py_sha256": h(router),
            "llm_system_prompt_sha256": hashlib.sha256(llm.SYSTEM.encode("utf-8")).hexdigest()}


def run_router_eval() -> dict[str, Any]:
    rows = []
    for qs in QUESTIONS:
        res = router_answer(qs["question"])
        rows.append({"id": qs["id"], "split": qs["split"], "question": qs["question"], "out_of_scope": qs["out_of_scope"],
                     "known_before_rules": qs.get("known_before_rules", False),
                     "answer": res["answer"], "calls": [[n, a] for n, a in res["calls"]], "score": score(qs, res)})
    out = {"system": "deterministic keyword router (rebuilt from dev questions only)", "provenance": provenance(),
           "summary": summarize(rows), "rows": rows}
    write_json("agent_eval_router.json", out)
    return out


def run_llm_eval(model: str, splits: tuple[str, ...] = ("dev", "test", "hard", "heldout")) -> dict[str, Any]:
    from .llm import available, llm_answer

    models = available()
    if model not in models:
        raise SystemExit(f"model {model!r} not available from Ollama (have: {models})")
    rows = []
    for qs in QUESTIONS:
        if qs["split"] not in splits:
            continue
        t = time.time()
        res = llm_answer(qs["question"], model)
        dt = time.time() - t
        first = {"answer": res["first_answer"] or "", "refused": "OUT_OF_SCOPE" in (res["first_answer"] or ""),
                 "calls": res["calls"], "outputs": res["outputs"]}
        rows.append({
            "id": qs["id"], "split": qs["split"], "question": qs["question"], "out_of_scope": qs["out_of_scope"],
            "known_before_rules": qs.get("known_before_rules", False), "answer": res["answer"], "first_answer": res["first_answer"], "calls": [[n, a] for n, a in res["calls"]],
            "first_valid": res.get("first_valid"), "first_unsupported": res.get("first_unsupported", []),
            "repaired": res["repaired"], "fallback": res["fallback"], "seconds": round(dt, 1),
            "score": score(qs, res), "score_first_answer_raw": score(qs, first),
        })
        print(f"{qs['id']:4s} {qs['split']:4s} correct={rows[-1]['score']['correct']!s:5s} "
              f"raw={rows[-1]['score_first_answer_raw']['correct']!s:5s} fb={res['fallback']!s:5s} {dt:5.1f}s", flush=True)
    summ = summarize(rows)
    raw_rows = [dict(r, score=r["score_first_answer_raw"]) for r in rows]
    out = {
        "system": f"ollama:{model}",
        "provenance": provenance(),
        "summary_final": summ,
        "summary_first_answer_raw": summarize(raw_rows),
        "validator": {
            "first_answer_rejected": sum(1 for r in rows if r["first_valid"] is False),
            "repaired": sum(r["repaired"] for r in rows),
            "fallback_to_template": sum(r["fallback"] for r in rows),
        },
        "rows": rows,
    }
    safe = model.replace(":", "_").replace("/", "_")
    write_json(f"agent_eval_llm_{safe}.json", out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default=None, help="Ollama model name, e.g. qwen2.5:3b")
    ap.add_argument("--splits", default="dev,test,hard,heldout")
    a = ap.parse_args(argv)
    if a.llm:
        r = run_llm_eval(a.llm, tuple(a.splits.split(",")))
        print(r["summary_final"])
    else:
        r = run_router_eval()
        print(r["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
