# NG Link Generator

A Windows tool that takes a collection of OME-Zarr datasets served over HTTP and produces an Excel sheet of clickable Neuroglancer viewer URLs. Each URL is pre-configured with the correct spatial axes, per-channel colors pulled from the OME metadata, and per-channel brightness/contrast ranges computed from a sample of the data.

## What's in this folder

- `Generate_NG_Links.bat` — double-click to run interactively on Windows
- `generate_ng_links.py` — the worker script (used by the .bat; can also be run from the command line)
- `README.md` — this file

## Requirements

### Input data MUST be OME-Zarr (OME-NGFF)

**This tool only works with OME-Zarr.** It does NOT work with:

- Raw TIFF, IMS (Imaris), LIF, CZI, LSM, ND2, or any other proprietary microscopy format
- Neuroglancer's `precomputed://` format
- N5
- Plain zarr without OME-NGFF multiscale metadata

OME-Zarr (also called **OME-NGFF**, "Next-Generation File Format") is a cloud-ready chunked image format maintained by the Open Microscopy Environment consortium. It bundles a zarr multiscale pyramid with standardized metadata for axes, units, physical scale, and channels. The tool specifically reads:

- `.zattrs` with an OME-NGFF `multiscales` block (axis order, voxel size, pyramid levels)
- Optional `OME/METADATA.ome.xml` for per-channel laser/color info (`bioformats2raw.layout: 3` tree)

### Converting IMS, LIF, CZI, etc. to OME-Zarr using Glencoe's `bioformats2raw`

Glencoe Software maintains [**bioformats2raw**](https://github.com/glencoesoftware/bioformats2raw), the standard converter from Bio-Formats-readable microscopy formats to OME-Zarr. It writes a `bioformats2raw`-layout OME-Zarr tree:

```
output.ome.zarr/
├── .zattrs             (contains "bioformats2raw.layout": 3)
├── OME/
│   └── METADATA.ome.xml   (per-channel laser names, colors, etc.)
└── 0/                     (series 0 — the actual OME-NGFF multiscale group)
    ├── .zattrs            (OME-NGFF multiscales metadata)
    ├── .zgroup
    ├── 0/                 (level 0 — highest resolution; one zarr array)
    ├── 1/                 (level 1 — 2x downsampled)
    ├── 2/
    └── 3/
```

The tool handles this layout and the "direct NGFF" layout (multiscale metadata at the root) automatically.

#### Install on Windows

**Option A — Pre-built binary**

1. Download the latest release zip from <https://github.com/glencoesoftware/bioformats2raw/releases>.
2. Unzip to a convenient location. The executable is at `bin/bioformats2raw.bat`.
3. Add that `bin/` directory to your `PATH` (System Properties → Environment Variables), or just call it with the full path.

Requires **Java 11 or newer**. If you don't have Java installed:

```
winget install Microsoft.OpenJDK.21
```

Verify with `java -version` in a new terminal.

**Option B — Docker**

```
docker pull glencoesoftware/bioformats2raw
```

Then run with volume mounts:

```
docker run --rm -v C:\path\to\data:/data glencoesoftware/bioformats2raw ^
    /data/input.ims /data/output.ome.zarr --resolutions 4 --tile-width 1024 --tile-height 1024
```

#### Convert one file

```
bioformats2raw input.ims output.ome.zarr --resolutions 4 --tile-width 1024 --tile-height 1024
```

Useful flags:

| Flag | Default | What it does |
| --- | --- | --- |
| `--resolutions N` | *auto* | number of pyramid levels (4 → full-res, 2x, 4x, 8x) |
| `--tile-width W` / `--tile-height H` | 1024 | chunk size in XY. 1024 is good for Neuroglancer. |
| `--series N` | all | convert only series N (use when source has multiple images per file) |
| `--compression blosc` | blosc | chunk compressor; blosc+lz4 is fast and small |
| `--max-workers N` | *CPUs* | parallelism; raise on big machines |

For CZI, LIF, ND2, etc. the command is identical — `bioformats2raw` auto-detects the format via Bio-Formats.

#### Serve the converted data over HTTP

The output `.ome.zarr/` directory needs to be reachable over HTTP(S) with directory autoindex and (ideally) CORS. The Kuan Lab convention is to drop it under `\\nginx\nginx_share\exm\...` (SMB-mapped as `X:\nginx_share\exm\...`), which is served at `https://neuroglancer.kuanlab.yale.edu/data/exm/...`.

Paths with spaces work but must be percent-encoded in URLs (`Single Label Zarr` → `Single%20Label%20Zarr`). Prefer no-space folder names for new data.

## How the code works

For each `.ome.zarr` dataset:

1. **Discover the multiscale group.** The script looks at `/0/.zattrs` first (`bioformats2raw` layout) and falls back to `/.zattrs` (direct NGFF). It reads the `multiscales` block to pick up `axes` (names + units) and the level-0 `coordinateTransformations.scale` (per-axis voxel size).

2. **Read channel metadata from `OME/METADATA.ome.xml`** if the file exists. The first `<Image>` element's `<Channel>` tags carry a `Name` (often encoding the laser line, e.g. `637_Cy5_CF40_Sona01`) and a `Color` attribute, a signed int32 encoding RGBA with R in the high byte. The script extracts the RGB for shading each channel.

3. **Sample the lowest-resolution level.** The script lists numeric subdirectories (`0/`, `1/`, ...) under the multiscale group, picks the highest-numbered (smallest) one, and loads the entire volume via `zarr.storage.FsspecStore` over HTTP. For each channel it computes the 1st and 99.9th percentiles — those become the lower/upper bounds of the baked brightness/contrast range.

4. **Build the Neuroglancer state.** Produces a JSON state with:
   - Global `dimensions` of (optional `t`), `z`, `y`, `x` with voxel sizes in meters
   - `displayDimensions: ["x", "y", "z"]` — this is the field that forces XY/XZ/YZ panels; Neuroglancer's zarr driver does not honor OME-NGFF `axes[].type`
   - One `image` layer per channel, each with:
     - `source: "zarr://<multiscale_url>/"`
     - `localDimensions: {"c'": [1, ""]}` — a per-layer channel dim; the apostrophe suffix tells Neuroglancer this is local (not caret `^`)
     - `localPosition: [<channel_index>]` — pins the layer to a specific channel
     - A GLSL shader `emitRGB(vec3(R, G, B) * normalized())` using the OME color
     - `shaderControls.normalized.range` = the sampled `[p1, p99.9]`

5. **URL-encode the JSON state** and prepend `https://neuroglancer-demo.appspot.com/#!` to get the clickable URL.

6. **Write to `.xlsx`.** The URL is attached as the cell's hyperlink; the display text is `Open <dataset_name>`.

### Fallback behavior

- OME-XML missing or unparseable → channels use a default red/blue/green/yellow/magenta/cyan palette.
- Lowest-res sample fails (network, zarr lib absent) → layer written without `shaderControls`. Neuroglancer then uses the full uint16 range; the image may look near-black. Click **"Auto range"** on the intensity widget to fit.
- No multiscale at `/0/` → tries the dataset root directly.

## Usage

### Interactive (recommended)

Double-click `Generate_NG_Links.bat`. You'll be prompted for:

1. **Input mode**:
   - `[1]` HTTP URL to a folder containing `.ome.zarr` subdirs — the tool walks the server's autoindex listing and discovers all `.ome.zarr` folders.
   - `[2]` Path to a text file with one zarr URL per line (lines starting with `#` are comments; a trailing `/0/` or `/0` is stripped automatically).

2. **Output mode**:
   - `[1]` Full path to a specific `.xlsx` file (will be created or overwritten).
   - `[2]` A folder path — the output file will be named `ng_links.xlsx` inside.

### Command line

```
python generate_ng_links.py --folder-url "https://.../some_folder/" --output "ng.xlsx"
python generate_ng_links.py --url-file list.txt --output "ng.xlsx"
```

### First-run setup

The `.bat` runs `pip install --user` the first time to install:

- `openpyxl` (xlsx writer)
- `numpy` (percentile computation)
- `zarr>=3`, `fsspec[http]`, `aiohttp` (HTTP-backed zarr reader)

**Python 3.9 or newer must already be installed and on `PATH`.**

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| "No zarr metadata found" error in Neuroglancer | URL path doesn't exist, or folder isn't OME-Zarr. Open it in a browser — you should see an autoindex with `.zattrs`. Watch for folder-name mismatches (e.g. missing initials suffix like ` - SZ`). |
| Blank "source" box when the link opens | Viewer state failed to parse. If you hand-edited, check JSON before URL-encoding. Local dim suffix is `'` (apostrophe), not `^` (caret). |
| Image is black or nearly black | `shaderControls` range wasn't baked (sample failed) → Neuroglancer uses full uint16 range. Click "Auto range" per layer, or re-run. |
| Panels labeled `t/y/z` instead of `x/y/z` | `displayDimensions` field missing. Shouldn't happen with URLs generated by this tool. |
| Script takes a long time | Each dataset: one OME-XML fetch + one lowest-res zarr download. Budget ~5-10 s per dataset on slow links. |
| `pip install` fails during first run | Usually a corporate proxy issue. Run `python -m pip install --user openpyxl numpy "zarr>=3" "fsspec[http]" aiohttp` manually in a terminal with full output and fix from there. |

## License / credit

Generator: lab tool, use freely. Depends on open-source libraries: Neuroglancer (Google), zarr-python (community), openpyxl, fsspec/aiohttp, numpy. Converting source data uses Glencoe Software's bioformats2raw (Apache 2.0).
