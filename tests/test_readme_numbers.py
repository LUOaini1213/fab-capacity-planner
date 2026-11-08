"""Every number in the README's results tables must trace to a results/ file.

Table rows carry a source cell like `des_layers.json#layers.1_machines_only.starts_per_week.mean`
(optionally followed by `x100` for percentages). The first number in the row's value cell must
equal the referenced value rounded to the same number of decimals.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = re.compile(r"`([A-Za-z0-9_.]+\.json)#([^`]+)`(\s*x100)?")
NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _rows():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        for i, c in enumerate(cells):
            m = SRC.search(c)
            if m and i > 0:
                yield line, cells[i - 1], m.group(1), m.group(2), bool(m.group(3))


def _get(obj, path):
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def test_readme_has_traced_numbers():
    assert len(list(_rows())) >= 20


@pytest.mark.parametrize("line,value_cell,fname,path,pct", list(_rows()))
def test_readme_number_matches_results(line, value_cell, fname, path, pct):
    data = json.loads((ROOT / "results" / fname).read_text(encoding="ascii"))
    v = float(_get(data, path)) * (100.0 if pct else 1.0)
    m = NUM.search(value_cell.replace(",", ""))
    assert m, line
    shown = m.group(0)
    decimals = len(shown.split(".")[1]) if "." in shown else 0
    assert round(v, decimals) == float(shown), f"{line}: results give {v}"


# Prose numbers carry an invisible marker right after them: `7.4<!--src: a.json#x - a.json#y-->`.
# The expression may combine `file.json#key` references with numbers, + - * / and parentheses.
# The shown number is a magnitude (a range like `13.7-15.7` marks each end); signs go in the expression.
PROSE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*<!--src:(.*?)-->", re.S)
REF = re.compile(r"([A-Za-z0-9_.]+\.json)#([A-Za-z0-9_.]+)")


def _eval(expr):
    import ast
    import operator

    refs = {}

    def sub(m):
        name = f"_r{len(refs)}"
        data = json.loads((ROOT / "results" / m.group(1)).read_text(encoding="ascii"))
        refs[name] = float(_get(data, m.group(2)))
        return name

    tree = ast.parse(REF.sub(sub, expr).strip(), mode="eval")
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.USub: operator.neg}

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in refs:
            return refs[node.id]
        raise ValueError(f"unsupported expression: {expr!r}")

    return ev(tree)


def _prose():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return [(m.group(1), m.group(2).strip()) for m in PROSE.finditer(text)]


def test_readme_has_traced_prose_numbers():
    assert len(_prose()) >= 11
    # every marker must sit right after a number; a stray marker is a broken pin
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert text.count("<!--src:") == len(_prose())


@pytest.mark.parametrize("shown,expr", _prose())
def test_readme_prose_number_matches_results(shown, expr):
    v = _eval(expr)
    decimals = len(shown.split(".")[1]) if "." in shown else 0
    assert round(v, decimals) == float(shown), f"{shown} <!--src:{expr}-->: results give {v}"
