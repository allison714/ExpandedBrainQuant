"""
A training set of synapses from a stitched pan-ASLM volume: the best ones, and a set of doubtful
ones for a person to judge.

  aslm_training.py plan    <dataset folder> <tiles across> <tiles down> [overlap %]     where to sample, from the pilot
  aslm_training.py collect <dataset folder> <tiles across> <tiles down> [overlap %] [worker k of n, as "k/n"]
  aslm_training.py export  <dataset folder> <tiles across> <tiles down> [overlap %]     choose, cut the patches, write everything

Where to sample (plan)
  aslm_synapses.py has already measured one field in every tile at a few depths. From it, the
  number of synapses a field gives is modelled as (effect of depth) x (effect of the tile): the
  depth effect is interpolated between the measured depths, the tile effect is each tile's
  share of the total. Fields are then drawn at random, without replacement, with a chance
  proportional to that expected number. A floor (FLOOR of the average chance) keeps every depth
  and tile in the set, so the training data are not all from the easiest tissue. A field is the
  middle FIELD_PX pixels of one tile on one plane; planes are PLANE_STEP apart (6.4 µm), so no
  synapse is seen twice.

What is collected (collect)
  The synapse finder of synapse_exf.py with its gates loosened (LOOSE), so that profiles that
  only just fail are kept as well. Each candidate keeps its measurements and a raw patch.

How they are chosen (export)
  Every candidate gets a quality score between 0 and 1 (see score()). It does not use the
  expansion factor, so choosing the best does not push the measured distance towards any value.
    best          the BEST highest-scoring candidates that pass the normal gates
    questionable  QUESTIONABLE of the others, the same number from each tenth of their score
                  range, so the person judging sees the whole range from nearly-good to poor.
                  They are listed in random order. Candidates that are plain camera noise
                  (NOISE_SNR) are left out: nobody needs to judge those.
"""

import csv
import json
import math
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

import aslm_synapses
import convert_to_zarr as core
import stitch_check
import synapse_exf as syn
import synapse_structure as structure

BEST, QUESTIONABLE = 8000, 2000
FIELDS = 1400            # fields to read: about three times as many good candidates as are wanted
FLOOR = 0.25             # no field's chance of being drawn is below this share of the average
PLANE_STEP = 32          # planes used are 32 apart (one chunk each), and sit mid-chunk so the planes around them are in the same files
PATCH = 96               # raw patch kept with every candidate, pixels
OUT_PATCH = 64           # patch written to the training set, pixels
OUT_PLANES = 9           # ...and the planes around it (this many in all)
LOOSE = {"PSD_RANK": 0.98, "FIT_R2": 0.85, "MIN_DIP": 0.15}      # gates while collecting
STRICT = {k: getattr(syn, k) for k in LOOSE}                     # the normal gates
NOISE_SNR = 4.0          # below this a candidate is camera noise and is left out of the questionable set
REVIEW_LOG ="pan-ASLM Training Review.xlsx"


def layout(folder, nx, ny, overlap):
    core.load_settings()
    vol = stitch_check.Volume(folder)
    s0 = vol.scale(0)
    sx, sy, sz = s0["size"]
    name = next((p.name for p in (Path(folder), *Path(folder).parents) if len(p.name) > 6 and p.name[:6].isdigit()), Path(folder).name)
    tile_x, tile_y = sx / (nx - (nx - 1) * overlap), sy / (ny - (ny - 1) * overlap)
    out = Path(core.DEFAULT_SHOTS).parent / "Zarr Synapse ExF" / "pan-ASLM" / name
    work = Path.home() / "AppData" / "Local" / "ZarrBatchConverter" / "aslm_training" / name      # large, temporary, not synced
    work.mkdir(parents=True, exist_ok=True)

    def corner(i, j):
        x0 = int(i * tile_x * (1 - overlap) + tile_x / 2 - aslm_synapses.FIELD_PX / 2)
        y0 = int(j * tile_y * (1 - overlap) + tile_y / 2 - aslm_synapses.FIELD_PX / 2)
        return max(0, min(x0, sx - aslm_synapses.FIELD_PX)), max(0, min(y0, sy - aslm_synapses.FIELD_PX))

    return vol, s0, name, out, work, corner


def plan(folder, nx, ny, overlap):
    vol, s0, name, out, work, _ = layout(folder, nx, ny, overlap)
    sz = s0["size"][2]
    pilot = pickle.loads((out / "_fields.pkl").read_bytes())
    depths = sorted({k[0] for k in pilot})
    count = np.zeros((len(depths), ny, nx))
    for (z, j, i), v in pilot.items():
        count[depths.index(z), j, i] = len(v["hits"])
    by_depth, by_tile = count.mean(axis=(1, 2)), count.mean(axis=0) / count.mean()
    planes = np.arange(PLANE_STEP // 2, sz - PLANE_STEP // 2, PLANE_STEP)
    expected = np.interp(planes, depths, by_depth)[:, None, None] * by_tile[None]          # synapses per field: depth x tile
    chance = np.maximum(expected, FLOOR * expected.mean())
    rng = np.random.default_rng(0)
    picks = rng.choice(chance.size, size=min(FIELDS, chance.size), replace=False, p=(chance / chance.sum()).ravel())
    fields = sorted((int(planes[p // (nx * ny)]), int(p % (nx * ny) // nx), int(p % nx)) for p in picks)
    (work / "plan.json").write_text(json.dumps(fields))
    took = np.zeros(chance.shape, bool).ravel()
    took[picks] = True
    took = took.reshape(chance.shape)
    print(f"{name}: pilot of {len(pilot)} fields, {int(count.sum())} synapse profiles\n")
    print("Synapses per field in the pilot, by depth:  " + ",  ".join(f"z {z}: {c:.1f}" for z, c in zip(depths, by_depth)))
    print("Tile effect (1 = average), rows top to bottom:")
    for row in by_tile:
        print("   " + "  ".join(f"{v:4.2f}" for v in row))
    print(f"\n{len(fields)} of {chance.size} possible fields drawn ({len(planes)} planes x {nx * ny} tiles); "
          f"expected synapses at the normal gates: {expected[took].sum():.0f}")
    with open(out / f"{name}_training_sampling-plan.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Depth From (µm)", "Depth To (µm)", "Fields Possible", "Fields Drawn", "Share Drawn (%)", "Expected Synapses per Field"])
        edges = np.linspace(0, len(planes), 11).astype(int)
        print("\nFields drawn by depth:")
        for a, b in zip(edges[:-1], edges[1:]):
            row = [round(planes[a] * s0["resolution"][2] / 1000), round(planes[b - 1] * s0["resolution"][2] / 1000), took[a:b].size, int(took[a:b].sum()),
                   round(100 * took[a:b].mean(), 1), round(float(expected[a:b].mean()), 1)]
            w.writerow(row)
            print(f"   {row[0]:5d}–{row[1]:5d} µm: {row[3]:4d} of {row[2]:4d} ({row[4]:4.1f}%), about {row[5]:.1f} synapses per field")
    print("Fields drawn by tile, rows top to bottom:")
    for row in took.sum(axis=0):
        print("   " + "  ".join(f"{v:4d}" for v in row))


def collect(folder, nx, ny, overlap, part="1/1"):
    vol, s0, name, out, work, corner = layout(folder, nx, ny, overlap)
    um = s0["resolution"][0] / 1000
    k, n = (int(v) for v in part.split("/"))
    fields = [tuple(f) for f in json.loads((work / "plan.json").read_text())]
    mine = [f for m, f in enumerate(fields) if m % n == k - 1]
    for key, value in LOOSE.items():
        setattr(syn, key, value)
    size, half = aslm_synapses.FIELD_PX, PATCH // 2
    for m, (z, j, i) in enumerate(mine, start=1):
        file = work / f"field_z{z:05d}_r{j}_c{i}.pkl"
        if file.is_file():
            continue
        t0 = time.time()
        x0, y0 = corner(i, j)
        field = aslm_synapses.read_plane(vol, x0, x0 + size, y0, y0 + size, z)
        hits = [h for h in syn.find_synapses(field, None, um) if half <= h["row"] < size - half and half <= h["col"] < size - half]
        for h in hits:
            r, c = int(round(h["row"])), int(round(h["col"]))
            h["patch"] = field[r - half:r + half, c - half:c + half].copy()
            h["patch_at"] = (r + y0, c + x0)                       # volume pixel of the patch's centre pixel [half, half]
            h["row"], h["col"], h["z"], h["fov"] = h["row"] + y0, h["col"] + x0, z, (z, j, i)
            for drop in ("lines", "ab_profile", "ab2_profile"):
                h.pop(drop, None)
        valid = field > 0
        tmp = file.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps({"hits": hits, "area": int(valid.sum()), "mean": float(field[valid].mean()) if valid.any() else 0.0}))
        tmp.replace(file)
        print(f"   [{part}] {m}/{len(mine)}  z {z:5d} tile {i},{j}: {len(hits):3d} candidates  ({time.time() - t0:.0f} s)", flush=True)
    print(f"[{part}] done")


def score(h):
    """0 to 1: how sure it is that this double peak is a synapse seen side-on. The geometric mean of
         fit       how well two Gaussians explain the profile (R² from 0.85 to 1)
         dip       how deep the trough between the peaks is (0 to 0.6 of the smaller peak)
         balance   how alike the two peaks' heights are (1 within a factor 2, 0 at a factor 4)
         bright    how bright the brighter density is for its own field (rank from 0.98 to 1)
         bar       how little of the patch around it is bright: a synapse is two thin bars in a
                   dark surround, a cell body or a blob of stain fills the patch
         signal    how far the dimmer of the two peaks stands above the camera noise (4 to 16
                   noise-widths): in planes with almost no stain the finder otherwise picks noise
       The expansion factor is not part of it."""
    patch = h["patch"].astype(np.float32)
    mid = patch[PATCH // 2 - 24:PATCH // 2 + 24, PATCH // 2 - 24:PATCH // 2 + 24]
    filled = float((mid - h["base"] > 0.5 * (h["peak"] - h["base"])).mean())
    # camera noise from pixel-to-pixel differences of the raw patch; the 1-pixel smoothing the profile was read from leaves 0.28 of it
    noise = 0.28 * float(np.median(np.abs(np.diff(patch, axis=1)))) * 1.4826 / math.sqrt(2) or 1.0
    h["snr"] = float((min(h["tops"]) - h["base"]) / noise)
    parts = [np.clip((h["r2"] - 0.85) / 0.15, 0, 1), np.clip(h["dip"] / 0.6, 0, 1), 1 - np.clip(abs(math.log2(max(h["ratio"], 1e-6))) - 1, 0, 1),
             np.clip((h["bright"] - 0.98) / 0.02, 0, 1), 1 - np.clip((filled - 0.08) / 0.22, 0, 1), np.clip((h["snr"] - 4) / 12, 0, 1)]
    h["filled"] = filled
    return float(np.prod(np.clip(parts, 0.02, 1)) ** (1 / len(parts)))


def slabs(vol, items):
    """The OUT_PLANES x OUT_PATCH x OUT_PATCH block around each (z, row, col), chunk files read once each."""
    s = vol.scale(0)
    cx, cy, cz = s["chunk_sizes"][0]
    half, dz = OUT_PATCH // 2, OUT_PLANES // 2
    need = {}
    for n, (z, r, c) in enumerate(items):
        for gx in range(((c - half) // cx) * cx, c + half, cx):
            for gy in range(((r - half) // cy) * cy, r + half, cy):
                need.setdefault((gx, gy, (z // cz) * cz), []).append(n)
    out = np.zeros((len(items), OUT_PLANES, OUT_PATCH, OUT_PATCH), vol.dtype)

    def one(key):
        return key, vol.chunk(s, *key)

    with ThreadPoolExecutor(aslm_synapses.READERS) as pool:
        for done, ((gx, gy, gz), block) in enumerate(pool.map(one, list(need)), start=1):
            for n in need[(gx, gy, gz)]:
                z, r, c = items[n]
                ax, ay = max(gx, c - half), max(gy, r - half)
                bx, by = min(gx + block.shape[0], c + half), min(gy + block.shape[1], r + half)
                za, zb = max(z - dz, gz), min(z + dz + 1, gz + block.shape[2])
                out[n, za - (z - dz):zb - (z - dz), ay - (r - half):by - (r - half), ax - (c - half):bx - (c - half)] = \
                    block[ax - gx:bx - gx, ay - gy:by - gy, za - gz:zb - gz].transpose(2, 1, 0)
            if done % 2000 == 0:
                print(f"      {done}/{len(need)} chunk files", flush=True)
    return out


def export(folder, nx, ny, overlap):
    from scipy import ndimage

    vol, s0, name, out, work, _ = layout(folder, nx, ny, overlap)
    um = s0["resolution"][0] / 1000
    files = sorted(work.glob("field_*.pkl"))
    radius = syn.REF_NM * syn.EXF_NOMINAL / (um * 1000)
    every, area = [], 0
    for file in files:
        got = pickle.loads(file.read_bytes())
        area += got["area"]
        for h in syn.unique(got["hits"], radius):
            h["score"] = score(h)
            h["strict"] = h["bright"] >= STRICT["PSD_RANK"] and h["r2"] >= STRICT["FIT_R2"] and h["dip"] >= STRICT["MIN_DIP"]
            every.append(h)
    strict = sorted((h for h in every if h["strict"]), key=lambda h: -h["score"])
    best = strict[:BEST]
    chosen = {id(h) for h in best}
    # not worth a person's time: candidates whose dimmer peak is within NOISE_SNR noise-widths of the background (plain camera noise)
    rest = sorted((h for h in every if id(h) not in chosen and h["snr"] >= NOISE_SNR), key=lambda h: -h["score"])
    rng = np.random.default_rng(0)
    doubt = []
    for part in np.array_split(np.arange(len(rest)), 10):           # the same number from each tenth of the score range
        take = rng.choice(part, size=min(QUESTIONABLE // 10, len(part)), replace=False)
        doubt += [rest[t] for t in take]
    doubt = [doubt[t] for t in rng.permutation(len(doubt))]          # judged in random order
    print(f"{name}: {len(files)} fields, {len(every)} candidates, {len(strict)} pass the normal gates\n"
          f"   best: {len(best)}, score {best[-1]['score']:.3f} to {best[0]['score']:.3f} (the top {100 * len(best) / max(len(strict), 1):.0f}% of those that pass)\n"
          f"   questionable: {len(doubt)}, score {min(h['score'] for h in doubt):.3f} to {max(h['score'] for h in doubt):.3f}; "
          f"{sum(h['strict'] for h in doubt)} of them pass the normal gates")

    folder_out = out / "Training Set"
    folder_out.mkdir(exist_ok=True)
    view = Path(folder).parent / core.STATE_DIR / f"{name}.json"
    state = json.loads(view.read_text(encoding="utf-8")) if view.is_file() else None
    head = ["ID", "Set", "Row In Array", "Slice", "X (px)", "Y (px)", "Tile Column", "Tile Row", "Score", "Passes Normal Gates", "ExF (peak to peak)",
            "ExF (Gaussian fit)", "DP-PSD (µm, as imaged)", "Orientation (deg)", "Class", "PSD Named By", "PSD x In Patch", "PSD y In Patch",
            "DP x In Patch", "DP y In Patch", "Bar Length (px)", "Fit R²", "Dip", "Peak Ratio", "Brightness Rank", "Patch Filled"]
    table = []
    for label, group in (("best", best), ("questionable", doubt)):
        for h in group:
            h["ab_pos"] = h["ab2_pos"] = False
            h["ab_profile"] = h["ab2_profile"] = None
            syn.classify(h, None, um)
            h["example"] = syn.example_score(h, um)
            structure.measure(h, um)
        items = [(h["z"], h["patch_at"][0], h["patch_at"][1]) for h in group]
        print(f"   {label}: reading {OUT_PLANES} planes around {len(items)} synapses...", flush=True)
        block = slabs(vol, items)
        np.savez_compressed(folder_out / f"{name}_{label}_patches.npz", patches_2d=block[:, OUT_PLANES // 2], patches_3d=block,
                            pixel_nm=np.array(s0["resolution"], np.float32))
        for n, h in enumerate(group):
            across = np.array([-math.sin(h["theta"]), math.cos(h["theta"])])       # (row, col), from one density to the other
            origin = np.array([h["row"], h["col"]]) - (h["fit"][2] + h["fit"][5]) / 2 * across
            points = [origin + m * across - np.array(h["patch_at"]) + OUT_PATCH // 2 for m in h["summits"]]
            psd, dp = points[h["psd"]], points[1 - h["psd"]]
            table.append([f"z{h['z']:04d}_x{round(h['col']):05d}_y{round(h['row']):05d}", label, n, h["z"], round(h["col"], 1), round(h["row"], 1), h["fov"][2], h["fov"][1],
                          round(h["score"], 4), "Y" if h["strict"] else "", round(h["exf"], 2), round(h["exf_fit"], 2), round(h["distance_um"], 4),
                          round(math.degrees(h["theta"]) % 180, 1), h["class"], h["psd_by"], round(psd[1], 2), round(psd[0], 2), round(dp[1], 2), round(dp[0], 2),
                          round(h["length"], 1), round(h["r2"], 3), round(h["dip"], 3), round(h["ratio"], 3), round(h["bright"], 4), round(h["filled"], 3)])
    with open(folder_out / f"{name}_training_synapses.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(table)

    # ---- at a glance: the top of the best, a random draw of the best, the bottom of the best
    half = int(round(2 * syn.PROFILE_HALF_NM * syn.EXF_NOMINAL / (um * 1000) * 0.75))

    def tile_of(h):
        return {"half": half, "pan": syn.crop(ndimage.gaussian_filter(h["patch"].astype(np.float32), syn.SMOOTH_PX), PATCH // 2 + h["row"] - h["patch_at"][0],
                                              PATCH // 2 + h["col"] - h["patch_at"][1], half), "ab": None}

    bar_exf = float(np.mean([h["exf"] for h in best]))      # for the scale bars: the volume's expansion factor, from the best set
    spread = [best[t] for t in sorted(rng.choice(len(best), size=min(100, len(best)), replace=False))]
    for title, group, first in (("best_first-100", best[:100], 1), ("best_random-100", spread, None), ("best_last-100", best[-100:], len(best) - 99),
                                ("questionable_first-100", doubt[:100], 1)):
        numbered = [(first + n if first else best.index(h) + 1, h, tile_of(h)) for n, h in enumerate(group)]
        syn.contact_sheet(folder_out / f"{name}_{title}.png", f"{name} ({title.replace('_', ', ').replace('-', ' ')})", numbered, um, None, bar_exf)

    # ---- the questionable ones, for a person: a picture each, and a workbook with an Accept column
    review = out.parent / REVIEW_LOG
    try:
        kept = syn.read_decisions(review)
        if review.is_file():
            open(review, "ab").close()
    except PermissionError:
        sys.exit(f"{REVIEW_LOG} is open in Excel. Close it and run this again.")
    cards = folder_out / "Questionable"
    cards.mkdir(exist_ok=True)
    for stale in cards.glob("*.png"):
        stale.unlink()
    rows = []
    for number, h in enumerate(doubt, start=1):
        ident = f"z{h['z']:04d}_x{round(h['col']):05d}_y{round(h['row']):05d}"
        png = cards / f"{name}_questionable-{number:04d}_{ident}.png"
        syn.card(png, number, name, h, tile_of(h), um, {"label": "488 nm"}, None, bar_exf)
        link = None
        if state:
            place = {"x": h["col"], "y": h["row"], "z": h["z"]}
            here = dict(state, position=[place[d] for d in state["dimensions"]], crossSectionScale=0.15, layout="xy")
            link = core.state_to_link(here, Path(folder).parent / core.STATE_DIR / "training" / name / f"questionable_{number:04d}.json")
        accept, notes = syn.decision_for(kept, name, h)
        rows.append({"#": number, "Sample": name, "Slice": h["z"], "X (px)": round(h["col"]), "Y (px)": round(h["row"]), "Antibody": "none (pan only)",
                     "DP–PSD (µm, as imaged)": round(h["distance_um"], 3), "ExF (peak to peak)": round(h["exf"], 1), "ExF (Gaussian fit)": round(h["exf_fit"], 1),
                     "In 16–24": "Y" if syn.EXF_EXPECTED[0] <= h["exf"] <= syn.EXF_EXPECTED[1] else "no", "Fit R²": round(h["r2"], 3), "Dip": round(h["dip"], 2),
                     "Peak Ratio": round(h["ratio"], 2), "Class": h["class"], "PSD Named By": h["psd_by"], "Example Score": round(h["score"], 2),
                     "Synapse ID": ident, "Accept": accept, "Notes": notes, "Picture": str(png), "Neuroglancer": link})
        if number % 250 == 0:
            print(f"      {number}/{len(doubt)} pictures", flush=True)
    syn.write_log(review, rows, [])
    e = np.array([h["exf"] for h in best])
    print(f"\n   best: ExF {e.mean():.1f} ± {e.std(ddof=1):.1f} (median {np.median(e):.1f}); in 16–24: {100 * np.mean((e >= 16) & (e <= 24)):.0f}%\n"
          f"   best by depth (tenths of the stack): {np.histogram([h['z'] for h in best], bins=10, range=(0, s0['size'][2]))[0].tolist()}\n"
          f"   best by tile: {np.bincount([h['fov'][1] * nx + h['fov'][2] for h in best], minlength=nx * ny).tolist()}\n\nDone. {folder_out}\n{review}")


if __name__ == "__main__":
    step, folder, nx, ny = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    overlap = float(sys.argv[5]) / 100 if len(sys.argv) > 5 else 0.15
    if step == "plan":
        plan(folder, nx, ny, overlap)
    elif step == "collect":
        collect(folder, nx, ny, overlap, sys.argv[6] if len(sys.argv) > 6 else "1/1")
    else:
        export(folder, nx, ny, overlap)
