"""
Batch-convert every microscopy image in a folder (and its subfolders) to OME-Zarr,
then write a formatted Excel image list with Neuroglancer links.

Conversion is done by Glencoe's bioformats2raw -- the same engine the NGFF-Converter
GUI runs -- using the copy (and the Java runtime) bundled with the NGFF-Converter
install. Any format Bio-Formats can read works: IMS, ND2, LIF, CZI, TIFF, OME-TIFF...

For each converted image the script:
  - reads the OME metadata bioformats2raw saved next to the pixels (channel names,
    wavelengths, acquisition date, objective, voxel size, dimensions)
  - picks a display colour per channel from its laser line
  - samples the smallest pyramid level to get a brightness/contrast range
  - builds a Neuroglancer link (only possible when the output sits under nginx_share)

Re-running is safe: finished zarrs are skipped, and anything typed into the
hand-filled Excel columns is carried over to the new sheet.
"""

import argparse
import getpass
import configparser
import datetime as dt
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# --------------------------------------------------------------------------- settings

# File types to convert. Add an extension here to pick up another format.
EXTENSIONS = {
    ".ims", ".nd2", ".lif", ".czi", ".lsm", ".oib", ".oif", ".oir", ".vsi", ".dv",
    ".tif", ".tiff", ".btf",
}

# Laser line (nm, upper bound of each band) -> display colour (R, G, B) and label.
# The first band whose upper bound is above the laser wavelength wins.
# These follow publication practice rather than the laser's own colour: no red with
# green (indistinguishable to colour-blind readers), and no pure blue (too dark to
# see). The pan stain, and any image with a single channel, is shown in white.
LASER_COLORS = [
    (470, (0, 255, 255), "cyan"),       # 405 / 445
    (530, (0, 255, 0), "green"),        # 488 / 514
    (600, (255, 255, 0), "yellow"),     # 561 / 594
    (700, (255, 0, 255), "magenta"),    # 637 / 640 / 647
    (9999, (255, 0, 0), "red"),         # 730+
]

# Used when a channel name has no laser number in it but does name a dye.
DYE_LASERS = {
    "dapi": 405, "hoechst": 405, "bfp": 405,
    "gfp": 488, "fitc": 488, "af488": 488, "alexa488": 488, "yfp": 514,
    "rfp": 561, "dsred": 561, "tdtomato": 561, "cy3": 561, "mcherry": 561, "tritc": 561, "af568": 561, "af594": 594,
    "cy5": 640, "af647": 640, "alexa647": 640, "cy7": 750,
}

# Formats that are one self-contained file, so they can be copied to the local disk
# before converting. Others (TIFF sets, .vsi, .oif) may depend on neighbouring files
# and are read where they are.
SINGLE_FILE_FORMATS = {".ims", ".nd2", ".lif", ".czi", ".lsm", ".oib", ".oir", ".dv"}

# Colours for channels where no wavelength can be worked out at all.
FALLBACK_PALETTE = [(255, 0, 255), (0, 255, 0), (0, 255, 255), (255, 255, 0)]

# Microscope guessed from the file type; detail found in the metadata is appended.
MICROSCOPE_BY_EXT = {
    ".ims": "Andor Dragonfly", ".nd2": "Nikon", ".lif": "Leica", ".czi": "Zeiss",
    ".lsm": "Zeiss", ".oib": "Olympus", ".oif": "Olympus", ".oir": "Olympus", ".vsi": "Olympus",
}

# Where finished zarrs go (one flat folder, '<image name>.zarr'), and where the
# Excel image list is kept. These are only the starting values: First_Time_Setup.bat
# saves each person's own choices in settings.ini beside this script, and those win.
DEFAULT_OUT = r"X:\nginx_share\exm\2026\zarr"
# The list is generated metadata and sits on the server beside the data. The notes
# workbook is personal and stays in the user's own folder.
DEFAULT_LIST = r"X:\nginx_share\exm\2026\Zarr Image List.xlsx"
_onedrive = Path.home() / "OneDrive - Yale University" / "MS Excel"
DEFAULT_NOTES = str((_onedrive if _onedrive.is_dir() else Path.home() / "Documents") / "Zarr Image List - Notes.xlsx")
# Stitched volumes in Neuroglancer "precomputed" format get their own tab in both
# workbooks. They are only listed, never converted. "none" = no such tab.
DEFAULT_STITCHED = r"X:\nginx_share\exm\2026\Stitched Precomputed NG"
# Where the screenshot/ROI log (roi_log.py) keeps one subfolder per sample and its workbook.
_images = Path.home() / "OneDrive - Yale University" / "09_Data & Imaging" / "Images"
DEFAULT_SHOTS = str((_images / "2026" if _images.is_dir() else Path.home() / "Documents") / "Zarr Screenshots")
DEFAULT_WORK = ""       # blank = the Windows temp folder
CONVERTER_HOME = ""     # blank = look in the usual install locations
SETTINGS_FILE = Path(__file__).with_name("settings.ini")
CONVERTER_DOWNLOAD = "https://github.com/glencoesoftware/NGFF-Converter/releases/latest"

# Local folder name that the lab web server publishes, and the URL it maps to.
SERVER_FOLDER = "nginx_share"
SERVER_URL = "https://neuroglancer.kuanlab.yale.edu/data/"
NG_VIEWER = "https://neuroglancer-demo.appspot.com/"

B2R_MAIN = "com.glencoesoftware.bioformats2raw.Converter"
STATE_DIR = "_ng_states"
LOG_NAME = "_conversion_log.txt"
FAILED_NAME = "_failed.json"  # failures are remembered here until the file converts

# --------------------------------------------------------------------------- settings file


def load_settings():
    """Apply settings.ini (written by First_Time_Setup.bat) over the defaults above."""
    global DEFAULT_OUT, DEFAULT_LIST, DEFAULT_NOTES, DEFAULT_STITCHED, DEFAULT_SHOTS, DEFAULT_WORK, CONVERTER_HOME, SERVER_FOLDER, SERVER_URL, NG_VIEWER
    if not SETTINGS_FILE.is_file():
        return False
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(SETTINGS_FILE, encoding="utf-8-sig")  # Notepad may save with a byte-order mark
    get = lambda section, key, default: cfg.get(section, key, fallback="").strip() or default
    DEFAULT_OUT = get("paths", "zarr_folder", DEFAULT_OUT)
    DEFAULT_LIST = get("paths", "image_list", DEFAULT_LIST)
    DEFAULT_NOTES = get("paths", "notes_file", DEFAULT_NOTES)
    DEFAULT_STITCHED = get("paths", "stitched_folder", DEFAULT_STITCHED)
    DEFAULT_SHOTS = get("paths", "screenshot_folder", DEFAULT_SHOTS)
    DEFAULT_WORK = get("paths", "work_folder", DEFAULT_WORK)
    CONVERTER_HOME = get("paths", "ngff_converter", CONVERTER_HOME)
    SERVER_FOLDER = get("server", "served_folder", SERVER_FOLDER).lower()
    SERVER_URL = get("server", "served_url", SERVER_URL).rstrip("/") + "/"
    NG_VIEWER = get("server", "neuroglancer", NG_VIEWER).rstrip("/") + "/"
    return True


def save_settings():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg["paths"] = {
        "zarr_folder": DEFAULT_OUT, "image_list": DEFAULT_LIST, "notes_file": DEFAULT_NOTES,
        "stitched_folder": DEFAULT_STITCHED,
        "screenshot_folder": DEFAULT_SHOTS,
        "work_folder": DEFAULT_WORK, "ngff_converter": CONVERTER_HOME,
    }
    cfg["server"] = {"served_folder": SERVER_FOLDER, "served_url": SERVER_URL, "neuroglancer": NG_VIEWER}
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        f.write("; Settings for the Zarr Batch Converter. Run First_Time_Setup.bat to change them,\n"
                "; or edit this file in Notepad.\n"
                ";   zarr_folder     REQUIRED  where converted images go; must be inside the served folder\n"
                ";   image_list      REQUIRED  read-only metadata workbook, rewritten on every run\n"
                ";   notes_file      OPTIONAL  your own notes workbook; 'none' = do not keep one\n"
                ";   screenshot_folder OPTIONAL folder for the screenshot/ROI log: one subfolder per sample plus its workbook\n"
                ";   stitched_folder OPTIONAL  folder of stitched precomputed volumes to list; 'none' = no Stitched tab\n"
                ";   work_folder     OPTIONAL  local scratch folder; blank = the Windows temp folder\n"
                ";   ngff_converter  OPTIONAL  NGFF-Converter install folder; blank = find it automatically\n"
                ";   [server] values are the same for the whole lab; change only if the web server changes\n\n")
        cfg.write(f)


def ask(default):
    """Read one answer; Enter accepts the default."""
    answer = input(f"  Enter = {default}\n  > ").strip("\ufeff \t").strip('"').strip()
    print()
    return answer or default


def explain(number, title, need, *lines):
    print("-" * 70)
    print(f"{number}  {title}   [{need}]")
    for line in lines:
        print(f"   {line}")
    print()


def run_setup():
    """Interactive first-time setup: check the prerequisites and save the paths."""
    global DEFAULT_OUT, DEFAULT_LIST, DEFAULT_NOTES, DEFAULT_STITCHED, DEFAULT_SHOTS, DEFAULT_WORK, CONVERTER_HOME
    existing = load_settings()
    print("This sets four locations. For each one, press Enter to accept the value shown,")
    print("or paste a different path. REQUIRED ones must point somewhere real for the")
    print("converter to work; OPTIONAL ones can be left as they are or switched off.\n")
    print(f"Python         : OK ({sys.version.split()[0]})")

    java, app = find_converter()
    while not java:
        print("NGFF-Converter : NOT FOUND   [REQUIRED]")
        print("  The program that does the actual conversion. Nothing can be converted")
        print("  without it. Download the Windows .msi and install with default options:")
        print(f"  {CONVERTER_DOWNLOAD}")
        answer = input("  Press Enter after installing it, paste its folder if you installed it\n"
                       "  somewhere unusual, or type S to skip for now.\n  > ").strip().strip('"')
        print()
        if answer.lower() == "s":
            break
        if answer:
            CONVERTER_HOME = answer
        java, app = find_converter()
    if java:
        jar = next(Path(app).glob("bioformats2raw-*.jar")).stem
        works = subprocess.run([java, "-version"], capture_output=True).returncode == 0
        print(f"NGFF-Converter : {'OK' if works else 'found, but its Java would not start'} ({Path(app).parent}, {jar})\n")

    explain("1 of 6", "ZARR FOLDER", "REQUIRED",
            "Where converted images are written, one '<image name>.zarr' folder each.",
            f"It must be inside '{SERVER_FOLDER}' on the lab server: that is the only place",
            "the web server publishes, so it is the only place Neuroglancer links work.",
            "Everyone in the lab should use the same folder.")
    while True:
        DEFAULT_OUT = ask(DEFAULT_OUT)
        out = Path(DEFAULT_OUT)
        if not out.is_dir():
            print(f"  ! That folder cannot be reached right now. Is the {out.drive or 'network'} drive connected?")
            if input("  Use it anyway? (y/n) > ").strip().lower() != "y":
                print()
                continue
            print()
        if server_url_for(out) is None:
            print(f"  ! It is not inside a '{SERVER_FOLDER}' folder. Images will still convert and be")
            print("    listed, but they will have no Neuroglancer links.\n")
        else:
            print(f"  OK. Images there are served from {server_url_for(out)}/\n")
        break

    def workbook(current, filename):
        path = ask(current)
        if not path.lower().endswith(".xlsx"):
            path = str(Path(path) / filename)  # a folder was given
        if not Path(path).parent.is_dir():
            try:
                Path(path).parent.mkdir(parents=True)
                print(f"  Created {Path(path).parent}\n")
            except OSError as e:
                print(f"  ! That folder cannot be reached or created right now ({e}).\n")
        return path

    explain("2 of 6", "IMAGE LIST", "REQUIRED - the default is fine",
            "A read-only Excel workbook listing every image in the zarr folder with its",
            "metadata and Neuroglancer link. The converter rewrites it on every run and",
            "nobody types in it. It is always produced; you only choose where it goes.",
            "Keep it on the server, beside the zarr folder, so the whole lab shares one list.")
    DEFAULT_LIST = workbook(DEFAULT_LIST, "Zarr Image List.xlsx")

    explain("3 of 6", "NOTES WORKBOOK", "OPTIONAL - type NONE to go without",
            "Your own Excel workbook for what the microscope cannot know: ms ID, ROI,",
            "antibody labels, pan stain, expansion factor, date analyzed, notes.",
            "The converter adds a blank row for each new image and never reads or changes",
            "what you type. Keep it in a folder of your own, not on the shared server,",
            "unless you want to share one notes workbook with other people.")
    shown = "" if DEFAULT_NOTES.lower() == "none" else DEFAULT_NOTES
    answer = input(f"  Enter = {shown or 'NONE (no notes workbook)'}\n  > ").strip("\ufeff \t").strip('"').strip()
    print()
    if answer.lower() == "none" or (not answer and not shown):
        DEFAULT_NOTES = "none"
        print("  No notes workbook will be kept. Run this setup again to add one.\n")
    else:
        path = answer or shown
        if not path.lower().endswith(".xlsx"):
            path = str(Path(path) / "Zarr Image List - Notes.xlsx")
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"  ! That folder cannot be reached or created right now ({e}).\n")
        DEFAULT_NOTES = path

    explain("4 of 6", "STITCHED FOLDER", "OPTIONAL - type NONE to go without",
            "A folder of stitched volumes already in Neuroglancer 'precomputed' format.",
            "Nothing in it is converted or changed. Its datasets are listed on a separate",
            "'Stitched' tab of both workbooks, with the same columns and their own links.",
            f"Like the zarr folder, it must be inside '{SERVER_FOLDER}' for the links to work.")
    shown = "" if DEFAULT_STITCHED.lower() == "none" else DEFAULT_STITCHED
    answer = input(f"  Enter = {shown or 'NONE (no Stitched tab)'}\n  > ").strip("\ufeff \t").strip('"').strip()
    print()
    DEFAULT_STITCHED = "none" if answer.lower() == "none" or (not answer and not shown) else (answer or shown)
    if DEFAULT_STITCHED != "none" and not Path(DEFAULT_STITCHED).is_dir():
        print("  ! That folder cannot be reached right now; the tab is skipped until it can be.\n")

    explain("5 of 6", "SCREENSHOT FOLDER", "OPTIONAL - the default is fine",
            "Used by Log_Screenshots.bat and Save_NG_View.bat. Holds one subfolder per sample",
            "for its screenshots, and 'Zarr Screenshot Log.xlsx' listing every entry with its",
            "slice, rating and notes. Keep it in a folder of your own.")
    DEFAULT_SHOTS = ask(DEFAULT_SHOTS)

    explain("6 of 6", "SCRATCH FOLDER", "OPTIONAL - the default is fine",
            "A temporary work area on this PC. Images that live on the network are copied",
            "here, converted, uploaded as a finished zarr, then deleted from here. That is",
            "much faster than converting directly on the server.",
            "Change it only if this drive is short of space: it needs about 2x the size of",
            "your largest image. It must be a plain local folder, not OneDrive or network.")
    while True:
        work = ask(DEFAULT_WORK or tempfile.gettempdir())
        if "onedrive" in work.lower() or is_network(work):
            print("  ! That is a OneDrive or network folder. Pick a plain local folder.\n")
            continue
        try:
            Path(work).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"  ! Cannot use that folder ({e}).\n")
            continue
        print(f"  OK. {shutil.disk_usage(work).free / 1e9:.0f} GB free there.\n")
        DEFAULT_WORK = "" if os.path.normcase(work) == os.path.normcase(tempfile.gettempdir()) else work
        break

    save_settings()
    print("=" * 70)
    print(f"{'Updated' if existing else 'Saved'} {SETTINGS_FILE}\n")
    print(f"  Zarr folder    : {DEFAULT_OUT}")
    print(f"  Image list     : {DEFAULT_LIST}")
    print(f"  Notes workbook : {'none' if DEFAULT_NOTES == 'none' else DEFAULT_NOTES}")
    print(f"  Stitched folder: {DEFAULT_STITCHED}")
    print(f"  Screenshots    : {DEFAULT_SHOTS}")
    print(f"  Scratch folder : {DEFAULT_WORK or tempfile.gettempdir()}\n")
    if java:
        print("Setup is complete. Double-click Convert_To_Zarr.bat (or drag a folder onto it) to convert.")
    else:
        print("Paths are saved, but conversion will not work until NGFF-Converter is installed.")
    return 0 if java else 1


# How the 3D panel of a stack is turned, in degrees: tipped back about the x axis, then
# swung about the z axis, so the block is seen from above one corner.
VOLUME_TILT = (-55, 25)

# --------------------------------------------------------------------------- converter


def find_converter():
    """Return (java_exe, app_dir) for the NGFF-Converter install, or (None, None)."""
    roots = [
        CONVERTER_HOME,
        os.environ.get("NGFF_CONVERTER_HOME"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "NGFF-Converter"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "NGFF-Converter"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "NGFF-Converter"),
    ]
    for root in roots:
        if not root:
            continue
        java = Path(root) / "runtime" / "bin" / "java.exe"
        app = Path(root) / "app"
        if java.is_file() and any(app.glob("bioformats2raw-*.jar")):
            return str(java), str(app)
    return None, None


def is_network(path):
    """True for UNC paths and mapped network drives."""
    p = str(Path(path).resolve())
    if p.startswith("\\\\"):
        return True
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(os.path.splitdrive(p)[0] + "\\") == 4  # DRIVE_REMOTE
    except Exception:
        return False


def run_conversion(java, app, src, dst, workers, memo_dir, log, stage=None):
    """Convert one file. Returns (ok, message).

    With `stage` (a local folder) the work happens on the local disk: the source is
    copied down, converted there, and the finished zarr is uploaded in one pass.
    A zarr is tens of thousands of small files, which is very slow to write one at
    a time over a network share.
    """
    final_tmp = dst.with_name(dst.name + ".partial")
    if final_tmp.exists():
        shutil.rmtree(final_tmp, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)

    read_from, write_to = src, final_tmp
    copy_source = False
    if stage:
        # the zarr is normally no bigger than the source; allow 1.2x plus headroom
        size = src.stat().st_size
        free = shutil.disk_usage(stage.parent).free
        copy_source = src.suffix.lower() in SINGLE_FILE_FORMATS and is_network(src)
        if copy_source and free < size * 2.2 + 2e9:
            copy_source = False
            print(f"      only {free / 1e9:.0f} GB free locally: reading the source over the network, writing the zarr locally")
        if free < size * 1.2 + 2e9:
            print(f"      only {free / 1e9:.0f} GB free locally, too little for a {size / 1e9:.0f} GB file; "
                  "converting directly on the network")
            stage = None
    if stage:
        shutil.rmtree(stage, ignore_errors=True)
        (stage / "src").mkdir(parents=True)
        write_to = stage / "out.ome.zarr"
        if copy_source:
            t0 = time.time()
            print("      copying to local disk...", end="", flush=True)
            read_from = stage / "src" / src.name
            shutil.copyfile(src, read_from)
            print(f" {time.time() - t0:,.0f} s")

    cmd = [
        java,
        f"-Djna.library.path={app}",  # blosc.dll lives beside the jars
        "-cp", os.path.join(app, "*"),
        B2R_MAIN,
        str(read_from), str(write_to),
        "--max-workers", str(workers),
        "--memo-directory", memo_dir,  # keep .bfmemo cache files out of the data folder
    ]
    if src.suffix.lower() == ".ims":
        # Bio-Formats lists each Imaris resolution level as another image; only the
        # first is real data, the rest would be redundant downsampled copies
        cmd += ["--series", "0"]
    log.write(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S}  {src}\n{subprocess.list2cmdline(cmd)}\n")
    log.flush()
    try:
        t0 = time.time()
        print("      converting...", end="", flush=True)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        print(f" {time.time() - t0:,.0f} s")
        log.write(proc.stdout or "")
        log.write(f"exit code {proc.returncode}\n")
        log.flush()
        if proc.returncode != 0 or not (write_to / ".zattrs").is_file():
            shutil.rmtree(write_to, ignore_errors=True)
            return False, last_error_line(proc.stdout) or f"converter exit code {proc.returncode}"
        if stage:
            t0 = time.time()
            print("      uploading zarr...", end="", flush=True)
            up = subprocess.run(
                ["robocopy", str(write_to), str(final_tmp), "/E", "/MT:16", "/R:2", "/W:5",
                 "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            print(f" {time.time() - t0:,.0f} s")
            if up.returncode >= 8:  # robocopy: 0-7 are success codes
                log.write(f"robocopy failed ({up.returncode}):\n{up.stdout}\n")
                shutil.rmtree(final_tmp, ignore_errors=True)
                return False, f"upload to the output folder failed (robocopy code {up.returncode})"
    finally:
        if stage:
            shutil.rmtree(stage, ignore_errors=True)
    if dst.exists():
        shutil.rmtree(dst)
    final_tmp.rename(dst)
    return True, ""


def last_error_line(text):
    """Pick the most informative line out of a Java stack trace."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    causes = [ln for ln in lines if ln.startswith("Caused by:")]
    pick = causes[-1] if causes else next((ln for ln in lines if "Exception" in ln or "Error" in ln), "")
    pick = re.sub(r"^Caused by:\s*", "", pick)
    pick = re.sub(r"[A-Za-z]:\\[^:*?\"<>|]*\\", "", pick)  # folders add nothing; keep the file name
    return pick[:200]


# --------------------------------------------------------------------------- scanning


# Raw stitching tiles: 'name_F0.ims', 'name_F12.ims'. The stitched result is what gets
# viewed, so tiles are left out unless --include-tiles is given.
TILE_NAME = re.compile(r"_F\d+$")
# Folders that hold pictures of images, not images: the acquisition software's snapshots.
SKIP_FOLDERS = {"snapshots", "screenshots"}


def is_tile(path):
    return bool(TILE_NAME.search(image_stem(path)))


def find_images(root, skip_dirs, include_tiles=False):
    """All convertible files under root, skipping zarr trees and the output folder."""
    skip = {os.path.normcase(str(p)) for p in skip_dirs}
    found = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(
            d for d in dirs
            if not d.lower().endswith((".zarr", ".zarr.partial"))
            and not d.startswith(".")
            and d.lower() not in SKIP_FOLDERS
            and os.path.normcase(os.path.join(cur, d)) not in skip
        )
        for f in sorted(files):
            if f.startswith((".", "~$")):
                continue
            if Path(f).suffix.lower() in EXTENSIONS and (include_tiles or not is_tile(Path(f))):
                found.append(Path(cur) / f)
    return found


def image_stem(path):
    """File name without its extension(s): 'a.ome.tiff' -> 'a'."""
    name = path.name
    for ext in (".ome.tiff", ".ome.tif", ".ome.btf"):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return path.stem


# When one image exists in several formats under the same name, the first of these is
# converted and the rest are skipped: the instrument's own file carries the most metadata.
FORMAT_PREFERENCE = [".ims", ".nd2", ".czi", ".lif", ".lsm", ".oir", ".oib", ".oif", ".vsi", ".dv",
                     ".btf", ".tif", ".tiff"]


def plan_outputs(files, out_root):
    """Map source files to '<out>/<image name>.zarr' (one flat folder).

    A zarr is identified by the image's name, so one name means one zarr: of several files
    with the same name (X.ims beside X.ome.tiff, or copies in two folders) only one is
    converted. Returns (plan, [(skipped file, the file kept instead)]).
    """
    def rank(f):
        ext = f.suffix.lower()
        return FORMAT_PREFERENCE.index(ext) if ext in FORMAT_PREFERENCE else len(FORMAT_PREFERENCE)

    chosen, order = {}, []
    for f in files:
        key = image_stem(f).lower()
        if key not in chosen:
            chosen[key] = f
            order.append(key)
        elif rank(f) < rank(chosen[key]):
            chosen[key] = f
    kept = set(chosen.values())
    duplicates = [(f, chosen[image_stem(f).lower()]) for f in files if f not in kept]
    return [(chosen[k], out_root / f"{image_stem(chosen[k])}.zarr") for k in order], duplicates


# --------------------------------------------------------------------------- metadata


def strip_ns(tag):
    return tag.rsplit("}", 1)[-1]


def read_ome_xml(zarr_dir):
    """Parse OME/METADATA.ome.xml -> (images, objective, microscope, original_metadata)."""
    path = zarr_dir / "OME" / "METADATA.ome.xml"
    if not path.is_file():
        return [], "", "", {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return [], "", "", {}
    images, objective, microscope, original = [], "", "", {}
    for el in root.iter():
        tag = strip_ns(el.tag)
        if tag == "Image":
            img = {"name": el.get("Name", ""), "date": "", "channels": []}
            for sub in el.iter():
                st = strip_ns(sub.tag)
                if st == "AcquisitionDate":
                    img["date"] = (sub.text or "").strip()
                elif st == "Channel":
                    img["channels"].append(dict(sub.attrib))
            images.append(img)
        elif tag == "Objective" and not objective:
            mag = el.get("NominalMagnification") or el.get("CalibratedMagnification")
            na = el.get("LensNA")
            objective = el.get("Model") or ""
            if not objective and mag:
                objective = f"{float(mag):g}x" + (f" / NA {na}" if na else "")
        elif tag == "Microscope" and not microscope:
            microscope = " ".join(x for x in (el.get("Manufacturer"), el.get("Model")) if x)
        elif tag == "OriginalMetadata":
            key = val = None
            for sub in el:
                if strip_ns(sub.tag) == "Key":
                    key = sub.text
                elif strip_ns(sub.tag) == "Value":
                    val = sub.text
            if key and val is not None:
                original.setdefault(key.strip(), val.strip())
    return images, objective, microscope, original


DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
    "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y:%m:%d %H:%M:%S",
    "%Y-%m-%d", "%m/%d/%Y",
)


def parse_date(text):
    if not text:
        return None
    s = re.sub(r"\s+", " ", text.strip()).rstrip("Z")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)  # trim sub-microsecond digits
    s = re.sub(r"[+-]\d\d:?\d\d$", "", s)  # drop a timezone offset
    for fmt in DATE_FORMATS:
        try:
            d = dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if 1990 <= d.year <= dt.date.today().year + 1:
            return d
    return None


DATE_KEYS = re.compile(r"(?i)(recordingdate|dateandtime|acquisition.?(date|time|start)|creat\w*.?date|^date$|textinfoitem_9$)")


def find_acquisition_date(ome_date, original, names, mtime_of):
    """Return (date, where_it_came_from)."""
    d = parse_date(ome_date)
    if d:
        return d, "image metadata"
    for key in ("RecordingDate", "DateAndTime"):
        d = parse_date(original.get(key))
        if d:
            return d, f"image metadata ({key})"
    for key, val in original.items():
        if DATE_KEYS.search(key):
            d = parse_date(val)
            if d:
                return d, f"image metadata ({key})"
    # lab naming convention: YYMMDD or YYYYMMDD at the start of the file or its folder
    for name in names:
        m = re.match(r"(\d{8}|\d{6})(?!\d)", name)
        if m:
            fmt = "%Y%m%d" if len(m.group(1)) == 8 else "%y%m%d"
            try:
                d = dt.datetime.strptime(m.group(1), fmt)
            except ValueError:
                continue
            if 2000 <= d.year <= dt.date.today().year + 1:
                return d, f"date prefix of '{name}'"
    try:
        return dt.datetime.fromtimestamp(mtime_of.stat().st_mtime), f"modified time of {mtime_of.name} (no date in metadata)"
    except OSError:
        return None, ""


def describe_microscope(ext, ome_microscope, original):
    base = MICROSCOPE_BY_EXT.get(ext, "")
    # the stand's name (first one recorded), then the imaging mode and the camera
    stand = ome_microscope or original.get("m_sMicroscopePhysFullName") or original.get("Microscope") or ""
    detail = [stand] if stand else []
    for key in ("MicroscopeMode",):
        val = original.get(key)
        if val:
            # 'SpinningDiskConfocal' -> 'Spinning Disk Confocal'
            detail.append(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", val))
    if not detail:
        return base
    if not base or stand.lower().startswith(base.split()[0].lower()):
        return ", ".join(detail)  # 'Nikon Ji' already names the maker
    return f"{base} ({', '.join(detail)})"


def describe_objective(ome_objective, original):
    if ome_objective:
        return ome_objective
    mag, na = original.get("LensPower"), original.get("NumericalAperture")
    if mag:
        return f"{mag}x" + (f" / NA {na}" if na else "")
    return ""


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def channel_laser(attrs):
    """Best guess at the excitation laser (nm) for an OME <Channel>. Returns (nm, source)."""
    name = attrs.get("Name", "") or ""
    for m in re.finditer(r"(?<!\d)(\d{3})(?!\d)", name):
        nm = int(m.group(1))
        if 350 <= nm <= 800:
            return nm, "name"
    ex = to_float(attrs.get("ExcitationWavelength"))
    if ex and 300 <= ex <= 900:
        return int(round(ex)), "excitation"
    squashed = re.sub(r"[^a-z0-9]", "", name.lower())
    for dye, nm in DYE_LASERS.items():
        if dye in squashed:
            return nm, "dye"
    em = to_float(attrs.get("EmissionWavelength"))
    if em and 350 <= em <= 950:
        # no laser recorded: a typical Stokes shift puts excitation ~30 nm below emission
        return int(round(em - 30)), "emission"
    return None, ""


def laser_color(nm):
    for upper, rgb, label in LASER_COLORS:
        if nm < upper:
            return rgb, label
    return LASER_COLORS[-1][1], LASER_COLORS[-1][2]


# Colours handed to the antibody channels of an image, longest laser first. Magenta and
# green are the standard colour-blind-safe pair, so a two-antibody image is always those.
ANTIBODY_COLORS = [("magenta", (255, 0, 255)), ("green", (0, 255, 0)), ("cyan", (0, 255, 255)),
                   ("yellow", (255, 255, 0)), ("red", (255, 0, 0)), ("orange", (255, 153, 0))]
NUCLEAR_BELOW_NM = 430   # a 405 channel is a nuclear counterstain (DAPI/Hoechst): always cyan


def assign_colours(channels):
    """Give each channel its display colour, in place.

    The pan stain and a lone channel are white (handled where the view is built). A
    nuclear counterstain is cyan. The remaining channels take ANTIBODY_COLORS in order of
    laser, longest first; channels whose laser is unknown follow in file order.
    """
    taken = set()
    for ch in channels:
        if not ch.get("pan") and ch.get("laser") and ch["laser"] < NUCLEAR_BELOW_NM:
            ch["rgb"], ch["color_name"] = (0, 255, 255), "cyan"
            taken.add("cyan")
    rest = [c for c in channels if not c.get("pan") and not (c.get("laser") and c["laser"] < NUCLEAR_BELOW_NM)]
    rest.sort(key=lambda c: (c.get("laser") is None, -(c.get("laser") or 0), c["index"]))
    free = [c for c in ANTIBODY_COLORS if c[0] not in taken]
    for ch, (name, rgb) in zip(rest, free + free):
        ch["rgb"], ch["color_name"] = rgb, name


def ome_color(attrs):
    """OME stores colour as a signed 32-bit RGBA integer."""
    try:
        u = int(attrs["Color"]) & 0xFFFFFFFF
    except (KeyError, ValueError):
        return None
    rgb = ((u >> 24) & 0xFF, (u >> 16) & 0xFF, (u >> 8) & 0xFF)
    return None if rgb in ((255, 255, 255), (0, 0, 0)) else rgb


def channel_label(name, laser):
    """'SIM 637_Cy5_CF40_Sona01' -> '637 Cy5'."""
    tokens = [t for t in re.split(r"[_\s]+", name or "") if t]
    if laser and str(laser) in tokens:
        rest = tokens[tokens.index(str(laser)) + 1:]
        return f"{laser} {rest[0]}" if rest else str(laser)
    if laser and name:
        return f"{laser} {name}" if str(laser) not in name else name
    return name or (str(laser) if laser else "")


def describe_channels(ome_channels, omero, n_channels):
    """One dict per channel: label, laser, rgb, colour label."""
    out = []
    for i in range(n_channels):
        attrs = dict(ome_channels[i]) if i < len(ome_channels) else {}
        if not attrs.get("Name") and omero and i < len(omero):
            attrs["Name"] = omero[i].get("label", "")
        laser, how = channel_laser(attrs)
        if laser:
            rgb, color_name = laser_color(laser)
        else:
            rgb = ome_color(attrs) or FALLBACK_PALETTE[i % len(FALLBACK_PALETTE)]
            color_name = "file colour" if ome_color(attrs) else "default"
        label = channel_label(attrs.get("Name", ""), laser) or f"ch{i}"
        if how in ("dye", "emission"):
            label = "~" + label  # laser not recorded in the file; inferred
        out.append({
            "index": i,
            "label": label,
            "laser": laser, "laser_source": how, "rgb": rgb, "color_name": color_name,
        })
    return out


UNIT_TO_M = {
    "angstrom": 1e-10, "nanometer": 1e-9, "micrometer": 1e-6, "millimeter": 1e-3,
    "centimeter": 1e-2, "meter": 1.0,
}


def read_series(series_dir):
    """Read the OME-NGFF multiscale group of one series."""
    attrs = json.loads((series_dir / ".zattrs").read_text(encoding="utf-8"))
    ms = attrs["multiscales"][0]
    axes = [a["name"] for a in ms["axes"]]
    units = {a["name"]: a.get("unit") for a in ms["axes"]}
    levels = [d["path"] for d in ms["datasets"]]
    scale = next(
        (t["scale"] for t in ms["datasets"][0].get("coordinateTransformations", []) if t.get("type") == "scale"),
        [1.0] * len(axes),
    )
    shape = json.loads((series_dir / levels[0] / ".zarray").read_text(encoding="utf-8"))["shape"]
    return {
        "axes": axes, "units": units, "levels": levels,
        "scale": dict(zip(axes, scale)), "shape": dict(zip(axes, shape)),
        "omero": (attrs.get("omero") or {}).get("channels") or [],
    }


def sample_ranges(series_dir, info):
    """Sample the smallest pyramid level. Returns (ranges, centre):
    ranges - (low, high) display range per channel
    centre - {"x", "y", "z"} full-resolution voxel to open the view on: the middle of
             the part of the volume that actually holds signal. Stitched mosaics are
             padded with zeros, sometimes with whole empty planes, and a view that
             opens on the padding looks like nothing loaded.
    """
    import zarr

    arr = zarr.open(str(series_dir / info["levels"][-1]), mode="r")
    axes, full = info["axes"], info["shape"]
    n_c = arr.shape[axes.index("c")] if "c" in axes else 1
    n_z = arr.shape[axes.index("z")] if "z" in axes else 1
    z_step = max(1, n_z // 48)
    ranges, stats, filled = [], [], None
    for c in range(n_c):
        idx = []
        for name in axes:
            if name == "c":
                idx.append(c)
            elif name == "t":
                idx.append(0)
            elif name == "z":
                idx.append(slice(None, None, z_step))
            else:
                idx.append(slice(None))
        data = np.asarray(arr[tuple(idx)])
        if "z" not in axes:
            data = data[None]
        if data.size == 0:
            ranges.append((0, 1))
            stats.append({"p1": 0, "p50": 0, "p999": 1, "fill": 0})
            continue
        nonzero = data > 0
        filled = nonzero if filled is None else (filled | nonzero)
        # exact zeros are padding, not signal: a camera never reads 0
        values = data[nonzero] if 0 < nonzero.sum() < data.size * 0.995 else data
        lo, mid, hi = np.percentile(values, [1, 50, 99.9])  # 99.9 keeps sparse bright puncta
        lo = float(lo)
        hi = float(hi)
        # 'fill': the share of pixels carrying signal (well above the darkest level)
        stats.append({"p1": lo, "p50": float(mid), "p999": hi,
                      "fill": float((values > lo + 0.15 * max(hi - lo, 1)).mean())})
        if np.issubdtype(data.dtype, np.integer):
            lo, hi = int(lo), int(max(hi, lo + 1))
        elif hi <= lo:
            hi = lo + 1.0
        ranges.append((lo, hi))

    # Neuroglancer places OME-Zarr voxel k at coordinates k-0.5 .. k+0.5, so a whole
    # number is the middle of a plane and k+0.5 is the boundary with the next one.
    centre = {n: full[n] / 2 for n in "xy" if n in full}
    centre["z"] = (full.get("z", 1) - 1) // 2
    if filled is not None and filled.any():
        zs = np.flatnonzero(filled.any(axis=(1, 2)))
        z_mid = zs[len(zs) // 2] * z_step  # a sampled plane that holds data
        centre["z"] = int(min(z_mid, full.get("z", 1) - 1))
        plane = filled[zs[len(zs) // 2]]
        ys, xs = np.flatnonzero(plane.any(axis=1)), np.flatnonzero(plane.any(axis=0))
        # middle of the filled area, scaled from this pyramid level to full resolution
        centre["y"] = (ys[0] + ys[-1] + 1) / 2 * full["y"] / plane.shape[0]
        centre["x"] = (xs[0] + xs[-1] + 1) / 2 * full["x"] / plane.shape[1]
    return ranges, centre, stats


# Pan-stain detection thresholds (camera counts). Noise-only channels sit around 5-15
# counts of median lift; a pan stain in these datasets is 25 to several hundred.
PAN_MIN_LIFT = 20      # the typical pixel must be this far above the channel's darkest level
PAN_MIN_RATIO = 2.0    # ...and this many times further than in any other channel
PAN_MIN_FILL = 0.25    # ...with signal in at least this share of pixels (rules out puncta, beads)


def pick_pan(image_name, channels, stats):
    """Index of the pan-stain channel, or None. Returns (index, how it was decided).

    A pan stain labels all protein, so it is the one channel where the *typical* pixel
    is bright, not just scattered spots. Antibody channels are mostly background with
    sparse puncta; empty channels are camera noise.
      1. If the file name says so ('pan488'), use the channel with that laser.
      2. Otherwise compare each channel's median lift (median minus darkest level).
    """
    m = re.search(r"pan[\s_-]?(\d{3})(?!\d)", image_name, flags=re.IGNORECASE)
    if m:
        named = [c["index"] for c in channels if c.get("laser") and abs(c["laser"] - int(m.group(1))) <= 15]
        if len(named) == 1:
            return named[0], f"file name says pan{m.group(1)}"
    if len(channels) < 2 or len(stats) != len(channels):
        return None, ""
    lift = [st["p50"] - st["p1"] for st in stats]
    best = int(np.argmax(lift))
    others = max(v for i, v in enumerate(lift) if i != best)
    if (lift[best] >= PAN_MIN_LIFT and lift[best] >= PAN_MIN_RATIO * max(others, 1)
            and stats[best]["fill"] >= PAN_MIN_FILL):
        return best, f"dense signal: typical pixel {lift[best]:.0f} counts above background, next channel {others:.0f}"
    return None, ""


# The colours offered in each layer's colour dropdown: (name, the square shown in the
# dropdown, the colour drawn). The squares are Unicode coloured-square characters, the
# only way to show a colour rather than a word in a Neuroglancer control. There is no
# cyan square, so the blue square stands for cyan (pure blue is too dark to see anyway).
STANDARD_COLORS = [
    ("white", "\u2b1c", (1.0, 1.0, 1.0)),
    ("magenta", "\U0001F7EA", (1.0, 0.0, 1.0)),
    ("green", "\U0001F7E9", (0.0, 1.0, 0.0)),
    ("cyan", "\U0001F7E6", (0.0, 1.0, 1.0)),
    ("yellow", "\U0001F7E8", (1.0, 1.0, 0.0)),
    ("red", "\U0001F7E5", (1.0, 0.0, 0.0)),
    ("orange", "\U0001F7E7", (1.0, 0.6, 0.0)),
]


def standard_color(rgb):
    """The standard colour closest to an (R, G, B) 0-255 colour, as (name, square, rgb)."""
    return min(STANDARD_COLORS, key=lambda c: sum((v / 255 - w) ** 2 for v, w in zip(rgb, c[2])))


def layer_shader(rgb, white=False, default_range=None):
    """Layer shader: a colour dropdown, 'invert', 'defaults', and brightness.

    The dropdown lists coloured squares; picking one sets the layer's colour.

    Neuroglancer has no way to reset a brightness slider. Ticking 'defaults' makes the
    layer ignore the slider and the dropdown and draw with the brightness and colour it
    opened with, which are written into the shader; unticking hands control back.
    """
    start = STANDARD_COLORS[0] if white else standard_color(rgb)
    quote = lambda text: json.dumps(text, ensure_ascii=False)
    lines = ["#uicontrol string_t colour select(options=[{}], default={})".format(
                 ",".join(quote(square) for _, square, _ in STANDARD_COLORS), quote(start[1])),
             "#uicontrol bool invert checkbox(default=false)"]
    if default_range is not None:
        lines.append("#uicontrol bool defaults checkbox(default=false)")
    lines.append("#uicontrol invlerp normalized")
    pick = "".join("if(colour=={}){{c=vec3({:.2f},{:.2f},{:.2f});}}".format(quote(square), *value)
                   for _, square, value in STANDARD_COLORS)
    body = "void main(){float v=normalized();vec3 c=vec3(1.0);" + pick + "bool inv=invert;"
    if default_range is not None:
        lo, hi = (float(x) for x in default_range)
        # repr() keeps the decimal point: GLSL will not mix a float with an integer literal
        body += f"if(defaults){{v=clamp((float(toRaw(getDataValue()))-{lo!r})/{max(hi - lo, 1e-6)!r},0.0,1.0);"
        body += "c=vec3({:.2f},{:.2f},{:.2f});inv=false;}}".format(*start[2])
    body += "if(inv)v=1.0-v;emitRGB(c*v);}"
    return "\n".join(lines) + "\n" + body


# --------------------------------------------------------------------------- neuroglancer


def server_url_for(path):
    """HTTP URL for a local path under the web-served folder, else None."""
    parts = Path(path).parts
    lowered = [p.lower() for p in parts]
    if SERVER_FOLDER not in lowered:
        return None
    rel = parts[lowered.index(SERVER_FOLDER) + 1:]
    return SERVER_URL + "/".join(urllib.parse.quote(p) for p in rel)


def build_state(source_url, info, channels, ranges, centre=None, stats=None):
    axes, shape, scale, units = info["axes"], info["shape"], info["scale"], info["units"]
    dims, pos = {}, []
    for name in axes:
        if name == "c":
            continue  # channel becomes a per-layer local dimension
        if name == "t":
            dims["t"] = [scale.get("t", 1), "s"] if units.get("t") == "second" else [1, ""]
            pos.append(0)
        else:
            dims[name] = [scale.get(name, 1.0) * UNIT_TO_M.get(units.get(name), 1e-6), "m"]
            middle = (shape[name] - 1) // 2 if name == "z" else shape[name] / 2
            pos.append((centre or {}).get(name, middle))
    layers = []
    has_pan = any(c.get("pan") for c in channels)
    for ch in channels:
        st = stats[ch["index"]] if stats and ch["index"] < len(stats) else None
        # An antibody channel is mostly background with scattered spots. Next to a pan
        # stain, or when it is sparse on its own, it is shown so the spots stand out.
        spots = not ch.get("pan") and st is not None and (has_pan or st["fill"] < PAN_MIN_FILL)
        layer = {
            "type": "image",
            "source": f"zarr://{source_url}/",
            "localDimensions": {"c'": [1, ""]},
            "localPosition": [ch["index"]],
            "blend": "additive",
            # the pan stain is dimmed so it gives context without burying the antibodies
            "opacity": 0.5 if ch.get("pan") and len(channels) > 1 else 1,
            # the pan-stain channel starts white (its 'white' box ticked); the laser colour
            # is still there underneath when the box is cleared
            "shader": layer_shader(ch["rgb"], white=bool(ch.get("pan")) or len(channels) == 1),
            "tab": "rendering",
            "name": ch["label"],
        }
        if spots and shape.get("z", 1) > 4:
            # maximum-intensity projection of this channel in the 3D panel
            layer["volumeRendering"] = "max"
        if ch["index"] < len(ranges):
            lo, hi = ranges[ch["index"]]
            if spots:
                # start the brightness above the background haze: the median of a
                # sparse channel is background, not signal
                raised = st["p50"] + 0.15 * (st["p999"] - st["p50"])
                lo = int(round(raised)) if isinstance(hi, int) else raised
                lo = min(lo, hi - 1)
            top = hi + (hi - lo) * 0.5
            # a channel on its own is shown in white: grey shows more detail than any colour
            layer["shader"] = layer_shader(ch["rgb"], bool(ch.get("pan")) or len(channels) == 1, (lo, hi))
            layer["shaderControls"] = {"normalized": {
                "range": [lo, hi],
                "window": [0, int(round(top)) if isinstance(hi, int) else top],
            }}
        layers.append(layer)
    # a channel recorded on a second camera is moved into register (see channel_registration.py)
    import channel_registration
    channel_registration.apply_to_state({"layers": layers, "dimensions": dims})
    widest = max(shape.get("x", 1), shape.get("y", 1))
    view = {
        "dimensions": dims,
        "displayDimensions": ["x", "y", "z"],  # without this the panels come up as t/z/y
        "position": pos,
        # zoom so the whole image fits: one large panel for thin images, a quarter-size
        # panel in the four-panel layout
        "crossSectionScale": round(max(1.0, widest / (800 if shape.get("z", 1) <= 4 else 420)), 2),
        "projectionScale": widest * 1.5,
        "layers": layers,
        # open the side panel on the first channel so its colour and brightness
        # controls are visible straight away
        "selectedLayer": {"visible": True, "layer": layers[0]["name"]} if layers else {},
        # side views of a slab only a few planes thick show nothing useful
        "layout": "4panel" if shape.get("z", 1) > 4 else "xy",
        "selection": {"row": 2, "visible": False},
        # a side palette with the colour, brightness and opacity controls of all channels
        # together ('+' starts another group of controls to include)
        "toolPalettes": {"Shader controls": {"row": 1, "query": "type:shaderControl +type:opacity"}},
    }
    if shape.get("z", 1) > 4:
        # 3D panel: the whole block, tilted, with the projected channels and no slice planes
        # cutting through it. Neuroglancer measures the 3D zoom in units of the finest voxel.
        finest = min(dims[n][0] for n in "xyz" if n in dims)
        longest = max(shape[n] * dims[n][0] / finest for n in "xyz" if n in dims)
        tip, swing = (math.radians(a) / 2 for a in VOLUME_TILT)
        # quaternion (x, y, z, w): rotation about x, followed by rotation about z
        view["projectionOrientation"] = [
            math.sin(tip) * math.cos(swing), -math.sin(tip) * math.sin(swing),
            math.cos(tip) * math.sin(swing), math.cos(tip) * math.cos(swing)]
        view["projectionScale"] = longest * 1.6
        view["showSlices"] = False
    return view


def state_url(state_path):
    """Viewer link for a saved state file. The '?v=' part changes whenever the file's
    content does; without it a browser can keep showing its cached copy of an older
    view after the view has been improved or re-saved."""
    version = hashlib.md5(state_path.read_bytes()).hexdigest()[:8]
    return f"{NG_VIEWER}#!{server_url_for(state_path)}?v={version}"


def state_to_link(state, state_path):
    """Save the viewer state beside the data and return a link that loads it.

    The link points at the saved file rather than carrying the state inside the URL.
    That keeps it short (Excel rejects links over ~2079 characters) and means a link
    already pasted somewhere picks up later improvements to the state.
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    pretty = json.dumps(state, indent=1)
    if not state_path.is_file() or state_path.read_text(encoding="utf-8") != pretty:
        state_path.write_text(pretty, encoding="utf-8")
    return state_url(state_path)


# --------------------------------------------------------------------------- excel

GROUPS = {  # name -> (header colour, tint for hand-filled cells)
    "Image": ("1F3864", "DDE6F3"),
    "Sample & Labels": ("2E7D32", "E4F1E0"),
    "Acquisition": ("6A1B9A", "EFE5F5"),
    "Analysis": ("C55A11", "FCE8DA"),
    "Files": ("595959", "EDEDED"),
    "Neuroglancer": ("00796B", "DAF1EE"),
}

# (header, group, width, filled by hand?)
ALL_COLUMNS = [
    ("Image Name", "Image", 42, False),
    ("ms ID", "Sample & Labels", 11, True),
    ("ROI", "Sample & Labels", 14, True),
    ("AB Label 1", "Sample & Labels", 16, True),
    ("AB Label 2", "Sample & Labels", 16, True),
    ("AB Label 3", "Sample & Labels", 16, True),
    ("Pan", "Sample & Labels", 12, True),
    ("ExF", "Sample & Labels", 8, True),
    ("Date Acquired", "Acquisition", 14, False),
    ("Microscope", "Acquisition", 30, False),
    ("Objective", "Acquisition", 20, False),
    ("Channels (laser)", "Acquisition", 30, False),
    ("Size X × Y × Z", "Acquisition", 20, False),
    ("C", "Acquisition", 5, False),
    ("T", "Acquisition", 5, False),
    ("Voxel X, Y, Z (µm)", "Acquisition", 22, False),
    ("Voxels", "Acquisition", 16, False),
    ("Extent X × Y × Z (µm)", "Acquisition", 26, False),
    ("Volume (µm³)", "Acquisition", 16, False),
    ("AB Spots / 1000 µm²", "Analysis", 13, False),
    ("AB Density by Channel", "Analysis", 46, False),
    ("Date Analyzed", "Analysis", 14, True),
    ("Notes", "Analysis", 28, True),
    ("Status", "Files", 18, False),
    ("Link Status", "Files", 36, False),
    ("Source File", "Files", 44, False),
    ("Zarr Folder", "Files", 44, False),
]
MANUAL = [c[0] for c in ALL_COLUMNS if c[3]]
# The image list holds generated columns only; hand-filled ones live in the notes workbook.
COLUMNS = [c for c in ALL_COLUMNS if not c[3]]
HEADERS = [c[0] for c in COLUMNS]
KEY_COLUMN = "Zarr Folder"
TITLE_ROWS = 2  # title + subtitle above the group band
THIN = Side(style="thin", color="D0D0D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def read_columns(xlsx, sheet, wanted):
    """{Zarr Folder key: {column: value}} for the wanted columns of a workbook we wrote."""
    if not xlsx.is_file():
        return {}
    try:
        ws = load_workbook(xlsx)[sheet]
    except Exception:
        return {}
    header_row = next((r for r in range(1, 12) if "Image Name" in [c.value for c in ws[r]]), None)
    if not header_row:
        return {}
    cols = {ws.cell(header_row, c).value: c for c in range(1, ws.max_column + 1)}
    if KEY_COLUMN not in cols:
        return {}
    out = {}
    for r in range(header_row + 1, ws.max_row + 1):
        key = ws.cell(r, cols[KEY_COLUMN]).value
        found = {h: ws.cell(r, cols[h]).value for h in wanted if h in cols and ws.cell(r, cols[h]).value is not None}
        if key and found:
            out[key] = found
    return out


# The notes workbook: identifying columns, then everything typed by hand.
# Generated columns the notes workbook also carries. Unlike what is typed, these are
# refreshed on every run: the image's size, and whether its link currently works.
NOTES_GENERATED = ["Voxels", "Extent X × Y × Z (µm)", "Volume (µm³)", "Link Status"]
# A small copy of the sample's tilted-volume screenshot, shown in its row of the notes workbook.
THUMB_HEADER = "Volume Image"
THUMB_PX = 110   # height of the thumbnail on screen
NOTES_COLUMNS = [("Image Name", "Image", 44), ("Date Acquired", "Acquisition", 14)] + \
                [(h, g, w) for h, g, w, manual in ALL_COLUMNS if manual] + \
                [(h, g, w) for h, g, w, manual in ALL_COLUMNS if h in NOTES_GENERATED] + \
                [(THUMB_HEADER, "Image", 22), (KEY_COLUMN, "Files", 44)]


def safe_save(wb, path):
    """Save a workbook without ever leaving a half-written file behind.

    It is written beside the real file first and only then swapped in. If anything goes
    wrong while writing, the existing workbook - which may hold typed notes - is untouched.
    Raises PermissionError when Excel has the real file open.
    """
    path = Path(path)
    draft = path.with_name(f"~{path.stem}.saving{path.suffix}")
    try:
        wb.save(draft)
        os.replace(draft, path)
    finally:
        if draft.exists():
            try:
                draft.unlink()
            except OSError:
                pass


def add_thumbnails(ws, rows, known, col):
    """Put a thumbnail of each sample's tilted-volume screenshot in its row. Returns how many
    were added. Rows that already show one are left alone, so nothing piles up on re-runs."""
    if not col:
        return 0
    try:
        from openpyxl.drawing.image import Image as SheetImage
        from PIL import Image as Picture
    except ImportError:
        return 0
    shots = Path(DEFAULT_SHOTS)
    have = set()
    for image in getattr(ws, "_images", []):
        marker = getattr(image.anchor, "_from", None)
        if marker is not None and marker.col == col - 1:
            have.add(marker.row + 1)
    added = 0
    for row in rows:
        r = known.get(row["cells"].get(KEY_COLUMN))
        if not r or r in have:
            continue
        stem = Path(row["cells"][KEY_COLUMN]).parts[0]
        stem = stem[: -len(".zarr")] if stem.lower().endswith(".zarr") else stem
        stem = stem[: -len(".ome")] if stem.endswith(".ome") else stem
        source = shots / stem / f"{stem}_Volume_zall.png"
        if not source.is_file():
            continue   # flat images have no volume picture; stacks get one from Log_Screenshots.bat
        try:
            small = Picture.open(source).convert("RGB")
            small.thumbnail((THUMB_PX * 2, THUMB_PX))
            # openpyxl reads the picture back from its file object when saving, so hand it
            # a PNG held in memory rather than a bare image
            held = io.BytesIO()
            small.save(held, format="PNG")
            held.seek(0)
            picture = SheetImage(Picture.open(held))
        except Exception:
            continue
        ws.add_image(picture, f"{get_column_letter(col)}{r}")
        ws.row_dimensions[r].height = THUMB_PX * 0.75 + 4   # points: tall enough to show it
        ws.column_dimensions[get_column_letter(col)].width = max(ws.column_dimensions[get_column_letter(col)].width or 0, 22)
        added += 1
    return added


def style_generated(cell, header):
    """Number formats and the red/green of a Link Status cell, shared by both workbooks."""
    if header in ("Voxels", "Volume (µm³)"):
        cell.number_format = "#,##0"
    elif header == "AB Spots / 1000 µm²":
        cell.number_format = "0.0"
    elif header == "Link Status" and cell.value:
        ok = cell.value == "OK"
        cell.font = Font(name="Calibri", size=10, bold=not ok, color="006100" if ok else "9C0006")
        cell.fill = PatternFill("solid", fgColor="E2EFDA" if ok else "FFC7CE")
NOTES_HEADER_ROW = 3


def update_notes(path, rows, sheet="Notes", title="Zarr Image Notes"):
    """Add a row to a sheet of the notes workbook for each image it does not list yet.

    This is the user's file: what they typed is never changed, and the file is only
    saved when there is something to add. Returns (rows added, problem).
    """
    headers = [c[0] for c in NOTES_COLUMNS]
    wb = None
    if path.is_file():
        try:
            wb = load_workbook(path)
        except PermissionError:
            return 0, "is open in Excel, so new images were not added to it; close it and run again"
        except Exception as e:
            return 0, f"could not be read ({e})"
    if wb is not None and sheet in wb.sheetnames:
        ws = wb[sheet]
        cols = {ws.cell(NOTES_HEADER_ROW, c).value: c for c in range(1, ws.max_column + 1)}
        if KEY_COLUMN not in cols:
            return 0, f"has no '{KEY_COLUMN}' column in row {NOTES_HEADER_ROW} of the {sheet} tab; leave that header row in place"
        # a workbook made before these columns existed gets them added at the right-hand end
        for header, group, width in NOTES_COLUMNS:
            if header in NOTES_GENERATED + [THUMB_HEADER] and header not in cols:
                c = ws.max_column + 1
                cell = ws.cell(NOTES_HEADER_ROW, c, header)
                cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor=GROUPS[group][0])
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                ws.column_dimensions[get_column_letter(c)].width = width
                cols[header] = c
    else:
        if wb is None:
            wb = Workbook()
            ws = wb.active
            ws.title = sheet
        else:
            ws = wb.create_sheet(sheet)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
        ws.cell(1, 1, title).font = Font(name="Calibri", size=18, bold=True, color="1F3864")
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
        ws.cell(2, 1, "Type in the tinted columns. The converter adds a row for each new image and never "
                      "changes what is already here.").font = Font(name="Calibri", size=10, italic=True, color="7F7F7F")
        ws.row_dimensions[1].height = 30
        for c, (header, group, width) in enumerate(NOTES_COLUMNS, start=1):
            cell = ws.cell(NOTES_HEADER_ROW, c, header)
            cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=GROUPS[group][0])
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.column_dimensions[get_column_letter(c)].width = width
        ws.row_dimensions[NOTES_HEADER_ROW].height = 30
        ws.freeze_panes = ws.cell(NOTES_HEADER_ROW + 1, 2)
        ws.sheet_view.showGridLines = False
        cols = {h: i for i, h in enumerate(headers, start=1)}

    key_col = cols[KEY_COLUMN]
    known = {ws.cell(r, key_col).value: r for r in range(NOTES_HEADER_ROW + 1, ws.max_row + 1)}
    last = NOTES_HEADER_ROW
    for r in range(ws.max_row, NOTES_HEADER_ROW, -1):
        if any(ws.cell(r, c).value is not None for c in range(1, ws.max_column + 1)):
            last = r
            break
    manual = set(MANUAL)
    added = relinked = 0
    link_col = cols.get("Image Name")
    for row in rows:
        key = row["cells"].get(KEY_COLUMN)
        if key in known and link_col and row.get("url"):
            # the image name is the Neuroglancer link. It is generated, not typed, so
            # keep it pointing at the current viewer state
            cell = ws.cell(known[key], link_col)
            link = cell.hyperlink
            # Excel stores the part after '#' separately once it has saved the file
            current = None if link is None else (link.target or "") + ("#" + link.location if link.location else "")
            if current != row["url"]:
                cell.hyperlink = row["url"]
                cell.font = Font(name="Calibri", size=10, bold=True, underline="single", color="00796B")
                relinked += 1
        if key in known:
            # refresh the generated columns of a row that is already there
            for header in NOTES_GENERATED:
                if header in cols and header in row["cells"]:
                    cell = ws.cell(known[key], cols[header])
                    if cell.value != row["cells"][header]:
                        cell.value = row["cells"][header]
                        cell.border = BORDER
                        cell.font = Font(name="Calibri", size=10)
                        cell.alignment = Alignment(vertical="center", horizontal="center")
                        style_generated(cell, header)
                        relinked += 1
        if not key or key in known:
            continue
        failure = ""
        if row["cells"].get("Status") == "FAILED":
            failure = (f"Conversion FAILED {row.get('failed_on') or dt.date.today().isoformat()}: "
                       f"{row.get('error') or 'see the conversion log'}")
        last += 1
        added += 1
        for header, group, width in NOTES_COLUMNS:
            if header not in cols:
                continue  # the user removed that column
            cell = ws.cell(last, cols[header])
            cell.border = BORDER
            cell.font = Font(name="Calibri", size=10, bold=header == "Image Name")
            cell.alignment = Alignment(vertical="center", wrap_text=header == "Notes",
                                       horizontal="left" if header in ("Image Name", "Notes", KEY_COLUMN) else "center")
            if header in manual:
                cell.fill = PatternFill("solid", fgColor=GROUPS[group][1])
                if header == "Notes" and failure:
                    cell.value = failure
                    cell.font = Font(name="Calibri", size=10, bold=True, color="9C0006")
                if header == "Date Analyzed":
                    cell.number_format = "yyyy-mm-dd"
            else:
                cell.value = row["cells"].get(header)
                if header == "Date Acquired":
                    cell.number_format = "yyyy-mm-dd"
                if header == "Image Name" and row.get("url"):
                    cell.hyperlink = row["url"]
                    cell.font = Font(name="Calibri", size=10, bold=True, underline="single", color="00796B")
                style_generated(cell, header)
        ws.row_dimensions[last].height = 22
        known[key] = last
    if sheet == "Notes":
        relinked += add_thumbnails(ws, rows, known, cols.get(THUMB_HEADER))
    if not added and not relinked and path.is_file():
        return 0, ""
    ws.auto_filter.ref = f"A{NOTES_HEADER_ROW}:{get_column_letter(ws.max_column)}{max(last, NOTES_HEADER_ROW + 1)}"
    try:
        safe_save(wb, path)
    except PermissionError:
        return 0, "is open in Excel, so new images were not added to it; close it and run again"
    except Exception as e:
        return 0, f"could not be saved ({e}); it was left exactly as it was"
    return added, ""


def darker(rgb, factor=0.72):
    """Screen-bright colours are unreadable on white; darken them for text."""
    return "".join(f"{int(v * factor):02X}" for v in rgb)


def fill_sheet(ws, rows, kept, title, subtitle):
    """Write one tab of the image list."""
    n_cols = len(COLUMNS)
    band_row, header_row = TITLE_ROWS + 1, TITLE_ROWS + 2
    first = header_row + 1

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    ws.cell(1, 1, title).font = Font(name="Calibri", size=18, bold=True, color="1F3864")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n_cols)
    ws.cell(2, 1, subtitle).font = Font(name="Calibri", size=10, italic=True, color="7F7F7F")
    ws.row_dimensions[1].height = 30
    ws.row_dimensions[2].height = 18

    # coloured group band, then the column headers
    start = 1
    for i in range(1, n_cols + 2):
        group = COLUMNS[i - 1][1] if i <= n_cols else None
        if group != COLUMNS[start - 1][1]:
            name = COLUMNS[start - 1][1]
            ws.merge_cells(start_row=band_row, start_column=start, end_row=band_row, end_column=i - 1)
            for c in range(start, i):
                ws.cell(band_row, c).fill = PatternFill("solid", fgColor=GROUPS[name][0])
            cell = ws.cell(band_row, start, name.upper())
            cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal="center", vertical="center")
            start = i
    for c, (header, group, width, manual) in enumerate(COLUMNS, start=1):
        cell = ws.cell(header_row, c, header)
        cell.font = Font(name="Calibri", size=11, bold=True, color=GROUPS[group][0])
        cell.fill = PatternFill("solid", fgColor=GROUPS[group][1])
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(left=THIN, right=THIN, top=THIN, bottom=Side(style="medium", color=GROUPS[group][0]))
        ws.column_dimensions[get_column_letter(c)].width = width
    ws.row_dimensions[band_row].height = 18
    ws.row_dimensions[header_row].height = 32

    status_fill = {"Converted": "C6EFCE", "On server": "DDEBF7", "FAILED": "FFC7CE"}
    status_font = {"Converted": "006100", "On server": "1F4E79", "FAILED": "9C0006"}
    for n, row in enumerate(rows):
        r = first + n
        values = dict(row["cells"])
        for h, v in kept.get(row["cells"].get(KEY_COLUMN), {}).items():
            if h != "Source File" or not values.get(h):  # a source seen this run wins
                values[h] = v
        stripe = "F7F7F7" if n % 2 else "FFFFFF"
        for c, (header, group, width, manual) in enumerate(COLUMNS, start=1):
            cell = ws.cell(r, c, values.get(header))
            cell.border = BORDER
            cell.font = Font(name="Calibri", size=10)
            cell.alignment = Alignment(
                vertical="center", wrap_text=header in ("Microscope", "Channels (laser)", "Notes", "Image Name", "AB Density by Channel", "Link Status"),
                horizontal="left" if header in ("Image Name", "Microscope", "Objective", "Channels (laser)", "Notes", "Source File", "Zarr Folder", "AB Density by Channel") else "center",
            )
            # hand-filled columns keep their group tint so the gaps to fill stand out
            cell.fill = PatternFill("solid", fgColor=GROUPS[group][1] if manual else stripe)
            if header in ("Date Acquired", "Date Analyzed"):
                cell.number_format = "yyyy-mm-dd"
            style_generated(cell, header)
        # the image name is the Neuroglancer link
        name = ws.cell(r, HEADERS.index("Image Name") + 1)
        name.font = Font(name="Calibri", size=10, bold=True)
        if row.get("url"):
            name.hyperlink = row["url"]
            name.font = Font(name="Calibri", size=10, bold=True, underline="single", color="00796B")
            if row.get("saved_view"):
                name.comment = Comment(f"Opens the view saved {row['saved_view']} with Save_NG_View.bat", "Zarr converter")
        elif row.get("no_link_reason"):
            name.comment = Comment(f"No Neuroglancer link: {row['no_link_reason']}", "Zarr converter")

        if row.get("date_note"):
            ws.cell(r, HEADERS.index("Date Acquired") + 1).comment = Comment(f"Source: {row['date_note']}", "Zarr converter")
        if row.get("channels"):
            parts = []
            for i, ch in enumerate(row["channels"]):
                if i:
                    parts.append(TextBlock(InlineFont(sz=10, color="808080"), "  ·  "))
                shown_white = ch.get("pan") or len(row["channels"]) == 1
                parts.append(TextBlock(InlineFont(sz=10, b=True, color="404040" if shown_white else darker(ch["rgb"])),
                                       ch["label"]))
                if ch.get("pan"):
                    parts.append(TextBlock(InlineFont(sz=10, i=True, color="404040"), " (pan)"))
            ws.cell(r, HEADERS.index("Channels (laser)") + 1).value = CellRichText(*parts)

        status = ws.cell(r, HEADERS.index("Status") + 1)
        kind = "FAILED" if str(status.value).startswith("FAILED") else status.value
        if kind in status_fill:
            status.fill = PatternFill("solid", fgColor=status_fill[kind])
            status.font = Font(name="Calibri", size=10, bold=True, color=status_font[kind])
        if row.get("error"):
            status.comment = Comment(row["error"], "Zarr converter")

        ws.row_dimensions[r].height = 30

    last = max(first, first + len(rows) - 1)
    ws.freeze_panes = ws.cell(first, 2)
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(n_cols)}{last}"
    ws.sheet_view.zoomScale = 100
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{band_row}:{header_row}"

    # Everything here is regenerated on each run, so block accidental typing. Notes
    # belong in the notes workbook. Filtering and resizing stay available.
    ws.protection.sheet = True
    ws.protection.autoFilter = False
    ws.protection.formatColumns = False
    ws.protection.formatRows = False



def write_workbook(xlsx, sheets, kept):
    """sheets: [(tab name, rows, title, subtitle)], written in order, then the Legend."""
    wb = Workbook()
    for i, (name, rows, title, subtitle) in enumerate(sheets):
        ws = wb.active if i == 0 else wb.create_sheet(name)
        ws.title = name
        fill_sheet(ws, rows, kept if i == 0 else {}, title, subtitle)
    write_legend(wb.create_sheet("Legend"))
    try:
        if xlsx.is_file():
            os.chmod(xlsx, stat.S_IWRITE)
        wb.save(xlsx)
        os.chmod(xlsx, stat.S_IREAD)  # Excel then opens it read-only and does not lock it
        return xlsx
    except PermissionError:
        # the list is open in Excel; do not lose the run
        alt = xlsx.with_name(f"{xlsx.stem} ({dt.datetime.now():%Y%m%d-%H%M%S}){xlsx.suffix}")
        wb.save(alt)
        return alt


def write_legend(ws):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 90
    r = 1
    ws.cell(r, 1, "Column groups").font = Font(size=14, bold=True, color="1F3864")
    notes = {
        "Image": "Click an image name to open it in Neuroglancer, one coloured layer per channel. A side palette "
                 "lists each channel's colour dropdown, 'normalized' brightness bar and opacity. A name that is not "
                 "a link has no viewable image; hover it for the reason. Everything in this workbook is generated; "
                 "nothing here is typed by hand.",
        "Sample & Labels": "Typed in the Notes workbook and copied here on each run. Do not type here.",
        "Acquisition": "Read from the image metadata. Hover the date cell to see where the date came from.",
        "Analysis": "Measured from the pixels of the plane the link opens on: how densely each antibody channel "
                    "is labelled, with background excluded. A spot is 3 or more connected pixels that are over 5 "
                    "noise-widths above their local background. Areas are imaged micrometres, not corrected for expansion.",
        "Files": "Conversion result, the original file, and the zarr's folder inside the zarr destination.",
        "Neuroglancer": "Click to open the image with one coloured layer per channel. To change a channel's colour: "
                        "click its layer name at the top, then in the panel on the right (Rendering tab) click the "
                        "'color' swatch. Brightness is the 'normalized' slider just below it.",
    }
    for name, (dark, light) in GROUPS.items():
        if name not in {c[1] for c in COLUMNS}:
            continue
        r += 1
        cell = ws.cell(r, 1, name)
        cell.fill = PatternFill("solid", fgColor=dark)
        cell.font = Font(bold=True, color="FFFFFF")
        ws.cell(r, 2, notes[name]).fill = PatternFill("solid", fgColor=light)
    r += 2
    ws.cell(r, 1, "Channel colours").font = Font(size=14, bold=True, color="1F3864")
    r += 1
    ws.cell(r, 1, "pan stain / single channel").fill = PatternFill("solid", fgColor="D9D9D9")
    ws.cell(r, 2, "white (written in grey in this list)")
    r += 1
    ws.cell(r, 1, "405 (nuclear stain)").fill = PatternFill("solid", fgColor="00FFFF")
    ws.cell(r, 2, "cyan")
    for i, (label, rgb) in enumerate(ANTIBODY_COLORS[:4]):
        r += 1
        cell = ws.cell(r, 1, f"antibody channel {i + 1}")
        cell.fill = PatternFill("solid", fgColor="".join(f"{v:02X}" for v in rgb))
        cell.font = Font(bold=True)
        ws.cell(r, 2, label + ("  (the longest laser)" if i == 0 else ""))
    r += 1
    how = ws.cell(r, 2, "Antibody channels are coloured in order of laser, longest first, so two antibodies are "
                        "always magenta and green. The laser is the number in the channel name, else the recorded "
                        "excitation wavelength, else the dye name, else emission minus 30 nm; a leading ~ means it "
                        "was inferred, not recorded. '(pan)' marks the channel detected as the pan stain. Every "
                        "layer has a 'colour' dropdown of coloured squares (the blue square is cyan), plus "
                        "'invert' and 'defaults' tick boxes.")
    how.font = Font(italic=True, color="7F7F7F")
    how.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[r].height = 62
    r += 2
    ws.cell(r, 1, "Status").font = Font(size=14, bold=True, color="1F3864")
    for label, fill, text in (
        ("Converted", "C6EFCE", "Converted to OME-Zarr in this run."),
        ("On server", "DDEBF7", "The zarr was already in the destination folder before this run."),
        ("FAILED", "FFC7CE", f"Bio-Formats could not convert the file. Hover the cell for the reason; full output is in {LOG_NAME}."),
    ):
        r += 1
        ws.cell(r, 1, label).fill = PatternFill("solid", fgColor=fill)
        ws.cell(r, 2, text)


# --------------------------------------------------------------------------- main


def save_view(url):
    """Make a Neuroglancer view (colours, brightness, position) the one the Excel link opens.

    `url` is the browser address copied after adjusting the image; with no url the
    clipboard is used. The view replaces that image's saved state on the server.
    """
    load_settings()
    out_root = Path(os.path.abspath(DEFAULT_OUT))
    if not url:
        url = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                             capture_output=True, text=True, errors="replace").stdout
    url = (url or "").strip().strip('"')
    if "#!" not in url:
        print("That is not a Neuroglancer address. In the browser, click the address bar,\n"
              "press Ctrl+A then Ctrl+C, and run this again.")
        return 1
    fragment = url.split("#!", 1)[1]
    if re.match(r"[a-z][a-z0-9+.-]*://", fragment):
        print("That address is the unchanged link from Excel, so there is nothing to save.\n"
              "Change a colour or the brightness first; the address then becomes much longer.")
        return 1
    try:
        state = json.loads(urllib.parse.unquote(fragment))
        sources = [l["source"] if isinstance(l["source"], str) else l["source"]["url"] for l in state["layers"]]
    except (ValueError, KeyError, TypeError):
        print("The address could not be read. Copy the whole address (Ctrl+A, Ctrl+C) and try again.")
        return 1
    base = server_url_for(out_root)
    if base is None:
        print(f"The zarr folder {out_root} is not on the web server, so there are no links to update.")
        return 1
    m = None
    for src in sources:
        m = re.match(r"zarr://" + re.escape(base) + r"/([^/]+)\.zarr/(\d+)/?$", src)
        if m:
            break
    if not m:
        print(f"That view does not show an image from {out_root}.")
        return 1
    stem, series = urllib.parse.unquote(m.group(1)), m.group(2)
    stem = stem[:-len(".ome")] if stem.endswith(".ome") else stem
    if not (out_root / f"{urllib.parse.unquote(m.group(1))}.zarr").is_dir():
        print(f"{stem}.zarr is no longer in {out_root}.")
        return 1
    state_path = out_root / STATE_DIR / f"{stem}_s{series}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    state_path.with_suffix(".saved").write_text(
        f"View saved {dt.datetime.now():%Y-%m-%d %H:%M}. Delete this file to go back to the automatic view.\n",
        encoding="utf-8")
    print(f"Saved the view for {stem}.")
    for layer in state["layers"]:
        controls = layer.get("shaderControls", {})
        rng = controls.get("normalized", {}).get("range")
        opened = re.search(r'select\(options=\[[^\]]*\], default="([^"]*)"\)', layer.get("shader", ""))
        square = controls.get("colour", opened.group(1) if opened else "")
        colour = next((name for name, sq, _ in STANDARD_COLORS if sq == square), "?")
        print(f"   {layer.get('name', '?'):<16} colour {colour}   brightness {rng if rng else 'full range'}")
    print("\nThe link in Excel opens this view, for everyone who uses the list. If a browser")
    print("still shows the old view, rebuild the list (Convert_To_Zarr.bat, then Enter).")
    print(f"To undo, delete {state_path.with_suffix('.saved')} and run the converter.")
    return 0


def find_precomputed(dataset, depth=3):
    """Folders under `dataset` that hold a precomputed 'info' file. Scale folders
    ('203_203_200') are full of chunks and are not entered."""
    found, level = [], [dataset]
    for _ in range(depth + 1):
        nxt = []
        for d in level:
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            if any(e.name == "info" and e.is_file() for e in entries):
                found.append(Path(d))
                continue
            nxt += [e.path for e in entries if e.is_dir() and not re.fullmatch(r"\d+_\d+_\d+", e.name)
                    and not e.name.startswith(("_", "."))]
        level = nxt
    return sorted(found)


def sample_precomputed(volume, info):
    """(low, high) display range from a 3 x 3 grid of chunks across the middle plane of
    the coarsest scale. One chunk alone can sit in an unusually dark or bright spot."""
    import gzip

    scale = info["scales"][-1]
    if scale.get("encoding") != "raw":
        return None
    size, chunk = scale["size"], scale["chunk_sizes"][0]
    offset = scale.get("voxel_offset", [0, 0, 0])

    def span(axis, frac):
        start = int(size[axis] * frac) // chunk[axis] * chunk[axis]
        return f"{offset[axis] + start}-{offset[axis] + min(start + chunk[axis], size[axis])}"

    pieces = []
    for fx in (0.25, 0.5, 0.75):
        for fy in (0.25, 0.5, 0.75):
            path = volume / scale["key"] / "_".join((span(0, fx), span(1, fy), span(2, 0.5)))
            if path.is_file():
                raw = path.read_bytes()
            elif path.with_name(path.name + ".gz").is_file():
                raw = path.with_name(path.name + ".gz").read_bytes()
            else:
                continue
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            pieces.append(np.frombuffer(raw, dtype=np.dtype(info["data_type"]).newbyteorder("<")))
    if not pieces:
        return None
    data = np.concatenate(pieces)
    data = data[data > 0]  # exact zeros are padding around the stitched tiles
    if data.size == 0:
        return None
    lo, hi = np.percentile(data, [1, 99.9])
    if np.issubdtype(data.dtype, np.integer):
        return int(lo), int(max(hi, lo + 1))
    return float(lo), float(max(hi, lo + 1e-6))


def stitched_rows(folder, refresh):
    """One spreadsheet row per stitched dataset in `folder` (Neuroglancer precomputed)."""
    make_links = server_url_for(folder) is not None
    rows = []
    for ds in sorted((d for d in folder.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))),
                     key=lambda d: d.name.lower()):
        print(f"   {ds.name}")
        date, note = find_acquisition_date("", {}, [ds.name], ds)
        row = {"cells": {"Image Name": ds.name, "Date Acquired": date.date() if date else None,
                         "Status": "On server", KEY_COLUMN: ds.name},
               "date_note": note}
        volumes = []
        for vol in find_precomputed(ds):
            try:
                info = json.loads((vol / "info").read_text(encoding="utf-8"))
                if info.get("type", "image") == "image" and info.get("scales"):
                    volumes.append((vol, info))
            except (OSError, ValueError):
                continue
        if not volumes:
            row["no_link_reason"] = "no precomputed 'info' file found"
            print("      no precomputed 'info' file found; listed without a link")
            rows.append(row)
            continue

        res, size = volumes[0][1]["scales"][0]["resolution"], volumes[0][1]["scales"][0]["size"]
        channels = []
        for i, (vol, info) in enumerate(volumes):
            label = "image" if vol == ds else str(vol.relative_to(ds)).replace("\\", "/")
            label = re.sub(r"/precomputed$", "", label)
            rgb = (255, 255, 255) if len(volumes) == 1 else FALLBACK_PALETTE[i % len(FALLBACK_PALETTE)]
            channels.append({"index": i, "label": label, "rgb": rgb, "vol": vol, "info": info})
        row["channels"] = [{"label": c["label"], "rgb": (90, 90, 90) if c["rgb"] == (255, 255, 255) else c["rgb"]} for c in channels]
        row["cells"].update({
            "Size X × Y × Z": f"{size[0]} × {size[1]} × {size[2]}",
            "C": sum(c["info"].get("num_channels", 1) for c in channels), "T": 1,
            "Voxel X, Y, Z (µm)": ", ".join(f"{r / 1000:.4g}" for r in res),  # precomputed resolution is in nm
        })
        if make_links:
            state_path = folder / STATE_DIR / f"{ds.name}.json"
            old = {}
            if state_path.is_file() and not refresh:
                try:
                    old = {l["name"]: l["shaderControls"]["normalized"]["range"]
                           for l in json.loads(state_path.read_text(encoding="utf-8"))["layers"]}
                except (ValueError, KeyError, TypeError):
                    old = {}
            layers = []
            for c in channels:
                rng = old.get(c["label"])
                if rng is None:
                    try:
                        rng = sample_precomputed(c["vol"], c["info"])
                    except Exception as e:
                        print(f"      brightness sampling failed ({e}); link will need Auto range")
                layer = {
                    "type": "image",
                    "source": f"precomputed://{server_url_for(c['vol'])}",
                    "tab": "rendering", "blend": "additive", "opacity": 1,
                    "shader": layer_shader(c["rgb"]),
                    "name": c["label"],
                }
                if rng:
                    lo, hi = rng
                    layer["shader"] = layer_shader(c["rgb"], False, (lo, hi))
                    layer["shaderControls"] = {"normalized": {"range": [lo, hi], "window": [0, hi + (hi - lo) * 0.5]}}
                layers.append(layer)
            state = {
                "dimensions": {a: [r * 1e-9, "m"] for a, r in zip("xyz", res)},
                "position": [n / 2 for n in size],
                "crossSectionScale": round(max(1.0, max(size[0], size[1]) / 900), 2),
                "projectionScale": max(size[0], size[1]) * 1.5,
                "layers": layers,
                "selectedLayer": {"visible": True, "layer": layers[0]["name"]},
                "layout": "4panel",
                "selection": {"row": 2, "visible": False},
                "toolPalettes": {"Shader controls": {"row": 1, "query": "type:shaderControl +type:opacity"}},
            }
            row["url"] = state_to_link(state, state_path)
        else:
            row["no_link_reason"] = f"not under {SERVER_FOLDER}"
        rows.append(row)
        print(f"      {size[0]}×{size[1]}×{size[2]}  {len(volumes)} volume(s)")
    return rows


def load_failures(out_root):
    """{zarr folder name: {"source", "error", "date"}} for conversions that failed."""
    try:
        return json.loads((out_root / FAILED_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_failures(out_root, failures):
    path = out_root / FAILED_NAME
    if failures:
        path.write_text(json.dumps(failures, indent=1), encoding="utf-8")
    elif path.is_file():
        path.unlink()


def personal_view(out_root, key):
    """This user's own default view file for a row key ('name.zarr\\0'), whether or not it exists.

    Saved with Save_NG_View.bat. It is kept on the server, where Neuroglancer can load it,
    in a folder named after the Windows user; only that user's notes workbook links to it.
    """
    parts = Path(key).parts
    stem = parts[0][: -len(".zarr")]
    stem = stem[: -len(".ome")] if stem.endswith(".ome") else stem
    series = parts[1] if len(parts) > 1 else "0"
    return out_root / STATE_DIR / "users" / getpass.getuser() / f"{stem}_s{series}.json"


def with_personal_views(rows, out_root):
    """Copies of the rows whose link is the user's own default view where they saved one."""
    out = []
    for row in rows:
        key = row["cells"].get(KEY_COLUMN)
        mine = personal_view(out_root, key) if key and row.get("url") else None
        if mine is not None and mine.is_file():
            row = dict(row, url=state_url(mine))
        out.append(row)
    return out


def measure_density(series_dir, info, channel, z):
    """How densely one channel is labelled on plane z, background excluded. None without scipy.

    Local background is the median of each 64x64-pixel block (so uneven illumination and
    tile shading are followed, not counted); noise is the robust spread of what is left;
    a labelled pixel is over 5 noise-widths above its background; a spot is 3 or more
    connected labelled pixels. Zero pixels (stitching padding) are left out.
    """
    try:
        import zarr
        from scipy import ndimage
    except ImportError:
        return None
    block = 64
    arr = zarr.open(str(series_dir / info["levels"][0]), mode="r")
    idx = tuple({"t": 0, "c": channel, "z": z, "y": slice(None), "x": slice(None)}[a] for a in info["axes"])
    img = np.asarray(arr[idx]).astype(np.float32)
    valid = img > 0
    if valid.sum() < 1000 or min(img.shape) < 2 * block:
        return None
    h, w = (img.shape[0] // block) * block, (img.shape[1] // block) * block
    blocks = np.where(valid[:h, :w], img[:h, :w], np.nan).reshape(h // block, block, w // block, block)
    with np.errstate(all="ignore"):
        med = np.nanmedian(blocks, axis=(1, 3))
    med = np.where(np.isnan(med), np.nanmedian(med), med)
    med = ndimage.median_filter(med, size=3)  # one block of dense label must not lift its own background
    resid = img - ndimage.zoom(med, (img.shape[0] / med.shape[0], img.shape[1] / med.shape[1]), order=1)
    r = resid[valid]
    sigma = 1.4826 * float(np.median(np.abs(r - np.median(r)))) or 1.0
    labels, n = ndimage.label((resid > 5 * sigma) & valid)
    sizes = np.bincount(labels.ravel())[1:] if n else np.array([], int)
    keep = sizes >= 3
    unit = UNIT_TO_M.get(info["units"].get("x"))
    um = info["scale"]["x"] * unit * 1e6 if unit else None
    area = float(valid.sum()) * um * um if um else None
    spots = int(keep.sum())
    return {"spots": spots, "area_pct": round(100.0 * float(sizes[keep].sum()) / float(valid.sum()), 3),
            "per_1000um2": round(spots / area * 1000, 2) if area else None}


def opening_plane_visible(series_dir, info, state):
    """False when nothing on the plane a view opens on is brighter than its layers'
    brightness floors - the image would look black. Checked on the smallest pyramid level."""
    import zarr

    z = int(round(dict(zip(state["dimensions"], state["position"])).get("z", 0)))
    arr = zarr.open(str(series_dir / info["levels"][-1]), mode="r")
    for layer in state["layers"]:
        lo = ((layer.get("shaderControls") or {}).get("normalized") or {}).get("range", [0])[0]
        idx = tuple({"t": 0, "c": layer["localPosition"][0], "z": z, "y": slice(None), "x": slice(None)}[a]
                    for a in info["axes"])
        if (np.asarray(arr[idx]) > lo).mean() > 1e-4:
            return True
    return False


def check_links(rows, out_root):
    """Fill each row's Link Status: OK, or what is wrong with its link."""
    def reachable(url):
        try:
            req = urllib.request.Request(url, headers={"Origin": NG_VIEWER.rstrip("/")})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status == 200 and bool(r.headers.get("Access-Control-Allow-Origin"))
        except Exception:
            return False

    def one(row):
        cells = row["cells"]
        if not row.get("url"):
            reason = row.get("error") or row.get("no_link_reason") or "no view"
            return "Not converted: " + reason if cells.get("Status") == "FAILED" else "No link: " + reason
        if not reachable(row["url"].split("#!", 1)[1]):
            return "View file not reachable on the web server"
        if "visible" in row:   # a zarr image (not a stitched dataset)
            if not reachable(server_url_for(out_root / cells[KEY_COLUMN]) + "/.zattrs"):
                return "Image data not reachable on the web server"
            if row["visible"] is False:
                return "Nothing visible on the opening plane"
        return "OK"

    with ThreadPoolExecutor(8) as pool:
        for row, status in zip(rows, pool.map(one, rows)):
            row["cells"]["Link Status"] = status
    return sum(1 for r in rows if r["cells"]["Link Status"] != "OK")


def failed_row(src, zarr_dir, error):
    date, note = find_acquisition_date("", {}, [src.name, src.parent.name], src)
    return {
        "cells": {
            "Image Name": zarr_dir.name[: -len(".zarr")], "Date Acquired": date.date() if date else None,
            "Microscope": MICROSCOPE_BY_EXT.get(src.suffix.lower(), ""),
            "Status": "FAILED", "Source File": str(src), KEY_COLUMN: zarr_dir.name,
        },
        "date_note": note, "error": error, "no_link_reason": "not converted",
    }


def build_rows(zarr_dir, out_root, src, status, make_links, refresh):
    """Spreadsheet rows for one zarr (one per image series). `src` is None when the
    zarr was not made in this run."""
    images, ome_objective, ome_microscope, original = read_ome_xml(zarr_dir)
    series_dirs = sorted((d for d in zarr_dir.iterdir() if d.is_dir() and d.name.isdigit()), key=lambda d: int(d.name))
    # Imaris files converted without '--series 0' carry their resolution levels as
    # extra series; only level 1 is the image
    series_dirs = [
        d for d in series_dirs
        if not (int(d.name) < len(images) and re.search(r"Resolution Level (?!1$)\d+$", images[int(d.name)]["name"]))
    ]
    stem = zarr_dir.name[: -len(".zarr")]
    stem = stem[: -len(".ome")] if stem.endswith(".ome") else stem
    rows = []
    for sdir in series_dirs:
        s = int(sdir.name)
        try:
            info = read_series(sdir)
        except Exception as e:  # e.g. plate layouts, which have no image at this level
            print(f"      series {s}: could not read zarr metadata ({e})")
            continue
        img = images[s] if s < len(images) else {"name": "", "date": "", "channels": []}
        shape, scale = info["shape"], info["scale"]
        channels = describe_channels(img["channels"], info["omero"], shape.get("c", 1))
        date, note = find_acquisition_date(img["date"], original, [zarr_dir.name], zarr_dir / ".zattrs")
        # the original file type survives in the OME image name ('x.ims Resolution Level 1')
        ext = src.suffix.lower() if src else next(
            (e for e in sorted(MICROSCOPE_BY_EXT) if e + " " in img["name"].lower() + " "), "")

        name = stem
        if len(series_dirs) > 1:
            name = f"{stem} · {img['name'] or f'series {s}'}"

        # an axis with no unit has no calibration in the file (e.g. z of a single plane)
        voxel = ", ".join(
            f"{scale[a] * UNIT_TO_M[info['units'][a]] * 1e6:.4g}" if info["units"].get(a) in UNIT_TO_M else "–"
            for a in ("x", "y", "z") if a in scale
        )
        row = {
            "cells": {
                "Image Name": name,
                "Date Acquired": date.date() if date else None,
                "Microscope": describe_microscope(ext, ome_microscope, original),
                "Objective": describe_objective(ome_objective, original),
                "Size X × Y × Z": f"{shape.get('x', 1)} × {shape.get('y', 1)} × {shape.get('z', 1)}",
                "C": shape.get("c", 1), "T": shape.get("t", 1),
                "Voxel X, Y, Z (µm)": voxel,
                "Voxels": shape.get("x", 1) * shape.get("y", 1) * shape.get("z", 1),
                "Extent X × Y × Z (µm)": " × ".join(
                    f"{shape[a] * scale[a] * UNIT_TO_M[info['units'][a]] * 1e6:.4g}"
                    if info["units"].get(a) in UNIT_TO_M else "–" for a in ("x", "y", "z") if a in shape),
                "Volume (µm³)": round(float(np.prod([shape[a] * scale[a] * UNIT_TO_M[info["units"][a]] * 1e6
                                                    for a in ("x", "y", "z")])))
                if all(a in shape and info["units"].get(a) in UNIT_TO_M for a in ("x", "y", "z")) else None,
                "Status": status, "Source File": str(src) if src else None,
                KEY_COLUMN: str(sdir.relative_to(out_root)),
            },
            "channels": channels, "date_note": note,
        }
        if make_links:
            state_path = out_root / STATE_DIR / f"{stem}_s{s}.json"
            marker = state_path.with_suffix(".saved")
            if marker.is_file() and state_path.is_file():
                # the user saved their own colours and brightness for this image; keep them
                row["url"] = state_url(state_path)
                row["saved_view"] = dt.datetime.fromtimestamp(marker.stat().st_mtime).strftime("%Y-%m-%d")
                rows.append(row)
                print(f"      {shape.get('x')}×{shape.get('y')}×{shape.get('z')}  C={shape.get('c', 1)}  [saved view]")
                continue
            # What was measured from the pixels is kept beside the view file, so later
            # runs rebuild the view without pulling pixels off the server again.
            cache = state_path.with_suffix(".stats.json")
            ranges = centre = stats = None
            kept_stats = {}
            if cache.is_file() and not refresh:
                try:
                    kept_stats = json.loads(cache.read_text(encoding="utf-8"))
                    ranges = [tuple(r) for r in kept_stats["ranges"]]
                    centre, stats = kept_stats["centre"], kept_stats["stats"]
                    if len(ranges) != len(channels):
                        ranges = None
                except (ValueError, KeyError, TypeError):
                    ranges = None
            if ranges is None:
                try:
                    ranges, centre, stats = sample_ranges(sdir, info)
                    kept_stats = {"ranges": ranges, "centre": centre, "stats": stats}
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(kept_stats, indent=1), encoding="utf-8")
                except Exception as e:
                    print(f"      series {s}: brightness sampling failed ({e}); link will need Auto range")
                    ranges, centre, stats = [], None, []
            pan, why = pick_pan(stem, channels, stats or [])
            if pan is not None:
                channels[pan]["pan"] = True
            assign_colours(channels)
            if pan is not None:
                channels[pan]["color_name"] = f"white, pan ({why})"
            state = build_state(server_url_for(sdir), info, channels, ranges, centre, stats)
            row["url"] = state_to_link(state, state_path)

            # measurements kept with the other cached values: they need pixels off the server
            dirty = False
            z_open = int(round(dict(zip(state["dimensions"], state["position"])).get("z", 0)))
            floors = [l["shaderControls"]["normalized"]["range"][0] if "shaderControls" in l else None
                      for l in state["layers"]]
            seen = kept_stats.get("visible") or {}
            if seen.get("z") != z_open or seen.get("floors") != floors:
                try:
                    seen = {"z": z_open, "floors": floors, "ok": bool(opening_plane_visible(sdir, info, state))}
                    kept_stats["visible"], dirty = seen, True
                except Exception as e:
                    print(f"      series {s}: could not check the opening plane ({e})")
                    seen = {}
            if "ok" in seen:
                row["visible"] = seen["ok"]
            antibodies = [c for c in channels if (not c.get("pan") or len(channels) == 1)
                          and not (c.get("laser") and c["laser"] < NUCLEAR_BELOW_NM)]
            density = kept_stats.get("density") or {}
            if density.get("z") != z_open or set(density.get("channels", {})) != {str(c["index"]) for c in antibodies}:
                try:
                    found = {str(c["index"]): measure_density(sdir, info, c["index"], z_open) for c in antibodies}
                    if all(v is not None for v in found.values()):
                        density = {"z": z_open, "channels": found}
                        kept_stats["density"], dirty = density, True
                except Exception as e:
                    print(f"      series {s}: antibody density not measured ({e})")
            measured = [(c, density["channels"][str(c["index"])]) for c in antibodies
                        if str(c["index"]) in density.get("channels", {})]
            if measured:
                per = [d["per_1000um2"] for _, d in measured if d.get("per_1000um2") is not None]
                row["cells"]["AB Spots / 1000 µm²"] = max(per) if per else None
                row["cells"]["AB Density by Channel"] = "  ·  ".join(
                    f"{c['label']}: " + (f"{d['per_1000um2']:.1f} spots" if d.get("per_1000um2") is not None
                                         else f"{d['spots']} spots") + f", {d['area_pct']:.2f}% area"
                    for c, d in measured)
            if dirty and kept_stats.get("ranges") is not None:
                cache.write_text(json.dumps(kept_stats, indent=1), encoding="utf-8")
        else:
            assign_colours(channels)
            row["no_link_reason"] = f"not under {SERVER_FOLDER}"
        rows.append(row)
        if len(channels) == 1:
            channels[0]["color_name"] = "white (single channel)"
        lasers = ", ".join(f"{c['label']} → {c['color_name']}" for c in channels)
        print(f"      {shape.get('x')}×{shape.get('y')}×{shape.get('z')}  C={shape.get('c', 1)}  [{lasers}]")
    return rows


def main():
    if "--setup" in sys.argv[1:]:
        sys.exit(run_setup())
    if "--save-view" in sys.argv[1:]:
        rest = [a for a in sys.argv[1:] if a != "--save-view"]
        sys.exit(save_view(rest[0] if rest else ""))
    load_settings()
    ap = argparse.ArgumentParser(description="Batch-convert microscopy images to OME-Zarr and list them in Excel.")
    ap.add_argument("source", nargs="?", help="folder to scan (subfolders included) or a single image file; "
                                              "omit with --list-only")
    ap.add_argument("--setup", action="store_true", help="first-time setup: check prerequisites and save paths")
    ap.add_argument("--save-view", action="store_true",
                    help="make the Neuroglancer address on the clipboard the view the Excel link opens")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"folder the zarrs go in (default: {DEFAULT_OUT})")
    ap.add_argument("--list", dest="xlsx", help=f"read-only Excel image list (default: {DEFAULT_LIST})")
    ap.add_argument("--notes", help=f"your notes workbook (default: {DEFAULT_NOTES})")
    ap.add_argument("--overwrite", action="store_true", help="re-convert files whose zarr already exists")
    ap.add_argument("--include-tiles", action="store_true",
                    help="also convert raw stitching tiles (names ending _F0, _F1, ...), which are skipped by default")
    ap.add_argument("--from-list", help="text file with one image path per line to convert, instead of a folder")
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 4), help="parallel tile workers per file")
    ap.add_argument("--work-dir", default=DEFAULT_WORK or None,
                    help="local scratch folder for network data (default: the Windows temp folder)")
    ap.add_argument("--no-local", action="store_true", help="never stage on the local disk; convert in place")
    ap.add_argument("--list-only", action="store_true", help="convert nothing; just rebuild the Excel list")
    ap.add_argument("--refresh-links", action="store_true", help="re-sample brightness and rebuild every Neuroglancer link")
    args = ap.parse_args()

    out_root = Path(os.path.abspath(args.out))
    plan = []
    if not args.list_only:
        if not args.source and not args.from_list:
            sys.exit("Give a folder or an image file to convert.")
        source = Path(os.path.abspath((args.from_list or args.source).strip().strip('"')))
        if args.from_list:
            lines = [ln.strip().strip('"') for ln in source.read_text(encoding="utf-8-sig").splitlines()]
            files = [Path(ln) for ln in lines if ln and not ln.startswith("#")]
            missing = [f for f in files if not f.is_file()]
            if missing:
                sys.exit("Not found:\n  " + "\n  ".join(str(m) for m in missing[:10]))
        elif source.is_file():
            if source.suffix.lower() not in EXTENSIONS:
                sys.exit(f"Not a supported image type: {source.name}\nLooked for: {', '.join(sorted(EXTENSIONS))}")
            files = [source]
        elif source.is_dir():
            files = find_images(source, [out_root], args.include_tiles)
            if not files:
                sys.exit(f"No image files found under {source}\nLooked for: {', '.join(sorted(EXTENSIONS))}")
        else:
            sys.exit(f"Not found: {source}")
        java, app = find_converter()
        if not java:
            sys.exit("NGFF-Converter is not installed (looked in Program Files).\n"
                     f"Install it from {CONVERTER_DOWNLOAD}\nthen run First_Time_Setup.bat.")
        plan, duplicates = plan_outputs(files, out_root)

    try:
        out_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.exit(f"Cannot reach the zarr folder {out_root}\n{e}")
    xlsx = Path(args.xlsx) if args.xlsx else Path(DEFAULT_LIST)
    if not xlsx.parent.is_dir():
        xlsx = out_root / "_Zarr Image List.xlsx"
    make_links = server_url_for(out_root) is not None
    stage = None
    if plan and not args.no_local and (is_network(out_root) or is_network(plan[0][0])):
        stage = Path(args.work_dir or tempfile.gettempdir()) / f"zarr_convert_{os.getpid()}"
        stage.parent.mkdir(parents=True, exist_ok=True)

    if plan:
        new = sum(1 for _, dst in plan if not dst.is_dir())
        print(f"Source   : {source}")
        print(f"Found    : {len(plan)} image(s): {new} new, {len(plan) - new} already converted (skipped"
              + (" unless --overwrite)" if not args.overwrite else ", but --overwrite is on)"))
        if duplicates:
            print(f"           {len(duplicates)} more file(s) left out: same image name as a file above, e.g. "
                  f"{duplicates[0][0].name} (kept {duplicates[0][1].name})")
    print(f"Zarrs    : {out_root}")
    print(f"List     : {xlsx}  (read-only)")
    notes = Path(args.notes or DEFAULT_NOTES)
    if str(notes).lower() == "none":
        notes = None  # the user chose to go without a notes workbook
    else:
        print(f"Notes    : {notes}  (yours to edit)")
    if stage:
        print(f"Working  : locally in {stage.parent} ({shutil.disk_usage(stage.parent).free / 1e9:.0f} GB free), then uploading")
    if not make_links:
        print(f"NOTE     : the zarr folder is not under '{SERVER_FOLDER}', so Neuroglancer links cannot be made.")
    print()

    # nothing is read from the notes workbook; only remember where earlier conversions came from
    kept = read_columns(xlsx, "Images", ["Source File"])
    memo_dir = tempfile.mkdtemp(prefix="bfmemo_")
    counts = {"Converted": 0, "Skipped": 0, "FAILED": 0}
    sources = {}  # zarr folder -> source file, for zarrs touched in this run
    # Failures are kept between runs, so a failure is still written into the notes
    # workbook if that workbook happened to be open when it occurred.
    failures = load_failures(out_root)
    started = time.time()
    try:
        with open(out_root / LOG_NAME, "a", encoding="utf-8") as log:
            for n, (src, dst) in enumerate(plan, start=1):
                print(f"[{n}/{len(plan)}] {src.name}  ({src.stat().st_size / 1e6:,.0f} MB)")
                if dst.is_dir() and not args.overwrite:
                    print(f"      {dst.name} already exists, skipping")
                    counts["Skipped"] += 1
                    sources[dst] = (src, "On server")
                    continue
                t0 = time.time()
                ok, error = run_conversion(java, app, src, dst, args.workers, memo_dir, log, stage)
                print(f"      {'done' if ok else 'FAILED'} in {time.time() - t0:,.0f} s total" + (f": {error}" if error else ""))
                counts["Converted" if ok else "FAILED"] += 1
                if ok:
                    sources[dst] = (src, "Converted")
                    # links must be rebuilt for a re-converted image
                    for old in (out_root / STATE_DIR).glob(f"{dst.name[:-len('.zarr')]}_s*.json"):
                        old.unlink()
                else:
                    failures[dst.name] = {"source": str(src), "error": error,
                                          "date": f"{dt.date.today():%Y-%m-%d}"}

            # the list covers everything in the zarr folder, not just this run
            print("\nBuilding the image list...")
            rows = []
            zarrs = sorted((d for d in out_root.iterdir() if d.is_dir() and d.name.lower().endswith(".zarr")),
                           key=lambda d: d.name.lower())
            for z in zarrs:
                src, status = sources.get(z, (None, "On server"))
                print(f"   {z.name}")
                try:
                    rows.extend(build_rows(z, out_root, src, status, make_links, args.refresh_links))
                except Exception as e:
                    print(f"      could not read its metadata: {e}")
                    log.write(f"metadata error for {z}: {e!r}\n")
                    rows.append({"cells": {"Image Name": z.name[:-len(".zarr")], "Status": "FAILED",
                                           "Source File": str(src) if src else None, KEY_COLUMN: z.name},
                                 "error": f"the zarr exists but its metadata could not be read: {e}",
                                 "no_link_reason": "unreadable zarr"})
            # a file that has since converted is no longer a failure
            failures = {name: f for name, f in failures.items() if not (out_root / name).is_dir()}
            save_failures(out_root, failures)
            for name, f in sorted(failures.items()):
                row = failed_row(Path(f["source"]), out_root / name, f["error"])
                row["failed_on"] = f["date"]
                rows.append(row)
    finally:
        shutil.rmtree(memo_dir, ignore_errors=True)

    stamp = f"updated {dt.datetime.now():%Y-%m-%d %H:%M}"
    sheets = [("Images", rows, "Zarr Image List", f"{out_root}   ·   {len(rows)} image(s)   ·   {stamp}")]
    stitched, stitched_dir = None, Path(DEFAULT_STITCHED)
    if DEFAULT_STITCHED.lower() != "none":
        if stitched_dir.is_dir():
            print("\nListing stitched datasets...")
            stitched = stitched_rows(stitched_dir, args.refresh_links)
            sheets.append(("Stitched", stitched, "Stitched Datasets",
                           f"{stitched_dir}   ·   {len(stitched)} dataset(s)   ·   {stamp}"))
        else:
            print(f"\nNOTE: the stitched folder {stitched_dir} cannot be reached; its tab is left out this run.")
    broken = check_links(rows + (stitched or []), out_root)
    saved = write_workbook(xlsx, sheets, kept)

    print()
    if plan:
        print(f"Done in {time.time() - started:,.0f} s:  {counts['Converted']} converted, "
              f"{counts['Skipped']} already there, {counts['FAILED']} failed")
    added, problem = update_notes(notes, with_personal_views(rows, out_root)) if notes else (0, "")
    if notes and stitched and not problem:
        more, problem = update_notes(notes, stitched, "Stitched", "Stitched Dataset Notes")
        added += more
    if saved != xlsx:
        print("The image list was open for editing in Excel, so this run was saved as a new file.")
    print(f"Image list: {saved}")
    if notes:
        print(f"Notes     : {notes}" + (f"  ({added} new row(s) added)" if added else ""))
    if problem:
        print(f"  ! The notes workbook {problem}.")
    if broken:
        print(f"Links     : {broken} row(s) are flagged in the 'Link Status' column of both workbooks")
    if counts["FAILED"]:
        print(f"Failures  : see {out_root / LOG_NAME}")
    sys.exit(1 if counts["FAILED"] else 0)


if __name__ == "__main__":
    main()
