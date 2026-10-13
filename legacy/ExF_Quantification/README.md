# ExF Quantification

Measures the linear expansion factor (ExF) of pan-ExM samples from their synapses.

The program finds synapses in the pan stain, measures the distance between the presynaptic dense projections (DPs) and the postsynaptic density (PSD), and divides it by that distance before expansion, 81.9 nm. The distance is read peak to peak on a line 2 pixels thick, and the result is reported as the mean ± s.d. across fields of view. The method and the 81.9 nm value come from M'Saad, Cairns et al., *Nature Biotechnology* 2025. What is taken from whom is set out in [METHODS_AND_CREDITS.md](METHODS_AND_CREDITS.md).

Started 6 October 2026. First sample set: GluA2 (260831_WT4).

## How to run it

1. Connect the lab server drive (X:).
2. Double-click `Measure_ExF.bat` in this folder.
3. Type part of the sample name (for example `GluA2`) and press Enter.
4. Answer whether each antibody it read from the file names is a synaptic one. If it is, synapses carrying that antibody are listed first. If not, the antibody is ignored.

A 3x3 overview takes under a minute. A 44-plane stack takes a few minutes.

To use every plane of a stack rather than 12 spread through it, run from a command prompt:

```
Measure_ExF.bat GluA2 --max-planes 50
```

## What you get

Everything goes to `OneDrive - Yale University\09_Data & Imaging\Images\2026\Zarr Synapse ExF\`.

| File | What it is |
|---|---|
| `Synapse Log.xlsx`, sheet Synapses | The 60 best synapses of each sample: distance, ExF, a link to its picture, a link that opens Neuroglancer on it |
| `Synapse Log.xlsx`, sheet Summary | Per sample: ExF with its 95% interval, pixel size, resolution, synapse classes, antibody labelling, structure measurements |
| `Synapse Log.xlsx`, sheet Method | Every step and threshold used for that run |
| `<sample>\..._synapse-NNN_zNNNN_xNNNNN_yNNNNN.png` | One synapse: pan, antibody, overlay with scale bar, profile with the PSD and DP peaks named and its class |
| `<sample>\..._synapses_structure.png` | Six panels: ExF by orientation and by field, DP–DP spacing, antibody position, PSD length, labelling by depth |
| `<sample>\..._nuclei.png`, `..._nuclei-3d.png`, `.gif` | Stacks only: complete nuclei outlined in the pan stain, and the largest in 3D |
| `Nucleus Sizes.csv` | Nuclear volume and projected area per stack (from `nucleus_size.py`) |
| `<sample>\..._synapses_contact-sheet.png` | All 60 at a glance, numbered as in the log |
| `<sample>\..._synapses.csv` | Every synapse found, logged or not |

## Accepting and rejecting synapses

1. Open the contact sheet beside `Synapse Log.xlsx`. The numbers match.
2. In the Accept column, choose Y or N. Notes is yours too.
3. Save, close the workbook, and run `Measure_ExF.bat` again.

The Summary sheet then shows "ExF, accepted", from your Y rows only. Your answers are kept on every run.

Reject a synapse when it is tilted (no clear gap between the two densities), when the two peaks are two membranes rather than a PSD and DPs, or when the antibody spot sits beside the synapse instead of on it.

## Which number to use

The Summary sheet's `ExF` column is the one number per sample. `ExF Basis` says what it rests on:

| ExF Basis | Use it? |
|---|---|
| accepted by a person | Yes |
| antibody-positive synapses, unreviewed | Yes, until you have reviewed |
| all double peaks, pan only, unreviewed | No. Without a synaptic antibody to confirm them, some "synapses" are pairs of membranes, and the value reads high |

`ExF (Gaussian fit)` is the same synapses read from two fitted Gaussians. It runs about 10% higher than peak to peak and is kept for comparison only.

A synapse's number can change when a sample is measured again. Its `Synapse ID` (slice and position, also in the picture's file name) does not, so use the ID or the file name when listing synapses for a paper.

## Results (7 October 2026, not yet reviewed by a person)

46 samples are in the log. These are the ones whose ExF rests on antibody-positive synapses:

| Gel / antibody | Samples | ExF (mean ± s.d. across fields) |
|---|---|---|
| WT4, GluA2 | 2 | 16.3 ± 1.4 and 16.7 ± 1.2 |
| WT5, GluA3 (rabbit and unlabelled host) | 4 | 17.3 to 19.0 |
| KO6, GluA3 (3x3 overview) | 1 | 16.9 ± 1.6 |
| WT2 and KO2, GluA1 | 5 | 20.1 to 20.9 |
| 260910 antibody testing | 2 | 23.1 ± 0.7 and 23.2 ± 1.0 |
| 260616, NR1 + Homer1 (after the camera correction, see below) | 12 | 21.2 to 23.2 |

On 16 June 2026 the pan (488) channel was recorded on a second camera, mirrored, rotated and shifted. `channel_registration.py` measures the correction from that day's two bead images and every tool applies it; before it, the June samples showed no antibody at their synapses. The July (`260722`) samples also name a second camera but were in register and need no correction.

The other samples (Cx43, mGluR5, some GluA3, the July set) have too few antibody-positive synapses and fall back on pan-only double peaks. Their values (19 to 23) are marked unreliable and should not be quoted.

Things the numbers show that need a person's judgement:

- **ExF differs between gels** (about 16.5 for the GluA2 gel, about 20.5 for the GluA1 gels) and agrees between samples of the same gel.
- **The DP–DP second ruler is not usable.** It returns 18 to 21 for every sample whatever the DP–PSD value, which means it is set by the optics, not by the tissue.
- **GluA1 labels the knockout as strongly as the wild type** (51–65% of synapses in KO2, 45–65% in WT2). Either that knockout is not a GluA1 knockout or the antibody is not specific. mGluR5 in KO3 labels 0%.
- No sample shows a dependence of ExF on synapse orientation.

Nothing here has been checked against hand measurements in FIJI. That is the step that would make the ExF numbers defensible in a paper.

## Other tools in the same folder

| Double-click | What it does |
|---|---|
| `Compare_AB.bat` (in ZarrBatchConverter) | Sets two groups of samples side by side, antibody by antibody, from the antibody channel alone. First use: SZ against AC |
| `Make_Gallery.bat` (in ZarrBatchConverter) | Rebuilds the gallery page, which shows ExF, resolution, classes and labelling per sample; clicking a synapse thumbnail opens Neuroglancer on it |

`nucleus_size.py` measures nuclei for an expansion factor from nuclei. It reports sizes only until nuclei measured in tissue that was not expanded are entered at the top of the script.

## Which samples are left out

- `SZ` or `control` in the name.
- A repeat named number_number, such as `Sample1_2`.

These stay: a number that ends an antibody's name (`Homer1_2`), and a grid size (`3x3_1`; 3x3 marks the 2D overview rather than the volume).

The antibody names the program recognises are in the `ANTIBODIES` table at the top of `synapse_exf.py`. Add new ones there.

## Where the code is

The programs live in `Desktop\ZarrBatchConverter`, because they share its settings, pan-stain detection and channel colours. `Measure_ExF.bat` in this folder only starts them.

| File | What it does |
|---|---|
| `synapse_exf.py` | Finds the synapses, measures them, writes the log and pictures |
| `synapse_structure.py` | DP–DP spacing, orientation and field checks, antibody position, PSD size, labelling by depth |
| `nucleus_size.py` | Nuclear volume and projected area in stacks, with a 3D rendering |
| `compare_ab.py` | Compares antibody labelling between two sets of samples |
| `synapse_fit_options.py` | Compares nine ways of reading the distance off a profile |
| `ab_efficiency.py` | Separate tool: antibody labelling efficiency against the pan stain |

Requirements: Python 3.10 or newer with numpy 2, zarr 3, dask, scikit-image, scipy, matplotlib and openpyxl. The .bat installs what is missing.
