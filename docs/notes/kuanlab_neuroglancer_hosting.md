---
name: Kuan Lab neuroglancer hosting (neuroglancer.kuanlab.yale.edu)
description: URL pattern, format, and pipeline the lab uses to host public Neuroglancer volumes from nginx_share
type: reference
originSessionId: <globus-endpoint-id>
---
The Kuan Lab hosts Neuroglancer **precomputed** volumes at:

```
https://neuroglancer.kuanlab.yale.edu/data/<top>/<dataset>/<channel>/
```

Yale-hosted server (no gs:// or s3://). Neuroglancer viewer is the public Google-hosted one at https://neuroglancer-demo.appspot.com/ — the URL above is the data source, prefixed with `precomputed://`.

**Confirmed examples (from pan-exm-t_paper repo):**
- `precomputed://https://neuroglancer.kuanlab.yale.edu/data/exm/20241006_image_FusionStitcher_try3/chan0`
- `precomputed://https://neuroglancer.kuanlab.yale.edu/data/exm/20241215_8B_tip_CA3_iii_2x2/chan0`

**Path conventions:**
- `<top>` = imaging modality folder (`exm` for pan-ExM; may differ for other modalities).
- `<dataset>` = `YYYYMMDD_<descriptor>`.
- `<channel>` = `chan0`, `chan1`, ... even for single-channel data.
- Newer pan-ExM work goes under `/nginx_share/exm/2026/Stitched Precomputed NG/<dataset>/chan0/`.

**Format: Neuroglancer precomputed (NOT OME-Zarr).** Existing conversion code lives at github.com/kuan-lab/pan-exm-t_paper under `ng_precomputed_conversion/`. Reuse it before rolling anything new.

**nginx → URL mapping:** files at `/nginx_share/exm/<path>/` are served at `https://neuroglancer.kuanlab.yale.edu/data/exm/<path>/`. The `/nginx_share/` backend is the same disk seen as `X:\nginx_share\` over SMB from Windows.

**Upload from compute → nginx: Globus.**
- Source (Yale CRC Misha): `<globus-endpoint-id>`
- Destination (KuanLab nginx): `<globus-endpoint-id>`
- Destination root: `/nginx_share/`
- Documented in `kuan-lab/magneton/.claude/skills/globus/SKILL.md`.
- CLI: `globus transfer <src>:<src_path> <dst>:<dst_path> --recursive --label "<name>"`. Transfers >50 GB run async — CLI returns task ID; check via `globus task show <id>`.
- First use on Misha needs `globus session consent`.

**How to apply:**
- For new NG volumes: convert to precomputed, transfer via Globus to `/nginx_share/exm/<year>/.../chan<N>/`, share the `precomputed://https://...` URL.
- Don't round-trip through the Windows SMB mount — the `X:\` mount IS the destination disk.
- Never recommend OME-Zarr for lab hosting without checking this pattern first — the lab is on precomputed with existing tooling.
- nginx config (new top-level folders, vhosts) requires lab admin.

**URL-encoding gotcha:** paths under `2026/Stitched Precomputed NG/` contain spaces — the final URL must percent-encode them (`Stitched%20Precomputed%20NG`) or Neuroglancer will fail to fetch. Prefer no-space folder names for future datasets.
