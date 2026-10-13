# data

Small intermediate results that exist only on the author's PC (not on the lab server), backed up here on 8 October 2026.

| Path | What it is | Written by |
|---|---|---|
| `measurement_caches/SZ vs AC/_measured/*.json` | Per-sample antibody-channel measurements (puncta density, signal, per-field values) for the SZ-vs-AC comparison. | `compare_ab.py SZ AC` |
| `measurement_caches/WT vs KO/_measured/*.json` | The same for the Shank2 WT-vs-KO comparison (260831 animals). | `compare_ab.py WT KO` |
| `aslm_training/260618_pan_aslm_4x4x9520/plan.json` | Which fields (tile × depth) of the stitched pan-ASLM volume were chosen for the training set, and why. | `aslm_training.py plan` |
| `Zarr Image List - Notes.xlsx` | The hand-typed notes workbook that sits beside the server's read-only image list. | `convert_to_zarr.py` adds rows; the columns are typed by hand |

The caches let `compare_ab.py` and `antibody_report.py` be re-run without re-reading the zarrs. Copy a `_measured` folder into `<Zarr Comparisons>\<A vs B>\` to reuse it.

Not included: the 1,400-file, 1.3 GB candidate cache (`*.pkl`) from `aslm_training.py collect`. It is regenerable from the stitched volume on the server.
