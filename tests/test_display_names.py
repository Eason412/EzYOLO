# -*- coding: utf-8 -*-
"""图片显示名：把抽帧生成的文件名念成人话，同时不丢原始文件名。

覆盖的真实问题：
    1. 视频抽帧落库的名字是 20260712_000602_575947_frame_000223.jpg。
       一屏几十行，前 22 个字符完全一样——列表里等于每张图都没有名字。
    2. 只截尾巴不行：两段视频都抽到第 223 帧时，两行会长得一模一样。
    3. 别名只是显示层的事：文件名和数据库不能被改写，完整名字必须还找得到（tooltip）。

运行：
    python tests/test_display_names.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys

from gui.display_names import display_name, display_names, parse_frame_name

_app = _bootstrap.app()
db = _bootstrap.db


# ==================== 纯函数 ====================

def test_generated_frame_name_is_spoken_as_frame_number():
    """抽帧名 → 「帧 223」：前导零去掉，时间戳不念。"""
    assert display_name("20260712_000602_575947_frame_000223.jpg") == "帧 223"
    assert display_name("20260712_000602_575947_frame_000001.jpg") == "帧 1"


def test_all_zero_frame_number_stays_zero_not_empty():
    """第 0 帧是「帧 0」，不能把零全去掉变成「帧 」。"""
    assert display_name("20260712_000602_575947_frame_000000.jpg") == "帧 0"


def test_user_filenames_are_left_alone():
    """普通图片的文件名是用户自己起的，本来就可读——原样返回，不替他改写。"""
    for name in ("cat.jpg", "IMG_2024.png", "安全帽-01.jpeg", "frame_000223.jpg"):
        assert display_name(name) == name, name


def test_unrecognized_names_degrade_to_themselves():
    """认不出来的名字宁可难看也不能念错——退回原样，不猜。"""
    assert display_name("") == ""
    assert display_name("2026_frame_1.jpg") == "2026_frame_1.jpg"
    assert parse_frame_name("cat.jpg") is None


def test_parse_exposes_the_pieces_for_discriminating():
    parsed = parse_frame_name("20260712_000602_575947_frame_000223.jpg")
    assert parsed['date'] == "20260712"
    assert parsed['time'] == "000602"
    assert parsed['frame'] == "000223"


# ==================== 重名 ====================

def test_frames_from_one_video_need_no_discriminator():
    """同一段视频里帧号本来就不会重复，别名保持最短。"""
    names = [
        "20260712_000602_575947_frame_000000.jpg",
        "20260712_000602_575948_frame_000030.jpg",
        "20260712_000602_575949_frame_000060.jpg",
    ]
    assert display_names(names) == ["帧 0", "帧 30", "帧 60"]


def test_same_frame_number_from_different_days_is_split_by_date():
    """两段视频都抽到第 223 帧：先用日期分，别名不能一模一样。"""
    names = [
        "20260712_000602_575947_frame_000223.jpg",
        "20260713_101500_000001_frame_000223.jpg",
    ]
    aliases = display_names(names)

    assert aliases == ["帧 223 · 07-12", "帧 223 · 07-13"], aliases
    assert len(set(aliases)) == 2


def test_same_day_falls_through_to_time():
    """同一天导入的两段视频：日期分不开，用时间分。"""
    names = [
        "20260712_000602_575947_frame_000223.jpg",
        "20260712_183000_000001_frame_000223.jpg",
    ]
    aliases = display_names(names)

    assert aliases == ["帧 223 · 00:06:02", "帧 223 · 18:30:00"], aliases


def test_same_second_falls_back_to_a_stable_ordinal():
    """同一秒也分不开（微秒不同）：给序号。顺序稳定，不随调用次数变。"""
    names = [
        "20260712_000602_575947_frame_000223.jpg",
        "20260712_000602_575999_frame_000223.jpg",
    ]
    aliases = display_names(names)

    assert aliases == ["帧 223 (1)", "帧 223 (2)"], aliases
    assert display_names(names) == aliases, "同样的输入必须给同样的别名"


def test_duplicate_user_filenames_also_get_ordinals():
    """普通文件名重了一样要能分开（没有时间戳可用 → 序号）。"""
    aliases = display_names(["cat.jpg", "cat.jpg", "dog.jpg"])
    assert aliases == ["cat.jpg (1)", "cat.jpg (2)", "dog.jpg"], aliases


def test_aliases_line_up_one_to_one_with_the_input():
    """别名列表和输入列表一一对应——错位就会把 A 的名字挂到 B 头上。"""
    names = [
        "cat.jpg",
        "20260712_000602_575947_frame_000223.jpg",
        "20260713_101500_000001_frame_000223.jpg",
        "dog.png",
    ]
    aliases = display_names(names)

    assert len(aliases) == len(names)
    assert aliases[0] == "cat.jpg"
    assert aliases[3] == "dog.png"
    assert aliases[1].startswith("帧 223") and aliases[2].startswith("帧 223")
    assert aliases[1] != aliases[2]


# ==================== 页面里用起来 ====================

def _seed_frames(project_id, filenames):
    for name in filenames:
        db.add_image(project_id, name, f"/tmp/{name}", width=640, height=480)


def test_import_grid_shows_aliases_but_keeps_the_real_name_in_the_tooltip():
    """导入页网格：格子上写别名，完整文件名和分辨率留在 tooltip 里。"""
    from PyQt6.QtCore import Qt
    from gui.pages.import_page import ImportPage

    project_id = _bootstrap.create_temp_project(name="别名测试", project_type="detect")
    original = "20260712_000602_575947_frame_000223.jpg"
    _seed_frames(project_id, [original])

    page = ImportPage()
    page.set_project(project_id)
    _app.processEvents()

    item = page.image_list.item(0)
    assert item.text() == "帧 223", item.text()
    assert original in item.toolTip(), item.toolTip()
    assert "640x480" in item.toolTip(), item.toolTip()

    # 数据库里存的还是原来那个名字——别名只是显示层的事
    stored = db.get_project_images(project_id)[0]['filename']
    assert stored == original

    page.stop_image_loading()
    db.delete_project(project_id)


def test_annotate_list_and_context_bar_use_aliases_and_put_position_first():
    """标注页：左边列表用别名（保留 ✓/○），顶上先说第几张，再说是哪张。"""
    from gui.pages.annotate_page import AnnotatePage

    project_id = _bootstrap.create_temp_project(name="别名测试2", project_type="detect")
    original = "20260712_000602_575947_frame_000223.jpg"
    _seed_frames(project_id, [original])

    page = AnnotatePage()
    page.current_project_id = project_id
    page.load_image_list()
    _app.processEvents()

    item = page.image_list.item(0)
    assert item.text() == "○ 帧 223", item.text()
    assert original in item.toolTip(), item.toolTip()

    page.load_image(page.images[0]['id'])
    _app.processEvents()

    text = str(page.image_name_label.property('_full_text'))
    assert text.startswith("第 1/1 张"), f"位置要排在最前面：{text!r}"
    assert "帧 223" in text, text
    assert original not in text, f"长文件名不该出现在信息条上：{text!r}"
    # 完整文件名还找得到
    assert original in page.image_name_label.toolTip()

    page.shutdown()
    db.delete_project(project_id)


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(dict(globals())))
