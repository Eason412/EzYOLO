# -*- coding: utf-8 -*-
"""标注画布的 wheel / 原生手势 / PinchGesture 入口。

    python tests/test_annotate_gestures.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys

from PyQt6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import (  # noqa: E402
    QColor, QNativeGestureEvent, QPointingDevice, QPixmap, QWheelEvent,
)
from PyQt6.QtWidgets import QApplication, QGestureEvent, QPinchGesture  # noqa: E402

from gui.pages.annotate_page import AnnotationCanvas  # noqa: E402

_app = _bootstrap.app()


def _settle():
    for _ in range(2):
        _app.processEvents()


def _canvas(with_image=True):
    canvas = AnnotationCanvas()
    canvas.resize(420, 320)
    canvas.show()
    _settle()
    if with_image:
        image = QPixmap(800, 600)
        image.fill(QColor(120, 120, 130))
        canvas.current_image = image
        canvas.image_scale = 1.0
        canvas.image_offset = QPoint(20, 15)
    return canvas


def _wheel_event(canvas, angle_delta=0, pixel_delta=0, local=QPoint(100, 80)):
    position = QPointF(local)
    global_position = QPointF(canvas.mapToGlobal(local))
    return QWheelEvent(
        position,
        global_position,
        QPoint(0, pixel_delta),
        QPoint(0, angle_delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )


def _native_event(canvas, gesture_type, value=0.0, local=QPoint(100, 80)):
    position = QPointF(local)
    global_position = QPointF(canvas.mapToGlobal(local))
    return QNativeGestureEvent(
        gesture_type,
        QPointingDevice.primaryPointingDevice(),
        0,
        position,
        position,
        global_position,
        value,
        QPointF(),
        1,
    )


def _pinch_event(canvas, scale_factor, local=QPoint(100, 80)):
    pinch = QPinchGesture()
    pinch.setScaleFactor(scale_factor)
    pinch.setCenterPoint(QPointF(canvas.mapToGlobal(local)))
    return QGestureEvent([pinch]), pinch


def _send_pinch(canvas, event):
    """macOS 运行时不 grab PinchGesture；合成事件直接交给覆盖的 event。"""
    if canvas.pinch_gesture_registered:
        return QApplication.sendEvent(canvas, event)
    return canvas.event(event)


def _image_point(canvas, local):
    return canvas.widget_to_image(local.x(), local.y())


def test_wheel_angle_pixel_and_zero_delta_use_one_zoom_path():
    canvas = _canvas()
    local = QPoint(100, 80)
    before = _image_point(canvas, local)

    angle = _wheel_event(canvas, angle_delta=120, pixel_delta=-12, local=local)
    assert QApplication.sendEvent(canvas, angle)
    assert angle.isAccepted()
    assert canvas.image_scale == 1.1, "angleDelta 应优先于相反方向的 pixelDelta"
    after = _image_point(canvas, local)
    assert abs(after[0] - before[0]) <= 1 and abs(after[1] - before[1]) <= 1

    pixel = _wheel_event(canvas, pixel_delta=-12, local=local)
    assert QApplication.sendEvent(canvas, pixel)
    assert pixel.isAccepted()
    assert canvas.image_scale < 1.1, "angleDelta 为零时 pixelDelta 负方向没有缩小"

    before_zero = (canvas.image_scale, QPoint(canvas.image_offset))
    zero = _wheel_event(canvas, local=local)
    assert QApplication.sendEvent(canvas, zero)
    assert zero.isAccepted()
    assert (canvas.image_scale, canvas.image_offset) == before_zero, "零 delta 不该改视图"
    canvas.close()


def test_native_zoom_begin_end_and_other_types_have_the_expected_handling():
    canvas = _canvas()
    local = QPoint(130, 90)
    before = _image_point(canvas, local)

    zoom_in = _native_event(canvas, Qt.NativeGestureType.ZoomNativeGesture, 0.25, local)
    zoom_in.ignore()
    assert QApplication.sendEvent(canvas, zoom_in)
    assert zoom_in.isAccepted()
    assert canvas.image_scale == 1.25
    after = _image_point(canvas, local)
    assert abs(after[0] - before[0]) <= 1 and abs(after[1] - before[1]) <= 1

    scale_after_zoom = canvas.image_scale
    zoom_out = _native_event(canvas, Qt.NativeGestureType.ZoomNativeGesture, -0.2, local)
    zoom_out.ignore()
    assert QApplication.sendEvent(canvas, zoom_out)
    assert zoom_out.isAccepted()
    assert canvas.image_scale < scale_after_zoom

    before_boundary = (canvas.image_scale, QPoint(canvas.image_offset))
    for gesture_type in (
        Qt.NativeGestureType.BeginNativeGesture,
        Qt.NativeGestureType.EndNativeGesture,
    ):
        boundary = _native_event(canvas, gesture_type, local=local)
        boundary.ignore()
        assert QApplication.sendEvent(canvas, boundary)
        assert boundary.isAccepted()
        assert (canvas.image_scale, canvas.image_offset) == before_boundary

    other = _native_event(canvas, Qt.NativeGestureType.PanNativeGesture, local=local)
    other.ignore()
    assert not QApplication.sendEvent(canvas, other), "非缩放 NativeGesture 不该由画布吞掉"
    assert not other.isAccepted(), "非缩放 NativeGesture 应交给 Qt 父类处理"
    canvas.close()


def test_pinch_gesture_uses_global_center_as_a_canvas_anchor():
    canvas = _canvas()
    local = QPoint(160, 100)
    before = _image_point(canvas, local)

    event, pinch = _pinch_event(canvas, 1.2, local)
    event.ignore()
    assert _send_pinch(canvas, event)
    assert event.isAccepted(pinch)
    assert canvas.image_scale == 1.2
    after = _image_point(canvas, local)
    assert abs(after[0] - before[0]) <= 1 and abs(after[1] - before[1]) <= 1

    outside_event, outside_pinch = _pinch_event(canvas, 1.1, QPoint(-20, -20))
    outside_event.ignore()
    before_center = _image_point(canvas, canvas.rect().center())
    assert _send_pinch(canvas, outside_event)
    assert outside_event.isAccepted(outside_pinch)
    after_center = _image_point(canvas, canvas.rect().center())
    assert abs(after_center[0] - before_center[0]) <= 1
    assert abs(after_center[1] - before_center[1]) <= 1
    canvas.close()


def test_no_image_events_are_accepted_without_changing_view():
    canvas = _canvas(with_image=False)
    wheel = _wheel_event(canvas, pixel_delta=12)
    native = _native_event(canvas, Qt.NativeGestureType.ZoomNativeGesture, 0.2)
    pinch_event, pinch = _pinch_event(canvas, 1.2)
    native.ignore()
    pinch_event.ignore()

    assert QApplication.sendEvent(canvas, wheel) and wheel.isAccepted()
    assert QApplication.sendEvent(canvas, native) and native.isAccepted()
    assert _send_pinch(canvas, pinch_event) and pinch_event.isAccepted(pinch)
    assert canvas.image_scale == 1.0
    assert canvas.image_offset == QPoint(0, 0)
    canvas.close()


def test_pinch_registration_matches_the_platform_without_double_zoom_on_macos():
    canvas = _canvas()
    accepts_touch = canvas.testAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
    if sys.platform == 'darwin':
        assert not canvas.pinch_gesture_registered
        assert not accepts_touch, "macOS 不应注册 Qt PinchGesture，以免与 NativeGesture 双缩放"
    else:
        assert canvas.pinch_gesture_registered
        assert accepts_touch
    canvas.close()


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
