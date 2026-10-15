"""Deterministic result writing: floats rounded to 10 significant digits, LF line endings."""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any, Iterable

from .fab import ROOT

RESULTS_DIR = ROOT / "results"
SIG_DIGITS = 10


def r10(x: float) -> float | None:
    """Round to 10 significant digits; map non-finite values to None (JSON null)."""
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    if x == 0.0:
        return 0.0
    out = float(f"{x:.{SIG_DIGITS}g}")
    return 0.0 if out == 0.0 else out


def clean(obj: Any) -> Any:
    """Recursively round floats and convert tuples to lists for stable JSON."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return r10(obj)
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if hasattr(obj, "item"):  # numpy scalar
        return clean(obj.item())
    raise TypeError(f"cannot serialise {type(obj)!r}")


def dumps(obj: Any) -> str:
    return json.dumps(clean(obj), indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_json(name: str | Path, obj: Any, results_dir: Path | None = None) -> Path:
    path = Path(results_dir or RESULTS_DIR) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(obj).encode("ascii"))
    return path


def _fmt(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, float):
        rv = r10(v)
        return "" if rv is None else repr(rv)
    return v


def write_csv(name: str | Path, header: list[str], rows: Iterable[Iterable[Any]], results_dir: Path | None = None) -> Path:
    path = Path(results_dir or RESULTS_DIR) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for row in rows:
        w.writerow([_fmt(v) for v in row])
    path.write_bytes(buf.getvalue().encode("ascii"))
    return path


def read_json(name: str | Path, results_dir: Path | None = None) -> Any:
    return json.loads((Path(results_dir or RESULTS_DIR) / name).read_text(encoding="ascii"))
