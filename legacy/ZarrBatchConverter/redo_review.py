"""Re-detect synapses in every sample with the larger per-sample cap, remake the card JPEGs, rebuild
the PC copy of the Synapse Review page, and refresh the server mirror. Launched detached (Start-Process)
because it runs for hours. Log: redo_review.log beside this file."""
import subprocess
import sys
import time
from pathlib import Path

tools = Path(__file__).resolve().parent
steps = [("synapse_exf (all samples, 150 per sample)", ["synapse_exf.py", "--synaptic", "auto"]),
         ("review_thumbs", ["review_thumbs.py"]),
         ("synapse_review (PC copy)", ["synapse_review.py", "--no-server"]),
         ("make_gallery --mirror", ["make_gallery.py", "--mirror"])]
for title, args in steps:
    t0 = time.time()
    print(f"\n===== {title} =====", flush=True)
    rc = subprocess.run([sys.executable, "-u", "-W", "ignore", *args], cwd=tools, stdin=subprocess.DEVNULL).returncode
    print(f"===== {title}: exit {rc}, {(time.time() - t0) / 60:.1f} min =====", flush=True)
print("\nALL DONE", flush=True)
