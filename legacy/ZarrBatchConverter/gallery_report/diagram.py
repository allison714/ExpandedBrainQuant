"""Pipeline diagram for the setup document."""
import sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
matplotlib.rcParams["font.family"] = "sans-serif"; matplotlib.rcParams["font.sans-serif"] = ["Arial"]
out = Path(sys.argv[1])

fig, ax = plt.subplots(figsize=(11, 8.2)); ax.set_xlim(0, 11); ax.set_ylim(0, 8.2); ax.axis("off")
C = {"data": "#e8f0fa", "tool": "#ffffff", "out": "#f3f5f8", "pres": "#eaf5ee"}
E = {"data": "#0b5cad", "tool": "#5b6470", "out": "#9aa4b2", "pres": "#1a6f3c"}
boxes = {}

def box(key, x, y, w, h, title, sub="", kind="tool"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=C[kind], ec=E[kind], lw=1.2))
    ax.text(x + w / 2, y + h - 0.17, title, ha="center", va="top", fontsize=8.6, fontweight="bold", color="#1b1f24")
    if sub: ax.text(x + w / 2, y + 0.1, sub, ha="center", va="bottom", fontsize=7, color="#5b6470", linespacing=1.25)
    boxes[key] = (x, y, w, h)

def arrow(a, b, side="auto", color="#5b6470", style="-|>"):
    xa, ya, wa, ha = boxes[a]; xb, yb, wb, hb = boxes[b]
    if side == "down": p, q = (xa + wa / 2, ya), (xb + wb / 2, yb + hb)
    elif side == "right": p, q = (xa + wa, ya + ha / 2), (xb, yb + hb / 2)
    elif side == "left": p, q = (xa, ya + ha / 2), (xb + wb, yb + hb / 2)
    elif side == "up": p, q = (xa + wa / 2, ya + ha), (xb + wb / 2, yb)
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=10, color=color, lw=1.0, connectionstyle="arc3,rad=0.0"))

# row 1: acquisition -> conversion -> server
box("mic", 0.3, 7.0, 2.2, 0.95, "Microscope files", ".ims .nd2 .lif .czi .tif\nDragonfly, Nikon, pan-ASLM tiles", "data")
box("conv", 2.9, 7.0, 2.4, 0.95, "Convert_To_Zarr", "convert_to_zarr.py\nbioformats2raw (NGFF-Converter's Java)")
box("zarr", 5.7, 7.0, 2.4, 0.95, "OME-Zarr on the server", "X:\\nginx_share\\exm\\2026\\zarr\n_ng_states, _channel_registration.json", "data")
box("ng", 8.5, 7.0, 2.2, 0.95, "Neuroglancer links", "neuroglancer.kuanlab.yale.edu\nZarr Image List.xlsx (shared, read-only)", "pres")
arrow("mic", "conv", "right"); arrow("conv", "zarr", "right"); arrow("zarr", "ng", "right")

# row 2: QC and views
box("shots", 0.3, 5.5, 2.2, 0.95, "Log_Screenshots / Save_NG_View", "roi_log.py: standard views, ROIs,\nZarr Screenshot Log.xlsx (rating, notes)")
box("chk", 2.9, 5.5, 2.4, 0.95, "Channel checks", "channel_check.py (dark, sparse, bleed screen)\nantibody_channels.py (which AB on which laser)\nchannel_registration.py (camera 2)")
box("exf", 5.7, 5.5, 2.4, 0.95, "Measure_ExF", "synapse_exf.py + synapse_structure.py\nfinds synapses in the pan stain,\nDP–PSD ÷ 81.9 nm, AB-positive, classes")
box("abeff", 8.5, 5.5, 2.2, 0.95, "Measure_AB_Efficiency", "ab_efficiency.py: PCC, Manders,\ncoverage, SBR, penetration, montage seams")
for k in ("shots", "chk", "exf", "abeff"): arrow("zarr", k, "down" if k == "exf" else "auto") if k == "exf" else None
# explicit arrows from zarr to row 2
xz, yz, wz, hz = boxes["zarr"]
for k in ("shots", "chk", "abeff"):
    xb, yb, wb, hb = boxes[k]
    ax.add_patch(FancyArrowPatch((xz + wz / 2, yz), (xb + wb / 2, yb + hb), arrowstyle="-|>", mutation_scale=10, color="#5b6470", lw=1.0, connectionstyle="arc3,rad=0.0"))

# row 3: outputs of ExF
box("log", 5.7, 4.0, 2.4, 0.95, "Synapse Log.xlsx", "Synapses (60 per sample, Accept Y/N)\nSummary (ExF, structure, AB numbers)\nMethod · per sample: CSV, cards, contact sheet", "out")
arrow("exf", "log", "down")
box("logshots", 0.3, 4.0, 2.2, 0.95, "Zarr Screenshots\\", "PNG views per sample\nZarr Screenshot Log.xlsx", "out")
arrow("shots", "logshots", "down")
box("comp", 2.9, 4.0, 2.4, 0.95, "Zarr Comparisons\\", "Channel Check, AB Metrics, SZ vs AC,\nWT vs KO, Double/Single/Multi Label,\nStitching, Antibody Report", "out")
arrow("chk", "comp", "down"); arrow("abeff", "comp", "auto") if False else None
xa, ya, wa, ha = boxes["abeff"]; xc, yc, wc, hc = boxes["comp"]
ax.add_patch(FancyArrowPatch((xa + 0.3, ya), (xc + wc, yc + hc - 0.15), arrowstyle="-|>", mutation_scale=10, color="#5b6470", lw=1.0, connectionstyle="arc3,rad=-0.25"))

# row 4: downstream analyses reading the log / zarr
box("single", 0.3, 2.4, 2.0, 1.0, "single_label.py", "efficiency beyond chance\n(decoy footprints), per antibody")
box("double", 2.5, 2.4, 2.0, 1.0, "double_label.py", "bleed-through (quantile\nregression), co-labelling")
box("morph", 4.7, 2.4, 2.0, 1.0, "compare_morphology.py", "WT vs KO at animal level,\npermutation test")
box("metrics", 6.9, 2.4, 2.0, 1.0, "ab_metrics.py · compare_ab.py", "puncta density, spacing,\nenrichment; set vs set")
box("nuc", 9.1, 2.4, 1.6, 1.0, "nucleus_size.py", "nuclei as a third ruler")
for k in ("single", "double", "morph", "metrics", "nuc"):
    xb, yb, wb, hb = boxes[k]; xl, yl, wl, hl = boxes["log"]
    ax.add_patch(FancyArrowPatch((xl + wl / 2, yl), (xb + wb / 2, yb + hb), arrowstyle="-|>", mutation_scale=9, color="#9aa4b2", lw=0.9, connectionstyle="arc3,rad=0.0"))

# row 5: presentation and pan-ASLM
box("gal", 0.3, 0.6, 3.4, 1.1, "Make_Gallery", "make_gallery.py: 2026 AC Gallery.html (tabs, chips, filters)\n--server one-file copy · --mirror full copy to X:\\...\\gallery\\", "pres")
box("rep", 4.0, 0.6, 3.0, 1.1, "Reports", "antibody_report.py · double_label_report.py\ngallery_report\\ (this series of PDFs)", "pres")
box("aslm", 7.3, 0.6, 3.4, 1.1, "pan-ASLM (precomputed volumes)", "stitch_check.py: doubling, seams\naslm_synapses.py: same finder on sampled fields\naslm_training.py: 8,000 + 2,000 patches")
for k in ("single", "double"):
    xb, yb, wb, hb = boxes[k]; xg, yg, wg, hg = boxes["gal"]
for src, dst in (("logshots", "gal"), ("log", "gal"), ("comp", "rep"), ("log", "rep")):
    xs, ys, ws, hs = boxes[src]; xd, yd, wd, hd = boxes[dst]
    ax.add_patch(FancyArrowPatch((xs + ws / 2, ys), (xd + wd / 2 + (0.6 if src == "log" and dst == "gal" else -0.6 if src == "log" else 0), yd + hd),
                                 arrowstyle="-|>", mutation_scale=9, color="#1a6f3c", lw=0.9, alpha=0.6, connectionstyle="arc3,rad=0.0"))
xz, yz, wz, hz = boxes["zarr"]; xa, ya, wa, ha = boxes["aslm"]
ax.add_patch(FancyArrowPatch((xz + wz, yz + 0.2), (xa + wa - 0.2, ya + ha), arrowstyle="-|>", mutation_scale=9, color="#0b5cad", lw=0.9, alpha=0.5, connectionstyle="arc3,rad=-0.35"))
ax.text(10.65, 6.75, "Stitched Precomputed NG\n(pan-ASLM, NRStitcher)", fontsize=6.8, color="#0b5cad", ha="right", va="top")

# legend
for i, (k, lab) in enumerate((("data", "image data"), ("tool", "tool (Python script, .bat launcher)"), ("out", "results folder"), ("pres", "viewing and reporting"))):
    ax.add_patch(FancyBboxPatch((0.3 + i * 2.7, 0.12), 0.25, 0.22, boxstyle="round,pad=0.01", fc=C[k], ec=E[k], lw=1))
    ax.text(0.62 + i * 2.7, 0.23, lab, fontsize=7.2, va="center", color="#1b1f24")
fig.savefig(out / "fig" / "pipeline.png", dpi=220, facecolor="white", bbox_inches="tight"); plt.close(fig)

# ---- the ExF measurement, as a schematic profile
import numpy as np
fig, axes = plt.subplots(1, 3, figsize=(11, 3.4))
t = np.linspace(-5, 5, 400)
prof = 150 + 95 * np.exp(-((t - 0.1) ** 2) / (2 * 0.45 ** 2)) + 55 * np.exp(-((t + 1.55) ** 2) / (2 * 0.5 ** 2)) + 6 * np.sin(3 * t)
ax = axes[0]; ax.plot(t, prof, color="#1b1f24", lw=1.6); ax.set_ylim(120, 264)
for m, lab, c in ((0.1, "PSD", "#b3261e"), (-1.55, "DP", "#1a6f3c")):
    ax.axvline(m, color=c, ls=":", lw=1); ax.text(m, 252, lab, ha="center", va="bottom", color=c, fontsize=9, fontweight="bold")
ax.annotate("", (0.1, 170), (-1.55, 170), arrowprops=dict(arrowstyle="<->", color="#0b5cad", lw=1.3))
ax.text(-0.72, 126, "peak to peak = d (µm as imaged);  ExF = d ÷ 81.9 nm", ha="center", va="bottom", fontsize=8, color="#0b5cad")
ax.set_title("1. Profile across the bar;\nsecond peak searched at 82 nm × 12–30", fontsize=8.6, loc="left"); ax.set_xlabel("distance across the synapse (µm as imaged)", fontsize=8); ax.set_ylabel("pan counts", fontsize=8)
ax = axes[1]
fit = 150 + 95 * np.exp(-((t - 0.1) ** 2) / (2 * 0.45 ** 2)) + 55 * np.exp(-((t + 1.55) ** 2) / (2 * 0.5 ** 2))
ax.plot(t, prof, color="#9aa4b2", lw=1.2, label="profile"); ax.plot(t, fit, color="#0b5cad", lw=1.8, label="two Gaussians + baseline")
ax.fill_between(t, 150, fit, where=(t > -1.55) & (t < 0.1), color="#e8f0fa")
ax.text(-0.72, 165, "dip ≥ 0.2 of the\nsmaller peak", ha="center", fontsize=7.5, color="#5b6470")
ax.set_title("2. Gates: R² ≥ 0.90, dip ≥ 0.20, peak ratio 0.25–4,\nbrighter peak above 99.5% of the plane", fontsize=8.6, loc="left"); ax.legend(frameon=False, fontsize=7.5, loc="upper right"); ax.set_xlabel("distance (µm)", fontsize=8)
ax = axes[2]
ax.bar(["pan-only\ndouble peaks", "antibody-\npositive", "accepted\nby a person"], [1, 2, 3], color=["#9aa4b2", "#0b5cad", "#1a6f3c"], width=0.55)
ax.set_yticks([]); ax.set_ylabel("trust", fontsize=8)
ax.text(0, 1.1, "reads high\n(membrane pairs)", ha="center", fontsize=7.2, color="#5b6470"); ax.text(1, 2.1, "needs ≥ 10", ha="center", fontsize=7.2, color="#5b6470"); ax.text(2, 3.1, "needs ≥ 5 Y", ha="center", fontsize=7.2, color="#5b6470")
ax.set_ylim(0, 3.8); ax.set_title("3. Which ExF is quoted: the best basis available;\nmean ± s.d. across fields of view", fontsize=8.6, loc="left")
for a in axes:
    for s in ("top", "right"): a.spines[s].set_visible(False)
    a.tick_params(labelsize=7.5)
fig.tight_layout(); fig.savefig(out / "fig" / "exf_schematic.png", dpi=220, facecolor="white"); plt.close(fig)
print("diagram done")
