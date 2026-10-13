import numpy as np

from ebq.profile import DEFAULT, measure_profile, two_peaks


def test_reads_known_separation():
    um = 0.203
    t = np.arange(-25, 25.25, 0.5)
    truth = 8.0                                          # px -> 8 * 203 / 81.9 = 19.8x
    profile = two_peaks(t, 100, 200, 0.0, 1.5, 120, truth, 1.5)
    out = measure_profile(t, profile, profile, um, DEFAULT)
    assert out is not None
    assert abs(out["exf"] - truth * um * 1000 / 81.9) < 0.3
    assert abs(out["exf_fit"] - out["exf"]) < 0.3
    assert out["r2"] > 0.99


def test_rejects_single_peak():
    um = 0.203
    t = np.arange(-25, 25.25, 0.5)
    profile = two_peaks(t, 100, 200, 0.0, 1.5, 0, 8.0, 1.5)
    assert measure_profile(t, profile, profile, um, DEFAULT) is None


def test_rejects_outside_search_window():
    um = 0.203
    t = np.arange(-25, 25.25, 0.5)
    profile = two_peaks(t, 100, 200, 0.0, 1.5, 120, 16.0, 1.5)   # 16 px = 40x: outside 12-30x
    assert measure_profile(t, profile, profile, um, DEFAULT) is None
