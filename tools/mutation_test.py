"""Scripted mutation testing: apply one source mutation at a time, run the test
suite, record whether any test fails (mutant "killed").

Usage: python tools/mutation_test.py [--only ID] [--out results/mutation.json]
Each mutant must match its source text exactly once; files are restored after
every run (also on error / Ctrl-C).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "fabcap"

# (id, file relative to src/fabcap, original, replacement, what it breaks)
MUTANTS = [
    ("M01", "fab.py", "return self.load + self.run + self.unload", "return self.load + self.run", "occupancy drops unload"),
    ("M02", "capacity.py", "process += p.starts_per_week * st.occupancy / g.batch_size", "process += p.starts_per_week * st.occupancy", "batching ignored in load"),
    ("M03", "capacity.py", 'return float(g.setup["step_change"])', 'return float(g.setup["product_change"])', "wrong setup for step change"),
    ("M04", "capacity.py", 'return float(g.setup["both_change"])', 'return float(g.setup["step_change"])', "wrong setup for both change"),
    ("M05", "capacity.py", "pm = g.pm_minutes * week / fab.period_minutes(g.pm_period)", 'pm = g.pm_minutes * week / fab.calendar["shift"]', "PM period ignored"),
    ("M06", "capacity.py", "em = week * mean_repair / (mean_ttf + mean_repair)", "em = week * mean_repair / mean_ttf", "renewal EM fraction wrong"),
    ("M07", "capacity.py", 'em = g.em["spec_events_per_week"] * mean_repair', 'em = g.em["spec_events_per_week"] * g.em["repair_max"]', "spec EM uses max repair"),
    ("M08", "capacity.py", "max_starts = (total * util_cap / u) if u > 0", "max_starts = (total / u) if u > 0", "util cap ignored"),
    ("M09", "capacity.py", 'bottleneck = max(rows, key=lambda k: (rows[k]["utilization"], k))', 'bottleneck = min(rows, key=lambda k: (rows[k]["utilization"], k))', "bottleneck = least loaded"),
    ("M10", "capacity.py", 'u = ld["load_min_per_week"] / available if available > 0', 'u = ld["process_min_per_week"] / available if available > 0', "setups dropped from utilisation"),
    ("M11", "queueing.py", "u ** (math.sqrt(2.0 * (m + 1)) - 1.0)", "u ** (math.sqrt(2.0 * (m + 1)))", "Sakasegawa exponent off by one"),
    ("M12", "queueing.py", "/ (m * (1.0 - u))) * te", "/ (1.0 - u)) * te", "G/G/m drops 1/m"),
    ("M13", "queueing.py", "max(ns - 1.0, 0.0) / ns**2 * ts**2", "max(ns - 1.0, 0.0) / ns * ts**2", "outage variance term wrong"),
    ("M14", "queueing.py", "batch_wait[(p, s)] = (k - 1) / (2.0 * lam_f)", "batch_wait[(p, s)] = k / (2.0 * lam_f)", "batch-formation wait wrong"),
    ("M15", "queueing.py", "ca2_job = ca2 / k", "ca2_job = ca2", "batch arrival SCV not divided"),
    ("M16", "planning.py", "return fab.groups[g].tools + pulp.lpSum(x[g, qq] for qq in range(T) if qq + L <= q)",
     "return fab.groups[g].tools + pulp.lpSum(x[g, qq] for qq in range(T) if qq + L < q)", "lead time off by one in MILP"),
    ("M17", "planning.py", "disc = {q: 1.0 / (1.0 + d) ** q for q in range(T)}", "disc = {q: 1.0 for q in range(T)}", "no discounting"),
    ("M18", "planning.py", "<= cap * avail[g] * tools_expr(g, q)", "<= avail[g] * tools_expr(g, q)", "utilisation cap dropped from MILP"),
    ("M19", "validate.py", "if u > cap + 1e-7:", "if u > cap + 1.0:", "validator ignores cap"),
    ("M20", "validate.py", "need.append(math.ceil(independent_loads", "need.append(math.floor(independent_loads", "greedy rounds tools down"),
    ("M21", "des.py", "elif lot.product != prod:", "elif False:", "S5 batches may mix products"),
    ("M22", "des.py", "if n_test >= self.max_test:", "if n_test > self.max_test:", "two test lots per batch"),
    ("M23", "des.py", "return lot.hist.get(m.group) != m.name", "return True", "test lot may reuse machine"),
    ("M24", "des.py", "elif m.next_fail < run_start + step.run:", "elif False:", "failures only at run start"),
    ("M25", "des.py", "                self.in_cell[g] -= 1\n                self._wip_change(-1)\n", "                self.in_cell[g] -= 1\n", "scrapped lots stay in WIP"),
    ("M26", "des.py", "return sorted(q, key=lambda l: (setup_between(g, m.last_type, (l.product, l.step)) if m.last_type else 0.0,", "return sorted(q, key=lambda l: (0.0,", "SA rule degenerates to FIFO"),
    ("M27", "des.py", "m.pm_due = max(window_start + period, self.env.now + period / 2.0)", "m.pm_due = max(window_start + period, self.env.now)", "PM gap rule dropped"),
    ("M28", "des.py", "return sorted(q, key=lambda l: ((l.due - now) / max(l.raw_left[l.idx], 1.0), l.arrive, l.seq))", "return sorted(q, key=lambda l: ((now - l.due) / max(l.raw_left[l.idx], 1.0), l.arrive, l.seq))", "CR sorted backwards"),
    ("M29", "des.py", "        if grp.setup:\n            m.last_type = jtype\n", "", "litho setup state never updated"),
    ("M30", "agent/validator.py", "bad = [x for x in nums if abs(x) not in allowed]", "bad = [x for x in nums if not any(abs(abs(x) - a) <= 1 for a in allowed)]", "validator tolerates +-1"),
    ("M45", "agent/llm.py", '    if "OUT_OF_SCOPE" in content:  # also after', '    if "OUT_OF_SCOPE" in content and not calls:  # also after', "refusal after a tool call not recognised"),
    ("M42", "agent/validator.py", "return allowed | (echoed & asked)", "return allowed | echoed", "tool-argument laundering allowed"),
    ("M43", "agent/validator.py", r'_NUM = re.compile(r"(?<![\d.])[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)")',
     r'_NUM = re.compile(r"(?<![A-Za-z\d.])[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")', "numbers glued to letters / leading dot escape"),
    ("M44", "des.py", "        self.in_proc[g] -= len(job)  # unloaded lots wait", "        self.in_proc[g] -= 0  # unloaded lots wait", "stocker metric counts lots after unload as on-machine"),
    ("M31", "agent/router.py", "return -v if NEG.search(text) else v", "return v", "demand decreases read as increases"),
    ("M32", "agent/router.py", 'if re.search(r"next quarter", text, re.I):\n        return 2', 'if re.search(r"next quarter", text, re.I):\n        return 1', "next quarter -> Q1"),
    ("M33", "agent/tools.py", "return round(100.0 * x, 1)", "return round(100.0 * x, 0)", "percent rounding coarsened"),
    ("M34", "agent/evaluate.py", "w.startswith(tok[:4])", "w.startswith(tok[:1])", "keyword scoring too lenient"),
    ("M35", "stats.py", "t = float(_st.t.ppf(0.5 + level / 2.0, n - 1))", "t = float(_st.t.ppf(level, n - 1))", "one-sided t quantile"),
    ("M36", "io.py", "SIG_DIGITS = 10", "SIG_DIGITS = 8", "result rounding changed"),
    ("M37", "agent/router.py", "    if foreign_entity(text):\n        return True\n", "", "named-entity scope rule removed"),
    ("M46", "agent/router.py", "if PERSONAL.search(text) or WRITING.search(text):", "if WRITING.search(text):", "personal-belongings scope rule removed"),
    ("M47", "agent/router.py", "if m.group(1).lower() not in FAB_UNITS:", "if False:", "foreign-unit scope rule removed"),
    ("M48", "agent/router.py", "if acronym or (caps and i > 0):", "if acronym:", "capitalised names not treated as entities"),
    ("M49", "agent/validator.py", 'return [o for o in tool_outputs if not (isinstance(o, dict) and "error" in o)]', "return list(tool_outputs)", "numbers from error payloads allowed"),
    ("M50", "agent/validator.py", "            echoed |= derived\n", "            allowed |= derived\n", "argument-derived outputs allowed without the question"),
    ("M51", "agent/validator.py", '"ok": not bad and not bad_q and not malformed,', '"ok": not bad and not bad_q,', "dotted digit runs accepted"),
    ("M52", "agent/validator.py", r"\b(?:G/G/[1m]|M/M/1)\b", r"\b[GM]/[GM]/(?:\d+|m)\b", "queue-name identifier swallows any number"),
    ("M53", "agent/validator.py", "bad_q = [q for q in extract_quarters(answer) if q not in quarters]", "bad_q = []", "quarter names not checked"),
    ("M54", "agent/tools.py", 'derived = (["starts_per_week"] if d != 0 else []) + ([f"tools.{g}"] if g and n else [])', "derived = []", "capacity tool does not mark argument-derived outputs"),
    ("M69", "agent/render.py", 'return field in out.get("arg_derived", [])', "return False", "templates print argument-derived numbers"),
    ("M39", "agent/llm.py", 'if v["ok"] and content:', "if content:", "LLM answer not validated"),
    ("M40", "agent/llm.py", 'if v2["ok"] and content2 and "OUT_OF_SCOPE" not in content2:', 'if content2 and "OUT_OF_SCOPE" not in content2:', "repair turn not validated"),
    ("M41", "agent/llm.py", "    if not calls:\n        # no tool output", "    if False:\n        # no tool output", "number-only answer without tools not refused"),
    ("M55", "des.py", "            m.next_fail -= step.run\n", "            pass\n", "run clock never ages"),
    ("M56", "des.py", "if m.next_fail < step.run:  # run minutes left", "if m.next_fail < 0.0:  # run minutes left", "run clock never fires mid-run"),
    ("M57", "des.py", 'return _r(m.rng.uniform(grp.em["tbf_min"] * s, grp.em["tbf_max"] * s))', 'return _r(m.rng.uniform(grp.em["tbf_min"], grp.em["tbf_max"]))', "tbf_scale ignored"),
    ("M58", "des.py", "        yield from self._with_operator(m, segments)\n", "        for seg in segments:\n            yield from self._with_operator(m, [seg])\n", "operator released between setup and load"),
    ("M59", "des.py", "            if people is self.techs:  # grants", "            if False:  # grants", "second technician not woken for PM"),
    ("M60", "des.py", "m.next_fail = self._draw_tbf(m, grp) + (0.0 if run_clock else self.env.now)", "m.next_fail = self._draw_tbf(m, grp) + 0.0", "calendar clock restarts at time zero after a repair"),
    ("M61", "capacity.py", "rate *= run_minutes_per_tool(fab, group) / run_minutes_per_tool(ref, group)", "rate *= 1.0", "run-clock EM ignores run time"),
    ("M62", "capacity.py", 'return (fab.week / rate - (em["repair_min"] + em["repair_max"]) / 2.0) / mean_tbf', "return (fab.week / rate) / mean_tbf", "calendar TBF scale ignores repair time"),
    ("M63", "planning.py", 'pulp.lpSum(pc["maint_min_per_tool"][g] * tools_expr(g, q) for g in groups)', 'pulp.lpSum(pc["maint_min_per_tool"][g] * fab.groups[g].tools for g in groups)', "technician load ignores purchased tools"),
    ("M64", "planning.py", 'prob += (ol - (ol / D) * s[q] <= cap_p * pc["operator_min_per_person"]', 'prob += (ol - (ol / D) * s[q] <= pc["operator_min_per_person"]', "people cap dropped for operators"),
    ("M65", "planning.py", "+ (labour_cost if labour else 0))", "+ 0)", "labour cost left out of the objective"),
    ("M66", "validate.py", "if u > cap_p + 1e-7:", "if u > cap_p + 1.0:", "validator ignores the people cap"),
    ("M67", "validate.py", "labour_pv += sum(wage[r] * (staff[q] - base[r]) / (1 + d) ** q for q in range(T))", "labour_pv += 0.0", "greedy ignores labour cost"),
    ("M71", "capacity.py", 'maint = g.tools * (av["pm_min_per_tool"] + em_minutes_per_tool(fab, name, em_mode, clock, ref))', 'maint = g.tools * (av["pm_min_per_tool"] + av["em_min_per_tool"])', "personnel load ignores the failure clock"),
    # equivalent mutants (documented, excluded from the score): they cannot change any observable output
    ("E01", "des.py", "if m.next_fail <= run_start:", "if m.next_fail < run_start:",
     "EQUIVALENT: when next_fail == run_start the elif branch sets fail_at = next_fail = run_start, same behaviour"),
    ("E02", "io.py", "    if x == 0.0:\n        return 0.0\n", "",
     "EQUIVALENT: f'{0.0:.10g}' is '0' and the final line maps -0.0 to 0.0 anyway"),
    ("M38", "des.py", "self._acc(g, \"run\", run_start, self.env.now)", "self._acc(g, \"load\", run_start, self.env.now)", "run time booked as load (accounting)"),
]


def run_tests(timeout: int) -> tuple[bool, float, str]:
    t = time.time()
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=ROOT,
                           capture_output=True, text=True, timeout=timeout)
        failed = p.returncode != 0
        tail = (p.stdout or "").strip().splitlines()[-1:] if p.stdout else []
        return failed, time.time() - t, " ".join(tail)
    except subprocess.TimeoutExpired:
        return True, time.time() - t, "timeout"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default=str(ROOT / "results" / "mutation.json"))
    a = ap.parse_args(argv)
    ok, _, tail = run_tests(a.timeout)
    if ok:  # "failed" is True -> baseline broken
        print("baseline test suite fails; fix it first:", tail)
        return 2
    rows = []
    for mid, rel, orig, repl, what in MUTANTS:
        if a.only and mid != a.only:
            continue
        path = SRC / rel
        src = path.read_bytes()
        text = src.decode("utf-8")
        n = text.count(orig)
        if n != 1:
            rows.append({"id": mid, "file": rel, "what": what, "status": f"NOT APPLIED (matches={n})"})
            print(mid, "NOT APPLIED", n)
            continue
        try:
            path.write_bytes(text.replace(orig, repl).encode("utf-8"))
            killed, secs, tail = run_tests(a.timeout)
        finally:
            path.write_bytes(src)
        status = "killed" if killed else "SURVIVED"
        if mid.startswith("E"):
            status = "equivalent (survived, as expected)" if not killed else "killed: NOT equivalent, reclassify"
        rows.append({"id": mid, "file": rel, "what": what, "status": status, "seconds": round(secs, 1), "pytest": tail})
        print(mid, status, f"{secs:.1f}s", what, flush=True)
    applied = [r for r in rows if r["status"] in ("killed", "SURVIVED")]
    killed = sum(r["status"] == "killed" for r in applied)
    out = {"killed": killed, "total": len(applied), "equivalent": [r for r in rows if r["id"].startswith("E")],
           "not_applied": [r["id"] for r in rows if r["status"].startswith("NOT APPLIED")], "mutants": rows}
    if not a.only:
        Path(a.out).write_text(json.dumps(out, indent=2) + "\n", encoding="ascii", newline="\n")
    print(f"killed {killed}/{len(applied)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
