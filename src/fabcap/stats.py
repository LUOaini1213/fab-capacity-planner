"""Replication statistics: mean and two-sided Student-t confidence interval."""
from __future__ import annotations

import math
from typing import Iterable

from scipy import stats as _st


def mean_ci(values: Iterable[float], level: float = 0.95) -> dict[str, float]:
    xs = [float(v) for v in values]
    n = len(xs)
    if n == 0:
        raise ValueError("no values")
    m = sum(xs) / n
    if n == 1:
        return {"mean": m, "half_width": math.inf, "n": 1, "sd": math.nan}
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    t = float(_st.t.ppf(0.5 + level / 2.0, n - 1))
    return {"mean": m, "half_width": t * sd / math.sqrt(n), "n": n, "sd": sd}
