"""
Put a channel that was recorded on a second camera back in register with the others.

  channel_registration.py            measure the camera shift from the bead images and record
                                     which samples need it (run once per day that needs it)
  channel_registration.py --views    also write it into the Neuroglancer views of those samples

On 16 June 2026 the 488 channel was on camera 2 (channel name '...Sona02') and the others on
camera 1. Camera 2's image is mirrored left-right, turned by a degree or more and shifted by
tens of pixels relative to camera 1, and the camera was moved during the session, so the two
bead images of that day ('tetraspec-beads' at 10:22, 'tetraspec-beads_1' at 10:47) give two
different transforms.

How the transform is measured. Beads are found in the camera-1 and the camera-2 channel of a
bead image. Pairs of bright beads the same distance apart in both are tried as a match, with
and without mirroring, and each trial is scored by how many camera-2 beads land on a camera-1
bead; the best is refined by a least-squares affine fit (RANSAC, 1 px). The result maps camera-2
pixels (x, y) to camera-1 pixels.

Which transform a sample gets. Every sample of the same day whose channel names put a channel
on another camera is tried with each bead transform (and with none). Antibody puncta on camera 1
should sit on bright pan structure on camera 2 when the two are in register, so the transform
that makes the pan stain brightest at the puncta is taken, if it beats "none" clearly.

The record is '_channel_registration.json' in the zarr folder. Everything that reads pixels
through ab_efficiency.open_level, and the screenshots, apply it; the raw zarr is not changed.
"""

import itertools
import json
import re
import sys
from pathlib import Path

import numpy as np

REGISTRY_NAME = "_channel_registration.json"
BEAD_WORDS = ("tetraspec", "bead")
BEAD_SIGMAS = 12.0       # a bead is this far above the noise
MATCH_PX = 2.0           # a camera-2 bead "lands on" a camera-1 bead within this
MAX_TURN_DEG = 12.0      # the camera was nudged, not turned over
MIN_GAIN = 1.08          # a transform must make the pan stain this much brighter at puncta than no transform
_cache = {}


def registry_path():
    import convert_to_zarr as core
    return Path(core.DEFAULT_OUT) / REGISTRY_NAME


def load(folder=None):
    """{sample name: {channel index: 3x3 matrix}} from the record ({} when there is none)."""
    path = (Path(folder) / REGISTRY_NAME) if folder else registry_path()
    key = str(path)
    stamp = path.stat().st_mtime if path.is_file() else None
    if _cache.get(key, (None,))[0] != stamp:
        table = {}
        if stamp:
            for name, entry in json.loads(path.read_text(encoding="utf-8")).get("samples", {}).items():
                table[name] = {int(c): np.array(m, float) for c, m in entry["channels"].items()}
        _cache[key] = (stamp, table)
    return _cache[key][1]


def for_sample(zarr_dir):
    """{channel: matrix} for the zarr folder of one sample ('<name>.zarr')."""
    zarr_dir = Path(zarr_dir)
    name = zarr_dir.name[:-len(".zarr")] if zarr_dir.name.endswith(".zarr") else zarr_dir.name
    return load(zarr_dir.parent).get(name, {})


def to_camera_1(plane, matrix, factor=1.0):
    """Resample one 2D plane recorded on camera 2 into camera-1 pixel coordinates.

    matrix - 3x3, camera-2 (x, y, 1) -> camera-1 (x, y), in full-resolution pixels
    factor - how many times smaller this plane's pixels are counted (a pyramid level): shifts shrink with it
    Pixels with no camera-2 data come out 0, which every tool here already treats as "no data".
    """
    from scipy import ndimage

    m = np.array(matrix, float)
    m[:2, 2] *= factor
    back = np.linalg.inv(m)                                  # camera 1 (x, y) -> camera 2 (x, y)
    linear = np.array([[back[1, 1], back[1, 0]], [back[0, 1], back[0, 0]]])     # the same, for (row, col)
    moved = ndimage.affine_transform(plane.astype(np.float32), linear, offset=[back[1, 2], back[0, 2]], order=1, cval=0.0)
    return moved if np.issubdtype(plane.dtype, np.floating) else np.rint(moved).astype(plane.dtype)


def lazy(d, zarr_dir, info):
    """A (c, z, y, x) Dask array with the registered channels put in register, still lazy."""
    matrices = for_sample(zarr_dir)
    if not matrices:
        return d
    import dask.array as da

    factor = d.shape[-1] / info["shape"]["x"]
    out = []
    for c in range(d.shape[0]):
        channel = d[c]
        if c in matrices:
            whole = channel.rechunk((1,) + channel.shape[1:])            # one plane per block: the turn needs the whole plane
            channel = whole.map_blocks(lambda b, m=matrices[c]: to_camera_1(b[0], m, factor)[None], dtype=channel.dtype).rechunk(channel.chunks)
        out.append(channel)
    return da.stack(out)


class Registered:
    """A zarr array that hands out registered pixels, for code that indexes the array directly
    (roi_log's screenshots). Indexing is the zarr kind: one int or slice per axis."""

    def __init__(self, array, zarr_dir, info):
        self.array, self.axes, self.shape = array, list(info["axes"]), array.shape
        self.matrices = for_sample(zarr_dir)
        self.factor = array.shape[self.axes.index("x")] / info["shape"]["x"]

    def __getitem__(self, index):
        c = index[self.axes.index("c")] if "c" in self.axes else 0
        if not isinstance(c, (int, np.integer)) or int(c) not in self.matrices:
            return self.array[index]
        iy, ix = self.axes.index("y"), self.axes.index("x")
        whole = list(index)
        whole[iy] = whole[ix] = slice(None)
        data = np.asarray(self.array[tuple(whole)])                       # (..., y, x): planes come last
        planes = data.reshape((-1,) + data.shape[-2:])
        moved = np.stack([to_camera_1(p, self.matrices[int(c)], self.factor) for p in planes]).reshape(data.shape)
        return moved[..., index[iy], index[ix]]


# --------------------------------------------------------------------------- measuring it


def find_beads(image):
    """(x, y, brightness) of the beads in one image."""
    from scipy import ndimage
    from skimage import measure

    smooth = ndimage.gaussian_filter(image.astype(np.float32), 1.0)
    middle = np.median(smooth)
    noise = 1.4826 * np.median(np.abs(smooth - middle)) or 1.0
    labels = measure.label(smooth > middle + BEAD_SIGMAS * noise)
    return np.array([(r.centroid_weighted[1], r.centroid_weighted[0], r.intensity_max)
                     for r in measure.regionprops(labels, intensity_image=smooth - middle) if 4 <= r.area <= 400]).reshape(-1, 3)


def bead_transform(reference, other):
    """Affine camera-2 -> camera-1 from two bead images. Returns (3x3 matrix, beads matched, median residual px)."""
    from scipy import spatial
    from skimage import measure, transform

    a_all, b_all = find_beads(reference), find_beads(other)
    if len(a_all) < 20 or len(b_all) < 20:
        raise ValueError("too few beads")
    a = a_all[np.argsort(-a_all[:, 2])][:150, :2]
    b = b_all[np.argsort(-b_all[:, 2])][:150, :2]
    a_all, b_all = a_all[:, :2], b_all[:, :2]
    tree = spatial.cKDTree(a_all)
    pairs_a = np.array(list(itertools.combinations(range(len(a)), 2)))
    d_a = np.linalg.norm(a[pairs_a[:, 0]] - a[pairs_a[:, 1]], axis=1)
    keep = (d_a > 150) & (d_a < 900)
    pairs_a, d_a = pairs_a[keep][np.argsort(d_a[keep])], np.sort(d_a[keep])
    pairs_b = np.array(list(itertools.combinations(range(len(b)), 2)))
    rng = np.random.default_rng(0)
    best = (0, None)
    for mirror in (False, True):
        bm, bm_all = (b * [-1, 1], b_all * [-1, 1]) if mirror else (b, b_all)
        for i, j in pairs_b[rng.permutation(len(pairs_b))[:400]]:
            d = np.linalg.norm(bm[i] - bm[j])
            if not 150 < d < 900:
                continue
            lo, hi = np.searchsorted(d_a, [d - 1.0, d + 1.0])
            for p, q in itertools.chain(pairs_a[lo:hi], pairs_a[lo:hi][:, ::-1]):
                vb, va = bm[j] - bm[i], a[q] - a[p]
                turn = np.arctan2(va[1], va[0]) - np.arctan2(vb[1], vb[0])
                if abs((np.degrees(turn) + 180) % 360 - 180) > MAX_TURN_DEG:
                    continue
                rot = np.array([[np.cos(turn), -np.sin(turn)], [np.sin(turn), np.cos(turn)]])
                shift = a[p] - rot @ bm[i]
                hits = int((tree.query(bm_all @ rot.T + shift)[0] < MATCH_PX).sum())
                if hits > best[0]:
                    best = (hits, (mirror, rot, shift))
    if best[1] is None or best[0] < 20:
        raise ValueError("the beads of the two channels could not be matched")
    mirror, rot, shift = best[1]
    moved = (b_all * [-1, 1] if mirror else b_all) @ rot.T + shift
    dist, index = tree.query(moved)
    ok = dist < 3
    model, inliers = measure.ransac((b_all[ok], a_all[index[ok]]), transform.AffineTransform, min_samples=4,
                                    residual_threshold=1.0, max_trials=2000, rng=0)
    left = np.linalg.norm(model(b_all[ok][inliers]) - a_all[index[ok]][inliers], axis=1)
    return model.params, int(inliers.sum()), float(np.median(left))


def describe(matrix):
    """'mirrored, turned 1.26°, shifted x +2055 y +12 px' for a camera-2 -> camera-1 matrix."""
    m = np.array(matrix, float)
    mirrored = np.linalg.det(m[:2, :2]) < 0
    turn = np.degrees(np.arctan2(m[1, 0] * (-1 if mirrored else 1), m[0, 0] * (-1 if mirrored else 1)))
    return f"{'mirrored left-right, ' if mirrored else ''}turned {abs(turn):.2f}°, shifted x {m[0, 2]:+.1f} y {m[1, 2]:+.1f} px"


def camera_of(channel_name):
    found = re.search(r"(?i)sona\s*0*(\d+)", channel_name or "")
    return int(found.group(1)) if found else None


def brightness_at_puncta(array, antibody_channels, pan_channel, matrix):
    """How bright the pan stain is at the antibody puncta once moved by `matrix`, over the plane's median."""
    from scipy import ndimage
    from skimage import measure

    z = array.shape[2] // 2
    spots = []
    for c in antibody_channels:
        smooth = ndimage.gaussian_filter(np.asarray(array[0, c, z]).astype(np.float32), 1.0)
        middle = np.median(smooth)
        noise = 1.4826 * np.median(np.abs(smooth - middle)) or 1.0
        spots += [r.centroid for r in measure.regionprops(measure.label(smooth > middle + 8 * noise)) if r.area >= 4]
    if len(spots) < 15:
        return None
    spots = np.round(np.array(spots)).astype(int)
    moved = ndimage.gaussian_filter(to_camera_1(np.asarray(array[0, pan_channel, z]), matrix).astype(np.float32), 1.5)
    at = moved[spots[:, 0], spots[:, 1]]
    return float(np.mean(at[at > 0]) / np.median(moved[moved > 0])) if (at > 0).sum() >= 15 else None


def calibrate():
    """Measure the bead transforms, decide which sample needs which, and write the record."""
    import zarr

    import convert_to_zarr as core
    import roi_log as roi

    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    samples = {}
    for name, zarr_dir in roi.samples(out_root):
        images, _, _, original = core.read_ome_xml(zarr_dir)
        names = [c.get("Name", "") for c in images[0]["channels"]] if images else []
        samples[name] = {"dir": zarr_dir, "when": str(original.get("RecordingDate", "")), "cameras": [camera_of(n) for n in names], "names": names}
    record = {"how": __doc__.split("How the transform is measured.")[1].split("The record is")[0].strip(), "beads": {}, "samples": {}}
    for bead, b in samples.items():
        cams = [c for c in b["cameras"] if c]
        if not any(w in bead.lower() for w in BEAD_WORDS) or len(set(cams)) < 2:
            continue
        main = max(set(cams), key=cams.count)                              # the camera most channels are on
        ref = b["cameras"].index(main)
        array = zarr.open(str(b["dir"] / "0" / "0"), mode="r")
        for c, cam in enumerate(b["cameras"]):
            if cam and cam != main:
                matrix, matched, left = bead_transform(np.asarray(array[0, ref, array.shape[2] // 2]), np.asarray(array[0, c, array.shape[2] // 2]))
                record["beads"][bead] = {"when": b["when"], "camera": cam, "matrix": matrix.tolist(), "beads_matched": matched,
                                         "residual_px": round(left, 2), "in_words": describe(matrix)}
                record["samples"][bead] = {"channels": {str(c): matrix.tolist()}, "from": bead, "in_words": describe(matrix)}
                print(f"{bead} ({b['when'][:16]}): camera {cam} -> camera {main}: {describe(matrix)}; {matched} beads, residual {left:.2f} px")
    for name, s in samples.items():
        cams = [c for c in s["cameras"] if c]
        if name in record["samples"] or len(set(cams)) < 2:
            continue
        main = max(set(cams), key=cams.count)
        other = [c for c, cam in enumerate(s["cameras"]) if cam and cam != main]
        same_day = {b: v for b, v in record["beads"].items() if v["when"][:10] == s["when"][:10]}
        if not same_day:
            # no bead image that day: taken to be in register (the 22 July 2026 set is, per the user)
            print(f"{name}: a channel on camera {s['cameras'][other[0]]}, no bead image of {s['when'][:10]}: taken as in register")
            continue
        array = zarr.open(str(s["dir"] / "0" / "0"), mode="r")
        antibody = [c for c in range(len(s["cameras"])) if c not in other]
        scores = {"none": brightness_at_puncta(array, antibody, other[0], np.eye(3))}
        scores.update({b: brightness_at_puncta(array, antibody, other[0], np.array(v["matrix"])) for b, v in same_day.items()})
        usable = {k: v for k, v in scores.items() if v is not None}
        best = max(usable, key=usable.get) if usable else None
        shown = ", ".join(f"{k} {v:.2f}" if v is not None else f"{k} –" for k, v in scores.items())
        if best and best != "none" and usable[best] >= MIN_GAIN * usable.get("none", 0):
            record["samples"][name] = {"channels": {str(c): same_day[best]["matrix"] for c in other}, "from": best,
                                       "in_words": same_day[best]["in_words"], "pan_brightness_at_puncta": scores}
            print(f"{name} ({s['when'][11:16]}): {best}   [{shown}]")
        else:
            print(f"{name} ({s['when'][11:16]}): NOT DECIDED, left as it is   [{shown}]")
    path = out_root / REGISTRY_NAME
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(f"\n{len(record['samples'])} sample(s) recorded in {path}")
    return record


# --------------------------------------------------------------------------- Neuroglancer


def layer_source(url, matrix, dims):
    """A Neuroglancer layer source that moves the channel by `matrix` (camera-2 -> camera-1, pixels)."""
    m = np.array(matrix, float)
    names = [n for n in ("t",) if n in dims] + ["c'"] + [n for n in ("z", "y", "x") if n in dims]
    rows = []
    for n in names:
        row = [0.0] * (len(names) + 1)
        if n == "x":
            row[names.index("x")], row[names.index("y")], row[-1] = m[0, 0], m[0, 1], m[0, 2]
        elif n == "y":
            row[names.index("x")], row[names.index("y")], row[-1] = m[1, 0], m[1, 1], m[1, 2]
        else:
            row[names.index(n)] = 1.0
        rows.append(row)
    return {"url": url, "transform": {"matrix": rows, "outputDimensions": {n: ([1, ""] if n == "c'" else dims[n]) for n in names}}}


def apply_to_state(state, folder=None):
    """Put the recorded transforms into the image layers of one Neuroglancer state. True if it changed."""
    table = load(folder)
    changed = False
    for layer in state.get("layers", []):
        source = layer.get("source")
        url = source.get("url") if isinstance(source, dict) else source
        found = re.search(r"/([^/]+)\.zarr/", str(url))
        channel = (layer.get("localPosition") or [None])[0]
        if layer.get("type") != "image" or not found or channel is None:
            continue
        matrix = table.get(found.group(1), {}).get(int(channel))
        if matrix is not None:
            new = layer_source(url, matrix, state.get("dimensions", {}))
            if source != new:
                layer["source"], changed = new, True
    return changed


def update_views():
    """Write the transforms into every saved Neuroglancer view of the recorded samples."""
    import convert_to_zarr as core

    core.load_settings()
    out_root = Path(core.DEFAULT_OUT)
    names = set(load(out_root))
    done = 0
    for path in (out_root / core.STATE_DIR).rglob("*.json"):
        if path.name.endswith(".stats.json") or not any(n in path.name or n in str(path.parent) for n in names):
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(state, dict) and apply_to_state(state, out_root):
            path.write_text(json.dumps(state, indent=1), encoding="utf-8")
            done += 1
    print(f"{done} Neuroglancer view(s) updated")


if __name__ == "__main__":
    calibrate()
    if "--views" in sys.argv:
        update_views()
