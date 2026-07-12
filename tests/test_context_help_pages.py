# -*- coding: utf-8 -*-
"""六个页面接上同一个「使用说明」，以及自动标注设置页的底部收口。

组件本身（展开/收起/键盘/长文换行）由 test_context_help.py 覆盖，这里只管接线：
每个位置都真的有一个 ContextHelp、进页面时都是收起的；自动标注设置在 1280x720、
150% 字号下，摘要不被按钮压住，取消和保存等高同基线。

    python tests/test_context_help_pages.py
    python -m pytest tests/test_context_help_pages.py -q
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys

from gui.main_window import MainWindow  # noqa: E402
from gui.pages.auto_label_dialog import AutoLabelDialog  # noqa: E402
from gui.widgets.context_help import ContextHelp  # noqa: E402

_app = _bootstrap.app()
db = _bootstrap.db

SLACK = 2


def _settle(rounds=3):
    for _ in range(rounds):
        _app.processEvents()


def _make_window() -> MainWindow:
    from gui import main_window as mw
    mw.db = db
    return MainWindow()


def test_five_pages_and_the_dialog_each_have_a_collapsed_help():
    window = _make_window()
    window.resize(1280, 720)
    window.show()
    _settle()

    dialog = AutoLabelDialog(None)

    spots = [
        ("数据导入", window.import_page),
        ("数据标注", window.annotate_page),
        ("模型训练", window.train_page),
        ("结果分析", window.result_page),
        ("模型测试", window.test_page),
        ("自动标注设置", dialog),
    ]

    for name, spot in spots:
        help_widget = getattr(spot, "context_help", None)
        assert isinstance(help_widget, ContextHelp), f"{name} 没有使用说明入口"
        assert not help_widget.is_expanded(), f"{name} 的使用说明应该默认收起"
        assert not help_widget.content.isVisible(), f"{name} 收起时不该显示步骤"

        steps = help_widget.content.findChildren(type(help_widget._label))
        assert 3 <= len(steps) <= 5, f"{name} 的说明应该是 3-5 步，现在是 {len(steps)} 步"

    dialog.close()
    window.close()


def test_auto_label_dialog_footer_holds_at_1280x720_and_150_percent_font():
    base_point_size = _app.font().pointSizeF() or 13.0

    font = _app.font()
    font.setPointSizeF(base_point_size * 1.5)

    dialog = AutoLabelDialog(None)
    dialog.setFont(font)
    for child in dialog.findChildren(type(dialog.lbl_preview)):
        child.setFont(font)

    # 弹窗要能在 1280x720 的屏幕里完整摆开
    dialog.resize(1200, 700)
    dialog.show()
    _settle(5)

    assert dialog.minimumSizeHint().width() <= 1280, "150% 字号下弹窗已经放不进 1280 宽"
    assert dialog.minimumSizeHint().height() <= 720, "150% 字号下弹窗已经放不进 720 高"

    # 摘要固定在滚动区和按钮之间：既不被按钮压住，也不盖住上面的标签页
    preview_bottom = dialog.lbl_preview.mapTo(dialog, dialog.lbl_preview.rect().bottomLeft()).y()
    tabs_bottom = dialog.tab_widget.mapTo(dialog, dialog.tab_widget.rect().bottomLeft()).y()
    save_top = dialog.btn_save.mapTo(dialog, dialog.btn_save.rect().topLeft()).y()

    assert tabs_bottom <= dialog.lbl_preview.mapTo(dialog, dialog.lbl_preview.rect().topLeft()).y(), (
        "配置摘要盖住了可滚动的标签页内容"
    )
    assert preview_bottom <= save_top + SLACK, "配置摘要和按钮叠在一起了"

    # 取消 / 保存：同高、同基线
    assert dialog.btn_cancel.height() == dialog.btn_save.height(), (
        f"两个按钮高度不一致：取消 {dialog.btn_cancel.height()}px，保存 {dialog.btn_save.height()}px"
    )
    assert dialog.btn_cancel.y() == dialog.btn_save.y(), "两个按钮基线没对齐"

    dialog.close()


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
