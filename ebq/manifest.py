"""The dataset manifest: one CSV row per imaged volume saying what is in it.

Nothing about a channel's role is inferred from lasers or file names any more. The antibody
panel changes (Shank2, NR1, Homer1, mGluR5 today), secondaries move between lasers, and the
pan stain is not always on 488, so the manifest is the single place it is written down.

Columns (header row; extra columns are kept and ignored):
  dataset     a short name, used in every output
  path        folder of the volume: a precomputed dataset (has an 'info' file) or a .ome.zarr
  animal      animal ID (the unit of replication for statistics)
  genotype    WT / KO / HET. Blank = WT unless the dataset name contains a 'KO' token.
  sex         M / F / blank
  age         e.g. P60
  region      e.g. ACC
  channel_0, channel_1, ...   role of each channel: pan, nuclear, or the antibody's name
                              (Shank2, NR1, Homer1, mGluR5, ...). Blank = not used.
  exf         expansion factor if already known (blank otherwise)
  notes
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

PAN = "pan"
NUCLEAR = "nuclear"


@dataclass
class Dataset:
    name: str
    path: Path
    animal: str = ""
    genotype: str = "WT"
    genotype_inferred: bool = False
    sex: str = ""
    age: str = ""
    region: str = ""
    roles: dict = field(default_factory=dict)      # channel index -> role
    exf: float | None = None
    notes: str = ""

    @property
    def kind(self) -> str:
        return "precomputed" if (self.path / "info").is_file() else "ome-zarr"

    @property
    def pan(self) -> int | None:
        return next((i for i, r in self.roles.items() if r == PAN), None)

    @property
    def nuclear(self) -> int | None:
        return next((i for i, r in self.roles.items() if r == NUCLEAR), None)

    @property
    def antibodies(self) -> dict:
        """{antibody name: channel index}, in channel order."""
        return {r: i for i, r in sorted(self.roles.items()) if r not in (PAN, NUCLEAR)}


def infer_genotype(name: str) -> str:
    return "KO" if re.search(r"(^|[_\s-])KO\d*[a-z]?($|[_\s-])", name, re.IGNORECASE) else "WT"


def read_manifest(path) -> list[Dataset]:
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("dataset"):
                continue
            roles = {}
            for key, value in row.items():
                m = re.fullmatch(r"channel_(\d+)", key)
                if m and value:
                    roles[int(m.group(1))] = value.strip()
            genotype = row.get("genotype", "").upper()
            inferred = not genotype
            out.append(Dataset(
                name=row["dataset"], path=Path(row.get("path", "")), animal=row.get("animal", ""),
                genotype=genotype or infer_genotype(row["dataset"]), genotype_inferred=inferred,
                sex=row.get("sex", "").upper(), age=row.get("age", ""), region=row.get("region", ""),
                roles=roles, exf=float(row["exf"]) if row.get("exf") else None, notes=row.get("notes", "")))
    return out


def check_manifest(datasets: list[Dataset]) -> list[str]:
    """Problems a person should fix before running anything."""
    problems = []
    seen = set()
    for d in datasets:
        if d.name in seen:
            problems.append(f"{d.name}: listed twice")
        seen.add(d.name)
        if not d.path.exists():
            problems.append(f"{d.name}: path does not exist: {d.path}")
        if d.pan is None:
            problems.append(f"{d.name}: no channel marked 'pan'")
        if sum(r == PAN for r in d.roles.values()) > 1:
            problems.append(f"{d.name}: more than one channel marked 'pan'")
        if d.genotype not in ("WT", "KO", "HET"):
            problems.append(f"{d.name}: genotype '{d.genotype}' is not WT, KO or HET")
        if d.genotype_inferred and d.genotype == "KO":
            problems.append(f"{d.name}: genotype KO was inferred from the name; confirm it in the manifest")
        if not d.animal:
            problems.append(f"{d.name}: no animal ID (needed for animal-level statistics)")
    return problems


def write_template(path, datasets: list[dict] | None = None):
    cols = ["dataset", "path", "animal", "genotype", "sex", "age", "region",
            "channel_0", "channel_1", "channel_2", "channel_3", "channel_4", "exf", "notes"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in datasets or []:
            w.writerow({c: row.get(c, "") for c in cols})
