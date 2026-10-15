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
