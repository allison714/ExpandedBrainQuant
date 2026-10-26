"""New summary charts for the gallery report (light background, Arial, for print)."""
import csv, sys, collections
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
matplotlib.rcParams["font.family"] = "sans-serif"; matplotlib.rcParams["font.sans-serif"] = ["Arial"]
out = Path(sys.argv[1]); fig_dir = out / "fig"
ROOT = Path(r"C:\Users\admin\OneDrive - Yale University\09_Data & Imaging\Images\2026")
INK, MUTED = "#1b1f24", "#5b6470"
f = lambda v: None if v in (None, "", "None") else float(v)

# ---------- 1. ExF per sample, grouped
rows = list(csv.DictReader(open(out / "synapse_summary.csv", encoding="utf-8")))
rows = [r for r in rows if f(r["ExF"]) and f(r["ExF s.d."]) is not None and int(r["Synapses found"]) >= 30 and not r["Sample"].endswith("_F0")]
def group(r):
    s = r["Sample"]
    if s.startswith("AC_"): return "AC (29 Apr, pan only)"
    if s.startswith("260616"): return "16 Jun NR1 + Homer1"
    if s.startswith("260910"): return "10 Sep double label"
    if s.startswith("260831"):
        ab = r["Antibody"]; g = "KO" if "_KO" in s else "WT"
        return f"31 Aug {ab} {g}"
    return "other"
order = ["16 Jun NR1 + Homer1", "10 Sep double label", "31 Aug GluA1 WT", "31 Aug GluA1 KO", "31 Aug GluA2 WT", "31 Aug GluA3 WT", "31 Aug GluA3 KO",
         "31 Aug Cx43 WT", "31 Aug Cx43 KO", "31 Aug mGluR5 KO", "AC (29 Apr, pan only)"]
rows.sort(key=lambda r: (order.index(group(r)) if group(r) in order else 99, r["Sample"]))
fig, ax = plt.subplots(figsize=(10, 0.26 * len(rows) + 1.6))
y = 0; ticks, labels, last = [], [], None
for r in rows:
    g = group(r)
    if g != last:
        y += 0.8; ax.text(11.6, y, g, color=INK, fontsize=8.5, fontweight="bold", va="center"); y += 1; last = g
    ci = r["ExF 95% CI"]
    lo, hi = (f(x) for x in ci.split("–")) if ci and "–" in ci else (None, None)
    trusted = r["ExF Basis"].startswith(("antibody", "accepted"))
    col = "#0b5cad" if trusted else "#9aa4b2"
    if lo is not None: ax.plot([lo, hi], [y, y], color=col, lw=2)
    ax.scatter(f(r["ExF"]), y, s=36, color=col, zorder=3, marker="o" if trusted else "s")
    ticks.append(y); labels.append(r["Sample"].replace("_FusionStitcher", "").replace("260831_", "").replace("260616_", "")[:34]); y += 1
ax.set_yticks(ticks); ax.set_yticklabels(labels, fontsize=7.5, color=INK); ax.invert_yaxis()
ax.axvspan(16, 24, color="#e8f0fa", zorder=0); ax.set_xlim(11.5, 27)
ax.set_xlabel("Expansion factor (DP–PSD peak to peak ÷ 81.9 nm; mean across fields of view, 95% bootstrap interval)", fontsize=9, color=INK)
ax.scatter([], [], color="#0b5cad", label="rests on antibody-positive synapses"); ax.scatter([], [], color="#9aa4b2", marker="s", label="pan-only double peaks (reads high)")
ax.legend(frameon=False, fontsize=8, loc="lower right"); ax.grid(axis="x", color="#e3e6ea"); ax.tick_params(colors=MUTED)
for s in ("top", "right"): ax.spines[s].set_visible(False)
ax.set_title("Expansion factor of every sample with 30 or more synapses (blue band = expected 16–24)", fontsize=10, color=INK, loc="left")
fig.tight_layout(); fig.savefig(fig_dir / "chart_exf.png", dpi=200, facecolor="white"); plt.close(fig)

# ---------- 2. inventory by day
inv = list(csv.DictReader(open(out / "image_list_images.csv", encoding="utf-8")))
inv = [d for d in inv if not any(k in d["Image Name"].lower() for k in ("pff", "tetraspec"))]
days = collections.OrderedDict()
for d in inv:
    day = str(d["Date Acquired"])[:10]; n = int(float(d["C"] or 1))
    pan = "(pan)" in str(d["Channels (laser)"])
    if pan:
        key = "pan + 1 antibody" if n - 1 == 1 else "pan + 2 antibodies"
    else:
        key = {1: "1 antibody", 2: "2 channels", 3: "3 channels"}.get(n, "3 channels")
    days.setdefault(day, collections.Counter())[key] += 1
kinds = ["1 antibody", "2 channels", "3 channels", "pan + 1 antibody", "pan + 2 antibodies"]
cols = {"1 antibody": "#9aa4b2", "2 channels": "#6c7f99", "3 channels": "#3f5a80", "pan + 1 antibody": "#0b5cad", "pan + 2 antibodies": "#1a6f3c"}
months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"]
fig, ax = plt.subplots(figsize=(9, 3.4)); bottom = np.zeros(len(days)); x = np.arange(len(days))
for k in kinds:
    v = np.array([days[d].get(k, 0) for d in days]); ax.bar(x, v, bottom=bottom, color=cols[k], label=k, width=0.7); bottom += v
for i, d in enumerate(days): ax.text(i, bottom[i] + 0.5, str(int(bottom[i])), ha="center", fontsize=8, color=INK)
ax.set_xticks(x); ax.set_xticklabels([f"{d[8:10]} {months[int(d[5:7]) - 1]}" for d in days], fontsize=9, color=INK)
ax.set_ylabel("Images on the server", fontsize=9, color=INK); ax.legend(frameon=False, fontsize=8, ncol=3, loc="upper right")
ax.set_title("Images by acquisition day and channel content (PFF and bead images left out)", fontsize=10, color=INK, loc="left")
for s in ("top", "right"): ax.spines[s].set_visible(False)
ax.grid(axis="y", color="#e3e6ea"); ax.set_axisbelow(True); fig.tight_layout(); fig.savefig(fig_dir / "chart_inventory.png", dpi=200, facecolor="white"); plt.close(fig)

# ---------- 3. efficiency across all antibodies (single + multi), one chart
sl = list(csv.DictReader(open(ROOT / "Zarr Comparisons/Single Label/Single Label - Per Antibody.csv", encoding="utf-8-sig")))
ml = list(csv.DictReader(open(ROOT / "Zarr Comparisons/Multi Label/Multi Label - Per Antibody.csv", encoding="utf-8-sig")))
items = []
for r in sl:
    if r["Imaged"] == "260831":
        items.append((f"{r['Antibody']} {r['Set']}  (31 Aug)", f(r["Efficiency (%)"]), f(r["Efficiency Low"]), f(r["Efficiency High"]), f(r["By Chance (%)"]), int(r["Synapses"]), "#0b5cad" if r["Set"] == "WT" else "#d9731a"))
for r in ml:
    day = {"260616": "16 Jun", "260910": "10 Sep"}.get(r["Imaged"], r["Imaged"])
    items.append((f"{r['Antibody']}  ({day})", f(r["Efficiency (%)"]), f(r["Efficiency Low"]), f(r["Efficiency High"]), f(r["By Chance (%)"]), int(r["Synapses"]), "#1a6f3c"))
items.sort(key=lambda t: -t[1])
fig, ax = plt.subplots(figsize=(9, 0.34 * len(items) + 1.4))
for i, (lab, e, lo, hi, ch, n, c) in enumerate(items):
    ax.plot([lo, hi], [i, i], color=c, lw=2); ax.scatter(e, i, s=46, color=c, zorder=3); ax.scatter(ch, i, marker="|", s=60, color=MUTED)
    ax.text(max(hi, 0) + 1.5, i, f"{max(e, 0):.0f}%  (n = {n})", va="center", fontsize=8, color=INK)
ax.set_yticks(range(len(items))); ax.set_yticklabels([t[0] for t in items], fontsize=8.5, color=INK); ax.invert_yaxis()
ax.axvline(0, color=MUTED, lw=1, ls=":"); ax.set_xlim(-6, 100)
ax.set_xlabel("Share of pan-found synapses carrying the antibody, beyond chance (%; 95% bootstrap over fields; grey tick = by chance)", fontsize=8.5, color=INK)
for c, lab in (("#0b5cad", "WT, single label"), ("#d9731a", "Shank2 KO, single label"), ("#1a6f3c", "double label, each channel on its own")):
    ax.scatter([], [], color=c, label=lab)
ax.legend(frameon=False, fontsize=8, loc="lower right"); ax.grid(axis="x", color="#e3e6ea")
for s in ("top", "right"): ax.spines[s].set_visible(False)
ax.set_title("Antibody labelling efficiency at synapses, every antibody channel with a pan stain", fontsize=10, color=INK, loc="left")
fig.tight_layout(); fig.savefig(fig_dir / "chart_efficiency.png", dpi=200, facecolor="white"); plt.close(fig)

# ---------- 4. pan-ASLM synapses by depth
fl = list(csv.DictReader(open(ROOT / "Zarr Synapse ExF/pan-ASLM/260618_pan_aslm_4x4x9520/260618_pan_aslm_4x4x9520_fields.csv", encoding="utf-8-sig")))
by = collections.OrderedDict()
for r in fl: by.setdefault(f(r["Depth (µm, as imaged)"]), []).append(r)
fig, axes = plt.subplots(1, 2, figsize=(9, 3.2))
d = list(by); axes[0].bar(range(len(d)), [sum(int(x["Synapses"]) for x in by[k]) for k in d], color="#0b5cad", width=0.7)
axes[0].set_xticks(range(len(d))); axes[0].set_xticklabels([f"{k:.0f}" for k in d], fontsize=8); axes[0].set_xlabel("Depth in the stack (µm as imaged)", fontsize=9, color=INK)
axes[0].set_ylabel("Synapse profiles in 16 fields", fontsize=9, color=INK); axes[0].set_title("Synapses found per depth (16 tiles, one 2048-px field each)", fontsize=9.5, color=INK, loc="left")
ex = [[f(x["ExF (mean)"]) for x in by[k] if f(x["ExF (mean)"])] for k in d]
axes[1].boxplot(ex, positions=range(len(d)), widths=0.5, medianprops={"color": "#0b5cad"}); axes[1].axhspan(16, 24, color="#e8f0fa", zorder=0)
axes[1].set_xticks(range(len(d))); axes[1].set_xticklabels([f"{k:.0f}" for k in d], fontsize=8); axes[1].set_xlabel("Depth (µm as imaged)", fontsize=9, color=INK)
axes[1].set_ylabel("ExF per field (all double peaks)", fontsize=9, color=INK); axes[1].set_title("Expansion factor per field by depth", fontsize=9.5, color=INK, loc="left")
for ax in axes:
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax.grid(axis="y", color="#e3e6ea"); ax.set_axisbelow(True)
fig.tight_layout(); fig.savefig(fig_dir / "chart_aslm_depth.png", dpi=200, facecolor="white"); plt.close(fig)
print("charts done")
