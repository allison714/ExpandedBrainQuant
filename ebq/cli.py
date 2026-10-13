"""ebq command line.

  ebq manifest-template OUT.csv
  ebq manifest-check    MANIFEST.csv
  ebq nuclei     VOLUME --out DIR [--voxel-um 3.2] [--exf 21.7]
  ebq synapses3d VOLUME --out DIR [--fields 20] [--field-px 256] [--seed 0]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

import numpy as np


def _volume(path):
    from .io.precomputed import Volume

    if not (Path(path) / "info").is_file():
        sys.exit(f"{path}: not a precomputed volume (no 'info' file). OME-Zarr stacks are not wired into the CLI yet.")
    return Volume(path)


def cmd_nuclei(a):
    from . import nuclei as nuc

    vol = _volume(a.volume)
    level = vol.level_for(a.voxel_um / 2) if a.binning == 0 else vol.level_for(a.voxel_um / a.binning)
    binning = a.binning or max(1, int(round(a.voxel_um / vol.voxel_um(level)[2])))
    binning = min((b for b in (1, 2, 4) if b >= binning), default=4)
    voxel = vol.voxel_um(level)[2] * binning
    zs, ys, xs = vol.shape(level)
    print(f"{a.volume}\n  level {level} ({vol.voxel_um(level)[2]:.3f} µm voxels) binned x{binning} -> {voxel:.2f} µm; "
          f"{zs // binning} x {ys // binning} x {xs // binning} voxels, ~{zs * ys * xs // binning ** 3 * 4 / 1e9:.1f} GB", flush=True)
    t0 = time.time()
    data = vol.read_level(level, binning, progress=lambda k, n: print(f"  read {k}/{n} chunks, {time.time() - t0:.0f} s", flush=True))
    print(f"  read in {time.time() - t0:.0f} s; stored voxels {(data > 0).mean() * 100:.0f}%", flush=True)
    settings = nuc.NucleusSettings(diameter_um=(a.min_diameter, a.max_diameter))
    found, labels = nuc.find_nuclei_3d(data, voxel, settings)
    extent_um3 = (data > 0).sum() * voxel ** 3
    tissue_mm3 = extent_um3 / 1e9 / (a.exf ** 3) if a.exf else None
    summary = nuc.summarise(found, a.exf, tissue_mm3)
    summary.update({"volume": str(a.volume), "level": level, "binning": binning, "voxel_um": voxel,
                    "extent_um3_as_imaged": float(extent_um3), "settings": settings.__dict__})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "nuclei.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["label", "z", "y", "x", "volume_um3", "diameter_um", "projected_um2", "solidity", "aspect", "depth_voxels"])
        for n in found:
            w.writerow([n["label"], *(round(c, 1) for c in n["centroid"]), round(n["volume_um3"]), round(n["diameter_um"], 1),
                        round(n["projected_um2"]), round(n["solidity"], 3), round(n["aspect"], 2), n["depth_voxels"]])
    (out / "nuclei_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"  {summary['n']} complete nuclei; "
          + (f"diameter {summary['diameter_um_mean']:.0f} ± {summary['diameter_um_sd']:.0f} µm as imaged, "
             f"volume {summary['volume_um3_mean']:,.0f} ± {summary['volume_um3_sd']:,.0f} µm³" if found else "") + f"\n  -> {out}")
    if found and not a.no_figure:
        _nuclei_figure(data, labels, found, voxel, out / "nuclei.png", a.exf)
        print(f"  picture: {out / 'nuclei.png'}")


def _nuclei_figure(data, labels, found, voxel, png, exf):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from skimage import measure

    shown = sorted(found, key=lambda n: -n["volume_um3"])[:24]
    cols = 6
    rows = math.ceil(len(shown) / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(2.6 * cols, 2.8 * rows), squeeze=False)
    for ax in axes.ravel():
        ax.axis("off")
    for ax, n in zip(axes.ravel(), shown):
        z, y, x = (int(round(c)) for c in n["centroid"])
        half = int(n["diameter_um"] / voxel * 0.9)
        y0, x0 = max(0, y - half), max(0, x - half)
        crop = data[z, y0:y + half, x0:x + half]
        lo, hi = np.percentile(crop[crop > 0], [1, 99.5]) if (crop > 0).any() else (0, 1)
        ax.imshow(np.clip((crop - lo) / max(hi - lo, 1e-6), 0, 1), cmap="gray", interpolation="nearest")
        for line in measure.find_contours((labels[z, y0:y + half, x0:x + half] == n["label"]).astype(float), 0.5):
            ax.plot(line[:, 1], line[:, 0], color="#FF9900", lw=1)
        ax.set_title(f"d = {n['diameter_um']:.0f} µm" + (f" ({n['diameter_um'] / exf:.1f} tissue)" if exf else ""), fontsize=8)
    fig.suptitle(f"{len(found)} complete nuclei in the pan stain, largest {len(shown)} shown at their central plane "
                 f"({voxel:.1f} µm voxels, as imaged)", fontsize=10)
    fig.tight_layout()
    fig.savefig(png, dpi=150)
    plt.close(fig)


def cmd_synapses3d(a):
    from . import synapse3d as syn

    vol = _volume(a.volume)
    level = a.level
    um = vol.voxel_um(level)[2]
    zs, ys, xs = vol.shape(level)
    rng = np.random.default_rng(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n = a.field_px
    rows = []
    print(f"{a.volume}: level {level}, {um * 1000:.0f} nm voxels; {a.fields} random {n}³ fields", flush=True)
    for k in range(a.fields):
        z0 = int(rng.integers(int(zs * 0.08), max(int(zs * 0.92) - n, int(zs * 0.08) + 1)))
        y0 = int(rng.integers(int(ys * 0.08), max(int(ys * 0.92) - n, int(ys * 0.08) + 1)))
        x0 = int(rng.integers(int(xs * 0.08), max(int(xs * 0.92) - n, int(xs * 0.08) + 1)))
        t0 = time.time()
        block = vol.block(level, z0, z0 + n, y0, y0 + n, x0, x0 + n)
        t1 = time.time()
        hits = syn.find_synapses_3d(block, um, s=settings_for(a))
        print(f"  field {k + 1}/{a.fields} at z{z0} y{y0} x{x0}: {len(hits)} synapses "
              f"(read {t1 - t0:.0f} s, find {time.time() - t1:.0f} s)", flush=True)
        for h in hits:
            cz, cy, cx = h["centre"]
            rows.append([k, z0, y0, x0, round(cz + z0, 1), round(cy + y0, 1), round(cx + x0, 1),
                         *(round(v, 3) for v in h["normal"]), round(h["distance_um"], 4), round(h["exf"], 2),
                         round(h["exf_fit"], 2), round(h["r2"], 3), round(h["dip"], 3), round(h["ratio"], 3),
                         round(h["bright"], 4), round(h["length_px"] * um, 3)])
    with open(out / "synapses3d.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["field", "field_z0", "field_y0", "field_x0", "z", "y", "x", "nz", "ny", "nx",
                    "dp_psd_um", "exf", "exf_fit", "r2", "dip", "ratio", "bright_rank", "psd_length_um"])
        w.writerows(rows)
    exfs = np.array([r[11] for r in rows])
    per_field = [np.mean([r[11] for r in rows if r[0] == k]) for k in range(a.fields) if sum(r[0] == k for r in rows) >= 3]
    print(f"  {len(rows)} synapses; ExF {exfs.mean():.1f} ± {exfs.std(ddof=1) if len(exfs) > 1 else 0:.1f} per synapse"
          + (f"; {np.mean(per_field):.1f} ± {np.std(per_field, ddof=1) if len(per_field) > 1 else 0:.1f} across {len(per_field)} fields" if per_field else "")
          + f"\n  -> {out / 'synapses3d.csv'}" if rows else "  no synapses found")


def settings_for(a):
    from .synapse3d import DEFAULT, FinderSettings

    return FinderSettings(psd_rank=a.psd_rank) if a.psd_rank is not None else DEFAULT


def cmd_manifest_template(a):
    from .manifest import write_template

    write_template(a.out)
    print(f"wrote {a.out}")


def cmd_manifest_check(a):
    from .manifest import check_manifest, read_manifest

    ds = read_manifest(a.manifest)
    problems = check_manifest(ds)
    for d in ds:
        print(f"{d.name}: {d.kind}, animal {d.animal or '?'}, {d.genotype}{' (inferred)' if d.genotype_inferred else ''}, "
              f"{d.sex or '?'}, pan ch{d.pan}, antibodies {d.antibodies}")
    for p in problems:
        print("PROBLEM:", p)
    sys.exit(1 if problems else 0)


def main(argv=None):
    p = argparse.ArgumentParser(prog="ebq", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("nuclei", help="find nuclei in a precomputed pan-stain volume and report their sizes")
    q.add_argument("volume")
    q.add_argument("--out", required=True)
    q.add_argument("--voxel-um", type=float, default=3.2, help="work at about this voxel size, as imaged (default 3.2)")
    q.add_argument("--binning", type=int, default=0, help="force a binning factor (1, 2, 4); 0 = choose from --voxel-um")
    q.add_argument("--exf", type=float, default=None, help="expansion factor, only to report implied tissue sizes")
    q.add_argument("--min-diameter", type=float, default=80.0, help="µm as imaged")
    q.add_argument("--max-diameter", type=float, default=400.0, help="µm as imaged")
    q.add_argument("--no-figure", action="store_true")
    q.set_defaults(func=cmd_nuclei)

    q = sub.add_parser("synapses3d", help="find synapses in random 3D fields of a precomputed pan-stain volume")
    q.add_argument("volume")
    q.add_argument("--out", required=True)
    q.add_argument("--level", type=int, default=0)
    q.add_argument("--fields", type=int, default=20)
    q.add_argument("--field-px", type=int, default=256)
    q.add_argument("--seed", type=int, default=0)
    q.add_argument("--psd-rank", type=float, default=None)
    q.set_defaults(func=cmd_synapses3d)

    q = sub.add_parser("manifest-template")
    q.add_argument("out")
    q.set_defaults(func=cmd_manifest_template)
    q = sub.add_parser("manifest-check")
    q.add_argument("manifest")
    q.set_defaults(func=cmd_manifest_check)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
