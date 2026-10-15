import pytest

from fabcap.fab import _build

TOY = {
    "name": "toy",
    "calendar": {"week": 10080, "day": 1440, "shift": 720},
    "products": {
        "A": {"kind": "product", "starts_per_week": 30, "route": ["X1", "Y1", "X2"]},
        "B": {"kind": "product", "starts_per_week": 12, "route": ["X1", "Y1", "X2"]},
    },
    "steps": {
        "X1": {"group": "X", "load": 5, "run": 100, "unload": 15, "batch_rule": "mix_products"},
        "Y1": {"group": "Y", "load": 3, "run": 20, "unload": 7},
        "X2": {"group": "X", "load": 10, "run": 140, "unload": 10, "batch_rule": "no_product_mix"},
    },
    "groups": {
        "X": {"tools": 3, "batch_size": 2, "pm": {"minutes": 60, "period": "day"}, "cell": 1},
        "Y": {"tools": 1, "batch_size": 1, "pm": {"minutes": 40, "period": "shift"}, "cell": 2,
              "em": {"tbf_min": 1000, "tbf_max": 3000, "repair_min": 200, "repair_max": 400,
                     "spec_events_per_week": 3},
              "setup": {"step_change": 7, "product_change": 4, "both_change": 9}},
    },
    "transport": {"positions": {"STARTS": 0, "X": 1, "Y": 2, "OUTS": 3}, "travel_adjacent": 2, "load": 1, "unload": 1},
    "personnel": {"operators": {}, "operator_off_time_per_shift": [], "technician_off_time_per_shift": [],
                  "move_time_adjacent_cell": 1},
}


@pytest.fixture
def toy():
    import copy
    return _build(copy.deepcopy(TOY))


@pytest.fixture(scope="session")
def mini():
    from fabcap.fab import load_fab
    return load_fab()
