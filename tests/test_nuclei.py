import math

import numpy as np

from ebq.nuclei import exf_from_nuclei, find_nuclei_3d, summarise
from synthetic import nuclei_volume


def test_finds_two_spheres_with_right_volume():
    voxel = 3.0                                   # µm as imaged: spheres of radius 15 px = 90 µm diameter
    vol = nuclei_volume(radius_px=15)
    found, labels = find_nuclei_3d(vol, voxel)
    assert len(found) == 2
    expected = 4 / 3 * math.pi * 15 ** 3 * voxel ** 3
    for n in found:
        assert abs(n["volume_um3"] / expected - 1) < 0.15
        assert abs(n["diameter_um"] - 90) < 10
        assert n["aspect"] < 1.3
    s = summarise(found, exf_hint=20.0)
    assert abs(s["implied_diameter_tissue_um"] - 4.5) < 0.6


def test_border_sphere_is_left_out():
    vol = nuclei_volume(centres=((45, 45, 45), (10, 70, 70)), radius_px=15)
    found, _ = find_nuclei_3d(vol, 3.0)
    assert len(found) == 1


def test_exf_from_volumes():
    assert abs(exf_from_nuclei(8000.0, 1.0) - 20.0) < 1e-9
