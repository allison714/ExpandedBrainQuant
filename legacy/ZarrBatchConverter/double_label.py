"""
Double-labelled samples: is one antibody channel showing up in the other (bleed-through), and how
much of what it should label does each antibody label (efficiency)?

  double_label.py [text]      the samples whose names contain the text (all when left out)

A sample is double-labelled when it has two or more antibody channels (every channel that is
not the pan stain or a nuclear stain, as in the other tools). It works on channels, not on
antibody names; a name is shown where antibody_channels.py knows it.

1. Bleed-through, source -> target, both ways, from the image itself
   Bleed-through is a fixed fraction k of the source's light arriving in the target, so at
   EVERY source spot the target must show at least k x source. Two antibodies on the same
   synapses also overlap, but nothing forces the target up at every source spot. So the
   source spots with the least target give a ceiling on k:

     source, target  at each strong source spot, both as the mean over a (2 WINDOW_PX + 1)-pixel
                square, in counts above the frame's background. A mean over a square, not the
                brightest pixel, so that a colour shift of a pixel or the noise of one pixel
                does not move it.
     floor slope     the line under the cloud of spots: a quantile regression of target on source
                at the FLOOR_PCT-th percentile, with a free intercept. Its slope is how fast the
                LEAST target seen rises with the source, which is what bleed-through does.
                Noise lowers the line as a whole (the intercept) but does not tilt it, so dim
                spots do not blur the answer the way a ratio per spot would.
     at most    the bootstrap 97.5% bound of that slope: bleed-through cannot be larger.
                'Slope Low' is the 2.5% bound: when it is at or under zero, the floor does not
                rise with the source at all and no bleed-through is detected.
     target-free  share of strong source spots with no target at all (under 2 noise-widths).
     explains   "at most" / the typical target-to-source ratio at spots that have both: the
                largest share of the target's signal at shared spots that bleed-through
                could account for.

   What this cannot do: where the two antibodies sit on the same spots in a steady ratio, a
   high ceiling does not prove bleed-through, it only fails to rule it out. Only a
   single-label control (one antibody, both channels imaged, same settings) measures k itself.
   A double label whose second channel is dark is used as one: it is marked "acts as control".

2. Efficiency, at the synapses synapse_exf.py found in the pan stain (its <sample>_synapses.csv)
   AB1 is the channel on the longest laser, AB2 the next, as in the Synapse Log. A synapse is
   positive for an antibody at SIGMAS noise-widths, the rule used everywhere else.

     of pan synapses        share positive for AB1, for AB2, for both, for neither. The pan
                            double peaks include membrane pairs, so these are lower bounds.
     at confirmed synapses  share of AB2-positive synapses that are also AB1-positive, and the
                            reverse, with Wilson 95% intervals. Where both antibodies are meant
                            to be at every (excitatory) synapse, as NR1 and Homer1 are, this is
                            the labelling efficiency of each antibody: the other one confirms
                            the synapse. Bleed-through would inflate it, which is why part 1
                            comes first.
     by chance              the share positive for both if the two were independent.

Output, in 'Zarr Comparisons\\Double Label': 'Double Label - Bleed-through.csv' (one row per
sample and direction), 'Double Label - Efficiency.csv' (one row per sample), a picture per
sample and 'Double Label - Summary.png'.
"""

import csv
import math
import sys
import textwrap
from pathlib import Path

import numpy as np

import ab_efficiency as eff
import antibody_channels
import channel_check
import convert_to_zarr as core
import roi_log as roi

SIGMAS = 6.0             # a spot, and a positive synapse, is this many noise-widths up
STRONG = 10.0            # only source spots this strong are used for the bleed-through test
WINDOW_PX = 2            # source and target are averaged over a square this far round the spot
FLOOR_PCT = 10.0         # the floor under the spots is the quantile regression at this percentile
FIT_SPOTS = 1200         # at most this many spots go into one fit (drawn at random)
ABSENT = 2.0             # the target is absent at a spot under this many of its noise-widths
PRESENT = 4.0            # ...and present above this many
FRAME_PX = 2048
FRAMES = 12              # camera frames read per sample, at most
MIN_SPOTS = 30           # fewer strong source spots than this give no verdict
DARK_BELOW = channel_check.DARK_BELOW
UNSURE_ABOVE = 10.0      # a limit this wide, with a slope that could be zero, is called inconclusive
NONE_BELOW = 3.0         # a ceiling under this many per cent is "no bleed-through to speak of"
BOOTSTRAPS = 150
FOLDER = "Double Label"


def frames_of(raw):
    """(plane, y, x) of the camera frames to read: the middle of the stack, the middle of the plane first."""
    n_z, ny, nx = raw.shape[1:]
    planes = sorted({int(round((n_z - 1) * f)) for f in (0.25, 0.4, 0.5, 0.6, 0.75)}) if n_z >= roi.STACK_MIN_PLANES else list(range(n_z))[:5]
    corners = [(y, x) for y in range(0, max(1, ny - FRAME_PX // 2), FRAME_PX) for x in range(0, max(1, nx - FRAME_PX // 2), FRAME_PX)]
    if len(corners) == 1:
        corners = [(max(0, (ny - FRAME_PX) // 2), max(0, (nx - FRAME_PX) // 2))]
    corners.sort(key=lambda c: (c[0] + FRAME_PX / 2 - ny / 2) ** 2 + (c[1] + FRAME_PX / 2 - nx / 2) ** 2)
    return [(z, y, x) for y, x in corners[:max(1, FRAMES // len(planes))] for z in planes]


def wilson(k, n):
    """Wilson 95% interval of a share k / n, in per cent."""
    if not n:
        return None, None
    p, z = k / n, 1.96
    mid, half = (p + z * z / (2 * n)) / (1 + z * z / n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return 100 * max(0.0, mid - half), 100 * min(1.0, mid + half)


def bleed_through(name, zarr_dir, out_root):
    """(channels, pan, antibody indices, names, {(source, target): arrays}, {channel: spots per 1000 µm²})."""
    from scipy import ndimage
    from skimage import measure

    series = zarr_dir / "0"
    info = core.read_series(series)
    channels, pan, antibodies, _ = eff.channel_roles(name, zarr_dir, out_root, info)
    named = antibody_channels.for_sample(name, channels, antibodies)
    if len(antibodies) < 2:
        return channels, pan, antibodies, named, {}, {}
    raw = eff.open_level(series, info, 0)                              # lazy (c, z, y, x)
    um = eff.voxel_um(info, raw.shape[1:])[2] or 1.0
    pairs = {(s, t): {"s": [], "t": [], "tn": []} for s in antibodies for t in antibodies if s != t}
    count, area = {c: 0 for c in antibodies}, 0.0
    side = 2 * WINDOW_PX + 1
    for z, y, x in frames_of(raw):
        tile = raw[:, z, y:y + FRAME_PX, x:x + FRAME_PX][antibodies].compute()
        got = {c: channel_check.frame_numbers(tile[i]) for i, c in enumerate(antibodies)}
        if any(g is None for g in got.values()):
            continue                                                    # stitching padding
        area += float((tile[0] > 0).sum()) * um * um
        box, box_noise = {}, {}
        for c, (smooth, middle, _) in got.items():
            box[c] = ndimage.uniform_filter(smooth - middle, side)
            box_noise[c] = 1.4826 * float(np.median(np.abs(box[c] - np.median(box[c])))) or 1.0
        for s in antibodies:
            smooth, middle, noise = got[s]
            labels = measure.label(smooth > middle + SIGMAS * noise)
            regions = [r for r in measure.regionprops(labels, intensity_image=smooth) if r.area >= 3]
            count[s] += len(regions)
            for r in regions:
                if r.intensity_max - middle < STRONG * noise:
                    continue
                rr, cc = np.unravel_index(np.argmax(np.where(labels[r.slice] == r.label, smooth[r.slice], -np.inf)), r.image.shape)
                at = (r.slice[0].start + rr, r.slice[1].start + cc)        # the spot's brightest pixel
                if box[s][at] <= 0:
                    continue
                for t in antibodies:
                    if t != s:
                        pairs[(s, t)]["s"].append(float(box[s][at]))
                        pairs[(s, t)]["t"].append(float(box[t][at]))
                        pairs[(s, t)]["tn"].append(float(box[t][at] / box_noise[t]))
    density = {c: count[c] / area * 1000 if area else 0.0 for c in antibodies}
    return channels, pan, antibodies, named, {k: {a: np.asarray(b) for a, b in v.items()} for k, v in pairs.items()}, density


def floor_line(src, tgt, q):
    """(intercept, slope) of the quantile regression of target on source: the line with a share q of the spots under it."""
    from scipy.optimize import linprog

    n = len(src)
    cost = np.concatenate([[0, 0], np.full(n, q), np.full(n, 1 - q)])
    rows = np.hstack([np.ones((n, 1)), src[:, None], np.eye(n), -np.eye(n)])
    done = linprog(cost, A_eq=rows, b_eq=tgt, bounds=[(None, None)] * 2 + [(0, None)] * (2 * n), method="highs")
    return float(done.x[0]), float(done.x[1])


def judge(v, rng):
    """The numbers and the verdict for one direction, from its spots."""
    src, tgt, tn = v["s"], v["t"], v["tn"]
    out = {"Source Spots": len(src)}
    if len(src) < MIN_SPOTS:
        out["Verdict"] = "too few strong source spots to test"
        return out
    if len(src) > FIT_SPOTS:
        keep = rng.choice(len(src), FIT_SPOTS, replace=False)
        f_src, f_tgt = src[keep], tgt[keep]
    else:
        f_src, f_tgt = src, tgt
    intercept, slope = floor_line(f_src, f_tgt, FLOOR_PCT / 100)
    boot = []
    for _ in range(BOOTSTRAPS):
        pick = rng.integers(0, len(f_src), len(f_src))
        boot.append(floor_line(f_src[pick], f_tgt[pick], FLOOR_PCT / 100)[1])
    low, high = (100 * float(b) for b in np.percentile(boot, [2.5, 97.5]))
    limit = max(0.0, high)
    ratio = tgt / src
    here = tn >= PRESENT
    typical = 100 * float(np.median(ratio[here])) if here.sum() >= 5 else None
    explains = None if typical is None or typical <= 0 else min(100.0, 100 * limit / typical)
    cv = float(1.4826 * np.median(np.abs(ratio[here] - np.median(ratio[here]))) / np.median(ratio[here])) if here.sum() >= 5 and typical else None
    r = float(np.corrcoef(src, tgt)[0, 1]) if src.std() > 0 and tgt.std() > 0 else 0.0
    free = 100 * float((tn < ABSENT).mean())
    out.update({"Target-Free Source Spots (%)": round(free, 1), "Target Present At (%)": round(100 * float(here.mean()), 1),
                "Floor Slope (%)": round(100 * slope, 2), "Slope Low (%)": round(low, 2), "Bleed-Through At Most (%)": round(limit, 2),
                "Floor Intercept (counts)": round(intercept, 2),
                "Typical Ratio Where Both (%)": None if typical is None else round(typical, 1),
                "Ratio CV": None if cv is None else round(cv, 2), "r": round(r, 2),
                "Could Explain Of Target (%)": None if explains is None else round(explains, 1)})
    if limit < NONE_BELOW:
        out["Verdict"] = f"none to speak of: at most {limit:.1f}% of the source"
    elif low <= 0 and limit < UNSURE_ABOVE:
        out["Verdict"] = f"none detected: the least target seen does not rise with the source (at most {limit:.1f}% of it)"
    elif low <= 0:
        out["Verdict"] = f"inconclusive: too few or too dim spots to tell (anywhere from none to {limit:.0f}% of the source)"
    elif limit < UNSURE_ABOVE or (explains is not None and explains < 25):
        out["Verdict"] = f"minor: {100 * slope:.1f}% of the source (at most {limit:.1f}%)"
    else:
        out["Verdict"] = (f"NOT RULED OUT: the least target seen rises by {100 * slope:.0f}% of the source ({low:.0f} to {high:.0f}%); "
                          "bleed-through, or two labels that grow together")
    return out


def efficiency(csv_path):
    """Counts of antibody-positive synapses from a sample's synapse table, or None when it has none."""
    if not csv_path.is_file():
        return None
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    value = lambda row, key: float(row[key]) if row.get(key) not in (None, "") else None
    both = [(value(r, "AB Signal (σ)"), value(r, "AB2 Signal (σ)")) for r in rows]
    both = [(a, b) for a, b in both if a is not None and b is not None]
    if not both:
        return None
    a = np.array([p[0] for p in both]) >= SIGMAS
    b = np.array([p[1] for p in both]) >= SIGMAS
    return {"n": len(both), "ab1": int(a.sum()), "ab2": int(b.sum()), "both": int((a & b).sum()), "neither": int((~a & ~b).sum())}


def picture(png, name, channels, label, pairs, verdicts, counts):
    """The spots of both directions with the ceiling drawn in, and how the synapses divide."""
    order = sorted(pairs, key=lambda k: -(channels[k[0]].get("laser") or 0))[:2]
    fig, axes = eff.figure(1, 3, figsize=(16, 4.8), gridspec_kw={"width_ratios": [1, 1, 0.9]})
    for ax, key in zip(axes, order):
        s, t = key
        v, row = pairs[key], verdicts[key]
        if len(v["s"]):
            ax.scatter(v["s"], v["t"], s=6, color=eff.colour_of(channels[t]), alpha=0.5, lw=0)
            top_x, top_y = np.percentile(v["s"], 99.5), max(np.percentile(v["t"], 99.5), 1.0)
            ax.set_xlim(0, top_x * 1.05)
            ax.set_ylim(min(0.0, float(np.percentile(v["t"], 0.5))), top_y * 1.05)
            if "Bleed-Through At Most (%)" in row:
                a, k, most = row["Floor Intercept (counts)"], row["Floor Slope (%)"] / 100, row["Bleed-Through At Most (%)"]
                ax.plot([0, top_x], [a, a + k * top_x], color="white", lw=1.5,
                        label=f"Floor under the spots: rises by {100 * k:.1f}% of the source (at most {most:.1f}%)")
                if row["Typical Ratio Where Both (%)"]:
                    m = row["Typical Ratio Where Both (%)"] / 100
                    ax.plot([0, top_x], [0, m * top_x], color=eff.MUTED, lw=1, ls="--", label=f"Typical where both are present: {100 * m:.0f}%")
                ax.legend(frameon=False, labelcolor=eff.INK, fontsize=8, loc="upper left")
        ax.set_xlabel(f"Source: {label(s)} (counts above background)", color=eff.INK, fontsize=9)
        ax.set_ylabel(f"Target: {label(t)} (counts above background)", color=eff.INK, fontsize=9)
        ax.set_title(f"{label(s)} into {label(t)}\n" + textwrap.fill(row["Verdict"], 78), color=eff.INK, fontsize=9, loc="left")
        ax.grid(color="#2A2A2A", lw=0.6)
    ax = axes[2]
    if counts:
        n = counts["n"]
        parts = [("AB1 only", counts["ab1"] - counts["both"], eff.colour_of(channels[order[0][0]])), ("Both", counts["both"], (1, 1, 1)),
                 ("AB2 only", counts["ab2"] - counts["both"], eff.colour_of(channels[order[0][1]])), ("Neither", counts["neither"], (0.35, 0.35, 0.35))]
        ax.bar([p[0] for p in parts], [100 * p[1] / n for p in parts], color=[p[2] for p in parts], width=0.65)
        for i, p in enumerate(parts):
            ax.text(i, 100 * p[1] / n + 1.5, f"{100 * p[1] / n:.0f}%\n({p[1]})", ha="center", va="bottom", color=eff.INK, fontsize=8)
        ax.set_ylim(0, 100 * max(p[1] for p in parts) / n * 1.3 + 5)
        ax.set_ylabel(f"Share of the {n} synapses found in the pan stain (%)", color=eff.INK, fontsize=9)
        one = f"AB1 at AB2-positive synapses: {100 * counts['both'] / counts['ab2']:.0f}%" if counts["ab2"] else "no AB2-positive synapses"
        two = f"AB2 at AB1-positive synapses: {100 * counts['both'] / counts['ab1']:.0f}%" if counts["ab1"] else "no AB1-positive synapses"
        ax.set_title(f"{one}\n{two}", color=eff.INK, fontsize=9, loc="left")
    else:
        ax.axis("off")
        ax.text(0.5, 0.5, "No synapses from the pan stain:\nefficiency not measured", ha="center", va="center", color=eff.MUTED, fontsize=10, transform=ax.transAxes)
    fig.suptitle(f"{name}   ·   AB1 = {label(order[0][0])}, AB2 = {label(order[0][1])}", color=eff.INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    eff.save(fig, png)


def summary_picture(png, bleed, eff_rows):
    """Every sample at a glance: the bleed-through limit each way, and each antibody's efficiency."""
    names = sorted({r["Sample"] for r in bleed})
    at = {n: i for i, n in enumerate(names)}
    fig, axes = eff.figure(1, 2, figsize=(16, max(4.5, 0.33 * len(names) + 2)), sharey=True)
    seen = set()
    for r in bleed:
        if "Bleed-Through At Most (%)" not in r:
            continue
        up = r["Direction"] == "AB1 into AB2"
        hollow = r["Verdict"].startswith("NOT RULED OUT")
        axes[0].scatter(r["Bleed-Through At Most (%)"], at[r["Sample"]] + (-0.15 if up else 0.15), s=42, marker="o" if up else "s",
                        facecolor="none" if hollow else ("#FF40FF" if up else "#40FF40"), edgecolor="#FF40FF" if up else "#40FF40", lw=1.5,
                        label=None if (up, hollow) in seen else f"{r['Direction']}{' (not ruled out)' if hollow else ''}")
        seen.add((up, hollow))
    axes[0].set_xscale("log")
    axes[0].axvline(NONE_BELOW, color=eff.MUTED, lw=1, ls=":")
    axes[0].set_xlabel("Bleed-through at most (% of the source's signal; open marker = not ruled out)", color=eff.INK, fontsize=9)
    axes[0].set_yticks(range(len(names)))
    axes[0].set_yticklabels(names, color=eff.INK, fontsize=8)
    axes[0].invert_yaxis()
    axes[0].legend(frameon=False, labelcolor=eff.INK, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2)
    axes[0].set_title("Bleed-through, each way", color=eff.INK, fontsize=10, loc="left")
    for r in eff_rows:
        for key, lo, hi, shift, colour, mark in (("AB1 At AB2+ Synapses (%)", "AB1 Low", "AB1 High", -0.15, "#FF40FF", "o"),
                                                 ("AB2 At AB1+ Synapses (%)", "AB2 Low", "AB2 High", 0.15, "#40FF40", "s")):
            if r.get(key) is not None:
                y = at[r["Sample"]] + shift
                axes[1].plot([r[lo], r[hi]], [y, y], color=colour, lw=1.5)
                axes[1].scatter(r[key], y, s=42, marker=mark, color=colour, zorder=3)
    axes[1].scatter([], [], marker="o", color="#FF40FF", label="AB1 (longest laser) at AB2-positive synapses")
    axes[1].scatter([], [], marker="s", color="#40FF40", label="AB2 at AB1-positive synapses")
    axes[1].legend(frameon=False, labelcolor=eff.INK, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2)
    axes[1].set_xlim(0, 100)
    axes[1].set_xlabel("Labelling efficiency at synapses confirmed by the other antibody (%, Wilson 95% interval)", color=eff.INK, fontsize=9)
    axes[1].set_title("Efficiency", color=eff.INK, fontsize=10, loc="left")
    for ax in axes:
        ax.grid(color="#2A2A2A", lw=0.6)
    fig.tight_layout()
    eff.save(fig, png)


def write(path, rows):
    heads = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, heads)
        w.writeheader()
        w.writerows(rows)


def run(text):
    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    base = Path(core.DEFAULT_SHOTS).parent / "Zarr Comparisons" / FOLDER
    synapses = Path(core.DEFAULT_SHOTS).parent / "Zarr Synapse ExF"
    base.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    names = [(n, d) for n, d in roi.samples(out_root) if text.lower() in n.lower() and not any(s in n.lower() for s in channel_check.SKIP)]
    bleed, eff_rows = [], []
    for name, zarr_dir in names:
        try:
            channels, pan, antibodies, named, pairs, density = bleed_through(name, zarr_dir, out_root)
        except Exception as e:
            print(f"{name}: skipped ({e})", flush=True)
            continue
        if len(antibodies) < 2:
            continue
        label = lambda c: (named[c][0] + " " if named.get(c, ("",))[0] else "") + channels[c]["label"]
        by_laser = sorted(antibodies, key=lambda c: (-(channels[c].get("laser") or 0), c))
        ab1, ab2 = by_laser[0], by_laser[1]                     # as synapse_exf.py numbers them
        verdicts = {}
        for (s, t), v in pairs.items():
            row = judge(v, rng)
            dark = density[t] < DARK_BELOW
            if dark and "Bleed-Through At Most (%)" in row:
                row["Verdict"] += "; the target channel is dark, so this sample acts as a single-label control"
            verdicts[(s, t)] = row
            bleed.append({"Sample": name, "Direction": "AB1 into AB2" if (s, t) == (ab1, ab2) else "AB2 into AB1" if (s, t) == (ab2, ab1) else "other",
                          "Source": label(s), "Target": label(t), "Source Spots / 1000 µm²": round(density[s], 2),
                          "Target Spots / 1000 µm²": round(density[t], 2), **row})
        counts = efficiency(synapses / name / f"{name}_synapses.csv") if pan is not None else None
        line = {"Sample": name, "AB1": label(ab1), "AB2": label(ab2), "Pan": channels[pan]["label"] if pan is not None else "none found"}
        if counts:
            n = counts["n"]
            a_lo, a_hi = wilson(counts["both"], counts["ab2"])
            b_lo, b_hi = wilson(counts["both"], counts["ab1"])
            share = lambda k: round(100 * k / n, 1)
            line.update({"Pan Synapses": n, "AB1+ (%)": share(counts["ab1"]), "AB2+ (%)": share(counts["ab2"]), "Both (%)": share(counts["both"]),
                         "Both By Chance (%)": round(100 * counts["ab1"] / n * counts["ab2"] / n, 1),
                         "AB1 Only (%)": share(counts["ab1"] - counts["both"]), "AB2 Only (%)": share(counts["ab2"] - counts["both"]),
                         "Neither (%)": share(counts["neither"]), "AB2+ Synapses": counts["ab2"],
                         "AB1 At AB2+ Synapses (%)": round(100 * counts["both"] / counts["ab2"], 1) if counts["ab2"] else None,
                         "AB1 Low": None if a_lo is None else round(a_lo, 1), "AB1 High": None if a_hi is None else round(a_hi, 1),
                         "AB1+ Synapses": counts["ab1"],
                         "AB2 At AB1+ Synapses (%)": round(100 * counts["both"] / counts["ab1"], 1) if counts["ab1"] else None,
                         "AB2 Low": None if b_lo is None else round(b_lo, 1), "AB2 High": None if b_hi is None else round(b_hi, 1)})
        else:
            line["Notes"] = "no synapses from the pan stain (no pan stain found, or the sample is left out of the Synapse Log)"
        for key, title in (((ab1, ab2), "AB1 into AB2"), ((ab2, ab1), "AB2 into AB1")):
            line[f"Bleed-Through {title}"] = verdicts[key]["Verdict"]
        eff_rows.append(line)
        picture(base / f"{name}_double-label.png", name, channels, label, pairs, verdicts, counts)
        print(f"{name}\n   {label(ab1)} into {label(ab2)}: {verdicts[(ab1, ab2)]['Verdict']}\n   {label(ab2)} into {label(ab1)}: {verdicts[(ab2, ab1)]['Verdict']}\n"
              + (f"   efficiency: {label(ab1)} {line.get('AB1 At AB2+ Synapses (%)')}% of {counts['ab2']} AB2+ synapses; "
                 f"{label(ab2)} {line.get('AB2 At AB1+ Synapses (%)')}% of {counts['ab1']} AB1+ synapses ({counts['n']} pan synapses)" if counts else "   efficiency: no pan synapses"),
              flush=True)
    if not eff_rows:
        sys.exit("No double-labelled sample.")
    write(base / "Double Label - Bleed-through.csv", bleed)
    write(base / "Double Label - Efficiency.csv", eff_rows)
    summary_picture(base / "Double Label - Summary.png", bleed, eff_rows)
    print(f"\n{len(eff_rows)} double-labelled sample(s). {base}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "")
