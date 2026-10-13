"""The DP-PSD measurement on one intensity profile across a synapse.

This is the measurement of M'Saad, Cairns et al. (Nat. Biotechnol. 2025, Supplementary
Methods), unchanged from the 2D tool: a two-Gaussian fit decides whether the profile is a
synapse seen side-on and where its two peaks are; the distance is then read peak to peak on a
thin line, each peak's top placed between samples by a parabola, and divided by 81.9 nm.

Nothing here knows whether the profile came from a 2D plane or a 3D block. The caller samples
the image and passes:
  t        positions along the normal, in pixels (evenly spaced, 0 at the detected density)
  profile  intensity averaged over the width of the density, at each t
  thin     intensity on a line LINE_PX wide, at each t
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ProfileSettings:
    ref_nm: float = 81.9           # DP-PSD distance before expansion
    exf_nominal: float = 20.0      # sets filter sizes and the profile reach only
    exf_search: tuple = (12.0, 30.0)   # the second peak is looked for at ref_nm x this range
    profile_half_nm: float = 260.0     # the profile runs this far either side of the density (before expansion)
    average_half_nm: float = 90.0      # ...averaged over at most this far either side along it
    line_px: int = 2                   # the reading line is this many pixels wide
    fit_r2: float = 0.90
    min_dip: float = 0.20              # trough at least this share of the smaller peak's height
    peak_ratio: tuple = (0.25, 4.0)    # second peak's height as a share of the first

    def px_per_nm(self, um: float) -> float:
        return self.exf_nominal / (um * 1000)

    def search_px(self, um: float) -> tuple[float, float]:
        return tuple(self.ref_nm * e / (um * 1000) for e in self.exf_search)


DEFAULT = ProfileSettings()


def two_peaks(t, b, a1, m1, s1, a2, m2, s2):
    return b + a1 * np.exp(-(t - m1) ** 2 / (2 * s1 * s1)) + a2 * np.exp(-(t - m2) ** 2 / (2 * s2 * s2))


def measure_profile(t, profile, thin, um, s: ProfileSettings = DEFAULT):
    """Returns a dict (distance_um, exf, exf_fit, summits, fit, r2, dip, ratio, ...) or None."""
    from scipy import optimize, signal

    t = np.asarray(t, float)
    profile = np.asarray(profile, float)
    thin = np.asarray(thin, float)
    step = t[1] - t[0]
    spread = profile.max() - profile.min()
    if spread <= 0:
        return None
    peaks, _ = signal.find_peaks(profile, prominence=0.08 * spread)
    if len(peaks) < 2:
        return None
    first = peaks[np.argmin(np.abs(t[peaks]))]
    if abs(t[first]) > 3:
        return None
    near, far = s.search_px(um)
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
    total = float(((profile[win] - profile[win].mean()) ** 2).sum())
    r2 = 1 - float(((profile[win] - model) ** 2).sum()) / total if total > 0 else 0.0
    b, a1, m1, s1, a2, m2, s2 = fit
    distance = abs(m2 - m1)
    if not (near <= distance <= far):
        return None
    between = (t >= min(m1, m2)) & (t <= max(m1, m2))
    tops = min(two_peaks(m1, *fit), two_peaks(m2, *fit))
    dip = float((tops - two_peaks(t[between], *fit).min()) / max(tops - b, 1e-6))
    ratio = float(a2 / a1) if a1 > 0 else 0.0
    if r2 < s.fit_r2 or dip < s.min_dip or not (s.peak_ratio[0] <= ratio <= s.peak_ratio[1]):
        return None

    def summit(m):
        near_m = np.flatnonzero(np.abs(t - m) <= distance / 3)
        i = near_m[np.argmax(thin[near_m])]
        if not 0 < i < len(t) - 1:
            return float(t[i])
        bend = thin[i - 1] - 2 * thin[i] + thin[i + 1]
        shift = 0.5 * (thin[i - 1] - thin[i + 1]) / bend if bend < 0 else 0.0
        return float(t[i] + float(np.clip(shift, -0.5, 0.5)) * step)

    summits = (summit(m1), summit(m2))
    reading = abs(summits[1] - summits[0])
    if not (near <= reading <= far):
        return None
    mid = (summits[0] + summits[1]) / 2
    return {
        "distance_um": float(reading * um), "exf": float(reading * um * 1000 / s.ref_nm),
        "fit_um": float(distance * um), "exf_fit": float(distance * um * 1000 / s.ref_nm),
        "summits": summits, "mid": float(mid), "gap_px": float(gap), "fit": tuple(float(v) for v in fit),
        "r2": float(r2), "dip": dip, "ratio": ratio, "base": float(b), "widths_px": (float(s1), float(s2)),
        "peak": float(max(two_peaks(m1, *fit), two_peaks(m2, *fit))),
        "footprint": (min(m1, m2) - gap / 4, max(m1, m2) + gap / 4),
    }
