# scripts

One-off drivers that string the tools together.

| Script | What it does |
|---|---|
| `redo_figures.py` | Redoes every synapse figure with the current layout, in order: `synapse_exf.py --synaptic auto` (all samples), `aslm_synapses.py` on the 260618 pan-ASLM volume, `aslm_training.py export`, then `make_gallery.py --server --mirror`. Each step is a subprocess; the run takes an hour or more. Launch it detached (`Start-Process`) rather than from a tool that kills background jobs. The paths at the top are the author's; edit `tools` and `volume` before running elsewhere. |
