"""Number validator: every number in an answer must equal a computed value of a tool output.

What it checks
* Known identifiers are removed before extraction: steps S1-S6/S3X/S4X, operators PO1-PO4,
  technicians MT1-MT2, machines Ma-Me, and exactly the queue names G/G/1, G/G/m, M/M/1.
  Every other digit sequence counts as a number, including ones glued to letters or units
  ("x12", "$18.5M", "3nm", ".5"). Quarter names (Q1, Q7, ...) are checked separately: each must
  occur in a tool output or in the question.
* A number is accepted only if it equals a *computed* value of a non-error tool output from the
  same conversation (absolute values compared, no rounding tolerance - tools already return
  presentation-rounded values).
* Numbers from a tool's ``error`` payload are never accepted (an error message can echo
  arbitrary text the model put into a tool name or argument).
* Values that a tool merely echoes from its arguments (``INPUT_KEYS``) and outputs that are a
  direct transform of the arguments (listed by the tool in ``arg_derived``, e.g. starts/week =
  84 x (1 + demand change), or a tool count after adding tools) are accepted only if the same
  number also occurs in the user's question. This stops a model from laundering an invented
  number through a tool argument.
* Dotted digit runs such as "81.5.999" or "1.2.3" are rejected as malformed (they could hide a
  number behind one that is allowed).

Not guaranteed (documented in the README): numbers written as words; scientific or other
notations ("1e2" is read as 1 and 2); the *direction* or meaning attached to a correct number
("adds 12.2" vs a tool value of -12.2 passes, signs are compared as absolute values); a correct
number attached to the wrong claim; small integers such as ranks and tool counts are present in
many outputs and therefore hard to misuse detectably; tool arguments that are wrong but stated
in the question.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

IDENT = re.compile(r"\b(?:S[1-6]X?|PO[1-4]|MT[12]|M[a-e])\b|\b(?:G/G/[1m]|M/M/1)\b")
QUARTER = re.compile(r"\bQ(\d+)\b")
_NUM = re.compile(r"(?<![\d.])[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)")
DOTTED = re.compile(r"\d+(?:\.\d+){2,}")
INPUT_KEYS = frozenset({"demand_change_pct", "from_quarter", "extra_tools"})
META_KEYS = frozenset({"arg_derived"})


def extract_numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(QUARTER.sub(" ", IDENT.sub(" ", text or ""))):
        tok = m.group(0).replace(",", "")
        try:
            out.append(float(tok))
        except ValueError:  # pragma: no cover - regex guarantees a number
            continue
    return out


def extract_quarters(text: str) -> list[str]:
    return ["Q" + m.group(1) for m in QUARTER.finditer(text or "")]


def _get_path(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def numeric_leaves(obj: Any, skip_keys: frozenset[str] = frozenset()) -> set[float]:
    vals: set[float] = set()
    if isinstance(obj, bool) or obj is None:
        return vals
    if isinstance(obj, (int, float)):
        vals.add(float(obj))
    elif isinstance(obj, str):
        vals.update(extract_numbers(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip_keys:
                continue
            vals |= numeric_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            vals |= numeric_leaves(v)
    return vals


def _strings_and_keys(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            out += _strings_and_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out += _strings_and_keys(v)
    return out


def _derived_free(o: dict[str, Any]) -> tuple[dict[str, Any], set[float]]:
    """Copy of a tool output without its argument-derived leaves, and the set of those values."""
    paths = [p for p in o.get("arg_derived", []) if isinstance(p, str)]
    derived: set[float] = set()
    clean: dict[str, Any] = {k: v for k, v in o.items()}
    for p in paths:
        derived |= {abs(v) for v in numeric_leaves(_get_path(o, p))}
        parts = p.split(".")
        node = clean
        for part in parts[:-1]:  # copy along the path, then drop the leaf
            if not isinstance(node.get(part), dict):
                node = None
                break
            node[part] = dict(node[part])
            node = node[part]
        if node is not None:
            node.pop(parts[-1], None)
    return clean, derived


def _usable(tool_outputs: Iterable[Any]) -> list[Any]:
    return [o for o in tool_outputs if not (isinstance(o, dict) and "error" in o)]


def allowed_numbers(tool_outputs: Iterable[Any], question: str | None = None) -> set[float]:
    allowed: set[float] = set()
    echoed: set[float] = set()
    for o in _usable(tool_outputs):
        if isinstance(o, dict):
            clean, derived = _derived_free(o)
            echoed |= derived
            allowed |= {abs(v) for v in numeric_leaves(clean, INPUT_KEYS | META_KEYS)}
            for k in INPUT_KEYS & set(o):
                echoed |= {abs(v) for v in numeric_leaves(o[k])}
        else:
            allowed |= {abs(v) for v in numeric_leaves(o)}
    asked = {abs(v) for v in extract_numbers(question or "")}
    return allowed | (echoed & asked)


def allowed_quarters(tool_outputs: Iterable[Any], question: str | None = None) -> set[str]:
    out = set(extract_quarters(question or ""))
    for o in _usable(tool_outputs):
        for s in _strings_and_keys(o):
            out |= set(extract_quarters(s))
    return out


def validate_answer(answer: str, tool_outputs: Iterable[Any], question: str | None = None) -> dict[str, Any]:
    outs = list(tool_outputs)
    allowed = allowed_numbers(outs, question)
    nums = extract_numbers(answer)
    bad = [x for x in nums if abs(x) not in allowed]
    quarters = allowed_quarters(outs, question)
    bad_q = [q for q in extract_quarters(answer) if q not in quarters]
    malformed = DOTTED.findall(answer or "")
    return {"ok": not bad and not bad_q and not malformed, "numbers": nums, "unsupported": bad,
            "unsupported_quarters": bad_q, "malformed": malformed}
