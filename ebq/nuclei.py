"""Nuclei in a 3D pan-stain volume, and their sizes, for an expansion-factor ruler that does
not depend on synapse structure.

In the pan-ASLM pan stain, binned to a few micrometres per voxel, a nucleus is a bright,
smooth, round body (often with one or two brighter nucleoli) standing out from speckled
neuropil. Tiles differ in brightness and the seams between them are dark, so nothing here uses
a global threshold:

  1. Background = the volume smoothed at BACKGROUND_UM. Signal = volume - background.
  2. Seeds = maxima of the Laplacian-of-Gaussian response to the signal at nucleus scales
     (radii spanning DIAMETER_UM), SEED_SIGMAS noise-widths above zero.
  3. Around each seed, a box of 1.5 x the largest radius: the nucleus is the connected region,
     containing the seed, of the lightly smoothed signal above LEVEL_FRACTION of the seed's
     signal; holes filled. Kept when its equivalent diameter is in DIAMETER_UM, it is convex
     enough (solidity), not too elongated (inertia aspect), and it touches neither the volume
     border nor unstored (zero) voxels. Later seeds inside a kept nucleus are skipped.

Output per nucleus: volume and projected area as imaged, equivalent diameter, aspect, centroid.
An expansion factor follows only once nuclei of the same tissue have been measured
unexpanded: ExF = (V_expanded / V_unexpanded)^(1/3). Until then the sizes implied before
expansion at a given ExF are reported, as a sanity check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class NucleusSettings:
    diameter_um: tuple = (80.0, 400.0)   # as imaged (5-25 µm of tissue at ~16x)
    background_um: float = 250.0         # background is the volume smoothed at this scale
    seed_sigmas: float = 4.0             # a seed's LoG response is this many noise-widths above zero
    level_fraction: float = 0.5          # the nucleus boundary is at this share of the body's signal
    min_solidity: float = 0.65           # skimage's 3D convex hull gives ~0.78 for a perfect sphere
    max_aspect: float = 2.2              # longest / shortest principal extent
    open_radius: int = 1                 # voxels: cuts the thin tendrils that join a nucleus to its surround
    max_seeds: int = 5000


DEFAULT = NucleusSettings()


def signal_and_seeds(vol, voxel_um, s: NucleusSettings = DEFAULT):
    """(signal above local background, seeds as (z, y, x) int array sorted strongest first, LoG response)."""
    from scipy import ndimage
    from skimage import feature

    v = np.asarray(vol, np.float32)
    valid = v > 0
    background = ndimage.gaussian_filter(v, s.background_um / voxel_um)
    signal = np.where(valid, v - background, 0.0).astype(np.float32)
    radii_um = np.geomspace(s.diameter_um[0] / 2, s.diameter_um[1] / 2, 4)
    response = None
    for r in radii_um:
        sig = max(1.0, r / voxel_um / math.sqrt(3))
        log = -ndimage.gaussian_laplace(signal, sig) * sig ** 2
        response = log if response is None else np.maximum(response, log)
    noise = 1.4826 * float(np.median(np.abs(response[valid] - np.median(response[valid])))) or 1.0
    min_dist = max(2, int(radii_um[0] / voxel_um * 0.8))
    seeds = feature.peak_local_max(response, min_distance=min_dist, threshold_abs=s.seed_sigmas * noise,
                                   labels=valid.astype(np.uint8), exclude_border=False)
    if len(seeds):
        seeds = seeds[np.argsort(response[tuple(seeds.T)])[::-1][:s.max_seeds]]
    return signal, seeds, response


def grow_nucleus(signal_smooth, seed, voxel_um, s: NucleusSettings):
    """Mask (in a local box) of the nucleus around one seed, or None. Returns (mask, box origin)."""
    from scipy import ndimage

    r_max = s.diameter_um[1] / 2 / voxel_um
    r_min = s.diameter_um[0] / 2 / voxel_um
    half = int(1.5 * r_max) + 2
    z, y, x = seed
    z0, y0, x0 = (max(0, v - half) for v in (z, y, x))
    box = signal_smooth[z0:z + half + 1, y0:y + half + 1, x0:x + half + 1]
    # the nucleus body's brightness (not the seed voxel's: the seed usually sits on a nucleolus)
    # against its own surround, so that a bright tile does not swallow the nucleus
    bz, by, bx = np.ogrid[:box.shape[0], :box.shape[1], :box.shape[2]]
    d2 = (bz - (z - z0)) ** 2 + (by - (y - y0)) ** 2 + (bx - (x - x0)) ** 2
    core = d2 <= (0.6 * r_min) ** 2
    surround = d2 >= (1.2 * r_max) ** 2
    body = float(np.median(box[core])) if core.any() else float(signal_smooth[z, y, x])
    around = float(np.median(box[surround])) if surround.any() else 0.0
    level = around + s.level_fraction * (body - around)
    if body <= around:
        return None, None
    above = ndimage.binary_opening(box > level, morphology_ball(s.open_radius))
    labels, _ = ndimage.label(above)
    lab = labels[z - z0, y - y0, x - x0]
    if lab == 0:
        return None, None
    mask = ndimage.binary_fill_holes(labels == lab)
    return mask, (z0, y0, x0)


def morphology_ball(radius):
    from skimage import morphology

    return morphology.ball(radius)


def find_nuclei_3d(vol, voxel_um, s: NucleusSettings = DEFAULT, stats: dict | None = None):
    """Complete nuclei in a (z, y, x) volume with isotropic voxels of voxel_um micrometres.
    Returns (list of nucleus dicts, label image)."""
    from scipy import ndimage
    from skimage import measure

    if stats is None:
        stats = {}
    vol = np.asarray(vol)
    valid = vol > 0
    signal, seeds, _ = signal_and_seeds(vol, voxel_um, s)
    smooth = ndimage.gaussian_filter(signal, 1.0)
    invalid_near = ndimage.binary_dilation(~valid, iterations=2) if (~valid).any() else None
    labels = np.zeros(vol.shape, np.int32)
    lo, hi = s.diameter_um
    out = []
    stats.update(seeds=int(len(seeds)), grown=0, size_ok=0, inside=0, shape_ok=0, duplicate=0)
    for seed in seeds:
        if labels[tuple(seed)]:
            stats["duplicate"] += 1
            continue
        mask, origin = grow_nucleus(smooth, seed, voxel_um, s)
        if mask is None:
            continue
        stats["grown"] += 1
        volume = mask.sum() * voxel_um ** 3
        diameter = 2 * (3 * volume / (4 * math.pi)) ** (1 / 3)
        if not (lo <= diameter <= hi):
            continue
        stats["size_ok"] += 1
        z0, y0, x0 = origin
        zz, yy, xx = np.nonzero(mask)
        bz0, by0, bx0, bz1, by1, bx1 = zz.min() + z0, yy.min() + y0, xx.min() + x0, zz.max() + z0 + 1, yy.max() + y0 + 1, xx.max() + x0 + 1
        touches_box = zz.min() == 0 or yy.min() == 0 or xx.min() == 0 or zz.max() == mask.shape[0] - 1 or yy.max() == mask.shape[1] - 1 or xx.max() == mask.shape[2] - 1
        at_border = bz0 == 0 or by0 == 0 or bx0 == 0 or bz1 == vol.shape[0] or by1 == vol.shape[1] or bx1 == vol.shape[2]
        if touches_box or at_border:
            continue
        sub = (slice(z0, z0 + mask.shape[0]), slice(y0, y0 + mask.shape[1]), slice(x0, x0 + mask.shape[2]))
        if invalid_near is not None and invalid_near[sub][mask].any():
            continue
        stats["inside"] += 1
        props = measure.regionprops(mask.astype(np.uint8))[0]
        solidity = float(props.solidity)
        ev = np.sqrt(np.clip(props.inertia_tensor_eigvals, 1e-9, None))
        aspect = float(ev[0] / ev[-1])
        if solidity < s.min_solidity or aspect > s.max_aspect:
            continue
        stats["shape_ok"] += 1
        label = len(out) + 1
        labels[sub][mask] = label
        centroid = tuple(float(c + o) for c, o in zip(props.centroid, origin))
        out.append({"label": label, "centroid": centroid, "seed": tuple(int(v) for v in seed),
                    "volume_um3": float(volume), "diameter_um": float(diameter),
                    "projected_um2": float(mask.any(axis=0).sum() * voxel_um ** 2),
                    "solidity": solidity, "aspect": aspect, "depth_voxels": int(bz1 - bz0),
                    "signal": float(smooth[tuple(seed)]),
                    "bbox": (int(bz0), int(by0), int(bx0), int(bz1), int(by1), int(bx1))})
    return out, labels


def summarise(nuclei, exf_hint=None, tissue_mm3=None):
    """Mean +- sd of the sizes; the sizes implied before expansion at exf_hint; density per mm³ of tissue."""
    if not nuclei:
        return {"n": 0}
    vol = np.array([n["volume_um3"] for n in nuclei])
    area = np.array([n["projected_um2"] for n in nuclei])
    dia = np.array([n["diameter_um"] for n in nuclei])
    sd = lambda v: float(v.std(ddof=1)) if len(v) > 1 else 0.0
    out = {"n": len(nuclei), "volume_um3_mean": float(vol.mean()), "volume_um3_sd": sd(vol),
           "volume_um3_median": float(np.median(vol)),
           "projected_um2_mean": float(area.mean()), "projected_um2_sd": sd(area),
           "diameter_um_mean": float(dia.mean()), "diameter_um_sd": sd(dia), "diameter_um_median": float(np.median(dia))}
    if exf_hint:
        out["exf_hint"] = exf_hint
        out["implied_diameter_tissue_um"] = float(dia.mean() / exf_hint)
        out["implied_volume_tissue_um3"] = float(vol.mean() / exf_hint ** 3)
        if tissue_mm3:
            out["nuclei_per_mm3_tissue"] = len(nuclei) / tissue_mm3
    return out


def exf_from_nuclei(volume_expanded_um3, volume_unexpanded_um3):
    return (volume_expanded_um3 / volume_unexpanded_um3) ** (1 / 3)
