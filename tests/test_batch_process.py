# -*- coding: utf-8 -*-
"""批处理弹窗的清理契约。

BatchProcessDialog 是非模态的：用户一边在画布上点像素点，一边在弹窗里设置。
只要弹窗没了，画布就必须退出点选模式——否则用户每点一下还在继续加点，而且是
加给一个已经关掉的对话框。

三条退出路径都必须走同一个幂等 helper：
    确认执行(process_requested) / 取消(reject) / 直接关窗(close)

运行：
    python -m pytest tests/test_batch_process.py -q
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys

from PyQt6.QtWidgets import QMessageBox

from gui.pages import batch_process_dialog as bpd
from gui.pages.annotate_page import AnnotatePage

_app = _bootstrap.app()


class _AutoYesBox:
    """把弹出的确认框直接当成「是」，测试里不停在模态对话框上。"""

    StandardButton = QMessageBox.StandardButton

    @staticmethod
    def question(*_args, **_kwargs):
        return QMessageBox.StandardButton.Yes

    @staticmethod
    def warning(*_args, **_kwargs):
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def information(*_args, **_kwargs):
        return QMessageBox.StandardButton.Ok


def make_page() -> AnnotatePage:
    """直接摆好页面状态。set_project 是开线程异步加载的，测试里不等它。"""
    page = AnnotatePage()
    page.current_project_id = 1
    page.images = [{'id': 1, 'file_name': 'a.jpg'}]
    page.classes = [{'id': 0, 'name': 'cat', 'color': '#ff0000'}]
    return page


def assert_batch_mode_clean(page):
    """点选模式的四个状态位必须全部收干净。"""
    assert page.batch_process_dialog is None, "页面还攥着弹窗引用"
    assert page.canvas.batch_process_mode is False, "画布还在点选模式"
    assert page.canvas.batch_process_points == [], "画布还留着上次的点"
    assert page.canvas.batch_process_dialog is None, "画布还指着已关闭的弹窗"


def test_opening_dialog_enters_batch_mode():
    page = make_page()
    page.show_batch_process_dialog()

    assert page.batch_process_dialog is not None
    assert page.canvas.batch_process_mode is True
    assert page.canvas.batch_process_dialog is page.batch_process_dialog

    page.batch_process_dialog.reject()


def test_cancel_exits_batch_mode():
    page = make_page()
    page.show_batch_process_dialog()
    page.canvas.batch_process_points.append((3, 4))

    page.batch_process_dialog.reject()  # 「取消」按钮走的就是 reject
    assert_batch_mode_clean(page)


def test_closing_window_exits_batch_mode():
    page = make_page()
    page.show_batch_process_dialog()
    page.canvas.batch_process_points.append((3, 4))

    page.batch_process_dialog.close()  # 点窗口的关闭按钮
    assert_batch_mode_clean(page)


def test_execute_runs_once_and_exits_batch_mode():
    page = make_page()

    calls = []
    page.execute_batch_process = lambda config: calls.append(config)

    page.show_batch_process_dialog()
    dialog = page.batch_process_dialog

    dialog.add_point(5, 5)
    dialog.delete_class_list.setCurrentRow(0)
    assert dialog.btn_execute.isEnabled()

    real_box = bpd.QMessageBox
    bpd.QMessageBox = _AutoYesBox
    try:
        dialog.execute_process()
    finally:
        bpd.QMessageBox = real_box

    # 确认执行只跑一次：process_requested 和 accept() 触发的 finished
    # 都会调 _exit_batch_process_mode，但批处理本身不能跑两遍
    assert len(calls) == 1, f"批处理执行了 {len(calls)} 次"
    assert calls[0]['operation'] == 'delete'
    assert calls[0]['points'] == [(5, 5)]
    assert_batch_mode_clean(page)


def test_exit_helper_is_idempotent():
    page = make_page()
    page.show_batch_process_dialog()

    page._exit_batch_process_mode()
    page._exit_batch_process_mode()  # 重复调用不该炸，也不该有副作用
    assert_batch_mode_clean(page)


def test_reopening_does_not_leak_previous_dialog():
    page = make_page()
    page.show_batch_process_dialog()
    first = page.batch_process_dialog

    # 弹窗还开着就再点一次「批处理」：上一个要被收掉，不能两个弹窗抢画布
    page.show_batch_process_dialog()
    second = page.batch_process_dialog

    assert second is not first
    assert page.canvas.batch_process_dialog is second
    assert page.canvas.batch_process_mode is True

    second.reject()
    assert_batch_mode_clean(page)


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
