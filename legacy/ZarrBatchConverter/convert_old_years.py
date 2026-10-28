"""Convert the pre-2026 stitched z-stacks (z step 2 um or less) to OME-Zarr, one server folder per
acquisition year: X:\nginx_share\exm\<year>\zarr, with that year's 'Zarr Image List.xlsx'.

The source lists (old_sources_<year>.txt) were chosen on 2026-10-28 from the Imaris and Nikon headers
(old_sources_inventory.csv): at least 10 planes, z step <= 2 um, stitched results only (no _F tiles),
one copy per image (the .ims originals on Y:\2024; the OME-TIFF copies on X:\nginx_share\exm are
the same images and are not converted). Reads sources in place; no TIFFs are written.
Launched detached; log: convert_old_years.log beside this file. Safe to re-run: done zarrs are skipped."""
import subprocess
import sys
import time
from pathlib import Path

tools = Path(__file__).resolve().parent
for year in ("2023", "2024"):
    t0 = time.time()
    root = Path(rf"X:\nginx_share\exm\{year}")
    print(f"\n===== {year}: converting into {root / 'zarr'} =====", flush=True)
    rc = subprocess.run([sys.executable, "-u", "-W", "ignore", "convert_to_zarr.py",
                         "--from-list", str(tools / f"old_sources_{year}.txt"),
                         "--out", str(root / "zarr"), "--list", str(root / "Zarr Image List.xlsx")],
                        cwd=tools, stdin=subprocess.DEVNULL).returncode
    print(f"===== {year}: exit {rc}, {(time.time() - t0) / 3600:.1f} h =====", flush=True)
print("\nALL DONE", flush=True)
