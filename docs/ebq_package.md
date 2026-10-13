# ebq — 3D analysis of pan-ExM volumes

The successor to the 2D tools in [ExpandedBrainQuant](https://github.com/allison714/ExpandedBrainQuant): the same DP–PSD expansion-factor measurement (M'Saad, Cairns *et al.*, Nat. Biotechnol. 2025), but in 3D, on any microscope, driven by a dataset manifest instead of file-name guesses.

Status (2026-10-13): working on synthetic data and on the 260618 pan-ASLM volume; not yet validated against hand measurements.

## What it does

| Module | What | How |
|---|---|---|
| `synapse3d` | Finds synapses in a 3D pan-stain block and reads the DP–PSD distance along the true trans-synaptic axis | PSDs are bright plates: Hessian plateness → seeds at local maxima → the plate's extent and normal → a cone of directions is tried and the profile that best fits two peaks is kept → peak-to-peak ÷ 81.9 nm. Per antibody channel: positive or not, and where along the normal it peaks (pre/post alignment). |
| `nuclei` | Finds complete nuclei in a whole volume and reports their sizes | Bright blobs at nucleus scale against the *local* surround (robust to tile seams), grown to a mask, filtered by size, solidity and aspect. ExF from nuclei once unexpanded sizes are entered. |
| `profile` | The measurement itself | Two-Gaussian gate (R², dip, peak ratio), then peak-to-peak on a 2-px line with parabolic refinement. Dimension-agnostic. |
| `manifest` | One CSV row per dataset: animal, genotype, sex, age, region, and which channel is pan / which antibody | Nothing is inferred from lasers or names except a WT default. |
| `microscopes` | Per-instrument profile (resolution, frame, overlap, how to recognise it in metadata) | The algorithms work in physical units; the instrument only supplies numbers. |
| `io.precomputed`, `io.zarr_ome` | Readers for Neuroglancer precomputed (pan-ASLM) and bioformats2raw OME-Zarr (confocal) | 3D blocks, parallel chunk reads, binned whole-level reads. |

## Use

```
pip install -e .
ebq manifest-template datasets.csv      # fill it in
ebq manifest-check    datasets.csv
ebq nuclei     <precomputed folder> --out out/nuclei --exf 21.7
ebq synapses3d <precomputed folder> --out out/syn --fields 20 --field-px 256
python -m pytest                            # synthetic-data tests
```

## Settings that matter

All in `FinderSettings` / `NucleusSettings` / `ProfileSettings`, in physical units where possible:
`psd_sigma_nm` (plate filter scale), `psd_length_nm`, `ridge_percentile` / `bright_percentile` (seed thresholds, taken from the block interior), `psd_rank` (the brighter density must be in the top 0.5 % of the block), `search_cone_deg` (how far from the Hessian normal to look), `exf_search` (12–30×: the second peak is looked for in this window, so no result can fall outside it).

## Known limits

- Pan-only detection: without a synaptic antibody some "synapses" are membrane pairs. Antibody channels are measured when given, not yet used to rank.
- One 256³ field takes ~15 s on one core; the 260618 volume has ~75,000 such fields. Sample fields, or run blocks in parallel on the HPC.
- Not validated against FIJI hand measurements (see the Missing Metadata Checklist).
