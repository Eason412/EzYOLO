"""标注画布缩放的纯数学核心，不依赖 Qt。"""

import math


ZOOM_MIN = 0.1
ZOOM_MAX = 5.0


def _is_finite(value) -> bool:
    try:
        return math.isfinite(value)
    except (TypeError, ValueError):
        return False


def wheel_zoom_factor(delta):
    """把滚轮方向转换为固定缩放倍率。"""
    if not _is_finite(delta):
        return 1.0
    if delta > 0:
        return 1.1
    if delta < 0:
        return 0.9
    return 1.0


def native_zoom_factor(value):
    """macOS 原生捏合手势的增量倍率。"""
    return 1.0 + value if _is_finite(value) else 1.0


def pinch_zoom_factor(scale_factor):
    """Qt PinchGesture 给出的有效增量倍率。"""
    if _is_finite(scale_factor) and scale_factor > 0:
        return scale_factor
    return 1.0


def zoom_at(scale, offset_x, offset_y, anchor_x, anchor_y, factor, bounds):
    """按倍率缩放，并让锚点继续对应缩放前的同一图像坐标。

    输入不完整、非有限或没有实际缩放时原样返回，事件层可据此安全地不刷新画布。
    """
    original = (scale, offset_x, offset_y)
    values = (scale, offset_x, offset_y, anchor_x, anchor_y, factor)
    if not all(_is_finite(value) for value in values):
        return original
    if scale <= 0 or factor <= 0 or factor == 1:
        return original

    try:
        minimum, maximum = bounds
    except (TypeError, ValueError):
        return original
    if not _is_finite(minimum) or not _is_finite(maximum):
        return original
    if minimum <= 0 or maximum < minimum:
        return original

    new_scale = min(max(scale * factor, minimum), maximum)
    if new_scale == scale:
        return original

    image_x = (anchor_x - offset_x) / scale
    image_y = (anchor_y - offset_y) / scale
    return (
        float(new_scale),
        float(anchor_x - image_x * new_scale),
        float(anchor_y - image_y * new_scale),
    )
