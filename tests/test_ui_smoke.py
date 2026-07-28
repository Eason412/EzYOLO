# -*- coding: utf-8 -*-
"""界面冒烟测试：主窗口能起来，五个步骤都能打开，前置条件页会正确挡人。

离屏渲染，不弹真窗口。数据库、QSettings、启动期文件同步都在 _bootstrap 里换掉了，
所以测试不碰 data/、outputs/ 和用户的真实设置。

运行：
    python -m pytest tests/test_ui_smoke.py -q
    或
    python tests/test_ui_smoke.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys
from unittest.mock import patch

from PyQt6.QtCore import QSettings  # noqa: E402

from gui.workflow import (  # noqa: E402
    STEP_IMPORT, STEP_ANNOTATE, STEP_TRAIN, STEP_RESULT, STEP_TEST,
    PAGE_SETTINGS, PAGE_ABOUT,
)
from gui.main_window import MainWindow, GATE_INDEX  # noqa: E402

_app = _bootstrap.app()
db = _bootstrap.db


def make_window() -> MainWindow:
    from gui import main_window as mw
    from gui import workflow
    mw.db = db
    # 临时数据库的 project id 可能与真实 runs/ 中的项目号相同；界面冒烟不能
    # 因此把用户已有模型误算成当前临时项目的训练结果。
    workflow.APP_ROOT = _bootstrap.TEMP_PROJECTS_DIR
    return MainWindow()


def test_window_opens_on_first_step():
    window = make_window()
    assert window.current_index == STEP_IMPORT
    assert window.content_stack.currentIndex() == STEP_IMPORT
    # 没有项目时，工作流的下一步数据也不该催下一步
    # （导入页是业务页，PageHeader 本身也不显示，见 test_ui_layout.py）
    assert window.header._next_index is None


def test_all_steps_reachable_and_gated_without_project():
    window = make_window()

    # 没有项目：导入页可以进，后面四步都被挡在前置条件页
    window.switch_page(STEP_IMPORT)
    assert window.content_stack.currentIndex() == STEP_IMPORT

    for index in (STEP_ANNOTATE, STEP_TRAIN, STEP_RESULT, STEP_TEST):
        window.switch_page(index)
        assert window.content_stack.currentIndex() == GATE_INDEX, index
        assert window.gate.title.text()
        assert window.gate.goto_btn.text()


def test_settings_and_about_open_directly():
    window = make_window()

    window.switch_page(PAGE_SETTINGS)
    assert window.content_stack.currentIndex() == PAGE_SETTINGS
    assert window.btn_settings.isChecked()

    window.switch_page(PAGE_ABOUT)
    assert window.content_stack.currentIndex() == PAGE_ABOUT
    assert window.btn_about.isChecked()
    assert not window.btn_settings.isChecked()


def test_project_with_images_opens_annotate_but_blocks_train():
    project_id = _bootstrap.create_temp_project(name="冒烟项目", project_type="detect", classes=[])
    db.add_image(project_id, "a.jpg", "/tmp/does-not-matter-a.jpg", width=10, height=10)

    window = make_window()
    window.load_projects(select_id=project_id)
    assert window.current_project_id == project_id

    # 有图片 → 标注页能进
    window.switch_page(STEP_ANNOTATE)
    assert window.content_stack.currentIndex() == STEP_ANNOTATE

    # 一张都没标 → 训练被挡住，并指回标注
    window.switch_page(STEP_TRAIN)
    assert window.content_stack.currentIndex() == GATE_INDEX
    assert window.gate._goto_index == STEP_ANNOTATE

    # 工作流的下一步数据应该指向标注（导入页是业务页，页头本身不显示，
    # 见 test_ui_layout.py 的 header 隐藏测试；这里只验证数据仍然对）
    window.switch_page(STEP_IMPORT)
    assert window.header._next_index == STEP_ANNOTATE

    db.delete_project(project_id)


def test_reopening_window_restores_the_last_selected_project():
    project_id = _bootstrap.create_temp_project(
        name="重开恢复项目",
        project_type="detect",
        classes=[],
    )
    first = make_window()
    first.load_projects(select_id=project_id)

    reopened = make_window()

    assert reopened.current_project_id == project_id
    assert reopened.project_combo.currentData() == project_id
    db.delete_project(project_id)


def test_first_open_selects_the_only_project_when_no_previous_choice_exists():
    project_id = _bootstrap.create_temp_project(
        name="唯一项目",
        project_type="detect",
        classes=[],
    )
    settings = QSettings("EzYOLO", "MainWindow")
    settings.remove("current_project_id")
    settings.sync()
    only_project = db.get_project(project_id)

    with patch.object(db, "get_all_projects", return_value=[only_project]):
        window = make_window()

    assert window.current_project_id == project_id
    assert window.project_combo.currentData() == project_id
    db.delete_project(project_id)


def test_gate_bypass_lets_user_into_test_page():
    project_id = _bootstrap.create_temp_project(name="跳过项目", project_type="detect", classes=[])
    db.add_image(project_id, "b.jpg", "/tmp/does-not-matter-b.jpg", width=10, height=10)

    window = make_window()
    window.load_projects(select_id=project_id)

    window.switch_page(STEP_TEST)
    assert window.content_stack.currentIndex() == GATE_INDEX

    # 「我有现成的模型，直接测试」
    window.on_gate_bypassed()
    assert window.content_stack.currentIndex() == STEP_TEST

    db.delete_project(project_id)


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
