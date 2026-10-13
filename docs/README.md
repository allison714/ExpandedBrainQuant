# Notes

Working notes kept during development (September–October 2026). They record the lab's server conventions, the Neuroglancer and OME-Zarr quirks that cost time to find, and the analysis decisions behind the numbers. They are reference material, not instructions; dates inside are when something was decided or found.

| Note | What it covers |
|---|---|
| [kuanlab_neuroglancer_hosting.md](notes/kuanlab_neuroglancer_hosting.md) | How the lab serves Neuroglancer volumes: URL pattern, precomputed layout, the Misha → Globus → nginx pipeline. |
| [kuanlab_ab_screening_ome_zarr.md](notes/kuanlab_ab_screening_ome_zarr.md) | The bioformats2raw OME-Zarr layout (multiscale group at `/0/`), the Neuroglancer state template that works for it, and the schema gotchas (`displayDimensions`, `c'` local dims, baked `shaderControls`). |
| [zarr_batch_converter_tool.md](notes/zarr_batch_converter_tool.md) | How the converter drives bioformats2raw headlessly through NGFF-Converter's bundled Java; per-format metadata findings (.ims, .nd2); Neuroglancer rendering gotchas; headless-Edge screenshot command. |
| [kuanlab_synapse_exf_analysis.md](notes/kuanlab_synapse_exf_analysis.md) | The ExF analysis log: method decisions (peak-to-peak ÷ 81.9 nm, mean ± s.d. across fields), sample-exclusion rules, second-camera registration, results per antibody, gallery organisation, pan-ASLM work, double-label and WT-vs-KO findings. |
| [feedback_check_lab_conventions.md](notes/feedback_check_lab_conventions.md) | Check what the lab already uses before recommending a format or tool. |
| [feedback_ignore_pff_samples.md](notes/feedback_ignore_pff_samples.md) | PFF and tetraspec samples are left out of every analysis and the gallery. |
| [feedback_long_urls_via_file_or_clipboard.md](notes/feedback_long_urls_via_file_or_clipboard.md) | Long Neuroglancer URLs break when pasted from a terminal; hand them over via clipboard, file or Excel cell. |

Globus endpoint IDs have been replaced with `<globus-endpoint-id>`; see `kuan-lab/magneton` for the real ones.
