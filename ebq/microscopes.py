"""Microscope profiles: what differs between instruments, kept out of the analysis code.

The analysis works in physical units (nm of tissue, µm as imaged), so an instrument only
changes the numbers in its profile: voxel size when the file does not say, the axial/lateral
resolution ratio, the camera frame, the tile overlap, and which metadata fields identify it.
Nothing in the algorithms branches on the microscope.

identify() reads the instrument from the data where it can (OME-XML Instrument/Objective/
Detector written by bioformats2raw; a Plexus or pan-ASLM acquisition log beside raw tiles).
When it cannot, the 'microscope' column of the manifest decides, and a GUI or setup script
should offer this list and store the answer there, not ask every run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Microscope:
    name: str
    kind: str                       # 'spinning-disk', 'light-sheet'
    lateral_resolution_um: float    # as imaged, FWHM
    axial_over_lateral: float       # how much worse the axial resolution is (1 = isotropic)
    frame_px: int                   # camera frame side, for tile layout guesses
    tile_overlap: float             # typical overlap fraction of montages
    default_voxel_um: tuple | None = None   # (z, y, x) when the file carries none
    match: tuple = field(default_factory=tuple)   # lower-case substrings found in the metadata
    notes: str = ""


PROFILES = {
    "Nikon_SoRa": Microscope(
        "Nikon_SoRa", "spinning-disk", 0.25, 2.5, 2048, 0.10,
        match=("nikon", "csu-w1", "sora", "ti2"),
        notes="Nikon CSU-W1 SoRa. .nd2 files carry no acquisition date (read it from the name)."),
    "Dragonfly": Microscope(
        "Dragonfly", "spinning-disk", 0.29, 2.5, 2048, 0.10,
        match=("dragonfly", "andor", "fusion"),
        notes="Andor Dragonfly; Fusion .ims. Channel colours in the file are arbitrary; colour by laser."),
    "pan-ASLM": Microscope(
        "pan-ASLM", "light-sheet", 0.50, 1.0, 3200, 0.15, default_voxel_um=(0.200, 0.203, 0.203),
        match=("aslm", "kinetix"),
        notes="Bewersdorf-lab axially swept light sheet; stitched with NRStitcher to precomputed."),
    "Plexus_pan-ASLM": Microscope(
        "Plexus_pan-ASLM", "light-sheet", 0.50, 1.0, 3200, 0.15,
        match=("plexus",),
        notes="Plexus-controlled pan-ASLM; acquisition logs (Zstack_log_*.txt) sit beside the tiles."),
}


def identify(text: str | None = None, path=None) -> str | None:
    """The profile name suggested by metadata text (OME-XML, info, logs) and/or the dataset path."""
    hay = (text or "").lower()
    p = Path(path) if path else None
    if p is not None:
        hay += " " + str(p).lower()
        if p.is_dir():
            if any(p.glob("Zstack_log_*.txt")) or (p / "Notes.txt").is_file():
                hay += " plexus"
            ome = p / "OME" / "METADATA.ome.xml"
            if ome.is_file():
                try:
                    hay += " " + ome.read_text(encoding="utf-8", errors="ignore")[:20000].lower()
                except OSError:
                    pass
    for name, prof in PROFILES.items():
        if any(key in hay for key in prof.match):
            return name
    return None
