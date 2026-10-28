"""Golden regression for the 2D synapse detector: re-run two samples into a scratch folder and
compare with the frozen references in tests/golden/.

    python scripts/golden_check.py                 # re-runs synapse_exf.py for the two samples, then compares
    python scripts/golden_check.py --compare DIR   # only compare an existing output folder

Match rule: a golden synapse is "found" when the new run has a synapse on the same plane within
3 px; the DP-PSD distance and both ExF readings must then agree within 0.5 %, and the class must be
the same. Prints a table and exits 1 on any failure, so it can gate a merge.
"""
import argparse
import csv
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE.parent / "tests" / "golden"
TOOLS = Path(r"C:\Users\admin\OneDrive\Desktop\ZarrBatchConverter")      # the live 2D toolbox (legacy/ZarrBatchConverter in the repo)
SAMPLES = ["260616_NR1_Homer1", "260831_WT4_GluA2ms_40x_3x3_FusionStitcher"]
TOL = 0.005
PX = 3.0


def load(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def compare(golden, new):
    key = lambda r: (int(float(r["Slice"])), float(r["X (px)"]), float(r["Y (px)"]))
    byplane = {}
    for r in new:
        z, x, y = key(r); byplane.setdefault(z, []).append((x, y, r))
    found = agree = 0; misses = []
    for g in golden:
        z, x, y = key(g)
        cands = [(abs(cx - x) + abs(cy - y), r) for cx, cy, r in byplane.get(z, []) if abs(cx - x) <= PX and abs(cy - y) <= PX]
        if not cands:
            misses.append(f"missing z{z} ({x:.0f},{y:.0f})"); continue
        found += 1
        r = min(cands)[1]
        ok = True
        for col in ("DP-PSD peak to peak (µm, as imaged)", "ExF (peak to peak)", "ExF (Gaussian fit)"):
            a, b = float(g[col]), float(r[col])
            if abs(a - b) > TOL * max(abs(a), 1e-9):
                ok = False; misses.append(f"z{z} ({x:.0f},{y:.0f}) {col}: {a} vs {b}")
        if g["Class"] != r["Class"]:
            ok = False; misses.append(f"z{z} ({x:.0f},{y:.0f}) class: {g['Class']} vs {r['Class']}")
        agree += ok
    return found, agree, misses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", help="an output folder with <sample>/<sample>_synapses.csv already in it")
    a = ap.parse_args()
    out = Path(a.compare) if a.compare else Path(tempfile.mkdtemp(prefix="golden_"))
    if not a.compare:
        for s in SAMPLES:
            print(f"re-running {s} into {out}")
            subprocess.run([sys.executable, "-u", "-W", "ignore", "synapse_exf.py", s, "--synaptic", "auto", "--no-links", "--out", str(out)],
                           cwd=TOOLS, check=True, stdin=subprocess.DEVNULL)
    failed = False
    print(f"\n{'Sample':<50} {'golden':>6} {'found':>6} {'agree':>6}")
    for s in SAMPLES:
        g = load(GOLDEN / f"{s}_synapses.csv"); n = load(out / s / f"{s}_synapses.csv")
        found, agree, misses = compare(g, n)
        print(f"{s:<50} {len(g):>6} {found:>6} {agree:>6}")
        if misses:
            failed = True
            for m in misses[:10]:
                print("   " + m)
            if len(misses) > 10:
                print(f"   ... {len(misses) - 10} more")
    print("\nGOLDEN " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
