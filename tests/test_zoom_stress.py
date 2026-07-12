# -*- coding: utf-8 -*-
"""连续缩放事件的轻量回归护栏。

    python tests/test_zoom_stress.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys
import time

from PyQt6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import (  # noqa: E402
    QColor, QNativeGestureEvent, QPointingDevice, QPixmap,
)
from PyQt6.QtWidgets import QApplication  # noqa: E402

from gui.pages.annotate_page import AnnotationCanvas  # noqa: E402
from gui.view_zoom import ZOOM_MAX, ZOOM_MIN  # noqa: E402

_app = _bootstrap.app()


def _native_event(canvas, local, value):
    position = QPointF(local)
    return QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        QPointingDevice.primaryPointingDevice(),
        0,
        position,
        position,
        QPointF(canvas.mapToGlobal(local)),
        value,
        QPointF(),
        1,
    )


def test_600_continuous_zoom_events_stay_bounded_anchored_and_single_refresh():
    """回归护栏，不是机器性能基准。"""
    canvas = AnnotationCanvas()
    canvas.resize(420, 320)
    canvas.show()
    _app.processEvents()
    image = QPixmap(800, 600)
    image.fill(QColor(120, 120, 130))
    canvas.current_image = image
    canvas.image_scale = 1.0
    canvas.image_offset = QPoint(20, 15)

    updates = []
    canvas.update = lambda *args: updates.append(args)
    anchor = QPoint(170, 110)
    original_image_point = canvas.widget_to_image(anchor.x(), anchor.y())

    started = time.perf_counter()
    for index in range(600):
        value = 0.01 if index % 2 == 0 else -(1 - 1 / 1.01)
        native = _native_event(canvas, anchor, value)
        native.ignore()
        before_updates = len(updates)
        assert QApplication.sendEvent(canvas, native)
        assert len(updates) - before_updates <= 1, "一个 native 事件触发了多次 update"
        assert ZOOM_MIN <= canvas.image_scale <= ZOOM_MAX
    elapsed = time.perf_counter() - started

    final_image_point = canvas.widget_to_image(anchor.x(), anchor.y())
    drift = max(
        abs(final_image_point[0] - original_image_point[0]),
        abs(final_image_point[1] - original_image_point[1]),
    )
    assert drift <= 3, f"600 次连续缩放后锚点漂移 {drift:.2f}px"
    assert elapsed < 2.0, f"600 个连续事件花了 {elapsed:.3f}s"
    print(f"600 个 native 缩放事件: {elapsed:.3f}s，锚点漂移 {drift:.2f}px")
    canvas.close()


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
