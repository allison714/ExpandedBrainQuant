import numpy as np

from ebq.synapse3d import alignment, find_synapses_3d
from synthetic import synapse_volume


def test_finds_tilted_synapse_and_reads_distance():
    um = 0.203
    sep = 8.0
    vol, centre, n = synapse_volume(separation_px=sep, normal=(0.3, 0.8, 0.5))
    hits = find_synapses_3d(vol, um)
    assert len(hits) >= 1
    best = min(hits, key=lambda h: np.linalg.norm(np.array(h["centre"]) - (centre + n * sep / 2)))
    assert np.linalg.norm(np.array(best["centre"]) - (centre + n * sep / 2)) < 2.0
    assert abs(abs(np.dot(best["normal"], n)) - 1) < 0.05        # normal recovered up to sign
    assert abs(best["exf"] - sep * um * 1000 / 81.9) < 1.0


def test_antibody_alignment_along_normal():
    um = 0.203
    sep = 8.0
    vol, centre, n = synapse_volume(separation_px=sep, normal=(0.0, 0.6, 0.8), seed=3)
    rng = np.random.default_rng(1)
    zz, yy, xx = np.mgrid[:vol.shape[0], :vol.shape[1], :vol.shape[2]].astype(np.float32)

    def puncta(offset_px):
        c = centre + n * offset_px
        r2 = (zz - c[0]) ** 2 + (yy - c[1]) ** 2 + (xx - c[2]) ** 2
        return (100 + rng.normal(0, 3, vol.shape) + 400 * np.exp(-r2 / (2 * 1.5 ** 2))).astype(np.float32)

    post = puncta(0.0)            # on the PSD
    pre = puncta(sep)             # on the DP row
    hits = find_synapses_3d(vol, um, antibodies={"Homer1": post, "Bassoon": pre})
    assert hits
    h = min(hits, key=lambda h: np.linalg.norm(np.array(h["centre"]) - (centre + n * sep / 2)))
    assert h["antibodies"]["Homer1"]["positive"] and h["antibodies"]["Bassoon"]["positive"]
    gap = abs(alignment(h, "Homer1", "Bassoon"))
    assert abs(gap - sep * um) < 1.0 * um


def test_empty_block_gives_nothing():
    assert find_synapses_3d(np.zeros((32, 32, 32), np.uint16), 0.2) == []
