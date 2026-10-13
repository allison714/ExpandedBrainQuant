# pan-exm-tools

Windows tools for pan-ExM (pan-expansion microscopy) image data in the Kuan Lab: convert microscope files to OME-Zarr on the lab's Neuroglancer server, keep an Excel image list with one-click viewer links, export QC screenshots and a browsable gallery, and measure the expansion factor and antibody labelling from the synapses in the pan stain.

Everything is Python 3.10+ driven by double-clickable `.bat` files. The `.bat` files install the Python packages they need on first run (see [requirements.txt](requirements.txt)).

## What is in this repo

| Folder | What it is | Start here |
|---|---|---|
| [ZarrBatchConverter/](ZarrBatchConverter/) | The main toolbox. Conversion, image list, Neuroglancer links, screenshots, gallery, and every analysis script. All the other folders call into it. | [QUICKSTART.txt](ZarrBatchConverter/QUICKSTART.txt) |
| [ExF_Quantification/](ExF_Quantification/) | Expansion-factor measurement from synapses (DP–PSD distance ÷ 81.9 nm). A launcher plus the write-up of the method, what was taken from the paper, and what to cite. | [README.md](ExF_Quantification/README.md), [METHODS_AND_CREDITS.md](ExF_Quantification/METHODS_AND_CREDITS.md) |
| [NGLinkGenerator/](NGLinkGenerator/) | The earlier, standalone tool: walk a folder of OME-Zarr datasets served over HTTP and write an Excel sheet of Neuroglancer URLs. Superseded by the converter's image list but still works on its own. | [README.md](NGLinkGenerator/README.md) |
| [scripts/](scripts/) | One-off drivers. `redo_figures.py` re-runs the whole figure pipeline (ExF → pan-ASLM synapses → training export → gallery). | |
| [docs/notes/](docs/notes/) | Working notes on the server layout, the OME-Zarr/Neuroglancer gotchas, and the analysis decisions, written during development. The place to look for *why* something is the way it is. | [docs/README.md](docs/README.md) |
| [data/](data/) | Small result caches that are not on the lab server: per-sample measurement JSONs from the SZ-vs-AC and WT-vs-KO comparisons, the pan-ASLM sampling plan, and the image-list notes workbook. | |

Not in this repo: the raw images and zarrs (on the lab server, `X:\nginx_share\exm\2026\`), the generated screenshots, logs, figures and galleries (mirrored to the server under `exm/2026/gallery/`), and the 1.3 GB pan-ASLM training-candidate cache (`%LOCALAPPDATA%\ZarrBatchConverter\aslm_training\`).

## The tools, in the order you would use them

All of these live in `ZarrBatchConverter/`. Double-click the `.bat`; the `.py` beside it is what runs.

| Step | Double-click | What it does |
|---|---|---|
| 0 | `First_Time_Setup.bat` | Once per PC. Checks NGFF-Converter and Python, asks for your folders, writes `settings.ini`. |
| 1 | `Convert_To_Zarr.bat` | Converts every image in a folder to OME-Zarr on the server (via bioformats2raw bundled with NGFF-Converter) and rewrites the shared image list with metadata and Neuroglancer links. |
| 2 | `Save_NG_View.bat` | Saves a Neuroglancer view from the clipboard as your default link for an image and/or as a logged ROI. |
| 3 | `Log_Screenshots.bat` | Draws standard QC pictures per sample straight from the zarr data (projections, depth planes, seam crops, close-ups) and keeps the screenshot log. |
| 4 | `Measure_ExF.bat` | Finds synapses in the pan stain and measures the expansion factor. Writes `Synapse Log.xlsx` with an Accept column for a person to review. |
| 5 | `Measure_AB_Efficiency.bat` | Antibody labelling against the pan stain: PCC, Manders, coverage, SBR, penetration with depth. |
| 6 | `Compare_AB.bat` / `Double_Label.bat` | Compare two sample sets antibody by antibody; test double-labelled samples for bleed-through and labelling efficiency. |
| 7 | `Make_Gallery.bat` | Builds the one-page HTML gallery of every sample (with ExF, resolution and labelling numbers) and can mirror it to the server. |

### Analysis modules without a `.bat`

Run from a command prompt in `ZarrBatchConverter/`: `python <script>.py [filter text]`.

| Script | What it does |
|---|---|
| `synapse_structure.py` | PSD length and thickness, DP–DP spacing, antibody offset, anisotropy test. Used by `synapse_exf.py`. |
| `synapse_fit_options.py` | Ranks nine ways of reading the DP–PSD distance by split-half repeatability. Why peak-to-peak was chosen. |
| `single_label.py` | Labelling efficiency of single-labelled samples at the synapses, with a decoy-based chance level. |
| `compare_morphology.py` | WT vs KO synapse morphology at the animal level, exact permutation test. |
| `ab_metrics.py` | Puncta density, nearest-neighbour, Clark–Evans R, synaptic enrichment, in tissue units. |
| `channel_check.py` | Does each channel hold signal, and does one show up in another? |
| `antibody_channels.py` | Which antibody is on which channel (`_antibody_channels.csv` on the server, then the file name). |
| `channel_registration.py` | Measures and applies the second-camera transform for the 16 June 2026 session from bead images. |
| `nucleus_size.py` | Nuclear volume and projected area in stacks, for an expansion factor from nuclei. |
| `stitch_check.py` | Stitching-quality check (doubling, sharpness, brightness seams) on NRStitcher precomputed volumes. |
| `aslm_synapses.py`, `aslm_training.py` | The same synapse finder on a stitched pan-ASLM precomputed volume; sampling plan and training-set export. |
| `antibody_report.py`, `double_label_report.py` | One-file HTML reports from the comparison CSVs. |

## Setup

1. Install [NGFF-Converter](https://github.com/glencoesoftware/NGFF-Converter/releases/latest) (Glencoe Software) with default options. The converter uses its bundled bioformats2raw and Java, so nothing else needs installing.
2. Have Python 3.10 or newer on `PATH` (Anaconda counts).
3. Connect the lab server drive (`X:`) so `X:\nginx_share` is reachable.
4. Run `ZarrBatchConverter\First_Time_Setup.bat`, or copy `settings.example.ini` to `settings.ini` and edit it.

If you install packages by hand, keep `numpy>=2` and `contourpy>=1.3` pinned in the same `pip install`; an older numpy breaks zarr 3.

## Method and credits

The expansion-factor method and the 81.9 nm DP–PSD reference come from M'Saad, Cairns *et al.*, *Nature Biotechnology* 2025 ([doi:10.1038/s41587-025-02905-4](https://doi.org/10.1038/s41587-025-02905-4)). [ExF_Quantification/METHODS_AND_CREDITS.md](ExF_Quantification/METHODS_AND_CREDITS.md) sets out exactly what was taken from the paper, what was written here, where the two differ, and what to cite. The analysis code was written in October 2026 with Claude (Anthropic), directed by the author.

Related repos: [pan-aslm2neuroglancer](https://github.com/allison714/pan-aslm2neuroglancer) (pan-ASLM tiles → stitched Neuroglancer precomputed on the HPC), [kuan-lab/pan-exm-t_paper](https://github.com/kuan-lab/pan-exm-t_paper) (the paper's precomputed conversion).

## Things to know

- The tools share `settings.ini`, pan-stain detection and channel colours through `convert_to_zarr.py`, so keep all the files in `ZarrBatchConverter/` together. `ExF_Quantification\Measure_ExF.bat` expects `ZarrBatchConverter` as its sibling folder.
- `settings.ini` is per PC and is not committed. `antibody_report.py` and the fallbacks in `convert_to_zarr.py` still carry the author's OneDrive paths; `settings.ini` overrides them.
- Samples with `pff` or `tetraspec` in the name are excluded everywhere; `SZ`, `control` and `number_number` repeats are excluded from ExF. See [docs/notes](docs/notes/).
- The `.py` files use LF line endings and the `.bat` files CRLF; `.gitattributes` keeps it that way.
