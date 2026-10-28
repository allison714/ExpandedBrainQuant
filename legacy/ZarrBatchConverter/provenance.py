"""One line saying which code produced an output, written into every log and page.

    stamp(__file__)  ->  "synapse_exf.py 2026.10.28 (code a1b2c3d4) run 2026-10-28 09:15 on KuanLabWorkstation, Python 3.12.4"

The code hash is the first 8 hex digits of the SHA-256 of the script file itself, so the stamp does
not depend on git being present: the same bytes always give the same hash, and a hash can be
matched against the repository history later (git log -p, or sha256sum on a checkout).
The version is the date of the last deliberate change to the toolbox; bump it in VERSION below.
"""
import datetime as dt
import hashlib
import platform
import socket
import sys
from pathlib import Path

VERSION = "2026.10.28"


def code_hash(*files):
    """Short SHA-256 over the given script files, in order."""
    h = hashlib.sha256()
    for f in files:
        h.update(Path(f).read_bytes())
    return h.hexdigest()[:8]


def stamp(*files):
    names = " + ".join(Path(f).name for f in files)
    return (f"{names} {VERSION} (code {code_hash(*files)}) run {dt.datetime.now():%Y-%m-%d %H:%M} on {socket.gethostname()}, "
            f"Python {platform.python_version()}, {sys.platform}")
