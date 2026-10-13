# Methods and credits

This file says how the expansion factor is measured here, what was taken from other groups, and what was added.

## Credits

### The method: M'Saad, Cairns et al., 2025

> Ons M'Saad\*, Allison Cairns\*, Jonathan Gulcicek, Ravi Kiran Kasula, Jacob Liao, Ilona Kondratiuk, Emma H. Bewersdorf, Phylicia Kidd, Hanieh Falahati, Juliana E. Gentile, Robert F. Niescier, Katherine Watters, Robert C. Sterner, Seong-il Lee, Xiaojia Guo, Xinran Liu, Gary Desir, Pietro De Camilli, James E. Rothman, Anthony J. Koleske, Thomas Biederer, Aaron T. Kuan & Joerg Bewersdorf. All-optical visualization of specific molecules in the ultrastructural context of brain tissue. *Nature Biotechnology* (2025). https://doi.org/10.1038/s41587-025-02905-4
>
> \* co-first authors

Taken from this paper:

| What | Where in the paper |
|---|---|
| Using the DP–PSD distance as the ruler for the linear expansion factor | Results: "In all subsequent experiments, we used the DP–PSD distance as a metric for linear expansion factor calculation" |
| The reference value. 82 ± 26 nm in the main text; 81.9 nm in the Supplementary Methods, which is the value used here | Results; Supplementary Methods, "Neuron expansion factor calculation" |
| Reading the distance peak to peak from a line profile 2 pixels thick | Supplementary Methods, same section |
| Reporting the expansion factor as the mean ± s.d. across fields of view | Supplementary Methods, same section |
| Measuring resolution from Gaussian fits to small antibody features | Extended Data Fig. 5 |
| How that value was calibrated: against the expansion of cell nuclei in cultured neurons (15.7 ± 0.3×) | Same sections |
| Recognising a synapse in the pan stain by its PSD and its row of dense projections | Fig. 1 and Fig. 2 |
| Light Gaussian smoothing before measuring (the paper used 0.4–1.5 pixels; this code uses 1.0) | Methods, "Image processing" |
| Checking synaptic antibodies against the pan-stain ultrastructure | Fig. 1 and Fig. 3 |

The pan-ExM-t sample preparation itself is also theirs. Pan-ExM was introduced by M'Saad & Bewersdorf, *Nature Communications* 2020.

### Reviewed for ideas; no code or models used

| Source | What it is | What it contributed here |
|---|---|---|
| [JaneliaSciComp/exllsm-synapse-detector](https://github.com/JaneliaSciComp/exllsm-synapse-detector) | 3D U-Net synapse detection for expansion light-sheet data, with size filtering and pre/post-synaptic overlap rules in its parent pipeline | Two ideas: filter candidates by size, and count a synapse as confirmed when a synaptic marker overlaps it. Its U-Net was not used; it expects near-isotropic volumes, and these stacks step about 10 µm in z. |
| [kuan-lab/magneton](https://github.com/kuan-lab/magneton) | The lab's segmentation pipeline for EM and pan-ExM volumes (PyTorch Connectomics affinities, watershed for synapses) | Nothing used. It is the natural next step if synapse detection from the pan stain alone needs a trained model. |
| [computational-cell-analytics/synapse-net](https://github.com/computational-cell-analytics/synapse-net) | Deep-learning segmentation of vesicles, active zones and mitochondria in electron microscopy | Nothing used. It is for EM. |

### Software this runs on

- **scikit-image**: Hessian ridge filter, connected components and their shape measurements.
- **SciPy**: least-squares curve fitting, image interpolation, Gaussian filtering, peak finding.
- **NumPy**, **Dask**, **zarr-python**: arrays, and reading the images in pieces.
- **Matplotlib**, **openpyxl**: pictures and the Excel log.
- **Neuroglancer** (Google): the viewer the links open.
- **bioformats2raw / NGFF-Converter** (Glencoe Software) and **Bio-Formats** (Open Microscopy Environment): conversion of the microscope files to OME-Zarr.
- **FIJI / ImageJ**: not used by the code, but its Plot Profile tool is what the paper measured with, and what the "peak-to-peak" option imitates.

### What to cite in a paper

No code was copied from any GitHub repository, so there is no licence notice to carry. The three repositories above were read for ideas only. A methods section should cite the paper for the method and the libraries the analysis runs on:

- M'Saad, Cairns et al., *Nat. Biotechnol.* 2025 (the DP–PSD method, the 81.9 nm reference, the synapse classes).
- van der Walt et al., scikit-image: image processing in Python, *PeerJ* 2:e453 (2014).
- Virtanen et al., SciPy 1.0, *Nat. Methods* 17, 261–272 (2020).
- Harris et al., Array programming with NumPy, *Nature* 585, 357–362 (2020).
- Hunter, Matplotlib: a 2D graphics environment, *Comput. Sci. Eng.* 9, 90–95 (2007).
- Linkert et al., Metadata matters: access to image data in the real world (Bio-Formats), *J. Cell Biol.* 189, 777–782 (2010).
- Gray, Axo-somatic and axo-dendritic synapses of the cerebral cortex, *J. Anat.* 93, 420–433 (1959), for the type I / type II classes.
- Dask, Zarr, Neuroglancer and bioformats2raw have no journal paper; cite their project pages and the version used.

If the Janelia detector's ideas are mentioned, cite its repository. Check each reference against the journal before submission.

### Written here

The detection, measurement, log and comparison code was written in October 2026 by Claude (Anthropic's AI model, through Claude Code), directed by a member of the Kuan Lab, on top of the lab's Zarr Batch Converter. The images are the lab's own.

New in this folder, and not from any of the sources above:

- Finding synapses automatically instead of drawing each line by hand.
- The brightness rule that separates a PSD from a pair of membranes, set from GluA2-positive synapses.
- Ranking antibody-positive synapses first.
- The accept/reject log.
- The comparison of nine ways to read the distance, scored by repeatability.
- The antibody-labelling numbers measured at the synapses (share of synapses labelled, share of puncta on synapses, enrichment over chance).

None of this has been validated against hand measurements yet.

## How the measurement is done

Sizes in nanometres are before expansion. They are converted to pixels at a nominal 20×, which sets filter sizes only.

1. **Planes.** Full resolution, one plane at a time. Planes outside the gel are left out. Planes closer than 4 µm are skipped so no synapse is counted twice.
2. **Smoothing.** Both channels, Gaussian, 1.0 pixel.
3. **Candidate bars.** Ridge filter on the pan channel (most negative Hessian eigenvalue, sigma 15 nm). Kept: the strongest 3% of pixels that are also brighter than 80% of the plane, forming bars 120–900 nm long.
4. **Profile.** Across each bar, 260 nm either side, averaged along the bar over up to ±90 nm.
5. **Second peak.** Looked for at 81.9 nm × 12 to 30. No synapse can therefore read outside 12–30×.
6. **Is it a synapse?** Two Gaussians on a flat baseline are fitted to the averaged profile. It is kept when the fit R² is at least 0.90, the dip between the peaks is at least 20% of the smaller peak, and the peak heights are within a factor of 4.
7. **Distance, as in the paper.** A line 2 pixels thick is taken across the middle of the bar. The top of each peak is its brightest sample, placed between samples by a parabola through it and its two neighbours. Top-to-top distance ÷ 81.9 nm is that synapse's ExF. The distance between the fitted Gaussian centres ÷ 81.9 nm is kept beside it as "ExF (Gaussian fit)".
8. **PSD brightness.** The brighter peak must be brighter than 99.5% of the plane.
9. **Antibody-positive.** The brightest smoothed antibody pixel on the synapse is at least 6 noise-widths (1.4826 × MAD) above the plane's median.
10. **Logged.** The 60 best per sample: antibody-positive first by fit quality, then the rest by brightness.
11. **The sample's number.** Mean ± s.d. across fields of view, each field's synapses averaged first. A field of view is one plane of a stack or one tile of a montage (the tile grid is estimated), and needs 3 synapses to count. The synapses used are the accepted ones if at least 5 were accepted; otherwise the antibody-positive ones if the antibody is synaptic and there are at least 10; otherwise all double peaks, which read high and are marked as not reliable.

The brightness rule in step 8 was set from the data. Without it most double peaks were membrane pairs, piling up at 26–30×. In the GluA2 3x3 montage, 95% of antibody-positive synapses ranked above 99.6% in brightness; the other double peaks had a median rank of 97.8%.

## Where this differs from the paper

| | Paper (Supplementary Methods) | This code |
|---|---|---|
| Choosing synapses | By eye | Automatic, then accept/reject by a person |
| Placing the line | Drawn by hand in FIJI | Placed automatically across the middle of the bar |
| Line thickness | 2 pixels | 2 pixels |
| Reading the distance | Peak to peak | Peak to peak, refined between samples with a parabola |
| Smoothing before reading | Not stated for measurements | 1-pixel Gaussian |
| Reference | 81.9 nm | 81.9 nm |
| Reported as | Mean ± s.d. across fields of view | Mean ± s.d. across fields of view, where at least two fields have 3 synapses each |

The first version of this code read the distance between the centres of two fitted Gaussians and divided by 82 nm. It was changed on 7 October 2026 after the Supplementary Methods were read. The fitted value is still recorded.

## Why peak to peak and not the fit

The same 88 GluA2-positive synapses were measured nine ways (`Fit options (GluA2).png` in the output folder). Each synapse's bar was cut in two along its length, both halves were measured, and a method was scored by how little its two answers differ. That comparison used the wide averaged profile, not the 2-pixel line.

| Rank | Method | Repeatability (% of distance) | ExF, mean ± s.d. (median) |
|---|---|---|---|
| 1 | Brightest sample + parabola (sub-pixel) | 7.7 | 16.6 ± 2.7 (16.0) |
| 2 | Three Gaussians (two peaks + broad haze) | 7.7 | 17.7 ± 2.6 (17.1) |
| 3 | Two Lorentzians, flat baseline | 8.2 | 18.6 ± 3.0 (17.8) |
| 4 | Brightest sample of each peak | 8.7 | 16.6 ± 2.7 (15.6) |
| 5 | Two Gaussians, flat baseline | 8.9 | 18.2 ± 3.1 (17.5) |
| 6 | Two Gaussians, sloped baseline | 9.2 | 17.9 ± 2.9 (17.2) |
| 7 | One Gaussian per peak, fitted separately | 9.3 | 16.9 ± 2.7 (16.4) |
| 8 | Centroid of each peak above half height | 9.8 | 17.1 ± 2.8 (16.5) |
| 9 | Two Gaussians, one shared width | 10.5 | 18.2 ± 3.3 (17.3) |

- Repeatability hardly separates the methods.
- The methods disagree systematically: reading each peak on its own gives about 16.6×, fitting both together gives about 18×.
- The 81.9 nm reference was measured peak to peak, so dividing a peak-to-peak distance by it is like for like. That decided it.

## Antibody labelling at the synapses (provisional)

Measured in the same run, for the first antibody channel of each sample.

| Number | What it is | How to read it |
|---|---|---|
| AB Labelled Synapses (%) | Share of the synapses found that are antibody-positive. The synapses are found from the pan stain without looking at the antibody. | Sensitivity. A lower bound: membrane pairs mistaken for synapses sit in the denominator. |
| AB Signal at Synapses (σ) | Median antibody signal at the positive synapses, in noise-widths. | How far above background a labelled synapse is. |
| AB Puncta on Synapses (%) | Share of all antibody puncta within two nominal DP–PSD distances of a synapse found. | Far below the true on-target share, because only side-on synapses are found. |
| AB Enrichment (× chance) | That share ÷ the share of the field those neighbourhoods cover. | Specificity. 1 means the puncta fall at random with respect to synapses. This is the number to compare between antibodies. |

An antibody punctum is 3 or more connected pixels of the smoothed antibody channel at least 6 noise-widths above the plane's median.

These definitions are a first proposal and have not been checked against a known good and a known bad antibody.

## Resolution

Width (FWHM) of isolated antibody puncta, from a round 2D Gaussian fitted to the unsmoothed image. The paper did this on hand-picked sub-diffraction features (290 ± 60 nm as imaged, same 40× lens). Here the puncta are picked automatically and most are receptor clusters wider than the optics, so the 10th percentile of the widths is reported: the smallest puncta. It is an upper bound, lateral only, and depends on the antibody. Corrected resolution = as imaged ÷ ExF.

## Caveats

- 81.9 nm is a mean with a spread of ± 26 nm between synapses. One synapse says little; the mean over many carries the expansion factor.
- Only synapses lying side-on in the image give the true distance. Tilted ones read short. The dip rule favours side-on synapses but cannot guarantee it.
- Which peak is the PSD and which the DPs is not decided. The distance does not depend on it.
- Synapses without antibody are found from the pan stain alone and still include some membrane pairs.
- The brightness rule was set on one antibody (GluA2) in one sample set. Check it on the next antibody before trusting it there.
