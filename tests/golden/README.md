# Golden references for the 2D synapse detector

Frozen on 2026-10-28 from the full re-run of `synapse_exf.py` (150 synapses per sample, 12 planes), so
that later refactoring can be shown not to change the numbers.

| File | What |
|---|---|
| `260616_NR1_Homer1_synapses.csv` | NR1 + Homer1 double label, Dragonfly 60x stack; 83 synapse profiles |
| `260831_WT4_GluA2ms_40x_3x3_FusionStitcher_synapses.csv` | GluA2 single label, Dragonfly 40x montage; 243 profiles |
| `Synapse Log 2026-10-28.xlsx` | The whole log at that date (6,044 rows, 48 samples) with the first 151 human decisions |

Check a new build against them with `python scripts/golden_check.py` (see its docstring). The check
matches synapses by plane and position (within 3 px) and compares the DP-PSD distance, both ExF
readings and the class; a refactor passes when every golden synapse is found and the distances agree
within 0.5 %.
