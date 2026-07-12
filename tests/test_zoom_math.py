# -*- coding: utf-8 -*-
"""缩放数学：不导入 Qt，直接验证倍率、边界和锚点公式。

    python tests/test_zoom_math.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import math
import sys
import time

from gui.view_zoom import (  # noqa: E402
    ZOOM_MAX, ZOOM_MIN, native_zoom_factor, pinch_zoom_factor,
    wheel_zoom_factor, zoom_at,
)


def test_input_factors_are_safe_and_directional():
    assert wheel_zoom_factor(120) == 1.1
    assert wheel_zoom_factor(-1) == 0.9
    assert wheel_zoom_factor(0) == 1.0
    assert wheel_zoom_factor(float('nan')) == 1.0

    assert native_zoom_factor(0.25) == 1.25
    assert native_zoom_factor(float('inf')) == 1.0
    assert pinch_zoom_factor(1.2) == 1.2
    assert pinch_zoom_factor(0) == 1.0
    assert pinch_zoom_factor(-1) == 1.0
    assert pinch_zoom_factor(float('nan')) == 1.0


def test_zoom_at_maps_and_keeps_the_anchor_on_the_same_image_point():
    scale, offset_x, offset_y = 1.5, 37.0, -12.0
    anchor_x, anchor_y = 160.0, 90.0
    image_x = (anchor_x - offset_x) / scale
    image_y = (anchor_y - offset_y) / scale

    new_scale, new_offset_x, new_offset_y = zoom_at(
        scale, offset_x, offset_y, anchor_x, anchor_y, 1.2, (ZOOM_MIN, ZOOM_MAX)
    )

    assert math.isclose(new_scale, 1.8)
    assert math.isclose((anchor_x - new_offset_x) / new_scale, image_x)
    assert math.isclose((anchor_y - new_offset_y) / new_scale, image_y)


def test_zoom_at_clamps_without_moving_a_clamped_anchor():
    result = zoom_at(4.9, 10.0, 20.0, 100.0, 80.0, 2.0, (ZOOM_MIN, ZOOM_MAX))
    assert result[0] == ZOOM_MAX

    result = zoom_at(0.11, 10.0, 20.0, 100.0, 80.0, 0.1, (ZOOM_MIN, ZOOM_MAX))
    assert result[0] == ZOOM_MIN

    already_max = (ZOOM_MAX, 10.0, 20.0)
    assert zoom_at(*already_max, 100.0, 80.0, 1.1, (ZOOM_MIN, ZOOM_MAX)) == already_max


def test_zoom_at_rejects_invalid_and_noop_inputs_without_throwing():
    cases = (
        (1.0, 10.0, 20.0, 100.0, 80.0, 1.0, (ZOOM_MIN, ZOOM_MAX)),
        (1.0, 10.0, 20.0, 100.0, 80.0, 0.0, (ZOOM_MIN, ZOOM_MAX)),
        (0.0, 10.0, 20.0, 100.0, 80.0, 1.1, (ZOOM_MIN, ZOOM_MAX)),
        (1.0, 10.0, 20.0, 100.0, 80.0, float('nan'), (ZOOM_MIN, ZOOM_MAX)),
        (1.0, 10.0, 20.0, 100.0, 80.0, 1.1, (5.0, 0.1)),
    )
    for args in cases:
        assert zoom_at(*args) == args[:3]


def test_zoom_at_50000_calls_stays_fast_enough_for_continuous_input():
    """回归护栏，不是机器性能基准。"""
    started = time.perf_counter()
    scale, offset_x, offset_y = 1.0, 0.0, 0.0
    for index in range(50_000):
        factor = 1.01 if index % 2 == 0 else 1 / 1.01
        scale, offset_x, offset_y = zoom_at(
            scale, offset_x, offset_y, 180.0, 120.0, factor, (ZOOM_MIN, ZOOM_MAX)
        )
    elapsed = time.perf_counter() - started

    assert ZOOM_MIN <= scale <= ZOOM_MAX
    assert elapsed < 2.0, f"50000 次 zoom_at 花了 {elapsed:.3f}s"
    print(f"50000 次 zoom_at: {elapsed:.3f}s")


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
