"""Synthetic volumes for the tests: textured background with known synapses or nuclei."""

import numpy as np
from scipy import ndimage


def textured(shape, rng, base=100.0, amplitude=15.0, sigma=1.5):
    noise = rng.normal(0, 1, shape).astype(np.float32)
    return base + amplitude * ndimage.gaussian_filter(noise, sigma) / max(ndimage.gaussian_filter(noise, sigma).std(), 1e-6)


def orthonormal(normal, rng):
    n = np.asarray(normal, float)
    n /= np.linalg.norm(n)
    a = rng.normal(size=3)
    e1 = a - a.dot(n) * n
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    return n, e1, e2


def add_disc(vol, centre, normal, radius, thickness, height, rng):
    """A bright disc (plate) of the given radius and Gaussian thickness, added to vol."""
    n, e1, e2 = orthonormal(normal, rng)
    zz, yy, xx = np.mgrid[:vol.shape[0], :vol.shape[1], :vol.shape[2]].astype(np.float32)
    d = np.stack([zz - centre[0], yy - centre[1], xx - centre[2]], axis=-1)
    along = d @ n
    inplane = np.sqrt(np.clip((d ** 2).sum(-1) - along ** 2, 0, None))
    edge = 1 / (1 + np.exp((inplane - radius) / 1.0))
    vol += height * np.exp(-along ** 2 / (2 * thickness ** 2)) * edge
    return n


def synapse_volume(shape=(128, 128, 128), separation_px=8.0, normal=(0.3, 0.8, 0.5), seed=0):
    """Background texture plus a PSD disc and a dimmer DP disc separation_px apart along the normal."""
    rng = np.random.default_rng(seed)
    vol = textured(shape, rng)
    centre = np.array(shape, float) / 2
    n = add_disc(vol, centre, normal, radius=14, thickness=1.2, height=260, rng=rng)
    add_disc(vol, centre + separation_px * n, n, radius=14, thickness=1.2, height=170, rng=rng)
    vol += rng.normal(0, 4, shape)
    return np.clip(vol, 1, None).astype(np.float32), centre, n


def nuclei_volume(shape=(140, 140, 140), centres=((45, 45, 45), (95, 90, 80)), radius_px=15, seed=0):
    """Speckled background with a brightness gradient (tile-like), plus bright smooth spheres
    with a brighter nucleolus at the given centres: nuclei as they look in the pan-ASLM data."""
    rng = np.random.default_rng(seed)
    vol = textured(shape, rng, base=120.0, amplitude=25.0, sigma=1.0)
    zz, yy, xx = np.mgrid[:shape[0], :shape[1], :shape[2]].astype(np.float32)
    vol += 40.0 * xx / shape[2]                                  # a tile-like gradient
    for c in centres:
        r = np.sqrt((zz - c[0]) ** 2 + (yy - c[1]) ** 2 + (xx - c[2]) ** 2)
        edge = 1 / (1 + np.exp((r - radius_px) / 0.7))
        vol += 70.0 * edge + rng.normal(0, 1.0, shape) * edge
        vol[r <= 3] += 80.0
    return np.clip(vol, 1, None).astype(np.float32)
