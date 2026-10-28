"""Phone-sized JPEG copies of the synapse cards, for the online Synapse Review page.

Reads every '<sample>_synapse-NNN_<id>.png' card under the Zarr Synapse ExF folder and writes
'_review_jpg/<sample>/<same name>.jpg' beside the sample folders (so make_gallery.py --mirror
carries them to the server), then copies the new files to the server mirror right away.

Usage:  python review_thumbs.py [--width 1600] [--quality 80] [--force]
"""
import argparse
import configparser
import shutil
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ini = configparser.ConfigParser()
ini.read(HERE / "settings.ini", encoding="utf-8")
SHOTS = Path(ini.get("paths", "screenshot_folder"))
EXF = SHOTS.parent / "Zarr Synapse ExF"
MIRROR = Path(r"X:\nginx_share\exm\2026\gallery") / "Zarr Synapse ExF"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--quality", type=int, default=80)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-mirror", action="store_true")
    a = ap.parse_args()
    out_root = EXF / "_review_jpg"
    cards = sorted(p for p in EXF.glob("*/*_synapse-*.png") if not p.parent.name.startswith("_"))
    print(f"{len(cards)} cards under {EXF}")
    made = 0
    for i, png in enumerate(cards, 1):
        out = out_root / png.parent.name / (png.stem + ".jpg")
        if out.is_file() and not a.force and out.stat().st_mtime >= png.stat().st_mtime:
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(png) as im:
            im = im.convert("RGB")
            if im.width > a.width:
                im = im.resize((a.width, round(im.height * a.width / im.width)), Image.LANCZOS)
            im.save(out, "JPEG", quality=a.quality, optimize=True, progressive=True)
        made += 1
        if i % 200 == 0:
            print(f"  {i}/{len(cards)}", flush=True)
    print(f"wrote {made} JPEGs to {out_root}")
    if not a.no_mirror and MIRROR.is_dir():
        dst = MIRROR / "_review_jpg"
        n = 0
        for jpg in out_root.rglob("*.jpg"):
            t = dst / jpg.relative_to(out_root)
            if t.is_file() and t.stat().st_size == jpg.stat().st_size:
                continue
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(jpg, t)
            n += 1
        print(f"copied {n} to {dst}")


if __name__ == "__main__":
    sys.exit(main())
