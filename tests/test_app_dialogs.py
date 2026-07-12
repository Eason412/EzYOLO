# -*- coding: utf-8 -*-
"""应用内弹窗 + 自动标注配置的保存反馈。

覆盖的真实问题：
    1. 删除类操作弹的是系统 QMessageBox：英文 No/Yes、一个巨大的问号图标，
       而且默认焦点落在「Yes」上——回车一下 164 张图片就没了。
       现在换成应用内弹窗：中文按钮、红色破坏性操作、默认焦点在「取消」、没有图标。
    2. 自动标注配置点「保存设置」：原来不管有没有真的写进文件都直接关窗，
       写失败时用户看到的是「点了保存，什么都没发生」，下次打开发现设置根本没存上。
    3. 从设置页打开配置窗口，保存完应该还在设置页，并且能看见「已保存」，
       而不是被甩到别的步骤去。

运行：
    python tests/test_app_dialogs.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QDialog, QLabel, QMessageBox

import gui.pages.auto_label_dialog as auto_label_module
from gui.widgets.app_dialog import AppDialog, TextInputDialog, ask_text
from gui.pages.auto_label_dialog import AutoLabelDialog
from gui.main_window import MainWindow
from gui.workflow import PAGE_SETTINGS, STEP_IMPORT

_app = _bootstrap.app()

_TMP_DIR = Path(tempfile.mkdtemp(prefix="ezyolo-app-dialogs-"))


# ==================== 破坏性确认框 ====================

def _destructive_dialog() -> AppDialog:
    return AppDialog(
        None, "清空图片", "当前项目里的图片会全部删除，无法恢复。",
        detail="共 164 张图片。",
        confirm_text="删除 164 张", cancel_text="取消", destructive=True,
    )


def test_destructive_dialog_speaks_chinese_and_has_no_giant_icon():
    """按钮是中文，不是 Yes/No；框里没有那个巨大的系统问号图标。"""
    dialog = _destructive_dialog()

    assert dialog.btn_confirm.text() == "删除 164 张"
    assert dialog.btn_cancel.text() == "取消"
    for button in (dialog.btn_confirm, dialog.btn_cancel):
        assert button.text() not in ("Yes", "No", "OK", "Cancel"), button.text()

    assert not isinstance(dialog, QMessageBox), "不能再用系统消息框"
    # 系统那个问号图标是一个带 pixmap 的 QLabel；我们这里所有标签都只有文字
    for label in dialog.findChildren(QLabel):
        pixmap = label.pixmap()
        assert pixmap is None or pixmap.isNull(), "弹窗里不该有图标"

    # 数量单独一行，用户点之前看得见到底要删多少
    assert "164" in dialog.lbl_detail.text()
    assert dialog.lbl_detail.isVisible() or dialog.lbl_detail.text()


def test_destructive_dialog_defaults_to_the_safe_action():
    """默认焦点和回车都落在「取消」上：删除不该是一个回车就发生的事。"""
    dialog = _destructive_dialog()

    assert dialog.btn_cancel.isDefault(), "默认按钮必须是取消"
    assert not dialog.btn_confirm.isDefault(), "破坏性按钮不能是默认按钮"
    assert dialog.btn_confirm.objectName() == "danger", "破坏性操作要用红色样式"


def test_destructive_dialog_returns_accepted_only_when_confirmed():
    """点确认 = Accepted，点取消 = Rejected。（不用真开模态窗，直接点按钮）"""
    confirmed = _destructive_dialog()
    confirmed.btn_confirm.click()
    assert confirmed.result() == QDialog.DialogCode.Accepted

    cancelled = _destructive_dialog()
    cancelled.btn_cancel.click()
    assert cancelled.result() == QDialog.DialogCode.Rejected


def test_info_dialog_has_a_single_button_and_no_cancel():
    dialog = AppDialog(None, "已删除", "删除了 12 张图片。", confirm_text="好", cancel_text=None)

    assert dialog.btn_cancel is None
    assert dialog.btn_confirm.text() == "好"
    assert dialog.btn_confirm.isDefault()


def test_safe_button_label_can_say_what_it_actually_does():
    """安全按钮不一定叫「取消」：二选一时它是「另一个选项」，必须照实说。

    导入标注时不覆盖，实际是「保留现有标注」并继续导入——写「取消」会让人
    以为整件事都放弃了。这里确认两个 helper 都能自定义这个标签，
    而且默认焦点还是落在这个安全按钮上。
    """
    seen = {}

    class _Spy(AppDialog):
        def exec(self):
            seen['confirm'] = self.btn_confirm.text()
            seen['cancel'] = self.btn_cancel.text()
            seen['cancel_is_default'] = self.btn_cancel.isDefault()
            return QDialog.DialogCode.Rejected

    with patch("gui.widgets.app_dialog.AppDialog", _Spy):
        from gui.widgets.app_dialog import confirm as _confirm
        from gui.widgets.app_dialog import confirm_destructive as _confirm_destructive

        assert _confirm_destructive(
            None, "覆盖已有标注", "…", confirm_text="覆盖", cancel_text="保留现有标注",
        ) is False
        assert seen == {
            'confirm': "覆盖", 'cancel': "保留现有标注", 'cancel_is_default': True,
        }, seen

        _confirm(None, "图像文件夹", "…", confirm_text="去选择", cancel_text="不用")
        assert seen['cancel'] == "不用", seen


# ==================== 文字输入框（新建项目） ====================

def test_text_input_dialog_is_chinese_and_blocks_empty_input():
    """替掉 QInputDialog.getText：中文按钮，名字没填时确认按钮点不动。"""
    dialog = TextInputDialog(
        None, "新建项目", "给项目起个名字。",
        placeholder="比如：安全帽检测", confirm_text="创建项目",
    )

    assert dialog.btn_confirm.text() == "创建项目"
    assert dialog.btn_cancel.text() == "取消"
    assert not dialog.btn_confirm.isEnabled(), "还没填名字，创建按钮不该能点"

    dialog.edit.setText("   ")
    assert not dialog.btn_confirm.isEnabled(), "只有空格也算没填"

    dialog.edit.setText("  安全帽检测  ")
    assert dialog.btn_confirm.isEnabled()
    assert dialog.value() == "安全帽检测", "首尾空格要去掉"

    # 跟别的应用内弹窗一样：没有系统那个大图标
    for label in dialog.findChildren(QLabel):
        pixmap = label.pixmap()
        assert pixmap is None or pixmap.isNull()


def test_ask_text_returns_none_when_cancelled_or_blank():
    """取消 → None；点了确认但内容是空白 → 也当成没填。"""
    with patch.object(TextInputDialog, "exec",
                      lambda self: QDialog.DialogCode.Rejected):
        assert ask_text(None, "新建项目", "名字") is None

    def _accept_blank(self):
        self.edit.setText("   ")
        return QDialog.DialogCode.Accepted

    with patch.object(TextInputDialog, "exec", _accept_blank):
        assert ask_text(None, "新建项目", "名字") is None

    def _accept_name(self):
        self.edit.setText(" 安全帽检测 ")
        return QDialog.DialogCode.Accepted

    with patch.object(TextInputDialog, "exec", _accept_name):
        assert ask_text(None, "新建项目", "名字") == "安全帽检测"


# ==================== 自动标注配置：保存反馈 ====================

def _auto_label_dialog(tmp_name: str):
    """建一个配置窗口，并把两份配置文件指到临时目录，不碰仓库里的 config/。"""
    sam_path = _TMP_DIR / tmp_name / "sam_config.json"
    llm_path = _TMP_DIR / tmp_name / "llm_config.json"
    sam_path.parent.mkdir(parents=True, exist_ok=True)
    return AutoLabelDialog(None), str(sam_path), str(llm_path)


def test_save_writes_both_configs_and_closes():
    """保存成功：两份配置都落盘，窗口关掉并返回 Accepted。"""
    dialog, sam_path, llm_path = _auto_label_dialog("ok")

    with patch.object(auto_label_module, "SAM_CONFIG_FILE", sam_path), \
         patch.object(auto_label_module, "LLM_CONFIG_FILE", llm_path), \
         patch.object(auto_label_module, "_sam_model_exists", lambda _f: True):
        dialog.le_llm_api_key.setText("test-key")
        dialog.on_save_clicked()

    assert dialog.result() == QDialog.DialogCode.Accepted, "保存成功应该关窗"
    assert json.loads(Path(sam_path).read_text())['sam_type']
    assert json.loads(Path(llm_path).read_text())['api_key'] == "test-key"


def test_save_failure_keeps_the_dialog_open_and_says_why():
    """写不进去时：不能默默关窗，要留在原地并说清楚哪份配置没存上。"""
    dialog, _sam_path, llm_path = _auto_label_dialog("fail")

    # 把 SAM 配置的路径指到「一个文件底下」——目录建不出来，写必然失败
    blocker = _TMP_DIR / "fail" / "not_a_dir"
    blocker.write_text("我是一个文件，不是目录")
    unwritable_sam = str(blocker / "sam_config.json")

    with patch.object(auto_label_module, "SAM_CONFIG_FILE", unwritable_sam), \
         patch.object(auto_label_module, "LLM_CONFIG_FILE", llm_path), \
         patch.object(auto_label_module, "_sam_model_exists", lambda _f: True):
        dialog.on_save_clicked()

    assert dialog.result() != QDialog.DialogCode.Accepted, "保存失败时窗口不能关掉"

    notice = dialog.lbl_notice.text()
    assert "保存失败" in notice, notice
    assert "SAM" in notice, notice


def test_partial_save_says_which_one_failed_and_which_one_got_written():
    """一份写成功、一份写失败：不能笼统说「设置没有写入」。

    磁盘上此刻是半新半旧的状态，用户必须知道到底哪一份进去了，
    否则他会以为可以放心重试或干脆放弃。
    """
    dialog, _sam_path, llm_path = _auto_label_dialog("partial")

    # SAM 的路径指到「一个文件底下」——目录建不出来，必然写失败；LLM 正常可写
    blocker = _TMP_DIR / "partial" / "not_a_dir"
    blocker.write_text("我是一个文件，不是目录")
    unwritable_sam = str(blocker / "sam_config.json")

    with patch.object(auto_label_module, "SAM_CONFIG_FILE", unwritable_sam), \
         patch.object(auto_label_module, "LLM_CONFIG_FILE", llm_path), \
         patch.object(auto_label_module, "_sam_model_exists", lambda _f: True):
        dialog.le_llm_api_key.setText("half-written")
        dialog.on_save_clicked()

    assert dialog.result() != QDialog.DialogCode.Accepted, "有一份没写成就不该关窗"

    notice = dialog.lbl_notice.text()
    assert "SAM 配置" in notice and "没有写入" in notice, notice
    assert "LLM 配置 已经写入" in notice, f"写成功的那一份必须说出来: {notice!r}"
    assert "设置没有写入" not in notice, f"不能谎称什么都没写: {notice!r}"

    # 而且 LLM 那一份是真的落盘了——提示说的是事实
    assert json.loads(Path(llm_path).read_text())['api_key'] == "half-written"


def test_custom_model_without_a_file_is_refused_inline():
    """选了自定义模型却没选文件：就地说明，不弹系统框，也不保存一个用不了的配置。"""
    dialog, sam_path, llm_path = _auto_label_dialog("custom")

    dialog.rbtn_custom.setChecked(True)
    dialog.custom_model_path = ""

    with patch.object(auto_label_module, "SAM_CONFIG_FILE", sam_path), \
         patch.object(auto_label_module, "LLM_CONFIG_FILE", llm_path):
        dialog.on_save_clicked()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "模型文件" in dialog.lbl_notice.text(), dialog.lbl_notice.text()
    assert not Path(sam_path).exists(), "校验没过就不该写配置"


# ==================== 保存后回到打开它的那个页面 ====================

def _window_with_stubbed_config_dialog(accepted: bool, side_effect=None):
    """开一个主窗口，把配置窗口的 exec() 换成替身（离屏跑不能真开模态窗）。"""
    window = MainWindow()
    window.switch_page(PAGE_SETTINGS)
    _app.processEvents()

    page = window.annotate_page
    page.init_auto_label_components()
    dialog = page.auto_label_dialog

    def fake_exec():
        if side_effect:
            side_effect(window)
        return (QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected)

    dialog.exec = fake_exec
    return window


def test_saving_from_settings_stays_on_settings_and_shows_it_saved():
    """从设置页打开配置、点保存 → 还留在设置页，并且底部明确说「已保存」。"""
    window = _window_with_stubbed_config_dialog(accepted=True)

    window.settings_page.btn_config_sam.click()
    _app.processEvents()

    assert window.current_index == PAGE_SETTINGS, "保存后不该跳到别的步骤"
    assert window.content_stack.currentIndex() == PAGE_SETTINGS

    status = window.settings_page.status_label.text()
    assert "已保存" in status, f"保存完什么都不说，跟没保存一样：{status!r}"


def test_cancelling_from_settings_says_nothing_was_changed():
    """点取消：也留在设置页，但不能谎称「已保存」。"""
    window = _window_with_stubbed_config_dialog(accepted=False)

    window.settings_page.btn_config_llm.click()
    _app.processEvents()

    assert window.current_index == PAGE_SETTINGS
    status = window.settings_page.status_label.text()
    assert "已保存" not in status, f"没保存却说保存了：{status!r}"
    assert "没有改动" in status, status


def test_a_page_jump_during_config_is_undone_on_close():
    """配置窗口开着时页面被换走（比如跳去第 1 步数据导入），关窗后必须回到设置页。

    用户抱怨的就是这个：在设置页点保存，回来人却站在「数据导入」上。
    """
    def jump_to_import(window):
        window.switch_page(STEP_IMPORT)

    window = _window_with_stubbed_config_dialog(accepted=True, side_effect=jump_to_import)

    window.settings_page.btn_open_auto_label.click()
    _app.processEvents()

    assert window.current_index == PAGE_SETTINGS, "关窗后应该回到打开它的设置页"
    assert window.content_stack.currentIndex() == PAGE_SETTINGS


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(dict(globals())))
