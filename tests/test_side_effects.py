# -*- coding: utf-8 -*-
"""构造页面不许动用户的文件；路径不许跟着当前工作目录走。

两个真实事故：
  1. TestPage.__init__ 里 rmtree 了 outputs/test_images 和 outputs/test_videos。
     MainWindow 一启动就构造所有页面 —— 应用打开的那一刻，用户上一次的测试结果
     就没了，而他根本没点过「开始测试」。
  2. result_page 用 Path.cwd() 算展示路径。双击 EzYOLO.app 启动时 cwd 是 /，
     结果页找结果、显示路径全跟着错。

运行：
    python -m pytest tests/test_side_effects.py -q
"""

import _bootstrap  # noqa: F401  必须第一个导入

import os
import sys
from pathlib import Path

from gui.pages.result_page import ResultPage
from gui.pages.test_page import TestPage, annotated_output_path, is_video_path
from gui.workflow import APP_ROOT

_app = _bootstrap.app()

OUTPUTS = APP_ROOT / "outputs"


def test_constructing_test_page_keeps_existing_results():
    """哨兵文件：构造 TestPage 之后必须原封不动。"""
    image_dir = OUTPUTS / "test_images"
    video_dir = OUTPUTS / "test_videos"
    image_dir.mkdir(parents=True, exist_ok=True)
    video_dir.mkdir(parents=True, exist_ok=True)

    sentinels = [
        image_dir / "sentinel_keep_me_annotated.jpg",
        video_dir / "sentinel_keep_me_annotated.mp4",
    ]
    for s in sentinels:
        s.write_bytes(b"user result, do not delete")

    try:
        before = {s: s.read_bytes() for s in sentinels}

        TestPage()  # 构造页面 —— 这一步以前会把整个目录 rmtree 掉

        for s in sentinels:
            assert s.exists(), f"构造 TestPage 删掉了 {s}"
            assert s.read_bytes() == before[s], f"构造 TestPage 改写了 {s}"
    finally:
        for s in sentinels:
            s.unlink(missing_ok=True)


def test_test_page_init_touches_no_directories(tmp_path=None):
    """构造 TestPage 不该创建目录，也不该删目录。"""
    page = TestPage()
    assert page.output_root == OUTPUTS
    assert page.image_output_dir == OUTPUTS / "test_images"
    assert page.video_output_dir == OUTPUTS / "test_videos"
    # startup 那把 rmtree 已经彻底没了
    assert not hasattr(page, "_clear_test_outputs_on_startup")


def test_clear_outputs_for_run_only_removes_its_own_targets(tmp_path=None):
    """只删这次测试会覆盖的那几个文件，别人的结果一个都不碰。"""
    import tempfile

    page = TestPage()
    sandbox = Path(tempfile.mkdtemp(prefix="ezyolo-outputs-"))
    page.output_root = sandbox
    page.image_output_dir = sandbox / "test_images"
    page.video_output_dir = sandbox / "test_videos"
    page.image_output_dir.mkdir(parents=True)
    page.video_output_dir.mkdir(parents=True)

    mine_img = page.image_output_dir / "a_annotated.jpg"
    mine_vid = page.video_output_dir / "clip_annotated.mp4"
    someone_else = page.image_output_dir / "old_run_annotated.jpg"
    for f in (mine_img, mine_vid, someone_else):
        f.write_bytes(b"x")

    page._clear_outputs_for_run(["/data/a.jpg", "/data/clip.mp4"])

    assert not mine_img.exists(), "本次会覆盖的图片结果没清掉"
    assert not mine_vid.exists(), "本次会覆盖的视频结果没清掉"
    assert someone_else.exists(), "把不相干的结果也删了"


def test_clear_outputs_for_run_survives_missing_files():
    """目标文件本来就不存在时不该抛异常。"""
    page = TestPage()
    import tempfile
    sandbox = Path(tempfile.mkdtemp(prefix="ezyolo-outputs-"))
    page.image_output_dir = sandbox / "test_images"
    page.video_output_dir = sandbox / "test_videos"
    page._clear_outputs_for_run(["/nope/x.jpg"])  # 不该炸


def test_output_naming_rule():
    assert is_video_path("/a/b/c.MP4") is True
    assert is_video_path("/a/b/c.jpg") is False

    img_dir, vid_dir = Path("/i"), Path("/v")
    assert annotated_output_path("/data/cat.png", img_dir, vid_dir) == img_dir / "cat_annotated.jpg"
    assert annotated_output_path("/data/clip.mov", img_dir, vid_dir) == vid_dir / "clip_annotated.mp4"


def test_result_page_display_path_is_cwd_independent():
    """从任意 cwd 启动，结果页的短路径都得算对。"""
    page = ResultPage()
    run_dir = APP_ROOT / "runs" / "train" / "exp_1"

    original = os.getcwd()
    try:
        os.chdir("/")  # 模拟双击 EzYOLO.app：cwd 是 /
        assert page._display_path(run_dir) == "runs/train/exp_1"
    finally:
        os.chdir(original)

    # 在项目目录里也一样
    assert page._display_path(run_dir) == "runs/train/exp_1"

    # 应用根目录之外的路径，原样显示绝对路径
    assert page._display_path(Path("/tmp/elsewhere")) == "/tmp/elsewhere"


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
