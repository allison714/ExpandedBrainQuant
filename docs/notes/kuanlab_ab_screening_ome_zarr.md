---
name: Kuan Lab AB Screening OME-Zarr layout (bioformats2raw)
description: Path convention, folder naming, and zarr internal layout for AB screening datasets on the nginx neuroglancer server
type: reference
originSessionId: <globus-endpoint-id>
---
AB screening datasets on the Kuan Lab nginx server use **OME-Zarr with bioformats2raw layout** (not precomputed).

**Path pattern:**
```
https://neuroglancer.kuanlab.yale.edu/data/exm/2026/AB%20Screening/<Progress>/<Progress> Zarr - <Initials>/<Initials>_<target>_<processing>.ome.zarr/
```

**Confirmed real path (2026-09-30 probe):**
```
.../AB Screening/Single Label/Single Label Zarr - SZ/SZ_shank2_surface_to_surface.ome.zarr/
```

**Folder naming gotcha:** the leaf folder is suffixed with the person's initials (`- SZ`, `- AC`, ...) — it's NOT just `Single Label Zarr/`. File prefixes (`SZ_`, `AC_`) match the folder suffix. This tripped up a Cairns_Shank2_Antibody_Testing.xlsx column of URLs where `- SZ` was dropped — none of the links resolved.

**Internal structure (bioformats2raw layout v3):**
- Root `.zattrs` contains only `{"bioformats2raw.layout": 3}` — NOT OME-NGFF multiscale metadata.
- The **multiscale group is at `/0/`** (series index 0). That's where the OME-NGFF `multiscales` metadata lives (axes t,c,z,y,x; four resolution levels `0`–`3`; zarr v2).
- Individual resolution arrays are at `/0/0/`, `/0/1/`, `/0/2/`, `/0/3/`.
- `OME/` sibling folder holds original OME-XML.

**Neuroglancer source URL:** point `zarr://` at the multiscale group — i.e. `.ome.zarr/0/`, NOT `.ome.zarr/` (which would land on the bf2raw layout stub, no multiscales) and NOT `.ome.zarr/0/0/` (single array, no pyramid).

**Example working source:**
```
zarr://https://neuroglancer.kuanlab.yale.edu/data/exm/2026/AB%20Screening/Single%20Label/Single%20Label%20Zarr%20-%20SZ/SZ_shank2_surface_to_surface.ome.zarr/0/
```

**Data shape (SZ shank2 series 0):** `[t=1, c=2, z=30, y=2046, x=2048]`, dtype `>u2`, blosc/lz4 compressed, chunk `1×1×1×1024×1024`. Two channels — user may want separate NG layers per channel for viewing.

**How to apply:**
- When wrapping these URLs into NG viewer links, use `https://neuroglancer-demo.appspot.com/#!<url-encoded-state>` with a `zarr://` source pointing at `.../ome.zarr/0/`.
- If a `zarr://` link errors with "No zarr metadata found," first verify the folder name (initials suffix) with `curl` before assuming a Neuroglancer/format bug.
- This layout coexists with the older precomputed pipeline documented in `kuanlab_neuroglancer_hosting.md` — precomputed is under `/data/exm/<dataset>/chan<N>/`; OME-Zarr AB screening is under `/data/exm/2026/AB Screening/...`.

**Working Neuroglancer state template for this data (verified 2026-10-01):**
```json
{
  "dimensions": {"t":[1,""], "z":[9.666666666666665e-6,"m"], "y":[1.5074291300097733e-7,"m"], "x":[1.5078125000000142e-7,"m"]},
  "displayDimensions": ["x","y","z"],
  "position": [0, 15, 1023, 1024],
  "crossSectionScale": 1,
  "projectionScale": 8388608,
  "layers": [{
    "type": "image",
    "source": "zarr://.../.ome.zarr/0/",
    "localDimensions": {"c'": [1, ""]},
    "localPosition": [0],
    "name": "<layer>"
  }],
  "layout": "4panel"
}
```

**Schema gotchas learned the hard way:**
- `displayDimensions: ["x","y","z"]` is the field that forces display axes. Without it, Neuroglancer's zarr driver on OME-NGFF doesn't honor `axes[].type` and picks the first 3 source dims (t, z, y) as display, giving a t/y/z panel layout.
- Local (per-layer) dim naming suffix is `'` (apostrophe) — NOT `^` (caret). `c'` is a channel-style local dim. Using `c^` causes Neuroglancer to silently fail parsing and show a blank source box.
- `source` as an object with `{"url": "...", "transform": {...}}` is fragile — the zarr driver appears not to accept a `transform` block in this configuration, and the layer loads with an empty source URL. Use `source` as a plain string + `localDimensions`/`localPosition` instead.
- `position` vector length and order matches the order of keys in the `dimensions` object.
- **Brightness/contrast does NOT auto-fit on load.** Omitting `shaderControls` leaves `invlerp` using the full dtype range (0–65535 for uint16), which renders ExM data near-black because signal is in the 100–700 range. The UI "Auto range" button is the only runtime auto-fit, and it writes explicit values into `shaderControls` when clicked. For click-once URLs you must bake `shaderControls.normalized.range` yourself (sample a low-res chunk, compute percentiles).
- Channel switching: `localPosition: [0]` for channel 0, `[1]` for channel 1. There are 2 channels on these datasets.

**Per-channel display defaults from OME metadata (lives at `.ome.zarr/OME/METADATA.ome.xml`):**
- Each `<Channel>` has `Color` (signed int32, interpret as unsigned → unpack as 0xRRGGBBAA) and `Name` (e.g., `"637_Cy5_CF40_Sona01"` — first field is the laser line in nm).
- On SZ AB screening files (verified 2026-10-01, all six single-label datasets): ch0 = Cy5 637 nm (OME color RGB 219,0,0 → red), ch1 = DAPI 405 nm (OME color RGB 0,26,255 → blue). Consistent across datasets.
- To build Neuroglancer colored layers: shader = `#uicontrol invlerp normalized\nvoid main(){emitRGB(vec3(R/255, G/255, B/255) * normalized());}`.

**Baking per-channel b/c from a probe (verified 2026-10-01):**
- Use `zarr.storage.FsspecStore.from_url(f"{url}/0/<lowest_level>", read_only=True)` + `zarr.open`. Lowest level under `/0/` for these datasets is `3` (255×256 xy, 30 z, 2 channels).
- Note: `storage_options={"anon": True}` triggers `aiohttp` TypeError — HTTP store does not accept S3-style options. Omit entirely.
- `zarr.open(...)[:]` loads a 30×255×256×2-channel volume in a few seconds; `np.percentile(slab, [1, 99.9])` gives usable range. Use p99.9 rather than p99 to preserve sparse bright puncta.
- Expect a harmless segfault at Python exit (fsspec/aiohttp cleanup bug) — stdout prints before it, so flush between datasets and ignore.
- Signal ranges observed: ch0 Cy5 varies p1≈105 / p99.9≈185–361 across targets; ch1 DAPI very stable at p1≈104 / p99.9≈126.
- Set `shaderControls.normalized.range = [p1, p99.9]` and `window = [0, round(p99.9 * 1.5)]` to leave room for user adjustment.
