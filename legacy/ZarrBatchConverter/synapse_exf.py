"""
Find synapses in the pan stain and measure the expansion factor from them.

  synapse_exf.py [text]     go through the samples whose names contain the text

Follows M'Saad, Cairns et al., Nat. Biotechnol. 2025 (pan-ExM-t): the linear expansion
factor is the distance between the presynaptic dense projections (DPs) and the postsynaptic
density (PSD), centre to centre along the trans-synaptic axis, divided by its known size
before expansion, 82 nm. The paper drew the line profiles by hand in FIJI; here the
synapses are found and the profiles drawn by the program, and a person accepts or rejects
each one in the log. What the program does, step by step (every number is a setting below):

  1. Each plane of the pan channel is lightly smoothed (Gaussian, SMOOTH_PX; the paper
     smoothed 0.4-1.5 px). Bright, thin, straight-ish bars are picked out with a ridge
     filter (the Hessian's most negative eigenvalue at the scale of a PSD's thickness) and
     kept when they are as long as a PSD.
  2. Across each bar, an intensity profile is taken along the bar's normal and averaged
     along the middle of the bar. A synapse seen side-on shows two peaks, PSD and DPs.
     The second peak is looked for at 82 nm x EXF_SEARCH, a deliberately wide window, so
     that the answer is not built in.
  3. Two Gaussians on a flat baseline are fitted to the profile, to decide whether this is
     a synapse and where its peaks are. The distance is then read as the paper read it:
     peak to peak on a line LINE_PX pixels thick, divided by 81.9 nm. The fitted distance
     is kept beside it. A profile is kept only when the fit is good (FIT_R2), the dip
     between the peaks is real (MIN_DIP), the peaks are of comparable height (PEAK_RATIO)
     and the brighter one is as bright as a PSD (PSD_RANK), which leaves out pairs of membranes.
  4. The antibody channel is measured over the same footprint. A synapse is antibody-
     positive when its brightest smoothed antibody pixel is AB_SIGMAS noise-widths above
     the plane's median. Positive synapses are listed first, when the antibody is synaptic.
  5. The best synapses of each sample get a picture (pan, antibody, overlay, profile with
     the fit) and a row in "Synapse Log.xlsx". Type Y or N in its Accept column and run
     this again: the Summary sheet then also gives the expansion factor from the accepted
     synapses alone. Every synapse that passed, logged or not, is in <sample>_synapses.csv.

Planes are independent: each is read and worked through in tiles, so no image is loaded whole.
"""

import argparse
import configparser
import csv
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

import ab_efficiency as eff
import synapse_structure as structure
import convert_to_zarr as core
import roi_log as roi

# --------------------------------------------------------------------------- settings

REF_NM = 81.9            # DP-PSD distance before expansion (M'Saad, Cairns et al. 2025, Supplementary Methods)
LINE_PX = 2              # thickness of the line the distance is read from, as in the paper
MIN_PER_FOV = 3          # a field of view needs this many synapses to count towards the mean across fields
BOOTSTRAPS = 2000        # resamples behind the 95% confidence interval of a sample's expansion factor
# Synapse classes (see classify):
ROUGHER_BY = 1.2         # one density must be this many times rougher along its length to be called the DP row
TYPE2_BRIGHTER_BY = 1.3  # ...and that DP row this many times brighter than the PSD for a type II call
GAP_BELOW = 0.5          # a perforation: the PSD falls below this share of its height along its length...
GAP_NM = (20.0, 250.0)   # ...over a stretch this long, with PSD at least SEGMENT_NM long on both sides
SEGMENT_NM = 40.0
EXF_NOMINAL = 20.0       # only sets the size of the filters; the measurement does not use it
EXF_SEARCH = (12.0, 30.0)  # the second peak is looked for at 82 nm times this range
EXF_EXPECTED = (16.0, 24.0)  # the expected range (user, 7 Oct 2026); values outside it are marked, not dropped
SMOOTH_PX = 1.0          # Gaussian smoothing of both channels before anything is measured
PSD_SIGMA_NM = 15.0      # scale of the ridge filter: half the thickness of a PSD
PSD_LENGTH_NM = (120.0, 900.0)   # a bar this long can be a PSD
RIDGE_PERCENTILE = 97.0  # bars are the pixels whose ridge strength is above this share of the plane
BRIGHT_PERCENTILE = 80.0 # ...and a PSD is among the brighter things in the plane
PROFILE_HALF_NM = 260.0  # the profile runs this far either side of the bar
AVERAGE_HALF_NM = 90.0   # ...and is averaged along the bar over at most this far either side of its middle
FIT_R2 = 0.90            # the two-Gaussian fit must explain this share of the profile
MIN_DIP = 0.20           # the trough must fall this share of the smaller peak's height
PEAK_RATIO = (0.25, 4.0) # second peak's height as a share of the first
# The brighter of the two densities must outshine this share of the plane. Two cell membranes
# lying side by side give the same double peak as a synapse but are dim; a PSD is among the
# brightest things in pan-ExM. Set from GluA2-positive synapses (260831_WT4_GluA2ms, 3x3):
# 95% of them ranked above 0.996, against a median of 0.978 for the other double peaks.
PSD_RANK = 0.995
AB_SIGMAS = 6.0          # antibody-positive: this many noise-widths above the plane's median
TOP_N = 150              # synapses pictured and logged per sample (was 60 until 2026-10-28; --top overrides)
# Antibody labelling, measured at the synapses (see antibody_numbers):
ON_SYNAPSE_X = 2.0       # a punctum this many nominal DP-PSD distances from a synapse's centre is "on" it
SPOT_MIN_PX = 3          # a punctum is at least this many connected pixels above the threshold
# Resolution, from the width of isolated antibody puncta (see spot_width):
SPOT_ALONE_PX = 12       # ...with no other punctum this close
SPOT_FITS = 60           # at most this many puncta are fitted per tile, the brightest first
SPOT_SMALLEST = 10       # the resolution is this percentile of the widths: the smallest puncta, not the typical one
MIN_WIDTHS = 20          # fewer fitted puncta than this give no resolution
MIN_FOR_EXF = 10         # fewer antibody-positive synapses than this cannot carry a sample's ExF
MIN_ACCEPTED = 5         # ...and fewer accepted ones than this cannot either
PLANE_GAP_UM = 4.0       # planes closer than this would show the same synapses twice; they are skipped
TILE_PX = 2048
LOG_NAME = "Synapse Log.xlsx"
HEADER_ROW = 3

# Antibodies recognised in file names: name -> (label, side of the synapse, or None when not synaptic)
ANTIBODIES = {
    "homer1": ("Homer1", "post"), "shank2": ("Shank2", "post"), "psd95": ("PSD-95", "post"),
    "nr1": ("NR1", "post"), "glua1": ("GluA1", "post"), "glua2": ("GluA2", "post"), "glua3": ("GluA3", "post"),
    "glua4": ("GluA4", "post"), "mglur5": ("mGluR5", "post"), "bassoon": ("Bassoon", "pre"),
    "synaptophysin": ("Synaptophysin", "pre"), "cx43": ("Cx43", None), "gfap": ("GFAP", None), "pff1": ("PFF1", None),
}

COLUMNS = [
    ("#", 6), ("Sample", 40), ("Slice", 7), ("X (px)", 9), ("Y (px)", 9), ("Antibody", 12), ("AB Signal (σ)", 13),
    ("AB+", 6), ("AB2 Signal (σ)", 13), ("AB2+", 6), ("AB1–AB2 (µm, as imaged)", 12), ("DP–PSD (µm, as imaged)", 14), ("ExF (peak to peak)", 12), ("ExF (Gaussian fit)", 12), ("In 16–24", 9), ("Class", 18), ("PSD Named By", 11), ("Example Score", 9), ("Synapse ID", 22), ("Fit R²", 8), ("Dip", 7),
    ("Peak Ratio", 10), ("Accept", 9), ("Notes", 40), ("Reviewed Class", 18),
    ("Manual Line", 24), ("P2P Manual (µm)", 12), ("ExF (manual line)", 12), ("Picture", 12), ("Neuroglancer", 13),
]
HEADERS = [c[0] for c in COLUMNS]


# --------------------------------------------------------------------------- which samples


def antibodies_in(name):
    """Antibodies named in a file name, as [(label, side)]; 'GluA2ms' is GluA2 raised in mouse."""
    found = []
    for token in re.split(r"[_\s-]+", name.lower()):
        for key, value in ANTIBODIES.items():
            if token.startswith(key) and value not in found:
                found.append(value)
    return found


NEVER = ("pff", "tetraspec")   # left out of every analysis: PFF samples (user, 7 Oct 2026) and bead images


def ignored(name):
    """Why a sample is left out, or ''. Left out: 'SZ' or 'control' in the name, or two numbers
    joined by an underscore (a repeat such as Sample1_2) unless the first number is the end
    of an antibody's name (Homer1_2 stays) or of a grid size (3x3_1 stays: 3x3 says the image
    is the 2D overview montage, not a repeat)."""
    if any(word in name.lower() for word in NEVER):
        return "left out of all testing"
    if re.search(r"(?<![A-Za-z])SZ(?![A-Za-z])", name):
        return "SZ in the name"
    if "control" in name.lower():
        return "control in the name"
    tokens = name.split("_")
    for left, right in zip(tokens[1:], tokens[2:]):       # the leading date is not a repeat number
        if re.fullmatch(r"\d+x\d+", left.lower()):
            continue
        if left[-1:].isdigit() and right[:1].isdigit() and not any(left.lower().startswith(k) for k in ANTIBODIES):
            return f"number_number ({left}_{right})"
    return ""


# --------------------------------------------------------------------------- finding and measuring


def two_peaks(t, b, a1, m1, s1, a2, m2, s2):
    return b + a1 * np.exp(-(t - m1) ** 2 / (2 * s1 * s1)) + a2 * np.exp(-(t - m2) ** 2 / (2 * s2 * s2))


def measure_bar(pan, ab, noise, row, col, theta, length, um, antibody_2=None):
    """Profile across one bar and its two-Gaussian fit. Returns a dict, or None when the
    profile is not that of a synapse seen side-on.

    pan, ab - smoothed planes (ab may be None); noise - (median, sigma) of the smoothed antibody plane
    theta   - angle of the bar's long axis from the row axis (skimage's 'orientation')
    """
    from scipy import ndimage, optimize, signal

    px_nm = EXF_NOMINAL / (um * 1000)                        # pixels per nanometre, at the nominal expansion
    reach = PROFILE_HALF_NM * px_nm
    along = np.array([math.cos(theta), math.sin(theta)])     # (row, col) along the bar
    across = np.array([-along[1], along[0]])
    half = min(0.3 * length, AVERAGE_HALF_NM * px_nm)
    t = np.arange(-reach, reach + 0.25, 0.5)
    s = np.arange(-half, half + 0.5, 1.0)
    rows = row + t[:, None] * across[0] + s[None, :] * along[0]
    cols = col + t[:, None] * across[1] + s[None, :] * along[1]
    if rows.min() < 1 or cols.min() < 1 or rows.max() > pan.shape[0] - 2 or cols.max() > pan.shape[1] - 2:
        return None
    grid = ndimage.map_coordinates(pan, [rows, cols], order=1)
    if (grid <= 0).any():                                    # stitching padding
        return None
    profile = grid.mean(axis=1)

    spread = profile.max() - profile.min()
    peaks, _ = signal.find_peaks(profile, prominence=0.08 * spread)
    if len(peaks) < 2:
        return None
    first = peaks[np.argmin(np.abs(t[peaks]))]               # the bar itself
    if abs(t[first]) > 3:
        return None
    near, far = (REF_NM * e / (um * 1000) for e in EXF_SEARCH)
    partners = [p for p in peaks if near <= abs(t[p] - t[first]) <= far]
    if not partners:
        return None
    second = max(partners, key=lambda p: profile[p])
    gap = abs(t[second] - t[first])
    lo, hi = min(t[first], t[second]) - 0.9 * gap, max(t[first], t[second]) + 0.9 * gap
    win = (t >= lo) & (t <= hi)
    base = float(profile[win].min())
    start = [base, profile[first] - base, t[first], gap / 4, profile[second] - base, t[second], gap / 4]
    low = [0, 0, t[first] - 2, 0.8, 0, t[second] - 2, 0.8]
    high = [float(profile.max()), 2 * spread + 1, t[first] + 2, gap, 2 * spread + 1, t[second] + 2, gap]
    try:
        fit, _ = optimize.curve_fit(two_peaks, t[win], profile[win], p0=start, bounds=(low, high), maxfev=2000)
    except (RuntimeError, ValueError):
        return None
    model = two_peaks(t[win], *fit)
    resid = float(((profile[win] - model) ** 2).sum())
    total = float(((profile[win] - profile[win].mean()) ** 2).sum())
    r2 = 1 - resid / total if total > 0 else 0.0
    b, a1, m1, s1, a2, m2, s2 = fit
    distance = abs(m2 - m1)
    if not (near <= distance <= far):
        return None
    between = (t >= min(m1, m2)) & (t <= max(m1, m2))
    tops = min(two_peaks(m1, *fit), two_peaks(m2, *fit))
    dip = float((tops - two_peaks(t[between], *fit).min()) / max(tops - b, 1e-6))
    height = float(a2 / a1) if a1 > 0 else 0.0
    if r2 < FIT_R2 or dip < MIN_DIP or not (PEAK_RATIO[0] <= height <= PEAK_RATIO[1]):
        return None

    # The measurement itself, as in the paper: a line LINE_PX pixels thick across the middle
    # of the bar, and the distance from one peak's top to the other's. The fit above only says
    # where the two peaks are; each top is the brightest sample near it, placed between samples
    # by a parabola through it and its two neighbours.
    lane = np.arange(LINE_PX) - (LINE_PX - 1) / 2
    thin = ndimage.map_coordinates(pan, [row + t[:, None] * across[0] + lane[None, :] * along[0],
                                         col + t[:, None] * across[1] + lane[None, :] * along[1]], order=1).mean(axis=1)

    def summit(m):
        near_m = np.flatnonzero(np.abs(t - m) <= distance / 3)
        i = near_m[np.argmax(thin[near_m])]
        if not 0 < i < len(t) - 1:
            return float(t[i])
        bend = thin[i - 1] - 2 * thin[i] + thin[i + 1]
        # never more than half a sample from the brightest one (a top at the edge of its window is not a true peak)
        return float(t[i] + float(np.clip(0.5 * (thin[i - 1] - thin[i + 1]) / bend if bend < 0 else 0.0, -0.5, 0.5)) * (t[1] - t[0]))

    summits = (summit(m1), summit(m2))
    reading = abs(summits[1] - summits[0])
    if not (near <= reading <= far):
        return None
    # Each density followed along its own length (a line LINE_PX thick through its top), for
    # telling a continuous PSD from a row of dense projections and for finding gaps in a PSD.
    span = np.arange(-0.75 * length, 0.75 * length + 0.5, 0.5)
    runs = []
    for top in summits:
        rr = row + top * across[0] + span[:, None] * along[0] + lane[None, :] * across[0]
        cc = col + top * across[1] + span[:, None] * along[1] + lane[None, :] * across[1]
        inside = (rr.min(axis=1) >= 0) & (cc.min(axis=1) >= 0) & (rr.max(axis=1) <= pan.shape[0] - 1) & (cc.max(axis=1) <= pan.shape[1] - 1)
        runs.append(np.where(inside, ndimage.map_coordinates(pan, [rr, cc], order=1, mode="nearest").mean(axis=1), np.nan))

    out = {
        "row": float(row + (m1 + m2) / 2 * across[0]), "col": float(col + (m1 + m2) / 2 * across[1]),
        "theta": theta, "distance_um": float(reading * um), "exf": float(reading * um * 1000 / REF_NM),
        "fit_um": float(distance * um), "exf_fit": float(distance * um * 1000 / REF_NM), "summits": summits, "thin": thin, "runs": runs, "span": span, "base": float(b), "widths": (float(s1), float(s2)),
        "tops": (float(np.interp(summits[0], t, thin)), float(np.interp(summits[1], t, thin))), "ab_at": None,
        "r2": float(r2), "dip": dip, "ratio": height, "t": t, "profile": profile, "fit": fit, "win": (lo, hi),
        "peak": float(max(two_peaks(m1, *fit), two_peaks(m2, *fit))), "lines": grid,
        "ab_sigma": None, "ab_profile": None, "length": float(length), "ab_cover": None,
        "ab2_sigma": None, "ab2_profile": None, "ab2_at": None,
    }
    if ab is not None:
        # the synapse's footprint: both densities and a quarter of their spacing beyond, along the averaged stretch
        foot = (t >= min(m1, m2) - gap / 4) & (t <= max(m1, m2) + gap / 4)
        grid = ndimage.map_coordinates(ab, [rows, cols], order=1)
        out["ab_sigma"] = float((grid[foot].max() - noise[0]) / noise[1])
        # share of the measured stretch of the bar that has antibody on it (4 noise-widths up):
        # near 1 for a band of antibody lying along the synapse, small for a single dot
        out["ab_cover"] = float((grid[foot].max(axis=0) >= noise[0] + 4 * noise[1]).mean())
        out["ab_profile"] = grid.mean(axis=1)
        out["ab_at"] = float(t[foot][np.argmax(out["ab_profile"][foot])])    # where across the synapse the antibody peaks
    if antibody_2 is not None:  # the second antibody (smoothed plane, (median, noise)): the same footprint, the same numbers
        foot = (t >= min(m1, m2) - gap / 4) & (t <= max(m1, m2) + gap / 4)
        grid = ndimage.map_coordinates(antibody_2[0], [rows, cols], order=1)
        out["ab2_sigma"] = float((grid[foot].max() - antibody_2[1][0]) / antibody_2[1][1])
        out["ab2_profile"] = grid.mean(axis=1)
        out["ab2_at"] = float(t[foot][np.argmax(out["ab2_profile"][foot])])
    return out


def find_synapses(pan_raw, ab_raw, um, ab2_raw=None):
    """Synapses in one 2D tile. Returns a list of measure_bar() results, positions in tile pixels."""
    from scipy import ndimage
    from skimage import feature, measure

    px_nm = EXF_NOMINAL / (um * 1000)
    valid = pan_raw > 0
    if valid.mean() < 0.05:
        return []
    pan = ndimage.gaussian_filter(pan_raw.astype(np.float32), SMOOTH_PX)
    ab = noise = None
    if ab_raw is not None:
        ab = ndimage.gaussian_filter(ab_raw.astype(np.float32), SMOOTH_PX)
        middle = float(np.median(ab[valid]))
        noise = (middle, 1.4826 * float(np.median(np.abs(ab[valid] - middle))) or 1.0)
    second = None
    if ab2_raw is not None:
        ab2 = ndimage.gaussian_filter(ab2_raw.astype(np.float32), SMOOTH_PX)
        middle2 = float(np.median(ab2[valid]))
        second = (ab2, (middle2, 1.4826 * float(np.median(np.abs(ab2[valid] - middle2))) or 1.0))
    sigma = max(1.0, PSD_SIGMA_NM * px_nm)
    hessian = feature.hessian_matrix(pan, sigma=sigma, order="rc", use_gaussian_derivatives=False)
    upper, lower = feature.hessian_matrix_eigvals(hessian)       # upper >= lower at every pixel
    # a bright line curves down sharply across itself (lower << 0) and hardly along itself
    ridge = np.where((lower < 0) & (np.abs(upper) < 0.5 * np.abs(lower)), -lower, 0)
    bars = (ridge > np.percentile(ridge[valid], RIDGE_PERCENTILE)) & (pan > np.percentile(pan[valid], BRIGHT_PERCENTILE))
    ranks = np.sort(pan[valid][::37])        # a sample of the plane, to rank a brightness against
    found = []
    for region in measure.regionprops(measure.label(bars)):
        length = region.major_axis_length
        if not (PSD_LENGTH_NM[0] * px_nm <= length <= PSD_LENGTH_NM[1] * px_nm):
            continue
        if region.minor_axis_length > 0.6 * length:
            continue
        hit = measure_bar(pan, ab, noise, region.centroid[0], region.centroid[1], region.orientation, length, um, second)
        if hit:
            # how bright the brighter density is: the share of the plane that is dimmer
            hit["bright"] = float(np.searchsorted(ranks, hit["peak"]) / len(ranks))
            if hit["bright"] >= PSD_RANK:
                found.append(hit)
    return found


def spot_width(raw, row, col):
    """Width (FWHM, pixels) of one punctum: a round 2D Gaussian on a flat background fitted to
    the unsmoothed image, 13 x 13 pixels around it. None when it is not a clean single spot."""
    from scipy import optimize

    r, c, half = int(round(row)), int(round(col)), 6
    if r < half or c < half or r + half >= raw.shape[0] or c + half >= raw.shape[1]:
        return None
    patch = raw[r - half:r + half + 1, c - half:c + half + 1].astype(np.float64)
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1]

    def blob(_, b, a, y0, x0, sd):
        return (b + a * np.exp(-((yy - y0) ** 2 + (xx - x0) ** 2) / (2 * sd * sd))).ravel()

    floor = float(np.median(patch))
    try:
        fit, _ = optimize.curve_fit(blob, None, patch.ravel(), p0=[floor, patch.max() - floor, 0, 0, 1.5],
                                    bounds=([0, 0, -2, -2, 0.4], [patch.max(), 4 * patch.max() + 1, 2, 2, 5]), maxfev=400)
    except (RuntimeError, ValueError):
        return None
    left = float(((patch.ravel() - blob(None, *fit)) ** 2).sum())
    total = float(((patch - patch.mean()) ** 2).sum())
    if total <= 0 or 1 - left / total < 0.6 or not 0.45 < fit[4] < 4.9:
        return None
    return 2.3548 * float(fit[4])


def find_spots(ab_raw, valid):
    """Antibody puncta in one tile, as [(row, col, FWHM in pixels or None)].

    A punctum is SPOT_MIN_PX or more connected pixels of the smoothed antibody channel that
    are AB_SIGMAS noise-widths above the plane's median: the same rule that makes a synapse
    antibody-positive. The width is measured on the brightest puncta that stand alone.
    """
    from scipy import ndimage, spatial
    from skimage import measure

    if valid.mean() < 0.05:
        return []
    ab = ndimage.gaussian_filter(ab_raw.astype(np.float32), SMOOTH_PX)
    middle = float(np.median(ab[valid]))
    sd = 1.4826 * float(np.median(np.abs(ab[valid] - middle))) or 1.0
    regions = [r for r in measure.regionprops(measure.label((ab > middle + AB_SIGMAS * sd) & valid), intensity_image=ab)
               if r.area >= SPOT_MIN_PX]
    if not regions:
        return []
    centres = np.array([r.centroid for r in regions])
    spots = [[float(r), float(c), None] for r, c in centres]
    if len(regions) > 1:
        apart = spatial.cKDTree(centres).query(centres, k=2)[0][:, 1]
    else:
        apart = np.array([np.inf])
    alone = [i for i in np.argsort([-r.intensity_max for r in regions]) if apart[i] > SPOT_ALONE_PX]
    for i in alone[:SPOT_FITS]:
        spots[i][2] = spot_width(ab_raw, *centres[i])
    return [tuple(sp) for sp in spots]


def antibody_numbers(hits, spots, area_px, um):
    """How well the antibody labels the synapses of one sample.

      labelled    share of the synapses found that carry antibody (sensitivity). The synapses
                  are found from the pan stain, without looking at the antibody.
      signal      median antibody signal, in noise-widths, at the synapses that carry it
      on synapse  share of all antibody puncta that sit on a synapse found (specificity)
      enrichment  that share divided by the share expected if the puncta fell at random,
                  which is the part of the field the synapses' neighbourhoods cover.
                  1 = no better than chance.
      resolution  width (FWHM) of the smallest isolated puncta, as imaged: the SPOT_SMALLEST-th
                  percentile of the widths. Most puncta are clusters of receptors and wider
                  than the optics; the smallest ones come closest to a point. Still an
                  upper bound on the lateral resolution.
    """
    from scipy import spatial

    out = {"puncta": len(spots)}
    marked = [h for h in hits if h["ab_sigma"] is not None and h["ab_sigma"] >= AB_SIGMAS]
    if hits:
        out["labelled_pct"] = 100 * len(marked) / len(hits)
    if marked:
        out["signal"] = float(np.median([h["ab_sigma"] for h in marked]))
    reach = ON_SYNAPSE_X * REF_NM * EXF_NOMINAL / (um * 1000)
    if spots and hits and area_px:
        on = 0
        for z in {sp[0] for sp in spots}:
            here = np.array([(h["row"], h["col"]) for h in hits if h["z"] == z])
            there = np.array([(sp[1], sp[2]) for sp in spots if sp[0] == z])
            if len(here):
                on += int((spatial.cKDTree(here).query(there)[0] <= reach).sum())
        chance = min(1.0, len(hits) * math.pi * reach * reach / area_px)
        out["on_pct"] = 100 * on / len(spots)
        out["enrichment"] = (on / len(spots)) / chance if chance > 0 else None
    widths = [sp[3] for sp in spots if sp[3] is not None]
    if len(widths) >= MIN_WIDTHS:
        out["fwhm_nm"], out["fwhm_n"] = float(np.percentile(widths, SPOT_SMALLEST)) * um * 1000, len(widths)
    return out


def classify(h, side, um):
    """Name a synapse's class as in M'Saad, Cairns et al. 2025, Fig. 2jj-ll, after Gray:
        Type I, macular      a prominent, unbroken PSD (asymmetric, excitatory)
        Type I, perforated   a prominent PSD with a gap along its length
        Type II (putative)   prominent DPs but a thin or faint PSD (symmetric, inhibitory)
    The paper classified by eye. Here it is done from the two densities, in place on `h`:

    Which density is the PSD ('psd', 0 or 1) is decided, in this order, by
      antibody    a positive antibody known to be post- or presynaptic sits on its side
      texture     followed along its length, a PSD is smooth and a row of DPs is bumpy; the
                  density at least ROUGHER_BY times rougher is the DP row. (On GluA2-positive
                  synapses this picked the antibody's side in 90% of cases.)
      brightness  otherwise the brighter density is taken as the PSD, which is true of type I
                  synapses (94% of GluA2-positive ones) and wrong by definition for type II
    Type II is called only when texture names the DP row and that row is TYPE2_BRIGHTER_BY
    times brighter than the PSD. Texture is wrong about one time in ten, and inhibitory
    synapses are about as rare, so a type II call is a suggestion to look, not a finding.
    """
    height = [h["tops"][k] - h["base"] for k in (0, 1)]
    reach = h["span"][-1] / 0.75                              # the bar's length
    core_part = np.abs(h["span"]) <= 0.35 * reach
    rough = []
    for run in h["runs"]:
        v = run[core_part & ~np.isnan(run)] - h["base"]
        rough.append(float(np.mean(np.abs(np.diff(v, 2))) / max(np.mean(v), 1e-6)) if len(v) > 4 else np.nan)
    brighter = int(height[1] > height[0])
    if h.get("ab_pos") and side in ("post", "pre") and h["ab_at"] is not None:
        near = int(abs(h["ab_at"] - h["summits"][1]) < abs(h["ab_at"] - h["summits"][0]))
        psd, by = (near if side == "post" else 1 - near), "antibody"
    elif not np.isnan(rough).any() and max(rough) >= ROUGHER_BY * max(min(rough), 1e-9):
        psd, by = int(rough[1] < rough[0]), "texture"
    else:
        psd, by = brighter, "brightness"
    h["psd"], h["psd_by"] = psd, by
    if by == "texture" and psd != brighter and height[1 - psd] >= TYPE2_BRIGHTER_BY * max(height[psd], 1e-6):
        h["class"] = "Type II (putative)"
        return
    # a perforated PSD: along its length, two stretches of PSD with a dark stretch between them
    px_nm = EXF_NOMINAL / (um * 1000)
    step = h["span"][1] - h["span"][0]
    run = h["runs"][psd] - h["base"]
    on = np.nan_to_num(run, nan=0.0) >= GAP_BELOW * np.nanmax(run)
    stretches, start = [], 0                                   # runs of equal values: (is PSD, length in pixels)
    for i in range(1, len(on) + 1):
        if i == len(on) or on[i] != on[start]:
            stretches.append((bool(on[start]), (i - start) * step))
            start = i
    perforated = any(
        not here and GAP_NM[0] * px_nm <= length <= GAP_NM[1] * px_nm
        and before[0] and before[1] >= SEGMENT_NM * px_nm and after[0] and after[1] >= SEGMENT_NM * px_nm
        for before, (here, length), after in zip(stretches, stretches[1:], stretches[2:]))
    h["class"] = "Type I, perforated" if perforated else "Type I, macular"


def example_score(h, um):
    """0 to 1: how good a textbook example this synapse is. High when the two-peak profile is
    clean (fit, dip), the PSD is long, and the antibody is strong and lies along the synapse as
    a band rather than a dot. Used only to order the log, best examples first; it plays no
    part in the expansion factor."""
    px_nm = EXF_NOMINAL / (um * 1000)
    parts = [
        np.clip((h["r2"] - FIT_R2) / (1 - FIT_R2), 0, 1),
        np.clip(h["dip"] / 0.6, 0, 1),
        np.clip(h["length"] / (300 * px_nm), 0, 1),
        np.clip(((h["ab_sigma"] or 0) - AB_SIGMAS) / AB_SIGMAS, 0.05, 1),
        np.clip(h["ab_cover"] or 0, 0.05, 1),
    ]
    return float(np.prod(parts) ** (1 / len(parts)))


def unique(hits, radius):
    """Drop repeats: a synapse found from its PSD and again from its DPs, or in two tiles.
    The better-fitted one of any pair closer than `radius` pixels is kept."""
    kept = []
    for h in sorted(hits, key=lambda h: -h["r2"] * h["dip"]):
        if all(h["z"] != k["z"] or (h["row"] - k["row"]) ** 2 + (h["col"] - k["col"]) ** 2 > radius ** 2 for k in kept):
            kept.append(h)
    return kept


def analyse(name, zarr_dir, out_root, synaptic, max_planes, side=None):
    """Every synapse in one sample. Returns (hits, info, channels, pan, antibody channel or None,
    um, antibody numbers from antibody_numbers())."""
    series = zarr_dir / "0"
    info = core.read_series(series)
    channels, pan, antibodies, _ = eff.channel_roles(name, zarr_dir, out_root, info)
    if pan is None:
        raise ValueError("no pan stain found")
    raw = eff.open_level(series, info, 0)                         # lazy (c, z, y, x)
    z_um, _, um = eff.voxel_um(info, raw.shape[1:])
    if not um:
        raise ValueError("the image has no pixel size")
    z0, z1 = eff.gel_planes(eff.open_level(series, info, eff.macro_level(info))[pan])
    step = max(1, math.ceil(PLANE_GAP_UM / z_um)) if z_um else 1
    planes = list(range(z0, z1 + 1, step))
    if len(planes) > max_planes:
        planes = [planes[int(round(i))] for i in np.linspace(0, len(planes) - 1, max_planes)]
    # With more than one antibody channel, the one on the longest laser is measured: the channel
    # drawn magenta (magenta before green, as the colours are handed out in convert_to_zarr).
    ab_c = max(antibodies, key=lambda c: (channels[c].get("laser") or 0, -c)) if antibodies else None
    # ...and the next one down (drawn green) is the second antibody, measured on the same synapses
    others = [c for c in antibodies if c != ab_c]
    ab2_c = max(others, key=lambda c: (channels[c].get("laser") or 0, -c)) if others else None
    print(f"   pan = {channels[pan]['label']}, antibody = {channels[ab_c]['label'] if ab_c is not None else 'none'}; "
          f"{len(planes)} plane(s) between {z0} and {z1}; pixel {um * 1000:.0f} nm as imaged")
    pad = int(math.ceil(2 * PROFILE_HALF_NM * EXF_NOMINAL / (um * 1000)))
    ny, nx = raw.shape[2:]
    hits, spots, area = [], [], 0
    for z in planes:
        before = len(hits)
        for y in range(0, ny, TILE_PX):
            for x in range(0, nx, TILE_PX):
                ya, xa = max(0, y - pad), max(0, x - pad)
                yb, xb = min(ny, y + TILE_PX + pad), min(nx, x + TILE_PX + pad)
                tile = raw[:, z, ya:yb, xa:xb][[pan] + ([ab_c] if ab_c is not None else []) + ([ab2_c] if ab2_c is not None else [])].compute()
                for h in find_synapses(tile[0], tile[1] if ab_c is not None else None, um, tile[2] if ab2_c is not None else None):
                    h["row"], h["col"], h["z"] = h["row"] + ya, h["col"] + xa, z
                    if y <= h["row"] < y + TILE_PX and x <= h["col"] < x + TILE_PX:   # each tile keeps its own
                        hits.append(h)
                area += int((tile[0][y - ya:y - ya + TILE_PX, x - xa:x - xa + TILE_PX] > 0).sum())
                if ab_c is not None:
                    for r, c, width in find_spots(tile[1], tile[0] > 0):
                        if y <= r + ya < y + TILE_PX and x <= c + xa < x + TILE_PX:
                            spots.append((z, r + ya, c + xa, width))
        print(f"   slice {z}: {len(hits) - before} synapse profile(s)")
    hits = unique(hits, REF_NM * EXF_NOMINAL / (um * 1000))
    # A field of view is one camera frame: a plane of a stack, or one tile of a montage (the
    # tile grid is estimated from the image size, as elsewhere).
    tiled = "montage" in roi.classify(info)
    edges_y, edges_x = eff.tile_edges(ny, ny), eff.tile_edges(nx, nx)
    for h in hits:
        h["ab_pos"] = bool(synaptic and h["ab_sigma"] is not None and h["ab_sigma"] >= AB_SIGMAS)
        classify(h, side, um)
        h["example"] = example_score(h, um)
        h["ab2_pos"] = bool(h["ab2_sigma"] is not None and h["ab2_sigma"] >= AB_SIGMAS)
        structure.measure(h, um)
        h["fov"] = (h["z"], int(np.searchsorted(edges_y, h["row"], side="right")),
                    int(np.searchsorted(edges_x, h["col"], side="right"))) if tiled else (h["z"], 0, 0)
    # Antibody-positive first, the best examples first (example_score). The rest by how bright the density is:
    # brightness is what tells a PSD from a membrane, and it does not favour any distance.
    hits.sort(key=lambda h: (False, -h["example"]) if h["ab_pos"] else (True, -h["peak"]))
    numbers = antibody_numbers(hits, spots, area, um) if ab_c is not None else {}
    numbers["area_px"] = area
    numbers["ab2_channel"] = ab2_c
    return hits, info, channels, pan, ab_c, um, numbers


# --------------------------------------------------------------------------- pictures


def crop(plane, row, col, half):
    """The square of side 2 * half + 1 around a point. Where it runs off the plane it is filled
    with NaN (drawn black), so every picture has the same size and the point stays in its middle."""
    r, c = int(round(row)), int(round(col))
    out = np.full((2 * half + 1, 2 * half + 1), np.nan, np.float32)
    r0, r1 = max(0, r - half), min(plane.shape[0], r + half + 1)
    c0, c1 = max(0, c - half), min(plane.shape[1], c + half + 1)
    if r1 > r0 and c1 > c0:
        out[r0 - (r - half):r1 - (r - half), c0 - (c - half):c1 - (c - half)] = plane[r0:r1, c0:c1]
    return out


def square(img, half):
    """A picture cut before crop() filled its edges (kept in an earlier run's files), filled now."""
    side = 2 * half + 1
    if img.shape == (side, side):
        return img
    out = np.full((side, side), np.nan, np.float32)
    out[:min(side, img.shape[0]), :min(side, img.shape[1])] = img[:side, :side]
    return out


def stretch(img, lo, hi):
    return np.clip((img - lo) / max(hi - lo, 1e-6), 0, 1)


def tag(ax, x, y, text, colour="white", size=8, **how):
    """Words on a picture, placed in fractions of it and outlined in black so they read on bright and dark alike."""
    from matplotlib import patheffects

    return ax.text(x, y, text, transform=ax.transAxes, color=colour, fontsize=size, zorder=6,
                   path_effects=[patheffects.withStroke(linewidth=2, foreground="black")], **how)


def bar_length(um, width_px):
    """The round length, in micrometres as imaged, nearest below a quarter of the picture's width."""
    return max((n for n in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20) if n <= width_px * um / 4), default=0.1)


def scale_bar(ax, um, width_px, label=False):
    """A bar in micrometres as imaged, bottom left. Its length is given in the figure's title
    (see bar_words), not on the picture (user, 8 Oct 2026)."""
    from matplotlib.patches import Rectangle

    length = bar_length(um, width_px)
    ax.add_patch(Rectangle((0.05, 0.05), length / um / width_px, 0.022, transform=ax.transAxes,
                           facecolor="white", edgecolor="black", lw=0.4, zorder=5))
    if label:
        tag(ax, 0.05, 0.085, f"{length:g} µm", ha="left", va="bottom")
    return length


def picture(ax, img):
    """An image filling its panel edge to edge, with a hairline round it so neighbours touch without merging."""
    ax.imshow(np.nan_to_num(np.clip(img, 0, 1)), interpolation="nearest")
    ax.set_xlim(-0.5, img.shape[1] - 0.5)
    ax.set_ylim(img.shape[0] - 0.5, -0.5)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("#6A6A6A")
        spine.set_linewidth(0.5)


def bar_words(length, exf):
    """The scale bar's length for a title: as imaged (expanded) and in the tissue before expansion."""
    return f"Scale bar = {length:g} µm expanded, {length * 1000 / exf:.0f} nm non-expanded (ExF = {exf:.1f})"


FIGURE_DPI = 300       # the synapse pictures are saved at print resolution
FIT_BLUE = "#4DA3FF"   # the fitted curve: a blue light enough to read on the dark background
MEASURED_RED = "#FF2A2A"   # the line the distance was read from, on the overlay and in the profile plot
PSD_PINK, DP_LIME = "#FA8072", "#BFFF00"   # the two tops, on the overlay and in the profile plot


def measured_line(ax, h, half, side_in):
    """The measurement, drawn on the overlay only so the other panels stay clean: the lane LINE_PX
    pixels thick that the profile was read from, in red and to scale, from the DP top to the PSD
    top; dotted beyond them over the rest of the profile; a tick and a label at each top in the
    colours of the profile plot. Profile positions t are measured from the point the synapse was
    found at; the tile is centred on the midpoint of the two fitted peaks, rounded to a pixel."""
    from matplotlib import patheffects

    across = np.array([-math.sin(h["theta"]), math.cos(h["theta"])])      # (row, col) steps along the profile
    mid = (h["fit"][2] + h["fit"][5]) / 2
    cy, cx = half + h["row"] - round(h["row"]), half + h["col"] - round(h["col"])

    def xy(t):
        return cx + (t - mid) * across[1], cy + (t - mid) * across[0]

    halo = [patheffects.withStroke(linewidth=1.2, foreground="black")]
    pts_per_px = side_in * 72 / (2 * half + 1)
    ends = sorted(h["summits"])
    for a, b in ((h["t"][0], ends[0]), (ends[1], h["t"][-1])):           # the rest of the profile, dotted
        (x0, y0), (x1, y1) = xy(a), xy(b)
        ax.plot([x0, x1], [y0, y1], color=MEASURED_RED, lw=1.1, ls=":", zorder=3, path_effects=halo)
    (x0, y0), (x1, y1) = xy(ends[0]), xy(ends[1])                          # the lane, DP top to PSD top
    ax.plot([x0, x1], [y0, y1], color=MEASURED_RED, lw=max(2.4, LINE_PX * pts_per_px), alpha=0.6,
            solid_capstyle="butt", zorder=3)
    tick = 0.045 * (2 * half + 1)
    perp = np.array([across[1], -across[0]])                               # (row, col) steps along the densities
    for k, s in enumerate(h["summits"]):
        x, y = xy(s)
        colour = PSD_PINK if k == h["psd"] else DP_LIME
        ax.plot([x - tick * perp[1], x + tick * perp[1]], [y - tick * perp[0], y + tick * perp[0]],
                color=colour, lw=1.8, zorder=4, path_effects=halo)
        ax.text(x + 1.6 * tick * perp[1], y + 1.6 * tick * perp[0], "PSD" if k == h["psd"] else "DP",
                color=colour, fontsize=7, fontweight="bold", ha="center", va="center", zorder=6,
                path_effects=[patheffects.withStroke(linewidth=2, foreground="black")])


def panel_box(fig, ax, pad=0.1):
    """Where an axes sits in the picture savefig(bbox_inches="tight") writes, as fractions of its
    width and height from the top left: [left, top, right, bottom]. pad is savefig's pad_inches."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    tight = fig.get_tightbbox(renderer)                                   # inches
    a = ax.get_window_extent(renderer).transformed(fig.dpi_scale_trans.inverted())
    x0, y0, x1, y1 = tight.x0 - pad, tight.y0 - pad, tight.x1 + pad, tight.y1 + pad
    w, hgt = x1 - x0, y1 - y0
    return [round((a.x0 - x0) / w, 5), round((y1 - a.y1) / hgt, 5), round((a.x1 - x0) / w, 5), round((y1 - a.y0) / hgt, 5)]


def card(png, number, name, h, tile, um, pan_ch, ab_ch, exf=None):
    """One synapse: pan, antibody, overlay, and the profile with its fit. exf is the sample's
    expansion factor, for the scale bar; without one the synapse's own is used."""
    half = tile["half"]
    raw = square(tile["pan"], half)
    pan = stretch(raw, *np.nanpercentile(raw, [1, 99.8]))
    rgb = np.dstack([pan] * 3)
    panels = [(rgb, f"Pan {pan_ch['label']}", "white")]
    if tile["ab"] is not None:
        ab = stretch(square(tile["ab"], half), tile["ab_lo"], tile["ab_hi"])
        tint = np.asarray(eff.colour_of(ab_ch), np.float32)
        both = rgb * 0.8 + ab[..., None] * tint
        panels.append((ab[..., None] * tint, f"Antibody {'1 ' if tile.get('ab2') is not None else ''}{ab_ch['label']}", eff.colour_of(ab_ch)))
        if tile.get("ab2") is not None:     # a second antibody has a panel of its own, and is in the overlay
            ab2 = stretch(square(tile["ab2"], half), tile["ab2_lo"], tile["ab2_hi"])[..., None] * np.asarray(tile["ab2_colour"], np.float32)
            both = both + ab2
            panels.append((ab2, f"Antibody 2 {tile['ab2_label']}", tuple(tile["ab2_colour"])))
        panels.append((both, "Overlay", "white"))
    # Laid out in inches, not by tight_layout: the pictures are squares that touch, the profile
    # sits beside them at the same height, and its legend has a column of its own on the right.
    n, side, gap, plot_w, legend_w, below, above, edge = len(panels), 2.6, 0.8, 4.0, 2.9, 0.55, 0.84, 0.1
    width, height = edge + n * side + gap + plot_w + legend_w, below + side + above
    fig, axes = eff.figure(1, n + 1, figsize=(width, height))
    for k, (ax, (img, title, colour)) in enumerate(zip(axes, panels)):
        ax.set_position([(edge + k * side) / width, below / height, side / width, side / height])
        picture(ax, img)
        ax.set_title(title, color=colour, fontsize=9)
        length = scale_bar(ax, um, img.shape[1])
    axes[-1].set_position([(edge + n * side + gap) / width, below / height, plot_w / width, side / height])
    measured_line(axes[n - 1], h, half, side)
    ax = axes[-1]
    t_um = h["t"] * um
    ax.plot(t_um, h["profile"], color=eff.MUTED, lw=1.2, label="Pan, averaged along the bar (fitted)")
    ax.plot(t_um, h["thin"], color=MEASURED_RED, lw=2, label=f"Pan, {LINE_PX}-pixel line (measured)")
    inside = (h["t"] >= h["win"][0]) & (h["t"] <= h["win"][1])
    ax.plot(t_um[inside], two_peaks(h["t"][inside], *h["fit"]), color=FIT_BLUE, lw=1.8, label="Two-Gaussian fit")
    for k, m in enumerate(h["summits"]):
        ax.axvline(m * um, color="white", lw=1, ls=":")
        ax.annotate("PSD" if k == h["psd"] else "DP", (m * um, h["tops"][k]), xytext=(0, 6), textcoords="offset points",
                    ha="center", color=PSD_PINK if k == h["psd"] else DP_LIME, fontsize=9, fontweight="bold")
    if h["ab_profile"] is not None:
        # the antibody along the same line, dashed, rescaled to the pan curve's height so its
        # position can be compared with the two pan peaks (its own counts are not on this axis)
        scaled = h["ab_profile"] - h["ab_profile"].min()
        scaled = scaled / max(scaled.max(), 1e-6) * (h["profile"].max() - h["profile"].min()) + h["profile"].min()
        ax.plot(t_um, scaled, color=eff.colour_of(ab_ch), lw=1.3, ls="--", label="Antibody (rescaled)")
    if h.get("ab2_profile") is not None and tile.get("ab2") is not None:
        scaled = h["ab2_profile"] - h["ab2_profile"].min()
        scaled = scaled / max(scaled.max(), 1e-6) * (h["profile"].max() - h["profile"].min()) + h["profile"].min()
        ax.plot(t_um, scaled, color=tile["ab2_colour"], lw=1.3, ls="--", label=f"Antibody 2, {tile['ab2_label']} (rescaled)")
    ax.set_xlabel("Distance across the synapse (µm, as imaged)", color=eff.INK, fontsize=9)
    ax.set_ylabel("Counts", color=eff.INK, fontsize=9)
    ax.grid(color="#2A2A2A", lw=0.6)
    low, high = ax.get_ylim()
    ax.set_ylim(low, high + 0.12 * (high - low))        # room for the PSD and DP labels under the title
    # beside the plot: inside the axes it sat on top of the peaks, under them on the axis label
    ax.legend(frameon=False, labelcolor=eff.INK, fontsize=8, loc="center left", bbox_to_anchor=(1.02, 0.5))
    ax.set_title(f"Peak to peak {h['distance_um']:.2f} µm ÷ {REF_NM:g} nm:  ExF = {h['exf']:.1f}     "
                 f"(Gaussian fit: ExF = {h['exf_fit']:.1f}, R² {h['r2']:.2f})", color=eff.INK, fontsize=9, loc="left")
    sign = "" if h["ab_sigma"] is None else f"   Antibody {h['ab_sigma']:.1f} σ" + (" (positive)" if h["ab_pos"] else "")
    fig.text(edge / width, 1 - 0.06 / height, f"#{number}   {name}   Slice {h['z']}   x {h['col']:.0f}, y {h['row']:.0f}{sign}"
             f"   ·   {h['class']} (PSD named by {h['psd_by']})\n{bar_words(length, exf or h['exf'])}",
             color=eff.INK, fontsize=10, ha="left", va="top", linespacing=1.5)
    box = panel_box(fig, axes[n - 1])     # the panel the measured line is drawn on
    eff.save(fig, png, FIGURE_DPI)
    return box


def contact_sheet(png, name, shown, um, ab_ch, exf=None):
    """Every logged synapse of a sample at a glance, numbered as in the log. exf is the sample's
    expansion factor, for the scale bar; without one the mean of the synapses shown is used."""
    cols = 10
    rows = math.ceil(len(shown) / cols)
    # squares that touch side to side; above each row a strip for the labels (user, 8 Oct 2026:
    # labels above the pictures, only the scale bar on them)
    side, strip, above = 1.8, 0.4, 0.4
    width, height = cols * side, rows * (side + strip) + above
    fig, axes = eff.figure(rows, cols, figsize=(width, height), squeeze=False,
                           gridspec_kw={"left": 0, "right": 1, "bottom": 0, "top": 1 - (above + strip) / height,
                                        "wspace": 0, "hspace": strip / side})
    for ax in axes.ravel():
        ax.axis("off")
    for k, (ax, (number, h, tile)) in enumerate(zip(axes.ravel(), shown)):
        raw = square(tile["pan"], tile["half"])
        pan = stretch(raw, *np.nanpercentile(raw, [1, 99.8]))
        rgb = np.dstack([pan] * 3) * 0.8
        if tile["ab"] is not None:
            rgb = rgb + stretch(square(tile["ab"], tile["half"]), tile["ab_lo"], tile["ab_hi"])[..., None] * np.asarray(eff.colour_of(ab_ch), np.float32)
        two = tile.get("ab2") is not None
        if two:       # the second antibody of a double label, in its own colour
            rgb = rgb + stretch(square(tile["ab2"], tile["half"]), tile["ab2_lo"], tile["ab2_hi"])[..., None] * np.asarray(tile["ab2_colour"], np.float32)
        flags = (" AB1+" if h["ab_pos"] else "") + (" AB2+" if h.get("ab2_pos") else "") if two else " AB+" if h["ab_pos"] else ""
        ax.axis("on")
        picture(ax, rgb)
        # the class in full: "I mac" was read as "| mac"
        ax.text(0.03, 1.12, f"#{number}{flags}", transform=ax.transAxes, color=eff.INK, fontsize=8,
                fontweight="bold", ha="left", va="center")
        ax.text(0.97, 1.12, f"ExF = {h['exf']:.1f}", transform=ax.transAxes, color=eff.INK, fontsize=8, ha="right", va="center")
        ax.text(0.03, 1.04, h["class"], transform=ax.transAxes, color=eff.MUTED, fontsize=7.5, ha="left", va="center")
        length = scale_bar(ax, um, rgb.shape[1])
    last = shown[-1][2]
    stains = "" if not ab_ch else f" with antibody {ab_ch['label']} overlaid" if last.get("ab2") is None else         f" with antibody 1 ({ab_ch['label']}) and antibody 2 ({last['ab2_label']}) overlaid"
    fig.text(0.05 * side / width, 1 - 0.1 / height,
             f"{name}   ·   Pan (grey){stains}   ·   Number as in the Synapse Log, expansion factor (ExF), class"
             f"   ·   {bar_words(length, exf or float(np.mean([h['exf'] for _, h, _ in shown])))}", color=eff.INK, fontsize=10, ha="left", va="top")
    eff.save(fig, png, FIGURE_DPI)


def synapse_link(out_root, name, number, h, write=True):
    """A Neuroglancer link that opens on one synapse: the sample's own view, moved and zoomed in.
    With write=False nothing is saved on the server; a view saved earlier is still linked."""
    saved = out_root / core.STATE_DIR / "synapses" / name / f"synapse_{number:04d}.json"
    if not write:
        try:
            old = json.loads(saved.read_text(encoding="utf-8"))
            at = dict(zip(old.get("dimensions", {}), old.get("position", [])))
            same = abs(at.get("x", -9) - h["col"]) < 1 and abs(at.get("y", -9) - h["row"]) < 1 and round(at.get("z", -9)) == h["z"]
        except (OSError, ValueError):
            same = False
        return core.state_url(saved) if same else None
    view = roi.view_file(out_root, name)
    if not view.is_file():
        return None
    state = json.loads(view.read_text(encoding="utf-8"))
    place = {"x": h["col"], "y": h["row"], "z": h["z"]}
    state["position"] = [place.get(n, p) for n, p in zip(state.get("dimensions", {}), state.get("position", []))]
    state["crossSectionScale"] = 0.15
    state["layout"] = "xy"
    return core.state_to_link(state, out_root / core.STATE_DIR / "synapses" / name / f"synapse_{number:04d}.json")


# --------------------------------------------------------------------------- the log


def summarise(values):
    """'mean ± s.d. (median, n)' of expansion factors."""
    if not len(values):
        return "–"
    v = np.asarray(values, float)
    return f"{v.mean():.1f} ± {v.std(ddof=1) if len(v) > 1 else 0:.1f}  (median {np.median(v):.1f}, n = {len(v)})"


MANUAL_COLUMNS = ("Manual Line", "P2P Manual (µm)", "ExF (manual line)")   # set by the review page's measurement-line tool and remeasure_lines.py


def read_decisions(path):
    """What was typed in the log: {(sample, slice): [(x, y, accept, notes, reviewed class, {manual-line columns})]}."""
    kept = {}
    if not path.is_file():
        return kept
    ws = load_workbook(path)["Synapses"]
    cols = {c.value: i for i, c in enumerate(ws[HEADER_ROW]) if c.value}
    for row in ws.iter_rows(min_row=HEADER_ROW + 1, values_only=True):
        if row[cols["Sample"]] is None:
            continue
        kept.setdefault((row[cols["Sample"]], row[cols["Slice"]]), []).append(
            (row[cols["X (px)"]], row[cols["Y (px)"]], row[cols["Accept"]], row[cols["Notes"]],
             row[cols["Reviewed Class"]] if "Reviewed Class" in cols else None,
             {k: row[cols[k]] for k in MANUAL_COLUMNS if k in cols and row[cols[k]] not in (None, "")}))
    return kept


def decision_for(kept, sample, h, radius=8):
    for x, y, accept, notes, reviewed, manual in kept.get((sample, h["z"]), []):
        if abs(x - h["col"]) <= radius and abs(y - h["row"]) <= radius:
            return accept, notes, reviewed, manual
    return None, None, None, {}


METHOD = [
    "How these numbers were made",
    "",
    "Reference: M'Saad, Cairns et al., Nat. Biotechnol. 2025 (pan-ExM-t). Expansion factor = DP–PSD distance ÷ {ref:g} nm,",
    "where DP–PSD is the centre-to-centre distance between the presynaptic dense projections and the postsynaptic density.",
    "The paper (Supplementary Methods) read it peak to peak from 2-pixel-thick line profiles drawn by hand in FIJI, and reported",
    "the mean and s.d. across fields of view. Here the program finds the synapses and places the lines; the reading is the same.",
    "",
    "1. Pan channel, full resolution, one plane at a time (planes closer than {gap:g} µm are skipped so no synapse is counted twice;",
    "   planes outside the gel are left out). Both channels are smoothed with a {smooth:g}-pixel Gaussian.",
    "2. Candidate bars: ridge filter (most negative Hessian eigenvalue, sigma = {sigma:g} nm at a nominal {nominal:g}x), strongest",
    "   {ridge:g}% of pixels that are also brighter than {bright:g}% of the plane; connected bars {len0:g}–{len1:g} nm long (at {nominal:g}x).",
    "3. Profile across each bar, {half:g} nm either side (at {nominal:g}x), averaged along the bar over up to ±{avg:g} nm.",
    "4. Second peak searched at {ref:g} nm x {s0:g}–{s1:g}. This window bounds the answer: no synapse can give an expansion",
    "   factor outside {s0:g}–{s1:g}. It is wider than the expected 16–24 on purpose; check the histogram is not piled at its edges.",
    "5. Two Gaussians + flat baseline fitted to that averaged profile, to decide whether this is a synapse and where its two",
    "   peaks are. Kept when R² ≥ {r2:g}, the dip between peaks ≥ {dip:g} of the smaller peak, and the peak heights are within",
    "   a factor {pr1:g} of each other.",
    "   THE MEASUREMENT: a line {line} pixels thick across the middle of the bar; the top of each peak is its brightest sample,",
    "   placed between samples by a parabola through it and its two neighbours; top-to-top distance ÷ {ref:g} nm = ExF.",
    "   The distance between the fitted Gaussian centres ÷ {ref:g} nm is kept as 'ExF (Gaussian fit)'; it reads about 10% higher.",
    "   The brighter peak must also be brighter than {rank:g}% of the plane. Without this rule most double peaks were pairs of",
    "   membranes, which piled up at 26–30x. The cut-off was set from GluA2-positive synapses (260831_WT4_GluA2ms 3x3 montage):",
    "   95% of them ranked above 99.6%, the other double peaks had a median rank of 97.8%.",
    "6. Antibody-positive: brightest smoothed antibody pixel on the synapse ≥ {ab:g} noise-widths (1.4826 x MAD) above the",
    "   plane's median. Only used when the antibody was confirmed as synaptic at the prompt.",
    "7. The {top} best synapses per sample are logged: antibody-positive first, best examples first ('Example Score': clean",
    "   two-peak profile, long PSD, strong antibody lying along the synapse as a band). The score only orders the log.",
    "   A synapse's number can change when the sample is measured again; its 'Synapse ID' (slice and position, also in the",
    "   picture's file name) does not.",
    "8. 'ExF' on the Summary sheet is the one number quoted for the sample. It uses the accepted synapses if at least {minacc}",
    "   were accepted; otherwise the antibody-positive synapses if the antibody is synaptic and there are at least {minpos};",
    "   otherwise all double peaks. 'ExF Basis' says which. The last kind reads high and should not be quoted.",
    "   As in the paper it is the mean ± s.d. across fields of view: each field's synapses are averaged first. A field of view",
    "   is one plane of a stack or one tile of a montage (tile grid estimated), and needs {minfov} synapses to count. With fewer",
    "   than two such fields the mean ± s.d. is taken across synapses instead, and 'ExF Basis' says so.",
    "   'ExF 95% CI' is a bootstrap interval of that mean ({boots} resamples, fixed seed), resampling fields of view and then the",
    "   synapses inside each, because synapses of one field are not independent. 'ExF at Window Edge' is the share of those",
    "   synapses within 1 of the search limits ({s0:g} and {s1:g}): if it is more than a few percent, the window is clipping the answer.",
    "",
    "Synapse classes (provisional; the paper classified by eye, Fig. 2jj–ll)",
    "• Type I, macular: prominent unbroken PSD. Type I, perforated: the PSD falls below {gapbelow:g} of its height over {gap0:g}–{gap1:g} nm",
    "  along its length, with at least {seg:g} nm of PSD on both sides. Type II (putative): prominent DPs, thin or faint PSD.",
    "• Which density is the PSD is decided by the antibody's side when the synapse is antibody-positive; otherwise by texture",
    "  (along its length a PSD is smooth, a DP row bumpy; the DP row must be {rougher:g}x rougher); otherwise the brighter is the PSD.",
    "  On GluA2-positive synapses texture picked the antibody's side in 90% of cases, and the PSD was the brighter density in 94%.",
    "• Type II is called only when texture names the DP row and it is {t2:g}x brighter than the PSD. Texture errs about one time",
    "  in ten and inhibitory synapses are about as rare, so roughly half of the type II calls are expected to be wrong: check them.",
    "• A perforation shows only when the plane cuts through the hole, so perforated synapses are under-counted.",
    "• Sizes in nm are converted to pixels at the nominal {nominal:g}x.",
    "",
    "Antibody labelling (provisional)",
    "• Antibody puncta: {spotpx}+ connected pixels of the smoothed antibody channel ≥ {ab:g} noise-widths above the plane's median.",
    "• AB Labelled Synapses (%): share of the synapses found (from the pan stain alone) that are antibody-positive. Membrane",
    "  pairs mistaken for synapses are in the denominator, so this is a lower bound on the true share.",
    "• AB Puncta on Synapses (%): share of all puncta within {onx:g} nominal DP–PSD distances of a synapse found. Only side-on",
    "  synapses are found, so most real synapses are missed and this is far below the true on-target share.",
    "• AB Enrichment (× chance): that share ÷ the share of the field those neighbourhoods cover. 1 = puncta fall at random",
    "  with respect to synapses. This is the number to compare between antibodies; it does not depend on how many synapses were found.",
    "",
    "Resolution",
    "• Resolution FWHM: width of the smallest isolated antibody puncta (the {small}th percentile of the widths), from a round 2D",
    "  Gaussian fitted to the unsmoothed image. The paper (Extended Data Fig. 5) fitted Gaussians to hand-picked sub-diffraction",
    "  antibody features and got 290 ± 60 nm as imaged. Puncta here are mostly receptor clusters and wider than the optics, so",
    "  this is an upper bound and depends on the antibody. Corrected = as imaged ÷ ExF. Lateral only.",
    "• Pixel (corrected) = pixel size as imaged ÷ ExF.",
    "",
    "Caveats",
    "• 81.9 nm is itself a measurement (82 ± 26 nm s.d. between synapses in the paper), so single synapses scatter by ~30%;",
    "  the mean over many synapses is what carries the expansion factor.",
    "• Only synapses lying side-on in the image plane give the true distance. Tilted ones read short. The dip criterion",
    "  favours side-on synapses but cannot guarantee it: reject tilted or doubtful ones in the Accept column.",
    "• Which peak is the PSD and which the DPs is not decided; the distance does not depend on it.",
    "• Synapses without antibody are found from the pan stain alone and still include some membrane pairs (they read high,",
    "  25–30x). The antibody-positive and the accepted values are the ones to trust; compare the median with the mean.",
    "• 'ExF, all found' uses every profile that passed, unreviewed. 'ExF, accepted' uses only rows marked Y.",
]


SUMMARY = [
    "Sample", "Antibody", "Synaptic", "Planes", "Synapses found", "AB+",
    "ExF", "ExF s.d.", "ExF 95% CI", "ExF Fields", "ExF n", "ExF Basis", "ExF (Gaussian fit)", "ExF at Window Edge (%)",
    "ExF, all found", "ExF, AB+ only", "ExF, logged", "ExF, accepted", "Accepted / Rejected / Undecided",
    "Found outside 16–24 (%)",
    "Pixel (nm, as imaged)", "Pixel (nm, corrected)", "Resolution FWHM (nm, as imaged)", "Resolution (nm, corrected)",
    "Resolution n", "AB Labelled Synapses (%)", "AB Signal at Synapses (σ)", "AB Puncta", "AB Puncta on Synapses (%)",
    "AB Enrichment (× chance)", "Type I Macular", "Type I Perforated", "Type II (putative)", "Classes Counted On", "Contact Sheet",
] + structure.COLUMNS


def sample_exf(accepted, positive, every, synaptic):
    """The one expansion factor quoted for a sample, from the best evidence there is, as the
    paper reports it: the mean and s.d. across fields of view of each field's mean.

    Each argument is a list of synapses. Returns a dict for the Summary sheet.
    """
    for group, enough, basis in ((accepted, MIN_ACCEPTED, "accepted by a person"),
                                 (positive if synaptic else [], MIN_FOR_EXF, "antibody-positive synapses, unreviewed"),
                                 (every, 1, "all double peaks, pan only, unreviewed (reads high)")):
        if len(group) < enough:
            continue
        fields = {}
        for h in group:
            fields.setdefault(h["fov"], []).append(h["exf"])
        means = [float(np.mean(v)) for v in fields.values() if len(v) >= MIN_PER_FOV]
        used = sum(len(v) for v in fields.values() if len(v) >= MIN_PER_FOV)
        if len(means) >= 2:
            mean, sd, how, n_fields, n = float(np.mean(means)), float(np.std(means, ddof=1)), "across fields of view", len(means), used
        else:   # not enough fields with MIN_PER_FOV synapses each: fall back on the synapses themselves
            v = np.asarray([h["exf"] for h in group])
            mean, sd, how, n_fields, n = float(v.mean()), float(v.std(ddof=1)) if len(v) > 1 else 0.0, "across synapses", None, len(v)
        # 95% confidence interval of that mean by bootstrap, resampling the way the data are
        # nested: fields of view first, then the synapses inside each. Synapses of one field
        # share its local expansion and focus, so they are not independent measurements.
        rng = np.random.default_rng(0)            # fixed seed: the same data give the same interval
        pools = [np.asarray(v) for v in fields.values() if len(v) >= MIN_PER_FOV] if n_fields else None
        values = np.asarray([h["exf"] for h in group])
        draws = []
        for _ in range(BOOTSTRAPS):
            if pools:
                draws.append(np.mean([rng.choice(pools[i], len(pools[i])).mean() for i in rng.integers(0, len(pools), len(pools))]))
            else:
                draws.append(rng.choice(values, len(values)).mean())
        low, high = np.percentile(draws, [2.5, 97.5])
        edge = float(np.mean((values < EXF_SEARCH[0] + 1) | (values > EXF_SEARCH[1] - 1)))
        return {"ExF": round(mean, 1), "ExF s.d.": round(sd, 1), "ExF Fields": n_fields, "ExF n": n,
                "ExF 95% CI": f"{low:.1f}–{high:.1f}" if len(values) > 1 else None,
                "ExF at Window Edge (%)": round(100 * edge, 1),
                "ExF Basis": f"{basis}; s.d. {how}",
                "ExF (Gaussian fit)": round(float(np.mean([h["exf_fit"] for h in group])), 1)}
    return {}


def write_log(path, rows, summary):
    wb = Workbook()
    ws = wb.active
    ws.title = "Synapses"
    ws["A1"] = "Synapse Log"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "Type Y or N under Accept (and anything under Notes), save, close, and run Measure_ExF.bat again. Other columns are rewritten."
    fill, mine = PatternFill("solid", fgColor="1F3864"), PatternFill("solid", fgColor="E4F1E0")
    for i, (header, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(HEADER_ROW, i, header)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = width
    for r, row in enumerate(rows, start=HEADER_ROW + 1):
        for i, header in enumerate(HEADERS, start=1):
            value = row.get(header)
            cell = ws.cell(r, i, value)
            if header in ("Picture", "Neuroglancer") and value:
                cell.value, cell.hyperlink = "open", value
                cell.font = Font(color="0563C1", underline="single")
            elif header in ("Accept", "Notes", "Reviewed Class") + MANUAL_COLUMNS:
                cell.fill = mine
    choice = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    ws.add_data_validation(choice)
    accept = get_column_letter(HEADERS.index("Accept") + 1)
    choice.add(f"{accept}{HEADER_ROW + 1}:{accept}{HEADER_ROW + max(len(rows), 1)}")
    ws.freeze_panes = ws.cell(HEADER_ROW + 1, 3)

    ws = wb.create_sheet("Summary")
    heads = SUMMARY
    for i, head in enumerate(heads, start=1):
        cell = ws.cell(1, i, head)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), fill
        ws.column_dimensions[get_column_letter(i)].width = 44 if i == 1 else 30 if "ExF" in head else 16
    for r, line in enumerate(summary, start=2):
        for i, head in enumerate(heads, start=1):
            ws.cell(r, i, line.get(head))

    ws = wb.create_sheet("Method")
    ws.column_dimensions["A"].width = 130
    values = dict(ref=REF_NM, gap=PLANE_GAP_UM, smooth=SMOOTH_PX, sigma=PSD_SIGMA_NM, nominal=EXF_NOMINAL,
                  ridge=100 - RIDGE_PERCENTILE, bright=BRIGHT_PERCENTILE, len0=PSD_LENGTH_NM[0], len1=PSD_LENGTH_NM[1],
                  half=PROFILE_HALF_NM, avg=AVERAGE_HALF_NM, s0=EXF_SEARCH[0], s1=EXF_SEARCH[1], r2=FIT_R2, dip=MIN_DIP,
                  pr1=PEAK_RATIO[1], ab=AB_SIGMAS, top=TOP_N, rank=100 * PSD_RANK, minacc=MIN_ACCEPTED,
                  minpos=MIN_FOR_EXF, spotpx=SPOT_MIN_PX, onx=ON_SYNAPSE_X, line=LINE_PX, minfov=MIN_PER_FOV, small=SPOT_SMALLEST, gapbelow=GAP_BELOW, gap0=GAP_NM[0],
                  gap1=GAP_NM[1], seg=SEGMENT_NM, boots=BOOTSTRAPS, perms=structure.PERMUTATIONS, face=structure.FACE_FRACTION, rougher=ROUGHER_BY, t2=TYPE2_BRIGHTER_BY)
    import provenance
    produced = "Produced by " + provenance.stamp(Path(__file__), Path(__file__).with_name("synapse_structure.py"))
    for r, line in enumerate([METHOD[0], produced] + METHOD[1:] + [""] + structure.METHOD, start=1):
        ws.cell(r, 1, line.format(**values) if line is not produced else line).font = Font(
            bold=(r == 1 or line in ("Caveats", "Antibody labelling (provisional)", "Resolution") or line.startswith(("Synapse classes", "Structure (provisional"))))
    wb.save(path)


# --------------------------------------------------------------------------- run


def run(args):
    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    if not out_root.is_dir():
        sys.exit(f"Cannot reach the zarr folder {out_root}")
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(core.SETTINGS_FILE, encoding="utf-8-sig")
    base = Path(args.out or cfg.get("paths", "synapse_folder", fallback="").strip()
                or Path(core.DEFAULT_SHOTS).parent / "Zarr Synapse ExF")
    base.mkdir(parents=True, exist_ok=True)
    log = base / LOG_NAME
    print(f"Zarr folder : {out_root}\nSynapse log : {log}\n")
    try:
        kept = read_decisions(log)
        if log.is_file():
            open(log, "ab").close()
    except PermissionError:
        sys.exit(f"{LOG_NAME} is open in Excel. Close it and run this again.")

    text = args.text
    if text is None and sys.stdin.isatty():
        text = roi.ask("Which samples? Type text their names must contain (for example GluA2), or Enter for all: ")
    chosen, left_out = [], []
    for name, zarr_dir in roi.samples(out_root):
        if (text or "").lower() in name.lower():
            why = ignored(name)
            (left_out if why else chosen).append((name, why or zarr_dir))
    for name, why in left_out:
        print(f"   left out: {name}   ({why})")
    if not chosen:
        sys.exit("No sample to measure.")

    # Ask about each antibody before any work is done: is it a synaptic antibody?
    synaptic = {}
    for name, _ in chosen:
        for label, side in antibodies_in(name) or [("the antibody in " + name, "?")]:
            if label in synaptic:
                continue
            if args.synaptic == "auto":
                # as listed in ANTIBODIES. A name that gives no antibody is taken as synaptic: these
                # are antibody tests, and without it the sample's synapses could not be confirmed at all.
                synaptic[label] = side in ("pre", "post", "?")
            elif args.synaptic:
                synaptic[label] = args.synaptic == "y"
            elif side == "?" or not sys.stdin.isatty():
                synaptic[label] = roi.ask_choice(f"Is {label} a synaptic antibody? [Y/N]: ", "YN", "N") == "Y" \
                    if sys.stdin.isatty() else False
            else:
                guess = "Y" if side else "N"
                where = f"{side}synaptic" if side else "not synaptic"
                synaptic[label] = roi.ask_choice(f"{label}: I take it as {where}. Is it a synaptic antibody? "
                                                 f"[Y/N] (Enter = {guess}): ", "YN", guess) == "Y"
    print("\n" + "\n".join(f"   {k}: {'synaptic, positive synapses listed first' if v else 'not synaptic, antibody not used for ranking'}"
                           for k, v in synaptic.items()) + f"\n\n{len(chosen)} sample(s) to go through.\n")

    # What the log already holds. It is written again after every sample, so a run that is
    # stopped part-way keeps what it finished, and --new-only can pick up where it stopped.
    earlier = {"Synapses": [], "Summary": []}
    current = False
    if log.is_file():
        old = load_workbook(log)
        for sheet, key in (("Synapses", HEADER_ROW), ("Summary", 1)):
            names = [c.value for c in old[sheet][key]]
            for cells in old[sheet].iter_rows(min_row=key + 1):
                row = {h: (c.hyperlink.target if c.hyperlink else c.value) for h, c in zip(names, cells)}
                if row.get("Sample"):
                    earlier[sheet].append(row)
        current = [c.value for c in old["Summary"][1]] == SUMMARY     # written by this version of the program
    if args.new_only and current:
        have = {r["Sample"] for r in earlier["Summary"]}
        skipped = [name for name, _ in chosen if name in have]
        chosen = [c for c in chosen if c[0] not in have]
        print(f"{len(skipped)} sample(s) already in the log are left as they are (--new-only); {len(chosen)} to measure.\n")

    rows, summary = [], []

    def save():
        done = {r["Sample"] for r in summary}
        write_log(log, rows + [r for r in earlier["Synapses"] if r["Sample"] not in done],
                  summary + [r for r in earlier["Summary"] if r["Sample"] not in done])

    for n, (name, zarr_dir) in enumerate(chosen, start=1):
        print(f"[{n}/{len(chosen)}] {name}")
        named = antibodies_in(name)
        is_synaptic = any(synaptic.get(label) for label, _ in named) if named else synaptic.get("the antibody in " + name, False)
        try:
            # the antibody's side of the synapse, when the name gives one antibody (or several on the same side)
            sides = {sd for label_, sd in named if synaptic.get(label_)}
            hits, info, channels, pan, ab_c, um, ab_n = analyse(name, zarr_dir, out_root, is_synaptic, args.max_planes,
                                                                side=sides.pop() if len(sides) == 1 else None)
        except Exception as e:
            print(f"   SKIPPED: {e}\n")
            continue
        folder = base / name
        folder.mkdir(exist_ok=True)
        with open(folder / f"{name}_synapses.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Slice", "X (px)", "Y (px)", "DP-PSD peak to peak (µm, as imaged)", "ExF (peak to peak)",
                        "ExF (Gaussian fit)", "Field of View", "Class", "PSD Named By", "Example Score", "PSD Length (µm, as imaged)",
                        "PSD Thickness (µm, as imaged)", "DP-DP Spacings (µm, as imaged)", "AB to PSD (µm, as imaged)", "Orientation (deg)", "Fit R²", "Dip", "Peak Ratio", "Brightness Rank", "AB Signal (σ)", "AB+",
                        "AB2 Signal (σ)", "AB2+", "AB2 to PSD (µm, as imaged)", "AB1 to AB2 (µm, as imaged; + = AB2 towards DP)",
                        "Cleft to PSD (µm, as imaged)", "AB to Cleft (µm, as imaged; + = DP side)", "AB2 to Cleft (µm, as imaged; + = DP side)"])
            for h in hits:
                w.writerow([h["z"], round(h["col"], 1), round(h["row"], 1), round(h["distance_um"], 4), round(h["exf"], 2),
                            round(h["exf_fit"], 2), "z{}-r{}-c{}".format(*h["fov"]), h["class"], h["psd_by"], round(h["example"], 3),
                            "" if h["psd_len_um"] is None else round(h["psd_len_um"], 3), round(h["psd_fwhm_um"], 3),
                            " ".join(f"{g:.3f}" for g in h["dp_gaps_um"]),
                            "" if h["ab_offset_um"] is None else round(h["ab_offset_um"], 3), round(math.degrees(h["theta"]) % 180, 1),
                            round(h["r2"], 3), round(h["dip"], 3), round(h["ratio"], 3), round(h["bright"], 4),
                            "" if h["ab_sigma"] is None else round(h["ab_sigma"], 2), "Y" if h["ab_pos"] else "",
                            "" if h["ab2_sigma"] is None else round(h["ab2_sigma"], 2), "Y" if h["ab2_pos"] else ""]
                           + ["" if h.get(k) is None else round(h[k], 3) for k in ("ab2_offset_um", "ab12_um", "cleft_psd_um", "ab_cleft_um", "ab2_cleft_um")])
        logged = hits[:TOP_N]
        raw = eff.open_level(zarr_dir / "0", info, 0)
        half = int(round(2 * PROFILE_HALF_NM * EXF_NOMINAL / (um * 1000) * 0.75))
        ab_ch = channels[ab_c] if ab_c is not None else None
        ab2_c = ab_n.get("ab2_channel")
        ab2_ch = channels[ab2_c] if ab2_c is not None else None
        label = ", ".join(a for a, _ in named) or (ab_ch["label"] if ab_ch else "")
        shown, decisions = [], []
        # the sample's expansion factor, known before the pictures are drawn: their titles give the scale bar before expansion
        bar_exf = sample_exf([h for h in logged if str(decision_for(kept, name, h)[0] or "").strip().upper() == "Y"],
                             [h for h in hits if h["ab_pos"]], hits, is_synaptic).get("ExF")
        for stale in folder.glob(f"{name}_synapse-*.png"):    # pictures of an earlier run, numbered differently
            stale.unlink()
        card_boxes = {}
        from scipy import ndimage
        for number, h in enumerate(logged, start=1):
            r, c = int(round(h["row"])), int(round(h["col"]))
            wide = 6 * half                                     # antibody noise is judged over a wider field
            block = raw[:, h["z"], max(0, r - wide):r + wide + 1, max(0, c - wide):c + wide + 1].compute().astype(np.float32)
            rr, cc = r - max(0, r - wide), c - max(0, c - wide)
            tile = {"half": half, "pan": crop(ndimage.gaussian_filter(block[pan], SMOOTH_PX), rr, cc, half), "ab": None}
            if ab_c is not None:
                ab = ndimage.gaussian_filter(block[ab_c], SMOOTH_PX)
                middle = float(np.median(ab))
                sd = 1.4826 * float(np.median(np.abs(ab - middle))) or 1.0
                tile.update(ab=crop(ab, rr, cc, half), ab_lo=middle + sd, ab_hi=middle + max(12 * sd, 1))
            if ab2_c is not None:
                ab2 = ndimage.gaussian_filter(block[ab2_c], SMOOTH_PX)
                middle = float(np.median(ab2))
                sd = 1.4826 * float(np.median(np.abs(ab2 - middle))) or 1.0
                tile.update(ab2=crop(ab2, rr, cc, half), ab2_lo=middle + sd, ab2_hi=middle + max(12 * sd, 1),
                            ab2_colour=eff.colour_of(ab2_ch), ab2_label=ab2_ch["label"])
            png = folder / f"{name}_synapse-{number:03d}_z{h['z']:04d}_x{round(h['col']):05d}_y{round(h['row']):05d}.png"
            box = card(png, number, name, h, tile, um, channels[pan], ab_ch, bar_exf)
            # for the review page's line tool: tile pixel (i, j) is plane pixel (row0 + i, col0 + j)
            card_boxes[png.stem] = {"box": box, "side": 2 * half + 1, "row0": r - half, "col0": c - half, "z": h["z"]}
            accept, notes, reviewed, manual = decision_for(kept, name, h)
            decisions.append(accept)
            shown.append((number, h, tile))
            rows.append({
                "#": number, "Sample": name, "Slice": h["z"], "X (px)": round(h["col"]), "Y (px)": round(h["row"]),
                "Antibody": label, "AB Signal (σ)": None if h["ab_sigma"] is None else round(h["ab_sigma"], 1),
                "AB+": "Y" if h["ab_pos"] else None, "DP–PSD (µm, as imaged)": round(h["distance_um"], 3),
                "AB2 Signal (σ)": None if h["ab2_sigma"] is None else round(h["ab2_sigma"], 1), "AB2+": "Y" if h["ab2_pos"] else None,
                "AB1–AB2 (µm, as imaged)": None if h.get("ab12_um") is None else round(h["ab12_um"], 3),
                "ExF (peak to peak)": round(h["exf"], 1), "ExF (Gaussian fit)": round(h["exf_fit"], 1),
                "In 16–24": "Y" if EXF_EXPECTED[0] <= h["exf"] <= EXF_EXPECTED[1] else "no",
                "Fit R²": round(h["r2"], 3), "Dip": round(h["dip"], 2), "Peak Ratio": round(h["ratio"], 2),
                "Class": h["class"], "PSD Named By": h["psd_by"], "Example Score": round(h["example"], 2),
                "Synapse ID": f"z{h['z']:04d}_x{round(h['col']):05d}_y{round(h['row']):05d}",
                "Accept": accept, "Notes": notes, "Reviewed Class": reviewed, **manual, "Picture": str(png),
                "Neuroglancer": synapse_link(out_root, name, number, h, write=not args.no_links),
            })
        (folder / f"{name}_cards.json").write_text(json.dumps({"um": um, "cards": card_boxes}, indent=0), encoding="utf-8")
        if shown:
            contact_sheet(folder / f"{name}_synapses_contact-sheet.png", name, shown, um, ab_ch, bar_exf)
        every = [h["exf"] for h in hits]
        outside = [e for e in every if not EXF_EXPECTED[0] <= e <= EXF_EXPECTED[1]]
        accepted = [h for h, d in zip(logged, decisions) if str(d or "").strip().upper() == "Y"]
        yes = [h["exf"] for h in accepted]
        no = sum(str(d or "").strip().upper() == "N" for d in decisions)
        quoted = sample_exf(accepted, [h for h in hits if h["ab_pos"]], hits, is_synaptic)
        positive = [h for h in hits if h["ab_pos"]]
        counted, counted_on = ((accepted, "accepted synapses") if len(accepted) >= MIN_ACCEPTED else
                               (positive, "antibody-positive synapses") if is_synaptic and len(positive) >= MIN_FOR_EXF else
                               (hits, "all double peaks (includes membrane pairs)"))
        exf = quoted.get("ExF")
        fwhm = ab_n.get("fwhm_nm")
        rounded = lambda v, d=1: None if v is None else round(v, d)
        built = structure.summarise(counted, hits, ab_n.get("area_px"), um, quoted, folder / f"{name}_synapses_structure.png",
                                    name, eff.colour_of(ab_ch) if ab_ch else None,
                                    second=(ab2_ch["label"], eff.colour_of(ab2_ch)) if ab2_ch else None)
        summary.append({
            **quoted, **built,
            # classes are counted on the synapses the expansion factor rests on, so membrane pairs stay out where possible
            **{k: sum(h["class"] == c for h in counted) for k, c in (("Type I Macular", "Type I, macular"),
               ("Type I Perforated", "Type I, perforated"), ("Type II (putative)", "Type II (putative)"))},
            "Classes Counted On": counted_on,
            "Pixel (nm, as imaged)": round(um * 1000, 1), "Pixel (nm, corrected)": rounded(um * 1000 / exf) if exf else None,
            "Resolution FWHM (nm, as imaged)": rounded(fwhm, 0),
            "Resolution (nm, corrected)": rounded(fwhm / exf) if fwhm and exf else None, "Resolution n": ab_n.get("fwhm_n"),
            "AB Labelled Synapses (%)": rounded(ab_n.get("labelled_pct")), "AB Signal at Synapses (σ)": rounded(ab_n.get("signal")),
            "AB Puncta": ab_n.get("puncta"), "AB Puncta on Synapses (%)": rounded(ab_n.get("on_pct")),
            "AB Enrichment (× chance)": rounded(ab_n.get("enrichment")),
            "Contact Sheet": str(folder / f"{name}_synapses_contact-sheet.png") if shown else None,
            "Sample": name, "Antibody": label, "Synaptic": "yes" if is_synaptic else "no",
            "Planes": len({h["z"] for h in hits}), "Synapses found": len(hits), "AB+": sum(h["ab_pos"] for h in hits),
            "ExF, all found": summarise(every), "ExF, AB+ only": summarise([h["exf"] for h in hits if h["ab_pos"]]),
            "ExF, logged": summarise([h["exf"] for h in logged]), "ExF, accepted": summarise(yes),
            "Accepted / Rejected / Undecided": f"{len(yes)} / {no} / {len(logged) - len(yes) - no}",
            "Found outside 16–24 (%)": round(100 * len(outside) / len(every), 1) if every else None,
        })
        s = summary[-1]
        print(f"   {s['Synapses found']} synapse profile(s), {s['AB+']} antibody-positive\n"
              f"   ExF, all found : {s['ExF, all found']}\n   ExF, AB+ only  : {s['ExF, AB+ only']}\n"
              f"   ExF, accepted  : {s['ExF, accepted']}\n"
              f"   ExF quoted     : {s.get('ExF')} ± {s.get('ExF s.d.')}, 95% CI {s.get('ExF 95% CI')}, "
              f"{s.get('ExF at Window Edge (%)')}% at the window edge  ({s.get('ExF Basis')}; fields {s.get('ExF Fields')}, "
              f"n = {s.get('ExF n')}); Gaussian fit {s.get('ExF (Gaussian fit)')}\n"
              f"   antibody       : {s['AB Labelled Synapses (%)']}% of synapses labelled; {s['AB Puncta on Synapses (%)']}% of "
              f"{s['AB Puncta']} puncta on synapses ({s['AB Enrichment (× chance)']}x chance)\n"
              f"   resolution     : {s['Resolution FWHM (nm, as imaged)']} nm as imaged, {s['Resolution (nm, corrected)']} nm corrected "
              f"(n = {s['Resolution n']}); pixel {s['Pixel (nm, corrected)']} nm corrected\n"
              f"   DP–DP          : {s.get('DP–DP (nm, corrected)')} nm (n = {s.get('DP–DP n')}); ExF from DP–DP {s.get('ExF from DP–DP (÷67 nm)')}\n"
              f"   anisotropy     : {s.get('ExF Anisotropy (%)')}% (p = {s.get('Anisotropy p')}); field CV {s.get('ExF Field CV (%)')}%\n"
              f"   antibody at    : {s.get('AB to PSD (nm, corrected; + = towards DP)')} nm from the PSD, "
              f"{s.get('AB to DP (nm, corrected)')} nm from the DPs (n = {s.get('AB Offset n')})\n"
              f"   PSD            : length {s.get('PSD Length (nm, corrected)')} nm, thickness {s.get('PSD Thickness (nm, corrected, with blur)')} nm; "
              f"perforated {s.get('Perforated (%)')}%\n"
              f"   by depth       : labelled near faces {s.get('AB Labelled Near Faces (%)')}%, at centre {s.get('AB Labelled At Centre (%)')}%; "
              f"confirmed synapses {s.get('Confirmed Synapses / 100 µm² (corrected)')} per 100 µm²\n")
        try:
            save()
        except PermissionError:
            print(f"   ({LOG_NAME} is open in Excel: this sample is kept and written with the next one)\n")
    save()   # rows of samples not gone through this time stay in the log
    print(f"Done. Open {log}, mark Accept Y or N, and run this again for the accepted-only expansion factor.")
    return 0


def add_links(text=""):
    """Save a Neuroglancer view for every logged synapse that has no link yet, and put the
    links in the log. Uses only what the log already holds (slice and position), so nothing
    is measured again."""
    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(core.SETTINGS_FILE, encoding="utf-8-sig")
    log = Path(cfg.get("paths", "synapse_folder", fallback="").strip() or Path(core.DEFAULT_SHOTS).parent / "Zarr Synapse ExF") / LOG_NAME
    try:
        wb = load_workbook(log)
    except PermissionError:
        sys.exit(f"{LOG_NAME} is open in Excel. Close it and run this again.")
    ws = wb["Synapses"]
    at = {c.value: i for i, c in enumerate(ws[HEADER_ROW]) if c.value}
    made = 0
    for row in ws.iter_rows(min_row=HEADER_ROW + 1):
        name, cell = row[at["Sample"]].value, row[at["Neuroglancer"]]
        if not name or (text or "").lower() not in name.lower():
            continue
        h = {"col": row[at["X (px)"]].value, "row": row[at["Y (px)"]].value, "z": row[at["Slice"]].value}
        url = synapse_link(out_root, name, int(row[at["#"]].value), h)
        if url:
            cell.value, cell.hyperlink = "open", url
            cell.font = Font(color="0563C1", underline="single")
            made += 1
    wb.save(log)
    print(f"{made} synapse link(s) in {log}")
    return 0


def main():
    global TOP_N
    p = argparse.ArgumentParser(description="Find synapses in the pan stain and measure the expansion factor (DP-PSD / 82 nm).")
    p.add_argument("text", nargs="?", help="go through the samples whose names contain this (omit to be asked)")
    p.add_argument("--synaptic", choices=("y", "n", "auto"),
                   help="answer the 'is it a synaptic antibody' question in advance; auto = as listed in ANTIBODIES")
    p.add_argument("--max-planes", type=int, default=12, help="at most this many planes per sample, evenly spread (default 12)")
    p.add_argument("--no-links", action="store_true", help="do not save Neuroglancer views of the synapses on the server")
    p.add_argument("--new-only", action="store_true",
                   help="leave samples already in the log as they are; measure only the others (to resume a stopped run)")
    p.add_argument("--links", action="store_true",
                   help="only save a Neuroglancer view for every synapse already in the log, and link them there")
    p.add_argument("--out", help="folder for the log and pictures, instead of the one in settings.ini")
    p.add_argument("--top", type=int, default=TOP_N, help=f"synapses pictured and logged per sample (default {TOP_N})")
    args = p.parse_args()
    TOP_N = args.top
    return add_links(args.text) if args.links else run(args)


if __name__ == "__main__":
    sys.exit(main())
