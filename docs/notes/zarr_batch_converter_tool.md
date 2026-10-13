---
name: zarr-batch-converter-tool
description: Desktop\ZarrBatchConverter — batch any-format→OME-Zarr + Excel image list with NG links; how it drives bioformats2raw headlessly
metadata:
  node_type: memory
  type: reference
  originSessionId: <globus-endpoint-id>
  modified: 2026-10-06T21:56:11.859Z
---

`C:\Users\admin\OneDrive\Desktop\ZarrBatchConverter\` (`Convert_To_Zarr.bat` + `convert_to_zarr.py`, built 2026-10-06) scans a folder recursively, converts every image to OME-Zarr, and writes `<folder> Zarr[ - initials]\... - Image List.xlsx`. Successor to [[kuanlab_ab_screening_ome_zarr]]'s link-only tool in `Desktop\NGLinkGenerator`.

**Current layout (2026-10-06, supersedes the per-folder output above):** zarrs go flat into `X:\nginx_share\exm\2026\zarr\<name>.zarr`; paths live in `settings.ini` (written by `First_Time_Setup.bat`). Two workbooks: `X:\nginx_share\exm\2026\Zarr Image List.xlsx` (read-only, regenerated each run, generated metadata only — user was explicit it must never contain their input, not even a copy of notes) and `C:\Users\admin\OneDrive - Yale University\MS Excel\Zarr Image List - Notes.xlsx` (user's hand-typed columns; tool only appends rows, never reads them back). Network sources are staged on local disk, converted, then robocopied up (~20 min per 28 GB .nd2). `.ims` is converted with `--series 0` because Bio-Formats exposes each Imaris resolution level as a separate series.

**Screenshot/ROI log (`roi_log.py`, `Log_Screenshots.bat`, `Save_NG_View.bat`):** QC PNGs are drawn from the zarr data (not browser grabs) with a scale bar and the NG view's colours, into `C:\Users\admin\OneDrive - Yale University\Images\2026\Zarr Screenshots\<sample>\`, logged in `Zarr Screenshot Log.xlsx` there. Views by type: single-tile stack = XY/XZ/YZ MIP + sharpest plane; stitched stack = XY MIP + overview + seam crop; flat montage = overview + 5 crops; single tile = full image. Dense sampling is opt-in (`--every N`, `--grid`). Slice numbers are 0-based as in NG (user: "0 means the first"). A saved personal default view lives in `_ng_states\users\<windows user>\` and only that user's notes workbook links to it. Tiles named `_F0`, `_F1`… are skipped by the converter (user's rule). Old AB Screening zarrs under `Single Label Zarr - AC/SZ` are duplicated in the central folder; user plans to delete the originals after 2026-11-06 — do not delete them.

**Headless bioformats2raw on this PC (no separate Java install):** NGFF-Converter is a GUI only, but its install bundles everything:
`"C:\Program Files\NGFF-Converter\runtime\bin\java.exe" -Djna.library.path="C:\Program Files\NGFF-Converter\app" -cp "C:\Program Files\NGFF-Converter\app\*" com.glencoesoftware.bioformats2raw.Converter <in> <out>`
- `-Djna.library.path` is required or blosc.dll is not found (`java.library.path` does not work).
- Installed versions: NGFF-Converter 2.0.2, bioformats2raw 0.9.3, Bio-Formats 7.3.0 (zarr v2 output).
- Pass `--memo-directory <temp>` or a failed run leaves `.bfmemo` files in the source folder.

**Metadata findings (verified on real files):**
- `.ims` (Fusion): no OME AcquisitionDate; date is in original-metadata key `RecordingDate`. Channel colours in the file are arbitrary (561 = magenta, 637 = white) — colour by the laser number in the channel name instead.
- `.nd2` / Nikon OME-TIFF: no acquisition date exposed at all by Bio-Formats 7.3.0; z axis has no unit for single planes.
- Excel cell hyperlinks cap at ~2079 chars; a 3-channel inline NG state exceeds it. Use `neuroglancer-demo.appspot.com/#!https://.../state.json` (remote JSON state, confirmed in neuroglancer `url_hash_binding.ts`; lab nginx sends CORS for the demo origin).

**Neuroglancer gotchas found by rendering (2026-10-06):**
- NG places OME-Zarr voxel k at k-0.5..k+0.5 (pixel-centre convention). A z position of k+0.5 is a plane boundary; use whole numbers. Fusion-stitched `.ims` can have z=2 with plane 1 all zeros, so open on a plane that holds data.
- Browsers heuristically cache the state JSON (nginx sends no Cache-Control); links carry `?v=<content hash>`.
- All-channel side palette: `"toolPalettes":{"Shader controls":{"row":1,"query":"type:shaderControl +type:opacity"}}`.
- NG shader controls available on neuroglancer-demo.appspot.com: slider, color, checkbox, invlerp, transferFunction, and `select` (dropdown): `#uicontrol string_t colour select(options=["⬜","🟪",...], default="🟪")`, compared in GLSL as `colour=="🟪"`. Emoji squares as option values give text-free colour choices. GLSL ES needs float literals (`111.0`, not `111`). No way to reset an invlerp; a `defaults` checkbox that bypasses it with baked values is the workaround.
- User wants publication colours by default: pan/single channel white, 637 magenta, 561 yellow, 488 green, 405 cyan; never red+green.
- **Test a shader change in the headless browser BEFORE rebuilding live views** (a bad shader blacked out all links once).
- **To actually see a link render** (no browser tool needed): headless Edge with software GL works for Neuroglancer —
  `msedge --headless=new --enable-unsafe-swiftshader --use-angle=swiftshader --ignore-gpu-blocklist --user-data-dir=<tmp> --window-size=1700,1000 --virtual-time-budget=40000 --screenshot=<png> <url>`, then Read the PNG. Do this before telling the user links work.
- Small Fusion `.ims` files may be LZ4-compressed (HDF5 filter 32004); Bio-Formats 7.3.0 cannot read them.

`D:\Coding Temp` does not exist on this machine (no D: drive) — see [[kuanlab_neuroglancer_hosting]] for server paths.
