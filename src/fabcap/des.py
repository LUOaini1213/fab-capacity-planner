"""SimPy discrete-event simulation of the re-entrant Mini-Fab.

Modelled (from Kempf's specification): 3 tool groups / 5 machines / 6 steps,
re-entrant route, full-batch diffusion with the S1/S5 mixing rules and at most
one test lot per batch, test lots never on the same machine twice, litho
sequence-dependent setups, PM windows per shift/day, implant emergency
failures that scrap the lot in process, two operators with eligibility and
off-time, one technician with off-time, one transporter with travel times.

Simplifications (documented in docs/model.md): stocker capacities are not
enforced (peak contents are reported instead); off-time is taken at fixed
offsets within each shift; PM may be postponed while the technicians are busy
until the PM window closes.

Failure clock (``SimConfig.failure_clock``): "calendar" draws the time to the
next failure in calendar time from the end of the previous repair (a clock that
expires while the machine is idle fires at the start of its next run);
"run" draws it in run minutes, so only running time ages the machine (Kempf:
"a machine must be running for the surprise breakdown to happen").
``SimConfig.tbf_scale`` multiplies both bounds of the time-between-failure
distribution; ``capacity.des_tbf_scale`` derives it for a target failure rate.

Dispatching rules: FIFO, CR (critical ratio), SA (setup avoidance at litho,
FIFO elsewhere).
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import simpy

from .capacity import raw_process_time, setup_between
from .fab import Fab

RULES = ("FIFO", "CR", "SA")
RELEASES = ("deterministic", "poisson", "conwip")
FAILURE_CLOCKS = ("calendar", "run")
OPERATOR_BREAK_OFFSETS = {"PO1": (180.0, 360.0, 540.0), "PO2": (240.0, 420.0, 600.0), "PO3": (300.0, 480.0, 660.0),
                          "PO4": (210.0, 390.0, 570.0)}
TECH_BREAK_OFFSETS = {"MT1": (200.0, 400.0, 620.0), "MT2": (260.0, 460.0, 680.0)}


@dataclass(frozen=True)
class SimConfig:
    rule: str = "FIFO"
    start_scale: float = 1.0
    release: str = "deterministic"  # "deterministic" | "poisson" | "conwip"
    conwip_wip: int = 60  # lots kept in the line when release == "conwip"
    op_priority_group: str | None = None  # operators serve this group's requests first
    warmup_weeks: float = 20.0
    run_weeks: float = 100.0
    operators: bool = True
    technician: bool = True
    transport: bool = True
    failures: bool = True
    pm: bool = True
    setups: bool = True
    due_xf: float = 3.0
    failure_clock: str = "calendar"  # "calendar" | "run" (see module docstring)
    tbf_scale: float = 1.0  # multiplies the time-between-failure bounds

    def label(self) -> str:
        parts = [self.rule, f"x{self.start_scale:g}", self.release]
        if self.release == "conwip":
            parts.append(f"w{self.conwip_wip}")
        if self.op_priority_group:
            parts.append(f"op-first-{self.op_priority_group}")
        for flag in ("operators", "technician", "transport", "failures", "pm", "setups"):
            if not getattr(self, flag):
                parts.append(f"no-{flag}")
        if self.failure_clock != "calendar" or self.tbf_scale != 1.0:
            parts.append(f"em-{self.failure_clock}-x{self.tbf_scale:g}")
        return "/".join(parts)


def _r(x: float) -> float:
    """Round sampled durations so libm differences across platforms cannot leak in."""
    return round(x, 6)


class Lot:
    __slots__ = ("id", "product", "kind", "route", "idx", "release", "due", "raw_left", "hist", "arrive", "seq")

    def __init__(self, lid: int, product: str, kind: str, route: tuple[str, ...], release: float, due: float,
                 raw_left: list[float]):
        self.id = lid
        self.product = product
        self.kind = kind
        self.route = route
        self.idx = 0
        self.release = release
        self.due = due
        self.raw_left = raw_left
        self.hist: dict[str, str] = {}
        self.arrive = release
        self.seq = lid

    @property
    def step(self) -> str:
        return self.route[self.idx]


class Person:
    def __init__(self, env: simpy.Environment, name: str, groups: Iterable[str], loc: int):
        self.name = name
        self.groups = tuple(groups)
        self.res = simpy.PriorityResource(env, capacity=1)
        self.loc = loc
        self.busy = 0.0

    def free(self) -> bool:
        return self.res.count == 0 and len(self.res.queue) == 0


class Machine:
    def __init__(self, group: str, idx: int, name: str):
        self.group = group
        self.idx = idx
        self.name = name
        self.last_type: tuple[str, str] | None = None
        self.next_fail = math.inf
        self.pm_due = 0.0
        self.pm_close = math.inf
        self.rng: random.Random | None = None


class FabSim:
    def __init__(self, fab: Fab, cfg: SimConfig, seed: int):
        if cfg.rule not in RULES:
            raise ValueError(f"rule must be one of {RULES}")
        if cfg.release not in RELEASES:
            raise ValueError(f"release must be one of {RELEASES}")
        if cfg.failure_clock not in FAILURE_CLOCKS:
            raise ValueError(f"failure_clock must be one of {FAILURE_CLOCKS}")
        if not cfg.tbf_scale > 0:
            raise ValueError("tbf_scale must be positive")
        self.fab = fab
        self.cfg = cfg
        self.seed = seed
        self.env = simpy.Environment()
        self.week = fab.week
        self.t_start = cfg.warmup_weeks * self.week
        self.t_end = (cfg.warmup_weeks + cfg.run_weeks) * self.week
        self.rng_release = random.Random(seed * 1000 + 1)
        self.queues: dict[str, list[Lot]] = {g: [] for g in fab.groups}
        self.wake: dict[str, simpy.Event] = {g: self.env.event() for g in fab.groups}
        self.cell = {g: int(fab.transport["positions"][g]) for g in fab.groups}
        self.max_test = int(fab.test_lots.get("max_per_batch", 1))
        self.avoid_same = bool(fab.test_lots.get("avoid_same_machine_twice", False))
        # statistics
        self.state = {g: {s: 0.0 for s in ("load", "run", "unload", "setup", "pm", "repair", "wait_operator",
                                            "wait_tech", "failed_run")} for g in fab.groups}
        self.completions: list[tuple[float, str, float]] = []
        self.visit_wait: dict[str, list[float]] = {g: [] for g in fab.groups}
        self.visit_wait_by_step: dict[str, list[float]] = {s: [] for s in fab.steps}
        self.scrapped = 0
        self.scrapped_window = 0
        self.released_window = 0
        self.setups_window = 0
        self.setup_min_window = 0.0
        self.jobs_window = {g: 0 for g in fab.groups}
        self.failures_window = {g: 0 for g in fab.groups}
        self.wip = 0
        self.wip_area = 0.0
        self.wip_last_t = 0.0
        self.wip_thirds = [0.0, 0.0, 0.0]
        # lots present in a cell (stocker + on a machine) and lots on a machine; stocker = difference
        self.in_cell = {g: 0 for g in fab.groups}
        self.in_proc = {g: 0 for g in fab.groups}
        self.max_stocker = {g: 0 for g in fab.groups}
        self.lot_counter = 0
        self.completed_total = 0
        # machines
        self.machines: list[Machine] = []
        for g, grp in fab.groups.items():
            for i, name in enumerate(grp.machine_names[: grp.tools]):
                m = Machine(g, i, name)
                m.rng = random.Random(seed * 1000 + 10 + len(self.machines))
                if cfg.failures and grp.em:
                    # calendar clock: absolute time of the next failure; run clock: run minutes left
                    m.next_fail = self._draw_tbf(m, grp)
                if cfg.pm:
                    m.pm_due = 0.0
                    m.pm_close = fab.period_minutes(grp.pm_period)
                self.machines.append(m)
        # personnel
        self.ops_for: dict[str, list[Person]] = {g: [] for g in fab.groups}
        self.people: list[Person] = []
        move = float(fab.personnel.get("move_time_adjacent_cell", 0))
        self.move = move
        if cfg.operators:
            for name, spec in fab.personnel["operators"].items():
                p = Person(self.env, name, spec["groups"], self.cell[spec["groups"][0]])
                self.people.append(p)
                for g in spec["groups"]:
                    self.ops_for[g].append(p)
                offs = OPERATOR_BREAK_OFFSETS.get(name, (180.0, 360.0, 540.0))
                self.env.process(self._breaks(p, offs, fab.personnel["operator_off_time_per_shift"]))
        self.techs: list[Person] = []
        self.tech_free_ev = self.env.event()
        if cfg.technician:
            for name in (list(fab.personnel.get("technicians", {})) or ["MT1"]):
                t = Person(self.env, name, tuple(fab.groups), self.cell[next(iter(fab.groups))])
                self.techs.append(t)
                offs = TECH_BREAK_OFFSETS.get(name, TECH_BREAK_OFFSETS["MT1"])
                self.env.process(self._breaks(t, offs, fab.personnel["technician_off_time_per_shift"]))
        self.tech: Person | None = self.techs[0] if self.techs else None
        # transporter
        self.transporter = simpy.Resource(self.env, capacity=1) if cfg.transport else None
        self.tr_loc = int(fab.transport.get("positions", {}).get("STARTS", 0))
        self.raw = {p: raw_process_time(fab, p) for p in fab.products}
        self.raw_left = {
            p: [sum(fab.steps[s].occupancy for s in prod.route[i:]) for i in range(len(prod.route) + 1)]
            for p, prod in fab.products.items()
        }
        for m in self.machines:
            self.env.process(self._machine(m))
        if cfg.release == "conwip":
            total = sum(p.starts_per_week for p in fab.products.values())
            self.mix = {n: p.starts_per_week / total for n, p in fab.products.items() if p.starts_per_week > 0}
            self.mix_count = {n: 0 for n in self.mix}
            for _ in range(int(cfg.conwip_wip)):
                self._release_one(self._next_conwip_product())
        else:
            for p in fab.products.values():
                rate = p.starts_per_week * cfg.start_scale
                if rate > 0:
                    self.env.process(self._release(p.name, rate))

    def _draw_tbf(self, m: Machine, grp) -> float:
        """Time between failures: calendar minutes or run minutes, depending on the failure clock."""
        s = self.cfg.tbf_scale
        return _r(m.rng.uniform(grp.em["tbf_min"] * s, grp.em["tbf_max"] * s))

    # ------------------------------------------------------------------ accounting
    def _acc(self, group: str, state: str, t0: float, t1: float) -> None:
        a, b = max(t0, self.t_start), min(t1, self.t_end)
        if b > a:
            self.state[group][state] += b - a

    def _wip_change(self, delta: int) -> None:
        now = self.env.now
        a, b = max(self.wip_last_t, self.t_start), min(now, self.t_end)
        if b > a:
            self.wip_area += self.wip * (b - a)
            span = (self.t_end - self.t_start) / 3.0
            for k in range(3):
                lo, hi = self.t_start + k * span, self.t_start + (k + 1) * span
                x, y = max(a, lo), min(b, hi)
                if y > x:
                    self.wip_thirds[k] += self.wip * (y - x)
        self.wip_last_t = now
        self.wip += delta

    def _in_window(self) -> bool:
        return self.t_start <= self.env.now < self.t_end

    def _notify(self, group: str) -> None:
        ev = self.wake[group]
        self.wake[group] = self.env.event()
        if not ev.triggered:
            ev.succeed()

    def _notify_tech(self) -> None:
        ev = self.tech_free_ev
        self.tech_free_ev = self.env.event()
        if not ev.triggered:
            ev.succeed()

    # ------------------------------------------------------------------ processes
    def _breaks(self, person: Person, offsets: Iterable[float], durations: Iterable[float]):
        shift = self.fab.calendar["shift"]
        offsets, durations = tuple(offsets), tuple(durations)
        k = 0
        while True:
            for off, dur in zip(offsets, durations):
                t = k * shift + off
                if t > self.env.now:
                    yield self.env.timeout(t - self.env.now)
                req = person.res.request(priority=0)
                yield req
                yield self.env.timeout(dur)
                person.res.release(req)
                if person in self.techs:
                    self._notify_tech()
            k += 1

    def _next_conwip_product(self) -> str:
        """Smooth weighted round robin: the product furthest behind its target share."""
        n = sum(self.mix_count.values()) + 1
        best = max(self.mix, key=lambda p: (self.mix[p] * n - self.mix_count[p], -list(self.mix).index(p)))
        self.mix_count[best] += 1
        return best

    def _release_one(self, product: str) -> None:
        prod = self.fab.products[product]
        now = self.env.now
        self.lot_counter += 1
        lot = Lot(self.lot_counter, product, prod.kind, prod.route, now,
                  now + self.cfg.due_xf * self.raw[product], self.raw_left[product])
        if self._in_window():
            self.released_window += 1
        self._wip_change(+1)
        self.env.process(self._move(lot, None, self.fab.steps[prod.route[0]].group))

    def _lot_left(self) -> None:
        """A lot completed or was scrapped: under CONWIP release a replacement."""
        if self.cfg.release == "conwip" and self.env.now < self.t_end:
            self._release_one(self._next_conwip_product())

    def _release(self, product: str, rate_per_week: float):
        interval = self.week / rate_per_week
        if self.cfg.release == "deterministic":
            yield self.env.timeout(_r(self.rng_release.uniform(0, interval)))
        while True:
            if self.cfg.release == "poisson":
                yield self.env.timeout(_r(self.rng_release.expovariate(1.0 / interval)))
            if self.env.now >= self.t_end:
                return
            self._release_one(product)
            if self.cfg.release == "deterministic":
                yield self.env.timeout(interval)

    def _move(self, lot: Lot, src_group: str | None, dst_group: str | None):
        pos = self.fab.transport.get("positions", {})
        src = self.cell[src_group] if src_group else int(pos.get("STARTS", 0))
        dst = self.cell[dst_group] if dst_group else int(pos.get("OUTS", 0))
        if self.transporter is not None:
            tr = self.fab.transport
            with self.transporter.request() as req:
                yield req
                dead = tr["travel_adjacent"] * abs(self.tr_loc - src)
                if dead:
                    yield self.env.timeout(dead)
                if src_group:
                    self.in_cell[src_group] -= 1
                yield self.env.timeout(tr["load"] + tr["travel_adjacent"] * abs(src - dst) + tr["unload"])
                self.tr_loc = dst
        elif src_group:
            self.in_cell[src_group] -= 1
        if dst_group is None:
            self._complete(lot)
            return
        lot.arrive = self.env.now
        self.queues[dst_group].append(lot)
        self.in_cell[dst_group] += 1
        self._stocker_peak(dst_group)
        self._notify(dst_group)

    def _stocker_peak(self, g: str) -> None:
        """Track the peak stocker content (lots in the cell but not on a machine) inside the window."""
        if self._in_window():
            self.max_stocker[g] = max(self.max_stocker[g], self.in_cell[g] - self.in_proc[g])

    def _complete(self, lot: Lot) -> None:
        self._wip_change(-1)
        self.completed_total += 1
        if self._in_window():
            self.completions.append((self.env.now, lot.product, self.env.now - lot.release))
        self._lot_left()

    # ------------------------------------------------------------------ dispatching
    def _eligible(self, lot: Lot, m: Machine) -> bool:
        if lot.kind == "test" and self.avoid_same and self.fab.groups[m.group].tools > 1:
            return lot.hist.get(m.group) != m.name
        return True

    def _ordered(self, q: list[Lot], m: Machine) -> list[Lot]:
        rule = self.cfg.rule
        if rule == "FIFO":
            return sorted(q, key=lambda l: (l.arrive, l.seq))
        if rule == "CR":
            now = self.env.now
            return sorted(q, key=lambda l: ((l.due - now) / max(l.raw_left[l.idx], 1.0), l.arrive, l.seq))
        # SA: setup avoidance on groups with setups, FIFO elsewhere
        g = self.fab.groups[m.group]
        if g.setup and self.cfg.setups:
            return sorted(q, key=lambda l: (setup_between(g, m.last_type, (l.product, l.step)) if m.last_type else 0.0,
                                            l.arrive, l.seq))
        return sorted(q, key=lambda l: (l.arrive, l.seq))

    def _select(self, m: Machine) -> list[Lot] | None:
        q = self.queues[m.group]
        if not q:
            return None
        k = self.fab.groups[m.group].batch_size
        order = self._ordered(q, m)
        if k == 1:
            for lot in order:
                if self._eligible(lot, m):
                    return [lot]
            return None
        for head in order:
            if not self._eligible(head, m):
                continue
            step = head.step
            rule = self.fab.steps[step].batch_rule
            batch = [head]
            n_test = 1 if head.kind == "test" else 0
            prod = head.product if head.kind != "test" else None
            for lot in order:
                if lot is head or lot.step != step or not self._eligible(lot, m):
                    continue
                if lot.kind == "test":
                    if n_test >= self.max_test:
                        continue
                    n_test += 1
                elif rule == "no_product_mix":
                    if prod is None:
                        prod = lot.product
                    elif lot.product != prod:
                        continue
                batch.append(lot)
                if len(batch) == k:
                    return batch
        return None

    # ------------------------------------------------------------------ personnel
    def _acquire(self, people: list[Person], cell: int, priority: int = 2):
        reqs = [p.res.request(priority=priority) for p in people]
        if len(reqs) == 1:
            yield reqs[0]
            chosen = 0
        else:
            yield simpy.AnyOf(self.env, reqs)
            chosen = next(i for i, r in enumerate(reqs) if r.triggered)
            for i, r in enumerate(reqs):
                if i == chosen:
                    continue
                if r.triggered:
                    people[i].res.release(r)
                else:
                    r.cancel()
            if people is self.techs:  # grants on the other technicians were handed back: wake waiting PMs
                self._notify_tech()
        person = people[chosen]
        travel = self.move * abs(person.loc - cell)
        if travel:
            yield self.env.timeout(travel)
        person.loc = cell
        return person, reqs[chosen]

    def _with_operator(self, m: Machine, segments: list[tuple[float, str]]):
        """Hold ONE operator (if modelled) for consecutive ``(minutes, state)`` segments on machine m.

        Setup and load are passed together, so the operator who sets the tool up also loads it
        without releasing in between (S1: a setup is done immediately prior to the run)."""
        g = m.group
        held = self.cfg.operators and self.ops_for[g]
        if held:
            t0 = self.env.now
            prio = 1 if self.cfg.op_priority_group == g else 2
            person, req = yield from self._acquire(self.ops_for[g], self.cell[g], prio)
            self._acc(g, "wait_operator", t0, self.env.now)
        for minutes, state in segments:
            t1 = self.env.now
            yield self.env.timeout(minutes)
            self._acc(g, state, t1, self.env.now)
        if held:
            person.res.release(req)

    def _with_tech(self, m: Machine, minutes: float, state: str):
        g = m.group
        if self.techs:
            t0 = self.env.now
            person, req = yield from self._acquire(self.techs, self.cell[g])
            self._acc(g, "wait_tech", t0, self.env.now)
            t1 = self.env.now
            yield self.env.timeout(minutes)
            self._acc(g, state, t1, self.env.now)
            person.res.release(req)
            self._notify_tech()
        else:
            t1 = self.env.now
            yield self.env.timeout(minutes)
            self._acc(g, state, t1, self.env.now)

    # ------------------------------------------------------------------ machine
    def _schedule_next_pm(self, m: Machine) -> None:
        grp = self.fab.groups[m.group]
        period = self.fab.period_minutes(grp.pm_period)
        window_start = math.floor(m.pm_due / period) * period
        m.pm_due = max(window_start + period, self.env.now + period / 2.0)
        m.pm_close = math.floor(m.pm_due / period) * period + period

    def _machine(self, m: Machine):
        grp = self.fab.groups[m.group]
        while True:
            now = self.env.now
            if self.cfg.pm and now >= m.pm_due:
                if not self.techs or any(t.free() for t in self.techs) or now >= m.pm_close:
                    yield from self._with_tech(m, grp.pm_minutes, "pm")
                    self._schedule_next_pm(m)
                    continue
            job = self._select(m)
            if job is None:
                waits: list[simpy.Event] = [self.wake[m.group]]
                if self.cfg.pm:
                    if now < m.pm_due:
                        waits.append(self.env.timeout(m.pm_due - now))
                    else:
                        waits.append(self.tech_free_ev)
                        if now < m.pm_close:
                            waits.append(self.env.timeout(m.pm_close - now))
                yield simpy.AnyOf(self.env, waits)
                continue
            for lot in job:
                self.queues[m.group].remove(lot)
            self.in_proc[m.group] += len(job)
            yield from self._process(m, job)

    def _process(self, m: Machine, job: list[Lot]):
        g = m.group
        grp = self.fab.groups[g]
        step = self.fab.steps[job[0].step]
        start = self.env.now
        in_win = self._in_window()
        for lot in job:
            w = start - lot.arrive
            if in_win:
                self.visit_wait[g].append(w)
                self.visit_wait_by_step[lot.step].append(w)
        jtype = (job[0].product, job[0].step)
        setup = 0.0
        if self.cfg.setups and grp.setup and m.last_type is not None:
            setup = setup_between(grp, m.last_type, jtype)
        if grp.setup:
            m.last_type = jtype
        segments = [(step.load, "load")]
        if setup > 0:
            if in_win:
                self.setups_window += 1
                self.setup_min_window += setup
            segments.insert(0, (setup, "setup"))
        yield from self._with_operator(m, segments)
        run_start = self.env.now
        fail_at = None
        run_clock = self.cfg.failure_clock == "run"
        if self.cfg.failures and grp.em:
            if run_clock:
                if m.next_fail < step.run:  # run minutes left before the failure
                    fail_at = run_start + m.next_fail
            elif m.next_fail <= run_start:
                fail_at = run_start
            elif m.next_fail < run_start + step.run:
                fail_at = m.next_fail
        if fail_at is not None:
            if fail_at > run_start:
                yield self.env.timeout(fail_at - run_start)
            self._acc(g, "failed_run", run_start, self.env.now)
            if self._in_window():
                self.failures_window[g] += 1
                self.scrapped_window += len(job)
            self.scrapped += len(job)
            self.in_proc[g] -= len(job)
            for _ in job:
                self.in_cell[g] -= 1
                self._wip_change(-1)
                self._lot_left()
            repair = _r(m.rng.uniform(grp.em["repair_min"], grp.em["repair_max"]))
            yield from self._with_tech(m, repair, "repair")
            m.next_fail = self._draw_tbf(m, grp) + (0.0 if run_clock else self.env.now)
            return
        if run_clock and self.cfg.failures and grp.em:
            m.next_fail -= step.run
        yield self.env.timeout(step.run)
        self._acc(g, "run", run_start, self.env.now)
        yield from self._with_operator(m, [(step.unload, "unload")])
        if self._in_window():
            self.jobs_window[g] += 1
        self.in_proc[g] -= len(job)  # unloaded lots wait in the stocker for the transporter
        if self.transporter is not None:  # without a transporter they leave at this same instant
            self._stocker_peak(g)
        for lot in job:
            lot.hist[g] = m.name
            lot.idx += 1
            nxt = self.fab.steps[lot.route[lot.idx]].group if lot.idx < len(lot.route) else None
            self.env.process(self._move(lot, g, nxt))

    # ------------------------------------------------------------------ run
    def run(self) -> dict[str, Any]:
        self.env.run(until=self.t_end)
        self._wip_change(0)
        weeks = self.cfg.run_weeks
        horizon = self.t_end - self.t_start
        cts = [c[2] for c in self.completions]
        n = len(cts)
        mean_ct = sum(cts) / n if n else math.nan
        raw_w = {}
        for _, p, _ct in self.completions:
            raw_w[p] = raw_w.get(p, 0) + 1
        raw_avg = sum(self.raw[p] * k for p, k in raw_w.items()) / n if n else math.nan
        groups = {}
        for g, grp in self.fab.groups.items():
            st = self.state[g]
            cal = grp.tools * horizon
            prod = st["load"] + st["run"] + st["unload"]
            waits = self.visit_wait[g]
            groups[g] = {
                "tools": grp.tools,
                "state_fraction": {k: v / cal for k, v in st.items()},
                "productive_fraction": prod / cal,
                "non_idle_fraction": sum(st.values()) / cal,
                "mean_wait_min": sum(waits) / len(waits) if waits else math.nan,
                "jobs_per_week": self.jobs_window[g] / weeks,
                "failures_per_week": self.failures_window[g] / weeks,
                "max_stocker_lots": self.max_stocker[g],
            }
        thirds = [a / (horizon / 3.0) for a in self.wip_thirds]
        unstable = thirds[2] > 1.5 * thirds[0] and thirds[2] - thirds[0] > 10
        return {
            "config": asdict(self.cfg),
            "totals": {"released": self.lot_counter, "completed": self.completed_total, "scrapped": self.scrapped,
                       "wip_end": self.wip},
            "seed": self.seed,
            "throughput_per_week": n / weeks,
            "starts_per_week": self.released_window / weeks,
            "scrapped_per_week": self.scrapped_window / weeks,
            "cycle_time_min": mean_ct,
            "raw_process_time_min": raw_avg,
            "x_factor": mean_ct / raw_avg if n else math.nan,
            "wip_mean": self.wip_area / horizon,
            "wip_by_third": thirds,
            "unstable": unstable,
            "litho_setups_per_week": self.setups_window / weeks,
            "litho_setup_min_per_week": self.setup_min_window / weeks,
            "groups": groups,
            "step_mean_wait_min": {s: (sum(v) / len(v) if v else math.nan) for s, v in self.visit_wait_by_step.items()},
        }


def simulate(fab: Fab, cfg: SimConfig, seed: int) -> dict[str, Any]:
    return FabSim(fab, cfg, seed).run()
