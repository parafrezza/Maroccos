from GUI.services.time_sync import compute_offset_delay


def test_compute_offset_delay_zero_offset():
    # t0 -> t1 = 500 ns; t2 -> t3 = 500 ns => offset 0; delay 1000 ns
    t0, t1, t2, t3 = 1_000_000, 1_500_000, 1_600_000, 2_100_000
    off, dly = compute_offset_delay(t0, t1, t2, t3)
    assert off == 0
    assert dly == 1_000_000


def test_compute_offset_delay_positive_offset():
    # Server ahead by +200 ns approx
    t0, t1, t2, t3 = 1_000_000, 1_700_000, 1_800_000, 2_400_000
    off, dly = compute_offset_delay(t0, t1, t2, t3)
    assert off > 0
    assert dly >= 0

