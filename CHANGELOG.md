# Changelog

Dates are when the change landed on the lab's analysis PC; the repository snapshots them.

## 0.1.0 - 2026-10-28

First tagged release. Repository to be renamed pan-ExM Gallery (was ExpandedBrainQuant).

- Provenance: every Synapse Log carries a "Produced by" line in its Method sheet (tool, version, code hash, run time, host) and the gallery footer carries the same stamp (`provenance.py`).
- Golden regression: two per-sample CSVs and the full log of 2026-10-28 frozen in `tests/golden/`; `scripts/golden_check.py` re-runs the detector and compares.
- 2D synapse detector logs 150 synapses per sample (was 60); `--top` option.
- Synapse cards: the measured lane (2 px thick, DP top to PSD top) is drawn on the overlay in red and to scale, with the two tops ticked and labelled; the profile plot's measured curve is the same red (was a thin dotted orange axis). Numbers unchanged (golden check passed).
- Synapse Review page: phone-friendly accept/reject of every logged synapse, hosted beside the gallery, locked with the gallery password, decisions exported as CSV and imported with `review_import.py`; the gallery links to it per sample and remembers the password on the device.
- Power estimate from between-animal variance (`scripts/power_estimate.py`).
- Gallery: sorted by antibody density by default; single-plane images (tiles, flat montages) hidden unless the 'single-plane images' box is ticked, then listed after the z-stacks; hover text explains every badge, tab and metric (ExF formula included); 'updated <date time>' in the top bar with the full provenance stamp on hover.
- Synapse Review: the unsure mark is 'u' (was '?'); earlier '?' decisions are read as 'u'.
- Image list: Imaris files with no objective in their metadata take it from `objectives.csv` beside the settings file (image, objective), typed by the user; nothing is guessed from names.
- Synapse Review: a Show selector to revisit decisions (all, undecided, unsure, rejected, accepted), remembered with the other preferences.
- Gallery: a Setup selector (microscope and objective) with a metrics strip for the shown samples: samples, volumes (z step 2 um or less), pixel size, z steps, median ExF and resolution, synapses logged and reviewed; the detail table gets a Sampling line. For picking a homogeneous set for 3D work.
- Synapse Review: datasets ordered newest first (acquisition date from the image list), dates shown in the sample list.
- Server copy of the gallery: pictures, "Open folder" and "Synapse images" now link to the full-size files in the server mirror (and to each older year's own server folder); the notice says only that the pictures inside the page are reduced. The lock page reloads itself when the browser restores it from cache after Back.
- Older years: the 2023 and 2024 stitched z-stacks (z step 2 um or less; chosen from the Imaris/Nikon headers, `old_sources_inventory.csv`) are converted straight from the originals into `X:
ginx_share\exm\<year>\zarr` (`convert_old_years.py`), then screenshotted and measured with that year's settings (`analyse_old_years.py`; `ZBC_SETTINGS=settings_<year>.ini` picks the settings file). `[analysis] single_channel_pan = yes` lets a one-channel pan-only image be measured. The gallery gains a Year selector that opens on 2026, with 'Before 2026' and 'All years' (newest first); older years are read from their server folders. Fixes: a zarr folder without a channel-registration record no longer breaks the screenshots; an old image with no date in its metadata is dated by its source file, found again from the conversion log when the list is rebuilt.
- Pinned `requirements.txt`; `CITATION.cff`; `.gitattributes` for line endings.

## 2026-10-13

- `ebq` package: 3D synapse finder, nucleus finder, profile measurement, dataset manifest, microscope profiles, precomputed and OME-Zarr readers; synthetic-data tests.
- Windows tools of 2026 moved under `legacy/`.

## 2026-10-06 to 2026-10-09

- OME-Zarr batch converter, image list with Neuroglancer links, QC screenshots, gallery.
- Expansion factor from the DP-PSD distance (peak to peak on a 2 px line, divided by 81.9 nm, mean and s.d. across fields of view), synapse classes, structure measurements.
- Antibody labelling: single-, double- and multi-label efficiency, bleed-through, set comparisons; WT versus KO morphology at animal level.
