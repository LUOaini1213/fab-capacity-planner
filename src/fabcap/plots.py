"""Figures for the README (matplotlib, Agg backend). Reads results/*.json only."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .io import RESULTS_DIR, read_json  # noqa: E402

# reference categorical palette (slots 1-3 validate all-pairs for CVD), text inks, surface
SERIES = {"DIFF": "#2a78d6", "IMP": "#eb6834", "LITHO": "#1baf7a"}
SLOT = ["#2a78d6", "#eb6834", "#1baf7a"]
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10, "axes.titlesize": 12,
    "axes.titleweight": "bold", "axes.titlecolor": INK, "legend.frameon": False, "svg.hashsalt": "fabcap",
})


def _save(fig, name: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    fig.savefig(p, dpi=130, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return p


def fig_utilization(out: Path) -> Path:
    cap = read_json("capacity.json")
    groups = ["DIFF", "IMP", "LITHO"]
    spec = cap["variants"]["spec_nosetups"]["groups"]
    main = cap["variants"]["renewal_setups"]["groups"]
    fig, ax = plt.subplots(figsize=(7, 3.8))
    w = 0.36
    for i, (label, data, color) in enumerate([
        ("Kempf's table (no setups, 2 EM/week)", spec, SLOT[0]),
        ("This model (setups + EM from distributions)", main, SLOT[1]),
    ]):
        xs = [j + (i - 0.5) * w for j in range(len(groups))]
        vals = [data[g]["utilization"] * 100 for g in groups]
        ax.bar(xs, vals, width=w - 0.04, color=color, label=label)
        for x, v in zip(xs, vals):
            ax.text(x, v + 1.2, f"{v:.1f}%", ha="center", va="bottom", color=INK, fontsize=9)
    ax.axhline(100, color=INK2, lw=1, ls="--")
    ax.text(-0.45, 101, "100% = saturated", color=INK2, fontsize=8, ha="left", va="bottom")
    ax.set_xticks(range(len(groups)), groups)
    ax.set_ylim(0, 118)
    ax.set_ylabel("load / available time (%)")
    ax.set_title("Static utilisation at 84 starts/week")
    ax.legend(loc="upper left", fontsize=8.5)
    return _save(fig, "utilization.png", out)


def fig_xfactor(out: Path) -> Path:
    q = read_json("queueing.json")
    d = read_json("des_xfactor.json")
    fig, ax = plt.subplots(figsize=(7, 4))
    c = q["curve_ca2_1"]
    ax.plot([p["starts_per_week"] for p in c], [p["x_factor"] for p in c], color=SLOT[0], lw=2,
            label="Queueing model (G/G/m, ca2 = 1, machines only)")
    for variant, color, marker, lab in [("machines_only", SLOT[1], "o", "DES, machines only"),
                                        ("full", SLOT[2], "s", "DES, full model (operators, technician, transport)")]:
        pts = [p for p in d["points"] if p["model_variant"] == variant]
        xs = [p["starts_per_week"] for p in pts]
        ys = [p["des_x_factor"]["mean"] for p in pts]
        es = [p["des_x_factor"]["half_width"] for p in pts]
        ax.errorbar(xs, ys, yerr=es, fmt=marker, color=color, ms=6, capsize=3, lw=1.5, label=lab + " (95% CI)")
    ax.set_xlabel("starts per week (Kempf mix)")
    ax.set_ylabel("X-factor = cycle time / raw process time")
    ax.set_title("X-factor vs load")
    ax.set_ylim(bottom=0)
    ax.legend(fontsize=8.5, loc="upper left")
    return _save(fig, "xfactor.png", out)


def fig_capex_gantt(out: Path) -> Path:
    scen = ["base", "demand_plus_20", "mix_shift_pc"]
    plans = {s: read_json(f"plan_{s}.json") for s in scen}
    quarters = plans["base"]["quarters"]
    T = len(quarters)
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    rows = []
    for s in scen:
        for g in ["DIFF", "IMP", "LITHO"]:
            rows.append((s, g))
    labels = []
    for i, (s, g) in enumerate(rows):
        y = len(rows) - 1 - i
        p = plans[s]
        base = p["installed"][g][0] - sum(p["buy"][g][q] for q in range(T) if q + p["lead_time_quarters"] <= 0)
        cost = sum(pur["cost_musd"] for pur in p["purchases"] if pur["group"] == g)
        labels.append(f"{s} / {g}" + (f"  (${cost:g}M)" if cost else ""))
        for q in range(T):
            extra = p["installed"][g][q] - base
            if extra <= 0:
                continue
            ax.barh(y, 0.96, left=q + 0.02, height=0.6, color=SERIES[g])
            ax.text(q + 0.5, y, f"+{extra}", va="center", ha="center", color="#ffffff", fontsize=8.5, fontweight="bold")
        for pur in p["purchases"]:
            if pur["group"] == g:
                qo = quarters.index(pur["quarter_ordered"])
                ax.plot(qo + 0.5, y + 0.42, marker="v", color=INK2, ms=5)
    ax.plot([], [], marker="v", color=INK2, ls="none", label="order placed (1-quarter lead time)")
    ax.legend(loc="upper center", fontsize=8, bbox_to_anchor=(0.5, -0.08))
    ax.set_yticks(range(len(rows)), list(reversed(labels)), fontsize=8)
    ax.set_xticks([q + 0.5 for q in range(T)], quarters)
    ax.set_xticks(range(T + 1), minor=True)
    ax.grid(axis="x", which="minor", color=GRID)
    ax.grid(axis="x", which="major", visible=False)
    ax.tick_params(axis="x", which="minor", length=0)
    ax.set_xlim(0, T)
    ax.grid(axis="y", visible=False)
    for k in (3, 6):
        ax.axhline(k - 0.5, color=MUTED, lw=0.8)
    ax.set_title("Capex plan: extra tools in service per quarter (illustrative data)")
    return _save(fig, "capex_gantt.png", out)


def fig_des_vs_model(out: Path) -> Path:
    lay = read_json("des_layers.json")
    st = lay["static_max_starts_per_week"]
    L = lay["layers"]
    items = [
        ("Static: Kempf's assumptions (no setups, 2 EM/wk)", st["kempf_no_setups_spec_em"], 0.0, SLOT[0]),
        ("Static: + setups, EM from distributions", st["renewal_em_with_setups"], 0.0, SLOT[0]),
    ]
    names = [
        ("0_no_setups_no_failures", "DES: machines only, no setups/failures"),
        ("1_machines_only", "DES: machines only"),
        ("2_plus_transport", "DES: + transporter"),
        ("3_plus_technician", "DES: + technician"),
        ("4_plus_operators_full", "DES: + operators (full model)"),
    ]
    L2 = lay.get("layers_2_per_week", {})
    for k, lab in names:
        starts = L[k]["starts_per_week"]
        items.append((lab + (" [2.95 EM/wk]" if k in L2 else ""), starts["mean"], starts["half_width"], SLOT[1]))
        if k in L2:
            s2 = L2[k]["starts_per_week"]
            items.append((lab + " [2 EM/wk]", s2["mean"], s2["half_width"], SLOT[2]))
    fig, ax = plt.subplots(figsize=(7.5, 5.2))
    for i, (lab, v, hw, color) in enumerate(items):
        y = len(items) - 1 - i
        ax.barh(y, v, height=0.6, color=color, xerr=hw if hw else None, ecolor=INK2, capsize=3)
        ax.text(v + (hw or 0) + 1, y, f"{v:.1f}", va="center", color=INK, fontsize=9)
    ax.set_yticks(range(len(items)), [it[0] for it in reversed(items)], fontsize=8.5)
    ax.axvline(84, color=INK2, ls="--", lw=1)
    ax.text(84.5, len(items) - 0.6, "Kempf target 84", color=INK2, fontsize=8)
    ax.set_xlabel("capacity: lot starts per week the line sustains (DES: CONWIP, 95% CI)")
    ax.set_xlim(0, 105)
    ax.grid(axis="y", visible=False)
    ax.set_title("Where the static model is wrong")
    return _save(fig, "des_vs_model.png", out)


def make_all(out: Path | None = None) -> list[Path]:
    out = out or (RESULTS_DIR / "figures")
    return [fig_utilization(out), fig_xfactor(out), fig_capex_gantt(out), fig_des_vs_model(out)]
