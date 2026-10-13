"""
Generate Neuroglancer viewer URLs for OME-Zarr datasets served over HTTP.

For each .ome.zarr dataset the script:
  - reads OME-NGFF multiscale metadata to get axis order and voxel size
  - reads the embedded OME-XML (if present) to get per-channel laser names and colors
  - samples the lowest-resolution level to compute per-channel brightness/contrast range
  - builds a Neuroglancer state URL with x/y/z display axes, channel-aware local dim,
    per-channel color shader, and baked-in b/c range

Outputs an .xlsx file with one row per dataset. The link cell is a clickable hyperlink.
"""

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

import numpy as np
import openpyxl


def http_get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


class _HrefParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            for k, v in attrs:
                if k.lower() == "href" and v:
                    self.hrefs.append(v)


def list_hrefs(folder_url):
    html = http_get(folder_url).decode(errors="replace")
    p = _HrefParser()
    p.feed(html)
    return p.hrefs


def discover_zarr_urls_from_folder(folder_url):
    if not folder_url.endswith("/"):
        folder_url += "/"
    out = []
    for h in list_hrefs(folder_url):
        if h.endswith(".ome.zarr/") or h.endswith(".ome.zarr"):
            if not h.endswith("/"):
                h += "/"
            if h.startswith("http"):
                out.append(h)
            else:
                out.append(urllib.parse.urljoin(folder_url, h))
    return out


def read_url_file(path):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("zarr://"):
                s = s[len("zarr://"):]
            # Normalize to the .ome.zarr root (strip any trailing /0/ or /0)
            s = re.sub(r"/0/?$", "", s)
            if not s.endswith("/"):
                s += "/"
            out.append(s)
    return out


def find_multiscale_root(zarr_url):
    """
    Return (multiscale_group_url, axes_list, scale_list, unit) for the lowest-level
    OME-NGFF multiscale group under zarr_url.

    Tries bioformats2raw layout (/.ome.zarr/0/.zattrs) first, then direct multiscale
    (/.ome.zarr/.zattrs).
    """
    base = zarr_url.rstrip("/")
    for candidate in (f"{base}/0", base):
        try:
            attrs = json.loads(http_get(f"{candidate}/.zattrs", timeout=10))
        except Exception:
            continue
        ms = attrs.get("multiscales")
        if not ms:
            continue
        m = ms[0]
        axes = m.get("axes") or []
        datasets = m.get("datasets") or []
        if not axes or not datasets:
            continue
        axis_names = [a.get("name") for a in axes]
        unit = next((a.get("unit") for a in axes if a.get("name") == "x"), None)
        xforms = datasets[0].get("coordinateTransformations") or [{}]
        scale = next((t.get("scale") for t in xforms if t.get("type") == "scale"), None)
        return candidate, axis_names, scale, unit
    return None, None, None, None


def parse_ome_channels(zarr_url):
    try:
        xml = http_get(f"{zarr_url.rstrip('/')}/OME/METADATA.ome.xml", timeout=15).decode("utf-8", "replace")
    except Exception:
        return []
    first_image = re.search(r"<Image\b[^>]*>.*?</Image>", xml, flags=re.DOTALL)
    if not first_image:
        return []
    chans = []
    for m in re.finditer(r"<Channel\b([^/>]*)/?>", first_image.group(0)):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        cid = attrs.get("ID", "")
        idx_match = re.search(r":(\d+)$", cid)
        if not idx_match:
            continue
        idx = int(idx_match.group(1))
        color = attrs.get("Color")
        rgb = None
        if color is not None:
            try:
                u = int(color) & 0xFFFFFFFF
                rgb = ((u >> 24) & 0xFF, (u >> 16) & 0xFF, (u >> 8) & 0xFF)
            except ValueError:
                pass
        chans.append({"idx": idx, "name": attrs.get("Name", f"ch{idx}"), "rgb": rgb})
    chans.sort(key=lambda c: c["idx"])
    return chans


def sample_percentiles(multiscale_root_url, axis_names):
    """Return list of (p1, p99.9) per channel, or [] on failure."""
    try:
        import zarr
        import zarr.storage
    except ImportError:
        print("    (zarr not installed; skipping b/c probe)", file=sys.stderr)
        return []
    base = multiscale_root_url.rstrip("/")
    try:
        hrefs = list_hrefs(base + "/")
        levels = sorted({int(m.group(1)) for h in hrefs for m in [re.match(r"(\d+)/?$", h)] if m})
        if not levels:
            return []
        lowest = levels[-1]
        store = zarr.storage.FsspecStore.from_url(f"{base}/{lowest}", read_only=True)
        arr = zarr.open(store, mode="r")
        data = arr[:]
    except Exception as e:
        print(f"    (sample failed: {e})", file=sys.stderr)
        return []
    # Find which axis is the channel dim
    try:
        c_axis = axis_names.index("c")
    except (ValueError, AttributeError):
        c_axis = None
    out = []
    if c_axis is not None and data.ndim == len(axis_names):
        nchan = data.shape[c_axis]
        for ci in range(nchan):
            idxer = [slice(None)] * data.ndim
            idxer[c_axis] = ci
            # Collapse any size-1 non-space dims (like t)
            slab = data[tuple(idxer)]
            flat = slab.astype(np.int64).ravel()
            if flat.size == 0:
                out.append((0, 1))
                continue
            p1, p999 = np.percentile(flat, [1, 99.9])
            out.append((int(p1), max(int(p999), int(p1) + 1)))
    else:
        flat = data.astype(np.int64).ravel()
        p1, p999 = np.percentile(flat, [1, 99.9])
        out.append((int(p1), max(int(p999), int(p1) + 1)))
    return out


DEFAULT_PALETTE = [
    (255, 0, 0),      # red
    (0, 128, 255),    # blue (DAPI-like)
    (0, 255, 0),      # green
    (255, 255, 0),    # yellow
    (255, 0, 255),    # magenta
    (0, 255, 255),    # cyan
]


def nickname(name):
    if not name:
        return "ch"
    parts = re.split(r"[_\s]+", name)
    if len(parts) >= 2 and parts[0].isdigit():
        return parts[1]
    return parts[0]


def build_viewer_url(zarr_url, display_name):
    ms_root, axis_names, scale, unit = find_multiscale_root(zarr_url)
    if ms_root is None:
        # Fallback: assume zarr_url already points at the multiscale group
        ms_root = zarr_url.rstrip("/")
        axis_names = ["t", "c", "z", "y", "x"]
        scale = [1.0, 1.0, 1.0, 1.0, 1.0]
        unit = ""

    unit_scale = 1e-6 if unit == "micrometer" else 1.0
    def axis_scale(name, default):
        if name not in axis_names or scale is None:
            return default
        try:
            v = scale[axis_names.index(name)]
            return v * unit_scale if name in ("x", "y", "z") else v
        except Exception:
            return default
    sx = axis_scale("x", 1e-7)
    sy = axis_scale("y", 1e-7)
    sz = axis_scale("z", 1e-6)

    channels = parse_ome_channels(zarr_url)
    stats = sample_percentiles(ms_root, axis_names)
    if not channels and stats:
        channels = [{"idx": i, "name": f"ch{i}", "rgb": None} for i in range(len(stats))]
    if not channels:
        channels = [{"idx": 0, "name": "image", "rgb": None}]

    src = f"zarr://{ms_root}/"
    layers = []
    for i, ch in enumerate(channels):
        rgb = ch["rgb"] or DEFAULT_PALETTE[i % len(DEFAULT_PALETTE)]
        r, g, b = rgb[0] / 255, rgb[1] / 255, rgb[2] / 255
        shader = (
            "#uicontrol invlerp normalized\n"
            f"void main(){{emitRGB(vec3({r:.4f},{g:.4f},{b:.4f})*normalized());}}"
        )
        layer = {
            "type": "image",
            "source": src,
            "localDimensions": {"c'": [1, ""]},
            "localPosition": [ch["idx"]],
            "shader": shader,
            "name": f"{display_name}_{nickname(ch['name'])}",
        }
        if i < len(stats):
            p1, p999 = stats[i]
            layer["shaderControls"] = {
                "normalized": {"range": [p1, p999], "window": [0, int(round(p999 * 1.5))]}
            }
        layers.append(layer)

    # Global dims: include t only if source has it (keeps position vector in sync)
    dims = {}
    pos = []
    if "t" in axis_names:
        dims["t"] = [1, ""]
        pos.append(0)
    dims["z"] = [sz, "m"]
    dims["y"] = [sy, "m"]
    dims["x"] = [sx, "m"]
    # Position centered using the lowest-res shape would be ideal; use conservative defaults
    pos.extend([15, 1023, 1024])

    state = {
        "dimensions": dims,
        "displayDimensions": ["x", "y", "z"],
        "position": pos,
        "crossSectionScale": 1,
        "projectionScale": 8388608,
        "layers": layers,
        "layout": "4panel",
    }
    frag = urllib.parse.quote(json.dumps(state, separators=(",", ":")), safe="")
    return f"https://neuroglancer-demo.appspot.com/#!{frag}"


def dataset_name_from_url(url):
    last = url.rstrip("/").rsplit("/", 1)[-1]
    name = urllib.parse.unquote(last)
    if name.endswith(".ome.zarr"):
        name = name[: -len(".ome.zarr")]
    return name


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--folder-url", help="HTTP URL to a folder containing .ome.zarr subdirs")
    src.add_argument("--url-file", help="Path to a text file with one zarr URL per line")
    ap.add_argument("--output", required=True, help="Output .xlsx path")
    args = ap.parse_args()

    if args.folder_url:
        urls = discover_zarr_urls_from_folder(args.folder_url)
    else:
        urls = read_url_file(args.url_file)

    if not urls:
        print("No .ome.zarr URLs found.", file=sys.stderr)
        sys.exit(2)

    print(f"Processing {len(urls)} dataset(s)...")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "NG Links"
    ws["A1"] = "Dataset"
    ws["B1"] = "Neuroglancer Link"
    for i, url in enumerate(urls, start=2):
        name = dataset_name_from_url(url)
        print(f"  [{i-1}/{len(urls)}] {name}")
        try:
            vurl = build_viewer_url(url, name)
            ws.cell(row=i, column=1, value=name)
            cell = ws.cell(row=i, column=2, value=name)
            cell.hyperlink = vurl
            cell.style = "Hyperlink"
        except Exception as e:
            print(f"    FAILED: {e}", file=sys.stderr)
            ws.cell(row=i, column=1, value=name)
            ws.cell(row=i, column=2, value=f"ERROR: {e}")
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 32
    wb.save(args.output)
    print(f"\nWrote: {args.output}")


if __name__ == "__main__":
    main()
