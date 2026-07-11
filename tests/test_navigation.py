# -*- coding: utf-8 -*-
"""导航与配置入口：界面上写着「去别处配置」的地方，必须真的能点开配置。

死文字（「配置入口在数据标注 → 自动标注」）不算入口：用户还得自己找。
这里查的是三件事——
  1. 设置页有一个真按钮，点了会请求打开自动标注配置；
  2. 主窗口接住这个请求，真的把 AutoLabelDialog 开出来，关掉后刷新状态；
  3. 标注页缺 SAM / 缺 API Key 时给的是「去配置」动作，不是一段路径说明。

    python tests/test_navigation.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QPushButton, QMessageBox, QDialog  # noqa: E402

from gui.workflow import PAGE_SETTINGS  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.pages import annotate_page as annotate_module  # noqa: E402

_app = _bootstrap.app()
db = _bootstrap.db

# 兜底：模态窗的 exec() 会自己开一个事件循环，没人点就永远不返回——测试直接挂死，
# 只能靠外面 kill。所以留一个定时器在（模态自己的事件循环里也照样跑）：真有窗弹出来
# 就当场关掉、记一笔，最后由 test_zz_* 报错。测试是失败，不是卡住。
ESCAPED_MODALS = []


def _sweep_modals():
    widget = _app.activeModalWidget()
    if widget is not None:
        ESCAPED_MODALS.append(type(widget).__name__)
        widget.close()


_watchdog = QTimer()
_watchdog.timeout.connect(_sweep_modals)
_watchdog.start(200)


def make_window() -> MainWindow:
    """建一个窗口，并且把「真的会弹出模态窗」的那一个出口堵上。

    设置页的按钮是真按钮：点下去信号会一路走到 MainWindow.open_auto_label_config，
    再到标注页把 AutoLabelDialog.exec() 开出来。离屏跑测试时那个模态窗没人点，
    exec() 就永远不返回——测试挂死。所以这里统一换成替身：只记「请求打开哪一页」，
    不开窗。要验证「窗口层真的会把对话框开出来」的测试自己再换一个更细的替身
    （见 test_main_window_opens_the_config_dialog_and_refreshes_status）。

    返回 (window, opened)，opened 里是被请求打开的 section 列表。
    """
    from gui import main_window as mw
    mw.db = db
    window = MainWindow()

    opened = []
    window.annotate_page.open_auto_label_config = (
        lambda section="": (opened.append(section), False)[1]
    )
    return window, opened


def test_settings_page_has_a_real_config_button():
    """「AI 与自动标注」里必须有能点的入口按钮，而不只是一句说明。"""
    window, _ = make_window()
    page = window.settings_page

    labels = [
        btn.text() for btn in page.findChildren(QPushButton)
        if btn.text().strip()
    ]
    assert "打开自动标注配置" in labels, f"设置页没有配置入口按钮，只有: {labels}"

    window.close()


def test_settings_buttons_emit_config_request():
    """三个入口按钮分别请求打开：默认页 / SAM / LLM，并且请求真的送到了窗口层。

    只断言信号还不够——信号没人接也一样过。所以同时看替身收到了什么：
    按钮 → 信号 → MainWindow → 标注页的配置窗口，这条线整条都得通。
    """
    window, opened = make_window()
    page = window.settings_page

    requested = []
    page.auto_label_config_requested.connect(requested.append)

    page.btn_open_auto_label.click()
    page.btn_config_sam.click()
    page.btn_config_llm.click()

    assert requested == ['', 'sam', 'llm'], requested
    assert opened == ['', 'sam', 'llm'], f"请求没走到标注页的配置窗口: {opened}"

    window.close()


def test_settings_status_refreshes_after_config_closes():
    """配置窗口一关，设置页显示的 SAM / LLM 状态必须重新读一遍。

    否则用户刚配好，回到设置页看到的还是「还没配置过」。
    """
    window, _ = make_window()

    refreshed = []
    window.settings_page.refresh_ai_status = lambda: refreshed.append(True)

    window.settings_page.btn_config_sam.click()

    assert refreshed, "配置窗口关掉之后没有刷新设置页的状态"

    window.close()


def test_config_entry_works_without_a_project():
    """没有项目也要能配 SAM / LLM——配置是全局文件，不该被「先建项目」挡住。"""
    window, opened = make_window()
    window.current_project_id = None

    window.settings_page.btn_config_llm.click()

    assert opened == ['llm'], f"没有项目时配置入口被挡住了: {opened}"

    window.close()


def test_main_window_opens_the_config_dialog_and_refreshes_status():
    """点设置页的按钮 → 主窗口真的把配置对话框开出来，关掉之后刷新 SAM/LLM 状态。

    这个测试要验证的正是 make_window() 堵掉的那一段，所以把真的
    open_auto_label_config 还回来，只把最里面那层模态 exec() 换成替身
    （PyQt 的类方法改不动，实例属性可以）：不弹窗，只确认「开的是哪一个
    对话框、落在哪一页」。exec() 立刻返回 Rejected，测试不会等任何人点。
    """
    window, _ = make_window()
    del window.annotate_page.open_auto_label_config  # 恢复类上的真实现

    window.switch_page(PAGE_SETTINGS)
    _app.processEvents()

    page = window.annotate_page
    page.init_auto_label_components()
    dialog = page.auto_label_dialog

    opened = {}

    def fake_exec():
        opened['tab'] = dialog.tab_widget.currentIndex()
        opened['dialog'] = type(dialog).__name__
        return QDialog.DialogCode.Rejected

    dialog.exec = fake_exec

    refreshed = []
    original_refresh = window.settings_page.refresh_ai_status

    def counting_refresh():
        refreshed.append(True)
        original_refresh()

    window.settings_page.refresh_ai_status = counting_refresh

    window.settings_page.btn_config_llm.click()

    assert opened.get('dialog') == 'AutoLabelDialog', opened
    assert opened.get('tab') == 2, f"LLM 入口应该直接落在 LLM 视觉那一页，实际 {opened.get('tab')}"
    assert refreshed, "配置窗口关掉之后没有重新读一次 SAM/LLM 状态"

    # 入口靠信号 + 主窗口胶水连起来：用户不用先被扔到标注页去找按钮
    assert window.content_stack.currentWidget() is window.settings_page

    window.close()


class _ConfigClickingBox(QMessageBox):
    """替身提示框：直接点掉「去配置」，测试里不需要真人点。"""

    seen = {}

    def exec(self):
        _ConfigClickingBox.seen = {
            'text': self.text(),
            'buttons': [b.text() for b in self.buttons()],
        }
        for button in self.buttons():
            if "配置" in button.text():
                self._picked = button
                return 0
        self._picked = None
        return 0

    def clickedButton(self):
        return self._picked


def test_missing_sam_offers_a_config_action():
    """SAM 没配置时，给的是「去配置」按钮，而且点了真的开配置窗口的 SAM 页。"""
    window, calls = make_window()
    page = window.annotate_page

    original_box = annotate_module.QMessageBox
    annotate_module.QMessageBox = _ConfigClickingBox
    try:
        page._offer_auto_label_config(
            "还没配置 SAM 模型", "SAM 交互分割要先选好分割模型和权重文件。", 'sam'
        )
    finally:
        annotate_module.QMessageBox = original_box

    seen = _ConfigClickingBox.seen
    assert any("配置" in text for text in seen.get('buttons', [])), seen
    assert calls == ['sam'], f"点「去配置」应该打开 SAM 那一页，实际: {calls}"
    # 提示里不再只是甩一句「点击: 自动标注 → 设置 → …」的路径
    assert "点击:" not in seen.get('text', '')

    window.close()


def test_missing_llm_key_offers_a_config_action():
    """没填 API Key 时，_require_llm_config 给的是配置入口，而不是一句说明就完事。"""
    window, _ = make_window()
    page = window.annotate_page

    page._load_llm_config = staticmethod(lambda: {'api_key': '', 'model_name': ''})

    offered = []

    def fake_offer(title, message, section):
        offered.append(section)
        return False  # 用户点了取消

    page._offer_auto_label_config = fake_offer

    assert page._require_llm_config() is None
    assert offered == ['llm'], offered

    window.close()


def test_zz_no_real_modal_window_was_opened():
    """整个文件跑下来，一个真的模态窗都不该被打开过。

    名字带 zz 是为了排在最后：前面的测试都跑完，看板才有意义。
    """
    assert not ESCAPED_MODALS, (
        f"有真模态窗被打开了（已被兜底关掉，否则测试会挂死）: {ESCAPED_MODALS}"
    )


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
