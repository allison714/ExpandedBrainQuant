"""
Antibody labelling in numbers, for every antibody channel of every sample, in biological units.

  ab_metrics.py [text]      the samples whose names contain the text (all when left out)

It works on channels, not antibody names: which antibody sits on which channel changes while
secondaries are being tested. Every channel that is not the pan stain or a nuclear stain is
treated as an antibody channel.

Per antibody channel
  puncta density       puncta per 100 µm² of tissue (area as imaged / ExF²)
  nearest neighbour    distance from each punctum to the nearest other punctum of the same
                       channel, in nm of tissue; and Clark-Evans R = mean distance / the mean
                       expected if the puncta fell at random (R < 1 clustered, R > 1 spaced out)
  synaptic enrichment  share of puncta within ON_SYNAPSE_NM of a synapse found by synapse_exf.py,
                       divided by the share expected by chance; and the distance from each
                       punctum to the nearest synapse. Needs a pan stain.
  background           level and noise in camera counts, and how uneven the background is across
                       the field (spread of 128-pixel block medians, in noise-widths)
  large structures     long continuous stretches of antibody signal: vessel-like labelling
  against expectation  labelled sites per 100 µm² of tissue set against the number of synapse
                       profiles a section of this thickness should contain (see EXPECTED below).
                       A site is one or more puncta within SITE_NM of each other, so a synapse
                       that shows two or three nanoclusters is counted once.
Per pair of antibody channels (AB1 - AB2)
  distance from each punctum of one to the nearest punctum of the other, in nm of tissue; the
  share that have a partner within PAIR_NM, and the share expected by chance.

"nm of tissue" = distance as imaged / ExF. The ExF is the sample's own from the Synapse Log when
it rests on confirmed synapses; otherwise the mean of the samples imaged the same day that have
one; otherwise ASSUMED_EXF. The 'ExF Source' column says which. Distances are in the image
plane only (planes are about 10 µm apart), and channels are not corrected for colour shift,
which is about a pixel (under 10 nm of tissue).

A dataset is flagged when a channel's background level, noise or unevenness is far from the
other samples of the same day and channel (robust z above FLAG_Z), or when it shows large
vessel-like structures. Flags are for looking at the image, not verdicts.
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

SIGMAS = 6.0             # a punctum is this many noise-widths above its frame's median
PUNCTUM_PX = 3
SMOOTH_PX = 1.0
PLANES = 6               # planes read per stack
FRAME_PX = 2048
PAIR_NM = 150.0          # AB1 and AB2 puncta this close (nm of tissue) count as a pair
ON_SYNAPSE_NM = 165.0    # a punctum this close to a synapse's centre is on it (two DP-PSD distances)
ASSUMED_EXF = 20.0
COARSE = 8               # large structures are looked for on the image shrunk this many times
STRUCTURE_SIGMAS = 5.0   # ...as regions this far above the coarse noise
STRUCTURE_UM = 60.0      # ...at least this long, as imaged (about 3 µm of tissue)
STRUCTURE_MIN_WIDTH_UM = 10.0   # ...and this wide, and clear of the frame's edge
VESSEL_ELONGATION = 3.0  # ...and this many times longer than wide to be called vessel-like
FLAG_Z = 3.5
HIST_NM = np.arange(0, 2001, 50.0)
SITE_NM = 200.0          # puncta this close together (nm of tissue) are one labelled site
# What a section should contain. In a slab of thickness t, objects of mean width D and number
# density N per volume show N x (t + D) profiles per area (Abercrombie's relation). Here t is the
# optical section and D the width of a PSD. Each is given as (low, central, high): set them for
# the brain region imaged. They are textbook-range values, not measurements of these samples.
SYNAPSES_PER_UM3 = (0.7, 1.0, 1.5)        # synapses per µm³ of neuropil (mouse cortex to hippocampus)
PSD_WIDTH_NM = (200.0, 300.0, 400.0)      # mean width of a PSD
AXIAL_FWHM_NM_AS_IMAGED = 840.0           # optical section of the 40x/1.15 objective (M'Saad, Cairns et al. 2025, Ext. Data Fig. 5)


def expected_profiles(exf):
    """(low, central, high) synapse profiles per 100 µm² of tissue in one optical section."""
    section_um = AXIAL_FWHM_NM_AS_IMAGED / exf / 1000
    return tuple(n * (section_um + d / 1000) * 100 for n, d in zip(SYNAPSES_PER_UM3, PSD_WIDTH_NM))


def find_puncta(img):
    """Puncta of one channel of one frame. Returns (rows, cols, median, noise) or None for padding."""
    from scipy import ndimage
    from skimage import measure

    valid = img > 0
    if valid.mean() < 0.5:
        return None
    smooth = ndimage.gaussian_filter(img.astype(np.float32), SMOOTH_PX)
    middle = float(np.median(smooth[valid]))
    noise = 1.4826 * float(np.median(np.abs(smooth[valid] - middle))) or 1.0
    labels = measure.label((smooth > middle + SIGMAS * noise) & valid)
    centres = np.array([r.centroid for r in measure.regionprops(labels) if r.area >= PUNCTUM_PX]).reshape(-1, 2)
    # how uneven the background is: block medians, in noise-widths (puncta barely move a block's median)
    h, w = (img.shape[0] // 128) * 128, (img.shape[1] // 128) * 128
    blocks = np.median(smooth[:h, :w].reshape(h // 128, 128, w // 128, 128).transpose(0, 2, 1, 3).reshape(-1, 128 * 128), axis=1)
    return centres[:, 0], centres[:, 1], middle, noise, float(np.std(blocks) / noise), int(valid.sum())


def large_structures(img, um):
    """(number, share of the frame, number that are vessel-like) of long continuous stretches of signal."""
    from scipy import ndimage
    from skimage import measure

    h, w = (img.shape[0] // COARSE) * COARSE, (img.shape[1] // COARSE) * COARSE
    small = img[:h, :w].astype(np.float32).reshape(h // COARSE, COARSE, w // COARSE, COARSE).mean(axis=(1, 3))
    small = ndimage.gaussian_filter(small, 1.5)
    flat = small - ndimage.median_filter(small, 61)              # slow shading is not a structure
    noise = 1.4826 * float(np.median(np.abs(flat - np.median(flat)))) or 1.0
    found = vessel = 0
    area = 0
    edge = 8                                                     # coarse pixels: the camera's edge rows are brighter
    for r in measure.regionprops(measure.label(flat > STRUCTURE_SIGMAS * noise)):
        y0, x0, y1, x1 = r.bbox
        at_edge = y0 < edge or x0 < edge or y1 > small.shape[0] - edge or x1 > small.shape[1] - edge
        # a strip a few pixels wide is a camera row or a stitching seam, not tissue
        if r.major_axis_length * COARSE * um >= STRUCTURE_UM and r.minor_axis_length * COARSE * um >= STRUCTURE_MIN_WIDTH_UM and not at_edge:
            found += 1
            area += r.area
            vessel += r.major_axis_length >= VESSEL_ELONGATION * max(r.minor_axis_length, 1)
    return found, 100 * area / small.size, vessel


def measure_sample(name, zarr_dir, out_root, synapse_dir, exf):
    series = zarr_dir / "0"
    info = core.read_series(series)
    images = core.read_ome_xml(zarr_dir)[0]
    channels = core.describe_channels(images[0]["channels"] if images else [], info["omero"], info["shape"].get("c", 1))
    cache = out_root / core.STATE_DIR / f"{name}_s0.stats.json"
    try:
        stats = json.loads(cache.read_text(encoding="utf-8"))["stats"]
    except (OSError, ValueError, KeyError):
        stats = []
    pan = core.pick_pan(name, channels, stats)[0] if stats else None
    abs_ = [c["index"] for c in channels if c["index"] != pan and not (c.get("laser") and c["laser"] < core.NUCLEAR_BELOW_NM)]
    if not abs_:
        raise ValueError("no antibody channel")
    raw = eff.open_level(series, info, 0)
    um = eff.voxel_um(info, raw.shape[1:])[2]
    n_c, n_z, ny, nx = raw.shape
    # synapses found earlier, plane by plane (only where there is a pan stain)
    synapses = {}
    found = synapse_dir / name / f"{name}_synapses.csv"
    if found.is_file():
        with open(found, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                synapses.setdefault(int(r["Slice"]), []).append((float(r["Y (px)"]), float(r["X (px)"])))
    if synapses:
        zs = sorted(synapses)
        planes = [zs[int(round(i))] for i in np.linspace(0, len(zs) - 1, min(PLANES, len(zs)))]
    elif n_z >= roi.STACK_MIN_PLANES:
        planes = sorted({int(round(v)) for v in np.linspace(n_z * 0.1, n_z * 0.9 - 1, PLANES)})
    else:
        planes = list(range(n_z))
    per = {c: {"n": 0, "px": 0, "nn": [], "expected_nn": [], "bg": [], "noise": [], "uneven": [], "to_syn": [], "on": 0,
               "chance": [], "structures": 0, "structure_pct": [], "vessel": 0, "sites": 0} for c in abs_}
    pairs = {(a, b): {"d": [], "chance": []} for a in abs_ for b in abs_ if a != b}
    from scipy import spatial
    for z in sorted(set(planes)):
        plane = raw[:, z].compute()
        points = {c: [] for c in abs_}
        area = 0
        for y in range(0, ny, FRAME_PX):
            for x in range(0, nx, FRAME_PX):
                for c in abs_:
                    got = find_puncta(plane[c, y:y + FRAME_PX, x:x + FRAME_PX])
                    if got is None:
                        continue
                    rows, cols, middle, noise, uneven, pixels = got
                    points[c].append(np.column_stack([rows + y, cols + x]))
                    per[c]["bg"].append(middle)
                    per[c]["noise"].append(noise)
                    per[c]["uneven"].append(uneven)
                    per[c]["px"] += pixels
                    if c == abs_[0]:
                        area += pixels
                    count, share, vessel = large_structures(plane[c, y:y + FRAME_PX, x:x + FRAME_PX], um)
                    per[c]["structures"] += count
                    per[c]["structure_pct"].append(share)
                    per[c]["vessel"] += vessel
        points = {c: np.concatenate(v) if v else np.zeros((0, 2)) for c, v in points.items()}
        trees = {c: spatial.cKDTree(p) for c, p in points.items() if len(p)}
        syn_xy = np.array(synapses.get(z, [])).reshape(-1, 2)
        for c in abs_:
            p = points[c]
            per[c]["n"] += len(p)
            if len(p):
                # labelled sites: puncta chained together within SITE_NM of tissue count once
                from scipy.sparse import coo_matrix
                from scipy.sparse.csgraph import connected_components
                close = np.array(sorted(trees[c].query_pairs(SITE_NM * exf / 1000 / um))).reshape(-1, 2)
                links = coo_matrix((np.ones(len(close)), (close[:, 0], close[:, 1])), shape=(len(p), len(p)))
                per[c]["sites"] += int(connected_components(links, directed=False)[0])
            if len(p) > 1 and area:
                per[c]["nn"].append(trees[c].query(p, k=2)[0][:, 1] * um)
                per[c]["expected_nn"].append(0.5 / math.sqrt(len(p) / (area * um * um)))
            if len(p) and len(syn_xy):
                d = spatial.cKDTree(syn_xy).query(p)[0] * um
                per[c]["to_syn"].append(d)
                per[c]["chance"].append((len(syn_xy), area * um * um, len(p)))
        for (a, b), v in pairs.items():
            if len(points[a]) and b in trees and area:
                v["d"].append(trees[b].query(points[a])[0] * um)
                v["chance"].append((len(points[b]) / (area * um * um), len(points[a])))
    return {"name": name, "um": um, "planes": sorted(set(planes)), "pan": pan, "labels": {c: channels[c]["label"] for c in abs_},
            "colours": {c: channels[c]["rgb"] for c in abs_}, "per": per, "pairs": pairs, "has_synapses": bool(synapses)}


def exf_table(synapse_dir, names):
    """{sample: (ExF, where it comes from)}."""
    from openpyxl import load_workbook

    own = {}
    try:
        rows = load_workbook(synapse_dir / syn.LOG_NAME, read_only=True)["Summary"].iter_rows(values_only=True)
        heads = next(rows)
        for r in rows:
            d = dict(zip(heads, r))
            if d.get("ExF") and not str(d.get("ExF Basis") or "").startswith("all double peaks"):
                own[d["Sample"]] = float(d["ExF"])
    except Exception:
        pass
    day = lambda n: n.split("_")[0]
    out = {}
    for n in names:
        mates = [v for k, v in own.items() if day(k) == day(n)]
        out[n] = (own[n], "its own synapses") if n in own else \
                 (round(float(np.mean(mates)), 1), f"mean of {len(mates)} sample(s) of {day(n)}") if mates else (ASSUMED_EXF, "ASSUMED")
    return out


def run(text):
    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    synapse_dir = Path(core.DEFAULT_SHOTS).parent / "Zarr Synapse ExF"
    base = Path(core.DEFAULT_SHOTS).parent / "Zarr Comparisons" / "AB Metrics"
    (base / "_measured").mkdir(parents=True, exist_ok=True)
    names = [(n, d) for n, d in roi.samples(out_root) if text.lower() in n.lower()
             and not any(w in n.lower() for w in syn.NEVER) and not re.search(r"_F\d+$", n)]
    exf = exf_table(synapse_dir, [n for n, _ in names])
    rows, pair_rows, hists = [], [], {}
    for i, (name, zarr_dir) in enumerate(names, start=1):
        try:
            m = measure_sample(name, zarr_dir, out_root, synapse_dir, exf[name][0])
        except Exception as e:
            print(f"[{i}/{len(names)}] {name}: skipped ({e})")
            continue
        factor, source = exf[name]
        nm = 1000 / factor                                        # µm as imaged -> nm of tissue
        line = []
        for c, v in m["per"].items():
            if not v["bg"]:
                continue
            area_um2 = v["px"] * m["um"] ** 2
            nn = np.concatenate(v["nn"]) if v["nn"] else np.array([])
            row = {"Sample": name, "Channel": m["labels"][c], "ExF Used": factor, "ExF Source": source,
                   "Planes": " ".join(map(str, m["planes"])), "Puncta": v["n"],
                   "Puncta / 100 µm² (tissue)": round(v["n"] / (area_um2 / factor ** 2) * 100, 2) if area_um2 else None,
                   "Puncta / 1000 µm² (as imaged)": round(v["n"] / area_um2 * 1000, 2) if area_um2 else None}
            if area_um2:
                low, mid, high = expected_profiles(factor)
                sites = v["sites"] / (area_um2 / factor ** 2) * 100
                row.update({"Sites / 100 µm² (tissue)": round(sites, 2),
                            "Expected Synapse Profiles / 100 µm²": f"{mid:.0f} ({low:.0f}–{high:.0f})",
                            "Sites / Expected (%)": round(100 * sites / mid),
                            "Sites / Expected, range (%)": f"{100 * sites / high:.0f}–{100 * sites / low:.0f}"})
            if len(nn):
                expected = float(np.average(v["expected_nn"], weights=[len(x) for x in v["nn"]]))
                row.update({"Nearest Neighbour, median (nm)": round(float(np.median(nn)) * nm),
                            "Nearest Neighbour, IQR (nm)": "{:.0f}–{:.0f}".format(*(np.percentile(nn, [25, 75]) * nm)),
                            "Clark–Evans R": round(float(nn.mean()) / expected, 2)})
                hists[(name, m["labels"][c], "nn")] = np.histogram(nn * nm, HIST_NM)[0]
            if v["to_syn"]:
                d = np.concatenate(v["to_syn"]) * nm
                on = float((d <= ON_SYNAPSE_NM).mean())
                reach_um = ON_SYNAPSE_NM / nm
                chance = float(np.average([min(1.0, k * math.pi * reach_um ** 2 / a) for k, a, _ in v["chance"]],
                                          weights=[w for _, _, w in v["chance"]]))
                row.update({"Puncta on Synapses (%)": round(100 * on, 1), "Expected by Chance (%)": round(100 * chance, 2),
                            "Synaptic Enrichment (× chance)": round(on / chance, 1) if chance else None,
                            "To Nearest Synapse, median (nm)": round(float(np.median(d)))})
                hists[(name, m["labels"][c], "syn")] = np.histogram(d, HIST_NM)[0]
            row.update({"Background (counts)": round(float(np.median(v["bg"])), 1), "Noise (counts)": round(float(np.median(v["noise"])), 2),
                        "Background Unevenness (noise-widths)": round(float(np.median(v["uneven"])), 2),
                        "Large Structures": v["structures"], "Vessel-like Structures": int(v["vessel"]),
                        "Frame in Large Structures (%)": round(float(np.mean(v["structure_pct"])), 2)})
            rows.append(row)
            line.append(f"{m['labels'][c]} {row.get('Sites / 100 µm² (tissue)')} sites/100µm² = {row.get('Sites / Expected (%)')}% of expected")
        for (a, b), v in m["pairs"].items():
            if not v["d"]:
                continue
            d = np.concatenate(v["d"]) * nm
            reach_um = PAIR_NM / nm
            chance = float(np.average([1 - math.exp(-rho * math.pi * reach_um ** 2) for rho, _ in v["chance"]], weights=[w for _, w in v["chance"]]))
            paired = float((d <= PAIR_NM).mean())
            pair_rows.append({"Sample": name, "From (AB1)": m["labels"][a], "To (AB2)": m["labels"][b], "ExF Used": factor, "ExF Source": source,
                              "AB1 Puncta": len(d), "Distance to Nearest AB2, median (nm)": round(float(np.median(d))),
                              f"With an AB2 Partner within {PAIR_NM:g} nm (%)": round(100 * paired, 1),
                              "Expected by Chance (%)": round(100 * chance, 2),
                              "Pairing (× chance)": round(paired / chance, 1) if chance else None,
                              "Partner Distance, median of pairs (nm)": round(float(np.median(d[d <= PAIR_NM]))) if paired else None})
            hists[(name, f"{m['labels'][a]} → {m['labels'][b]}", "pair")] = np.histogram(d, HIST_NM)[0]
        print(f"[{i}/{len(names)}] {name} (ExF {factor}, {source}): " + "; ".join(line))

    day = lambda n: n.split("_")[0]
    # ---- no-primary controls of the same set and channel: what the secondary alone leaves behind
    floor = {}
    for r in rows:
        if "control" in r["Sample"].lower() and r.get("Sites / 100 µm² (tissue)") is not None:
            floor.setdefault((day(r["Sample"]), r["Channel"]), []).append(r["Sites / 100 µm² (tissue)"])
    for r in rows:
        mine = floor.get((day(r["Sample"]), r["Channel"]))
        if mine and "control" not in r["Sample"].lower():
            mid = expected_profiles(r["ExF Used"])[1]
            r["Control Sites / 100 µm² (tissue)"] = round(float(np.mean(mine)), 2)
            r["Sites above Controls / Expected (%)"] = round(100 * max(r["Sites / 100 µm² (tissue)"] - float(np.mean(mine)), 0) / mid)
    # ---- flags: far from the other samples of the same day and channel, or vessel-like structures
    for key, title in (("Background (counts)", "background level"), ("Noise (counts)", "noise"),
                       ("Background Unevenness (noise-widths)", "uneven background")):
        groups = {}
        for r in rows:
            groups.setdefault((day(r["Sample"]), r["Channel"]), []).append(r)
        for group in groups.values():
            v = np.array([r[key] for r in group], float)
            spread = 1.4826 * np.median(np.abs(v - np.median(v)))
            for r, x in zip(group, v):
                if len(group) >= 4 and spread > 0 and (x - np.median(v)) / spread > FLAG_Z:
                    r["Flags"] = (r.get("Flags", "") + f"; high {title} ({x:g} against {np.median(v):g} for its set)").lstrip("; ")
    for r in rows:
        if r["Vessel-like Structures"]:
            r["Flags"] = (r.get("Flags", "") + f"; {r['Vessel-like Structures']} vessel-like structure(s)").lstrip("; ")
        elif r["Large Structures"]:
            r["Flags"] = (r.get("Flags", "") + f"; {r['Large Structures']} large structure(s)").lstrip("; ")
    for file, data in (("AB Metrics.csv", rows), ("AB Pairs.csv", pair_rows)):
        heads = list(dict.fromkeys(k for r in data for k in r))
        with open(base / file, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, heads)
            w.writeheader()
            w.writerows(data)
    with open(base / "AB Distributions (nm).csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Sample", "Channel or Pair", "Distance", "Unit"] + [f"{int(a)}–{int(b)}" for a, b in zip(HIST_NM[:-1], HIST_NM[1:])])
        for (name, what, kind), counts in hists.items():
            w.writerow([name, what, {"nn": "nearest neighbour, same channel", "syn": "to nearest synapse", "pair": "AB1 to nearest AB2"}[kind],
                        "puncta per 50 nm bin of tissue distance"] + list(counts))
    flagged = [r for r in rows if r.get("Flags")]
    print(f"\n{len(flagged)} channel(s) flagged:")
    for r in flagged:
        print(f"   {r['Sample']}  {r['Channel']}: {r['Flags']}")
    print(f"\n{base}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "")
