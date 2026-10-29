"""Write decisions from the Synapse Review page into 'Synapse Log.xlsx' and the dataset manifest.

Input: the CSV exported by the page. Columns: key, sample, synapse_id, n, accept, notes, at, and since
2026-10-28 also cls (the reviewer's class call), mouse and genotype (on rows whose key ends in
'__SAMPLE', which carry the per-dataset notes).

  * Synapse rows: Accept (Y / N; 'u' or the older '?' = unsure, clears it), Notes and 'Reviewed Class' are written into the log,
    matched by key '<sample>__<synapse id>'. The 'Reviewed Class' column is created after Notes if the
    log does not have it yet; synapse_exf.py preserves it across re-runs like Accept and Notes.
  * Sample rows: mouse, genotype and notes go into 'datasets.csv' beside the log, the per-dataset
    manifest that ebq reads (columns dataset, animal, genotype, notes, plus the rest of the template
    left for later). Existing rows are updated by dataset name.

Afterwards run Measure_ExF.bat to recompute the accepted-only expansion factors, then rebuild the
review page so the decisions are baked into it.

Usage:  python review_import.py decisions.csv [--dry-run]
"""
import argparse
import configparser
import csv
import sys
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

HERE = Path(__file__).resolve().parent
ini = configparser.ConfigParser()
ini.read(HERE / "settings.ini", encoding="utf-8")
EXF = Path(ini.get("paths", "screenshot_folder")).parent / "Zarr Synapse ExF"
LOG = EXF / "Synapse Log.xlsx"
MANIFEST = EXF / "datasets.csv"
HEADER_ROW = 3
REVIEWED = "Reviewed Class"
LINE, MANUAL_RESULTS = "Manual Line", ("P2P Manual (µm)", "ExF (manual line)")   # the page's line tool; results from remeasure_lines.py
MANIFEST_COLS = ["dataset", "path", "animal", "genotype", "sex", "age", "region", "exf", "notes"]


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_manifest(samples, dry):
    rows = {}
    if MANIFEST.is_file():
        for r in read_csv(MANIFEST):
            rows[r["dataset"]] = r
    changed = 0
    for s, m in samples.items():
        r = rows.setdefault(s, {c: "" for c in MANIFEST_COLS}); r["dataset"] = s
        new = {"animal": m.get("mouse", ""), "genotype": m.get("genotype", ""), "notes": m.get("notes", "")}
        for k, v in new.items():
            if v and r.get(k, "") != v:
                r[k] = v; changed += 1
    if changed and not dry:
        cols = list(dict.fromkeys(MANIFEST_COLS + [c for r in rows.values() for c in r]))
        with open(MANIFEST, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
            for s in sorted(rows):
                w.writerow({c: rows[s].get(c, "") for c in cols})
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    decisions, samples = {}, {}
    for r in read_csv(a.csv):
        key = r["key"]
        if key.endswith("__SAMPLE"):
            samples[key[:-8]] = {"mouse": (r.get("mouse") or "").strip(), "genotype": (r.get("genotype") or "").strip(), "notes": (r.get("notes") or "").strip()}
            continue
        acc = (r.get("accept") or "").strip().upper()
        decisions[key] = (acc if acc in ("Y", "N") else None, (r.get("notes") or "").strip() or None, (r.get("cls") or "").strip() or None,
                          (r.get("line") or "").strip() or None)
    print(f"{len(decisions)} synapse decisions and {len(samples)} dataset notes in {a.csv}")

    wb = load_workbook(LOG)
    ws = wb["Synapses"]
    cols = {c.value: c.column for c in ws[HEADER_ROW] if c.value}
    if REVIEWED not in cols:
        at = cols["Notes"] + 1
        ws.insert_cols(at)
        h = ws.cell(HEADER_ROW, at, REVIEWED); h.font = Font(bold=True)
        ws.column_dimensions[h.column_letter].width = 18
        cols = {c.value: c.column for c in ws[HEADER_ROW] if c.value}
        print(f"added the '{REVIEWED}' column after Notes")
    for name in (LINE,) + MANUAL_RESULTS:      # the measurement-line columns, after Reviewed Class, in this order
        if name not in cols:
            at = max(cols[REVIEWED], *(cols[n] for n in (LINE,) + MANUAL_RESULTS if n in cols)) + 1
            ws.insert_cols(at)
            h = ws.cell(HEADER_ROW, at, name); h.font = Font(bold=True)
            ws.column_dimensions[h.column_letter].width = 24 if name == LINE else 12
            cols = {c.value: c.column for c in ws[HEADER_ROW] if c.value}
            print(f"added the '{name}' column")
    mine = PatternFill("solid", fgColor="FFF2CC")
    hit = changed = 0
    for row in ws.iter_rows(min_row=HEADER_ROW + 1):
        sample = row[cols["Sample"] - 1].value
        if sample is None:
            continue
        key = f"{sample}__{row[cols['Synapse ID'] - 1].value}"
        if key not in decisions:
            continue
        hit += 1
        acc, notes, cls, line = decisions[key]
        targets = [(row[cols["Accept"] - 1], acc, True), (row[cols["Notes"] - 1], notes, bool(notes)), (row[cols[REVIEWED] - 1], cls, bool(cls))]
        if line == "clear":      # the reviewer removed her line: drop it and its results
            targets += [(row[cols[n] - 1], None, True) for n in (LINE,) + MANUAL_RESULTS]
        elif line:
            old = row[cols[LINE] - 1].value
            targets.append((row[cols[LINE] - 1], line, True))
            if old != line:      # a new line: its old results no longer apply until remeasure_lines.py runs
                targets += [(row[cols[n] - 1], None, True) for n in MANUAL_RESULTS]
        for cell, value, write in targets:
            if write and cell.value != value:
                changed += 1
                if not a.dry_run:
                    cell.value = value; cell.fill = mine
    mchanged = write_manifest(samples, a.dry_run)
    print(f"matched {hit} synapse rows, {changed} cells changed; {mchanged} manifest fields changed" + (" (dry run, nothing written)" if a.dry_run else ""))
    if not a.dry_run and changed:
        wb.save(LOG)
        print(f"saved {LOG}")
    if not a.dry_run and mchanged:
        print(f"saved {MANIFEST}")
    if not a.dry_run:
        print("Now run Remeasure_Lines.bat if you placed lines, then Measure_ExF.bat (accepted-only ExF) and Build_Synapse_Review.bat.")


if __name__ == "__main__":
    sys.exit(main())
