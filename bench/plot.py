# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    bench/plot.py
# Owner:   Yadu Pratap (deployment lead)
# Purpose: charts of throughput, p95 latency and commit to block lag from the
#          summary file
# Covers:  FR27 (see docs/D.4.1)
# ============================================================================
"""Figures from results/summary.csv (medians over repeats, interquartile range as error bars).

  python -m bench.plot                         SmallBank: fig_throughput.png, fig_p95.png, fig_lag.png
  python -m bench.plot --workload analytics    sample_analytics: the same figures with an _analytics suffix

Every configuration is run several times (five in the final experiments).
Each point is the median of those repeats, and the error bar spans the
interquartile range, which is robust to the occasional slow run on a laptop.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # render to files; no display needed (works in Codespaces and CI)
import matplotlib.pyplot as plt

SUMMARY = Path("results/summary.csv")
# One fixed colour per target so figures stay comparable across runs.
COLORS = {"mongo-plain": "#6B7A8F", "mongo-a": "#1C6E6E", "mongo-s": "#005596",
          "sql-plain": "#B8860B", "sql-ledger": "#B22222"}


def _load(workload: str) -> list[dict]:
    """Rows of the summary file for one workload (rows without the column are SmallBank)."""
    with open(SUMMARY, newline="") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("workload") or "smallbank") == workload]


def _iqr(vals):
    """Distances from the median down to Q1 and up to Q3 (for asymmetric error bars)."""
    if len(vals) < 2:
        return 0.0, 0.0
    q = statistics.quantiles(vals, n=4)
    m = statistics.median(vals)
    return m - q[0], q[2] - m


def _label(r):
    """Series name: the target, plus k for Design S and the interval for Design A."""
    t = r["target"]
    if t == "mongo-s":
        return f"{t} (k={r['heads']})"
    if t in ("mongo-a",):
        return f"{t} ({r['interval']}s)"
    return t


def by_clients(rows, metric, fname, ylabel):
    """One line per configuration: ``metric`` against the number of client threads."""
    series = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get(metric):
            series[_label(r)][int(r["clients"])].append(float(r[metric]))
    if not series:
        return None
    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=150)
    for label, pts in sorted(series.items()):
        xs = sorted(pts)
        ys = [statistics.median(pts[x]) for x in xs]
        err = list(zip(*[_iqr(pts[x]) for x in xs]))
        ax.errorbar(xs, ys, yerr=err, marker="o", capsize=3, label=label,
                    color=COLORS.get(label.split(" ")[0]))
    ax.set_xscale("log", base=2)
    ax.set_xlabel("client threads")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(Path("results") / fname)
    return fname


def lag(rows, fname="fig_lag.png"):
    """Design A: mean commit to block lag against the digest interval."""
    pts = defaultdict(list)
    for r in rows:
        if r["target"] == "mongo-a" and r.get("lag_mean_s"):
            pts[float(r["interval"])].append(float(r["lag_mean_s"]))
    if not pts:
        return None
    xs = sorted(pts)
    fig, ax = plt.subplots(figsize=(5.2, 3.6), dpi=150)
    ax.plot(xs, [statistics.median(pts[x]) for x in xs], marker="o", color=COLORS["mongo-a"])
    ax.set_xlabel("digest interval (s)")
    ax.set_ylabel("mean commit to block lag (s)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(Path("results") / fname)
    return fname


def main(argv=None) -> int:
    """Write the figures for one workload into results/."""
    p = argparse.ArgumentParser(prog="bench.plot")
    p.add_argument("--workload", choices=["smallbank", "analytics"], default="smallbank")
    a = p.parse_args(argv)
    rows = _load(a.workload)
    sfx = "" if a.workload == "smallbank" else "_analytics"
    made = [by_clients(rows, "throughput_ops_s", f"fig_throughput{sfx}.png", "throughput (transactions/s)"),
            by_clients(rows, "p95_ms", f"fig_p95{sfx}.png", "95th percentile latency (ms)"),
            lag(rows, f"fig_lag{sfx}.png")]
    print("wrote", ", ".join(m for m in made if m) or "nothing (no rows for this workload)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
