"""
Compare nine ways of reading the DP-PSD distance off a synapse's profile, and draw them
on one picture from best to worst.

  synapse_fit_options.py [text]     use the antibody-positive synapses of the samples
                                    whose names contain the text (default GluA2)

There is no ground truth to score a method against, so the methods are ranked by
repeatability. Every synapse's bar is cut in two along its length, the two halves are
profiled separately, and each method measures both. The halves share the synapse but not
the noise, so the less a method's two answers differ, the less it is thrown by noise.
Repeatability is the standard deviation of (half A - half B) / sqrt(2), as a percentage
of the distance. A method that cannot produce an answer on a profile counts as a failure.

The synapses are found exactly as in synapse_exf.py (two Gaussians on a flat baseline),
and every method starts from the same two peak positions.
"""

import csv
import math
import sys
from pathlib import Path

import numpy as np
from scipy import optimize

import ab_efficiency as eff
import convert_to_zarr as core
import roi_log as roi
import synapse_exf as syn


def gauss(t, a, m, s):
    return a * np.exp(-(t - m) ** 2 / (2 * s * s))


def lorentz(t, a, m, g):
    return a / (1 + ((t - m) / g) ** 2)


def fitted(model, start, low, high):
    """A method that fits `model` in the window around both peaks. Returns (m1, m2, curve)."""
    def method(t, p, m1, m2):
        gap = abs(m2 - m1)
        win = (t >= min(m1, m2) - 0.9 * gap) & (t <= max(m1, m2) + 0.9 * gap)
        base, a1, a2 = p[win].min(), np.interp(m1, t, p) - p[win].min(), np.interp(m2, t, p) - p[win].min()
        ctx = dict(base=base, a1=max(a1, 1e-3), a2=max(a2, 1e-3), m1=m1, m2=m2, gap=gap, top=p.max(), span=p.max() - p.min() + 1)
        fit, _ = optimize.curve_fit(model, t[win], p[win], p0=start(ctx), bounds=(low(ctx), high(ctx)), maxfev=4000)
        return fit[model.centres[0]], fit[model.centres[1]], (t[win], model(t[win], *fit))
    return method


def model_flat(t, b, a1, m1, s1, a2, m2, s2):
    return b + gauss(t, a1, m1, s1) + gauss(t, a2, m2, s2)


def model_slope(t, b, k, a1, m1, s1, a2, m2, s2):
    return b + k * t + gauss(t, a1, m1, s1) + gauss(t, a2, m2, s2)


def model_shared(t, b, a1, m1, a2, m2, s):
    return b + gauss(t, a1, m1, s) + gauss(t, a2, m2, s)


def model_lorentz(t, b, a1, m1, g1, a2, m2, g2):
    return b + lorentz(t, a1, m1, g1) + lorentz(t, a2, m2, g2)


def model_three(t, b, a1, m1, s1, a2, m2, s2, a3, m3, s3):
    return b + gauss(t, a1, m1, s1) + gauss(t, a2, m2, s2) + gauss(t, a3, m3, s3)


model_flat.centres, model_slope.centres, model_shared.centres = (2, 5), (3, 6), (2, 4)
model_lorentz.centres, model_three.centres = (2, 5), (2, 5)


def separate(t, p, m1, m2):
    """One Gaussian + constant fitted to each peak on its own, a third of the spacing either side."""
    gap, out, ts, ys = abs(m2 - m1), [], [], []
    for m in (m1, m2):
        win = np.abs(t - m) <= gap / 3
        base = p[win].min()
        fit, _ = optimize.curve_fit(lambda x, b, a, c, s: b + gauss(x, a, c, s), t[win], p[win],
                                    p0=[base, p[win].max() - base + 1e-3, m, gap / 4],
                                    bounds=([0, 0, m - gap / 3, 0.5], [p.max(), 2 * (p.max() - p.min()) + 1, m + gap / 3, gap]))
        out.append(fit[2])
        ts.append(t[win])
        ys.append(fit[0] + gauss(t[win], *fit[1:]))
    return out[0], out[1], (np.concatenate([ts[0], [np.nan], ts[1]]), np.concatenate([ys[0], [np.nan], ys[1]]))


def maxima(t, p, m1, m2):
    """The brightest sample near each peak: what reading peaks off FIJI's Plot Profile gives."""
    gap = abs(m2 - m1)
    out = [t[np.abs(t - m) <= gap / 3][np.argmax(p[np.abs(t - m) <= gap / 3])] for m in (m1, m2)]
    return out[0], out[1], None


def parabola(t, p, m1, m2):
    """The brightest sample, refined with a parabola through it and its two neighbours."""
    gap, out = abs(m2 - m1), []
    for m in (m1, m2):
        near = np.flatnonzero(np.abs(t - m) <= gap / 3)
        i = near[np.argmax(p[near])]
        if i == 0 or i == len(t) - 1:
            raise ValueError("peak at the end of the profile")
        y0, y1, y2 = p[i - 1], p[i], p[i + 1]
        bend = y0 - 2 * y1 + y2
        out.append(t[i] + (0.5 * (y0 - y2) / bend if bend < 0 else 0) * (t[1] - t[0]))
    return out[0], out[1], None


def centroid(t, p, m1, m2):
    """Intensity-weighted centre of each peak: the samples above half its height over the trough."""
    lo, hi = min(m1, m2), max(m1, m2)
    trough = p[(t >= lo) & (t <= hi)].min()
    gap, out = hi - lo, []
    for m in (m1, m2):
        near = np.abs(t - m) <= gap / 2
        level = trough + 0.5 * (p[near].max() - trough)
        use = near & (p >= level)
        out.append(float((t[use] * (p[use] - level)).sum() / max((p[use] - level).sum(), 1e-9)))
    return out[0], out[1], None


METHODS = [
    ("Two Gaussians, flat baseline (used now)", fitted(
        model_flat, lambda c: [c["base"], c["a1"], c["m1"], c["gap"] / 4, c["a2"], c["m2"], c["gap"] / 4],
        lambda c: [0, 0, c["m1"] - 2, 0.8, 0, c["m2"] - 2, 0.8],
        lambda c: [c["top"], 2 * c["span"], c["m1"] + 2, c["gap"], 2 * c["span"], c["m2"] + 2, c["gap"]])),
    ("Two Gaussians, sloped baseline", fitted(
        model_slope, lambda c: [c["base"], 0, c["a1"], c["m1"], c["gap"] / 4, c["a2"], c["m2"], c["gap"] / 4],
        lambda c: [-np.inf, -np.inf, 0, c["m1"] - 2, 0.8, 0, c["m2"] - 2, 0.8],
        lambda c: [np.inf, np.inf, 2 * c["span"], c["m1"] + 2, c["gap"], 2 * c["span"], c["m2"] + 2, c["gap"]])),
    ("Two Gaussians, one shared width", fitted(
        model_shared, lambda c: [c["base"], c["a1"], c["m1"], c["a2"], c["m2"], c["gap"] / 4],
        lambda c: [0, 0, c["m1"] - 2, 0, c["m2"] - 2, 0.8],
        lambda c: [c["top"], 2 * c["span"], c["m1"] + 2, 2 * c["span"], c["m2"] + 2, c["gap"]])),
    ("Two Lorentzians, flat baseline", fitted(
        model_lorentz, lambda c: [c["base"], c["a1"], c["m1"], c["gap"] / 4, c["a2"], c["m2"], c["gap"] / 4],
        lambda c: [0, 0, c["m1"] - 2, 0.5, 0, c["m2"] - 2, 0.5],
        lambda c: [c["top"], 2 * c["span"], c["m1"] + 2, c["gap"], 2 * c["span"], c["m2"] + 2, c["gap"]])),
    ("Three Gaussians (two peaks + broad haze)", fitted(
        model_three, lambda c: [c["base"], c["a1"], c["m1"], c["gap"] / 4, c["a2"], c["m2"], c["gap"] / 4,
                                c["span"] / 10, (c["m1"] + c["m2"]) / 2, 2 * c["gap"]],
        lambda c: [0, 0, c["m1"] - 2, 0.8, 0, c["m2"] - 2, 0.8, 0, min(c["m1"], c["m2"]) - c["gap"], c["gap"]],
        lambda c: [c["top"], 2 * c["span"], c["m1"] + 2, c["gap"], 2 * c["span"], c["m2"] + 2, c["gap"],
                   c["span"], max(c["m1"], c["m2"]) + c["gap"], 6 * c["gap"]])),
    ("One Gaussian per peak, fitted separately", separate),
    ("Brightest sample of each peak (by eye in FIJI)", maxima),
    ("Brightest sample + parabola (sub-pixel)", parabola),
    ("Centroid of each peak above half height", centroid),
]


def run(text):
    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    base = Path(core.DEFAULT_SHOTS).parent / "Zarr Synapse ExF"
    synapses = []
    for name, zarr_dir in roi.samples(out_root):
        if text.lower() in name.lower() and not syn.ignored(name):
            print(name)
            hits, _, _, _, _, um, _ = syn.analyse(name, zarr_dir, out_root, True, 12)
            synapses += [dict(h, um=um, sample=name) for h in hits if h["ab_pos"]]
    if len(synapses) < 5:
        sys.exit("Too few antibody-positive synapses to compare methods on.")
    print(f"\n{len(synapses)} antibody-positive synapses\n")

    results = []
    for label, method in METHODS:
        whole, halves, failed = [], [], 0
        for h in synapses:
            t, lines, to_exf = h["t"], h["lines"], h["um"] * 1000 / syn.REF_NM
            m1, m2 = h["fit"][2], h["fit"][5]
            mid = lines.shape[1] // 2
            try:
                d = [abs(np.subtract(*method(t, p, m1, m2)[:2])) for p in
                     (lines.mean(axis=1), lines[:, :mid].mean(axis=1), lines[:, mid:].mean(axis=1))]
            except (RuntimeError, ValueError):
                failed += 1
                continue
            lo, hi = (syn.REF_NM * e / (h["um"] * 1000) for e in syn.EXF_SEARCH)
            if not all(np.isfinite(d)) or not lo <= d[0] <= hi:
                failed += 1
                continue
            whole.append(d[0] * to_exf)
            halves.append((d[1] - d[2]) / d[0])
        halves = np.asarray(halves)
        # Robust spread: half the 16th-84th percentile range, so one wild synapse does not decide
        # a method's rank. (Not the MAD: the brightest-sample method answers in half-pixel steps,
        # many of its differences are exactly zero, and a median would flatter it.)
        spread = float(np.subtract(*np.percentile(halves, [84, 16]))) / 2 / math.sqrt(2) * 100
        results.append({"label": label, "method": method, "exf": np.asarray(whole), "repeat": float(spread),
                        "failed": failed, "n": len(synapses)})
    # best first: fewest failures counted in, then tightest repeatability
    results.sort(key=lambda r: r["repeat"] / max(1 - r["failed"] / r["n"], 1e-6))

    # the example drawn in every panel: the synapse whose fit quality is the median one
    order = sorted(synapses, key=lambda h: h["r2"] * h["dip"])
    example = order[len(order) // 2]
    t, profile = example["t"], example["lines"].mean(axis=1)
    fig, axes = eff.figure(3, 3, figsize=(16, 12.5), sharex=True, sharey=True)
    for rank, (ax, r) in enumerate(zip(axes.ravel(), results), start=1):
        ax.plot(t * example["um"], profile, color="white", lw=2)
        try:
            m1, m2, curve = r["method"](t, profile, example["fit"][2], example["fit"][5])
            if curve is not None:
                ax.plot(curve[0] * example["um"], curve[1], color="#4DA3FF", lw=1.6, ls="--")
            for m in (m1, m2):
                ax.axvline(m * example["um"], color="#4DA3FF", lw=1.2, ls=":")
            here = f"This synapse: {abs(m2 - m1) * example['um']:.2f} µm, ExF = {abs(m2 - m1) * example['um'] * 1000 / syn.REF_NM:.1f}"
        except (RuntimeError, ValueError):
            here = "this synapse: no answer"
        e = r["exf"]
        ax.set_title(f"{rank}.  {r['label']}", color=eff.INK, fontsize=11, loc="left")
        ax.text(0.98, 0.97, f"Repeatability {r['repeat']:.1f}% of the distance\n"
                            f"All synapses: ExF = {e.mean():.1f} ± {e.std(ddof=1):.1f}  (median {np.median(e):.1f})\n"
                            f"no answer on {r['failed']} of {r['n']}\n{here}",
                transform=ax.transAxes, va="top", ha="right", color=eff.INK, fontsize=9, linespacing=1.5)
        ax.grid(color="#2A2A2A", lw=0.6)
    for ax in axes[-1]:
        ax.set_xlabel("Distance across the synapse (µm, as imaged)", color=eff.INK)
    for ax in axes[:, 0]:
        ax.set_ylabel("Pan, counts", color=eff.INK)
    fig.suptitle(f"Nine ways to read the DP–PSD distance, best to worst by repeatability "
                 f"({len(synapses)} antibody-positive synapses, {text})\n"
                 f"white = profile of one example synapse ({example['sample']}, slice {example['z']}, "
                 f"x {example['col']:.0f}, y {example['row']:.0f});  orange = the method's fit and the two centres it reports",
                 color=eff.INK, fontsize=12)
    fig.tight_layout()
    base.mkdir(parents=True, exist_ok=True)
    eff.save(fig, base / f"Fit options ({text}).png")
    with open(base / f"Fit options ({text}).csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Rank", "Method", "Repeatability (% of distance)", "ExF mean", "ExF s.d.", "ExF median", "No answer", "Synapses"])
        for rank, r in enumerate(results, start=1):
            e = r["exf"]
            w.writerow([rank, r["label"], round(r["repeat"], 2), round(e.mean(), 2), round(e.std(ddof=1), 2),
                        round(float(np.median(e)), 2), r["failed"], r["n"]])
            print(f"{rank}. {r['label']:48s} repeat {r['repeat']:5.1f}%   ExF {e.mean():.1f} ± {e.std(ddof=1):.1f} "
                  f"(median {np.median(e):.1f})   no answer {r['failed']}/{r['n']}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "GluA2")
