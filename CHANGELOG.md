# Changelog

Dates are when the change landed on the lab's analysis PC; the repository snapshots them.

## 0.1.0 - 2026-10-28

First tagged release. Repository to be renamed pan-ExM Gallery (was ExpandedBrainQuant).

- Provenance: every Synapse Log carries a "Produced by" line in its Method sheet (tool, version, code hash, run time, host) and the gallery footer carries the same stamp (`provenance.py`).
- Golden regression: two per-sample CSVs and the full log of 2026-10-28 frozen in `tests/golden/`; `scripts/golden_check.py` re-runs the detector and compares.
- 2D synapse detector logs 150 synapses per sample (was 60); `--top` option.
- Synapse Review page: phone-friendly accept/reject of every logged synapse, hosted beside the gallery, locked with the gallery password, decisions exported as CSV and imported with `review_import.py`; the gallery links to it per sample and remembers the password on the device.
- Power estimate from between-animal variance (`scripts/power_estimate.py`).
- Pinned `requirements.txt`; `CITATION.cff`; `.gitattributes` for line endings.

## 2026-10-13

- `ebq` package: 3D synapse finder, nucleus finder, profile measurement, dataset manifest, microscope profiles, precomputed and OME-Zarr readers; synthetic-data tests.
- Windows tools of 2026 moved under `legacy/`.

## 2026-10-06 to 2026-10-09

- OME-Zarr batch converter, image list with Neuroglancer links, QC screenshots, gallery.
- Expansion factor from the DP-PSD distance (peak to peak on a 2 px line, divided by 81.9 nm, mean and s.d. across fields of view), synapse classes, structure measurements.
- Antibody labelling: single-, double- and multi-label efficiency, bleed-through, set comparisons; WT versus KO morphology at animal level.
