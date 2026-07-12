# -*- coding: utf-8 -*-
"""菜单按钮必须使用项目自己的向下箭头，不能退回 Qt 原生指示器。

    python tests/test_menu_arrow_styles.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import re
import sys
from pathlib import Path

from PyQt6.QtWidgets import QPushButton  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402
from gui.pages.auto_label_dialog import AutoLabelDialog  # noqa: E402
from gui.styles import COLORS, generate_stylesheet  # noqa: E402

_app = _bootstrap.app()
_ASSETS_DIR = Path(__file__).parent.parent / "gui" / "assets"


def _make_window():
    from gui import main_window as main_window_module
    main_window_module.db = _bootstrap.db
    return MainWindow()


def test_menu_chevron_assets_and_global_qss_are_explicit():
    expected_colors = {
        "chevron_down.svg": "#007AFF",
        "chevron_down_disabled.svg": "#AEAEB2",
        "chevron_down_white.svg": "#FFFFFF",
        "chevron_down_red.svg": "#D70015",
    }
    for name, color in expected_colors.items():
        asset = _ASSETS_DIR / name
        assert asset.is_file(), name
        svg = asset.read_text(encoding="utf-8")
        assert 'width="12" height="12"' in svg
        assert 'stroke-width="1.6"' in svg
        assert f'stroke="{color}"' in svg

    stylesheet = generate_stylesheet(COLORS)
    assert 'QPushButton[menuIndicator="true"]' in stylesheet
    assert 'padding-right: 32px;' in stylesheet
    assert 'QPushButton#primary[menuIndicator="true"]::menu-indicator' in stylesheet
    assert 'chevron_down.svg' in stylesheet
    assert 'chevron_down_white.svg' in stylesheet
    assert 'chevron_down_disabled.svg' in stylesheet
    assert not re.search(r"^\s*QPushButton::menu-indicator", stylesheet, re.MULTILINE)


def test_all_menu_pushbuttons_are_marked_and_sam_refreshes_dynamically():
    window = _make_window()
    try:
        annotate = window.annotate_page
        train = window.train_page
        result = window.result_page

        expected = {
            annotate.btn_auto_label,
            annotate.btn_llm_label,
            train.btn_template_menu,
            result.btn_export_model,
        }
        assert all(button.menu() is not None for button in expected)
        assert all(button.property("menuIndicator") is True for button in expected)

        original_method = AutoLabelDialog.__dict__["get_saved_sam_config"]
        AutoLabelDialog.get_saved_sam_config = classmethod(
            lambda cls: {"sam_type": "SAM2", "usage_mode": "memory"}
        )
        try:
            annotate.apply_sam_button_mode()
            assert annotate.btn_sam.menu() is not None
            assert annotate.btn_sam.property("menuIndicator") is True
        finally:
            AutoLabelDialog.get_saved_sam_config = original_method

        AutoLabelDialog.get_saved_sam_config = classmethod(
            lambda cls: {"sam_type": "SAM", "usage_mode": "normal"}
        )
        try:
            annotate.apply_sam_button_mode()
            assert annotate.btn_sam.menu() is None
            assert annotate.btn_sam.property("menuIndicator") is False
        finally:
            AutoLabelDialog.get_saved_sam_config = original_method

        for button in window.findChildren(QPushButton):
            if button.menu() is not None:
                assert button.property("menuIndicator") is True, button.text()
    finally:
        window.close()


def test_annotate_tool_menu_arrows_have_roles_and_space():
    window = _make_window()
    try:
        annotate = window.annotate_page
        draw_style = annotate.btn_draw_tool.styleSheet()
        delete_style = annotate.btn_delete.styleSheet()

        assert annotate.btn_draw_tool.menu() is not None
        assert annotate.btn_delete.menu() is not None
        assert 'QToolButton::menu-arrow' in draw_style
        assert 'QToolButton::menu-indicator' in draw_style
        assert 'chevron_down.svg' in draw_style
        assert 'chevron_down_disabled.svg' in draw_style
        assert 'width: 24px;' in draw_style
        assert 'width: 12px;' in draw_style
        assert 'background-color: #FFFFFF;' in draw_style

        assert 'QToolButton::menu-arrow' in delete_style
        assert 'QToolButton::menu-indicator' in delete_style
        assert 'chevron_down_red.svg' in delete_style
        assert 'chevron_down_disabled.svg' in delete_style
        assert 'width: 24px;' in delete_style
        assert 'width: 12px;' in delete_style

        for button in (annotate.btn_draw_tool, annotate.btn_delete):
            assert button.width() >= button.minimumSizeHint().width()
    finally:
        window.close()


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
