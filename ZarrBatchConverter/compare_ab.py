"""
Compare antibody labelling between two sets of samples, antibody by antibody.

  compare_ab.py [A] [B]      for example:  compare_ab.py SZ AC

A and B are what tells the two sets apart in the sample names (asked for when left out):
either what the name starts with ('SZ_...', 'AC_...') or a part of it between underscores,
with or without a number ('..._KO2_...', '..._WT2_...':  compare_ab.py WT KO). Samples are
paired by the antibody in their names ('SZ_homer1_rabbit_...' with 'AC_Homer1_Rabbit_...');
the host species counts when the name gives one, spelled out or as a suffix ('GluA3ms',
'GluA1Rb'). Single tiles ('_F0') are left out, as everywhere else. Samples with 'control' in the name are not compared:
they give the floor, the puncta an image shows with no primary antibody at all.

Only the antibody channel is used, so this also works on images with no pan stain. In each
sample a few planes spread through the middle of the stack are read at full resolution (a
single-plane image gives its one plane), and each camera-frame-sized tile of a plane is one
field of view. Per field:
  puncta density   antibody puncta per 1000 µm² as imaged. A punctum is PUNCTUM_PX or more
                   connected pixels of the smoothed channel SIGMAS noise-widths above the
                   field's median: the rule synapse_exf.py uses for antibody-positive
  punctum signal   median over the field's puncta of (brightest pixel - field median), in
                   camera counts, and the same in noise-widths (signal-to-noise)
  labelled area    share of the field's pixels that belong to a punctum
  background, noise  the field's median and its noise-width (1.4826 x MAD), in counts

B is then set against A per antibody: the ratio B / A of the field means with a bootstrap
95% interval (fields resampled within each set), and a Mann-Whitney test. "Decreased" is
said only when the whole interval is below 1.

WHAT THIS CANNOT SEE: laser power and exposure are not in these files. If the two sets were
imaged with different settings, a difference in signal or in puncta counted is not a
difference in labelling. The settings the files do record are listed side by side.
"""

import csv
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

import ab_efficiency as eff
import convert_to_zarr as core
import roi_log as roi
import synapse_exf as syn

SIGMAS = 6.0             # a punctum is this many noise-widths above the field's median
PUNCTUM_PX = 3           # ...over at least this many connected pixels
SMOOTH_PX = 1.0          # Gaussian smoothing before puncta are looked for
PLANES = 8               # planes read per stack, spread evenly
MIDDLE = 0.8             # ...through this middle share of the stack (the ends may lie outside the gel)
FIELD_PX = 2048          # a field of view: one camera frame
MIN_FIELD_FILL = 0.5     # a tile needs this share of real pixels (not stitching padding) to count
BOOTSTRAPS = 2000
HOSTS = ("chicken", "rabbit", "mouse", "guinea_pig", "guinea pig", "goat", "rat", "donkey")
HOST_SUFFIX = {"ms": "mouse", "rb": "rabbit", "gp": "guinea pig", "ck": "chicken", "ch": "chicken", "gt": "goat"}
COLOUR_A, COLOUR_B = "#4DA3FF", "#FF9900"     # first set blue, second set orange
METRICS = [   # (key, column title, plotted on a log axis)
    ("density", "Puncta / 1000 µm² (as imaged)", True),
    ("snr", "Punctum Signal (noise-widths)", False),
    ("signal", "Punctum Signal (counts above background)", False),
    ("area", "Labelled Area (%)", True),
]


def antibody_key(name):
    """'Homer1 (rabbit)' from a sample name; 'control (rabbit)' for a control; None when no antibody is named."""
    low = name.lower()
    host = next((h.replace("_", " ") for h in HOSTS if h in low), "")
    if not host:       # 'GluA3ms': the antibody's name with the host tacked on
        for token in low.split("_"):
            for known in syn.ANTIBODIES:
                if token.startswith(known) and token[len(known):] in HOST_SUFFIX:
                    host = HOST_SUFFIX[token[len(known):]]
    if "control" in low:
        return f"control ({host})" if host else "control"
    found = syn.antibodies_in(name)
    if not found:
        return None
    return found[0][0] + (f" ({host})" if host else "")


def belongs(name, text):
    """Is this sample in the set `text`? The name starts with it, or one part of the name
    between underscores is it, alone or with a number and a letter ('KO', 'KO2', 'KO1b')."""
    if re.search(r"_F\d+$", name) or any(word in name.lower() for word in syn.NEVER):   # a single tile, or left out of all testing
        return False
    return name.lower().startswith(text.lower() + "_") or any(
        re.fullmatch(re.escape(text.lower()) + r"\d*[a-z]?", token) for token in name.lower().split("_"))


def antibody_channel(channels):
    """The antibody's channel: the longest laser that is not a nuclear stain (637 in these sets)."""
    usable = [c for c in channels if c.get("laser") and c["laser"] >= core.NUCLEAR_BELOW_NM]
    return max(usable, key=lambda c: c["laser"])["index"] if usable else 0


def measure_field(tile):
    """The numbers of one field of view (a 2D array of camera counts), or None if it is mostly padding."""
    from scipy import ndimage
    from skimage import measure

    valid = tile > 0
    if valid.mean() < MIN_FIELD_FILL:
        return None
    smooth = ndimage.gaussian_filter(tile.astype(np.float32), SMOOTH_PX)
    middle = float(np.median(smooth[valid]))
    noise = 1.4826 * float(np.median(np.abs(smooth[valid] - middle))) or 1.0
    labels = measure.label((smooth > middle + SIGMAS * noise) & valid)
    peaks, area = [], 0
    for r in measure.regionprops(labels, intensity_image=smooth):
        if r.area >= PUNCTUM_PX:
            peaks.append(r.intensity_max - middle)
            area += r.area
    return {"n": len(peaks), "pixels": int(valid.sum()), "area": 100 * area / int(valid.sum()),
            "signal": float(np.median(peaks)) if peaks else None, "snr": float(np.median(peaks)) / noise if peaks else None,
            "background": middle, "noise": noise}


def measure_sample(name, zarr_dir, out_root):
    """Every field of one sample. Returns a dict with the fields and what the file says about itself."""
    series = zarr_dir / "0"
    info = core.read_series(series)
    images, _, _, original = core.read_ome_xml(zarr_dir)
    channels = core.describe_channels(images[0]["channels"] if images else [], info["omero"], info["shape"].get("c", 1))
    c = antibody_channel(channels)
    raw = eff.open_level(series, info, 0)[c]                       # lazy (z, y, x)
    z_um, _, um = eff.voxel_um(info, raw.shape)
    n_z, ny, nx = raw.shape
    if n_z >= roi.STACK_MIN_PLANES:
        first, last = int(round(n_z * (1 - MIDDLE) / 2)), int(round(n_z * (1 + MIDDLE) / 2)) - 1
        planes = sorted({int(round(v)) for v in np.linspace(first, last, min(PLANES, last - first + 1))})
    else:
        planes = list(range(n_z))
    fields = []
    for z in planes:
        plane = raw[z].compute()
        if not (plane > 0).any():
            continue
        for y in range(0, ny, FIELD_PX):
            for x in range(0, nx, FIELD_PX):
                got = measure_field(plane[y:y + FIELD_PX, x:x + FIELD_PX])
                if got:
                    got.update(z=z, y=y, x=x, density=got["n"] / (got["pixels"] * um * um) * 1000)
                    fields.append(got)
    date, _ = core.find_acquisition_date(images[0]["date"] if images else "", original, [zarr_dir.name], zarr_dir / ".zattrs")
    return {
        "name": name, "fields": fields, "um": um, "z_um": z_um, "planes": planes, "channel": channels[c]["label"],
        "kind": roi.classify(info), "size": f"{nx} × {ny} × {n_z}", "date": str(date)[:10] if date else "",
        "settings": {"Channel preset": (images[0]["channels"][c].get("Name") if images else "") or "",
                     "Objective (LensPower)": original.get("LensPower", ""), "Pinhole (LSMPinhole)": original.get("LSMPinhole", ""),
                     "Binning": f"{original.get('BinningX', '')} × {original.get('BinningY', '')}",
                     "Pixel (µm)": f"{um:.4f}", "Plane spacing (µm)": f"{z_um:.2f}" if z_um else "–"},
    }


def values(samples, key):
    return np.asarray([f[key] for s in samples for f in s["fields"] if f[key] is not None], float)


def compare(a, b, rng):
    """B against A for one metric: (mean A, sd A, n A, mean B, sd B, n B, ratio, low, high, p)."""
    from scipy import stats

    if not len(a) or not len(b):
        return None
    ratio = b.mean() / a.mean() if a.mean() else float("nan")
    draws = [rng.choice(b, len(b)).mean() / max(rng.choice(a, len(a)).mean(), 1e-12) for _ in range(BOOTSTRAPS)]
    low, high = np.percentile(draws, [2.5, 97.5])
    p = stats.mannwhitneyu(a, b, alternative="two-sided").pvalue if len(a) > 1 and len(b) > 1 else float("nan")
    return a.mean(), a.std(ddof=1) if len(a) > 1 else 0.0, len(a), b.mean(), b.std(ddof=1) if len(b) > 1 else 0.0, len(b), ratio, low, high, p


def verdict(result):
    if result is None:
        return "not measured"
    low, high = result[7], result[8]
    return "DECREASED" if high < 1 else "increased" if low > 1 else "no change detected"


def crop_of(sample, out_root):
    """A 400-pixel crop round the field of the sample with the most puncta, for the side-by-side picture."""
    best = max(sample["fields"], key=lambda f: f["n"])
    info = core.read_series(out_root / f"{sample['name']}.zarr" / "0")
    images = core.read_ome_xml(out_root / f"{sample['name']}.zarr")[0]
    channels = core.describe_channels(images[0]["channels"] if images else [], info["omero"], info["shape"].get("c", 1))
    raw = eff.open_level(out_root / f"{sample['name']}.zarr" / "0", info, 0)[antibody_channel(channels)]
    y, x = best["y"] + 600, best["x"] + 600
    return raw[best["z"], y:y + 400, x:x + 400].compute().astype(np.float32) - best["background"], best


def run(a_text, b_text):
    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    if not out_root.is_dir():
        sys.exit(f"Cannot reach the zarr folder {out_root}")
    base = Path(core.DEFAULT_SHOTS).parent / "Zarr Comparisons" / f"{a_text} vs {b_text}"
    base.mkdir(parents=True, exist_ok=True)
    cache_dir = base / "_measured"
    cache_dir.mkdir(exist_ok=True)
    groups = {a_text: {}, b_text: {}}
    for name, zarr_dir in roi.samples(out_root):
        for text in groups:
            if belongs(name, text):
                key = antibody_key(name)
                if key:
                    groups[text].setdefault(key, []).append((name, zarr_dir))
    shared = sorted(k for k in groups[a_text] if k in groups[b_text] and not k.startswith("control"))
    print(f"Comparing {b_text} against {a_text}\nResults: {base}\n")
    for key in sorted(set(groups[a_text]) | set(groups[b_text])):
        print(f"   {key:22s} {a_text}: {len(groups[a_text].get(key, []))} sample(s)   {b_text}: {len(groups[b_text].get(key, []))} sample(s)"
              + ("" if key in shared or key.startswith("control") else "   (in one set only: not compared)"))
    if not shared:
        sys.exit("\nNo antibody is in both sets.")
    print()

    # measure (or reload) every sample that is needed
    measured = {}
    wanted = [(t, k, n, d) for t in groups for k in groups[t] if k in shared or k.startswith("control") for n, d in groups[t][k]]
    for i, (text, key, name, zarr_dir) in enumerate(wanted, start=1):
        cache = cache_dir / f"{name}.json"
        settings = {"sigmas": SIGMAS, "px": PUNCTUM_PX, "planes": PLANES, "middle": MIDDLE, "field": FIELD_PX}
        if cache.is_file():
            kept = json.loads(cache.read_text(encoding="utf-8"))
            if kept.get("how") == settings:
                measured[name] = kept
                continue
        print(f"[{i}/{len(wanted)}] {name}")
        got = measure_sample(name, zarr_dir, out_root)
        got["how"] = settings
        cache.write_text(json.dumps(got), encoding="utf-8")
        measured[name] = got
        d = values([got], "density")
        print(f"   {len(got['fields'])} field(s) on plane(s) {got['planes']}: {d.mean():.2f} puncta / 1000 µm²")
    of = lambda text, key: [measured[n] for n, _ in groups[text].get(key, [])]

    floor = values([s for k in groups[a_text] if k.startswith("control") for s in of(a_text, k)]
                   + [s for k in groups[b_text] if k.startswith("control") for s in of(b_text, k)], "density")
    rng = np.random.default_rng(0)
    rows, results = [], {}
    for key in shared:
        row = {"Antibody": key, f"{a_text} Samples": len(of(a_text, key)), f"{b_text} Samples": len(of(b_text, key)),
               f"{a_text} Date": ", ".join(sorted({s["date"] for s in of(a_text, key)})),
               f"{b_text} Date": ", ".join(sorted({s["date"] for s in of(b_text, key)})),
               "Control Floor (puncta / 1000 µm²)": round(float(floor.mean()), 3) if len(floor) else None}
        for metric, title, _ in METRICS:
            r = results[(key, metric)] = compare(values(of(a_text, key), metric), values(of(b_text, key), metric), rng)
            if r:
                row[f"{title}: {a_text}"] = f"{r[0]:.3g} ± {r[1]:.2g} (n = {r[2]})"
                row[f"{title}: {b_text}"] = f"{r[3]:.3g} ± {r[4]:.2g} (n = {r[5]})"
                row[f"{title}: {b_text} / {a_text}"] = f"{r[6]:.2f} ({r[7]:.2f}–{r[8]:.2f})"
                row[f"{title}: p"] = f"{r[9]:.2g}"
                row[f"{title}: Verdict"] = verdict(r)
            else:   # one set has no puncta at all to take this from: give the other's value on its own
                for text in (a_text, b_text):
                    v = values(of(text, key), metric)
                    row[f"{title}: {text}"] = f"{v.mean():.3g} ± {v.std(ddof=1) if len(v) > 1 else 0:.2g} (n = {len(v)})" if len(v) else "no puncta"
                row[f"{title}: Verdict"] = "not comparable: no puncta in one set"
        dens = results[(key, "density")]
        if dens and len(floor):
            row[f"{b_text} At Control Level"] = "YES" if dens[3] <= 2 * floor.mean() else "no"
        rows.append(row)
        print(f"{key}: puncta density {b_text}/{a_text} = {row.get(f'{METRICS[0][1]}: {b_text} / {a_text}')}  "
              f"-> {row.get(f'{METRICS[0][1]}: Verdict')};  signal-to-noise -> {row.get(f'{METRICS[1][1]}: Verdict')}")
    columns = list(dict.fromkeys(k for r in rows for k in r))
    with open(base / "Comparison.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, columns)
        w.writeheader()
        w.writerows(rows)
    with open(base / "Per Sample.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Set", "Antibody", "Sample", "Date", "Type", "Size (voxels)", "Channel", "Planes Read", "Fields"]
                   + [m[1] for m in METRICS] + ["Background (counts)", "Noise (counts)"] + list(next(iter(measured.values()))["settings"]))
        for text in groups:
            for key in sorted(groups[text]):
                for s in of(text, key) if key in shared or key.startswith("control") else []:
                    mean = lambda k: (lambda v: "" if not len(v) else round(float(v.mean()), 3))(values([s], k))
                    w.writerow([text, key, s["name"], s["date"], s["kind"], s["size"], s["channel"], " ".join(map(str, s["planes"])),
                                len(s["fields"])] + [mean(m[0]) for m in METRICS] + [mean("background"), mean("noise")]
                               + list(s["settings"].values()))

    # ---- picture 1: every field as a dot, the two sets side by side for each antibody
    fig, axes = eff.figure(2, 2, figsize=(max(11, 2.2 * len(shared) + 5), 9))
    for ax, (metric, title, log) in zip(axes.ravel(), METRICS):
        for i, key in enumerate(shared):
            for text, colour, shift in ((a_text, COLOUR_A, -0.18), (b_text, COLOUR_B, 0.18)):
                v = values(of(text, key), metric)
                if not len(v):
                    continue
                if log:
                    v = np.maximum(v, 1e-3)
                jitter = np.random.default_rng(1).uniform(-0.1, 0.1, len(v))
                ax.scatter(i + shift + jitter, v, s=12, color=colour, alpha=0.6, linewidths=0,
                           label=text if i == 0 else None)
                ax.plot([i + shift - 0.14, i + shift + 0.14], [v.mean()] * 2, color="white", lw=2)
        if metric == "density" and len(floor):
            ax.axhline(max(floor.mean(), 1e-3), color=eff.MUTED, lw=1.2, ls="--", label="Controls (no primary)")
        if log:
            ax.set_yscale("log")
        ax.set_xticks(range(len(shared)))
        ax.set_xticklabels(shared, rotation=20, ha="right", color=eff.INK, fontsize=9)
        ax.set_title(title + ("  (log scale)" if log else ""), color=eff.INK, fontsize=10, loc="left")
        ax.grid(color="#2A2A2A", lw=0.6, axis="y")
        ax.legend(frameon=False, labelcolor=eff.INK, fontsize=8)
    fig.suptitle(f"Antibody Labelling: {b_text} against {a_text}.  Each dot is one field of view; white bar = mean",
                 color=eff.INK, fontsize=11)
    fig.tight_layout()
    eff.save(fig, base / f"{a_text} vs {b_text} - Fields.png")

    # ---- picture 2: the same brightness scale for both sets, the busiest field of each
    fig, axes = eff.figure(2, len(shared), figsize=(3.3 * len(shared), 7.2), squeeze=False)
    for i, key in enumerate(shared):
        crops = []
        for text in (a_text, b_text):
            busiest = max(of(text, key), key=lambda s: max(f["n"] for f in s["fields"]))
            crops.append((text, busiest, *crop_of(busiest, out_root)))
        top = max(8 * max(c[3]["noise"] for c in crops), np.percentile(crops[0][2], 99.9))   # one scale for the pair
        for ax, (text, s, img, field) in zip(axes[:, i], crops):
            ax.imshow(np.clip(img / top, 0, 1), cmap="gray", interpolation="nearest")
            bar = 10 / s["um"]
            ax.plot([8, 8 + bar], [img.shape[0] - 10] * 2, color="white", lw=3, solid_capstyle="butt")
            ax.text(8, img.shape[0] - 14, "10 µm", color="white", fontsize=8, va="bottom")
            ax.set_title(f"{text}  {key}\n{s['date']}, slice {field['z']}", color=COLOUR_A if text == a_text else COLOUR_B, fontsize=9)
            ax.set_xticks([])
            ax.set_yticks([])
    fig.suptitle(f"Busiest Field of Each: Antibody Channel, Background Removed, the Same Brightness Scale Within Each Column",
                 color=eff.INK, fontsize=11)
    fig.tight_layout()
    eff.save(fig, base / f"{a_text} vs {b_text} - Examples.png")

    # ---- what the files say about how each set was imaged
    lines = [f"How {b_text} and {a_text} were imaged, as far as the files record it", ""]
    keys = list(next(iter(measured.values()))["settings"])
    for text in groups:
        used = [s for k in groups[text] if k in shared for s in of(text, k)]
        lines.append(f"{text}: " + "; ".join(f"{k} = {', '.join(sorted({str(s['settings'][k]) for s in used}))}" for k in keys)
                     + f"; dates {', '.join(sorted({s['date'] for s in used}))}")
    lines += ["", "Laser power and exposure time are NOT recorded in these files. If they differed between the two sets,",
              "a difference in punctum signal or in the number of puncta counted may be the imaging and not the labelling.",
              "Check the acquisition protocols before reading the comparison as a change in antibody efficiency."]
    (base / "Imaging Settings.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines[2:4]) + f"\n\n{base}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    first = args[0] if args else roi.ask("First set: what do its sample names start with? (Enter = SZ): ", "SZ")
    second = args[1] if len(args) > 1 else roi.ask("Second set, compared against the first? (Enter = AC): ", "AC")
    sys.exit(run(first, second))
