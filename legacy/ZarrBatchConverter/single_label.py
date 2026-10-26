"""
Labelling efficiency of single-labelled samples, antibody against antibody.

  single_label.py [text]            single-labelled samples whose names contain the text (all when left out)
  single_label.py --multi [text]    the same test on every antibody channel of double- and triple-labelled samples

A sample is single-labelled when it has one antibody channel (every channel that is not the
pan stain or a nuclear stain). With one antibody there is no second label to confirm a
synapse, so the synapses are the ones synapse_exf.py found in the pan stain
(<sample>_synapses.csv; run Measure_ExF.bat first). A sample with no pan stain cannot be
measured this way and is listed as such.

Per sample
  labelled       share of the synapses with antibody on them: the brightest antibody pixel in
                 the synapse's footprint (both densities and a quarter of their spacing beyond,
                 FOOT_HALF_PX either side of the middle along the synapse) is SIGMAS
                 noise-widths above the median of its camera frame. The rule of the Synapse Log.
  by chance      the same footprint, turned the same way, moved DECOY_NM of tissue away in each
                 of four diagonal directions: the share of those decoys that read as labelled.
                 This is what dense or unspecific labelling gives with no synapse there.
  efficiency     (labelled - by chance) / (1 - by chance): the share of synapses labelled
                 beyond chance. It is a LOWER bound on the true efficiency, because some of
                 the double peaks found in the pan stain are pairs of membranes, not synapses,
                 and no antibody should label those.
  clearest third the same, on the third of the synapses with the cleanest pan profile (fit
                 quality x cleft depth, which do not use the antibody): fewer membrane pairs,
                 so closer to the true efficiency.
  enrichment     labelled / by chance: how much more often the antibody is at a synapse than
                 at a decoy. Near 1 for an antibody that is not synaptic.
  signal         median brightness of the label at labelled synapses, in noise-widths.

Per antibody the samples are pooled, and the interval is a bootstrap over fields of view (one
camera frame each). Fields of one animal are not independent animals: where an antibody was
imaged in one animal, the interval says how steady the number is across that tissue, not
across animals. Laser power and exposure are not in the files; a dim image lowers "labelled".

Left out: single tiles ('_F0'), PFF and bead samples, and samples with fewer than MIN_SYNAPSES
synapses.

With --multi each antibody channel of a sample is measured on its own, exactly as above, and
the channels are compared by imaging day: which antibody is on which channel is shown where
antibody_channels.py knows it, otherwise the channel is named with the antibodies in the file
name ('637 Cy5 (NR1 or Homer1)'). double_label.py asks the other question for these samples:
how often the two labels are on the same synapse, and whether one channel bleeds into the other.

Output, in 'Zarr Comparisons\\Single Label' (or 'Multi Label'): '<Single or Multi> Label - Per
Sample.csv', '... - Per Antibody.csv', '... - Efficiency.png'.
"""

import csv
import math
import re
import sys
from pathlib import Path

import numpy as np

import ab_efficiency as eff
import antibody_channels
import channel_check
import compare_ab
import convert_to_zarr as core
import roi_log as roi
import synapse_exf as syn

SIGMAS = syn.AB_SIGMAS
FOOT_HALF_PX_NM = 60.0   # the footprint runs this far along the synapse either side of its middle (nm of tissue, at the nominal expansion)
DECOY_NM = 400.0         # decoys are this far from the synapse (nm of tissue, at the nominal expansion)
MIN_SYNAPSES = 30
BOOTSTRAPS = 2000
FOLDER = "Single Label"


def set_of(name):
    """'WT', 'KO' or '' from a sample name."""
    for token in name.split("_"):
        found = re.fullmatch(r"(WT|KO)\d*[a-z]?", token, re.IGNORECASE)
        if found:
            return found.group(1).upper()
    return ""


def measure_sample(name, zarr_dir, out_root, csv_path, ab_c=None, logged_column="AB Signal (σ)"):
    """Per-synapse arrays for one antibody channel of one sample (the only one when ab_c is None):
    antibody at the synapse and at its four decoys, in noise-widths."""
    from scipy import ndimage

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if len(rows) < MIN_SYNAPSES:
        return None
    series = zarr_dir / "0"
    info = core.read_series(series)
    channels, pan, antibodies, _ = eff.channel_roles(name, zarr_dir, out_root, info)
    raw = eff.open_level(series, info, 0)                              # lazy (c, z, y, x)
    um = eff.voxel_um(info, raw.shape[1:])[2]
    ab_c = antibodies[0] if ab_c is None else ab_c
    px_nm = syn.EXF_NOMINAL / (um * 1000)                              # pixels per nm of tissue, at the nominal expansion
    half, away = FOOT_HALF_PX_NM * px_nm, DECOY_NM * px_nm
    ny, nx = raw.shape[2:]
    on, decoy, logged, clean, field = [], [], [], [], []
    by_plane = {}
    for r in rows:
        by_plane.setdefault(int(r["Slice"]), []).append(r)
    for z, group in sorted(by_plane.items()):
        both = raw[:, z][[pan, ab_c]].compute().astype(np.float32)
        valid = both[0] > 0
        ab = ndimage.gaussian_filter(both[1], syn.SMOOTH_PX)
        for r in group:
            row, col = float(r["Y (px)"]), float(r["X (px)"])
            y0, x0 = int(row // syn.TILE_PX) * syn.TILE_PX, int(col // syn.TILE_PX) * syn.TILE_PX       # its camera frame
            tile, ok = ab[y0:y0 + syn.TILE_PX, x0:x0 + syn.TILE_PX], valid[y0:y0 + syn.TILE_PX, x0:x0 + syn.TILE_PX]
            if ok.sum() < 1000:
                continue
            middle = float(np.median(tile[ok]))
            noise = 1.4826 * float(np.median(np.abs(tile[ok] - middle))) or 1.0
            theta = math.radians(float(r["Orientation (deg)"]))
            along = np.array([math.cos(theta), math.sin(theta)])
            across = np.array([-along[1], along[0]])
            gap = float(r["DP-PSD peak to peak (µm, as imaged)"]) / um
            t = np.arange(-0.75 * gap, 0.75 * gap + 0.25, 0.5)             # both densities and a quarter of their spacing beyond
            s = np.arange(-half, half + 0.5, 1.0)

            def reading(dy, dx):
                rr = row + dy + t[:, None] * across[0] + s[None, :] * along[0]
                cc = col + dx + t[:, None] * across[1] + s[None, :] * along[1]
                if rr.min() < 1 or cc.min() < 1 or rr.max() > ny - 2 or cc.max() > nx - 2:
                    return np.nan
                if not valid[np.clip(rr.astype(int), 0, ny - 1), np.clip(cc.astype(int), 0, nx - 1)].all():
                    return np.nan                                          # stitching padding
                return float((ndimage.map_coordinates(ab, [rr, cc], order=1).max() - middle) / noise)

            here = reading(0, 0)
            if np.isnan(here):
                continue
            step = away / math.sqrt(2)
            on.append(here)
            decoy.append([reading(a * step, b * step) for a, b in ((1, 1), (1, -1), (-1, 1), (-1, -1))])
            logged.append(float(r[logged_column]) if r.get(logged_column) not in (None, "") else np.nan)
            clean.append(float(r["Fit R²"]) * float(r["Dip"]))
            field.append(r["Field of View"])
    if len(on) < MIN_SYNAPSES:
        return None
    return {"on": np.array(on), "decoy": np.array(decoy), "logged": np.array(logged), "clean": np.array(clean), "field": np.array(field),
            "channel": channels[ab_c]["label"], "pixel_nm": um * 1000}


def numbers(on, decoy):
    """(labelled %, by chance %, efficiency %, enrichment) from the readings of a set of synapses."""
    if not len(on):
        return np.nan, np.nan, np.nan, np.nan
    labelled = float((on >= SIGMAS).mean())
    chance = float((decoy[np.isfinite(decoy)] >= SIGMAS).mean()) if np.isfinite(decoy).any() else np.nan
    efficiency = (labelled - chance) / (1 - chance) if chance < 1 else np.nan
    return 100 * labelled, 100 * chance, 100 * efficiency, labelled / chance if chance > 0 else np.inf


def pooled(samples, rng):
    """One antibody's samples together, with a bootstrap over fields of view."""
    on = np.concatenate([s["on"] for s in samples])
    decoy = np.concatenate([s["decoy"] for s in samples])
    clean = np.concatenate([s["clean"] >= np.percentile(s["clean"], 100 * 2 / 3) for s in samples])
    field = np.concatenate([np.char.add(s["Sample"] + " ", s["field"]) for s in samples])
    names = np.unique(field)
    members = [np.flatnonzero(field == n) for n in names]
    boot, boot_clean = [], []
    for _ in range(BOOTSTRAPS):
        pick = np.concatenate([members[i] for i in rng.integers(0, len(names), len(names))])
        boot.append(numbers(on[pick], decoy[pick])[2])
        boot_clean.append(numbers(on[pick][clean[pick]], decoy[pick][clean[pick]])[2])
    return numbers(on, decoy), numbers(on[clean], decoy[clean]), np.nanpercentile(boot, [2.5, 97.5]), np.nanpercentile(boot_clean, [2.5, 97.5]), \
        len(names), len(on), float(np.median(on[on >= SIGMAS])) if (on >= SIGMAS).any() else np.nan


def run(text, multi=False):
    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    synapses = Path(core.DEFAULT_SHOTS).parent / "Zarr Synapse ExF"
    kind = "Multi Label" if multi else FOLDER
    base = Path(core.DEFAULT_SHOTS).parent / "Zarr Comparisons" / kind
    base.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    measured, per_sample = [], []
    for name, zarr_dir in roi.samples(out_root):
        if text.lower() not in name.lower() or any(s in name.lower() for s in channel_check.SKIP) or re.search(r"_F\d+$", name):
            continue
        try:
            info = core.read_series(zarr_dir / "0")
            channels, pan, antibodies, _ = eff.channel_roles(name, zarr_dir, out_root, info)
        except Exception as e:
            print(f"{name}: skipped ({e})", flush=True)
            continue
        if (len(antibodies) < 2) if multi else (len(antibodies) != 1):
            continue
        named = antibody_channels.for_sample(name, channels, antibodies)
        in_name = " or ".join(label for label, _ in syn.antibodies_in(name))
        by_laser = sorted(antibodies, key=lambda c: (-(channels[c].get("laser") or 0), c))     # AB1, AB2 as synapse_exf.py numbers them
        path = synapses / name / f"{name}_synapses.csv"
        for ab_c in (by_laser if multi else antibodies):
            if multi:
                key = named[ab_c][0] if named.get(ab_c, ("",))[0] else channels[ab_c]["label"] + (f" ({in_name})" if in_name else "")
            else:
                key = compare_ab.antibody_key(name) or "not named"
            line = {"Sample": name, "Antibody": key, "Set": set_of(name), "Channel": channels[ab_c]["label"]}
            got = None
            if pan is None:
                line["Notes"] = "no pan stain: synapses cannot be found, efficiency not measured"
            elif not path.is_file():
                line["Notes"] = "not in the Synapse Log (left out there, or no synapses found)"
            else:
                column = {0: "AB Signal (σ)", 1: "AB2 Signal (σ)"}.get(by_laser.index(ab_c), "")
                got = measure_sample(name, zarr_dir, out_root, path, ab_c, column)
                if got is None:
                    line["Notes"] = f"fewer than {MIN_SYNAPSES} synapses"
            if got:
                every, chance = numbers(got["on"], got["decoy"]), None
                keep = got["clean"] >= np.percentile(got["clean"], 100 * 2 / 3)
                best = numbers(got["on"][keep], got["decoy"][keep])
                known = np.isfinite(got["logged"])
                agree = float(np.mean((got["on"][known] >= SIGMAS) == (got["logged"][known] >= SIGMAS))) if known.any() else np.nan
                line.update({"Pixel (nm, as imaged)": round(got["pixel_nm"], 1), "Synapses": len(got["on"]), "Fields of View": len(set(got["field"])),
                             "Labelled (%)": round(every[0], 1), "By Chance (%)": round(every[1], 1), "Efficiency (%)": round(every[2], 1),
                             "Enrichment (× chance)": round(every[3], 1) if np.isfinite(every[3]) else None,
                             "Efficiency, Clearest Third (%)": round(best[2], 1), "Labelled, Clearest Third (%)": round(best[0], 1),
                             "Signal at Labelled Synapses (noise-widths)": round(float(np.median(got["on"][got["on"] >= SIGMAS])), 1) if (got["on"] >= SIGMAS).any() else None,
                             "Labelled in the Synapse Log (%)": round(100 * float((got["logged"][known] >= SIGMAS).mean()), 1) if known.any() else None,
                             "Agreement with the Synapse Log (%)": None if np.isnan(agree) else round(100 * agree, 1)})
                measured.append({**got, "Sample": name, "Antibody": key, "Set": set_of(name)})
                print(f"{name}: {key}  labelled {every[0]:.0f}%, by chance {every[1]:.0f}%, efficiency {every[2]:.0f}% "
                      f"(clearest third {best[2]:.0f}%), {len(got['on'])} synapses", flush=True)
            else:
                print(f"{name}: {key}  {line['Notes']}", flush=True)
            per_sample.append(line)
    if not measured:
        sys.exit(f"No {'multi' if multi else 'single'}-labelled sample with synapses.")

    groups = {}
    for m in measured:
        day = m["Sample"].split("_")[0]
        groups.setdefault((m["Antibody"], m["Set"], day if day[:2].isdigit() else day, round(m["pixel_nm"] / 5) * 5), []).append(m)
    per_antibody = []
    for (key, set_, day, pixel), group in sorted(groups.items()):
        every, best, span, span_best, fields, n, signal = pooled(group, rng)
        per_antibody.append({"Antibody": key, "Set": set_, "Imaged": day, "Pixel (nm, as imaged)": pixel, "Samples": len(group), "Fields of View": fields, "Synapses": n,
                             "Labelled (%)": round(every[0], 1), "By Chance (%)": round(every[1], 1), "Efficiency (%)": round(every[2], 1),
                             "Efficiency Low": round(float(span[0]), 1), "Efficiency High": round(float(span[1]), 1),
                             "Enrichment (× chance)": round(every[3], 1) if np.isfinite(every[3]) else None,
                             "Efficiency, Clearest Third (%)": round(best[2], 1), "Clearest Third Low": round(float(span_best[0]), 1),
                             "Clearest Third High": round(float(span_best[1]), 1),
                             "Signal at Labelled Synapses (noise-widths)": None if np.isnan(signal) else round(signal, 1)})

    def write(file, table):
        heads = list(dict.fromkeys(k for r in table for k in r))
        with open(base / file, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, heads)
            w.writeheader()
            w.writerows(table)

    write(f"{kind} - Per Sample.csv", per_sample)
    write(f"{kind} - Per Antibody.csv", per_antibody)

    order = sorted(per_antibody, key=lambda r: -r["Efficiency, Clearest Third (%)"])
    fig, axes = eff.figure(1, 2, figsize=(16, max(4.5, 0.42 * len(order) + 2.2)), sharey=True)
    colour = {"WT": "#E8E8E8", "KO": "#FF9900", "": "#4DA3FF"}
    for i, r in enumerate(order):
        for ax, key, lo, hi in ((axes[0], "Efficiency (%)", "Efficiency Low", "Efficiency High"),
                                (axes[1], "Efficiency, Clearest Third (%)", "Clearest Third Low", "Clearest Third High")):
            ax.plot([r[lo], r[hi]], [i, i], color=colour[r["Set"]], lw=2)
            ax.scatter(r[key], i, s=70, color=colour[r["Set"]], zorder=3)
            ax.text(r[hi] + 1.5, i, f"{r[key]:.0f}%", color=eff.INK, fontsize=8, va="center")
        axes[0].scatter(r["By Chance (%)"], i, s=40, marker="|", color=eff.MUTED, zorder=2)
    axes[0].set_yticks(range(len(order)))
    axes[0].set_yticklabels([f"{r['Antibody']}  {r['Set']}  ·  {r['Imaged']}  ·  {r['Synapses']} synapses" for r in order], color=eff.INK, fontsize=9)
    axes[0].invert_yaxis()
    axes[0].set_title("All synapses found in the pan stain (grey tick: labelled by chance)", color=eff.INK, fontsize=10, loc="left")
    axes[1].set_title("Clearest third of the synapses (fewer membrane pairs)", color=eff.INK, fontsize=10, loc="left")
    for ax in axes:
        ax.set_xlim(min(-5, min(r["Efficiency Low"] for r in order) - 3), 105)
        ax.axvline(0, color=eff.MUTED, lw=1, ls=":")
        ax.set_xlabel("Labelling efficiency beyond chance (%, bootstrap 95% interval over fields of view)", color=eff.INK, fontsize=9)
        ax.grid(color="#2A2A2A", lw=0.6)
    for set_, c in colour.items():
        if any(r["Set"] == set_ for r in order):
            axes[1].scatter([], [], color=c, s=70, label=set_ or "No genotype in the name")
    axes[1].legend(frameon=False, labelcolor=eff.INK, fontsize=9, loc="lower right")
    fig.suptitle(("Double- and triple-label samples, each antibody channel" if multi else "Single-label antibodies") + ": share of synapses labelled, beyond chance", color=eff.INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    eff.save(fig, base / f"{kind} - Efficiency.png")
    print()
    for r in order:
        print(f"   {r['Antibody']:18s} {r['Set']:3s} {r['Imaged']:7s} labelled {r['Labelled (%)']:5.1f}%  chance {r['By Chance (%)']:5.1f}%  "
              f"efficiency {r['Efficiency (%)']:5.1f}% ({r['Efficiency Low']:.0f}–{r['Efficiency High']:.0f})  "
              f"clearest third {r['Efficiency, Clearest Third (%)']:5.1f}% ({r['Clearest Third Low']:.0f}–{r['Clearest Third High']:.0f})  "
              f"enrichment {r['Enrichment (× chance)']}  n = {r['Synapses']}, {r['Fields of View']} fields, {r['Samples']} sample(s)")
    print(f"\n{base}")


if __name__ == "__main__":
    words = [a for a in sys.argv[1:] if a != "--multi"]
    run(words[0] if words else "", multi="--multi" in sys.argv)
