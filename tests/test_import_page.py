# -*- coding: utf-8 -*-
"""导入页交互连续性测试。

覆盖的真实问题：
    1. 点了导入之后，一个事件循环内必须看到明确状态（不是干等一个空进度条）。
    2. 导入任务状态和缩略图加载状态是两套东西，互相不能把对方隐藏掉——
       切到别的页面再回来（refresh_project_images）、缩略图加载完成，
       都不该让还在跑的导入状态条消失。
    3. 图片 / 文件夹导入的实际工作必须在后台线程，不能卡住调用者（GUI 主线程）。
    4. 任务结束（成功/取消/失败）后必须恢复被禁用的按钮。
    5. 取消按钮要真的能喊停后台线程。

运行：
    python -m pytest tests/test_import_page.py -q
    或
    python tests/test_import_page.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtGui import QImage, QColor
from PyQt6.QtWidgets import QMessageBox, QInputDialog

from gui.pages.import_page import ImportPage
from gui.main_window import MainWindow
from gui.workflow import PAGE_SETTINGS, STEP_IMPORT
from core.import_manager import ImportManager

_app = _bootstrap.app()
db = _bootstrap.db

_TMP_DIR = Path(tempfile.mkdtemp(prefix="ezyolo-import-page-"))


class _SilentMessageBox:
    """吞掉成功/失败弹窗，测试不该被模态框卡住。"""

    StandardButton = QMessageBox.StandardButton
    Icon = QMessageBox.Icon

    @staticmethod
    def information(*_args, **_kwargs):
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def warning(*_args, **_kwargs):
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def critical(*_args, **_kwargs):
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def question(*_args, **_kwargs):
        return QMessageBox.StandardButton.Yes


def _make_project() -> int:
    return _bootstrap.create_temp_project(name="导入页测试项目", project_type="detect", classes=[])


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


def _make_video(path: Path, frame_count: int = 30, size=(64, 48)) -> Path:
    import cv2
    import numpy as np

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, 10.0, size)
    for i in range(frame_count):
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        frame[:] = (i * 5 % 255, 0, 0)
        writer.write(frame)
    writer.release()
    return path


def _pump_until(predicate, timeout=10.0):
    """跑事件循环直到条件成立或超时，返回是否成立。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _app.processEvents()
        if predicate():
            return True
    return False


def _wait_import_done(page):
    ok = _pump_until(lambda: not page._import_busy)
    assert ok, "导入任务没有在超时时间内收尾"


def test_video_import_shows_busy_state_within_same_call():
    """确认导入后，一个事件循环内（同一次调用里）就要看到明确状态。"""
    project_id = _make_project()
    page = ImportPage()
    page.set_project(project_id)

    video_path = _make_video(_TMP_DIR / "busy.mp4", frame_count=40)

    with patch("gui.pages.import_page.QMessageBox", _SilentMessageBox), \
         patch.object(QInputDialog, "getInt", return_value=(5, True)):
        page.process_video_import(str(video_path), group_id=None)

        # 不经过任何 processEvents，直接检查：必须已经是「导入中」状态
        assert page._import_busy is True
        assert page.import_status_frame.isHidden() is False
        assert page.import_progress_bar.maximum() == 0, "总帧数还不知道时应该是忙碌态（indeterminate）"
        assert "打开视频" in page.import_status_label.text()
        assert page.btn_cancel_import.isHidden() is False

        for button in (
            page.btn_import_folder, page.btn_import_images,
            page.btn_import_video, page.btn_import_annotations,
            page.btn_delete_project, page.btn_clear,
        ):
            assert not button.isEnabled(), f"{button.text()} 导入中应该被禁用"

        _wait_import_done(page)

    assert len(page.images) > 0
    for button in (page.btn_import_folder, page.btn_import_video, page.btn_delete_project):
        assert button.isEnabled(), "导入结束后按钮应该恢复可用"


def test_images_import_shows_determinate_progress_immediately():
    """已知总量（选好的图片列表）应该立刻是确定进度，不是转圈忙碌态。"""
    project_id = _make_project()
    page = ImportPage()
    page.set_project(project_id)

    file_paths = _make_images(_TMP_DIR / "determinate", 5)

    with patch("gui.pages.import_page.QMessageBox", _SilentMessageBox):
        page.process_image_import(file_paths, group_id=None)

        assert page._import_busy is True
        assert page.import_progress_bar.maximum() == 100, "已知总量应该切到确定进度"
        assert "0/5" in page.import_status_label.text()

        _wait_import_done(page)

    assert len(page.images) == 5


def test_page_switch_refresh_does_not_hide_active_import():
    """切到别的页面再回来时会调 refresh_project_images，不该把导入状态藏起来。"""
    project_id = _make_project()
    page = ImportPage()
    page.set_project(project_id)

    page._start_import_ui("正在准备导入: demo.mp4")
    assert page.import_status_frame.isHidden() is False

    # 主窗口从别的页面切回导入页时调用的正是这个方法
    page.refresh_project_images()

    assert page.import_status_frame.isHidden() is False, "切页刷新不该隐藏正在进行的导入状态"
    assert page._import_busy is True

    # 清理，避免影响后面的测试
    page._end_import_ui()


def test_real_main_window_settings_round_trip_keeps_import_status():
    """复现用户路径：导入中点设置，再点回第 1 步，状态和文字仍在。"""
    project_id = _make_project()
    window = MainWindow()
    window.load_projects(select_id=project_id)
    window.show()
    _app.processEvents()
    page = window.import_page

    page._start_import_ui("正在打开视频: demo.mp4")

    window.switch_page(PAGE_SETTINGS)
    window.switch_page(STEP_IMPORT)
    _app.processEvents()

    assert page._import_busy is True
    assert page.import_status_frame.isVisible(), "从设置返回后导入状态条消失了"
    assert "demo.mp4" in page.import_status_label.text(), "返回后丢失了当前任务说明"

    page._end_import_ui()
    window.close()
    db.delete_project(project_id)


def test_thumbnail_load_finish_does_not_hide_active_import():
    """缩略图加载完成（on_load_finished）不该把导入状态条带下去。"""
    project_id = _make_project()
    image_paths = _make_images(_TMP_DIR / "thumb_independent", 3)
    for path in image_paths:
        db.add_image(project_id, Path(path).name, path, width=32, height=32)

    page = ImportPage()
    page.set_project(project_id)
    _pump_until(lambda: page.load_worker is None)  # 等 set_project 触发的初始缩略图加载先跑完

    page._start_import_ui("正在导入其它内容…")
    assert page.import_status_frame.isHidden() is False

    # 触发一次独立的缩略图刷新（不是通过 load_project_images，模拟纯缩略图加载场景）
    page.force_refresh_images()
    ok = _pump_until(lambda: page.load_worker is None)
    assert ok, "缩略图加载线程没有在超时时间内结束"

    assert page.import_status_frame.isHidden() is False, "缩略图加载完成不该隐藏导入状态"
    assert page._import_busy is True

    page._end_import_ui()


def test_cancel_button_stops_active_thread():
    """点取消按钮要真的调用到后台线程的 cancel()。"""
    project_id = _make_project()
    page = ImportPage()
    page.set_project(project_id)

    calls = []

    class _FakeThread:
        def cancel(self):
            calls.append("cancelled")

    page._active_import_thread = _FakeThread()
    page._start_import_ui("正在导入…")

    page._cancel_active_import()

    assert calls == ["cancelled"]
    assert not page.btn_cancel_import.isEnabled()
    assert "取消" in page.import_status_label.text()

    page._end_import_ui()


def test_folder_and_images_import_do_not_block_caller_thread():
    """process_folder_import / process_image_import 必须立刻返回，实际工作在后台线程。"""
    project_id = _make_project()
    page = ImportPage()
    page.set_project(project_id)

    folder = _TMP_DIR / "slow_folder"
    _make_images(folder, 3)

    real_import_folder = ImportManager.import_folder

    def slow_import_folder(self, *args, **kwargs):
        time.sleep(0.5)
        return real_import_folder(self, *args, **kwargs)

    with patch("gui.pages.import_page.QMessageBox", _SilentMessageBox), \
         patch.object(ImportManager, "import_folder", slow_import_folder):
        start = time.monotonic()
        page.process_folder_import(str(folder), group_id=None)
        elapsed = time.monotonic() - start

        assert elapsed < 0.2, f"process_folder_import 阻塞了调用者线程: {elapsed:.3f}s"

        _wait_import_done(page)

    assert len(page.images) == 3


def test_unknown_total_frames_video_import_completes_without_crash():
    """总帧数未知的视频也要能正常导入完，不因为除零而崩掉（GUI 层串联真实场景）。"""
    project_id = _make_project()
    page = ImportPage()
    page.set_project(project_id)

    video_path = _make_video(_TMP_DIR / "unknown_gui.mp4", frame_count=20)

    import cv2
    real_video_capture = cv2.VideoCapture

    class _FakeCap:
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

    with patch("gui.pages.import_page.QMessageBox", _SilentMessageBox), \
         patch.object(QInputDialog, "getInt", return_value=(5, True)), \
         patch("core.import_manager.cv2.VideoCapture", lambda p: _FakeCap(p)):
        page.process_video_import(str(video_path), group_id=None)
        assert page.import_progress_bar.maximum() == 0
        _wait_import_done(page)

    assert len(page.images) > 0


def test_project_switch_keeps_old_thread_alive_until_it_actually_finishes():
    """切换项目时取消旧导入：QThread 对象必须活到它真正退出为止，
    不能在还在运行时就丢掉引用（否则触发 "QThread: Destroyed while thread
    is still running"）。旧线程收尾后要真正释放，且不能污染新项目的数据。
    """
    project_a = _make_project()
    project_b = _make_project()
    page = ImportPage()
    page.set_project(project_a)

    folder = _TMP_DIR / "switch_slow_worker"
    _make_images(folder, 5)

    real_import_folder = ImportManager.import_folder

    def slow_import_folder(self, *args, **kwargs):
        # 真实导入很快就跑完，额外多睡一会儿，确保切项目那一刻线程仍在运行
        result = real_import_folder(self, *args, **kwargs)
        time.sleep(0.3)
        return result

    with patch("gui.pages.import_page.QMessageBox", _SilentMessageBox), \
         patch.object(ImportManager, "import_folder", slow_import_folder):
        page.process_folder_import(str(folder), group_id=None)
        old_thread = page._active_import_thread
        assert old_thread is not None

        page.set_project(project_b)

        # 切换后：不再是「当前活跃」导入，但对象必须还活着、还在跑，
        # 不能被提前 GC / deleteLater
        assert page._active_import_thread is None
        assert old_thread in page._retired_import_threads, "旧线程应该被继续持有，直到它真正退出"
        assert old_thread.isRunning() is True, "此刻旧线程应该还没跑完（被 mock 多睡了 0.3s）"
        assert page._import_busy is False

        ok = _pump_until(lambda: old_thread not in page._retired_import_threads, timeout=5.0)
        assert ok, "旧线程没有在超时时间内被正确收尾"

    assert old_thread.isRunning() is False, "旧线程收尾后必须真的已经退出"
    assert page.current_project_id == project_b
    assert page.images == [], "旧项目的导入结果不能污染新项目"


def test_normal_cancel_and_failure_paths_all_release_thread_reference():
    """正常完成 / 失败 / 取消三条路径，导入收尾后都不能残留线程引用（不泄漏）。"""
    project_id = _make_project()
    page = ImportPage()
    page.set_project(project_id)

    # 路径一：正常完成
    file_paths = _make_images(_TMP_DIR / "release_normal", 3)
    with patch("gui.pages.import_page.QMessageBox", _SilentMessageBox):
        page.process_image_import(file_paths, group_id=None)
        _wait_import_done(page)
        ok = _pump_until(lambda: page._active_import_thread is None and not page._retired_import_threads)
        assert ok, "正常完成后线程引用没有被释放"
    assert page._active_import_thread is None
    assert page._retired_import_threads == []

    # 路径二：失败（后台抛异常）
    with patch("gui.pages.import_page.QMessageBox", _SilentMessageBox), \
         patch.object(ImportManager, "import_images", side_effect=RuntimeError("boom")):
        page.process_image_import(file_paths, group_id=None)
        _wait_import_done(page)
        ok = _pump_until(lambda: page._active_import_thread is None and not page._retired_import_threads)
        assert ok, "失败后线程引用没有被释放"
    assert page._active_import_thread is None
    assert page._retired_import_threads == []

    # 路径三：取消
    video_path = _make_video(_TMP_DIR / "release_cancel.mp4", frame_count=40)
    with patch("gui.pages.import_page.QMessageBox", _SilentMessageBox), \
         patch.object(QInputDialog, "getInt", return_value=(1, True)):
        page.process_video_import(str(video_path), group_id=None)
        page._cancel_active_import()
        _wait_import_done(page)
        ok = _pump_until(lambda: page._active_import_thread is None and not page._retired_import_threads)
        assert ok, "取消后线程引用没有被释放"
    assert page._active_import_thread is None
    assert page._retired_import_threads == []


def test_cancelled_import_progress_bar_is_not_shown_as_full_completion():
    """取消导入后，页面进度条和 summary 不应该被显示成「100% 已完成」。"""
    project_id = _make_project()
    page = ImportPage()
    page.set_project(project_id)

    video_path = _make_video(_TMP_DIR / "cancel_ui_progress.mp4", frame_count=40)

    import cv2
    real_video_capture = cv2.VideoCapture

    class _SlowCap:
        """跟真实 VideoCapture 行为一致，只是每帧多睡一点，留出取消的窗口。"""

        def __init__(self, path):
            self._real = real_video_capture(path)

        def isOpened(self):
            return self._real.isOpened()

        def get(self, prop):
            return self._real.get(prop)

        def read(self):
            time.sleep(0.03)
            return self._real.read()

        def release(self):
            self._real.release()

    with patch("gui.pages.import_page.QMessageBox", _SilentMessageBox), \
         patch.object(QInputDialog, "getInt", return_value=(1, True)), \
         patch("core.import_manager.cv2.VideoCapture", lambda p: _SlowCap(p)):
        page.process_video_import(str(video_path), group_id=None)

        ok = _pump_until(lambda: page.import_progress_bar.value() > 0, timeout=5.0)
        assert ok, "没能观察到真实进度，取消窗口没抓住"

        page._cancel_active_import()
        _wait_import_done(page)

    assert "已取消" in page.import_status_label.text()
    assert page.import_progress_bar.value() < 100, "取消后不应该把进度条显示成 100% 完成"
    assert len(page.images) < 40, "取消应该在导完全部帧之前生效"


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
