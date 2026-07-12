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
from contextlib import ExitStack
from unittest.mock import patch

from PyQt6.QtGui import QImage, QColor
from PyQt6.QtWidgets import QFileDialog

from gui.pages.import_page import ImportPage, short_task_label
from gui.main_window import MainWindow
from gui.workflow import PAGE_SETTINGS, STEP_IMPORT
from core.import_manager import ImportManager

_app = _bootstrap.app()
db = _bootstrap.db

_TMP_DIR = Path(tempfile.mkdtemp(prefix="ezyolo-import-page-"))


def _silent_dialogs():
    """吞掉应用内弹窗（确认框 / 提示框），测试不该被模态框卡住。

    页面现在用的是 gui.widgets.app_dialog 里那几个函数，不再是 QMessageBox，
    所以要挡的是这几个名字。确认框一律当成「用户点了确认」。
    """
    stack = ExitStack()
    stack.enter_context(patch("gui.pages.import_page.show_info"))
    stack.enter_context(patch("gui.pages.import_page.show_warning"))
    stack.enter_context(patch("gui.pages.import_page.confirm", return_value=True))
    stack.enter_context(patch("gui.pages.import_page.confirm_destructive", return_value=True))
    return stack


def _video_plan(frame_interval=5, mode="interval", sample_count=None):
    """替掉抽帧设置框：直接返回一个抽帧方案，不弹窗。"""
    return patch(
        "gui.pages.import_page.ask_video_extract_plan",
        return_value={
            'mode': mode,
            'frame_interval': frame_interval,
            'sample_count': sample_count,
        },
    )


def _make_project() -> int:
    return _bootstrap.create_temp_project(name="导入页测试项目", project_type="detect", classes=[])


def _make_annotated_project() -> int:
    """一个已经有标注的项目：导入标注时才会问「要不要覆盖」。"""
    project_id = _make_project()
    image_path = _make_images(_TMP_DIR / f"annotated_{project_id}", 1)[0]
    image_id = db.add_image(
        project_id=project_id, filename=Path(image_path).name,
        storage_path=image_path, width=32, height=32,
    )
    db.add_annotation(
        image_id=image_id, project_id=project_id, class_id=0, class_name="人",
        annotation_type="rectangle", data={'x': 1, 'y': 1, 'width': 5, 'height': 5},
    )
    return project_id


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

    with _silent_dialogs(), \
         _video_plan(5):
        page.process_video_import(str(video_path), group_id=None)

        # 不经过任何 processEvents，直接检查：必须已经是「导入中」状态
        assert page._import_busy is True
        assert page.import_status_frame.isHidden() is False
        assert page.import_progress_bar.maximum() == 0, "总帧数还不知道时应该是忙碌态（indeterminate）"
        assert "打开视频" in page.import_status_label.text()
        assert page.btn_cancel_import.isHidden() is False

        # 会再起一个导入的、和会毁掉当前项目的，导入中都不能点。
        # 后两个现在住在「管理」菜单里，禁的是菜单项本身
        for control in (
            page.btn_import_folder, page.btn_import_images,
            page.btn_import_video, page.btn_import_annotations,
            page.action_delete_project, page.action_clear,
        ):
            assert not control.isEnabled(), f"{control.text()} 导入中应该被禁用"

        _wait_import_done(page)

    assert len(page.images) > 0
    for control in (page.btn_import_folder, page.btn_import_video, page.action_delete_project):
        assert control.isEnabled(), "导入结束后应该恢复可用"


def test_images_import_shows_determinate_progress_immediately():
    """已知总量（选好的图片列表）应该立刻是确定进度，不是转圈忙碌态。"""
    project_id = _make_project()
    page = ImportPage()
    page.set_project(project_id)

    file_paths = _make_images(_TMP_DIR / "determinate", 5)

    with _silent_dialogs():
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

    with _silent_dialogs(), \
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

    with _silent_dialogs(), \
         _video_plan(5), \
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

    with _silent_dialogs(), \
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
    with _silent_dialogs():
        page.process_image_import(file_paths, group_id=None)
        _wait_import_done(page)
        ok = _pump_until(lambda: page._active_import_thread is None and not page._retired_import_threads)
        assert ok, "正常完成后线程引用没有被释放"
    assert page._active_import_thread is None
    assert page._retired_import_threads == []

    # 路径二：失败（后台抛异常）
    with _silent_dialogs(), \
         patch.object(ImportManager, "import_images", side_effect=RuntimeError("boom")):
        page.process_image_import(file_paths, group_id=None)
        _wait_import_done(page)
        ok = _pump_until(lambda: page._active_import_thread is None and not page._retired_import_threads)
        assert ok, "失败后线程引用没有被释放"
    assert page._active_import_thread is None
    assert page._retired_import_threads == []

    # 路径三：取消
    video_path = _make_video(_TMP_DIR / "release_cancel.mp4", frame_count=40)
    with _silent_dialogs(), \
         _video_plan(1):
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

    with _silent_dialogs(), \
         _video_plan(1), \
         patch("core.import_manager.cv2.VideoCapture", lambda p: _SlowCap(p)):
        page.process_video_import(str(video_path), group_id=None)

        ok = _pump_until(lambda: page.import_progress_bar.value() > 0, timeout=5.0)
        assert ok, "没能观察到真实进度，取消窗口没抓住"

        page._cancel_active_import()
        _wait_import_done(page)

    assert "已取消" in page.import_status_label.text()
    assert page.import_progress_bar.value() < 100, "取消后不应该把进度条显示成 100% 完成"
    assert len(page.images) < 40, "取消应该在导完全部帧之前生效"


def test_new_project_uses_the_in_app_text_dialog_not_the_system_one():
    """新建项目不能再弹系统的 QInputDialog：那个框跟原来那个丑抽帧框是同一个模子。"""
    import gui.pages.import_page as import_page_module

    assert not hasattr(import_page_module, "QInputDialog"), \
        "导入页不该再依赖系统输入框"

    page = ImportPage()
    created = []
    page.projects_changed.connect(created.append)

    asked = {}

    def fake_ask_text(_parent, title, _label, **kwargs):
        asked['title'] = title
        asked['confirm_text'] = kwargs.get('confirm_text')
        return "新的安全帽项目"

    with patch("gui.pages.import_page.ask_text", fake_ask_text), \
         patch("gui.pages.import_page.ask_task_type", return_value="detect"):
        page.create_new_project()

    assert asked['title'] == "新建项目"
    assert asked['confirm_text'] == "创建项目", "确认按钮要说清楚它会干什么"

    assert len(created) == 1, "应该建出一个项目并通知主窗口"
    project = db.get_project(created[0])
    assert project['name'] == "新的安全帽项目"

    db.delete_project(created[0])


def test_new_project_is_not_created_when_the_name_dialog_is_cancelled():
    """取消起名字（ask_text 返回 None）就什么都不建。"""
    page = ImportPage()
    created = []
    page.projects_changed.connect(created.append)

    with patch("gui.pages.import_page.ask_text", return_value=None), \
         patch("gui.pages.import_page.ask_task_type", return_value="detect") as task_type:
        page.create_new_project()

    assert created == []
    assert not task_type.called, "名字都没起，不该继续问任务类型"


def test_annotation_import_confirmations_use_honest_button_labels():
    """导入标注这一路上的两个二选一，按钮要照实说，不能都叫「取消」。

    「不覆盖」实际是「保留现有标注」并继续导入，写「取消」会让人以为
    整个导入都放弃了；「不另选图像文件夹」是「不用」，也不是取消。
    """
    project_id = _make_annotated_project()
    page = ImportPage()
    page.set_project(project_id)

    labels_dir = _TMP_DIR / "yolo_labels"
    labels_dir.mkdir(parents=True, exist_ok=True)

    calls = []

    def record(_parent, title, _message, **kwargs):
        calls.append((title, kwargs.get('confirm_text'), kwargs.get('cancel_text')))
        return False  # 两个都选「安全的那一个」

    with _silent_dialogs(), \
         patch("gui.pages.import_page.confirm", record), \
         patch("gui.pages.import_page.confirm_destructive", record), \
         patch("gui.pages.import_page.ask_import_group", return_value=(True, None)), \
         patch.object(QFileDialog, "getExistingDirectory", return_value=str(labels_dir)):
        page.import_yolo_annotations(group_id=None)
        _pump_until(lambda: not hasattr(page, 'loading_overlay'), timeout=10.0)

    titles = {title: (confirm_text, cancel_text) for title, confirm_text, cancel_text in calls}

    assert "图像文件夹" in titles, calls
    assert titles["图像文件夹"] == ("去选择", "不用"), titles["图像文件夹"]

    assert "覆盖已有标注" in titles, calls
    assert titles["覆盖已有标注"] == ("覆盖", "保留现有标注"), titles["覆盖已有标注"]

    for _title, _confirm_text, cancel_text in calls:
        assert cancel_text != "取消", f"「{_title}」的安全按钮不该笼统叫「取消」"

    db.delete_project(project_id)


# ==================== 工具栏胶囊 / 管理菜单的状态 ====================

def _seed_images(project_id, count=3):
    for i in range(count):
        db.add_image(project_id, f"seed_{i}.jpg", f"/tmp/seed_{i}.jpg", width=32, height=32)


def test_task_chip_shows_the_short_chinese_label_only():
    """工具栏胶囊只写中文：「任务：目标检测」，不再拖一个 detect 的尾巴。

    共用的任务类型对话框仍然显示带英文的完整标签——那里 detect / segment
    要和 YOLO 的术语对得上，是有用的；在胶囊上它只是把宽度撑长。
    """
    from gui.widgets.task_type_dialog import task_type_label

    assert short_task_label('detect') == "目标检测"
    assert short_task_label('segment') == "实例分割"
    assert short_task_label(None) == "未设置"

    # 对话框那份标签没被改动
    assert task_type_label('detect') == "目标检测 detect"

    project_id = _make_project()
    page = ImportPage()
    page.set_project(project_id)

    assert page.btn_task_type.text() == "任务：目标检测", page.btn_task_type.text()

    db.delete_project(project_id)


def test_selection_actions_are_disabled_until_something_is_selected():
    """没选图片时「移动分组 / 删除选中」就该是灰的。

    以前它们一直亮着，点下去只弹一句「还没选图片」——用一个弹窗代替了
    本来一眼就该看出来的状态。
    """
    project_id = _make_project()
    _seed_images(project_id, 3)

    page = ImportPage()
    page.set_project(project_id)
    _app.processEvents()

    assert not page.action_move_group.isEnabled(), "没选图片，移动分组不该能点"
    assert not page.action_delete_selected.isEnabled(), "没选图片，删除选中不该能点"

    page.image_list.item(0).setSelected(True)
    _app.processEvents()

    assert page.action_move_group.isEnabled(), "选了图片就该能移动分组"
    assert page.action_delete_selected.isEnabled(), "选了图片就该能删除选中"

    page.image_list.clearSelection()
    _app.processEvents()

    assert not page.action_move_group.isEnabled(), "取消选中之后要变回灰的"
    assert not page.action_delete_selected.isEnabled()

    page.stop_image_loading()
    db.delete_project(project_id)


def test_menu_recomputes_state_when_it_opens():
    """菜单弹出前重算一次：选中状态可能是在菜单关着的时候变的。"""
    project_id = _make_project()
    _seed_images(project_id, 2)

    page = ImportPage()
    page.set_project(project_id)
    _app.processEvents()

    # 绕过信号，直接把选中状态做出来——模拟「菜单不知道的变化」
    page.action_move_group.setEnabled(False)
    page.image_list.item(0).setSelected(True)
    page.action_move_group.setEnabled(False)

    page.manage_menu.aboutToShow.emit()

    assert page.action_move_group.isEnabled(), "菜单打开时应该重算可用性"

    page.stop_image_loading()
    db.delete_project(project_id)


def test_clear_all_is_disabled_when_there_is_nothing_to_clear():
    """项目里没有图片时「清空全部图片」是灰的；有图片才亮。"""
    project_id = _make_project()

    page = ImportPage()
    page.set_project(project_id)
    _app.processEvents()

    assert not page.action_clear.isEnabled(), "没有图片就没有东西可清空"
    # 项目本身还是删得掉的
    assert page.action_delete_project.isEnabled()

    _seed_images(project_id, 2)
    page.load_project_images()
    _app.processEvents()

    assert page.action_clear.isEnabled(), "有图片了就该能清空"

    page.stop_image_loading()
    db.delete_project(project_id)


def test_import_busy_blocks_destruction_but_still_allows_moving_selected_images():
    """导入中：清空 / 删除项目一律关掉；已选中图片的移动、删除照常可用。

    正在往项目里写图片的时候不能把项目端了；但对已有图片的操作没有理由禁掉。
    """
    project_id = _make_project()
    _seed_images(project_id, 3)

    page = ImportPage()
    page.set_project(project_id)
    _app.processEvents()

    page.image_list.item(0).setSelected(True)
    _app.processEvents()

    page._start_import_ui("正在导入…")

    assert not page.action_clear.isEnabled(), "导入中不能清空图片"
    assert not page.action_delete_project.isEnabled(), "导入中不能删除项目"
    assert page.action_move_group.isEnabled(), "导入中仍然可以移动已选中的图片"
    assert page.action_delete_selected.isEnabled(), "导入中仍然可以删除已选中的图片"

    page._end_import_ui()
    _app.processEvents()

    assert page.action_clear.isEnabled(), "导入结束后要恢复"
    assert page.action_delete_project.isEnabled()

    page.stop_image_loading()
    db.delete_project(project_id)


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
