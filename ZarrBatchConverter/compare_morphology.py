"""
Compare synapse morphology between two sets of samples, from the pan stain alone.

  compare_morphology.py [A] [B]      for example:  compare_morphology.py WT KO

A and B tell the two sets apart in the sample names, as in compare_ab.py. Nothing is measured
here: it reads the synapses synapse_exf.py already found (<sample>_synapses.csv), so run
Measure_ExF.bat first. The synapse finder uses only the pan stain, with the same gates for
every sample, so the two sets are treated alike whatever antibody each animal carries.

What is compared, per synapse
  DP-PSD distance   as imaged, divided by 81.9 nm: the expansion factor the sample reads.
                    It mixes the gel's expansion with the real distance, so a difference here
                    cannot be put down to either.
  PSD length        the unbroken stretch of the PSD above half its height
  PSD thickness     width of the PSD across the synapse (includes the microscope's blur)
  DP-DP spacing     distance between neighbouring dense projections
  cleft depth       how far the pan signal falls between the DP and the PSD ('Dip')
  perforated, type II   share of synapses in those classes (automatic, provisional calls)

Lengths are given in nm of tissue on the sample's own scale: the length as imaged, divided by
the sample's median DP-PSD distance, times 81.9 nm. Gels expand differently, so lengths as
imaged cannot be compared; this scale is the same rule for every sample. It assumes the
DP-PSD distance itself is the same in both sets: if B had, say, a wider DP-PSD distance,
every length of B would read proportionally short. The shares and the cleft depth do not
depend on any scale.

The unit is the animal, not the synapse: thousands of synapses from one animal are not
thousands of independent observations of a genotype. An animal is the part of the name that
carries the set's text ('WT4', 'KO1b'). Per sample the median over its synapses is taken, per
animal the mean of its samples, and the two sets are compared across animals:
  B / A        ratio of the means across animals, with the range it takes when one animal at
               a time is left out
  p            exact permutation test on the animals (every way of dealing the animals into two
               sets of these sizes). With 5 and 4 animals the smallest possible p is 0.016.
Single tiles ('_F0') are left out, and so are samples imaged at a different pixel size from
the majority (another objective blurs and samples the synapse differently).

Output, in 'Zarr Comparisons\\<A> vs <B>': 'Morphology - Per Sample.csv', 'Morphology - Per
Animal.csv', 'Morphology - Comparison.csv', 'Morphology.png'.
"""

import csv
import itertools
import re
import sys
from pathlib import Path

import numpy as np
from openpyxl import load_workbook

import ab_efficiency as eff
import compare_ab
import convert_to_zarr as core
import roi_log as roi
import synapse_exf as syn

MIN_SYNAPSES = 30        # a sample with fewer synapses than this is left out
METRICS = [   # (key, title, unit)
    ("exf", "DP–PSD Distance ÷ 81.9 nm (ExF)", ""),
    ("psd_len", "PSD Length", "nm"),
    ("psd_thick", "PSD Thickness (with blur)", "nm"),
    ("dp_dp", "DP–DP Spacing", "nm"),
    ("dip", "Cleft Depth (share of the smaller peak)", ""),
    ("perforated", "Perforated", "%"),
    ("type2", "Type II (putative)", "%"),
    ("found", "Synapses Found per Sample", ""),
]


def animal_of(name, text):
    """'KO1b' from '260831_KO1b_Cx43_...': the part of the name that carries the set's text."""
    for token in name.split("_"):
        if re.fullmatch(re.escape(text) + r"\d*[a-z]?", token, re.IGNORECASE):
            return token
    return name.split("_")[0]


def sample_numbers(path):
    """The medians and shares of one sample's synapses, or None when it has too few."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if len(rows) < MIN_SYNAPSES:
        return None
    column = lambda key: np.array([float(r[key]) for r in rows if r.get(key) not in (None, "")])
    distance = column("DP-PSD peak to peak (µm, as imaged)")
    scale = syn.REF_NM / float(np.median(distance))            # nm of tissue per µm as imaged, on the sample's own scale
    gaps = [float(g) for r in rows for g in r["DP-DP Spacings (µm, as imaged)"].split()]
    classes = [r["Class"] for r in rows]
    return {"n": len(rows), "found": float(len(rows)), "exf": float(np.median(column("ExF (peak to peak)"))),
            "psd_len": float(np.median(column("PSD Length (µm, as imaged)"))) * scale,
            "psd_thick": float(np.median(column("PSD Thickness (µm, as imaged)"))) * scale,
            "dp_dp": float(np.median(gaps)) * scale if len(gaps) >= 10 else np.nan,
            "dip": float(np.median(column("Dip"))),
            "perforated": 100 * classes.count("Type I, perforated") / len(classes),
            "type2": 100 * classes.count("Type II (putative)") / len(classes)}


def permutation_p(a, b):
    """Exact two-sided permutation test on the difference of the means."""
    both = np.concatenate([a, b])
    seen = abs(a.mean() - b.mean())
    hits = total = 0
    for pick in itertools.combinations(range(len(both)), len(a)):
        mask = np.zeros(len(both), bool)
        mask[list(pick)] = True
        hits += abs(both[mask].mean() - both[~mask].mean()) >= seen - 1e-12
        total += 1
    return hits / total


def run(a_text, b_text):
    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    synapses = Path(core.DEFAULT_SHOTS).parent / "Zarr Synapse ExF"
    base = Path(core.DEFAULT_SHOTS).parent / "Zarr Comparisons" / f"{a_text} vs {b_text}"
    base.mkdir(parents=True, exist_ok=True)
    rows = load_workbook(synapses / syn.LOG_NAME, read_only=True)["Summary"].iter_rows(values_only=True)
    heads = next(rows)
    pixel = {r[0]: dict(zip(heads, r)).get("Pixel (nm, as imaged)") for r in rows}
    samples = []
    for name, _ in roi.samples(out_root):
        for text in (a_text, b_text):
            path = synapses / name / f"{name}_synapses.csv"
            if compare_ab.belongs(name, text) and path.is_file():
                got = sample_numbers(path)
                if got:
                    samples.append({"Set": text, "Animal": animal_of(name, text), "Sample": name, "pixel": pixel.get(name), **got})
    if not samples:
        sys.exit("No sample with synapses in either set. Run Measure_ExF.bat first.")
    sizes = [round(s["pixel"] / 5) * 5 for s in samples if s["pixel"]]
    usual = max(set(sizes), key=sizes.count)
    for s in [s for s in samples if s["pixel"] and round(s["pixel"] / 5) * 5 != usual]:
        print(f"   left out: {s['Sample']}   (pixel {s['pixel']} nm, the others {usual} nm)")
    samples = [s for s in samples if not s["pixel"] or round(s["pixel"] / 5) * 5 == usual]

    animals = {}
    for s in samples:
        animals.setdefault((s["Set"], s["Animal"]), []).append(s)
    per_animal = [{"Set": set_, "Animal": animal, "Samples": len(group), "Synapses": sum(g["n"] for g in group),
                   **{key: float(np.nanmean([g[key] for g in group])) for key, _, _ in METRICS}}
                  for (set_, animal), group in sorted(animals.items())]
    comparison = []
    for key, title, unit in METRICS:
        a = np.array([r[key] for r in per_animal if r["Set"] == a_text and not np.isnan(r[key])])
        b = np.array([r[key] for r in per_animal if r["Set"] == b_text and not np.isnan(r[key])])
        if len(a) < 2 or len(b) < 2:
            continue
        ratio = b.mean() / a.mean() if a.mean() else np.nan
        left_out = [np.delete(b, i).mean() / a.mean() for i in range(len(b))] + [b.mean() / np.delete(a, i).mean() for i in range(len(a))]
        comparison.append({"Measure": title + (f" ({unit})" if unit else ""), f"{a_text} (mean ± s.d. across animals)": f"{a.mean():.3g} ± {a.std(ddof=1):.2g}",
                           f"{a_text} Animals": len(a), f"{b_text} (mean ± s.d. across animals)": f"{b.mean():.3g} ± {b.std(ddof=1):.2g}", f"{b_text} Animals": len(b),
                           f"{b_text} / {a_text}": round(float(ratio), 3), "Leaving One Animal Out": f"{min(left_out):.2f}–{max(left_out):.2f}",
                           "p (permutation, animals)": round(permutation_p(a, b), 3),
                           "Effect (s.d. of the animals)": round(float((b.mean() - a.mean()) / np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)), 2), "key": key})

    def write(file, table, heads):
        with open(base / file, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, heads, extrasaction="ignore")
            w.writeheader()
            w.writerows(table)

    title_of = {key: title + (f" ({unit})" if unit else "") for key, title, unit in METRICS}
    named = lambda r: {**r, **{title_of[k]: round(r[k], 3) for k in title_of}}
    write("Morphology - Per Sample.csv", [named(s) for s in samples], ["Set", "Animal", "Sample", "n"] + list(title_of.values()))
    write("Morphology - Per Animal.csv", [named(r) for r in per_animal], ["Set", "Animal", "Samples", "Synapses"] + list(title_of.values()))
    write("Morphology - Comparison.csv", comparison, [k for k in comparison[0] if k != "key"])

    shown = [c for c in comparison if c["key"] != "found"]
    fig, axes = eff.figure(2, 4, figsize=(17, 8.2))
    colours = {a_text: "#E8E8E8", b_text: "#FF9900"}
    for ax, c in zip(axes.ravel(), shown):
        for x, text in enumerate((a_text, b_text)):
            ax.scatter([x + 0.18] * sum(s["Set"] == text for s in samples), [s[c["key"]] for s in samples if s["Set"] == text],
                       s=14, color=eff.MUTED, alpha=0.7, lw=0)
            group = [r for r in per_animal if r["Set"] == text and not np.isnan(r[c["key"]])]
            ax.scatter([x - 0.08] * len(group), [r[c["key"]] for r in group], s=60, color=colours[text], zorder=3)
            for r in group:
                ax.annotate(r["Animal"], (x - 0.08, r[c["key"]]), xytext=(-8, 0), textcoords="offset points", ha="right", va="center", color=eff.MUTED, fontsize=7)
            mean = float(np.mean([r[c["key"]] for r in group]))
            ax.plot([x - 0.3, x + 0.3], [mean, mean], color=colours[text], lw=2)
        ax.set_xticks([0, 1])
        ax.set_xticklabels([a_text, b_text], color=eff.INK, fontsize=10)
        ax.set_xlim(-0.75, 1.5)
        ax.set_ylabel(c["Measure"], color=eff.INK, fontsize=9)
        ax.set_title(f"{b_text} / {a_text} = {c[f'{b_text} / {a_text}']:.2f}   (p = {c['p (permutation, animals)']:.2f})", color=eff.INK, fontsize=9, loc="left")
        ax.grid(color="#2A2A2A", lw=0.6, axis="y")
    for ax in axes.ravel()[len(shown):]:
        ax.axis("off")
    axes.ravel()[-1].text(0, 0.95, "Large dot: one animal (mean of its samples)\nSmall grey dot: one sample (median of its synapses)\nLine: mean across animals\n\n"
                          f"Lengths in nm of tissue on each sample's own scale\n(its median DP–PSD distance = {syn.REF_NM:g} nm).\n"
                          "p: exact permutation test across animals,\nnot corrected for the number of measures.",
                          transform=axes.ravel()[-1].transAxes, color=eff.INK, fontsize=9, va="top")
    fig.suptitle(f"Synapse morphology from the pan stain: {b_text} against {a_text}   ·   "
                 f"{sum(r['Set'] == a_text for r in per_animal)} and {sum(r['Set'] == b_text for r in per_animal)} animals, "
                 f"{sum(s['n'] for s in samples)} synapses in {len(samples)} samples", color=eff.INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    eff.save(fig, base / "Morphology.png")

    for r in per_animal:
        print(f"   {r['Set']:3s} {r['Animal']:6s} {r['Samples']} sample(s), {r['Synapses']:5d} synapses")
    print()
    for c in comparison:
        print(f"   {c['Measure']:42s} {a_text} {c[f'{a_text} (mean ± s.d. across animals)']:>14s}   {b_text} {c[f'{b_text} (mean ± s.d. across animals)']:>14s}   "
              f"{b_text}/{a_text} {c[f'{b_text} / {a_text}']:.2f} ({c['Leaving One Animal Out']})   p = {c['p (permutation, animals)']:.3f}")
    print(f"\n{base}")


if __name__ == "__main__":
    args = sys.argv[1:]
    a_text = args[0] if args else roi.ask("First set (for example WT): ")
    b_text = args[1] if len(args) > 1 else roi.ask("Second set (for example KO): ")
    run(a_text, b_text)
