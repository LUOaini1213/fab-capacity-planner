"""Byte-compare regenerated result files against the committed ones.

Compared: every .json/.csv under results/ except the LLM evaluations
(agent_eval_llm_*.json need a local Ollama server). Figures are not compared
(PNG bytes depend on the font stack).

Usage: python scripts/compare_results.py results /tmp/results [--only PREFIX]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("candidate")
    ap.add_argument("--only", default="")
    ap.add_argument("--exclude", default="", help="comma-separated file names not to compare (e.g. inputs copied in)")
    ap.add_argument("--report", default=None, help="write a JSON summary here")
    ap.add_argument("--label", default="", help="free-text platform label stored in the report")
    a = ap.parse_args(argv)
    ref, cand = Path(a.reference), Path(a.candidate)
    excluded = {x.strip() for x in a.exclude.split(",") if x.strip()}
    files = sorted(p for p in cand.glob("*") if p.suffix in (".json", ".csv") and p.name.startswith(a.only)
                   and not p.name.startswith("agent_eval_llm_") and p.name not in excluded)
    if not files:
        print("no files to compare")
        return 1
    bad = 0
    rows = []
    for p in files:
        r = ref / p.name
        if not r.exists():
            print(f"MISSING in reference: {p.name}")
            bad += 1
            rows.append({"file": p.name, "identical": False, "sha256": digest(p)})
            continue
        same = r.read_bytes() == p.read_bytes()
        print(f"{'OK  ' if same else 'DIFF'} {p.name} {digest(p)[:16]}")
        bad += not same
        rows.append({"file": p.name, "identical": same, "sha256": digest(p)})
    print(f"{len(files) - bad}/{len(files)} identical")
    if a.report:
        rep = {"label": a.label, "compared": len(files), "identical": len(files) - bad, "files": rows}
        Path(a.report).write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="ascii", newline="\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
