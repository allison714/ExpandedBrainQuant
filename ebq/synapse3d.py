"""Synapses in a 3D pan-stain block, found as bright plates.

The 2D tool found a PSD as a bright bar on one plane and read the profile across it, so only
synapses lying side-on in that plane were seen. In an isotropic volume a PSD is a bright
plate: at the scale of its thickness the Hessian has one strongly negative eigenvalue (across
the plate) and two near zero (along it). The eigenvector of that eigenvalue is the plate's
normal, so the profile can be taken along it whatever way the synapse faces.

Steps (every number is a setting)
  1. Smooth the block (SMOOTH_PX). Plateness = -lambda_3 where lambda_3 << 0 and the other two
     eigenvalues are small (a tube has two strongly negative eigenvalues and is left out).
  2. Seeds: local maxima of plateness above RIDGE_PERCENTILE of the block, at voxels brighter
     than BRIGHT_PERCENTILE. Connected components are NOT used: in dense tissue they merge a
     PSD with the membranes around it into blobs whose shape says nothing about the PSD.
  3. At each seed the Hessian (averaged over a small neighbourhood) gives the normal and the
     two in-plate directions. The plate's extent along each in-plate direction is how far
     plateness stays above EXTENT_FRACTION of its value at the seed; the longer extent must be
     a PSD's length (PSD_LENGTH_NM).
  4. Along the normal through the seed: a profile averaged over a disc in the plate (radius
     up to AVERAGE_HALF_NM) and a thin line (LINE_PX square). profile.measure_profile gates it
     as a synapse and reads the DP-PSD distance.
  5. The brighter density must rank above PSD_RANK of the block (membrane pairs are dimmer).
  6. Seeds on the same plate give the same synapse: hits closer than MERGE_FRACTION of the PSD
     length are merged, the best fit kept.
  7. For each antibody channel: the brightest voxel in the synapse's footprint in noise-widths,
     and where along the normal the antibody peaks (for pre/post alignment between antibodies).

Coordinates in the result are (z, y, x) voxels of the block passed in. The block must be
isotropic (or near enough: the 203 x 203 x 200 nm pan-ASLM voxels are treated as isotropic).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .profile import DEFAULT as PROFILE, ProfileSettings, measure_profile


@dataclass(frozen=True)
class FinderSettings:
    smooth_px: float = 1.0
    psd_sigma_nm: float = 15.0          # half the thickness of a PSD: scale of the plate filter
    psd_length_nm: tuple = (120.0, 900.0)
    ridge_percentile: float = 97.0
    bright_percentile: float = 80.0
    psd_rank: float = 0.995             # the brighter density is brighter than this share of the block
    ab_sigmas: float = 6.0              # antibody-positive: noise-widths above the block's median
    seed_min_distance: int = 2          # voxels between local maxima used as seeds
    max_seeds: int = 20000              # the strongest seeds are tried when there are more
    extent_gap_px: float = 1.0          # the plate ends where smoothed plateness falls under the seed threshold
    search_cone_deg: float = 40.0       # directions within this angle of the Hessian normal are tried
    search_directions: int = 24         # ...this many of them, plus the normal itself
    merge_fraction: float = 1.0         # hits closer than this x PSD length are one synapse
    profile: ProfileSettings = PROFILE


DEFAULT = FinderSettings()


def noise_of(vol, valid):
    middle = float(np.median(vol[valid]))
    return middle, 1.4826 * float(np.median(np.abs(vol[valid] - middle))) or 1.0


def hessian(pan, sigma):
    """The six Hessian components (rr, rc, rz-style ordering from skimage) as a list."""
    from skimage import feature

    return feature.hessian_matrix(pan, sigma=sigma, order="rc", use_gaussian_derivatives=False)


def plateness_from(h):
    from skimage import feature

    e = feature.hessian_matrix_eigvals(h)                 # (3, z, y, x), decreasing
    l2, l3 = e[1], e[2]
    with np.errstate(invalid="ignore"):
        return np.where((l3 < 0) & (np.abs(l2) < 0.5 * np.abs(l3)), -l3, 0.0).astype(np.float32)


def plateness(pan, sigma):
    return plateness_from(hessian(pan, sigma))


def local_frame(h, zyx, radius=1):
    """(normal, e1, e2) at a voxel from the Hessian averaged over a (2 radius + 1)^3 neighbourhood.
    h is the list from skimage.feature.hessian_matrix in 'rc' order: zz, zy, zx, yy, yx, xx."""
    z, y, x = (int(round(v)) for v in zyx)
    sl = tuple(slice(max(0, v - radius), v + radius + 1) for v in (z, y, x))
    m = [float(c[sl].mean()) for c in h]
    H = np.array([[m[0], m[1], m[2]], [m[1], m[3], m[4]], [m[2], m[4], m[5]]])
    vals, vecs = np.linalg.eigh(H)                       # ascending: vals[0] most negative
    return vecs[:, 0], vecs[:, 2], vecs[:, 1]


def plate_extent(plate, centre, direction, floor, max_px):
    """(reach along +direction, reach along -direction) of the plate from centre, in voxels:
    how far plateness stays above 'floor' (the seed threshold), on a plateness map that has
    been lightly smoothed so that noise along a real PSD does not end it early."""
    from scipy import ndimage

    steps = np.arange(0.0, max_px + 0.5, 1.0)
    reaches = []
    for sign in (1, -1):
        pts = centre[None, :] + sign * steps[:, None] * direction[None, :]
        inside = (pts >= 0).all(axis=1) & (pts <= np.array(plate.shape) - 1).all(axis=1)
        vals = np.full(len(steps), 0.0)
        if inside.any():
            vals[inside] = ndimage.map_coordinates(plate, pts[inside].T, order=1)
        below = np.flatnonzero(vals < floor)
        reaches.append(float(steps[below[0]]) if len(below) else float(steps[-1]))
    return tuple(reaches)


def cone_directions(normal, e1, e2, cone_deg, count):
    """The normal plus 'count' unit vectors spread within cone_deg of it (two rings)."""
    out = [normal]
    for ring, frac in ((cone_deg / 2, 0.4), (cone_deg, 0.6)):
        n = max(1, int(round(count * frac)))
        tilt = np.radians(ring)
        for k in range(n):
            phi = 2 * np.pi * k / n
            out.append(np.cos(tilt) * normal + np.sin(tilt) * (np.cos(phi) * e1 + np.sin(phi) * e2))
    return out


def frame_for(direction, hint):
    """An orthonormal (e1, e2) in the plane perpendicular to 'direction', e1 as close as possible to 'hint'."""
    d = direction / np.linalg.norm(direction)
    a = hint - hint.dot(d) * d
    if np.linalg.norm(a) < 1e-6:
        a = np.cross(d, [1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.cross(d, [0.0, 1.0, 0.0])
    a /= np.linalg.norm(a)
    return a, np.cross(d, a)


def sample_profile(vol, centre, normal, e1, e2, reach, half, line_px):
    """(t, averaged profile over a disc of radius 'half' in the plate, thin-line profile, points)."""
    from scipy import ndimage

    t = np.arange(-reach, reach + 0.25, 0.5)
    r = np.arange(-half, half + 0.5, 1.0)
    u, v = np.meshgrid(r, r, indexing="ij")
    disc = (u ** 2 + v ** 2) <= half ** 2 + 1e-6
    u, v = u[disc], v[disc]
    pts = (centre[None, None, :] + t[:, None, None] * normal[None, None, :]
           + u[None, :, None] * e1[None, None, :] + v[None, :, None] * e2[None, None, :])
    if (pts.min(axis=(0, 1)) < 1).any() or (pts.max(axis=(0, 1)) > np.array(vol.shape) - 2).any():
        return None
    grid = ndimage.map_coordinates(vol, pts.reshape(-1, 3).T, order=1).reshape(len(t), len(u))
    if (grid <= 0).any():                                   # stitching padding
        return None
    lane = np.arange(line_px) - (line_px - 1) / 2
    lu, lv = np.meshgrid(lane, lane, indexing="ij")
    lpts = (centre[None, None, :] + t[:, None, None] * normal[None, None, :]
            + lu.ravel()[None, :, None] * e1[None, None, :] + lv.ravel()[None, :, None] * e2[None, None, :])
    thin = ndimage.map_coordinates(vol, lpts.reshape(-1, 3).T, order=1).reshape(len(t), -1).mean(axis=1)
    return t, grid.mean(axis=1), thin, pts


def find_synapses_3d(pan_raw, um, antibodies=None, s: FinderSettings = DEFAULT, stats: dict | None = None):
    """Synapses in one (z, y, x) block.

    pan_raw     the pan channel, any dtype; zeros mean 'nothing stored' (stitching padding)
    um          voxel size in micrometres, as imaged (isotropic)
    antibodies  {name: (z, y, x) block of that channel}, same shape as pan_raw
    stats       optional dict; filled with how many candidates passed each gate
    Returns a list of dicts, one per synapse.
    """
    from scipy import ndimage
    from skimage import feature

    if stats is None:
        stats = {}
    stats.update(seeds=0, extent_ok=0, sampled=0, profile_ok=0, bright_ok=0, merged=0)
    pan_raw = np.asarray(pan_raw)
    valid = pan_raw > 0
    if valid.mean() < 0.05:
        return []
    pan = ndimage.gaussian_filter(pan_raw.astype(np.float32), s.smooth_px)
    abs_ = {}
    for name, raw in (antibodies or {}).items():
        sm = ndimage.gaussian_filter(np.asarray(raw).astype(np.float32), s.smooth_px)
        abs_[name] = (sm, noise_of(sm, valid))
    px_nm = s.profile.px_per_nm(um)
    sigma = max(1.0, s.psd_sigma_nm * px_nm)
    h = hessian(pan, sigma)
    plate = plateness_from(h)
    reach = s.profile.profile_half_nm * px_nm
    lo_len, hi_len = (n * px_nm for n in s.psd_length_nm)
    # seeds within the profile's reach of a face cannot be sampled, and the Hessian is unreliable there
    margin = int(reach + s.profile.average_half_nm * px_nm) + 1
    # thresholds from the interior only: the Hessian's artefact on the six faces is strong enough
    # to be the top few per cent of the block and would hide every real plate
    interior = np.zeros_like(valid)
    m = min(margin, min(valid.shape) // 4)
    interior[m:-m, m:-m, m:-m] = valid[m:-m, m:-m, m:-m]
    if interior.mean() < 0.02:
        return []
    plate_thr = float(np.percentile(plate[interior], s.ridge_percentile))
    bright_thr = float(np.percentile(pan[interior], s.bright_percentile))
    ranks = np.sort(pan[interior].ravel()[::97])
    # (the brightness rule is applied to the map, not through 'labels': with labels,
    # peak_local_max applies exclude_border inside every labelled region)
    seeds = feature.peak_local_max(np.where(pan > bright_thr, plate, 0.0), min_distance=s.seed_min_distance,
                                   threshold_abs=plate_thr, exclude_border=margin)
    if len(seeds) > s.max_seeds:
        seeds = seeds[np.argsort(plate[tuple(seeds.T)])[::-1][:s.max_seeds]]
    stats["seeds"] = int(len(seeds))
    plate_soft = ndimage.gaussian_filter(plate, s.extent_gap_px)
    hits = []
    for seed in seeds:
        centre = seed.astype(float)
        normal, e1, e2 = local_frame(h, centre)
        p1, m1 = plate_extent(plate_soft, centre, e1, plate_thr, hi_len + 1)
        p2, m2 = plate_extent(plate_soft, centre, e2, plate_thr, hi_len + 1)
        ext1, ext2 = p1 + m1, p2 + m2
        length = max(ext1, ext2)
        if not (lo_len <= length <= hi_len):
            continue
        stats["extent_ok"] += 1
        # a seed can sit anywhere on the plate (often its rim): move to the plate's middle
        centre = centre + e1 * (p1 - m1) / 2 + e2 * (p2 - m2) / 2
        half = min(0.3 * length, s.profile.average_half_nm * px_nm)
        # the Hessian normal is only a hint at this signal level: try a cone of directions
        # around it and keep the one whose profile fits a synapse best
        best = None
        for direction in cone_directions(normal, e1, e2, s.search_cone_deg, s.search_directions):
            f1, f2 = frame_for(direction, e1)
            sampled = sample_profile(pan, centre, direction, f1, f2, reach, half, s.profile.line_px)
            if sampled is None:
                continue
            t, profile, thin, pts = sampled
            hit = measure_profile(t, profile, thin, um, s.profile)
            if hit is not None and (best is None or hit["r2"] > best[0]["r2"]):
                best = (hit, direction, t, profile, thin, pts)
        if best is None:
            continue
        stats["sampled"] += 1
        hit, normal, t, profile, thin, pts = best
        stats["profile_ok"] += 1
        hit["bright"] = float(np.searchsorted(ranks, hit["peak"]) / len(ranks))
        if hit["bright"] < s.psd_rank:
            continue
        stats["bright_ok"] += 1
        hit["seed"] = tuple(int(v) for v in seed)
        hit["centre"] = tuple(float(v) for v in centre + hit["mid"] * normal)   # midway between the densities
        hit["normal"] = tuple(float(v) for v in normal)
        hit["length_px"] = float(length)
        hit["extents_px"] = (float(ext1), float(ext2))
        hit["t"], hit["profile"], hit["thin"], hit["_pts"] = t, profile, thin, pts
        hits.append(hit)

    # seeds on the same plate -> one synapse (best fit kept)
    hits.sort(key=lambda hh: -hh["r2"])
    kept = []
    for hit in hits:
        c = np.array(hit["centre"])
        if any(np.linalg.norm(c - np.array(k["centre"])) < max(3.0, s.merge_fraction * k["length_px"]) for k in kept):
            stats["merged"] += 1
            continue
        kept.append(hit)

    for hit in kept:
        t, pts = hit["t"], hit.pop("_pts")
        f0, f1 = hit["footprint"]
        foot = (t >= f0) & (t <= f1)
        hit["antibodies"] = {}
        for name, (sm, (median, sd)) in abs_.items():
            grid = ndimage.map_coordinates(sm, pts.reshape(-1, 3).T, order=1).reshape(len(t), -1)
            prof = grid.mean(axis=1)
            at = float(t[foot][np.argmax(prof[foot])])
            hit["antibodies"][name] = {
                "sigma": float((grid[foot].max() - median) / sd),
                "positive": bool((grid[foot].max() - median) / sd >= s.ab_sigmas),
                "at_px": at,                              # along the normal, from the seed density
                "at_um": float(at * um),
                "profile": prof,
            }
    return kept


def alignment(hit, a: str, b: str):
    """Signed distance along the synapse normal from antibody a's peak to antibody b's peak, µm
    as imaged. The pre/post separation when a is presynaptic and b postsynaptic (sign is
    arbitrary per synapse: the normal's direction is not oriented pre -> post)."""
    ab = hit["antibodies"]
    if a not in ab or b not in ab:
        return None
    return ab[b]["at_um"] - ab[a]["at_um"]
