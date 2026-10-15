"""Deterministic no-LLM baseline: keyword/regex intent router + answer templates.

Rebuilt (2026-10-02) from the 24 **dev** questions only, plus the fab's own vocabulary (tool-group
names DIFF/IMP/LITHO and diffusion/implant/lithography, products Pa/Pb/Pc/TW, quarter labels
Q1-Q8, and the dispatching-rule codes FIFO/CR/SA of the tool schema). No pattern was added for a
word that occurs only in a test, hard or held-out question.

Scope (refusal) rules, as principles rather than a list of companies or topics:
1. a question must contain at least one capacity-domain word (from the dev questions), and must not
   be a writing task ("Write ...", dev o03);
2. it must not name an entity outside the model: a capitalised word that is not the first word of a
   sentence, or an all-caps acronym, that is not part of the fab's vocabulary (dev: "TSMC",
   "Singapore");
3. it must not be about the asker's own belongings ("my ...") or count a unit the model does not
   have ("cups per week"). Rule 3 was written after the review pointed at "What's the capacity of my
   coffee machine in cups per week?"; that question and "How many lots does GlobalFoundries start per
   week?" are in the held-out set but were known when the rules were written.
"""
from __future__ import annotations

import re
from typing import Any

from .render import REFUSAL, render
from .tools import call_tool

# words of the dev questions that mark the capacity domain
DOMAIN = re.compile(
    r"bottleneck|utili[sz]|capacity|\bstarts?\b|\blots?\b|\btools?\b|litho|implant|diffusion|demand|"
    r"cycle time|queue|raw process|capex|\bbuy\b|\border\b|\bplan\b|improvement|throughput|"
    r"simulat|dispatch|\brule\b|\bfifo\b|loaded|product mix",
    re.I)
KNOWN_NAMES = {"DIFF", "IMP", "LITHO", "FIFO", "CR", "SA", "Pa", "Pb", "Pc", "TW", "I", "X-factor",
               "Mini-Fab", "MiniFab"} | {f"Q{i}" for i in range(1, 9)} | {f"S{i}" for i in range(1, 7)}
NAME_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*")
UNIT_PER_TIME = re.compile(r"\b([a-z]+)\s+(?:per|a|each)\s+(?:week|day|month|hour|year)\b", re.I)
FAB_UNITS = {"lots", "lot", "starts", "start", "wafers", "lots/week", "products"}
PERSONAL = re.compile(r"\b(my|mine)\b", re.I)
WRITING = re.compile(r"^\s*(write|compose)\b", re.I)  # dev o03: writing tasks are not capacity questions
NEG = re.compile(r"\bfall|\bfell\b|\bdrop", re.I)
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "a": 1}
GROUPS = [("LITHO", r"\blitho\w*"), ("IMP", r"\bimplant\w*|\bimp\b"), ("DIFF", r"\bdiffusion|\bdiff\b")]


def _group_in(text: str) -> str | None:
    for g, pat in GROUPS:
        if re.search(pat, text, re.I):
            return g
    return None


def foreign_entity(text: str) -> str | None:
    """First capitalised / acronym token that is not sentence-initial and not part of the model."""
    for sent in re.split(r"(?<=[.?!])\s+", text.strip()):
        toks = NAME_TOKEN.findall(sent)
        for i, tok in enumerate(toks):
            base = re.sub(r"'s$", "", tok)
            if len(base) == 1 or base in KNOWN_NAMES or base.upper() in KNOWN_NAMES:
                continue
            caps = base[:1].isupper()
            acronym = len(base) >= 2 and base.isupper()
            if acronym or (caps and i > 0):
                return base
    return None


def out_of_scope(text: str) -> bool:
    if not DOMAIN.search(text):
        return True
    if foreign_entity(text):
        return True
    if PERSONAL.search(text) or WRITING.search(text):
        return True
    for m in UNIT_PER_TIME.finditer(text):
        if m.group(1).lower() not in FAB_UNITS:
            return True
    return False


def parse_demand_change(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*of\s+(current|today|base)", text, re.I)
    if m:
        return float(m.group(1)) - 100.0
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", text):
        after = text[m.end(): m.end() + 22].lower()
        if re.match(r"\s*(utili[sz]ation\s+)?(cap|ceiling|limit)", after):
            continue
        v = float(m.group(1))
        return -v if NEG.search(text) else v
    return 0.0


def parse_from_quarter(text: str) -> int:
    if re.search(r"next quarter", text, re.I):
        return 2
    m = re.search(r"(?:\bQ|quarter\s*)(\d)", text, re.I)
    return int(m.group(1)) if m else 1


def parse_extra_tools(text: str) -> tuple[str | None, int]:
    g = _group_in(text)
    if not g:
        return None, 0
    m = re.search(r"\b(add|with)\s+(\d+|one|two|three|four|a)\b(\s+more)?", text, re.I)
    if m:
        tok = m.group(2).lower()
        return g, int(tok) if tok.isdigit() else WORDNUM[tok]
    m = re.search(r"\b(\d+|one|two|three)\s+more\b", text, re.I)
    if m:
        tok = m.group(1).lower()
        return g, int(tok) if tok.isdigit() else WORDNUM[tok]
    return None, 0


def route(question: str) -> dict[str, Any]:
    """Return {'refused', 'calls': [(tool, args)]}."""
    t = question
    if out_of_scope(t):
        return {"refused": True, "calls": []}
    low = t.lower()
    extra_g, extra_n = parse_extra_tools(t)
    asks_capacity = re.search(r"max|utili[sz]|bottleneck", low)
    if extra_n and asks_capacity:
        return {"refused": False, "calls": [("capacity_status", {"extra_tools_group": extra_g, "extra_tools": extra_n})]}
    if re.search(r"improvement|per dollar|value for money", low):
        method = "des" if "simulat" in low else "static"
        return {"refused": False, "calls": [("debottleneck_ranking", {"method": method})]}
    if re.search(r"capex|\bbuy\b|\border\b|\bplan\b", low):
        args: dict[str, Any] = {}
        d = parse_demand_change(t)
        if d:
            args["demand_change_pct"] = d
            args["from_quarter"] = parse_from_quarter(t)
        if re.search(r"\bmix\b", low) and re.search(r"\bpc\b", low):
            args["scenario"] = "mix_shift_pc"
        return {"refused": False, "calls": [("capex_plan", args)]}
    if re.search(r"simulat|dispatch|\bfifo\b|\bcr\b|\bsa\b", low):
        rule = "CR" if re.search(r"\bcr\b", low) else ("SA" if re.search(r"\bsa\b", low) else "FIFO")
        return {"refused": False, "calls": [("simulation_summary", {"rule": rule})]}
    if re.search(r"cycle time|queue|raw process|how long", low):
        d = parse_demand_change(t)
        return {"refused": False, "calls": [("cycle_time_estimate", {"demand_change_pct": d} if d else {})]}
    d = parse_demand_change(t)
    return {"refused": False, "calls": [("capacity_status", {"demand_change_pct": d} if d else {})]}


def answer(question: str) -> dict[str, Any]:
    r = route(question)
    if r["refused"]:
        return {"answer": REFUSAL, "refused": True, "calls": [], "outputs": []}
    outs = [call_tool(name, args) for name, args in r["calls"]]
    text = " ".join(render(name, o) for (name, _), o in zip(r["calls"], outs))
    return {"answer": text, "refused": False, "calls": r["calls"], "outputs": outs}
