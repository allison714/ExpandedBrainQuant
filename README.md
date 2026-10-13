# ExpandedBrainQuant

Quantification of pan-expansion-microscopy (pan-ExM) brain volumes: synapses and the expansion factor from the DP–PSD distance, nuclei as an independent ExF ruler, multi-antibody organisation at synapses, and QC for light-sheet (pan-ASLM) data. Python, works from confocal stacks (OME-Zarr) to terabyte Neuroglancer-precomputed volumes.

The method follows M'Saad, Cairns *et al.*, *Nature Biotechnology* 2025 ([doi:10.1038/s41587-025-02905-4](https://doi.org/10.1038/s41587-025-02905-4)); what was taken from the paper and what was written here is set out in [legacy/ExF_Quantification/METHODS_AND_CREDITS.md](legacy/ExF_Quantification/METHODS_AND_CREDITS.md).

## Layout

| Folder | What |
|---|---|
| [ebq/](ebq/) | **The package.** 3D synapse finder, nucleus finder, the profile measurement, a dataset manifest (channel roles, animal, genotype, sex), microscope profiles, readers for precomputed and OME-Zarr. See [docs/ebq_package.md](docs/ebq_package.md). |
| [tests/](tests/) | Synthetic-data tests (`python -m pytest`). |
| [docs/](docs/) | Working notes on the server layout, Neuroglancer/OME-Zarr gotchas and analysis decisions ([docs/README.md](docs/README.md)); the package overview. |
| [legacy/](legacy/) | The Windows tools used in 2026: OME-Zarr batch converter, image list with Neuroglancer links, QC screenshots, gallery, and the original 2D synapse/ExF analysis. Still runnable; superseded by `ebq` for new analysis. See [legacy/README.md](legacy/README.md). |

## Quick start

```
pip install -e .
ebq manifest-template datasets.csv        # one row per dataset: path, animal, genotype, sex, channel roles
ebq manifest-check    datasets.csv
ebq synapses3d <precomputed folder> --out out/syn --fields 20
ebq nuclei     <precomputed folder> --out out/nuclei --exf 20
```

Requires Python 3.10+, numpy ≥ 2, scipy, scikit-image, zarr ≥ 3, dask, matplotlib (installed by `pip install -e .`).

## Status (October 2026)

- `ebq.synapse3d`: on a pan-ASLM volume it recovers 9 of 12 synapses logged by the 2D tool and gives ExF 23.7 ± 1.6 across fields where the 2D tool gave 21.7 ± 1.6. Not yet validated against hand measurements.
- `ebq.nuclei`: finds complete nuclei in a whole volume; recall on light-sheet data is still low (large nuclei merge with neighbours).
- The expansion factor of the pan-ASLM volumes must be measured on the gel; the DP–PSD method only reports values inside its 12–30× search window.
- Mitochondria: use [MAPS](https://github.com/MAPS-ExM/MAPS) (Tian *et al.* 2025) on the 60× confocal pan-stain stacks; the pretrained kidney model works on brain tissue out of the box.

## Related

[pan-aslm2neuroglancer](https://github.com/allison714/pan-aslm2neuroglancer) — pan-ASLM tiles → stitched Neuroglancer precomputed on the HPC. [kuan-lab/pan-exm-t_paper](https://github.com/kuan-lab/pan-exm-t_paper) — the paper's conversion code.
