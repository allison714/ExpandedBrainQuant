"""Redo every synapse picture with the new layout: all samples, the pan-ASLM volume, its training set, then the gallery."""
import subprocess
import sys
import time
from pathlib import Path

tools = Path(r"C:\Users\admin\OneDrive\Desktop\ZarrBatchConverter")
volume = r"X:\nginx_share\exm\2026\Stitched Precomputed NG\260618_pan_aslm_4x4x9520"
steps = [("synapse_exf", ["synapse_exf.py", "--synaptic", "auto"]),
         ("aslm_synapses", ["aslm_synapses.py", volume, "4", "4", "15", "6"]),
         ("aslm_training export", ["aslm_training.py", "export", volume, "4", "4", "15"]),
         ("make_gallery", ["make_gallery.py", "--server", "--mirror"])]
for title, args in steps:
    t0 = time.time()
    print(f"\n===== {title} =====", flush=True)
    rc = subprocess.run([sys.executable, "-u", "-W", "ignore", *args], cwd=tools, stdin=subprocess.DEVNULL).returncode
    print(f"===== {title}: exit {rc}, {(time.time() - t0) / 60:.1f} min =====", flush=True)
print("\nALL DONE", flush=True)
