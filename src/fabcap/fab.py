"""Fab data model: products, routes, steps, tool groups.

All times are in minutes. A "lot visit" is one lot passing one route step.
A "job" is one machine run (a batch of up to ``batch_size`` lots on DIFF).
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


@dataclass(frozen=True)
class Step:
    name: str
    group: str
    load: float
    run: float
    unload: float
    batch_rule: str | None = None

    @property
    def occupancy(self) -> float:
        """Machine time per job: load + run + unload (Kempf's capacity convention)."""
        return self.load + self.run + self.unload


@dataclass(frozen=True)
class Group:
    name: str
    tools: int
    batch_size: int
    pm_minutes: float
    pm_period: str  # "shift" or "day"
    cell: int
    machine_names: tuple[str, ...]
    em: dict[str, Any] | None = None
    setup: dict[str, float] | None = None


@dataclass(frozen=True)
class Product:
    name: str
    kind: str  # "product" or "test"
    starts_per_week: float
    route: tuple[str, ...]


@dataclass
class Fab:
    name: str
    calendar: dict[str, float]
    products: dict[str, Product]
    steps: dict[str, Step]
    groups: dict[str, Group]
    test_lots: dict[str, Any] = field(default_factory=dict)
    personnel: dict[str, Any] = field(default_factory=dict)
    transport: dict[str, Any] = field(default_factory=dict)
    stockers: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers
    @property
    def week(self) -> float:
        return float(self.calendar["week"])

    def period_minutes(self, period: str) -> float:
        return float(self.calendar[period])

    def total_starts(self) -> float:
        return sum(p.starts_per_week for p in self.products.values())

    def copy(self) -> "Fab":
        return copy.deepcopy(self)

    # ------------------------------------------------------------- what-if API
    def with_starts(self, starts: dict[str, float]) -> "Fab":
        """Return a copy with the given starts per week (unlisted products -> 0)."""
        f = self.copy()
        for name, p in f.products.items():
            f.products[name] = Product(p.name, p.kind, float(starts.get(name, 0.0)), p.route)
        unknown = set(starts) - set(f.products)
        if unknown:
            raise KeyError(f"unknown products: {sorted(unknown)}")
        return f

    def scaled(self, factor: float) -> "Fab":
        return self.with_starts({n: p.starts_per_week * factor for n, p in self.products.items()})

    def with_group(self, group: str, **changes: Any) -> "Fab":
        f = self.copy()
        g = f.groups[group]
        data = dict(g.__dict__)
        data.update(changes)
        if "tools" in changes and "machine_names" not in changes:
            n = int(changes["tools"])
            names = list(g.machine_names)
            while len(names) < n:
                names.append(f"{group}{len(names) + 1}")
            data["machine_names"] = tuple(names[:n])
        f.groups[group] = Group(**data)
        return f

    def with_tools(self, group: str, tools: int) -> "Fab":
        if tools < 1:
            raise ValueError("a tool group needs at least one tool")
        return self.with_group(group, tools=int(tools))


def _build(raw: dict[str, Any]) -> Fab:
    steps = {
        n: Step(n, s["group"], float(s["load"]), float(s["run"]), float(s["unload"]), s.get("batch_rule"))
        for n, s in raw["steps"].items()
    }
    groups = {}
    for n, g in raw["groups"].items():
        groups[n] = Group(
            name=n,
            tools=int(g["tools"]),
            batch_size=int(g.get("batch_size", 1)),
            pm_minutes=float(g["pm"]["minutes"]),
            pm_period=g["pm"]["period"],
            cell=int(g.get("cell", 0)),
            machine_names=tuple(g.get("machine_names", [f"{n}{i + 1}" for i in range(int(g["tools"]))])),
            em=g.get("em"),
            setup=g.get("setup"),
        )
    products = {
        n: Product(n, p["kind"], float(p["starts_per_week"]), tuple(p["route"])) for n, p in raw["products"].items()
    }
    fab = Fab(
        name=raw["name"],
        calendar={k: float(v) for k, v in raw["calendar"].items()},
        products=products,
        steps=steps,
        groups=groups,
        test_lots=raw.get("test_lots", {}),
        personnel=raw.get("personnel", {}),
        transport=raw.get("transport", {}),
        stockers=raw.get("stockers", {}),
    )
    validate_fab(fab)
    return fab


def validate_fab(fab: Fab) -> None:
    for p in fab.products.values():
        for s in p.route:
            if s not in fab.steps:
                raise ValueError(f"product {p.name} routes through unknown step {s}")
        if p.starts_per_week < 0:
            raise ValueError("negative starts")
    for s in fab.steps.values():
        if s.group not in fab.groups:
            raise ValueError(f"step {s.name} uses unknown group {s.group}")
        if min(s.load, s.run, s.unload) < 0:
            raise ValueError(f"negative time in step {s.name}")
    for g in fab.groups.values():
        if g.pm_period not in fab.calendar:
            raise ValueError(f"unknown PM period {g.pm_period}")


def load_fab(path: str | Path | None = None) -> Fab:
    path = Path(path) if path else DATA_DIR / "minifab.json"
    return _build(json.loads(path.read_text(encoding="utf-8")))


def load_planning(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else DATA_DIR / "planning.json"
    return json.loads(path.read_text(encoding="utf-8"))


def planning_fab(fab: Fab | None = None, planning: dict[str, Any] | None = None) -> Fab:
    """Mini-Fab plus the illustrative extra product/steps of the planning layer."""
    fab = (fab or load_fab()).copy()
    planning = planning or load_planning()
    for n, s in planning.get("extra_steps", {}).items():
        fab.steps[n] = Step(n, s["group"], float(s["load"]), float(s["run"]), float(s["unload"]), s.get("batch_rule"))
    for n, p in planning.get("extra_products", {}).items():
        fab.products[n] = Product(n, p["kind"], 0.0, tuple(p["route"]))
    validate_fab(fab)
    return fab
