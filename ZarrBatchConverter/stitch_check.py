"""
How well was a tiled volume stitched? For Neuroglancer precomputed volumes made by NRStitcher.

  stitch_check.py <dataset folder> <tiles across> <tiles down> [overlap %] [number of depths]
  e.g.  stitch_check.py "X:\\...\\260610_pan_aslm_4x4x200_toy\\chan0\\precomputed" 4 4 15

It reads small windows of the stitched volume at full resolution: along every band where two
tiles overlap, at the corners where four meet, and in the middle of every tile for comparison.
In a band the two tiles were blended, so any misregistration shows there. Per window:

  doubling    If the two tiles are blended a few pixels apart, every structure appears twice,
              the same distance and direction apart. That puts a second peak in the window's
              autocorrelation, away from the centre. The index is the height of the strongest
              such peak (0-1) within MAX_SHIFT pixels, after fine structure is isolated; its
              position is the misregistration vector. Tile interiors give the level expected
              from the tissue's own texture.
  sharpness   Blending two tiles that do not quite agree blurs. Sharpness is the energy of fine
              detail (Laplacian) over the energy of the window, relative to tile interiors.
  brightness  Mean intensity relative to tile interiors: a band that is brighter or darker than
              the tiles it joins shows as a stripe.

Across the whole plane (coarsest level): the mean profile along x and y, for steps at tile edges.
The tile grid is taken as regular (tiles of equal size, the stated overlap); a non-rigid stitch
moves things by tens of pixels at most, which the bands' width absorbs.
"""

import csv
import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np

WINDOW = 512             # window side, pixels
PER_BAND = 8             # windows along each overlap band, on each plane
MAX_SHIFT = 40           # doubling is looked for up to this many pixels apart
CENTRE_PX = 5            # ...and further than this from the centre (the structure's own width)
PLANES = (0.25, 0.75)    # depths sampled, as shares of the volume


class Volume:
    """A precomputed volume, unsharded, raw or gzip chunks. region() returns one XY plane, zero where nothing is stored."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.info = json.loads((self.folder / "info").read_text())
        self.dtype = np.dtype(self.info["data_type"]).newbyteorder("<")

    def scale(self, i):
        return self.info["scales"][i]

    def chunk(self, s, x0, y0, z0):
        cx, cy, cz = s["chunk_sizes"][0]
        sx, sy, sz = s["size"]
        x1, y1, z1 = min(x0 + cx, sx), min(y0 + cy, sy), min(z0 + cz, sz)
        path = self.folder / s["key"] / f"{x0}-{x1}_{y0}-{y1}_{z0}-{z1}"
        shape = (x1 - x0, y1 - y0, z1 - z0)
        for p, opener in ((path, open), (path.with_name(path.name + ".gz"), gzip.open)):
            if p.is_file():
                with opener(p, "rb") as f:
                    return np.frombuffer(f.read(), self.dtype).reshape(shape, order="F")
        return np.zeros(shape, self.dtype)

    def region(self, i, x0, x1, y0, y1, z):
        s = self.scale(i)
        cx, cy, cz = s["chunk_sizes"][0]
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(x1, s["size"][0]), min(y1, s["size"][1])
        out = np.zeros((y1 - y0, x1 - x0), self.dtype)
        z0 = (z // cz) * cz
        for gx in range((x0 // cx) * cx, x1, cx):
            for gy in range((y0 // cy) * cy, y1, cy):
                block = self.chunk(s, gx, gy, z0)[:, :, z - z0]            # (x, y)
                ax, ay = max(gx, x0), max(gy, y0)
                bx, by = min(gx + block.shape[0], x1), min(gy + block.shape[1], y1)
                out[ay - y0:by - y0, ax - x0:bx - x0] = block[ax - gx:bx - gx, ay - gy:by - gy].T
        return out


def window_numbers(img):
    """(doubling index, dx, dy, sharpness, mean, share of real pixels) of one window."""
    from scipy import ndimage

    real = float((img > 0).mean())
    if real < 0.9:
        return None
    a = img.astype(np.float32)
    fine = a - ndimage.gaussian_filter(a, 6)                 # structures a few pixels wide
    fine = ndimage.gaussian_filter(fine, 1.0)                # ...without single-pixel noise
    fine -= fine.mean()
    power = np.abs(np.fft.rfft2(fine)) ** 2
    auto = np.fft.fftshift(np.fft.irfft2(power, s=fine.shape))
    auto /= auto.max()
    c = np.array(auto.shape) // 2
    yy, xx = np.mgrid[:auto.shape[0], :auto.shape[1]]
    r = np.hypot(yy - c[0], xx - c[1])
    ring = np.where((r > CENTRE_PX) & (r <= MAX_SHIFT) & (yy >= c[0]), auto, -1)      # half plane: the other half mirrors it
    # a real second peak stands above its surroundings: take off the smooth fall-off of the central peak
    ring = np.where(ring > -1, auto - ndimage.gaussian_filter(auto, 4), -1)
    at = np.unravel_index(np.argmax(ring), ring.shape)
    sharp = float(np.var(ndimage.laplace(ndimage.gaussian_filter(a, 1.0))) / max(np.var(a), 1e-9))
    return float(ring[at]), int(at[1] - c[1]), int(at[0] - c[0]), sharp, float(a.mean()), real


def run(folder, nx, ny, overlap, depths=0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    import ab_efficiency as eff
    import convert_to_zarr as core

    vol = Volume(folder)
    s0 = vol.scale(0)
    sx, sy, sz = s0["size"]
    um = s0["resolution"][0] / 1000
    tile_x, tile_y = sx / (nx - (nx - 1) * overlap), sy / (ny - (ny - 1) * overlap)      # regular grid that gives this size
    pitch_x, pitch_y = tile_x * (1 - overlap), tile_y * (1 - overlap)
    name = next((p.name for p in (Path(folder), *Path(folder).parents) if len(p.name) > 6 and p.name[:6].isdigit()), Path(folder).name)
    print(f"{name}: {sx} x {sy} x {sz} voxels of {s0['resolution']} nm; taken as {nx} x {ny} tiles of {tile_x:.0f} x {tile_y:.0f} px "
          f"overlapping {overlap * tile_x:.0f} px")
    places = []
    for i in range(nx):
        for j in range(ny):
            places.append(("tile interior", f"tile {i},{j}", i * pitch_x + tile_x / 2, j * pitch_y + tile_y / 2))
    for i in range(1, nx):                                    # bands between left and right neighbours
        x = i * pitch_x + overlap * tile_x / 2
        for y in np.linspace(WINDOW, sy - WINDOW, PER_BAND):
            places.append(("overlap, left-right", f"between columns {i - 1} and {i}", x, y))
    for j in range(1, ny):
        y = j * pitch_y + overlap * tile_y / 2
        for x in np.linspace(WINDOW, sx - WINDOW, PER_BAND):
            places.append(("overlap, top-bottom", f"between rows {j - 1} and {j}", x, y))
    for i in range(1, nx):
        for j in range(1, ny):
            places.append(("corner, four tiles", f"corner {i},{j}", i * pitch_x + overlap * tile_x / 2, j * pitch_y + overlap * tile_y / 2))
    rows, crops = [], {}
    shares = np.linspace(0.05, 0.95, depths) if depths else PLANES        # a deep volume is sampled at more depths
    planes = sorted({int(sz * f) for f in shares})
    for z in planes:
        for n, (kind, where, x, y) in enumerate(places):
            x0, y0 = int(x - WINDOW / 2), int(y - WINDOW / 2)
            img = vol.region(0, x0, x0 + WINDOW, y0, y0 + WINDOW, z)
            got = window_numbers(img) if img.shape == (WINDOW, WINDOW) else None
            if got is None:
                continue
            rows.append({"Kind": kind, "Where": where, "x (px)": int(x), "y (px)": int(y), "z": z, "Doubling Index": round(got[0], 3),
                         "Offset x (px)": got[1], "Offset y (px)": got[2], "Offset (µm)": round(math.hypot(got[1], got[2]) * um, 2),
                         "Sharpness": got[3], "Mean": round(got[4], 1)})
            crops[len(rows) - 1] = img
        print(f"   plane {z}: {sum(r['z'] == z for r in rows)} windows read")
    inside = [r for r in rows if r["Kind"] == "tile interior"]
    base_d = np.array([r["Doubling Index"] for r in inside])
    base_s, base_m = np.median([r["Sharpness"] for r in inside]), np.median([r["Mean"] for r in inside])
    limit = float(np.percentile(base_d, 95) + 2 * base_d.std())
    for r in rows:
        r["Sharpness (interiors = 1)"] = round(r.pop("Sharpness") / base_s, 2)
        r["Brightness (interiors = 1)"] = round(r["Mean"] / base_m, 2)
        r["Doubled"] = "YES" if r["Kind"] != "tile interior" and r["Doubling Index"] > limit else ""
    out = Path(core.DEFAULT_SHOTS).parent / "Zarr Comparisons" / "Stitching" / name
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "Stitching Windows.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\ninteriors: doubling index {np.median(base_d):.3f} (95th percentile {np.percentile(base_d, 95):.3f}); a window counts as doubled above {limit:.3f}")
    table = []
    for kind in ("overlap, left-right", "overlap, top-bottom", "corner, four tiles"):
        for where in dict.fromkeys(r["Where"] for r in rows if r["Kind"] == kind):
            g = [r for r in rows if r["Where"] == where]
            d = [r for r in g if r["Doubled"]]
            off = np.median([r["Offset (µm)"] for r in d]) if d else 0
            vec = (int(np.median([r["Offset x (px)"] for r in d])), int(np.median([r["Offset y (px)"] for r in d]))) if d else (0, 0)
            table.append({"Where": where, "Kind": kind, "Windows": len(g), "Doubled": len(d),
                          "Doubling Index (median)": round(float(np.median([r["Doubling Index"] for r in g])), 3),
                          "Typical Offset (px, x y)": f"{vec[0]} {vec[1]}" if d else "", "Typical Offset (µm)": round(float(off), 2) if d else "",
                          "Sharpness (interiors = 1)": round(float(np.median([r["Sharpness (interiors = 1)"] for r in g])), 2),
                          "Brightness (interiors = 1)": round(float(np.median([r["Brightness (interiors = 1)"] for r in g])), 2)})
            t = table[-1]
            print(f"   {where:28s} doubled {t['Doubled']:2d}/{t['Windows']:2d}  index {t['Doubling Index (median)']:.3f}  offset {t['Typical Offset (px, x y)'] or '–':>8s} px"
                  f"  sharpness {t['Sharpness (interiors = 1)']:.2f}  brightness {t['Brightness (interiors = 1)']:.2f}")
    print("\nby depth (overlap windows only):")
    by_depth = []
    for z in planes:
        g = [r for r in rows if r["z"] == z and r["Kind"] != "tile interior"]
        own = [r for r in rows if r["z"] == z and r["Kind"] == "tile interior"]
        if not g:
            continue
        by_depth.append({"z": z, "Depth (µm)": round(z * s0["resolution"][2] / 1000, 1), "Windows": len(g), "Doubled": sum(bool(r["Doubled"]) for r in g),
                         "Doubling Index (median)": round(float(np.median([r["Doubling Index"] for r in g])), 3),
                         "Interiors Doubling Index (median)": round(float(np.median([r["Doubling Index"] for r in own])), 3) if own else "",
                         "Brightness (interiors = 1)": round(float(np.median([r["Brightness (interiors = 1)"] for r in g])), 2),
                         "Mean Counts": round(float(np.median([r["Mean"] for r in g])), 1)})
        t = by_depth[-1]
        print(f"   z {z:5d} ({t['Depth (µm)']:7.1f} µm)  doubled {t['Doubled']:2d}/{t['Windows']:2d}  index {t['Doubling Index (median)']:.3f} "
              f"(interiors {t['Interiors Doubling Index (median)']})  mean counts {t['Mean Counts']}")
    with open(out / "Stitching Depths.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(by_depth[0]))
        w.writeheader()
        w.writerows(by_depth)
    with open(out / "Stitching Bands.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(table[0]))
        w.writeheader()
        w.writerows(table)

    # ---- whole-plane profiles at the coarsest level: steps at tile edges
    last = len(vol.info["scales"]) - 1
    sl = vol.scale(last)
    plane = vol.region(last, 0, sl["size"][0], 0, sl["size"][1], sl["size"][2] // 2).astype(np.float32)
    lit = np.where(plane > 0, plane, np.nan)
    with np.errstate(all="ignore"):
        along_x, along_y = np.nanmean(lit, axis=0), np.nanmean(lit, axis=1)

    # ---- figure: map of the windows, the profiles, and the worst windows
    fig, axes = eff.figure(2, 3, figsize=(16, 10))
    ax = axes[0][0]
    lo, hi = np.nanpercentile(lit, [1, 99.5])
    ax.imshow(np.clip((plane - lo) / (hi - lo), 0, 1), cmap="gray", extent=(0, sx, sy, 0), interpolation="nearest")
    for i in range(1, nx):
        ax.axvspan(i * pitch_x, i * pitch_x + overlap * tile_x, color="#4DA3FF", alpha=0.12, lw=0)
    for j in range(1, ny):
        ax.axhspan(j * pitch_y, j * pitch_y + overlap * tile_y, color="#4DA3FF", alpha=0.12, lw=0)
    for r in rows:
        if r["Kind"] != "tile interior":
            ax.plot(r["x (px)"], r["y (px)"], "o", ms=6 if r["Doubled"] else 3, color="#FF9900" if r["Doubled"] else "white", alpha=0.9)
    ax.set_title(f"Middle plane, overlap bands shaded. Orange = window with doubling ({sum(bool(r['Doubled']) for r in rows)} of "
                 f"{sum(r['Kind'] != 'tile interior' for r in rows)})", color=eff.INK, fontsize=10, loc="left")
    ax.set_xlabel("x (pixels)", color=eff.INK, fontsize=9)
    ax.set_ylabel("y (pixels)", color=eff.INK, fontsize=9)
    for ax, profile, size, pitch, tile, axis in ((axes[0][1], along_x, sx, pitch_x, tile_x, "x"), (axes[0][2], along_y, sy, pitch_y, tile_y, "y")):
        ax.plot(np.linspace(0, size, len(profile)), profile, color="white", lw=1.5)
        for k in range(1, nx if axis == "x" else ny):
            ax.axvspan(k * pitch, k * pitch + overlap * tile, color="#4DA3FF", alpha=0.18, lw=0)
        ax.set_title(f"Mean brightness along {axis} (overlap bands shaded)", color=eff.INK, fontsize=10, loc="left")
        ax.set_xlabel(f"{axis} (pixels)", color=eff.INK, fontsize=9)
        ax.set_ylabel("Counts", color=eff.INK, fontsize=9)
        ax.grid(color="#2A2A2A", lw=0.6)
    worst = sorted((i for i, r in enumerate(rows) if r["Kind"] != "tile interior"), key=lambda i: -rows[i]["Doubling Index"])[:2]
    clean = min((i for i, r in enumerate(rows) if r["Kind"] == "tile interior"), key=lambda i: abs(rows[i]["Doubling Index"] - np.median(base_d)))
    for ax, i, title in ((axes[1][0], worst[0], "Most doubled window"), (axes[1][1], worst[1], "Second most doubled window"), (axes[1][2], clean, "A typical tile interior")):
        img = crops[i][128:384, 128:384].astype(np.float32)
        lo, hi = np.percentile(img, [1, 99.7])
        ax.imshow(np.clip((img - lo) / max(hi - lo, 1), 0, 1), cmap="gray", interpolation="nearest")
        r = rows[i]
        ax.plot([8, 8 + 5 / um], [248, 248], color="white", lw=3, solid_capstyle="butt")
        ax.text(8, 244, "5 µm", color="white", fontsize=8, va="bottom")
        ax.set_title(f"{title}: {r['Where']}, z {r['z']}\nindex {r['Doubling Index']}, offset ({r['Offset x (px)']}, {r['Offset y (px)']}) px = {r['Offset (µm)']} µm",
                     color=eff.INK, fontsize=10, loc="left")
        ax.set_xticks([])
        ax.set_yticks([])
    fig.suptitle(f"{name}: Stitching Check", color=eff.INK, fontsize=12)
    fig.tight_layout()
    eff.save(fig, out / "Stitching Check.png")
    print(f"\n{out}")


if __name__ == "__main__":
    import convert_to_zarr

    convert_to_zarr.load_settings()
    run(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]) / 100 if len(sys.argv) > 4 else 0.15, int(sys.argv[5]) if len(sys.argv) > 5 else 0)
