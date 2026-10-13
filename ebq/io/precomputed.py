"""Neuroglancer precomputed volumes (unsharded, raw or gzip chunks), read as 3D blocks.

Arrays returned here are ordered (z, y, x) like every other array in ebq. The chunk files on
disk are Fortran-ordered (x, y, z); the transpose happens once, in chunk().
"""

from __future__ import annotations

import gzip
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np


class Volume:
    def __init__(self, folder, readers: int = 16):
        self.folder = Path(folder)
        self.info = json.loads((self.folder / "info").read_text())
        if self.info.get("num_channels", 1) != 1:
            raise ValueError("only single-channel precomputed volumes are supported")
        self.dtype = np.dtype(self.info["data_type"]).newbyteorder("<")
        self.readers = readers

    # ---- metadata -----------------------------------------------------------------------
    @property
    def levels(self) -> int:
        return len(self.info["scales"])

    def scale(self, level: int) -> dict:
        return self.info["scales"][level]

    def shape(self, level: int) -> tuple[int, int, int]:
        """(z, y, x) size of one level."""
        sx, sy, sz = self.scale(level)["size"]
        return int(sz), int(sy), int(sx)

    def voxel_um(self, level: int) -> tuple[float, float, float]:
        """(z, y, x) voxel size in micrometres, as imaged."""
        rx, ry, rz = self.scale(level)["resolution"]
        return rz / 1000, ry / 1000, rx / 1000

    def level_for(self, voxel_um: float) -> int:
        """The level whose xy voxel is nearest (in log) to the one asked for."""
        sizes = [self.voxel_um(i)[2] for i in range(self.levels)]
        return int(np.argmin([abs(math.log(s / voxel_um)) for s in sizes]))

    # ---- reading ------------------------------------------------------------------------
    def chunk(self, level: int, x0: int, y0: int, z0: int) -> np.ndarray:
        """One chunk as (z, y, x); zeros when the file is missing."""
        s = self.scale(level)
        cx, cy, cz = s["chunk_sizes"][0]
        sx, sy, sz = s["size"]
        x1, y1, z1 = min(x0 + cx, sx), min(y0 + cy, sy), min(z0 + cz, sz)
        path = self.folder / s["key"] / f"{x0}-{x1}_{y0}-{y1}_{z0}-{z1}"
        shape = (x1 - x0, y1 - y0, z1 - z0)
        for p, opener in ((path, open), (path.with_name(path.name + ".gz"), gzip.open)):
            if p.is_file():
                with opener(p, "rb") as f:
                    return np.frombuffer(f.read(), self.dtype).reshape(shape, order="F").transpose(2, 1, 0)
        return np.zeros(shape[::-1], self.dtype)

    def block(self, level: int, z0: int, z1: int, y0: int, y1: int, x0: int, x1: int) -> np.ndarray:
        """A (z, y, x) block of one level, clipped to the volume. Chunks are read in parallel."""
        s = self.scale(level)
        cx, cy, cz = s["chunk_sizes"][0]
        sz, sy, sx = self.shape(level)
        z0, y0, x0 = max(0, z0), max(0, y0), max(0, x0)
        z1, y1, x1 = min(z1, sz), min(y1, sy), min(x1, sx)
        out = np.zeros((z1 - z0, y1 - y0, x1 - x0), self.dtype)
        corners = [(gz, gy, gx) for gz in range((z0 // cz) * cz, z1, cz)
                   for gy in range((y0 // cy) * cy, y1, cy) for gx in range((x0 // cx) * cx, x1, cx)]

        def one(c):
            gz, gy, gx = c
            return c, self.chunk(level, gx, gy, gz)

        with ThreadPoolExecutor(self.readers) as pool:
            for (gz, gy, gx), b in pool.map(one, corners):
                az, ay, ax = max(gz, z0), max(gy, y0), max(gx, x0)
                bz, by, bx = min(gz + b.shape[0], z1), min(gy + b.shape[1], y1), min(gx + b.shape[2], x1)
                out[az - z0:bz - z0, ay - y0:by - y0, ax - x0:bx - x0] = b[az - gz:bz - gz, ay - gy:by - gy, ax - gx:bx - gx]
        return out

    def read_level(self, level: int, binning: int = 1, progress=None) -> np.ndarray:
        """A whole level, optionally binned by the mean over binning^3 voxels. The chunk grid
        (128 x 128 x 32 here) is a multiple of 1, 2 and 4, so binning never straddles chunks."""
        s = self.scale(level)
        cx, cy, cz = s["chunk_sizes"][0]
        if any(c % binning for c in (cx, cy, cz)):
            raise ValueError(f"binning {binning} does not divide the chunk size {(cx, cy, cz)}")
        sz, sy, sx = self.shape(level)
        b = binning
        out = np.zeros((sz // b, sy // b, sx // b), np.float32)
        corners = [(gz, gy, gx) for gz in range(0, sz, cz) for gy in range(0, sy, cy) for gx in range(0, sx, cx)]

        def one(c):
            gz, gy, gx = c
            a = self.chunk(level, gx, gy, gz)
            nz, ny, nx = (n // b * b for n in a.shape)
            a = a[:nz, :ny, :nx].astype(np.float32)
            if b > 1:
                a = a.reshape(nz // b, b, ny // b, b, nx // b, b).mean(axis=(1, 3, 5))
            return c, a

        with ThreadPoolExecutor(self.readers) as pool:
            for k, ((gz, gy, gx), a) in enumerate(pool.map(one, corners)):
                z, y, x = gz // b, gy // b, gx // b
                out[z:z + a.shape[0], y:y + a.shape[1], x:x + a.shape[2]] = a
                if progress and k % 500 == 0:
                    progress(k, len(corners))
        return out
