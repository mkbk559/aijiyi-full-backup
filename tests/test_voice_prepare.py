"""Voice dataset interval selection regressions."""

from __future__ import annotations

from tools.voice_prepare import choose_clips


def test_short_intervals_are_skipped_and_long_intervals_are_split():
    clips = choose_clips(
        [(0.0, 1.0), (10.0, 18.0), (20.0, 45.0)],
        min_sec=3.0,
        max_sec=10.0,
        pad_sec=0.0,
    )
    assert clips == [
        (10.0, 18.0), (20.0, 30.0), (30.0, 40.0), (40.0, 45.0),
    ]


def test_padding_is_applied_but_reference_can_still_be_short():
    clips = choose_clips(
        [(5.0, 8.2)],
        min_sec=3.0,
        max_sec=10.0,
        pad_sec=0.15,
    )
    assert clips == [(4.85, 8.35)]
