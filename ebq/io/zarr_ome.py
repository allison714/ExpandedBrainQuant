"""OME-Zarr stacks written by bioformats2raw (multiscale group at <name>.ome.zarr/0/).

open_stack() returns a lazy Dask array ordered (c, z, y, x) and the (z, y, x) voxel size in
micrometres. A flat image or a single-channel image still gets the axis.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

UNIT_TO_UM = {"micrometer": 1.0, "micron": 1.0, "µm": 1.0, "um": 1.0, "nanometer": 1e-3, "nm": 1e-3,
              "millimeter": 1e3, "mm": 1e3, "meter": 1e6, "m": 1e6}


def multiscale_group(path):
    """The group that holds the OME-NGFF 'multiscales' metadata: <path>/0 for bioformats2raw
    layout, <path> itself for direct NGFF."""
    import zarr

    for candidate in (Path(path) / "0", Path(path)):
        try:
            g = zarr.open_group(str(candidate), mode="r")
        except Exception:
            continue
        if "multiscales" in g.attrs:
            return g
    raise ValueError(f"no OME-NGFF multiscales metadata under {path}")


def open_stack(path, level: int = 0):
    import dask.array as da

    g = multiscale_group(path)
    ms = g.attrs["multiscales"][0]
    axes = [a["name"] for a in ms["axes"]]
    units = {a["name"]: a.get("unit") for a in ms["axes"]}
    dataset = ms["datasets"][level]
    scale = next(t["scale"] for t in dataset["coordinateTransformations"] if t["type"] == "scale")
    d = da.from_zarr(g[dataset["path"]])
    names = list(axes)
    if "t" in names:
        d = d[tuple(0 if a == "t" else slice(None) for a in names)]
        names.remove("t")
    for a in "zc":
        if a not in names:
            d, names = d[None], [a] + names
    d = d.transpose([names.index(a) for a in "czyx"])
    voxel = []
    for a in "zyx":
        if a in axes:
            voxel.append(float(scale[axes.index(a)]) * UNIT_TO_UM.get(units.get(a) or "", np.nan))
        else:
            voxel.append(np.nan)
    return d, tuple(voxel)
