# -*- coding: utf-8 -*-
"""视频抽帧：元信息、两种抽法、抽帧设置框。

覆盖的真实问题：
    1. 原来只有 QInputDialog.getInt 问一个「间隔多少帧」，用户根本不知道最后能得到几张图。
       现在开抽之前先读元信息（只读属性，不解码、不导入任何一帧），并实时算出预计张数。
    2. 随机抽取要的是「不重复的 N 张」，而且必须按帧号升序导入——乱序就得来回 seek，
       很多容器 seek 不准。
    3. 总帧数读不到时：固定间隔照常能用，随机抽取必须禁掉（不知道有多少帧就没法随机取 N 张），
       而不是让它看着能点然后炸掉。
    4. 随机抽取一样能中途取消，且不会把进度条显示成 100% 完成。

运行：
    python tests/test_video_extract.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import random
import re
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from core.import_manager import (
    ImportManager, VIDEO_MODE_INTERVAL, VIDEO_MODE_RANDOM,
    probe_video_metadata, estimate_interval_frame_count, plan_random_frame_indices,
)
from gui.widgets.video_extract_dialog import (
    DIALOG_WIDTH, VideoExtractDialog, format_duration,
)

_app = _bootstrap.app()
db = _bootstrap.db

_TMP_DIR = Path(tempfile.mkdtemp(prefix="ezyolo-video-extract-"))


def _make_project() -> int:
    return _bootstrap.create_temp_project(name="抽帧测试项目", project_type="detect", classes=[])


def _make_video(path: Path, frame_count: int = 30, fps: float = 10.0, size=(64, 48)) -> Path:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, size)
    for i in range(frame_count):
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        frame[:] = (i * 5 % 255, 0, 0)
        writer.write(frame)
    writer.release()
    return path


def _saved_frame_numbers(project_id: int) -> list:
    """从落库的文件名里把帧号读回来（保存时写的是 ..._frame_000123.jpg）。"""
    numbers = []
    for image in db.get_project_images(project_id):
        match = re.search(r"_frame_(\d+)\.jpg$", image['filename'])
        if match:
            numbers.append(int(match.group(1)))
    return numbers


class _FakeCapUnknownTotal:
    """真的 VideoCapture 包一层，只是把总帧数汇报成 0（有些视频就是不写这个值）。"""

    real = cv2.VideoCapture

    def __init__(self, path):
        self._real = _FakeCapUnknownTotal.real(path)

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


# ==================== 元信息 ====================

def test_metadata_is_read_without_importing_any_frame():
    """读元信息只是打开文件读几个属性：拿到帧数/fps/时长，一张图都不会进项目。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "meta.mp4", frame_count=30, fps=10.0)

    meta = probe_video_metadata(str(video_path))

    assert meta['total_frames'] == 30, meta
    assert abs(meta['fps'] - 10.0) < 0.5, meta
    assert abs(meta['duration'] - 3.0) < 0.5, meta
    assert db.get_project_images(project_id) == [], "读元信息不该导入任何一帧"


def test_metadata_unknown_total_is_reported_as_zero_not_garbage():
    """总帧数读不到时给 0 / duration=None，而不是一个假的负数或 nan。"""
    video_path = _make_video(_TMP_DIR / "meta_unknown.mp4", frame_count=10)

    with patch("core.import_manager.cv2.VideoCapture", _FakeCapUnknownTotal):
        meta = probe_video_metadata(str(video_path))

    assert meta['total_frames'] == 0, meta
    assert meta['duration'] is None, meta


# ==================== 固定间隔 ====================

def test_interval_estimate_rounds_up_and_handles_unknown_total():
    """预计张数向上取整；总帧数未知时说「不知道」（None），不能编一个数出来。"""
    assert estimate_interval_frame_count(31, 30) == 2   # 第 0 帧和第 30 帧
    assert estimate_interval_frame_count(30, 30) == 1
    assert estimate_interval_frame_count(100, 1) == 100
    assert estimate_interval_frame_count(0, 30) is None
    assert estimate_interval_frame_count(-1, 30) is None


# ==================== 随机抽取 ====================

def test_random_indices_are_unique_sorted_and_in_range():
    """随机帧号：要几个给几个，不重复，升序，都落在 [0, 总帧数) 里。"""
    indices = plan_random_frame_indices(200, 12, rng=random.Random(7))

    assert len(indices) == 12
    assert len(set(indices)) == 12, "帧号不能重复"
    assert indices == sorted(indices), "必须升序，否则抽帧要来回 seek"
    assert all(0 <= i < 200 for i in indices), indices


def test_random_count_is_capped_at_total_frames():
    """要得比总帧数还多：按总帧数封顶，而不是报错或重复抽。"""
    indices = plan_random_frame_indices(5, 50, rng=random.Random(1))

    assert len(indices) == 5
    assert sorted(set(indices)) == [0, 1, 2, 3, 4]


def test_random_rejects_unknown_total_and_zero_count():
    """总帧数未知 / 张数 < 1：直接拒绝，不要走到解码那一步才发现干不了。"""
    for total, count in ((0, 10), (-3, 10), (100, 0)):
        try:
            plan_random_frame_indices(total, count)
        except ValueError:
            continue
        raise AssertionError(f"total={total} count={count} 应该抛 ValueError")


def test_random_import_saves_exactly_the_planned_unique_frames_in_order():
    """随机导入：张数正好、帧号不重复、按升序落库（同一个种子可复现）。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "random.mp4", frame_count=40)

    total = probe_video_metadata(str(video_path))['total_frames']
    expected = plan_random_frame_indices(total, 6, rng=random.Random(2026))

    manager = ImportManager(project_id)
    imported, skipped = manager.import_video(
        str(video_path), mode=VIDEO_MODE_RANDOM, sample_count=6,
        rng=random.Random(2026),
    )

    assert imported == 6, f"要 6 张，实际 {imported}"
    assert skipped == 0

    saved = _saved_frame_numbers(project_id)
    assert saved == expected, f"落库帧号应该等于计划帧号且升序: {saved} vs {expected}"


def test_random_import_stops_decoding_after_the_last_wanted_frame():
    """最后一个要的帧存完就收工，不再往下解码整段视频。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "random_stop.mp4", frame_count=60)

    reads = {'count': 0}
    real_capture = cv2.VideoCapture

    class _CountingCap:
        def __init__(self, path):
            self._real = real_capture(path)

        def isOpened(self):
            return self._real.isOpened()

        def get(self, prop):
            return self._real.get(prop)

        def read(self):
            reads['count'] += 1
            return self._real.read()

        def release(self):
            self._real.release()

    # 固定种子挑 3 张，最大帧号一定小于 60；解码次数不该到 60
    rng_seed = 99
    expected = plan_random_frame_indices(60, 3, rng=random.Random(rng_seed))

    manager = ImportManager(project_id)
    with patch("core.import_manager.cv2.VideoCapture", _CountingCap):
        imported, _ = manager.import_video(
            str(video_path), mode=VIDEO_MODE_RANDOM, sample_count=3,
            rng=random.Random(rng_seed),
        )

    assert imported == 3
    # 读到最后一个要的帧就停：读取次数 = 最大帧号 + 1
    assert reads['count'] == expected[-1] + 1, (
        f"应该在第 {expected[-1]} 帧之后停止解码，实际读了 {reads['count']} 次"
    )


def test_random_import_without_total_frames_refuses_instead_of_guessing():
    """总帧数读不到还硬要随机 N 张：抛 ValueError，不解码、不导入半成品。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "random_unknown.mp4", frame_count=20)

    manager = ImportManager(project_id)
    with patch("core.import_manager.cv2.VideoCapture", _FakeCapUnknownTotal):
        try:
            manager.import_video(str(video_path), mode=VIDEO_MODE_RANDOM, sample_count=5)
        except ValueError:
            pass
        else:
            raise AssertionError("总帧数未知时随机抽取应该抛 ValueError")

    assert db.get_project_images(project_id) == [], "失败时不该留下半截导入的图片"


def test_random_import_can_be_cancelled_midway():
    """随机抽取也能喊停：停在半路，最终进度不显示成 100%。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "random_cancel.mp4", frame_count=60)

    cancel_event = threading.Event()
    seen = []

    def progress_callback(progress, message):
        seen.append((progress, message))
        # 存下 2 张之后喊停
        if message.startswith("正在抽取") and len([m for _, m in seen if m.startswith("正在抽取")]) >= 2:
            cancel_event.set()

    manager = ImportManager(project_id)
    imported, _ = manager.import_video(
        str(video_path), mode=VIDEO_MODE_RANDOM, sample_count=20,
        progress_callback=progress_callback, cancel_event=cancel_event,
        rng=random.Random(5),
    )

    assert imported < 20, "取消之后不该把 20 张全抽完"
    final_progress, final_message = seen[-1]
    assert "已取消" in final_message, final_message
    assert final_progress < 100, f"取消不是完成，进度不该是 100：{final_progress}"


def test_progress_messages_state_mode_and_target_count():
    """进度和完成提示要说清楚「用的哪种抽法、目标多少张」。"""
    project_id = _make_project()
    video_path = _make_video(_TMP_DIR / "messages.mp4", frame_count=20)

    manager = ImportManager(project_id)

    random_calls = []
    manager.import_video(
        str(video_path), mode=VIDEO_MODE_RANDOM, sample_count=4,
        progress_callback=lambda p, m: random_calls.append(m), rng=random.Random(3),
    )
    assert "随机抽取 4 张" in random_calls[1], random_calls[1]
    assert "随机 4 张" in random_calls[-1], random_calls[-1]

    project_id2 = _make_project()
    manager2 = ImportManager(project_id2)
    interval_calls = []
    manager2.import_video(
        str(video_path), frame_interval=5, mode=VIDEO_MODE_INTERVAL,
        progress_callback=lambda p, m: interval_calls.append(m),
    )
    assert "间隔 5" in interval_calls[1] and "预计抽取 4 张" in interval_calls[1], interval_calls[1]
    assert "每 5 帧取 1 张" in interval_calls[-1], interval_calls[-1]


# ==================== 抽帧设置框 ====================

def test_dialog_shows_metadata_and_live_estimate():
    """框里要直接告诉用户：这视频多少帧多长，按现在填的数能抽出几张。

    「几张」就跟在输入框那一行后面——用户改的是那个数字，结果也该出现在那儿，
    而不是让他往下找一段说明文字。
    """
    meta = {'total_frames': 300, 'fps': 30.0, 'duration': 10.0}
    dialog = VideoExtractDialog(None, meta, filename="test.mp4")

    assert "300 帧" in dialog.lbl_meta.text()
    assert "30 fps" in dialog.lbl_meta.text()
    assert "10 秒" in dialog.lbl_meta.text()

    dialog.sb_interval.setValue(30)
    inline = dialog.lbl_interval_estimate.text()
    assert "预计 10 张" in inline, inline
    # fps 已知时要把间隔折算成时间，用户想的是「几秒一张」不是「几帧一张」
    assert "每 1.0 秒" in inline, inline

    dialog.sb_interval.setValue(60)
    assert "预计 5 张" in dialog.lbl_interval_estimate.text(), dialog.lbl_interval_estimate.text()

    plan = dialog.get_plan()
    assert plan == {'mode': VIDEO_MODE_INTERVAL, 'frame_interval': 60, 'sample_count': None}


def test_result_block_only_appears_when_it_has_something_to_add():
    """底下那块说明只在真有话说时出现：能算出张数的固定间隔模式不留空灰条。

    张数和「几秒一张」上面那行已经写了，再原样重复一遍只是把框撑高。
    随机模式（要给建议）和总帧数未知（要解释为什么算不出来）才该出现。
    """
    known = {'total_frames': 300, 'fps': 30.0, 'duration': 10.0}
    dialog = VideoExtractDialog(None, known, filename="test.mp4")
    dialog.show()
    _app.processEvents()

    assert not dialog.result_box.isVisible(), (
        f"固定间隔 + 总帧数已知时不该出现说明块：{dialog.lbl_estimate.text()!r}"
    )

    # 随机模式：抽几张全靠用户拍脑袋，必须给参照
    dialog.rbtn_random.setChecked(True)
    _app.processEvents()
    assert dialog.result_box.isVisible()
    assert "10 秒" in dialog.lbl_estimate.text(), dialog.lbl_estimate.text()

    dialog.close()

    # 总帧数未知：要解释为什么给不出预计张数
    unknown = VideoExtractDialog(None, {'total_frames': 0, 'fps': 0.0, 'duration': None})
    unknown.show()
    _app.processEvents()

    assert unknown.result_box.isVisible()
    assert "总帧数未知" in unknown.lbl_estimate.text(), unknown.lbl_estimate.text()
    assert "张数未知" in unknown.lbl_interval_estimate.text()

    unknown.close()


def test_dialog_random_mode_bounds_count_and_gives_advice():
    """随机模式：张数上限是总帧数，并按时长给一句人话建议。"""
    meta = {'total_frames': 300, 'fps': 30.0, 'duration': 10.0}
    dialog = VideoExtractDialog(None, meta, filename="test.mp4")

    dialog.rbtn_random.setChecked(True)

    assert dialog.sb_random_count.maximum() == 300, "随机张数不能超过总帧数"
    assert dialog.sb_random_count.minimum() == 1

    dialog.sb_random_count.setValue(12)
    text = dialog.lbl_estimate.text()
    assert "随机抽取 12 张" in text, text
    assert "10 秒" in text, text  # 建议里用时长说话

    plan = dialog.get_plan()
    assert plan['mode'] == VIDEO_MODE_RANDOM
    assert plan['sample_count'] == 12


def test_dialog_disables_random_mode_when_total_is_unknown():
    """总帧数未知：随机抽取整个禁掉并说明原因，固定间隔照常能用。"""
    meta = {'total_frames': 0, 'fps': 0.0, 'duration': None}
    dialog = VideoExtractDialog(None, meta, filename="stream.mp4")

    assert not dialog.rbtn_random.isEnabled(), "总帧数未知时随机抽取必须禁用"
    assert not dialog.sb_random_count.isEnabled()
    assert "总帧数" in dialog.rbtn_random.toolTip(), dialog.rbtn_random.toolTip()
    assert "读不到总帧数" in dialog.lbl_meta.text(), dialog.lbl_meta.text()

    assert dialog.rbtn_interval.isChecked(), "应该默认落在还能用的固定间隔上"
    assert dialog.sb_interval.isEnabled()
    dialog.sb_interval.setValue(10)
    assert "总帧数未知" in dialog.lbl_estimate.text(), dialog.lbl_estimate.text()

    plan = dialog.get_plan()
    assert plan['mode'] == VIDEO_MODE_INTERVAL and plan['frame_interval'] == 10


def test_switching_to_random_mode_grows_the_dialog_instead_of_squashing_the_text():
    """切到随机模式时说明从一行变三行——框子必须跟着长高。

    QLabel 的换行高度默认传不到对话框：不处理的话多出来的两行会直接画在
    「取消 / 开始抽帧」上面，用户看到的是半截压着按钮的字。
    """
    meta = {'total_frames': 3000, 'fps': 30.0, 'duration': 100.0}
    dialog = VideoExtractDialog(None, meta, filename="长视频.mp4")
    dialog.show()
    _app.processEvents()

    dialog.rbtn_random.setChecked(True)
    for _ in range(3):
        _app.processEvents()

    label = dialog.lbl_estimate
    needed = label.heightForWidth(label.width())
    assert label.height() >= needed, (
        f"建议文字需要 {needed}px，标签只有 {label.height()}px——会被压掉几行"
    )

    # 说明块不能压到按钮上
    assert dialog.result_box.geometry().bottom() <= dialog.btn_confirm.geometry().top(), \
        "说明块和按钮重叠了"

    # 长高不等于变窄：让高度自适应的那套做法很容易顺手把固定宽度也一起解开，
    # 框子会缩成一条窄柱子，说明文字被挤成每行三四个字
    assert dialog.width() == DIALOG_WIDTH, (
        f"对话框宽度应该稳定在 {DIALOG_WIDTH}，实际 {dialog.width()}"
    )

    dialog.close()


def test_duration_is_spoken_in_plain_chinese():
    assert format_duration(45) == "45 秒"
    assert format_duration(72) == "1 分 12 秒"
    assert format_duration(0) == ""
    assert format_duration(None) == ""


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(dict(globals())))
