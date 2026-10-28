"""Write decisions from the online Synapse Review page into 'Synapse Log.xlsx'.

Input: a CSV with columns  key, accept, notes  (key = '<sample>__<synapse id>', accept = Y / N / ?),
as exported from the review page ("Export CSV") or pulled from its store by KuanClaude.
Only the Accept and Notes columns of matching rows are changed; '?' clears Accept.
Afterwards run Measure_ExF.bat to recompute the accepted-only expansion factors.

Usage:  python review_import.py decisions.csv [--dry-run]
"""
import argparse
import configparser
import csv
import sys
from pathlib import Path

from openpyxl import load_workbook

HERE = Path(__file__).resolve().parent
ini = configparser.ConfigParser()
ini.read(HERE / "settings.ini", encoding="utf-8")
LOG = Path(ini.get("paths", "screenshot_folder")).parent / "Zarr Synapse ExF" / "Synapse Log.xlsx"
HEADER_ROW = 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    decisions = {}
    with open(a.csv, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            acc = (r.get("accept") or "").strip().upper()
            decisions[r["key"]] = (acc if acc in ("Y", "N") else None, (r.get("notes") or "").strip() or None)
    print(f"{len(decisions)} decisions in {a.csv}")
    wb = load_workbook(LOG)
    ws = wb["Synapses"]
    cols = {c.value: c.column for c in ws[HEADER_ROW] if c.value}
    hit = changed = 0
    for row in ws.iter_rows(min_row=HEADER_ROW + 1):
        sample = row[cols["Sample"] - 1].value
        if sample is None:
            continue
        key = f"{sample}__{row[cols['Synapse ID'] - 1].value}"
        if key not in decisions:
            continue
        hit += 1
        acc, notes = decisions[key]
        ca, cn = row[cols["Accept"] - 1], row[cols["Notes"] - 1]
        if ca.value != acc or (notes and cn.value != notes):
            changed += 1
            if not a.dry_run:
                ca.value = acc
                if notes:
                    cn.value = notes
    print(f"matched {hit} rows, {changed} changed" + (" (dry run, nothing written)" if a.dry_run else ""))
    if not a.dry_run and changed:
        wb.save(LOG)
        print(f"saved {LOG}. Now run Measure_ExF.bat to recompute the accepted-only ExF.")


if __name__ == "__main__":
    sys.exit(main())
