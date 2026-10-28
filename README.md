# pan-ExM Gallery

Browse, review and quantify pan-expansion microscopy (pan-ExM) brain volumes: convert microscope files to OME-Zarr on a Neuroglancer server, keep an image list with one-click viewer links, build a gallery of every sample, find synapses in the pan-stain and measure the expansion factor, synapse structure and antibody labelling, review the synapses from a phone, and run 3D analysis on terabyte light-sheet volumes.

Built in the Kuan and Bewersdorf Labs at Yale for the Shank2 project. The method follows M'Saad, Cairns et al., *Nature Biotechnology* 2025 ([doi:10.1038/s41587-025-02905-4](https://doi.org/10.1038/s41587-025-02905-4)); what was taken from the paper and what was written here is set out in [legacy/ExF_Quantification/METHODS_AND_CREDITS.md](legacy/ExF_Quantification/METHODS_AND_CREDITS.md).

> Repository name: this repository was created as **ExpandedBrainQuant** and is being renamed **pan-ExM Gallery**; GitHub redirects the old address.

## Two layers

| Layer | Where | For whom |
|---|---|---|
| **Lab tools** (Windows, double-click `.bat`) | [legacy/ZarrBatchConverter/](legacy/ZarrBatchConverter/) | Lab members on the analysis PC: conversion, image list, screenshots, gallery, 2D synapse detection and expansion factor, antibody labelling, the Synapse Review page. Start with [QUICKSTART.txt](legacy/ZarrBatchConverter/QUICKSTART.txt). |
| **`ebq` package** (Python, any OS, the HPC) | [ebq/](ebq/) | New analysis: 3D synapse finder, nucleus finder, profile measurement, dataset manifest, readers for precomputed and OME-Zarr. See [docs/ebq_package.md](docs/ebq_package.md). |

The `.bat` files are launchers only; the logic lives in the Python scripts beside them. New analysis goes into the `ebq` package, and the lab tools are being folded into it one command at a time (see CHANGELOG.md for what has moved).

## What the gallery shows

One card per sample with its QC screenshots, channel colours, expansion factor, resolution and antibody-labelling numbers, synapse contact sheets, Neuroglancer links, and a "Review synapses" button that opens the phone-friendly review page. A password-locked, self-contained copy can be shared outside the lab.

## Quick start

Lab PC (Windows): install [NGFF-Converter](https://github.com/glencoesoftware/NGFF-Converter/releases/latest) and Python 3.10+, connect the lab server drive, then double-click `First_Time_Setup.bat` in `legacy/ZarrBatchConverter/` and follow `QUICKSTART.txt`.

Anywhere (Python):

```
pip install -e .
ebq manifest-template datasets.csv        # one row per dataset: path, animal, genotype, sex, channel roles
ebq manifest-check    datasets.csv
ebq synapses3d <precomputed folder> --out out/syn --fields 20
ebq nuclei     <precomputed folder> --out out/nuclei --exf 20
python -m pytest                          # synthetic-data tests
python scripts/golden_check.py            # 2D detector against the frozen references (needs the lab data)
```

Pinned versions are in `requirements.txt`.

## Provenance

Every Synapse Log carries a "Produced by" line in its Method sheet and every gallery page a footer stamp: tool, version, an 8-character hash of the code that ran, the run time and the host. The hash can be matched to this repository's history. `tests/golden/` holds frozen outputs for two samples so that refactoring can be checked with `scripts/golden_check.py`.

## Layout

| Folder | What |
|---|---|
| [ebq/](ebq/) | The package: `synapse3d`, `nuclei`, `profile`, `manifest`, `microscopes`, `io`. |
| [legacy/ZarrBatchConverter/](legacy/ZarrBatchConverter/) | The lab tools, snapshotted from the analysis PC after each change. |
| [legacy/ExF_Quantification/](legacy/ExF_Quantification/) | The expansion-factor method write-up and credits. |
| [legacy/NGLinkGenerator/](legacy/NGLinkGenerator/) | The earlier standalone Neuroglancer-link tool. |
| [scripts/](scripts/) | One-off analyses: `power_estimate.py`, `golden_check.py`, `redo_figures.py`. |
| [tests/](tests/) | Synthetic-data tests for `ebq`; `golden/` references for the 2D detector. |
| [docs/](docs/) | Package overview and the working notes behind the analysis decisions. |

Not in this repository: raw images and zarrs, generated screenshots, logs, figures and galleries (on the lab server), and the pan-ASLM stitching pipeline ([pan-aslm2neuroglancer](https://github.com/allison714/pan-aslm2neuroglancer)).

## Status (October 2026)

- 2D detector: 6,044 synapses logged across 48 samples at 150 per sample; human review under way; not yet validated against hand measurements.
- `ebq.synapse3d`: recovers 9 of 12 synapses logged by the 2D tool on a pan-ASLM volume; not yet validated against hand measurements.
- The expansion factor of the light-sheet volumes must still be measured on the gel; the DP-PSD method reports only values inside its 12-30x search window.

## Citation and license

See `CITATION.cff`. MIT license.
