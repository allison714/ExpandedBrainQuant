"""
Structural measurements taken on the synapses that synapse_exf.py finds. Nothing here is
needed for the expansion factor; these are the further numbers a synapse's profile gives.

Per synapse (measure):
  PSD length       the unbroken stretch of the PSD above half its height, along its length
  PSD thickness    full width at half maximum across it, from the fitted Gaussian. Includes the
                   optical blur (about 430 nm as imaged), so it is an upper bound
  DP-DP spacing    distances between neighbouring dense projections along the DP row. The paper
                   (M'Saad, Cairns et al. 2025, Fig. 1o) reports 67 +- 15 nm, read peak to peak
                   on 2-pixel lines, so this is a second, independent ruler for the expansion
  antibody offset  where the antibody peaks across the synapse, measured from the PSD towards
                   the DPs (the paper's Fig. 3mmm-ppp)

Per sample (summarise), on the synapses the expansion factor rests on:
  ExF from DP-DP           mean DP-DP spacing / 67 nm
  anisotropy               whether ExF depends on which way a synapse points: ExF is fitted with
                           a + b cos 2q + c sin 2q over the orientations q; the amplitude as a
                           percentage of a, and a permutation p-value. Isotropic expansion gives
                           an amplitude near zero. (A synapse only reports the expansion along
                           its own trans-synaptic axis, which is what makes this test possible.)
  evenness                 spread of the fields' mean ExF (coefficient of variation)
  labelling by depth       share of synapses carrying antibody near the faces and at the centre
  confirmed-synapse density, perforated share
"""

import math

import numpy as np

DP_NM = 67.0            # spacing of neighbouring dense projections before expansion (the paper, Fig. 1o)
MIN_FOR_ANISOTROPY = 20 # fewer synapses than this cannot show a dependence on orientation
PERMUTATIONS = 1000     # shuffles behind the anisotropy p-value
FACE_FRACTION = 0.2     # the planes this close to either face of the gel are "near the faces"

COLUMNS = [
    "DP–DP (nm, corrected)", "DP–DP n", "ExF from DP–DP (÷67 nm)", "ExF Anisotropy (%)", "Anisotropy p",
    "ExF Field CV (%)", "AB to PSD (nm, corrected; + = towards DP)", "AB to DP (nm, corrected)", "AB Offset n",
    "PSD to Cleft (nm, corrected)", "DP to Cleft (nm, corrected)", "AB to Cleft (nm, corrected; + = DP side)",
    "AB2 Channel", "AB2 Labelled Synapses (%)", "Both / AB1 Only / AB2 Only / Neither (%)", "AB2 on AB1-positive Synapses (%)",
    "AB1 on AB2-positive Synapses (%)", "AB2 to PSD (nm, corrected; + = towards DP)", "AB2 to DP (nm, corrected)",
    "AB2 to Cleft (nm, corrected; + = DP side)", "AB1–AB2 Across the Synapse (nm, corrected; + = AB2 towards DP)", "AB1–AB2 n",
    "PSD Length (nm, corrected)", "PSD Thickness (nm, corrected, with blur)", "AB Labelled Near Faces (%)",
    "AB Labelled At Centre (%)", "Confirmed Synapses / 100 µm² (corrected)", "Perforated (%)", "Structure Figure",
]

METHOD = [
    "Structure (provisional; measured on the synapses the expansion factor rests on)",
    "• DP–DP: peaks along the DP row (a 2-pixel line through its top), neighbouring-peak distances between 67 nm x the search",
    "  window; mean ÷ ExF = spacing in nm. 'ExF from DP–DP' = mean spacing ÷ 67 nm (the paper: 67 ± 15 nm): a second ruler,",
    "  independent of the DP–PSD one. It needs the DP row to be told from the PSD, which is right about nine times in ten.",
    "• ExF Anisotropy: ExF fitted with a + b·cos 2θ + c·sin 2θ over the synapses' orientations θ; amplitude √(b²+c²) as % of a.",
    "  p from {perms} shuffles of the orientations. Near 0% with a large p = no sign that expansion depends on direction.",
    "• ExF Field CV: s.d. of the fields' mean ExF ÷ their mean. It contains sampling noise as well as real unevenness.",
    "• AB to PSD: where the antibody peaks across the synapse, from the PSD peak, + towards the DPs, ÷ ExF. For this the PSD is",
    "  the brighter density (not the antibody's side, which would bias the offset towards zero). AB to DP: distance to the other peak.",
    "• Cleft: the darkest point between the PSD and DP peaks on the 2-pixel line (placed between samples by a parabola). The",
    "  cleft is far narrower than the optics resolve, so this is where it is, not how wide it is. PSD to Cleft and DP to Cleft",
    "  are the distances from each peak to it; AB to Cleft is where the antibody peaks relative to it (+ = presynaptic side).",
    "• Second antibody (AB2): with two antibody channels, AB1 is the one on the longest laser (drawn magenta) and AB2 the next",
    "  (green). AB2 is measured on the same footprint of every synapse and is positive by the same rule. 'AB2 on AB1-positive",
    "  Synapses' is the share of AB1-positive synapses that also carry AB2: each antibody serves as the other's reference.",
    "  AB1–AB2 Across the Synapse is the distance between the two antibodies' peaks along the trans-synaptic axis, on synapses",
    "  positive for both; + means AB2 lies towards the DPs. The two channels are not corrected for colour shift (about a pixel,",
    "  under 10 nm of tissue), and bleed-through between them would pull this distance towards zero.",
    "• PSD Length: unbroken stretch above half height along the PSD. PSD Thickness: FWHM across it from the Gaussian fit;",
    "  both ÷ ExF. Thickness includes the optical blur and is an upper bound. Lengths cut off by the sampled stretch are left out.",
    "• AB Labelled Near Faces / At Centre: share of synapses found that carry antibody, in the planes within {face:g} of the gel's",
    "  thickness of either face, and in the middle {face:g}. A lower value at the centre = the antibody did not penetrate.",
    "• Confirmed Synapses / 100 µm²: antibody-positive synapses per area, area ÷ ExF². Only side-on synapses are found: a lower bound.",
]


def measure(h, um):
    """Add the per-synapse measurements to `h`, in micrometres as imaged (None where not measurable)."""
    from scipy import signal

    import synapse_exf as syn

    psd, dp = h["psd"], 1 - h["psd"]
    step = h["span"][1] - h["span"][0]
    bar = h["span"][-1] / 0.75                              # the bar's length in pixels

    # PSD length: the unbroken stretch around its brightest point that stays above half height
    run = np.nan_to_num(h["runs"][psd] - h["base"], nan=0.0)
    top = int(np.argmax(run))
    above = run >= 0.5 * run[top]
    a = b = top
    while a > 0 and above[a - 1]:
        a -= 1
    while b < len(run) - 1 and above[b + 1]:
        b += 1
    h["psd_len_um"] = None if a == 0 or b == len(run) - 1 else float((b - a + 1) * step * um)   # cut off = unknown
    h["psd_fwhm_um"] = float(2.3548 * h["widths"][psd] * um)

    # DP-DP: neighbouring peaks along the DP row, within the bar
    row = h["runs"][dp] - h["base"]
    v = row[(np.abs(h["span"]) <= 0.5 * bar) & ~np.isnan(row)]
    h["dp_gaps_um"] = []
    if len(v) > 6 and v.max() > v.min():
        peaks, _ = signal.find_peaks(v, prominence=0.1 * (v.max() - v.min()))
        near, far = (DP_NM * e / (um * 1000) for e in syn.EXF_SEARCH)
        h["dp_gaps_um"] = [float(g * um) for g in np.diff(peaks) * step if near <= g <= far]

    # antibody position across the synapse, from the brighter density (taken as the PSD) towards the other
    h["ab_offset_um"] = h["ab_dp_um"] = None
    if h["ab_profile"] is not None and h["ab_sigma"] is not None and h["ab_sigma"] >= syn.AB_SIGMAS and h["ab_at"] is not None:
        t, p = h["t"], h["ab_profile"]
        i = int(np.argmin(np.abs(t - h["ab_at"])))
        at = h["ab_at"]
        if 0 < i < len(t) - 1:
            bend = p[i - 1] - 2 * p[i] + p[i + 1]
            # between samples by a parabola; never more than half a sample from the brightest one
            at = t[i] + float(np.clip(0.5 * (p[i - 1] - p[i + 1]) / bend if bend < 0 else 0.0, -0.5, 0.5)) * (t[1] - t[0])
        bright = int(h["tops"][1] > h["tops"][0])
        towards = math.copysign(1.0, h["summits"][1 - bright] - h["summits"][bright])
        h["ab_offset_um"] = float((at - h["summits"][bright]) * towards * um)
        h["ab_dp_um"] = float(abs(at - h["summits"][1 - bright]) * um)

    # the cleft: the darkest point between the two peaks of the measured line
    t, thin = h["t"], h["thin"]
    bright = int(h["tops"][1] > h["tops"][0])
    towards = math.copysign(1.0, h["summits"][1 - bright] - h["summits"][bright])
    lo, hi = sorted(h["summits"])
    between = np.flatnonzero((t > lo) & (t < hi))
    h["cleft_psd_um"] = h["cleft_dp_um"] = h["ab_cleft_um"] = h["ab2_offset_um"] = h["ab2_dp_um"] = h["ab2_cleft_um"] = h["ab12_um"] = None
    if len(between) >= 3:
        i = between[int(np.argmin(thin[between]))]
        bend = thin[i - 1] - 2 * thin[i] + thin[i + 1]
        cleft = t[i] + float(np.clip(0.5 * (thin[i - 1] - thin[i + 1]) / bend if bend > 0 else 0.0, -0.5, 0.5)) * (t[1] - t[0])
        h["cleft_psd_um"] = float(abs(cleft - h["summits"][bright]) * um)
        h["cleft_dp_um"] = float(abs(h["summits"][1 - bright] - cleft) * um)

        def peak_of(profile, start):
            j = int(np.argmin(np.abs(t - start)))
            if not 0 < j < len(t) - 1:
                return start
            b = profile[j - 1] - 2 * profile[j] + profile[j + 1]
            return t[j] + float(np.clip(0.5 * (profile[j - 1] - profile[j + 1]) / b if b < 0 else 0.0, -0.5, 0.5)) * (t[1] - t[0])

        first = second = None
        if h["ab_offset_um"] is not None:
            first = peak_of(h["ab_profile"], h["ab_at"])
            h["ab_cleft_um"] = float((first - cleft) * towards * um)
        if h.get("ab2_profile") is not None and h["ab2_sigma"] is not None and h["ab2_sigma"] >= syn.AB_SIGMAS:
            second = peak_of(h["ab2_profile"], h["ab2_at"])
            h["ab2_offset_um"] = float((second - h["summits"][bright]) * towards * um)
            h["ab2_dp_um"] = float(abs(second - h["summits"][1 - bright]) * um)
            h["ab2_cleft_um"] = float((second - cleft) * towards * um)
        if first is not None and second is not None:
            h["ab12_um"] = float((second - first) * towards * um)


def anisotropy(angles, values, rng):
    """(amplitude as % of the mean, permutation p) of a cos 2q dependence of `values` on `angles`."""
    design = np.column_stack([np.ones_like(angles), np.cos(2 * angles), np.sin(2 * angles)])

    def amplitude(v):
        a, b, c = np.linalg.lstsq(design, v, rcond=None)[0]
        return math.hypot(b, c) / a

    seen = amplitude(values)
    shuffled = [amplitude(rng.permutation(values)) for _ in range(PERMUTATIONS)]
    return 100 * seen, float((1 + sum(s >= seen for s in shuffled)) / (1 + PERMUTATIONS))


def spread(values, scale=1.0):
    """'mean ± s.d.' of values x scale, or None."""
    v = np.asarray([x for x in values if x is not None], float) * scale
    return None if not len(v) else f"{v.mean():.0f} ± {v.std(ddof=1) if len(v) > 1 else 0:.0f}"


def summarise(group, hits, area_px, um, quoted, png, name, ab_colour, second=None):
    """Sample-level structure numbers (cells for the Summary sheet), and the figure `png`.

    group - the synapses the expansion factor rests on; hits - every double peak found
    quoted - the sample's expansion-factor cells from synapse_exf.sample_exf
    """
    import ab_efficiency as eff
    import synapse_exf as syn

    exf = quoted.get("ExF")
    if not exf or not group:
        return {}
    rng = np.random.default_rng(0)
    to_nm = 1000 / exf                                    # micrometres as imaged -> nanometres, corrected
    cells = {}
    gaps = [g for h in group for g in h["dp_gaps_um"]]
    if gaps:
        cells["DP–DP (nm, corrected)"], cells["DP–DP n"] = spread(gaps, to_nm), len(gaps)
        cells["ExF from DP–DP (÷67 nm)"] = round(float(np.mean(gaps)) * 1000 / DP_NM, 1)
    values = np.asarray([h["exf"] for h in group])
    angles = np.asarray([h["theta"] for h in group])
    fit = None
    if len(group) >= MIN_FOR_ANISOTROPY:
        amp, p = anisotropy(angles, values, rng)
        cells["ExF Anisotropy (%)"], cells["Anisotropy p"] = round(amp, 1), round(p, 3)
        fit = np.linalg.lstsq(np.column_stack([np.ones_like(angles), np.cos(2 * angles), np.sin(2 * angles)]), values, rcond=None)[0]
    if quoted.get("ExF Fields"):
        cells["ExF Field CV (%)"] = round(100 * quoted["ExF s.d."] / exf, 1)
    offsets = [h["ab_offset_um"] for h in group if h["ab_offset_um"] is not None]
    if offsets:
        cells["AB to PSD (nm, corrected; + = towards DP)"] = spread(offsets, to_nm)
        cells["AB to DP (nm, corrected)"] = spread([h["ab_dp_um"] for h in group], to_nm)
        cells["AB Offset n"] = len(offsets)
    cells["PSD to Cleft (nm, corrected)"] = spread([h["cleft_psd_um"] for h in group], to_nm)
    cells["DP to Cleft (nm, corrected)"] = spread([h["cleft_dp_um"] for h in group], to_nm)
    cells["AB to Cleft (nm, corrected; + = DP side)"] = spread([h["ab_cleft_um"] for h in group], to_nm)
    offsets2, between12 = [], []
    if second is not None:
        one = np.array([h["ab_sigma"] is not None and h["ab_sigma"] >= syn.AB_SIGMAS for h in hits])
        two = np.array([bool(h.get("ab2_pos")) for h in hits])
        share = lambda mask: round(100 * float(mask.mean()), 1)
        cells["AB2 Channel"] = second[0]
        cells["AB2 Labelled Synapses (%)"] = share(two)
        cells["Both / AB1 Only / AB2 Only / Neither (%)"] = f"{share(one & two)} / {share(one & ~two)} / {share(~one & two)} / {share(~one & ~two)}"
        if one.any():
            cells["AB2 on AB1-positive Synapses (%)"] = round(100 * float(two[one].mean()), 1)
        if two.any():
            cells["AB1 on AB2-positive Synapses (%)"] = round(100 * float(one[two].mean()), 1)
        offsets2 = [h["ab2_offset_um"] for h in group if h.get("ab2_offset_um") is not None]
        between12 = [h["ab12_um"] for h in group if h.get("ab12_um") is not None]
        cells["AB2 to PSD (nm, corrected; + = towards DP)"] = spread(offsets2, to_nm)
        cells["AB2 to DP (nm, corrected)"] = spread([h.get("ab2_dp_um") for h in group], to_nm)
        cells["AB2 to Cleft (nm, corrected; + = DP side)"] = spread([h.get("ab2_cleft_um") for h in group], to_nm)
        cells["AB1–AB2 Across the Synapse (nm, corrected; + = AB2 towards DP)"] = spread(between12, to_nm)
        cells["AB1–AB2 n"] = len(between12) or None
    lengths = [h["psd_len_um"] for h in group if h["psd_len_um"] is not None]
    cells["PSD Length (nm, corrected)"] = spread(lengths, to_nm)
    cells["PSD Thickness (nm, corrected, with blur)"] = spread([h["psd_fwhm_um"] for h in group], to_nm)

    # labelling by depth: every double peak found is the denominator, plane by plane
    planes = sorted({h["z"] for h in hits})
    by_plane = {z: [h["ab_sigma"] is not None and h["ab_sigma"] >= syn.AB_SIGMAS for h in hits if h["z"] == z] for z in planes}
    depth = None
    if len(planes) >= 5 and any(h["ab_sigma"] is not None for h in hits):
        edge = max(1, int(round(len(planes) * FACE_FRACTION)))
        mid = (len(planes) - edge) // 2
        share = lambda zs: round(100 * float(np.mean([x for z in zs for x in by_plane[z]])), 1) if any(by_plane[z] for z in zs) else None
        cells["AB Labelled Near Faces (%)"] = share(planes[:edge] + planes[-edge:])
        cells["AB Labelled At Centre (%)"] = share(planes[mid:mid + edge])
        depth = [(z, 100 * float(np.mean(by_plane[z])), len(by_plane[z])) for z in planes if by_plane[z]]
    confirmed = sum(h["ab_pos"] for h in hits)
    if confirmed and area_px:
        cells["Confirmed Synapses / 100 µm² (corrected)"] = round(confirmed / (area_px * um * um / exf ** 2) * 100, 2)
    type1 = [h for h in group if h["class"].startswith("Type I,")]
    if type1:
        cells["Perforated (%)"] = round(100 * sum(h["class"] == "Type I, perforated" for h in type1) / len(type1), 1)

    # ---- the figure: six small panels
    fig, axes = eff.figure(2, 3, figsize=(15, 8.5))
    ink, grid = eff.INK, "#2A2A2A"

    def dress(ax, title, xlabel, ylabel):
        ax.set_title(title, color=ink, fontsize=10, loc="left")
        ax.set_xlabel(xlabel, color=ink, fontsize=9)
        ax.set_ylabel(ylabel, color=ink, fontsize=9)
        ax.grid(color=grid, lw=0.6)

    def empty(ax, why):
        ax.text(0.5, 0.5, why, transform=ax.transAxes, ha="center", va="center", color=eff.MUTED, fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])

    ax = axes[0][0]
    degrees = np.degrees(angles) % 180
    ax.scatter(degrees, values, s=14, color="white", alpha=0.7, linewidths=0)
    if fit is not None:
        q = np.linspace(0, math.pi, 181)
        ax.plot(np.degrees(q), fit[0] + fit[1] * np.cos(2 * q) + fit[2] * np.sin(2 * q), color=syn.FIT_BLUE, lw=2)
        note = f"Amplitude {cells['ExF Anisotropy (%)']}% of the mean, p = {cells['Anisotropy p']}"
    else:
        note = f"Fewer than {MIN_FOR_ANISOTROPY} synapses: not tested"
    dress(ax, f"ExF by Synapse Orientation\n{note}", "Orientation of the PSD in the image (degrees)", "ExF")

    ax = axes[0][1]
    fields = {}
    for h in group:
        fields.setdefault(h["fov"], []).append(h["exf"])
    kept = sorted((k, v) for k, v in fields.items() if len(v) >= syn.MIN_PER_FOV)
    if kept:
        means = [np.mean(v) for _, v in kept]
        ax.errorbar(range(1, len(kept) + 1), means, yerr=[np.std(v, ddof=1) / math.sqrt(len(v)) for _, v in kept],
                    fmt="o", color="white", ecolor=eff.MUTED, ms=5, lw=1)
        ax.axhline(exf, color=syn.FIT_BLUE, lw=1.5)
        dress(ax, f"ExF by Field of View (Mean ± s.e.m.)\nCV across fields {cells.get('ExF Field CV (%)', '–')}%",
              "Field of view (plane or tile)", "ExF")
    else:
        empty(ax, "No field of view with enough synapses")
        dress(ax, "ExF by Field of View", "", "")

    ax = axes[0][2]
    if gaps:
        ax.hist(np.asarray(gaps) * to_nm, bins=np.arange(30, 141, 6), color="white", alpha=0.85, edgecolor=eff.SURFACE)
        ax.axvline(DP_NM, color=syn.FIT_BLUE, lw=1.5)
        dress(ax, f"DP–DP Spacing: {cells['DP–DP (nm, corrected)']} nm (n = {len(gaps)})\n"
                  f"Blue = 67 nm (paper).  ExF from DP–DP = {cells['ExF from DP–DP (÷67 nm)']}",
              "Spacing (nm, corrected with the DP–PSD ExF)", "Neighbouring DP pairs")
    else:
        empty(ax, "No neighbouring dense projections resolved")
        dress(ax, "DP–DP Spacing", "", "")

    ax = axes[1][0]
    if offsets:
        ax.hist(np.asarray(offsets) * to_nm, bins=np.arange(-120, 161, 10), color=ab_colour or "white", alpha=0.85, edgecolor=eff.SURFACE)
        if offsets2:
            ax.hist(np.asarray(offsets2) * to_nm, bins=np.arange(-120, 161, 10), color=second[1], alpha=0.6, edgecolor=eff.SURFACE)
        ax.axvline(0, color="#FA8072", lw=1.5)
        ax.axvline(syn.REF_NM, color="#BFFF00", lw=1.5)
        clefts = [h["cleft_psd_um"] for h in group if h["cleft_psd_um"] is not None]
        if clefts:
            ax.axvline(float(np.mean(clefts)) * to_nm, color="white", lw=1.2, ls="--")
        dress(ax, f"Antibody Position: {cells['AB to PSD (nm, corrected; + = towards DP)']} nm from the PSD (n = {len(offsets)})\n"
                  "Salmon = PSD peak, lime = DP peak (81.9 nm), dashed = cleft"
                  + (f";  second colour = {second[0]}: {cells.get('AB2 to PSD (nm, corrected; + = towards DP)')} nm (n = {len(offsets2)})" if offsets2 else ""),
              "Across the synapse, from the PSD towards the DPs (nm, corrected)", "Synapses")
    else:
        empty(ax, "No antibody-positive synapses")
        dress(ax, "Antibody Position", "", "")

    ax = axes[1][1]
    if lengths:
        ax.hist(np.asarray(lengths) * to_nm, bins=np.arange(0, 901, 50), color="white", alpha=0.85, edgecolor=eff.SURFACE)
        dress(ax, f"PSD Length: {cells['PSD Length (nm, corrected)']} nm (n = {len(lengths)})\n"
                  f"Thickness {cells['PSD Thickness (nm, corrected, with blur)']} nm, including optical blur",
              "PSD length (nm, corrected)", "Synapses")
    else:
        empty(ax, "No PSD length measured")
        dress(ax, "PSD Length", "", "")

    ax = axes[1][2]
    if depth:
        ax.plot([d[0] for d in depth], [d[1] for d in depth], "o-", color=ab_colour or "white", lw=1.5, ms=4)
        ax.set_ylim(bottom=0)
        dress(ax, f"Antibody Labelling by Depth\nNear faces {cells.get('AB Labelled Near Faces (%)')}%, at centre {cells.get('AB Labelled At Centre (%)')}%",
              "Slice (0 = first plane)", "Synapses found that carry antibody (%)")
    else:
        empty(ax, "Not a stack, or no antibody channel")
        dress(ax, "Antibody Labelling by Depth", "", "")

    fig.suptitle(f"{name}: Structure of {len(group)} Synapses   ·   ExF = {exf} ± {quoted.get('ExF s.d.')} ({quoted.get('ExF Basis')})",
                 color=ink, fontsize=11)
    fig.tight_layout()
    eff.save(fig, png)
    cells["Structure Figure"] = str(png)
    return cells
