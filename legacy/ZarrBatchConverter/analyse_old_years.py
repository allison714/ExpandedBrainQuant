"""After convert_old_years.py has finished: QC screenshots and synapse detection for every converted
image of each old year, with that year's settings (settings_<year>.ini via ZBC_SETTINGS), then the
gallery rebuilt so the old years appear in it (PC copy and server mirror; the password-protected
server page is rebuilt by Make_Gallery_Protected.bat afterwards).

Waits for 'ALL DONE' at the end of convert_old_years.log first, so both can be started together.
Launched detached; log: analyse_old_years.log beside this file. Safe to re-run: samples that already
have pictures only get the ones they lack, and synapse_exf keeps Accept/Notes across runs."""
import os
import subprocess
import sys
import time
from pathlib import Path

tools = Path(__file__).resolve().parent
convert_log = tools / "convert_old_years.log"
YEARS = ("2023", "2024")


def run(title, args, env_year=None, answers=None):
    t0 = time.time()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if env_year:
        env["ZBC_SETTINGS"] = f"settings_{env_year}.ini"
    print(f"\n===== {title} =====", flush=True)
    rc = subprocess.run([sys.executable, "-u", "-W", "ignore", *args], cwd=tools, env=env,
                        input=answers, text=True if answers is not None else None,
                        stdin=None if answers is not None else subprocess.DEVNULL).returncode
    print(f"===== {title}: exit {rc}, {(time.time() - t0) / 60:.1f} min =====", flush=True)
    return rc


while not (convert_log.is_file() and "ALL DONE" in convert_log.read_text(encoding="utf-8", errors="ignore")[-200:]):
    print(f"{time.strftime('%H:%M')}  waiting for the conversion to finish ({convert_log.name})", flush=True)
    time.sleep(600)

for year in YEARS:
    if not Path(rf"X:\nginx_share\exm\{year}\zarr").is_dir():
        print(f"\n{year}: no zarr folder, skipped", flush=True)
        continue
    # prompts: which samples (Enter = all); standard screenshots (Enter = yes); rating (Enter = no);
    # and, when some already have pictures, add only the missing ones (Enter = A)
    run(f"{year}: screenshots", ["roi_log.py", "--batch"], year, answers="\n\n\n\n\n")
    run(f"{year}: synapses", ["synapse_exf.py", "--synaptic", "auto"], year)
run("gallery (2026 + older years), PC copy and server mirror", ["make_gallery.py", "--mirror"])
print("\nALL DONE. Now run Make_Gallery_Protected.bat to rebuild the password-protected page with the old years.", flush=True)
