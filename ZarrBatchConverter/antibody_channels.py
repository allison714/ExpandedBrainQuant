"""
Which antibody is on which channel of a sample.

Secondaries move between channels while they are being tested, so the tools cannot tell from
the image whether the 637 channel is NR1 or Homer1. This module answers it, in this order:

  1. The table '_antibody_channels.csv' in the zarr folder: one row per sample (or per start
     of a sample name) and laser. It is the one place to write it down, shared by everyone
     who uses the zarr folder, and it wins over everything else. Leave 'Antibody' empty and
     put TO DO under 'Status' for a channel that still has to be looked up.
  2. The file name, when it gives the dye: 'NR1AF546_Homer1AF647' puts NR1 on 561 and Homer1
     on 637. Naming new files this way needs no table row:
         date_animal_pan488_NR1-AF546_Homer1-AF647_objective_kind
  3. The file name, when it names one antibody and the sample has one antibody channel.

Otherwise the antibody is unknown and the tools go on saying '637 channel'.

  antibody_channels.py        list every sample's channels and what is known about them
"""

import csv
import re
import sys
from pathlib import Path

TABLE_NAME = "_antibody_channels.csv"
COLUMNS = ["Sample (a name, or the start of several names)", "Laser (nm)", "Antibody", "Status", "Notes"]
LASER_TOLERANCE_NM = 15
# dye in a file name -> the laser it is imaged with
DYES = {"af488": 488, "alexa488": 488, "cf488": 488, "af546": 561, "af555": 561, "af568": 561, "cy3": 561, "cf568": 561,
        "af594": 594, "cf594": 594, "af647": 637, "alexa647": 637, "cy5": 637, "cf640": 637, "cf633": 637,
        "af750": 750, "af790": 790, "cy7": 750}
_cache = {}


def table_path():
    import convert_to_zarr as core
    return Path(core.DEFAULT_OUT) / TABLE_NAME


def read_table():
    """[(start of name in lower case, laser, antibody, status)] from the table."""
    path = table_path()
    stamp = path.stat().st_mtime if path.is_file() else None
    if "rows" not in _cache or _cache.get("stamp") != stamp:
        rows = []
        if stamp:
            with open(path, newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    start, laser = (r.get(COLUMNS[0]) or "").strip(), re.sub(r"\D", "", r.get(COLUMNS[1]) or "")
                    if start and laser:
                        rows.append((start.lower(), int(laser), (r.get(COLUMNS[2]) or "").strip(), (r.get(COLUMNS[3]) or "").strip()))
        _cache.update(stamp=stamp, rows=rows)
    return _cache["rows"]


def for_sample(name, channels, antibody_indices):
    """{channel index: (antibody name or '', where it comes from)} for the antibody channels of one sample.

    channels - as from convert_to_zarr.describe_channels (each has 'index' and 'laser')
    """
    import synapse_exf as syn

    out = {}
    lasers = {c: channels[c].get("laser") for c in antibody_indices}
    # 1. the table: the longest matching start of the name wins
    for c, laser in lasers.items():
        rows = [r for r in read_table() if name.lower().startswith(r[0]) and laser and abs(r[1] - laser) <= LASER_TOLERANCE_NM]
        if rows:
            best = max(rows, key=lambda r: len(r[0]))
            out[c] = (best[2].strip("?"), "table" if best[2].strip("?") else "table: to do")
    # 2. the file name gives the dye
    named = []
    for token in re.split(r"[_\s]+", name):
        low = token.lower().replace("-", "")
        for key, (label, _) in syn.ANTIBODIES.items():
            if low.startswith(key):
                named.append(label)
                dye = next((d for d in DYES if d in low[len(key):]), None)
                if dye:
                    for c, laser in lasers.items():
                        if c not in out and laser and abs(DYES[dye] - laser) <= LASER_TOLERANCE_NM:
                            out[c] = (label, "file name")
    # 3. one antibody named, one antibody channel
    named = list(dict.fromkeys(named))
    open_channels = [c for c in antibody_indices if c not in out]
    if len(named) == 1 and len(antibody_indices) == 1 and open_channels:
        out[open_channels[0]] = (named[0], "file name")
    for c in antibody_indices:
        out.setdefault(c, ("", "not known" + (f": one of {', '.join(named)}" if named else "")))
    return out


def ensure_table(todo_rows=()):
    """Create the table if it is not there, and add any rows not yet in it. Rows already there are never changed."""
    path = table_path()
    have = set()
    if path.is_file():
        with open(path, newline="", encoding="utf-8-sig") as f:
            have = {((r.get(COLUMNS[0]) or "").strip().lower(), re.sub(r"\D", "", r.get(COLUMNS[1]) or "")) for r in csv.DictReader(f)}
    new = [r for r in todo_rows if (r[0].lower(), str(r[1])) not in have]
    if path.is_file() and not new:
        return 0
    fresh = not path.is_file()
    with open(path, "a", newline="", encoding="utf-8-sig" if fresh else "utf-8") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(COLUMNS)
        w.writerows(new)
    _cache.clear()
    return len(new)


def main():
    import ab_efficiency as eff
    import convert_to_zarr as core
    import roi_log as roi
    import synapse_exf as syn

    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    todo = []
    for name, zarr_dir in roi.samples(out_root):
        if any(w in name.lower() for w in syn.NEVER):
            continue
        info = core.read_series(zarr_dir / "0")
        channels, pan, antibodies, _ = eff.channel_roles(name, zarr_dir, out_root, info)
        known = for_sample(name, channels, antibodies)
        print(f"{name[:52]:52s} " + "   ".join(f"{channels[c]['label']}: {known[c][0] or '?'} ({known[c][1]})" for c in antibodies))
        if len(antibodies) > 1:
            todo += [(name, channels[c]["laser"], "", "TO DO", known[c][1]) for c in antibodies if not known[c][0] and channels[c].get("laser")]
    if "--todo" in sys.argv:
        print(f"\n{ensure_table(todo)} row(s) added to {table_path()} for channels still to be looked up")


if __name__ == "__main__":
    main()
