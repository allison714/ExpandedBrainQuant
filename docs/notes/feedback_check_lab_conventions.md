---
name: Check lab conventions before recommending formats/tools
description: Before recommending a format, viewer, or hosting approach, verify what the lab already uses
type: feedback
originSessionId: <globus-endpoint-id>
---
Before recommending a "best" format, viewer, storage layout, or tool for lab work, check what's already established in the lab's existing datasets, repos, and memory.

**Why:** In a session about hosting Neuroglancer volumes, I initially framed the choice as "IMS-shim vs OME-Zarr" and recommended OME-Zarr as long-term best. The user prompted me to look at what other projects use. The lab was already running Neuroglancer precomputed under `neuroglancer.kuanlab.yale.edu/data/`, with existing conversion code (`kuan-lab/pan-exm-t_paper/ng_precomputed_conversion/`) and a Globus transfer pipeline. My generic recommendation would have created a parallel convention no one else uses.

**How to apply:**
- Before recommending a data format, viewer, or hosting scheme in this repo/lab, first scan `X:\nginx_share\` (existing datasets), memory for `kuanlab_*` reference files, and the `kuan-lab/*` GitHub org for the established pattern.
- Prefer the established pattern even if a newer standard would be "better in isolation" — unless the user explicitly wants a migration.
- When you don't know the convention, ask "what do other projects here use?" before recommending.
