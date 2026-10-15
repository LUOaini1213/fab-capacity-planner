"""Optional LLM agent via a local Ollama server (tool calling).

The LLM chooses tools and arguments and writes the final sentence; it never
supplies numbers on its own authority: the final answer is checked by
``validator.validate_answer`` against the tool outputs of this conversation.
On failure the model gets one repair turn; if it fails again the answer is
replaced by the deterministic template rendering of the same tool outputs
(and the replacement is counted in the evaluation).
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from .render import REFUSAL, render
from .tools import SCHEMAS, call_tool
from .validator import validate_answer

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
if not DEFAULT_HOST.startswith("http"):
    DEFAULT_HOST = "http://" + DEFAULT_HOST

SYSTEM = (
    "You are a capacity-planning assistant for a small semiconductor fab model (the Intel Mini-Fab teaching case). "
    "Tool groups: DIFF = diffusion furnaces, IMP = ion implanters, LITHO = lithography. "
    "Answer ONLY by calling the provided tools; never compute, estimate or invent numbers yourself. "
    "In the final answer copy numbers exactly as they appear in the tool outputs, without rounding or unit conversion. "
    "Argument conventions: 'demand rises 20%' -> demand_change_pct=20; 'falls 10%' -> -10; '70% of current demand' -> -30; "
    "'next quarter' -> from_quarter=2; 'from quarter 3' -> from_quarter=3; 'add one litho tool' -> extra_tools_group='LITHO', extra_tools=1; "
    "'according to the simulation' -> method='des'. "
    "If the question is not about this fab model (utilization, bottleneck, starts, capacity, cycle time, raw process "
    "time, queue time, tool purchases/capex, hiring, debottlenecking options, dispatching/simulation results) - for "
    "example about another company, a real-world fab, or anything that is not this model - reply with "
    "exactly OUT_OF_SCOPE and nothing else. Every number you write must be copied from a tool output; do not repeat "
    "numbers from the question unless the tool output contains them. "
    "Keep the final answer to 1-3 sentences and name the tool group or option explicitly."
)

REPAIR = ("Your answer contains numbers that do not appear in the tool outputs: {bad}. Rewrite the answer using only "
          "numbers copied exactly from the tool outputs above. Do not round or convert.")


class OllamaError(RuntimeError):
    pass


def _chat(model: str, messages: list[dict[str, Any]], tools: list[dict] | None, host: str, timeout: float) -> dict:
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": False,
                            "options": {"temperature": 0, "seed": 7, "num_ctx": 8192}}
    if tools:
        body["tools"] = tools
    if model.startswith("qwen3"):
        body["think"] = False
    req = urllib.request.Request(host.rstrip("/") + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # pragma: no cover - network
        raise OllamaError(str(e)) from e


def available(host: str = DEFAULT_HOST) -> list[str]:
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read().decode("utf-8")).get("models", [])]
    except Exception:
        return []


def llm_answer(question: str, model: str, host: str = DEFAULT_HOST, max_rounds: int = 4, timeout: float = 300) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    calls: list[tuple[str, dict]] = []
    outputs: list[dict] = []
    content = ""
    for _ in range(max_rounds):
        msg = _chat(model, messages, SCHEMAS, host, timeout)["message"]
        tcs = msg.get("tool_calls") or []
        if tcs:
            messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tcs})
            for tc in tcs:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                out = call_tool(name, args)
                calls.append((name, args))
                outputs.append(out)
                messages.append({"role": "tool", "content": json.dumps(out), "tool_name": name})
            continue
        content = (msg.get("content") or "").strip()
        break
    result: dict[str, Any] = {"calls": calls, "outputs": outputs, "first_answer": content, "repaired": False,
                              "fallback": False}
    if "OUT_OF_SCOPE" in content:  # also after a tool call: the model looked, then declined
        result.update(answer=REFUSAL, refused=True, first_valid=True)
        return result
    v = validate_answer(content, outputs, question)
    result["first_valid"] = v["ok"]
    result["first_unsupported"] = v["unsupported"]
    if v["ok"] and content:
        result.update(answer=content, refused=False)
        return result
    # one repair turn
    if content:
        messages.append({"role": "assistant", "content": content})
        bad = [f"{x:g}" for x in v["unsupported"]] + v["unsupported_quarters"] + v["malformed"]
        messages.append({"role": "user", "content": REPAIR.format(bad=", ".join(bad))})
        content2 = (_chat(model, messages, None, host, timeout)["message"].get("content") or "").strip()
        v2 = validate_answer(content2, outputs, question)
        if v2["ok"] and content2 and "OUT_OF_SCOPE" not in content2:
            result.update(answer=content2, refused=False, repaired=True)
            return result
    if not calls:
        # no tool output to fall back on: answer is unsupported -> refuse rather than guess
        result.update(answer=REFUSAL, refused=True, fallback=True)
        return result
    text = " ".join(render(n, o) for (n, _), o in zip(calls, outputs))
    result.update(answer=text, refused=False, fallback=True)
    return result
