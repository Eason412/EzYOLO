# -*- coding: utf-8 -*-
"""core.import_manager 的取消 / 进度契约测试。

只测纯逻辑（不起 GUI 线程），用临时项目 + 临时文件，不碰真实 data/projects/outputs。

运行：
    python -m pytest tests/test_import_manager.py -q
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PyQt6.QtGui import QImage, QColor

from core.import_manager import ImportManager

db = _bootstrap.db

_TMP_DIR = Path(tempfile.mkdtemp(prefix="ezyolo-import-manager-"))


def _make_project() -> int:
    return _bootstrap.create_temp_project(name="导入管理器测试项目", project_type="detect", classes=[])


def _make_images(folder: Path, count: int) -> list:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        path = folder / f"img_{i:03d}.jpg"
        image = QImage(32, 32, QImage.Format.Format_RGB888)
        image.fill(QColor(10 * i % 255, 20, 30))
        image.save(str(path))
        paths.append(str(path))
    return paths


def _make_video(path: Path, frame_count: int = 15, size=(64, 48)) -> Path:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, 10.0, size)
    for i in range(frame_count):
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        frame[:] = (i * 10 % 255, 0, 0)
        writer.write(frame)
    writer.release()
    return path


def test_import_folder_reports_scan_busy_state_before_total_known():
    """扫描阶段（还不知道总数）必须先给一个忙碌态回调，而不是干等。"""
    project_id = _make_project()
    folder = _TMP_DIR / "folder_scan"
    _make_images(folder, 3)

    manager = ImportManager(project_id)
    calls = []
    manager.import_folder(str(folder), progress_callback=lambda p, m: calls.append((p, m)))

    assert calls, "没有任何进度回调"
    first_progress, first_message = calls[0]
    assert first_progress == -1, "扫描阶段应该是总量未知的忙碌态（-1），不是具体百分比"
    assert "扫描" in first_message


def test_import_folder_cancel_stops_before_all_files_done():
    """取消后最迟在下一张图片边界停止，不会把剩下的也导入。"""
    project_id = _make_project()
    folder = _TMP_DIR / "folder_cancel"
    _make_images(folder, 10)

    manager = ImportManager(project_id)
    cancel_event = threading.Event()
    seen = []

    def progress_callback(progress, message):
        seen.append((progress, message))
        # 处理完第 2 张之后请求取消
        if len(seen) == 3:
            cancel_event.set()

    imported, skipped = manager.import_folder(
        str(folder), progress_callback=progress_callback, cancel_event=cancel_event
    )

    assert imported < 10, f"取消没有生效，全部 10 张都导入了: imported={imported}"
    assert imported > 0, "取消发生前已经成功导入的部分应该保留"
    final_message = seen[-1][1]
    assert "已取消" in final_message


def test_import_images_cancel_stops_before_all_files_done():
    """import_images 的取消行为跟 import_folder 一致。"""
    project_id = _make_project()
    folder = _TMP_DIR / "images_cancel"
    file_paths = _make_images(folder, 8)

    manager = ImportManager(project_id)
    cancel_event = threading.Event()
    seen = []

    def progress_callback(progress, message):
        seen.append((progress, message))
        if len(seen) == 2:
            cancel_event.set()

    imported, skipped = manager.import_images(
        file_paths, progress_callback=progress_callback, cancel_event=cancel_event
    )

    assert imported < 8
    assert imported > 0
    assert "已取消" in seen[-1][1]


def test_import_video_reports_open_and_metadata_before_extraction():
    """视频导入：先报「正在打开」，拿到元数据后再报总帧数/间隔/预计抽取数。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "meta.mp4", frame_count=15)

    manager = ImportManager(project_id)
    calls = []
    manager.import_video(
        str(video_path), frame_interval=5,
        progress_callback=lambda p, m: calls.append((p, m)),
    )

    assert len(calls) >= 2
    open_progress, open_message = calls[0]
    assert open_progress == -1
    assert "正在打开视频" in open_message
    assert video_path.name in open_message

    meta_progress, meta_message = calls[1]
    assert "帧" in meta_message
    assert "间隔 5" in meta_message
    assert "预计抽取" in meta_message


def test_import_video_unknown_total_frames_does_not_divide_by_zero():
    """总帧数拿不到（0 或负数）时不能除零，且要退化成忙碌态。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "unknown_total.mp4", frame_count=6)

    real_video_capture = cv2.VideoCapture

    class _FakeCap:
        """包一层真的 VideoCapture，只是把 FRAME_COUNT 汇报成 0。"""

        def __init__(self, path):
            self._real = real_video_capture(path)

        def isOpened(self):
            return self._real.isOpened()

        def get(self, prop):
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return 0
            return self._real.get(prop)

        def read(self):
            return self._real.read()

        def release(self):
            self._real.release()

    manager = ImportManager(project_id)
    calls = []

    with patch("core.import_manager.cv2.VideoCapture", lambda p: _FakeCap(p)):
        imported, skipped = manager.import_video(
            str(video_path), frame_interval=2,
            progress_callback=lambda p, m: calls.append((p, m)),
        )

    assert imported > 0, "总帧数未知也应该正常抽帧，不能因为除零而整个失败"
    # 抽帧阶段的进度必须全部是忙碌态（-1），不能算出一个假的百分比
    extraction_calls = calls[2:-1]  # 去掉「打开」「元数据」和最后一条「完成」
    assert extraction_calls, "没有抽帧阶段的进度回调"
    for progress, message in extraction_calls:
        assert progress == -1, f"总帧数未知时不应该出现具体百分比: {progress}"


def test_import_video_estimated_extraction_count_rounds_up():
    """预计抽取数应该向上取整：31 帧、间隔 30，第 0 帧和第 30 帧都会被抽到，预计 2 张。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "ceil_estimate.mp4", frame_count=31)

    manager = ImportManager(project_id)
    calls = []
    manager.import_video(
        str(video_path), frame_interval=30,
        progress_callback=lambda p, m: calls.append((p, m)),
    )

    meta_progress, meta_message = calls[1]
    assert "预计抽取 2 张" in meta_message, meta_message


def test_import_folder_cancelled_final_progress_is_not_misleading_full_completion():
    """取消后的最终进度应该停在中断时的真实位置，不能显示成 100% 已完成。"""
    project_id = _make_project()
    folder = _TMP_DIR / "folder_cancel_progress"
    _make_images(folder, 10)

    manager = ImportManager(project_id)
    cancel_event = threading.Event()
    seen = []

    def progress_callback(progress, message):
        seen.append((progress, message))
        if len(seen) == 3:
            cancel_event.set()

    manager.import_folder(str(folder), progress_callback=progress_callback, cancel_event=cancel_event)

    final_progress, final_message = seen[-1]
    assert "已取消" in final_message
    assert final_progress < 100, f"取消后不应该报告 100% 完成，实际: {final_progress}"


def test_import_video_cancelled_final_progress_is_not_misleading_full_completion():
    """视频抽帧取消时同理：最终进度不能是误导性的 100%。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "cancel_progress.mp4", frame_count=40)

    manager = ImportManager(project_id)
    cancel_event = threading.Event()
    seen = []

    def progress_callback(progress, message):
        seen.append((progress, message))
        if len(seen) == 3:
            cancel_event.set()

    manager.import_video(
        str(video_path), frame_interval=1,
        progress_callback=progress_callback, cancel_event=cancel_event,
    )

    final_progress, final_message = seen[-1]
    assert "已取消" in final_message
    assert final_progress < 100, f"取消后不应该报告 100% 完成，实际: {final_progress}"


def test_import_video_cancel_stops_before_reading_all_frames():
    """视频抽帧的取消：最迟在下一帧边界停止。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "cancel.mp4", frame_count=40)

    manager = ImportManager(project_id)
    cancel_event = threading.Event()
    seen = []

    def progress_callback(progress, message):
        seen.append((progress, message))
        if len(seen) == 3:
            cancel_event.set()

    imported, skipped = manager.import_video(
        str(video_path), frame_interval=1,
        progress_callback=progress_callback, cancel_event=cancel_event,
    )

    assert imported < 40
    assert "已取消" in seen[-1][1]


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
