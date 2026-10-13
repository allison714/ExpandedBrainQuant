"""
Antibody labelling efficiency for the zarr images: how well each antibody channel covers
and follows the structure mapped by the pan (total protein) stain.

  ab_efficiency.py [text]        measure every sample whose name contains the text
  ab_efficiency.py --napari NAME open one sample with its True Structure Mask for a look

For each sample and each antibody channel it measures
  colocalisation    Pearson's r, Manders' M1 / M2, coverage of the structure mask
  signal            signal-to-background and signal-to-noise of the antibody, and how
                    evenly the signal-to-background is spread over a 3 x 3 grid
  stacks            the antibody / pan ratio plane by plane (penetration into the gel)
  montages          steps at the tile seams and illumination fall-off inside the tiles

and keeps, in the analysis folder (settings.ini, analysis_folder):
  AB Efficiency Summary.csv      one row per sample and antibody channel, appended
  <sample name>\\                 its PNGs: axial profile, fidelity map, montage checks
The True Structure Mask of each sample is kept as a zarr in a local folder (mask_folder),
because a zarr is thousands of small files and does not belong in OneDrive.

Which channel is the pan stain, the channel colours, the settings and the zarr layout all
come from convert_to_zarr.py, so this agrees with the image list and the Neuroglancer views.

Nothing is loaded whole. The images are opened as Dask arrays over the zarr chunks; the
preprocessing, masks and metrics are built as one lazy graph per pass and only resolved
when the numbers are needed, chunk by chunk:
  pass 1   background-subtract every channel, into a scratch zarr on the local disk, and
           histogram it (the histograms give the min-max range and the thresholds)
  pass 2   masks and every sum the metrics are made of; the mask is written to its zarr
  pass 3   Pearson's r inside the bounding box of the structure, which pass 2 found
"""

import argparse
import configparser
import csv
import datetime as dt
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

import convert_to_zarr as core
import roi_log as roi

# --------------------------------------------------------------------------- settings

CSV_NAME = "AB Efficiency Summary.csv"
RADIUS_UM = 8.0         # rolling-ball radius, in micrometres as imaged (post-expansion): a little
                        # larger than the widest structure, so structures survive and haze does not
DILATE_UM = 1.0         # background is taken this far outside the structure mask, clear of its blur
SURFACE_FRACTION = 0.15 # the planes this close to either face of the gel count as "the surface"
CENTRE_FRACTION = 0.20  # the middle of the gel, where a penetration failure shows
LIMITED_BELOW = 0.5     # antibody / pan ratio at the centre below this share of the surface = limited
CV_FLAG = 0.25          # 3 x 3 signal-to-background varying by more than this = uneven staining
LOW_SNR = 2.0           # an antibody this little above background in the structure gets a warning in Notes
MIN_PIXELS = 500        # fewer mask pixels than this in a plane or region gives no value
MACRO_PX = 1024         # whole-image profiles (gel faces, seams, shading) use the coarsest level this wide
MAP_PX = 1024           # longest side of the fidelity map
MASK_CHUNK = (8, 2048, 2048)   # large chunks keep the mask zarr to a handful of files
FLOAT_BINS = 4096       # histogram bins for images that are not whole numbers
# Inferred tile layout, as in roi_log.seam_centre: 2048-pixel camera frames, about 10% overlap.
TILE_STEP_PX = 1850

COLUMNS = [
    "Image Name", "Type", "Antibody Channel", "Pan Channel", "Analyzed", "Level", "Voxel X, Y, Z (µm)", "ExF",
    "Gel Planes (first-last)", "Gel Thickness (µm)", "Threshold", "Pan Threshold (counts)", "AB Threshold (counts)",
    "PCC (structure box)", "PCC (in structure)", "Manders M1", "Manders M2", "Coverage %",
    "SBR", "SNR", "SBR CV (3x3)", "Staining",
    "NPI at Centre", "Z½ First Face (µm)", "Z½ Last Face (µm)", "NPI Z½ First Face (µm)", "NPI Z½ Last Face (µm)",
    "Z½ First Face Pre-Expansion (µm)", "Z½ Last Face Pre-Expansion (µm)", "Pan Change Across Gel (%)", "Penetration",
    "Seam Step (%)", "Seam Score", "Vignetting (%)",
    "Axial Profile", "Fidelity Map", "Montage Checks", "Mask Zarr", "Notes",
]


def folders():
    """(analysis folder, mask folder). Optional keys in settings.ini; the defaults sit beside
    the screenshot folder and in the local app-data folder."""
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(core.SETTINGS_FILE, encoding="utf-8-sig")
    get = lambda key, default: cfg.get("paths", key, fallback="").strip() or default
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return (Path(get("analysis_folder", str(Path(core.DEFAULT_SHOTS).parent / "Zarr AB Efficiency"))),
            Path(get("mask_folder", str(local / "ZarrABEfficiency" / "masks"))))


def mask_path(mask_root, name, level):
    return mask_root / f"{name}_L{level}_mask.zarr"


# --------------------------------------------------------------------------- reading


def open_level(series_dir, info, level):
    """One pyramid level as a lazy Dask array ordered (c, z, y, x). Nothing is read yet."""
    import dask.array as da
    import zarr

    d = da.from_zarr(zarr.open(str(series_dir / info["levels"][level]), mode="r"))
    names = list(info["axes"])
    if "t" in names:
        d = d[tuple(0 if a == "t" else slice(None) for a in names)]
        names.remove("t")
    for a in "zc":                      # a flat or single-channel image still gets the axis
        if a not in names:
            d, names = d[None], [a] + names
    d = d.transpose([names.index(a) for a in "czyx"])
    # a channel recorded on a second camera is put back in register with the others
    import channel_registration
    return channel_registration.lazy(d, Path(series_dir).parent, info)


def voxel_um(info, shape):
    """(z, y, x) size of a voxel of an array of this (z, y, x) shape, in micrometres. None where
    the file holds no calibration (the z of a single plane)."""
    out = []
    for a, n in zip("zyx", shape):
        unit = core.UNIT_TO_M.get(info["units"].get(a))
        out.append(info["scale"][a] * unit * 1e6 * info["shape"].get(a, 1) / n if unit else None)
    return tuple(out)


WEAK_PAN_STRUCTURE = 0.12   # a faint pan stain still shows this much structure (see structured_channel)
WEAK_PAN_AHEAD = 3.0        # ...and this many times more than any other channel


def structured_channel(series_dir, info):
    """The channel that is a pan stain by its texture, when it is too faint to be found by brightness.

    core.pick_pan finds the pan stain as the channel whose typical pixel is bright. A weak pan
    stain fails that, but it still shows tissue: neighbouring pixels rise and fall together,
    while an antibody channel (sparse spots) and an empty channel (camera noise) do not. The
    measure is the correlation between each pixel and the one 4 pixels along, in the middle of
    the middle plane at half resolution: 0.3-0.6 for the pan stains here, under 0.05 for antibody
    and empty channels. The channel is taken when it reaches WEAK_PAN_STRUCTURE and stands
    WEAK_PAN_AHEAD times above the next; if two channels show structure, none is chosen.
    """
    import zarr

    array = zarr.open(str(series_dir / info["levels"][min(1, len(info["levels"]) - 1)]), mode="r")
    axes = info["axes"]
    n_c = array.shape[axes.index("c")] if "c" in axes else 1
    ny, nx = array.shape[axes.index("y")], array.shape[axes.index("x")]
    scores = []
    for c in range(n_c):
        index = tuple({"t": 0, "c": c, "z": array.shape[axes.index("z")] // 2 if "z" in axes else 0,
                       "y": slice(max(0, ny // 2 - 256), ny // 2 + 256), "x": slice(max(0, nx // 2 - 256), nx // 2 + 256)}[a] for a in axes)
        crop = np.asarray(array[index]).astype(np.float32)
        scores.append(float(np.corrcoef(crop[:, :-4].ravel(), crop[:, 4:].ravel())[0, 1]) if crop.std() > 0 else 0.0)
    best = int(np.argmax(scores))
    others = max(s for i, s in enumerate(scores) if i != best) if n_c > 1 else 0.0
    return best if scores[best] >= WEAK_PAN_STRUCTURE and scores[best] >= WEAK_PAN_AHEAD * max(others, 0.01) else None


def channel_roles(name, zarr_dir, out_root, info, pan=None, antibodies=None):
    """(channels, pan index or None, antibody indices, per-channel stats), decided the way the
    image list decides them: core.pick_pan, and every other channel that is not a nuclear stain."""
    images = core.read_ome_xml(zarr_dir)[0]
    n_c = info["shape"].get("c", 1)
    channels = core.describe_channels(images[0]["channels"] if images else [], info["omero"], n_c)
    cache = out_root / core.STATE_DIR / f"{name}_s0.stats.json"   # measured when the image was listed
    try:
        stats = json.loads(cache.read_text(encoding="utf-8"))["stats"]
    except (OSError, ValueError, KeyError):
        stats = core.sample_ranges(zarr_dir / "0", info)[2]
    if pan is None:
        pan = core.pick_pan(name, channels, stats)[0]
    if pan is None and len(channels) > 1:
        pan = structured_channel(zarr_dir / "0", info)       # a pan stain too faint for the brightness rule
    if pan is not None:
        channels[pan]["pan"] = True
    core.assign_colours(channels)
    if antibodies is None:
        antibodies = [c["index"] for c in channels if c["index"] != pan
                      and not (c.get("laser") and c["laser"] < core.NUCLEAR_BELOW_NM)]
    return channels, pan, antibodies, stats


def macro_level(info):
    """The coarsest level still MACRO_PX wide: enough for whole-image profiles, cheap to read."""
    return roi.pick_level(info, max(info["shape"]["x"], info["shape"]["y"]), MACRO_PX)


def gel_planes(pan_macro):
    """(first, last) plane inside the gel, from the pan stain at a coarse level.

    A stack starts and ends outside the gel. As in roi_log.project, a plane is inside when its
    mean brightness is at least SAMPLE_SIGNAL of the way from the darkest plane to the
    brightest. A flat image keeps every plane that holds data (stitched files can carry an
    all-zero plane).
    """
    import dask.array as da

    lit = da.where(pan_macro > 0, pan_macro, np.nan).astype(np.float32)
    with np.errstate(all="ignore"):
        signal = da.nanmean(lit, axis=(1, 2)).compute()      # one number per plane
    held = np.flatnonzero(~np.isnan(signal))
    if not len(held):
        raise ValueError("the pan channel is empty")
    if len(signal) < roi.STACK_MIN_PLANES:
        return int(held[0]), int(held[-1])
    lo, hi = np.nanmin(signal), np.nanmax(signal)
    inside = np.flatnonzero(np.nan_to_num(signal, nan=lo) >= lo + roi.SAMPLE_SIGNAL * (hi - lo))
    return (int(inside[0]), int(inside[-1])) if len(inside) >= 3 else (int(held[0]), int(held[-1]))


# --------------------------------------------------------------------------- preprocessing


def subtract_background(block, radius):
    """Rolling-ball background subtraction of each plane of a (z, y, x) block.

    Done the way ImageJ does it: the plane is smoothed and shrunk (keeping the minimum of
    each cell), the ball is rolled under the small image, and the background is stretched
    back and kept at or below the plane. Pixels that are NaN (stitching padding) stay NaN.
    """
    from scipy import ndimage
    from skimage import measure, restoration, transform

    shrink = 8 if radius > 100 else 4 if radius > 10 else 1
    out = np.full(block.shape, np.nan, np.float32)
    for i, plane in enumerate(block):
        ok = ~np.isnan(plane)
        if not ok.any():
            continue
        filled = np.where(ok, plane, plane[ok].min())
        small = ndimage.uniform_filter(filled, 3)
        if shrink > 1:
            small = measure.block_reduce(small, shrink, np.min, cval=float(small.max()))
        ground = restoration.rolling_ball(small, radius=max(radius / shrink, 1), num_threads=1)
        if shrink > 1:
            ground = transform.resize(ground, plane.shape, order=1, mode="edge", anti_aliasing=False)
        out[i] = np.where(ok, filled - np.minimum(ground, filled), np.nan)
    return out


def preprocess(raw, valid, radius_px):
    """Lazy background-subtracted copy of a (z, y, x) channel, NaN outside `valid`.

    Each chunk is handed its neighbours' edges, one ball radius deep, so the ball rolls
    across chunk boundaries and leaves no seam there.
    """
    import dask.array as da

    depth = int(min(math.ceil(radius_px), min(raw.shape[1:]) - 1))
    lit = da.where(valid, raw, np.nan).astype(np.float32)
    return lit.map_overlap(subtract_background, depth={0: 0, 1: depth, 2: depth}, boundary="reflect",
                           dtype=np.float32, radius=radius_px)


def dilate(block, px):
    from scipy import ndimage

    return ndimage.maximum_filter(block, size=(1, 2 * px + 1, 2 * px + 1))


def li_threshold(counts, centres):
    """Li's minimum cross-entropy threshold, iterated on a histogram."""
    c, x = counts.astype(np.float64), centres
    t = float((c * x).sum() / c.sum())
    for _ in range(500):
        low = x <= t
        back = (c[low] * x[low]).sum() / max(c[low].sum(), 1)
        fore = (c[~low] * x[~low]).sum() / max(c[~low].sum(), 1)
        if back <= 0 or fore <= back:
            break
        new = float((fore - back) / (math.log(fore) - math.log(back)))
        if abs(new - t) < 0.5 * (x[1] - x[0]):
            return new
        t = new
    return t


def histogram_levels(counts, edges, method):
    """(min, max, threshold) of a channel from its histogram. The threshold is Otsu's or Li's."""
    from skimage import filters

    used = np.flatnonzero(counts)
    if len(used) < 2:
        raise ValueError("a channel has no range of values to threshold")
    sl = slice(used[0], used[-1] + 1)
    centres = (edges[:-1] + edges[1:])[sl] / 2
    t = li_threshold(counts[sl], centres) if method == "li" else float(
        filters.threshold_otsu(hist=(counts[sl], centres)))
    return float(centres[0]), float(centres[-1]), t


# --------------------------------------------------------------------------- lazy sums


def grid_sums(q):
    """Sum of a lazy (z, y, x) quantity in each cell of a 3 x 3 grid over the field. Lazy (3, 3)."""
    import dask.array as da

    plane = q.sum(axis=0, dtype=np.float64)
    ys, xs = (np.linspace(0, n, 4).astype(int) for n in plane.shape)
    return da.stack([da.stack([plane[ys[i]:ys[i + 1], xs[k]:xs[k + 1]].sum() for k in range(3)]) for i in range(3)])


def coarse_map(q, factor):
    """A lazy (z, y, x) count summed through z and over factor x factor pixel cells: a small 2D map."""
    import dask.array as da

    plane = q.sum(axis=0, dtype=np.float32)
    if factor == 1:
        return plane
    side = factor * math.ceil(1024 / factor)
    return da.coarsen(np.sum, plane.rechunk((side, side)), {0: factor, 1: factor}, trim_excess=True)


def pearson(n, sx, sy, sxx, syy, sxy):
    if n < 2:
        return None
    var = (sxx - sx * sx / n) * (syy - sy * sy / n)
    return float((sxy - sx * sy / n) / math.sqrt(var)) if var > 0 else None


def ratio(a, b):
    return float(a) / float(b) if b else None


def shown(value, spec):
    """A number for the console; a dash when it could not be measured."""
    return "–" if value is None or (isinstance(value, float) and math.isnan(value)) else format(value, spec)


# --------------------------------------------------------------------------- stacks


def half_depth(values, zone, um, level=0.5):
    """Depth below a face at which `values` first falls under `level` of its surface peak.

    values - one per plane, ordered from that face inwards
    zone   - how many planes count as the surface
    Returns micrometres from the face (interpolated between planes), or None when the
    signal never falls that far.
    """
    with np.errstate(all="ignore"):
        if np.all(np.isnan(values[:zone])):
            return None
        start = int(np.nanargmax(values[:zone]))
    target = level * values[start]
    for k in range(start + 1, len(values)):
        if values[k] < target:
            a, b = values[k - 1], values[k]
            step = (a - target) / (a - b) if a > b and not np.isnan(a) else 1.0
            return float((k - 1 + step) * um)
    return None


def penetration(z, n, s_pan, s_ab, z_um, exf):
    """The axial profile of a stack and what it says. Returns (CSV cells, profile for the plot).

    I_pan(z) and I_ab(z) are the mean normalised intensities inside the structure mask on
    each plane, and R(z) = I_ab / I_pan. Both channels dim with depth for optical reasons
    (scattering, refractive-index mismatch), so the pan stain serves as the reference:
    R(z) cancels what is optical and keeps what is biological. NPI is R(z) as a share of
    its highest value near either face. An antibody that dims while NPI holds is optical
    loss; NPI falling at the centre is the antibody not reaching it.
    """
    with np.errstate(all="ignore"):
        i_pan = np.where(n >= MIN_PIXELS, s_pan / n, np.nan)
        i_ab = np.where(n >= MIN_PIXELS, s_ab / n, np.nan)
        r = np.where(i_pan > 0, i_ab / i_pan, np.nan)
        zone = max(1, int(round(len(z) * SURFACE_FRACTION)))
        npi = r / np.nanmax(np.concatenate([r[:zone], r[-zone:]]))
        mid = max(1, int(round(len(z) * CENTRE_FRACTION)))
        first = (len(z) - mid) // 2
        npi_centre = float(np.nanmean(npi[first:first + mid]))
        pan_change = 100 * (np.nanmean(i_pan[-zone:]) / np.nanmean(i_pan[:zone]) - 1)
    um = z_um or 1.0
    halves = {
        "Z½ First Face (µm)": half_depth(i_ab, zone, um), "Z½ Last Face (µm)": half_depth(i_ab[::-1], zone, um),
        "NPI Z½ First Face (µm)": half_depth(npi, zone, um), "NPI Z½ Last Face (µm)": half_depth(npi[::-1], zone, um),
    }
    if np.isnan(npi_centre):
        verdict = "not measured (too little structure at the centre)"
    elif npi_centre < LIMITED_BELOW:
        verdict = "limited: antibody / pan ratio falls at the centre"
    elif halves["Z½ First Face (µm)"] is not None or halves["Z½ Last Face (µm)"] is not None:
        verdict = "even: antibody dims with depth but so does the pan stain (optical)"
    else:
        verdict = "even"
    cells = {"NPI at Centre": npi_centre, "Pan Change Across Gel (%)": float(pan_change), "Penetration": verdict}
    if z_um:   # without a z calibration a depth in micrometres cannot be given
        cells.update(halves)
        if exf:
            cells["Z½ First Face Pre-Expansion (µm)"] = halves["Z½ First Face (µm)"] and halves["Z½ First Face (µm)"] / exf
            cells["Z½ Last Face Pre-Expansion (µm)"] = halves["Z½ Last Face (µm)"] and halves["Z½ Last Face (µm)"] / exf
    return cells, {"z": z, "pan": i_pan, "ab": i_ab, "npi": npi, "um": z_um, "halves": halves}


# --------------------------------------------------------------------------- montages


def tile_edges(full_px, n_px):
    """Estimated tile boundaries along one axis, in pixels of a profile n_px long."""
    tiles = max(2, round(full_px / TILE_STEP_PX))
    return [n_px * k / tiles for k in range(tiles + 1)]


def montage_checks(pan_macro, offset, info):
    """Stitching and illumination checks of a montage, from the pan stain at a coarse level.

    The pan stain is everywhere, so its mean along each row and each column is flat unless
    the optics or the stitching say otherwise.
      seam step   the largest jump between neighbouring rows/columns near a tile boundary,
                  as a % of the mean signal, and as a multiple of the usual jump (score)
      vignetting  how much dimmer the outer tenth of a tile is than its middle, averaged
                  over the tiles: illumination fall-off, not staining
    `offset` is the camera's dark level, taken off first so the percentages are of signal.
    The tile grid is estimated (see TILE_STEP_PX); the zarr does not record it.
    """
    import dask
    import dask.array as da

    lit = da.where(pan_macro > 0, pan_macro, np.nan).astype(np.float32)
    with np.errstate(all="ignore"):
        along_x, along_y = dask.compute(da.nanmean(lit, axis=(0, 1)), da.nanmean(lit, axis=(0, 2)))
    out = {"profiles": {}, "step": 0.0, "score": 0.0}
    shade = []
    for axis, profile in (("x", along_x), ("y", along_y)):
        p = profile.astype(np.float64) - offset
        edges = tile_edges(info["shape"][axis], len(p))
        out["profiles"][axis] = (p, edges)
        jump = np.abs(np.diff(p))
        usual = 1.4826 * np.nanmedian(np.abs(jump - np.nanmedian(jump))) or np.nan
        width = (edges[1] - edges[0])
        for e in edges[1:-1]:
            near = jump[max(0, int(e - 0.1 * width)):int(e + 0.1 * width) + 1]
            if near.size and not np.all(np.isnan(near)):
                out["step"] = max(out["step"], float(100 * np.nanmax(near) / np.nanmean(p)))
                out["score"] = max(out["score"], float(np.nanmax(near) / usual))
        for a, b in zip(edges[:-1], edges[1:]):
            tile = p[int(a):int(b)]
            rim = max(1, len(tile) // 10)
            with np.errstate(all="ignore"):
                middle = np.nanmean(tile[int(len(tile) * 0.3):int(len(tile) * 0.7)])
                outer = np.nanmean(np.concatenate([tile[:rim], tile[-rim:]]))
            if middle > 0 and not np.isnan(outer):
                shade.append(100 * (1 - outer / middle))
    out["vignetting"] = float(np.mean(shade)) if shade else None
    return out


# --------------------------------------------------------------------------- pictures

INK, MUTED, SURFACE = "#E8E8E8", "#9A9A9A", "#111111"   # drawn on black, like the Neuroglancer views


def figure(*args, **kwargs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Nature asks for a sans-serif face in figures, Helvetica or Arial; Arial ships with Windows
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = ["Arial", "Helvetica", "DejaVu Sans"]
    matplotlib.rcParams["pdf.fonttype"] = 42   # keep text as text if a figure is ever saved as PDF
    fig, axes = plt.subplots(*args, facecolor=SURFACE, **kwargs)
    for ax in np.atleast_1d(axes).ravel():
        ax.set_facecolor(SURFACE)
        ax.tick_params(colors=MUTED, labelsize=9)
        for side, spine in ax.spines.items():
            spine.set_color("#444444")
            spine.set_visible(side in ("left", "bottom"))
    return fig, axes


def save(fig, png, dpi=150):
    import matplotlib.pyplot as plt

    fig.savefig(png, dpi=dpi, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print(f"   {png.name}")


def colour_of(channel):
    return tuple(v / 255 for v in channel["rgb"])


def plot_profile(png, name, pan, ab, prof):
    """Axial profile per plane: mean pan and antibody intensity (left axis) and NPI (right axis).

    Each intensity is drawn as a share of its own brightest plane. Min-max normalisation
    divides by the single brightest pixel of a channel, which leaves the two means on very
    different scales, and it is the shape of each curve that is read here.
    """
    fig, left = figure(figsize=(8, 4.6))
    right = left.twinx()
    right.set_facecolor("none")
    for side, spine in right.spines.items():
        spine.set_color("#444444")
        spine.set_visible(side == "right")
    z = prof["z"]
    ratio_colour = (1.0, 0.6, 0.0) if ab["color_name"] not in ("orange", "yellow") else (0.0, 1.0, 1.0)
    with np.errstate(all="ignore"):
        i_pan, i_ab = prof["pan"] / np.nanmax(prof["pan"]), prof["ab"] / np.nanmax(prof["ab"])
    lines = [
        left.plot(z, i_pan, color="white", lw=2, label=f"pan  {pan['label']}  (left)")[0],
        left.plot(z, i_ab, color=colour_of(ab), lw=2, label=f"antibody  {ab['label']}  (left)")[0],
        right.plot(z, prof["npi"], color=ratio_colour, lw=2, ls="--", label="NPI = antibody / pan, surface = 1  (right)")[0],
    ]
    # where the antibody falls to half its surface value, in from either face
    um = prof["um"]
    for key, origin, sign in (("Z½ First Face (µm)", z[0], 1), ("Z½ Last Face (µm)", z[-1], -1)):
        depth = prof["halves"][key]
        if depth is not None and um:
            at = origin + sign * depth / um
            left.axvline(at, color=MUTED, lw=1, ls=":")
            left.annotate(f"Z½ {depth:.0f} µm", (at, 1), xycoords=("data", "axes fraction"), xytext=(3, -12),
                          textcoords="offset points", color=INK, fontsize=9)
    left.set_xlabel("slice (0 = first plane, as in Neuroglancer)", color=INK)
    left.set_ylabel("mean intensity in the structure mask\n(brightest plane = 1)", color=INK)
    right.set_ylabel("NPI", color=INK)
    right.tick_params(colors=MUTED, labelsize=9)
    left.set_ylim(0, 1.05)
    right.set_ylim(0, max(1.05, float(np.nanmax(prof["npi"])) * 1.05))
    left.grid(color="#2A2A2A", lw=0.6)
    if um:
        top = left.secondary_xaxis("top", functions=(lambda s: (s - z[0]) * um, lambda d: d / um + z[0]))
        top.set_xlabel("depth below the first face (µm, as imaged)", color=INK)
        top.tick_params(colors=MUTED, labelsize=9)
    left.legend(handles=lines, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=1, frameon=False,
                labelcolor=INK, fontsize=9)
    fig.suptitle(name, color=INK, fontsize=11, y=1.04)
    save(fig, png)


def plot_fidelity(png, name, pan, ab, structure, missing, um_px):
    """Where the structure is and is not labelled: green = pan with antibody, magenta = pan
    without it (unlabelled structure). Brightness is how much of the cell is structure."""
    from matplotlib.patches import Patch

    full = max(float(np.percentile(structure, 99.5)), 1.0)   # a few solid cells must not dim the rest
    labelled, bare = (structure - missing) / full, missing / full
    rgb = np.clip(np.dstack([bare, labelled, bare]), 0, 1)          # magenta + green
    h, w = structure.shape
    fig, ax = figure(figsize=(7, 7 * h / w + 0.6))
    ax.imshow(rgb, extent=(0, w * um_px, h * um_px, 0) if um_px else None, interpolation="nearest")
    ax.set_xlabel("µm, as imaged" if um_px else "pixels", color=INK)
    ax.legend(handles=[Patch(color=(0, 1, 0), label=f"structure labelled by {ab['label']}"),
                       Patch(color=(1, 0, 1), label=f"structure without it (pan {pan['label']} only)")],
              loc="upper center", bbox_to_anchor=(0.5, -0.1), frameon=False, labelcolor=INK, fontsize=9)
    ax.set_title(f"{name}\nfidelity map, all gel planes", color=INK, fontsize=11)
    save(fig, png)


def plot_montage(png, name, pan, ab, checks, sbr_grid, coverage_grid):
    """Montage checks: pan profiles across the field with the tile boundaries, and the
    3 x 3 grids of signal-to-background and coverage."""
    fig, axes = figure(2, 2, figsize=(10, 7.5))
    for ax, axis in zip(axes[0], "xy"):
        p, edges = checks["profiles"][axis]
        ax.plot(np.arange(len(p)), p, color="white", lw=1.5)
        for e in edges[1:-1]:
            ax.axvline(e, color=MUTED, lw=1, ls=":")
        ax.set_title(f"pan {pan['label']}: mean along {axis}   (dotted = estimated tile seams)", color=INK, fontsize=10)
        ax.set_xlabel(f"{axis} (pixels at the coarse level)", color=INK, fontsize=9)
        ax.set_ylabel("counts above dark level", color=INK, fontsize=9)
        ax.grid(color="#2A2A2A", lw=0.6)
    for ax, grid, title, fmt in ((axes[1][0], sbr_grid, f"{ab['label']}: signal-to-background", "{:.2f}"),
                                 (axes[1][1], coverage_grid, f"{ab['label']}: coverage of structure (%)", "{:.1f}")):
        shown = np.ma.masked_invalid(grid)
        ax.imshow(shown, cmap="Greys_r", vmin=0, vmax=float(shown.max()) if shown.count() else 1)
        for (i, k), v in np.ndenumerate(grid):
            if not np.isnan(v):
                dark = v > 0.6 * float(shown.max())
                ax.text(k, i, fmt.format(v), ha="center", va="center", fontsize=11, color="black" if dark else INK)
        ax.set_title(title, color=INK, fontsize=10)
        ax.set_xticks([])
        ax.set_yticks([])
    fig.suptitle(name, color=INK, fontsize=11)
    fig.tight_layout()
    save(fig, png)


# --------------------------------------------------------------------------- one sample


def analyse(name, zarr_dir, out_root, folder, mask_root, work, args):
    """Measure one sample. Returns one CSV row (a dict) per antibody channel."""
    import dask
    import dask.array as da
    import zarr
    from dask.diagnostics import ProgressBar

    series = zarr_dir / "0"
    info = core.read_series(series)
    kind = roi.classify(info)
    channels, pan, antibodies, stats = channel_roles(name, zarr_dir, out_root, info, args.pan, args.antibody)
    if pan is None:
        raise ValueError("no pan stain found (name it with --pan N to use channel N as the reference)")
    if not antibodies:
        raise ValueError("no antibody channel beside the pan stain")
    level = min(args.level, len(info["levels"]) - 1)
    raw = open_level(series, info, level)
    macro = open_level(series, info, max(level, macro_level(info) if args.macro_level is None else args.macro_level))
    z0, z1 = gel_planes(macro[pan])
    raw = raw[:, z0:z1 + 1]
    z_um, y_um, x_um = voxel_um(info, raw.shape[1:])
    radius_px = args.radius_um / x_um if x_um else 50.0
    dilate_px = max(1, int(round(args.dilate_um / x_um))) if x_um else 7
    labels = ", ".join(f"{channels[c]['label']}" for c in antibodies)
    print(f"   {kind}; pan = {channels[pan]['label']}, antibody = {labels}; gel planes {z0}-{z1}; "
          f"level {level} ({raw.shape[3]} x {raw.shape[2]} x {raw.shape[1]}); ball {radius_px:.0f} px")

    # A camera never reads 0: exact zeros are stitching padding, and are left out throughout.
    valid = raw[pan] > 0
    used = [pan] + antibodies
    clean = {c: preprocess(raw[c], valid, radius_px) for c in used}

    # ---- pass 1: background-subtracted channels to local scratch, and their histograms
    integer = np.issubdtype(raw.dtype, np.integer)
    top = float(np.iinfo(raw.dtype).max) + 1 if integer else float(da.max(raw[used]).compute()) or 1.0
    bins = int(top) if integer else FLOAT_BINS
    hists = {c: da.histogram(clean[c], bins=bins, range=(0, top))[0] for c in used}
    chunk = (1,) + tuple(int(c[0]) for c in raw.chunks[2:])
    need = len(used) * 4 * int(np.prod(raw.shape[1:]))
    stores = []
    if shutil.disk_usage(work).free > 1.5 * need:
        for c in used:
            target = zarr.open(str(work / f"c{c}.zarr"), mode="w", shape=raw.shape[1:], chunks=chunk, dtype="f4")
            stores.append(da.store(clean[c].rechunk(chunk), target, compute=False, lock=False))
            clean[c] = da.from_zarr(target)
    else:
        print(f"   only {shutil.disk_usage(work).free / 1e9:.0f} GB free in {work}: not keeping the "
              "background-subtracted copy, so it is worked out again in each pass (slower)")
    print("   pass 1 of 3: background subtraction")
    with ProgressBar(dt=2):
        hists = dask.compute(hists, *stores)[0]
    edges = np.linspace(0, top, bins + 1)
    levels = {c: histogram_levels(hists[c], edges, args.threshold) for c in used}

    def normalised(c):    # min-max: (I - Imin) / (Imax - Imin)
        lo, hi, _ = levels[c]
        return (clean[c] - lo) / (hi - lo)

    def mask(c):          # the automatic threshold, on the normalised channel
        lo, hi, t = levels[c]
        return normalised(c) > (t - lo) / (hi - lo)

    # ---- pass 2: masks, and every sum the metrics are made of
    n_pan, m_pan = normalised(pan), mask(pan)     # m_pan is the True Structure Mask
    away = valid & ~m_pan.map_overlap(dilate, depth={0: 0, 1: dilate_px, 2: dilate_px}, boundary="none",
                                      dtype=bool, px=dilate_px)
    factor = max(1, math.ceil(max(raw.shape[2:]) / MAP_PX))
    mask_zarr = mask_path(mask_root, name, level)
    mask_zarr.parent.mkdir(parents=True, exist_ok=True)
    mask_chunk = tuple(min(a, b) for a, b in zip(MASK_CHUNK, raw.shape[1:]))
    target = zarr.open(str(mask_zarr), mode="w", shape=raw.shape[1:], chunks=mask_chunk, dtype="u1")
    target.attrs.update({"image": name, "level": level, "pan_channel": pan, "z_first": z0,
                         "scale_um_zyx": [z_um or 1.0, y_um or 1.0, x_um or 1.0]})
    todo = {
        "store": da.store(m_pan.astype("u1").rechunk(mask_chunk), target, compute=False, lock=False),
        "n": m_pan.sum(axis=(1, 2)), "pan": da.where(m_pan, n_pan, 0).sum(axis=(1, 2), dtype=np.float64),
        "pan2": da.where(m_pan, n_pan * n_pan, 0).sum(dtype=np.float64),
        "rows": m_pan.any(axis=(0, 2)), "cols": m_pan.any(axis=(0, 1)),
        "structure": coarse_map(m_pan, factor), "n_fg": grid_sums(m_pan), "n_bg": grid_sums(away), "ab": {},
    }
    for c in antibodies:
        n_ab, m_ab = normalised(c), mask(c)
        both = m_pan & m_ab
        counts = raw[c].astype(np.float64)        # signal and background are compared in camera counts
        todo["ab"][c] = {
            "z": da.where(m_pan, n_ab, 0).sum(axis=(1, 2), dtype=np.float64),
            "ab2": da.where(m_pan, n_ab * n_ab, 0).sum(dtype=np.float64),
            "cross": da.where(m_pan, n_ab * n_pan, 0).sum(dtype=np.float64),
            "n_ab": m_ab.sum(), "both": grid_sums(both),
            "pan_both": da.where(both, n_pan, 0).sum(dtype=np.float64),
            "ab_both": da.where(both, n_ab, 0).sum(dtype=np.float64),
            "ab_own": da.where(m_ab, n_ab, 0).sum(dtype=np.float64),
            "fg": grid_sums(da.where(m_pan, counts, 0)),
            "bg": grid_sums(da.where(away, counts, 0)), "bg2": grid_sums(da.where(away, counts * counts, 0)),
            "missing": coarse_map(m_pan & ~m_ab, factor),
        }
    print("   pass 2 of 3: masks and metrics")
    with ProgressBar(dt=2):
        got = dask.compute(todo)[0]
    if not got["n"].sum():
        raise ValueError("the structure mask is empty")

    # ---- pass 3: Pearson's r inside the bounding box of the structure
    zs, ys, xs = (np.flatnonzero(v) for v in (got["n"], got["rows"], got["cols"]))
    box = tuple(slice(int(v[0]), int(v[-1]) + 1) for v in (zs, ys, xs))
    b_pan = normalised(pan)[box]
    boxed = {"n": valid[box].sum(), "x": da.nansum(b_pan, dtype=np.float64),
             "xx": da.nansum(b_pan * b_pan, dtype=np.float64), "ab": {}}
    for c in antibodies:
        b_ab = normalised(c)[box]
        boxed["ab"][c] = {"y": da.nansum(b_ab, dtype=np.float64), "yy": da.nansum(b_ab * b_ab, dtype=np.float64),
                          "xy": da.nansum(b_ab * b_pan, dtype=np.float64)}
    print("   pass 3 of 3: correlation in the structure's bounding box")
    with ProgressBar(dt=2):
        boxed = dask.compute(boxed)[0]

    checks = montage_checks(macro[pan, z0:z1 + 1], stats[pan]["p1"], info) if "montage" in kind else None
    voxel = ", ".join(f"{v:.4g}" if v else "–" for v in (x_um, y_um, z_um))
    n_structure = float(got["n"].sum())
    rows = []
    for c in antibodies:
        g, b, ch = got["ab"][c], boxed["ab"][c], channels[c]
        tag = f"c{c}"
        with np.errstate(all="ignore"):
            fg = np.where(got["n_fg"] >= MIN_PIXELS, g["fg"] / got["n_fg"], np.nan)
            bg = np.where(got["n_bg"] >= MIN_PIXELS, g["bg"] / got["n_bg"], np.nan)
            sbr_grid = fg / bg
            coverage_grid = np.where(got["n_fg"] >= MIN_PIXELS, 100 * g["both"] / got["n_fg"], np.nan)
            cv = float(np.nanstd(sbr_grid) / np.nanmean(sbr_grid)) if np.isfinite(sbr_grid).sum() >= 2 else None
        mu_fg, mu_bg = ratio(g["fg"].sum(), n_structure), ratio(g["bg"].sum(), got["n_bg"].sum())
        sd_bg = math.sqrt(max(g["bg2"].sum() / got["n_bg"].sum() - mu_bg ** 2, 0)) if mu_bg is not None else None
        row = {
            "Image Name": name, "Type": kind, "Antibody Channel": ch["label"], "Pan Channel": channels[pan]["label"],
            "Analyzed": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "Level": level, "Voxel X, Y, Z (µm)": voxel,
            "ExF": args.exf, "Gel Planes (first-last)": f"{z0}-{z1}",
            "Gel Thickness (µm)": (z1 - z0 + 1) * z_um if z_um and kind.startswith("stack") else None,
            "Threshold": args.threshold, "Pan Threshold (counts)": levels[pan][2], "AB Threshold (counts)": levels[c][2],
            "PCC (structure box)": pearson(float(boxed["n"]), boxed["x"], b["y"], boxed["xx"], b["yy"], b["xy"]),
            "PCC (in structure)": pearson(n_structure, got["pan"].sum(), g["z"].sum(), got["pan2"], g["ab2"], g["cross"]),
            "Manders M1": ratio(g["pan_both"], got["pan"].sum()), "Manders M2": ratio(g["ab_both"], g["ab_own"]),
            "Coverage %": 100 * float(g["both"].sum()) / n_structure,
            "SBR": ratio(mu_fg, mu_bg) if mu_fg is not None else None,
            "SNR": (mu_fg - mu_bg) / sd_bg if sd_bg else None,
            "SBR CV (3x3)": cv, "Staining": None if cv is None else "UNEVEN" if cv > CV_FLAG else "even",
            "Mask Zarr": str(mask_zarr),
            "Notes": f"ball {args.radius_um:g} µm ({radius_px:.0f} px); background {args.dilate_um:g} µm clear of the mask",
        }
        if mu_bg is None:
            # dense structure at high magnification: the mask and its margin leave nothing outside
            row["Notes"] = "NO BACKGROUND REGION: the structure mask and its margin fill the field, so no SBR or SNR; " + row["Notes"]
            print(f"   {ch['label']}: no background region outside the structure mask; SBR and SNR left blank")
        elif row["SNR"] is None or row["SNR"] < LOW_SNR:
            # Either the antibody did not label, or it labels sparse spots that the mean over the
            # whole structure dilutes. A threshold on a channel of pure noise just splits the noise.
            row["Notes"] = (f"ANTIBODY NOT ABOVE BACKGROUND in the structure (SNR below {LOW_SNR:g}): check the image "
                            "before trusting coverage, Manders or penetration; ") + row["Notes"]
            print(f"   {ch['label']}: not above background in the structure (SNR below {LOW_SNR:g}); see Notes")
        if kind.startswith("stack"):
            cells, prof = penetration(np.arange(z0, z1 + 1), got["n"].astype(np.float64), got["pan"], g["z"], z_um, args.exf)
            row.update(cells)
            png = folder / f"{name}_{tag}_axial-profile.png"
            plot_profile(png, name, channels[pan], ch, prof)
            row["Axial Profile"] = str(png)
        png = folder / f"{name}_{tag}_fidelity-map.png"
        plot_fidelity(png, name, channels[pan], ch, got["structure"], g["missing"], x_um * factor if x_um else None)
        row["Fidelity Map"] = str(png)
        if checks:
            row.update({"Seam Step (%)": checks["step"], "Seam Score": checks["score"], "Vignetting (%)": checks["vignetting"]})
            png = folder / f"{name}_{tag}_montage-checks.png"
            plot_montage(png, name, channels[pan], ch, checks, sbr_grid, coverage_grid)
            row["Montage Checks"] = str(png)
        rows.append(row)
        print(f"   {ch['label']}: PCC {shown(row['PCC (structure box)'], '.3f')}, M1 {shown(row['Manders M1'], '.3f')}, "
              f"M2 {shown(row['Manders M2'], '.3f')}, coverage {row['Coverage %']:.1f}%, "
              f"SBR {shown(row['SBR'], '.2f')}, SNR {shown(row['SNR'], '.1f')}"
              + (f", penetration {row['Penetration']}" if row.get("Penetration") else ""))
    return rows


# --------------------------------------------------------------------------- summary file


def tidy(value):
    if isinstance(value, (float, np.floating)):
        return "" if math.isnan(value) else f"{value:.4g}"
    return "" if value is None else value


def measured(path):
    """(image name, antibody channel, level) of every row already in the summary."""
    if not path.is_file():
        return set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {(r.get("Image Name"), r.get("Antibody Channel"), r.get("Level")) for r in csv.DictReader(f)}


def append_rows(path, rows):
    """Append to the summary CSV. A summary written with different columns is set aside first."""
    if path.is_file():
        with open(path, newline="", encoding="utf-8-sig") as f:
            if next(csv.reader(f), None) != COLUMNS:
                path.rename(path.with_name(f"{path.stem} (before {dt.datetime.now():%Y-%m-%d %H%M%S}){path.suffix}"))
    fresh = not path.is_file()
    with open(path, "a", newline="", encoding="utf-8-sig" if fresh else "utf-8") as f:
        writer = csv.DictWriter(f, COLUMNS)
        if fresh:
            writer.writeheader()
        writer.writerows({k: tidy(r.get(k)) for k in COLUMNS} for r in rows)


def run(args):
    import dask

    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    base, mask_root = folders()
    if args.out:
        base = Path(args.out)
    if not out_root.is_dir():
        sys.exit(f"Cannot reach the zarr folder {out_root}")
    base.mkdir(parents=True, exist_ok=True)
    summary = base / CSV_NAME
    print(f"Zarr folder : {out_root}\nAnalysis    : {base}\nMasks       : {mask_root}\n")
    everything = roi.samples(out_root)
    text, exf = args.text, args.exf
    if text is None and sys.stdin.isatty():
        text = roi.ask(f"Which samples? Enter = all {len(everything)}, or type text their names must contain: ")
        if exf is None:
            answer = roi.ask("Expansion factor, for depths before expansion (Enter = leave those blank): ")
            try:
                exf = float(answer) if answer else None
            except ValueError:
                sys.exit("The expansion factor must be a number, such as 4.2.")
    args.exf = exf
    chosen = [s for s in everything if (text or "").lower() in s[0].lower()]
    if not chosen:
        sys.exit("No sample name contains that text.")
    done = {r[0] for r in measured(summary) if r[2] == str(args.level)}
    if not args.redo and any(s[0] in done for s in chosen):
        print(f"{sum(s[0] in done for s in chosen)} of these are already in the summary and are skipped "
              "(--redo measures them again and adds new rows).")
        chosen = [s for s in chosen if s[0] not in done]
    print(f"{len(chosen)} sample(s) to measure.\n")

    scratch = Path(core.DEFAULT_WORK) if core.DEFAULT_WORK else Path(tempfile.gettempdir())
    failed = 0
    with dask.config.set(scheduler="threads", num_workers=args.workers or min(8, os.cpu_count() or 4)):
        for n, (name, zarr_dir) in enumerate(chosen, start=1):
            print(f"[{n}/{len(chosen)}] {name}")
            folder = base / name
            work = Path(tempfile.mkdtemp(prefix="ab_efficiency_", dir=scratch))
            try:
                folder.mkdir(exist_ok=True)
                rows = analyse(name, zarr_dir, out_root, folder, mask_root, work, args)
                try:
                    append_rows(summary, rows)
                except PermissionError:
                    held = summary.with_name(f"{summary.stem} (pending {dt.datetime.now():%Y%m%d-%H%M%S}).csv")
                    append_rows(held, rows)
                    print(f"   {CSV_NAME} is open in Excel: these rows are in {held.name} instead")
            except Exception as e:
                failed += 1
                print(f"   SKIPPED: {e}")
            finally:
                shutil.rmtree(work, ignore_errors=True)
            print()
    print(f"Done. Summary: {summary}" + (f"   ({failed} sample(s) skipped, reasons above)" if failed else ""))
    return 1 if failed and failed == len(chosen) else 0


def main():
    p = argparse.ArgumentParser(description="Antibody labelling efficiency against the pan stain, per zarr image.")
    p.add_argument("text", nargs="?", help="measure the samples whose names contain this (omit to be asked)")
    p.add_argument("--level", type=int, default=0, help="pyramid level to measure on; 0 = full resolution (default)")
    p.add_argument("--macro-level", type=int, help="level for gel faces, seams and shading (default: about 1000 px wide)")
    p.add_argument("--radius-um", type=float, default=RADIUS_UM, help=f"rolling-ball radius in µm as imaged (default {RADIUS_UM:g})")
    p.add_argument("--dilate-um", type=float, default=DILATE_UM, help=f"gap between the mask and the background, µm (default {DILATE_UM:g})")
    p.add_argument("--threshold", choices=("otsu", "li"), default="otsu", help="automatic threshold for both masks")
    p.add_argument("--exf", type=float, help="expansion factor, to also give depths before expansion")
    p.add_argument("--pan", type=int, help="use this channel (0 = first) as the pan stain instead of detecting it")
    p.add_argument("--antibody", type=int, action="append", help="measure this channel (repeatable) instead of every antibody")
    p.add_argument("--redo", action="store_true", help="measure samples already in the summary again")
    p.add_argument("--out", help="analysis folder for this run, instead of the one in settings.ini")
    p.add_argument("--workers", type=int, help="chunks worked on at once (default 8)")
    p.add_argument("--napari", metavar="NAME", help="open this sample and its True Structure Mask in napari")
    args = p.parse_args()
    if args.napari:
        return open_in_napari(args.napari, args.level)
    return run(args)


# --------------------------------------------------------------------------- a look in napari


def open_in_napari(name, level=0):
    """Raw channels (whole pyramid, lazy) with the True Structure Mask on top, for a quick check."""
    import dask.array as da
    import napari
    import zarr

    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    series = out_root / f"{name}.zarr" / "0"
    info = core.read_series(series)
    channels, pan = channel_roles(name, series.parent, out_root, info)[:2]
    pyramid = [open_level(series, info, k) for k in range(len(info["levels"]))]      # (c, z, y, x) per level
    saved = zarr.open(str(mask_path(folders()[1], name, level)), mode="r")
    mask, scale, z_first = da.from_zarr(saved), saved.attrs["scale_um_zyx"], saved.attrs["z_first"]

    viewer = napari.Viewer(title=name)
    viewer.add_image(pyramid, channel_axis=0, multiscale=True, scale=[v or 1.0 for v in voxel_um(info, pyramid[0].shape[1:])],
                     name=[c["label"] for c in channels], blending="additive",
                     colormap=["gray" if c["index"] == pan else {"orange": "bop orange"}.get(c["color_name"], c["color_name"])
                               for c in channels])
    viewer.add_labels(mask, name="True Structure Mask", scale=scale, translate=(z_first * scale[0], 0, 0), opacity=0.4)
    napari.run()


if __name__ == "__main__":
    sys.exit(main())
