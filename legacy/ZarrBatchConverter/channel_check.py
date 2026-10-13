"""
Check every channel of every sample before trusting an antibody's numbers: does the channel
hold signal at all, and does one channel show up in another (spectral bleed-through)?

  channel_check.py [text]     the samples whose names contain the text (all when left out)

It works on channels, not on antibody names, because which antibody is on which channel
changes while secondaries are being tested.

Per channel (the middle camera frame of up to three planes through the stack):
  spots per 1000 µm² and how bright a typical spot is, in noise-widths. Under DARK_BELOW
  spots per 1000 µm² the channel is DARK: nothing was detected in it (no label, or no
  excitation); under SPARSE_BELOW it is sparse. The pan stain is not made of spots and is
  judged by its range in camera counts instead.

Per pair of channels, source -> target: at every bright spot of the source, how much signal
does the target show at the same place (within 2 pixels, so a small colour shift between
channels does not hide it)?
  present    share of the source's spots at which the target is above its own noise
  ratio      target / source at those spots (median), and how much it varies (robust CV)
  r          correlation of target with source across the spots
Bleed-through is a fixed fraction of the source arriving in the target, so it shows as the
target present at nearly every source spot, in a constant ratio, tightly correlated. Two
antibodies on the same synapses also overlap, but the ratio varies from spot to spot and
many spots have only one of the two. The verdicts use that difference; they are a screen.
Only a single-label control (one antibody, every channel imaged) proves bleed-through.
"""

import csv
import json
import sys
from pathlib import Path

import numpy as np

import ab_efficiency as eff
import convert_to_zarr as core
import roi_log as roi

SIGMAS = 6.0             # a spot is this many noise-widths above the frame's median
STRONG = 10.0            # only source spots this strong are used for the pair test
PRESENT = 4.0            # the target is "present" at a spot when this many of its own noise-widths up
REACH_PX = 2             # the target is looked for this far round the source's spot
PAN_PERCENTILE = 99.5    # the pan stain is not made of spots: its brightest structures are used
FRAME_PX = 2048
MIN_SPOTS = 20
DARK_BELOW = 0.1         # spots per 1000 µm² (as imaged) under which a channel is called dark
SPARSE_BELOW = 1.0       # ...and under which it is called sparse
PAN_RANGE = 50.0         # a pan stain spans at least this many camera counts (1st to 99.9th percentile)
SKIP = ("tetraspec", "pff")   # bead images, and the PFF samples, which are left out of all testing


def frame_numbers(img):
    """(smoothed image, median, noise-width) of one channel of one frame, or None if it is padding."""
    from scipy import ndimage

    valid = img > 0
    if valid.mean() < 0.5:
        return None
    smooth = ndimage.gaussian_filter(img.astype(np.float32), 1.0)
    middle = float(np.median(smooth[valid]))
    return smooth, middle, 1.4826 * float(np.median(np.abs(smooth[valid] - middle))) or 1.0


def check_sample(name, zarr_dir, out_root):
    """(channel rows, pair rows) for one sample."""
    from scipy import ndimage
    from skimage import measure

    series = zarr_dir / "0"
    info = core.read_series(series)
    # the pan stain, the antibody channels and their colours, as every other tool decides them
    channels, pan, antibodies, _ = eff.channel_roles(name, zarr_dir, out_root, info)
    import antibody_channels
    named = antibody_channels.for_sample(name, channels, antibodies)
    raw = eff.open_level(series, info, 0)                              # lazy (c, z, y, x)
    um = eff.voxel_um(info, raw.shape[1:])[2]
    n_c, n_z, ny, nx = raw.shape
    planes = sorted({int(round(n_z * f)) for f in (0.3, 0.5, 0.7)}) if n_z >= roi.STACK_MIN_PLANES else list(range(n_z))
    y0, x0 = max(0, (ny - FRAME_PX) // 2), max(0, (nx - FRAME_PX) // 2)
    spots = {c: {"n": 0, "area_um2": 0.0, "peak": [], "range": []} for c in range(n_c)}
    pairs = {(s, t): {"s": [], "t": []} for s in range(n_c) for t in range(n_c) if s != t}
    for z in planes:
        tile = raw[:, z, y0:y0 + FRAME_PX, x0:x0 + FRAME_PX].compute()
        got = [frame_numbers(tile[c]) for c in range(n_c)]
        if any(g is None for g in got):
            continue
        reach = [ndimage.maximum_filter(g[0], 2 * REACH_PX + 1) for g in got]
        for s in range(n_c):
            smooth, middle, noise = got[s]
            level = max(middle + SIGMAS * noise, np.percentile(smooth, PAN_PERCENTILE)) if s == pan else middle + SIGMAS * noise
            labels = measure.label(smooth > level)
            regions = [r for r in measure.regionprops(labels, intensity_image=smooth) if r.area >= 3]
            spots[s]["n"] += len(regions)
            spots[s]["area_um2"] += float((tile[s] > 0).sum()) * um * um
            spots[s]["peak"] += [float((r.intensity_max - middle) / noise) for r in regions]
            spots[s]["range"].append(float(np.subtract(*np.percentile(smooth, [99.9, 1]))))
            strong = [r for r in regions if r.intensity_max - middle >= STRONG * noise][:4000]
            for t in range(n_c):
                if t == s:
                    continue
                for r in strong:
                    rr, cc = np.unravel_index(np.argmax(np.where(labels[r.slice] == r.label, smooth[r.slice], -np.inf)), r.image.shape)
                    at = (r.slice[0].start + rr, r.slice[1].start + cc)        # the spot's brightest pixel
                    pairs[(s, t)]["s"].append(r.intensity_max - middle)
                    pairs[(s, t)]["t"].append((reach[t][at] - got[t][1]) / got[t][2])   # in the target's noise-widths
        noise_of = {c: got[c][2] for c in range(n_c)}
    label = lambda c: channels[c]["label"] + (" (pan)" if c == pan else "")
    channel_rows = []
    for c in range(n_c):
        if not spots[c]["range"]:
            continue
        density = spots[c]["n"] / spots[c]["area_um2"] * 1000 if spots[c]["area_um2"] else 0
        span = float(np.mean(spots[c]["range"]))
        if c == pan:
            status = "signal" if span >= PAN_RANGE else "weak pan stain"
        else:
            status = "DARK: nothing detected" if density < DARK_BELOW else "sparse" if density < SPARSE_BELOW else "signal"
        role = "pan" if c == pan else "antibody" if c in antibodies else "nuclear stain"
        colour = (255, 255, 255) if c == pan else channels[c]["rgb"]
        channel_rows.append({"Sample": name, "Channel": label(c), "Role": role, "Laser (nm)": channels[c].get("laser") or "",
                             "Antibody": named.get(c, ("", ""))[0], "Antibody From": named.get(c, ("", ""))[1],
                             "Colour": "#{:02x}{:02x}{:02x}".format(*colour),
                             "Spots / 1000 µm²": "" if c == pan else round(density, 2),
                             "Typical Spot (noise-widths)": round(float(np.median(spots[c]["peak"])), 1) if spots[c]["peak"] and c != pan else "",
                             "Range (counts)": round(span), "Status": status})
    pair_rows = []
    for (s, t), v in pairs.items():
        src, tgt = np.asarray(v["s"]), np.asarray(v["t"])
        row = {"Sample": name, "Source": label(s), "Target": label(t), "Source Spots": len(src)}
        if len(src) < MIN_SPOTS:
            row["Verdict"] = "too few source spots to test"
        else:
            here = tgt >= PRESENT
            present = float(here.mean())
            ratio = (tgt[here] * noise_of[t]) / src[here] if here.any() else np.array([])      # counts per count
            middle = float(np.median(ratio)) if len(ratio) else 0.0
            cv = float(1.4826 * np.median(np.abs(ratio - middle)) / middle) if len(ratio) > 5 and middle > 0 else None
            r = float(np.corrcoef(src, tgt)[0, 1]) if src.std() > 0 and tgt.std() > 0 else 0.0
            row.update({"Target Present At (%)": round(100 * present, 1), "Target / Source (%)": round(100 * middle, 1),
                        "Ratio CV": None if cv is None else round(cv, 2), "r": round(r, 2)})
            if present >= 0.8 and r >= 0.7 and cv is not None and cv <= 0.35:
                row["Verdict"] = "BLEED-THROUGH LIKELY"
            elif present >= 0.5 and r >= 0.5:
                row["Verdict"] = "possible bleed-through, or the two label the same places"
            elif present >= 0.2:
                row["Verdict"] = "partial overlap: the same places in part, ratio varies"
            else:
                row["Verdict"] = "none"
        pair_rows.append(row)
    return channel_rows, pair_rows


def run(text):
    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    base = Path(core.DEFAULT_SHOTS).parent / "Zarr Comparisons"
    base.mkdir(parents=True, exist_ok=True)
    all_channels, all_pairs = [], []
    names = [(n, d) for n, d in roi.samples(out_root) if text.lower() in n.lower() and not any(s in n.lower() for s in SKIP)]
    for i, (name, zarr_dir) in enumerate(names, start=1):
        try:
            c_rows, p_rows = check_sample(name, zarr_dir, out_root)
        except Exception as e:
            print(f"[{i}/{len(names)}] {name}: skipped ({e})")
            continue
        all_channels += c_rows
        all_pairs += p_rows
        flags = [f"{r['Channel']} {r['Status']}" for r in c_rows if r["Status"] != "signal"] + \
                [f"{r['Source']} -> {r['Target']}: {r['Verdict']}" for r in p_rows if r["Verdict"].startswith(("BLEED", "possible"))]
        print(f"[{i}/{len(names)}] {name}: " + ("; ".join(flags) if flags else "ok"))
    for file, rows in (("Channel Check - Channels.csv", all_channels), ("Channel Check - Pairs.csv", all_pairs)):
        heads = list(dict.fromkeys(k for r in rows for k in r))
        with open(base / file, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, heads)
            w.writeheader()
            w.writerows(rows)
    print(f"\n{base}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "")
