import json

import pytest

from fabcap import io as fio
from fabcap.capacity import capacity_table
from fabcap.stats import mean_ci
from fabcap.whatif import OPTIONS, apply_option, rank, static_whatif


def test_options_modify_the_right_thing(mini):
    f, o = apply_option(mini, "add_LITHO")
    assert f.groups["LITHO"].tools == 2 and len(f.groups["LITHO"].machine_names) == 2 and o == {}
    f, _ = apply_option(mini, "halve_litho_setup")
    assert f.groups["LITHO"].setup == {"step_change": 5.0, "product_change": 2.5, "both_change": 6.0}
    f, _ = apply_option(mini, "halve_imp_repair")
    assert (f.groups["IMP"].em["repair_min"], f.groups["IMP"].em["repair_max"]) == (180.0, 240.0)
    assert mini.groups["IMP"].em["repair_min"] == 360  # original untouched
    f, _ = apply_option(mini, "litho_pm_30_to_20")
    assert f.groups["LITHO"].pm_minutes == 20.0
    f, _ = apply_option(mini, "add_operator_imp_litho")
    assert f.personnel["operators"]["PO3"]["groups"] == ["IMP", "LITHO"]
    assert "PO3" not in mini.personnel["operators"]
    _, o = apply_option(mini, "operators_litho_first")
    assert o == {"op_priority_group": "LITHO"}
    with pytest.raises(KeyError):
        apply_option(mini, "pray")


def test_static_whatif_gains(mini):
    s = static_whatif(mini)
    by = {r["option"]: r for r in s["options"]}
    assert set(by) == set(OPTIONS)
    base = capacity_table(mini)["max_starts_per_week"]
    assert s["baseline_max_starts_per_week"] == pytest.approx(base)
    # litho is the bottleneck: only litho levers help the static model
    assert by["add_LITHO"]["gain_starts_per_week"] > 0
    assert by["add_DIFF"]["gain_starts_per_week"] == pytest.approx(0.0)
    assert by["add_operator_imp_litho"]["gain_starts_per_week"] == pytest.approx(0.0)
    assert by["halve_litho_setup"]["gain_per_musd"] > by["add_LITHO"]["gain_per_musd"]
    assert by["add_LITHO"]["bottleneck_after"] == "DIFF"


def test_rank_order():
    rows = [
        {"option": "a", "cost_musd": 2.0, "g": 4.0},
        {"option": "b", "cost_musd": 0.0, "g": 1.0},
        {"option": "c", "cost_musd": 1.0, "g": 3.0},
        {"option": "d", "cost_musd": 1.0, "g": -0.5},
        {"option": "e", "cost_musd": 0.0, "g": 0.0},
    ]
    out = rank(rows, "g")
    assert [r["option"] for r in out] == ["b", "c", "a", "d", "e"]
    assert [r["rank"] for r in out] == [1, 2, 3, 4, 5]


def test_mean_ci():
    r = mean_ci([2.0, 4.0, 9.0])
    assert r["mean"] == pytest.approx(5.0)
    assert r["sd"] == pytest.approx(3.605551275463989)
    assert r["half_width"] == pytest.approx(4.302652729911275 * 3.605551275463989 / 3 ** 0.5)
    with pytest.raises(ValueError):
        mean_ci([])


def test_rounding_and_stable_json(tmp_path):
    assert fio.r10(1 / 3) == 0.3333333333
    assert fio.r10(123456.78901234) == 123456.789
    assert fio.r10(float("inf")) is None
    p = fio.write_json("x.json", {"b": 2.0 / 3.0, "a": (1, 2)}, results_dir=tmp_path)
    raw = p.read_bytes()
    assert b"\r" not in raw
    assert json.loads(raw) == {"a": [1, 2], "b": 0.6666666667}
    assert raw.index(b'"a"') < raw.index(b'"b"')
