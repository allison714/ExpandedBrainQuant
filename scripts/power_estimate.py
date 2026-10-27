"""How many animals per genotype does the ACC study need?  (Plan item 1.2)

Reads the per-animal table written by compare_morphology.py (one row per animal, one column per
measure) and, for each measure, turns the between-animal spread of the WT animals into

  * the coefficient of variation (CV) across animals, and
  * the smallest fractional change a two-sample comparison could detect with power 0.8 at
    alpha 0.05 for n = 3 ... 12 animals per genotype, and
  * the n per genotype needed to detect a 10 %, 15 %, 20 % and 30 % change.

The animal is the unit of inference (manifest 4.6): each row is one animal's mean over all of
its synapses, so the spread here is what the genotype comparison actually has to beat.
Synapse counts do not enter the calculation, except that each animal's mean is already an
average over hundreds of synapses, which is why the between-animal CV is small.

Method: two-sided two-sample t-test, equal n and equal variance. Power is computed exactly from
the noncentral t distribution with noncentrality  delta * sqrt(n/2) / CV_pooled  and 2n-2 degrees
of freedom, where delta is the fractional change and CV_pooled is the between-animal CV pooled
over WT and KO (or WT alone with --wt-only). This is the standard formula a committee expects;
the limits are (1) it assumes the ACC animals vary as much as these method-development animals,
(2) it ignores sex as a factor (adding sex as a covariate costs about one degree of freedom and
can reduce residual variance), and (3) it is for one endpoint at a time, so with several
endpoints the alpha should be shared out (manifest 4.6).

Usage:
    python scripts/power_estimate.py "<path to Morphology - Per Animal.csv>" [--wt-only] [--alpha 0.05] [--power 0.8]
"""
import argparse
import csv
import math
from pathlib import Path

import numpy as np
from scipy import stats

SKIP = {"Set", "Animal", "Samples", "Synapses"}          # not measures


def power_two_sample(delta_frac, cv, n, alpha=0.05):
    """Power of a two-sided two-sample t-test to detect a fractional change delta_frac when the
    between-animal CV is cv and there are n animals per group."""
    df = 2 * n - 2
    if df < 1 or cv <= 0:
        return float("nan")
    nc = (delta_frac / cv) * math.sqrt(n / 2.0)
    tcrit = stats.t.ppf(1 - alpha / 2, df)
    return float(1 - stats.nct.cdf(tcrit, df, nc) + stats.nct.cdf(-tcrit, df, nc))


def detectable_change(cv, n, alpha=0.05, power=0.8):
    """Smallest fractional change detectable at the given power: solve power(delta) = power."""
    lo, hi = 0.0, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if power_two_sample(mid, cv, n, alpha) < power:
            lo = mid
        else:
            hi = mid
    return hi


def animals_needed(delta_frac, cv, alpha=0.05, power=0.8, n_max=200):
    for n in range(2, n_max + 1):
        if power_two_sample(delta_frac, cv, n, alpha) >= power:
            return n
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--wt-only", action="store_true", help="use only the WT animals for the spread")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--power", type=float, default=0.8)
    ap.add_argument("--out", help="CSV to write (default: beside the input)")
    a = ap.parse_args()

    with open(a.csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    measures = [k for k in rows[0] if k not in SKIP]
    groups = sorted({r["Set"] for r in rows})
    print(f"{len(rows)} animals: " + ", ".join(f"{g} n={sum(r['Set'] == g for r in rows)}" for g in groups))

    ns = list(range(3, 13))
    targets = (0.10, 0.15, 0.20, 0.30)
    out_rows = []
    hdr = ["Measure", "Groups used", "Mean", "Between-animal SD", "CV (%)"] + [f"Detectable change at n={n} (%)" for n in ns] + [f"n per genotype for {int(d*100)} %" for d in targets]
    for m in measures:
        vals = {g: np.array([float(r[m]) for r in rows if r["Set"] == g and r[m] not in ("", None)]) for g in groups}
        if a.wt_only:
            use = {"WT": vals.get("WT", np.array([]))}
        else:
            use = vals
        # pooled CV: pooled within-group SD over the grand mean of group means
        sds, dfs, means = [], [], []
        for g, v in use.items():
            if len(v) >= 2:
                sds.append(np.var(v, ddof=1) * (len(v) - 1)); dfs.append(len(v) - 1); means.append(v.mean())
        if not dfs:
            continue
        sd = math.sqrt(sum(sds) / sum(dfs)); mean = float(np.mean(means)); cv = sd / mean if mean else float("nan")
        det = [100 * detectable_change(cv, n, a.alpha, a.power) for n in ns]
        need = [animals_needed(d, cv, a.alpha, a.power) for d in targets]
        out_rows.append([m, "+".join(use), f"{mean:.3g}", f"{sd:.3g}", f"{100*cv:.1f}"] + [f"{x:.1f}" for x in det] + [str(x) if x else ">200" for x in need])

    # print a compact view
    print(f"\nPower {a.power}, alpha {a.alpha}, two-sided; spread from {'WT only' if a.wt_only else 'both genotypes pooled'}.\n")
    print(f"{'Measure':<42} {'CV %':>6}  {'detectable % at n=4':>20} {'n=6':>6} {'n=8':>6} {'n=12':>6}   n for 10% / 15% / 20% / 30%")
    for r in out_rows:
        cvp = r[4]; d = dict(zip(ns, r[5:5 + len(ns)]))
        print(f"{r[0][:42]:<42} {cvp:>6}  {d[4]:>20} {d[6]:>6} {d[8]:>6} {d[12]:>6}   {' / '.join(r[5 + len(ns):])}")

    out = Path(a.out) if a.out else Path(a.csv).with_name("Power Estimate.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(hdr); w.writerows(out_rows)
    print(f"\nwritten {out}")


if __name__ == "__main__":
    main()
