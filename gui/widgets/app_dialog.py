# -*- coding: utf-8 -*-
"""应用内统一弹窗（确认 / 提示）

替掉 QMessageBox：系统那个框在 macOS 上是英文 No/Yes 加一个巨大的问号图标，
跟应用其余部分（浅色、圆角、中文）完全不是一套东西，而且「Yes」放在右边、
默认还落在它上面——删除这种不可逆操作，回车一下就没了。

这里的规矩：
    标题说清楚要做什么，正文说清楚后果，细节（数量之类）单独一行；
    破坏性操作用红字按钮，取消是普通按钮并且拿默认焦点（回车 = 取消）；
    没有图标。

用法：
    if confirm_destructive(self, "删除项目", "…会一起删除，无法恢复。", "「安全帽检测」"):
        ...
"""

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)


class AppDialog(QDialog):
    """确认框 / 提示框的共同外壳。

    只有一个按钮时是提示框，两个按钮时是确认框。
    `exec()` 之外也能用：测试里直接点 `btn_confirm` / `btn_cancel` 就行，
    结果照样落在 `result()` 上，不用真的开一个模态窗。
    """

    def __init__(self, parent, title: str, message: str,
                 detail: str = "",
                 confirm_text: str = "确定",
                 cancel_text: Optional[str] = "取消",
                 destructive: bool = False):
        super().__init__(parent)

        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self.setMaximumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(8)

        self.lbl_title = QLabel(title)
        self.lbl_title.setObjectName("h2")
        self.lbl_title.setWordWrap(True)
        layout.addWidget(self.lbl_title)

        self.lbl_message = QLabel(message)
        self.lbl_message.setObjectName("subtitle")
        self.lbl_message.setWordWrap(True)
        layout.addWidget(self.lbl_message)

        self.lbl_detail = QLabel(detail)
        self.lbl_detail.setObjectName("caption")
        self.lbl_detail.setWordWrap(True)
        self.lbl_detail.setVisible(bool(detail))
        layout.addWidget(self.lbl_detail)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 10, 0, 0)
        button_row.setSpacing(8)
        button_row.addStretch()

        self.btn_cancel = None
        if cancel_text:
            self.btn_cancel = QPushButton(cancel_text)
            self.btn_cancel.clicked.connect(self.reject)
            button_row.addWidget(self.btn_cancel)

        self.btn_confirm = QPushButton(confirm_text)
        self.btn_confirm.setObjectName("danger" if destructive else "primary")
        self.btn_confirm.clicked.connect(self.accept)
        button_row.addWidget(self.btn_confirm)

        layout.addLayout(button_row)

        # 默认焦点给「安全的那个」：有取消就是取消，没有就是唯一那个按钮。
        # 回车键同理——删 164 张图片不该是一个回车就发生的事。
        safe_button = self.btn_cancel or self.btn_confirm
        safe_button.setDefault(True)
        safe_button.setFocus()


def confirm_destructive(parent, title: str, message: str, detail: str = "",
                        confirm_text: str = "删除", cancel_text: str = "取消") -> bool:
    """不可逆操作的确认框：红色确认按钮，默认焦点在安全的那个按钮。用户确认才返回 True。

    `cancel_text` 要照实说安全按钮到底会做什么：不是每一次「不确认」都等于
    「什么都不做」——比如导入标注时不覆盖，实际是「保留现有标注」并继续导入。
    这种时候写「取消」会让人以为整件事都被放弃了。
    """
    dialog = AppDialog(
        parent, title, message, detail,
        confirm_text=confirm_text, cancel_text=cancel_text, destructive=True,
    )
    return dialog.exec() == QDialog.DialogCode.Accepted


def confirm(parent, title: str, message: str, detail: str = "",
            confirm_text: str = "确定", cancel_text: str = "取消") -> bool:
    """普通的二选一确认框（不是破坏性操作）。

    `cancel_text` 同样照实说：二选一的「另一个选项」未必叫「取消」。
    """
    dialog = AppDialog(
        parent, title, message, detail,
        confirm_text=confirm_text, cancel_text=cancel_text, destructive=False,
    )
    return dialog.exec() == QDialog.DialogCode.Accepted


class TextInputDialog(QDialog):
    """要用户填一行字的框（比如给新项目起名字）。

    替掉 QInputDialog.getText：系统那个框跟抽帧设置用的 getInt 是同一个模子，
    英文 Cancel/OK、跟应用完全不是一套外观。这里跟 AppDialog 长一个样子。

    确认按钮在输入为空时是灰的——名字都没填就没什么可确认的。
    """

    def __init__(self, parent, title: str, label: str,
                 text: str = "", placeholder: str = "",
                 confirm_text: str = "确定", cancel_text: str = "取消"):
        super().__init__(parent)

        self.setWindowTitle(title)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(8)

        self.lbl_title = QLabel(title)
        self.lbl_title.setObjectName("h2")
        layout.addWidget(self.lbl_title)

        self.lbl_label = QLabel(label)
        self.lbl_label.setObjectName("subtitle")
        self.lbl_label.setWordWrap(True)
        layout.addWidget(self.lbl_label)

        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self._sync_confirm_enabled)
        self.edit.returnPressed.connect(self._on_return)
        layout.addWidget(self.edit)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 10, 0, 0)
        button_row.setSpacing(8)
        button_row.addStretch()

        self.btn_cancel = QPushButton(cancel_text)
        self.btn_cancel.clicked.connect(self.reject)
        button_row.addWidget(self.btn_cancel)

        self.btn_confirm = QPushButton(confirm_text)
        self.btn_confirm.setObjectName("primary")
        self.btn_confirm.setDefault(True)
        self.btn_confirm.clicked.connect(self.accept)
        button_row.addWidget(self.btn_confirm)

        layout.addLayout(button_row)

        self.edit.setFocus()
        self._sync_confirm_enabled()

    def _sync_confirm_enabled(self):
        self.btn_confirm.setEnabled(bool(self.edit.text().strip()))

    def _on_return(self):
        """回车 = 点确认，但空输入时什么也不发生（跟按钮灰着是一回事）。"""
        if self.btn_confirm.isEnabled():
            self.accept()

    def value(self) -> str:
        """用户填的内容（首尾空白已经去掉）。"""
        return self.edit.text().strip()


def ask_text(parent, title: str, label: str, text: str = "", placeholder: str = "",
             confirm_text: str = "确定") -> Optional[str]:
    """问用户要一行字。取消或没填返回 None。"""
    dialog = TextInputDialog(
        parent, title, label, text=text, placeholder=placeholder,
        confirm_text=confirm_text,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.value() or None


def show_info(parent, title: str, message: str, detail: str = ""):
    """只有一个「好」按钮的提示框。"""
    AppDialog(parent, title, message, detail,
              confirm_text="好", cancel_text=None).exec()


def show_warning(parent, title: str, message: str, detail: str = ""):
    """出问题了，但不需要用户做选择。"""
    AppDialog(parent, title, message, detail,
              confirm_text="知道了", cancel_text=None).exec()
