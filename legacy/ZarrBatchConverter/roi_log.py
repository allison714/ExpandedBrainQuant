"""
Screenshot / QC export and ROI log for the zarr images.

Two ways in:
  roi_log.py --batch       go through the samples, export the standard QC views for each,
                           and optionally collect a rating and notes
  roi_log.py --save-view   log the Neuroglancer view on the clipboard as an ROI (with a
                           crop), and/or make it your default link for that image

Everything is kept under the screenshot folder from settings.ini:
  <sample name>\\                 one subfolder per sample, holding its PNGs
  Zarr Screenshot Log.xlsx       one row per image saved, sorted by sample, then slice

Pictures are drawn straight from the image data, with a scale bar, using the colours,
brightness and opacity of the image's Neuroglancer view. They are not browser grabs, so
they are full resolution, free of panels, and can be produced for every sample unattended.

Which views a sample gets depends on what it is (see standard_views):
  stack, one tile     XY / XZ / YZ maximum projections, tilted volume, planes at
                      5/25/50/75/95% depth
  stack, montage      XY projection, tilted volume, overviews at those depths, one seam crop
  flat montage        overview, five crops (centre and four corners)
  flat single tile    the full-resolution image
Every plane saved from a stack also gets a 200% close-up of the middle of the data;
flat images get one.
Crops are otherwise only made at ROIs saved with --save-view. A plane every N slices
(--every N) and a crop of every tile (--grid) are available but off unless asked for.
"""

import datetime as dt
import json
import math
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

import numpy as np
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

import convert_to_zarr as core

LOG_NAME = "Zarr Screenshot Log.xlsx"
PENDING_NAME = "_pending_entries.json"   # entries waiting because the workbook was open in Excel
SHEET = "Log"
HEADER_ROW = 3
# (header, width). Slice is the plane number exactly as Neuroglancer shows it: the first plane is 0.
COLUMNS = [
    ("Sample / File Name", 46), ("Slice", 8), ("Rating", 8), ("Notes", 48), ("View", 15), ("Type", 15),
    ("Screenshot", 48), ("Sample Folder", 14), ("Neuroglancer", 14), ("X (px)", 9), ("Y (px)", 9), ("Logged", 12),
]
HEADERS = [c[0] for c in COLUMNS]
THIN = Side(style="thin", color="D0D0D0")

OUT_PX = 1600          # overviews and planes: at least this many pixels on the longest side
MIP_PX = 1024          # projections read every plane, so they use a coarser level
MAX_MIP_PLANES = 600   # stacks deeper than this are projected from evenly spaced planes
TILE_MAX_PX = 2400     # wider or taller than one camera frame (2048-2304 px here) = a montage
STACK_MIN_PLANES = 5   # fewer planes than this is treated as flat
CROP_PX = 768          # field of a seam or corner crop, in full-resolution pixels
VOLUME_PX = 360        # the tilted volume picture is built from a block about this many voxels a side
ZOOM = 2               # the close-up every sample gets: this many screen pixels per image pixel (200%)
# Planes saved from every stack, as a percentage of the way through the SAMPLE (the planes
# that hold signal), not through the raw stack: stacks usually start and end outside it.
# 5 and 95 show how far the stain reaches at each face, 50 the core, 25 and 75 the trend.
PLANE_PERCENTS = (5, 25, 50, 75, 95)
SAMPLE_SIGNAL = 0.2    # a plane is inside the sample when its signal is this fraction of the peak
ASSUMED_PANEL = 1000   # width in screen pixels assumed for the Neuroglancer panel that was copied
# views made automatically; re-running the batch replaces these rather than adding to them
AUTO_VIEWS = {"MIP XY", "MIP XZ", "MIP YZ", "Volume", "Best plane", "Overview", "Seam crop", "Full image", "Zoom 200%",
              "Crop centre", "Crop top-left", "Crop top-right", "Crop bottom-left", "Crop bottom-right"} \
             | {f"Plane {q}%" for q in PLANE_PERCENTS} | {f"Zoom 200% at {q}%" for q in PLANE_PERCENTS}

# --------------------------------------------------------------------------- reading views


def samples(out_root):
    """(name, zarr folder) for every image in the zarr folder."""
    found = []
    for d in sorted(out_root.iterdir(), key=lambda d: d.name.lower()):
        if d.is_dir() and d.name.lower().endswith(".zarr") and (d / "0" / ".zattrs").is_file():
            stem = d.name[: -len(".zarr")]
            found.append((stem[: -len(".ome")] if stem.endswith(".ome") else stem, d))
    return found


def view_file(out_root, name, series=0):
    """The view a link for this image opens: the user's own default if saved, else the shared one."""
    mine = core.personal_view(out_root, f"{name}.zarr\\{series}")
    return mine if mine.is_file() else out_root / core.STATE_DIR / f"{name}_s{series}.json"


def layer_settings(state):
    """How each visible layer of a view is drawn: channel, colour, brightness range, opacity."""
    squares = {square: rgb for _, square, rgb in core.STANDARD_COLORS}
    out = []
    for layer in state.get("layers", []):
        if layer.get("type") != "image" or layer.get("visible") is False or layer.get("archived"):
            continue
        shader = layer.get("shader", "")
        controls = layer.get("shaderControls", {})
        opened = re.search(r'select\(options=\[[^\]]*\], default="([^"]*)"\)', shader)
        colour = squares.get(controls.get("colour", opened.group(1) if opened else ""), (1.0, 1.0, 1.0))
        rng = (controls.get("normalized") or {}).get("range")
        invert = bool(controls.get("invert", False))
        baked = re.search(r"getDataValue\(\)\)\)-([-\d.e+]+)\)/([-\d.e+]+)", shader)
        if controls.get("defaults") and baked:
            # the 'defaults' box is ticked: the layer shows its opening brightness and colour
            lo = float(baked.group(1))
            rng, invert = [lo, lo + float(baked.group(2))], False
            colour = squares.get(opened.group(1) if opened else "", colour)
        out.append({
            "channel": (layer.get("localPosition") or [0])[0], "name": layer.get("name", ""),
            "colour": colour, "range": rng, "invert": invert, "opacity": float(layer.get("opacity", 1)),
            # the layers Neuroglancer projects in its 3D panel (the antibody channels)
            "projected": layer.get("volumeRendering") in ("max", "on"),
        })
    return out


def view_position(state):
    """{'x','y','z'} voxel position of a view."""
    return {n: v for n, v in zip(state.get("dimensions", {}), state.get("position", [])) if n in "xyz"}


def classify(info):
    """'stack', 'stack montage', 'montage' or 'tile', from the image's dimensions.

    The zarr does not record how an image was tiled, so a montage is recognised by being
    larger than one camera frame.
    """
    shape = info["shape"]
    deep = shape.get("z", 1) >= STACK_MIN_PLANES
    wide = max(shape["x"], shape["y"]) > TILE_MAX_PX
    return ("stack montage" if wide else "stack") if deep else ("montage" if wide else "tile")


# --------------------------------------------------------------------------- drawing


def compose(planes, layers, auto=False):
    """Blend one 2D array per layer into an RGB float image, additively, as Neuroglancer does.

    auto - set each layer's brightness from the array itself rather than from the view.
           Used for projections: the brightest pixel through a stack sits well above any
           single slice, so the slice's range would wash out.
    """
    rgb = None
    for data, layer in zip(planes, layers):
        data = data.astype(np.float32)
        if layer["range"] and not auto:
            lo, hi = layer["range"]
        else:
            lit = data[data > 0]
            lo, hi = np.percentile(lit if lit.size else data, [60, 99.95] if auto else [1, 99.9])
        v = np.clip((data - lo) / max(hi - lo, 1e-6), 0, 1)
        if layer["invert"]:
            v = 1 - v
        part = v[..., None] * np.asarray(layer["colour"], np.float32) * layer["opacity"]
        rgb = part if rgb is None else rgb + part
    if rgb is None:
        raise ValueError("no visible layers in the view")
    return np.clip(rgb, 0, 1)


def finish(rgb, um_per_px=None, size=None, zoom=None):
    """RGB float array -> PIL image with a scale bar.

    size - (w, h) to stretch to first
    zoom - enlarge by exactly this factor, each image pixel becoming a zoom x zoom square
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.fromarray((rgb * 255).astype(np.uint8))
    if size:
        img = img.resize(size, Image.BILINEAR)
    elif zoom:
        um_per_px = um_per_px / zoom if um_per_px else None
        img = img.resize((img.width * zoom, img.height * zoom), Image.NEAREST)
    elif max(img.size) < OUT_PX * 0.6:
        # zoomed in past full resolution: enlarge without smoothing so pixels stay honest
        f = max(1, int(OUT_PX * 0.8 // max(img.size)))
        um_per_px = um_per_px / f if um_per_px else None
        img = img.resize((img.width * f, img.height * f), Image.NEAREST)
    if um_per_px:
        target = img.width * um_per_px / 5
        length = max((n for n in (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000) if n <= target), default=0.5)
        bar, m, h = int(length / um_per_px), max(12, img.width // 60), max(4, img.width // 200)
        text = max(14, img.width // 55)
        draw = ImageDraw.Draw(img)
        draw.rectangle([m, img.height - m - h, m + bar, img.height - m], fill=(255, 255, 255), outline=(0, 0, 0))
        try:
            font, label = ImageFont.truetype("arial.ttf", text), f"{length:g} µm"
        except OSError:
            font, label = None, f"{length:g} um"   # the built-in font has no micro sign
        draw.text((m, img.height - m - h - text - 6), label, fill=(255, 255, 255), font=font,
                  stroke_width=2, stroke_fill=(0, 0, 0))
    return img


def pick_level(info, span, want_px):
    """Index of the coarsest pyramid level that still gives `want_px` pixels across `span`."""
    return max(0, min(len(info["levels"]) - 1, math.floor(math.log2(max(span, 1) / want_px))))


def x_um(info):
    unit = core.UNIT_TO_M.get(info["units"].get("x"))
    return info["scale"]["x"] * unit * 1e6 if unit else None


def render(zarr_dir, info, layers, z, centre=None, fov=None, zoom=None):
    """Draw one plane, or a square region of it. Returns (PIL image, plane drawn).

    centre - (x, y) in full-resolution pixels; None = middle of the image
    fov    - width of the square field to show, in full-resolution pixels; None = whole image
    zoom   - draw the field from full-resolution pixels, enlarged this many times
    """
    import zarr

    shape, axes = info["shape"], info["axes"]
    full_x, full_y, n_z = shape["x"], shape["y"], shape.get("z", 1)
    if fov is None:
        x0, x1, y0, y1 = 0, full_x, 0, full_y
    else:
        cx, cy = centre if centre else (full_x / 2, full_y / 2)
        x0, x1 = int(max(0, cx - fov / 2)), int(min(full_x, cx + fov / 2))
        y0, y1 = int(max(0, cy - fov / 2)), int(min(full_y, cy + fov / 2))
        if x1 <= x0 or y1 <= y0:
            raise ValueError("the view is outside the image")
    k = 0 if zoom else pick_level(info, max(x1 - x0, y1 - y0), OUT_PX)
    import channel_registration   # a channel recorded on a second camera is drawn in register with the others
    arr = channel_registration.Registered(zarr.open(str(zarr_dir / "0" / info["levels"][k]), mode="r"), zarr_dir, info)
    fx, fy = arr.shape[axes.index("x")] / full_x, arr.shape[axes.index("y")] / full_y
    sx = slice(int(x0 * fx), max(int(x0 * fx) + 1, int(math.ceil(x1 * fx))))
    sy = slice(int(y0 * fy), max(int(y0 * fy) + 1, int(math.ceil(y1 * fy))))
    z = int(min(max(round(z), 0), n_z - 1))
    planes = [np.asarray(arr[tuple({"t": 0, "c": l["channel"], "z": z, "y": sy, "x": sx}[a] for a in axes)])
              for l in layers]
    um = x_um(info)
    return finish(compose(planes, layers), um / fx if um else None, zoom=zoom), z


def project(zarr_dir, info, layers):
    """One pass through a stack. Returns ({'MIP XY','MIP XZ','MIP YZ': PIL image}, {percent: plane}).

    Every plane is read (evenly spaced ones beyond MAX_MIP_PLANES) at a coarse level.
    XY shows the antibody channels, as Neuroglancer's 3D panel does, because a projected
    pan stain is fog. The side views show every channel, since the pan stain is what
    reveals the slab's thickness and tilt.

    The planes returned are PLANE_PERCENTS of the way through the sample. The sample's
    extent is found from the structure channel: the run of planes whose mean brightness
    is at least SAMPLE_SIGNAL of the way from the darkest plane to the brightest.
    """
    import zarr

    shape, axes = info["shape"], info["axes"]
    n_z = shape["z"]
    k = pick_level(info, max(shape["x"], shape["y"]), MIP_PX)
    import channel_registration   # a channel recorded on a second camera is drawn in register with the others
    arr = channel_registration.Registered(zarr.open(str(zarr_dir / "0" / info["levels"][k]), mode="r"), zarr_dir, info)
    step = max(1, math.ceil(n_z / MAX_MIP_PLANES))
    planes = list(range(0, n_z, step))
    # the structure channel: the pan stain when there is one, else the first layer
    antibodies = [l for l in layers if l.get("projected")]
    focus = next((l for l in layers if antibodies and not l.get("projected")), layers[0])
    xy, xz, yz, signal = [], [], [], np.zeros(len(planes))
    thin = max(1, math.ceil(max(arr.shape[axes.index("x")], arr.shape[axes.index("y")]) / VOLUME_PX))
    skip = max(1, math.ceil(len(planes) / VOLUME_PX))
    small = []   # per layer: every `skip`-th plane, every `thin`-th pixel
    for layer in layers:
        top = None
        side_x, side_y, kept = [], [], []
        for i in range(0, len(planes), 16):
            block = planes[i:i + 16]
            idx = tuple({"t": 0, "c": layer["channel"], "z": slice(block[0], block[-1] + 1, step),
                         "y": slice(None), "x": slice(None)}[a] for a in axes)
            data = np.asarray(arr[idx])
            top = data.max(axis=0) if top is None else np.maximum(top, data.max(axis=0))
            side_x.append(data.max(axis=1))   # looking along y: rows are planes, columns are x
            side_y.append(data.max(axis=2))   # looking along x: rows are planes, columns are y
            kept.append(data[[j for j in range(len(block)) if (i + j) % skip == 0]][:, ::thin, ::thin])
            if layer is focus:
                signal[i:i + len(block)] = data.reshape(len(data), -1).astype(np.float32).mean(axis=1)
        xy.append(top)
        small.append(np.concatenate(kept))
        xz.append(np.concatenate(side_x))
        yz.append(np.concatenate(side_y).T)   # y down the page, z across, like an orthoview's right panel
    um = x_um(info)
    um_px = um * shape["x"] / arr.shape[axes.index("x")] if um else None
    z_unit = core.UNIT_TO_M.get(info["units"].get("z"))
    # stretch the side views to true proportions: a plane is usually much thicker than a pixel
    depth = len(planes)
    if um_px and z_unit:
        depth = int(round(len(planes) * step * info["scale"]["z"] * z_unit * 1e6 / um_px))
    depth = max(32, min(depth, 4000))
    top_layers = antibodies or layers
    images = {
        "MIP XY": finish(compose([xy[layers.index(l)] for l in top_layers], top_layers, auto=True), um_px),
        "MIP XZ": finish(compose(xz, layers, auto=True), um_px, size=(xz[0].shape[1], depth)),
        "MIP YZ": finish(compose(yz, layers, auto=True), None, size=(depth, yz[0].shape[0])),
    }
    try:
        # true proportions for the block: how many of its (thinned) pixels one kept plane is thick
        thick = (skip * step * info["scale"]["z"] * z_unit * 1e6) / (um_px * thin) if um_px and z_unit else 1.0
        size_um = None
        if um and z_unit:
            size_um = (shape["x"] * um, shape["y"] * um, n_z * info["scale"]["z"] * z_unit * 1e6)
        images["Volume"] = tilted_volume([small[layers.index(l)] for l in top_layers], top_layers, thick, size_um)
    except Exception as e:
        print(f"   tilted volume not drawn ({e})")
    inside = np.flatnonzero(signal >= signal.min() + SAMPLE_SIGNAL * (signal.max() - signal.min()))
    first, last = (inside[0], inside[-1]) if len(inside) >= 3 else (0, len(planes) - 1)
    chosen = {q: planes[int(round(first + (last - first) * q / 100))] for q in PLANE_PERCENTS}
    return images, chosen


def tilted_volume(blocks, layers, thick, size_um=None):
    """The whole stack as a tilted block: a maximum projection seen from above one corner,
    like the 3D panel of the Neuroglancer view, with the block's edges drawn in.

    blocks  - one (planes, y, x) array per layer, already thinned to a small size
    thick   - how many pixels deep one plane should be drawn, for true proportions
    size_um - (x, y, z) size of the stack in micrometres. Three edges are then labelled
              with their real lengths and the size is written underneath. A single scale
              bar would mislead here: the block is turned, so the same length is drawn
              shorter along one edge than along another.
    """
    from PIL import Image, ImageDraw, ImageFont
    from scipy import ndimage

    tip, swing = core.VOLUME_TILT
    rgb = None
    shape = None
    for data, layer in zip(blocks, layers):
        data = data.astype(np.float32)
        lit = data[data > 0]
        lo, hi = np.percentile(lit if lit.size else data, [60, 99.95])
        vol = (np.clip((data - lo) / max(hi - lo, 1e-6), 0, 1) * 255).astype(np.uint8)
        depth = int(min(max(round(vol.shape[0] * thick), 8), VOLUME_PX * 2))
        vol = ndimage.zoom(vol, (depth / vol.shape[0], 1, 1), order=1)
        shape = vol.shape
        vol = ndimage.rotate(vol, swing, axes=(1, 2), reshape=True, order=1)   # swing about the z axis
        vol = ndimage.rotate(vol, tip, axes=(0, 1), reshape=True, order=1)     # tip back about the x axis
        part = (vol.max(axis=0).astype(np.float32) / 255)[..., None] * np.asarray(layer["colour"], np.float32)
        rgb = part if rgb is None else rgb + part
    img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
    grow = max(1, round(1200 / max(img.size)))   # enlarge first, so lines and text stay crisp
    img = img.resize((img.width * grow, img.height * grow), Image.BILINEAR)
    margin = max(70, img.width // 12)            # room round the block for the edge labels
    page = Image.new("RGB", (img.width + 2 * margin, img.height + 2 * margin + 40), (0, 0, 0))
    page.paste(img, (margin, margin))

    # the block's twelve edges, put through the same two turns
    def turn(pts, angle, a, b, size_in, size_out):
        # scipy maps output -> input with [[cos, sin], [-sin, cos]] about the centres
        t = math.radians(angle)
        out = pts.copy()
        da, db = pts[:, a] - (size_in[a] - 1) / 2, pts[:, b] - (size_in[b] - 1) / 2
        out[:, a] = math.cos(t) * da - math.sin(t) * db + (size_out[a] - 1) / 2
        out[:, b] = math.sin(t) * da + math.cos(t) * db + (size_out[b] - 1) / 2
        return out

    def turned_size(size, angle, a, b):
        t = math.radians(angle)
        new = list(size)
        new[a] = int(round(abs(size[a] * math.cos(t)) + abs(size[b] * math.sin(t))))
        new[b] = int(round(abs(size[a] * math.sin(t)) + abs(size[b] * math.cos(t))))
        return new

    # corner i: bit 2 = far end in z, bit 1 = far end in y, bit 0 = far end in x
    corners = np.array([[z, y, x] for z in (0, shape[0] - 1) for y in (0, shape[1] - 1) for x in (0, shape[2] - 1)], float)
    size1 = turned_size(shape, swing, 1, 2)
    pts = turn(corners, swing, 1, 2, shape, size1)
    size2 = turned_size(size1, tip, 0, 1)
    pts = turn(pts, tip, 0, 1, size1, size2)
    px = pts[:, 2] * img.width / size2[2] + margin
    py = pts[:, 1] * img.height / size2[1] + margin
    draw = ImageDraw.Draw(page)
    yellow = (255, 221, 0)
    for i in range(8):
        for j in range(i + 1, 8):
            if bin(i ^ j).count("1") == 1:   # corners that differ along one axis share an edge
                draw.line([(px[i], py[i]), (px[j], py[j])], fill=yellow, width=2)

    if size_um:
        try:
            font = ImageFont.truetype("arial.ttf", max(18, page.width // 50))
            micro = "µm"
        except OSError:
            font, micro = None, "um"
        # label the three edges that meet at the corner lowest on the page: they are on the
        # outside of the drawing, where the text is clear of the data
        start = int(np.argmax(py))
        cx, cy = float(px.mean()), float(py.mean())
        for bit, axis, length in ((1, "X", size_um[0]), (2, "Y", size_um[1]), (4, "Z", size_um[2])):
            end = start ^ bit
            draw.line([(px[start], py[start]), (px[end], py[end])], fill=yellow, width=5)
            mx, my = (px[start] + px[end]) / 2, (py[start] + py[end]) / 2
            away = math.hypot(mx - cx, my - cy) or 1.0
            where = (mx + (mx - cx) / away * 52, my + (my - cy) / away * 52)   # pushed out from the block
            draw.text(where, f"{axis} {length:.4g} {micro}", fill=(255, 255, 255), font=font, anchor="mm",
                      stroke_width=3, stroke_fill=(0, 0, 0))
        draw.text((margin // 2, page.height - 22),
                  f"{size_um[0]:.4g} × {size_um[1]:.4g} × {size_um[2]:.4g} {micro}  (X × Y × Z)   "
                  "maximum projection, edges labelled with their real length",
                  fill=(210, 210, 210), font=font, anchor="lm")
    return page


def expected_views(kind):
    """(view, file tag) of every standard picture a sample of this type gets."""
    close = f"Zoom {ZOOM * 100}%"
    if kind in ("stack", "stack montage"):
        views = [("MIP XY", "MIP-XY")] + ([("MIP XZ", "MIP-XZ"), ("MIP YZ", "MIP-YZ")] if kind == "stack" else [])
        views.append(("Volume", "Volume"))
        for q in PLANE_PERCENTS:
            views += [(f"Plane {q}%", f"Plane-{q:02d}pct"), (f"{close} at {q}%", f"Zoom-{ZOOM * 100}pct-{q:02d}pct")]
        return views + ([("Seam crop", "Seam-crop")] if kind == "stack montage" else [])
    if kind == "montage":
        return [("Overview", "Overview")] + [(v, v.replace(" ", "-")) for v in (
            "Crop centre", "Crop top-left", "Crop top-right", "Crop bottom-left", "Crop bottom-right")] + \
            [(close, f"Zoom-{ZOOM * 100}pct")]
    return [("Full image", "Full-image"), (close, f"Zoom-{ZOOM * 100}pct")]


def missing_views(name, kind, folder):
    """The standard pictures this sample does not have a file for yet."""
    return [view for view, tag in expected_views(kind) if not any(folder.glob(f"{name}_{tag}_z*.png"))]


def seam_centre(info):
    """A point where tiles of a montage meet, nearest the middle (estimated).

    The zarr keeps no record of the tile layout, so the grid is inferred from the image
    size and a 2048-pixel camera frame with about 10% overlap.
    """
    def junction(size):
        n = max(2, round(size / 1850))
        return size * (n // 2) / n
    return junction(info["shape"]["x"]), junction(info["shape"]["y"])


def standard_views(name, zarr_dir, info, layers, default_z, folder, every=0, grid=False, centre=None, only=None):
    """Export the standard views for one sample. Returns (type, [(view, slice or None, png)], main plane).

    only - names of the views to draw (those the sample is missing); None = all of them.
           A picture is only computed if it is going to be saved.
    """
    kind = classify(info)
    shape = info["shape"]
    made = []

    def save(view, tag, z, draw):
        if only is not None and view not in only:
            return
        img = draw()
        if img is None:
            return
        png = folder / (f"{name}_{tag}_z{z:04d}.png" if z is not None else f"{name}_{tag}_zall.png")
        img.save(png)
        made.append((view, z, png))
        print(f"   {png.name}")

    z = default_z
    # where the 200% close-ups are taken: the middle of the data
    spot = (centre.get("x", shape["x"] / 2), centre.get("y", shape["y"] / 2)) if centre else None
    close = f"Zoom {ZOOM * 100}%"
    if kind in ("stack", "stack montage"):
        images, chosen = project(zarr_dir, info, layers)   # also finds the planes, so it always runs
        z = chosen[50] if 50 in chosen else sorted(chosen.values())[len(chosen) // 2]
        for view, tag in (("MIP XY", "MIP-XY"), ("MIP XZ", "MIP-XZ"), ("MIP YZ", "MIP-YZ"), ("Volume", "Volume")):
            if kind == "stack" or view in ("MIP XY", "Volume"):
                save(view, tag, None, lambda v=view: images.get(v))
        done = set()
        for q, plane in chosen.items():
            if plane not in done:   # a thin sample can put two percentages on one plane
                done.add(plane)
                save(f"Plane {q}%", f"Plane-{q:02d}pct", plane, lambda pl=plane: render(zarr_dir, info, layers, pl)[0])
                # the same depth at 200%, always over the same spot so the depths can be compared
                save(f"{close} at {q}%", f"Zoom-{ZOOM * 100}pct-{q:02d}pct", plane,
                     lambda pl=plane: render(zarr_dir, info, layers, pl, spot, OUT_PX // ZOOM, zoom=ZOOM)[0])
        if kind == "stack montage":
            save("Seam crop", "Seam-crop", z, lambda: render(zarr_dir, info, layers, z, seam_centre(info), CROP_PX)[0])
    elif kind == "montage":
        save("Overview", "Overview", z, lambda: render(zarr_dir, info, layers, z)[0])
        w, h = shape["x"], shape["y"]
        # inset the corners: the outermost edge of a stitched image is often padding
        spots = {"Crop centre": (0.5, 0.5), "Crop top-left": (0.15, 0.15), "Crop top-right": (0.85, 0.15),
                 "Crop bottom-left": (0.15, 0.85), "Crop bottom-right": (0.85, 0.85)}
        for view, (fx, fy) in spots.items():
            save(view, view.replace(" ", "-"), z,
                 lambda fx=fx, fy=fy: render(zarr_dir, info, layers, z, (w * fx, h * fy), CROP_PX)[0])
    else:
        save("Full image", "Full-image", z, lambda: render(zarr_dir, info, layers, z)[0])
    # flat images get one 200% close-up; stacks already have one at each depth above
    if kind in ("montage", "tile"):
        save(close, f"Zoom-{ZOOM * 100}pct", z,
             lambda: render(zarr_dir, info, layers, z, spot, OUT_PX // ZOOM, zoom=ZOOM)[0])

    # dense sampling, only when asked for (always drawn when the flag is given)
    only = None
    if every and shape.get("z", 1) > 1:
        for plane in range(0, shape["z"], every):
            save("Plane", "Plane", plane, lambda pl=plane: render(zarr_dir, info, layers, pl)[0])
    if grid and "montage" in kind:
        nx, ny = max(2, round(shape["x"] / 1850)), max(2, round(shape["y"] / 1850))
        for j in range(ny):
            for i in range(nx):
                at = (shape["x"] * (i + 0.5) / nx, shape["y"] * (j + 0.5) / ny)
                fov = max(shape["x"] / nx, shape["y"] / ny)
                save(f"Tile r{j + 1} c{i + 1}", f"Tile-r{j + 1}-c{i + 1}", z,
                     lambda at=at, fov=fov: render(zarr_dir, info, layers, z, at, fov)[0])
    return kind, made, z


# --------------------------------------------------------------------------- the log workbook


def slice_key(value):
    return value if isinstance(value, (int, float)) else float("inf")  # projections after the slices


def read_log(path):
    """Existing entries as a list of dicts, links included. Raises PermissionError if open in Excel."""
    if not path.is_file():
        return []
    ws = load_workbook(path)[SHEET]
    cols = {ws.cell(HEADER_ROW, c).value: c for c in range(1, ws.max_column + 1)}
    rows = []
    for r in range(HEADER_ROW + 1, ws.max_row + 1):
        if not ws.cell(r, cols.get(HEADERS[0], 1)).value:
            continue
        row = {}
        for h in HEADERS:
            if h in cols:
                cell = ws.cell(r, cols[h])
                row[h] = cell.value
                if cell.hyperlink is not None:
                    link = cell.hyperlink
                    row[h + "#link"] = (link.target or "") + ("#" + link.location if link.location else "")
        rows.append(row)
    return rows


def write_log(path, rows):
    """Rewrite the workbook, sorted by sample then slice."""
    lead = ["Overview", "Full image"] + [f"Plane {q}%" for q in PLANE_PERCENTS] + \
           ["Zoom 200%"] + [f"Zoom 200% at {q}%" for q in PLANE_PERCENTS] + \
           ["MIP XY", "MIP XZ", "MIP YZ", "Seam crop", "Crop centre"]
    rows = sorted(rows, key=lambda r: (
        str(r.get(HEADERS[0], "")).lower(), slice_key(r.get("Slice")),
        lead.index(r.get("View")) if r.get("View") in lead else len(lead), str(r.get("View", ""))))
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    n = len(COLUMNS)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n)
    ws.cell(1, 1, "Zarr Screenshot Log").font = Font(name="Calibri", size=18, bold=True, color="1F3864")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n)
    ws.cell(2, 1, "Sorted by sample, then slice. Rating and Notes can be edited here; they are kept. "
                  "Slice is the plane number as Neuroglancer shows it: the first plane is 0.").font = \
        Font(name="Calibri", size=10, italic=True, color="7F7F7F")
    ws.row_dimensions[1].height = 30
    for c, (header, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(HEADER_ROW, c, header)
        cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="C55A11" if header in ("Rating", "Notes") else "1F3864")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = width
    ws.row_dimensions[HEADER_ROW].height = 28

    previous, band = None, 0
    for i, row in enumerate(rows):
        r = HEADER_ROW + 1 + i
        if row.get(HEADERS[0]) != previous:
            previous, band = row.get(HEADERS[0]), band + 1   # shade alternate samples
        for c, (header, _) in enumerate(COLUMNS, start=1):
            cell = ws.cell(r, c, row.get(header))
            cell.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            cell.font = Font(name="Calibri", size=10, bold=header == HEADERS[0])
            cell.alignment = Alignment(vertical="center", wrap_text=header == "Notes",
                                       horizontal="left" if header in (HEADERS[0], "Notes", "Screenshot") else "center")
            cell.fill = PatternFill("solid", fgColor="FCE8DA" if header in ("Rating", "Notes")
                                    else ("F3F6FB" if band % 2 else "FFFFFF"))
            link = row.get(header + "#link")
            if link:
                cell.hyperlink = link
                cell.font = Font(name="Calibri", size=10, underline="single", color="00796B")
            if header == "Logged":
                cell.number_format = "yyyy-mm-dd"
        ws.row_dimensions[r].height = 22
    last = HEADER_ROW + max(len(rows), 1)
    rating = DataValidation(type="list", formula1='"1,2,3,4,5"', allow_blank=True)
    ws.add_data_validation(rating)
    col = get_column_letter(HEADERS.index("Rating") + 1)
    rating.add(f"{col}{HEADER_ROW + 1}:{col}{last + 500}")
    ws.freeze_panes = ws.cell(HEADER_ROW + 1, 2)
    ws.auto_filter.ref = f"A{HEADER_ROW}:{get_column_letter(n)}{last}"
    ws.sheet_view.showGridLines = False
    core.safe_save(wb, path)


def add_entries(base, new, redone=()):
    """Add entries to the log. If Excel has it open, park them and say so.

    redone - samples whose automatic views were just exported again: their old automatic
             rows are replaced, keeping any rating and notes typed against them.
    """
    path, pending = base / LOG_NAME, base / PENDING_NAME
    waiting = json.loads(pending.read_text(encoding="utf-8")) if pending.is_file() else []
    todo = waiting + new
    if not todo:
        return
    try:
        old = read_log(path)
        kept = {}
        for row in old:
            if row.get(HEADERS[0]) in redone and row.get("View") in AUTO_VIEWS and (row.get("Rating") or row.get("Notes")):
                kept[(row[HEADERS[0]], row["View"])] = (row.get("Rating"), row.get("Notes"))
        old = [r for r in old if not (r.get(HEADERS[0]) in redone and r.get("View") in AUTO_VIEWS)]
        for row in todo:
            rating, notes = kept.get((row.get(HEADERS[0]), row.get("View")), (None, None))
            row["Rating"] = row.get("Rating") or rating
            row["Notes"] = row.get("Notes") or notes
        write_log(path, old + todo)
        if pending.is_file():
            pending.unlink()
        print(f"\nLog updated: {path}  ({len(todo)} entr{'y' if len(todo) == 1 else 'ies'})")
    except PermissionError:
        pending.write_text(json.dumps(todo, indent=1, default=str), encoding="utf-8")
        print(f"\n! {LOG_NAME} is open in Excel, so {len(todo)} entr{'y is' if len(todo) == 1 else 'ies are'} waiting.")
        print("  The pictures are saved. Close the workbook and run this again to add the rows.")


def entry(name, view, kind, z, rating, notes, png, ng_url, pos=None):
    """One log row. Links are relative to the workbook, so the folder can be moved or synced."""
    row = {
        HEADERS[0]: name, "Slice": z if z is not None else "all", "Rating": rating, "Notes": notes or None,
        "View": view, "Type": kind, "Sample Folder": "open folder",
        "Sample Folder#link": urllib.parse.quote(name) + "/",
        "X (px)": round(pos["x"]) if pos and "x" in pos else None,
        "Y (px)": round(pos["y"]) if pos and "y" in pos else None,
        "Logged": dt.date.today().isoformat(),
    }
    if png:
        row["Screenshot"] = png.name
        row["Screenshot#link"] = urllib.parse.quote(name) + "/" + urllib.parse.quote(png.name)
    if ng_url:
        row["Neuroglancer"] = "open view"
        row["Neuroglancer#link"] = ng_url
    return row


def free_name(folder, stem, suffix=".png"):
    """A path in folder that does not exist yet."""
    path, n = folder / f"{stem}{suffix}", 2
    while path.exists():
        path, n = folder / f"{stem}_{n}{suffix}", n + 1
    return path


# --------------------------------------------------------------------------- prompts


def ask(prompt, default=""):
    try:
        answer = input(prompt).strip().strip('"')
    except EOFError:
        answer = ""
    return answer or default


def ask_choice(prompt, choices, default):
    """One letter out of `choices` (e.g. 'YNA')."""
    while True:
        answer = ask(prompt, default).upper()[:1]
        if answer in choices:
            return answer
        print(f"   Please type one of: {', '.join(choices)}")


def ask_rating():
    while True:
        answer = ask("   Rating 1-5 (Enter to skip): ")
        if not answer:
            return None
        if answer in "12345" and len(answer) == 1:
            return int(answer)
        print("   Type a number from 1 to 5, or just press Enter.")


def ask_slice(n_z):
    while True:
        answer = ask(f"   Slice 0-{n_z - 1}, where 0 is the first: ")
        if answer.isdigit() and int(answer) < n_z:
            return int(answer)
        print(f"   Type a whole number from 0 to {n_z - 1}.")


# --------------------------------------------------------------------------- batch


def run_batch(every=0, grid=False):
    core.load_settings()
    out_root, base = Path(core.DEFAULT_OUT), Path(core.DEFAULT_SHOTS)
    if not out_root.is_dir():
        sys.exit(f"Cannot reach the zarr folder {out_root}")
    base.mkdir(parents=True, exist_ok=True)
    print(f"Zarr folder : {out_root}")
    print(f"Screenshots : {base}")
    if every or grid:
        print("Dense extras: " + ", ".join(x for x in (f"a plane every {every} slices" if every else "",
                                                       "a crop of every tile" if grid else "") if x))
    print()
    everything = samples(out_root)
    try:
        logged = read_log(base / LOG_NAME)
    except PermissionError:
        sys.exit(f"{LOG_NAME} is open in Excel. Close it and run this again.")
    done = {r[HEADERS[0]] for r in logged if r.get("View") in AUTO_VIEWS}

    text = ask(f"Which samples? Enter = all {len(everything)}, or type text their names must contain: ")
    chosen = [s for s in everything if text.lower() in s[0].lower()]
    if not chosen:
        sys.exit("No sample name contains that text.")
    # Samples that already have pictures: by default only the pictures they lack are made,
    # so a new kind of view added to the tool is filled in without redrawing the rest.
    mode = "A"
    if any(s[0] in done for s in chosen):
        mode = ask_choice(f"{sum(1 for s in chosen if s[0] in done)} of these already have pictures in the log.\n"
                          "  [A]dd only the pictures they are missing / [R]edo all their pictures / "
                          "[S]kip those samples  (Enter = A): ", "ARS", "A")
        if mode == "S":
            chosen = [s for s in chosen if s[0] not in done]
    if not chosen:
        print("Nothing to do: every chosen sample is already in the log.")
        return
    print(f"\n{len(chosen)} sample(s) to go through.\n")
    print("Answer once for all samples, or be asked for each one:")
    shots = ask_choice("  Standard screenshots?  [Y]es to all / [N]o to all / [A]sk each time (Enter = Y): ", "YNA", "Y")
    notes = ask_choice("  Rating and notes?      [Y]es to all / [N]o to all / [A]sk each time (Enter = N): ", "YNA", "N")
    if notes == "Y":
        print("  You will be asked for a rating and a note for every sample.")
    print()

    new, redone = [], set()
    for n, (name, zdir) in enumerate(chosen, start=1):
        folder = base / name
        folder.mkdir(exist_ok=True)   # every sample gets its subfolder, screenshots or not
        try:
            info = core.read_series(zdir / "0")
            vf = view_file(out_root, name)
            state = json.loads(vf.read_text(encoding="utf-8")) if vf.is_file() else {}
            layers = layer_settings(state) or [
                {"channel": 0, "name": "ch0", "colour": (1.0, 1.0, 1.0), "range": None, "invert": False,
                 "opacity": 1.0, "projected": False}]
            n_z = info["shape"].get("z", 1)
            default_z = int(round(view_position(state).get("z", (n_z - 1) // 2)))
            ng_url = core.state_url(vf) if vf.is_file() and core.server_url_for(vf) else None
            kind = classify(info)
        except Exception as e:
            print(f"[{n}/{len(chosen)}] {name}\n   could not read this image ({e}); skipped")
            continue
        print(f"[{n}/{len(chosen)}] {name}   ({kind}, {info['shape']['x']}x{info['shape']['y']}x{n_z})")

        only = None
        if mode == "A" and name in done:
            only = set(missing_views(name, kind, folder))
            if not only and not (every or grid) and notes == "N":
                print("   has every standard picture already")
                continue
        do_shots = shots == "Y" or (shots == "A" and ask_choice("   Screenshots? [Y/N] (Enter = Y): ", "YN", "Y") == "Y")
        do_notes = notes == "Y" or (notes == "A" and ask_choice("   Rating and notes? [Y/N] (Enter = N): ", "YN", "N") == "Y")
        rows, main_z = [], default_z
        if do_shots:
            try:
                if only is not None:
                    print("   adding: " + (", ".join(sorted(only)) or "nothing new"))
                kind, made, main_z = standard_views(name, zdir, info, layers, default_z, folder, every, grid,
                                                    view_position(state), only)
                rows = [entry(name, view, kind, z, None, None, png, ng_url) for view, z, png in made]
                if only is None:
                    redone.add(name)   # every picture was redrawn: replace this sample's old rows
            except Exception as e:
                print(f"   screenshots failed: {e}")
        if do_notes:
            rating, note = ask_rating(), ask("   Notes (Enter to skip): ")
            # the rating belongs to the sample's main picture: the mid plane, overview or full image
            main = next((r for r in rows if r["View"] in ("Plane 50%", "Overview", "Full image")), None) \
                or next((r for r in rows if r["View"].startswith("Plane ")), None)
            if main is None and (rating or note):
                main = entry(name, "Note", kind, main_z, None, None, None, ng_url)
                rows.append(main)
            if main is not None:
                main["Rating"], main["Notes"] = rating, note or None
            while n_z > 1 and ask_choice("   Add a note for a particular slice? [Y/N] (Enter = N): ", "YN", "N") == "Y":
                z = ask_slice(n_z)
                rating, note, png = ask_rating(), ask("   Notes (Enter to skip): "), None
                if do_shots:
                    png = folder / f"{name}_Plane_z{z:04d}.png"
                    render(zdir, info, layers, z)[0].save(png)
                    print(f"   {png.name}")
                rows.append(entry(name, "Plane" if png else "Note", kind, z, rating, note, png, ng_url))
        new += rows
    add_entries(base, new, redone)


# --------------------------------------------------------------------------- one view from the clipboard


def run_save_view(url=""):
    core.load_settings()
    out_root, base = Path(core.DEFAULT_OUT), Path(core.DEFAULT_SHOTS)
    if not url:
        url = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                             capture_output=True, text=True, errors="replace").stdout
    url = (url or "").strip().strip('"')
    if "#!" not in url:
        sys.exit("That is not a Neuroglancer address. In the browser, click the address bar,\n"
                 "press Ctrl+A then Ctrl+C, and run this again.")
    fragment = url.split("#!", 1)[1]
    try:
        if re.match(r"[a-z][a-z0-9+.-]*://", fragment):
            # the unchanged link from Excel: load the view file it points at
            local = out_root / core.STATE_DIR / urllib.parse.unquote(fragment.split("/_ng_states/", 1)[1].split("?")[0])
            state = json.loads(local.read_text(encoding="utf-8"))
        else:
            state = json.loads(urllib.parse.unquote(fragment))
        sources = [l["source"] if isinstance(l["source"], str) else l["source"]["url"] for l in state["layers"]]
    except Exception:
        sys.exit("The address could not be read. Copy the whole address (Ctrl+A, Ctrl+C) and try again.")
    served = core.server_url_for(out_root)
    m = None
    for src in sources:
        m = re.match(r"zarr://" + re.escape(served or "") + r"/([^/]+)\.zarr/(\d+)/?$", src)
        if m:
            break
    if not served or not m:
        sys.exit(f"That view does not show an image from {out_root}.")
    zname, series = urllib.parse.unquote(m.group(1)), int(m.group(2))
    name = zname[: -len(".ome")] if zname.endswith(".ome") else zname
    zdir = out_root / f"{zname}.zarr"
    if not zdir.is_dir():
        sys.exit(f"{zname}.zarr is no longer in {out_root}.")
    info = core.read_series(zdir / str(series))
    pos = view_position(state)
    n_z = info["shape"].get("z", 1)
    z = int(min(max(round(pos.get("z", 0)), 0), n_z - 1))
    print(f"View of     : {name}")
    print(f"Position    : slice {z} of 0-{n_z - 1} (0 is the first),  x {pos.get('x', 0):.0f},  y {pos.get('y', 0):.0f}")
    for layer in layer_settings(state):
        print(f"   {layer['name']:<16} brightness {layer['range']}")
    print()

    key = f"{zname}.zarr\\{series}"
    if ask_choice("1. Make this view your default link for this image, in your notes workbook? "
                  "[Y/N] (Enter = N): ", "YN", "N") == "Y":
        mine = core.personal_view(out_root, key)
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_text(json.dumps(state, indent=1), encoding="utf-8")
        print("   " + set_notes_link(key, core.state_url(mine)))

    if ask_choice("\n2. Log this view as an ROI (crop, rating, notes) in the screenshot folder? "
                  "[Y/N] (Enter = Y): ", "YN", "Y") == "Y":
        base.mkdir(parents=True, exist_ok=True)
        folder = base / name
        folder.mkdir(exist_ok=True)
        label = re.sub(r"[^A-Za-z0-9-]+", "-", ask("   Short name for this ROI (Enter = ROI): ", "ROI")).strip("-") or "ROI"
        rating = ask_rating()
        note = ask("   Notes (Enter to skip): ")
        # keep the exact view so the log's link reopens it
        roi_view = free_name(out_root / core.STATE_DIR / "rois", f"{name}_s{series}_ROI-{label}_z{z:04d}", ".json")
        roi_view.parent.mkdir(parents=True, exist_ok=True)
        roi_view.write_text(json.dumps(state, indent=1), encoding="utf-8")
        png = None
        try:
            # Neuroglancer's zoom is in image pixels per screen pixel; the panel size is not
            # in the address, so the field drawn is that of a typical ~1000-pixel panel
            fov = float(state.get("crossSectionScale", 1)) * ASSUMED_PANEL
            img, _ = render(zdir, info, layer_settings(state), z, (pos.get("x"), pos.get("y")), fov)
            png = free_name(folder, f"{name}_ROI-{label}_z{z:04d}")
            img.save(png)
            print(f"   saved {png}")
        except Exception as e:
            print(f"   crop failed: {e}")
        add_entries(base, [entry(name, f"ROI {label}", classify(info), z, rating, note, png, core.state_url(roi_view), pos)])


def set_notes_link(key, url):
    """Point the image's name in the notes workbook at a view. Returns a line to print."""
    notes = Path(core.DEFAULT_NOTES)
    if str(notes).lower() == "none" or not notes.is_file():
        return "Saved. (There is no notes workbook to update.)"
    try:
        wb = load_workbook(notes)
        ws = wb["Notes"]
        cols = {ws.cell(core.NOTES_HEADER_ROW, c).value: c for c in range(1, ws.max_column + 1)}
        for r in range(core.NOTES_HEADER_ROW + 1, ws.max_row + 1):
            if ws.cell(r, cols[core.KEY_COLUMN]).value == key:
                ws.cell(r, cols["Image Name"]).hyperlink = url
                core.safe_save(wb, notes)
                return "Saved. The image's name in your notes workbook now opens this view."
        return "Saved, but this image has no row in your notes workbook yet; it will be linked on the next rebuild."
    except PermissionError:
        return "Saved. Your notes workbook is open in Excel, so its link will switch on the next rebuild."


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--batch" in args:
        every = int(args[args.index("--every") + 1]) if "--every" in args else 0
        run_batch(every, "--grid" in args)
    elif "--save-view" in args:
        rest = [a for a in args if a != "--save-view"]
        run_save_view(rest[0] if rest else "")
    else:
        sys.exit(__doc__)
