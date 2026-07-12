# -*- coding: utf-8 -*-
"""图片分组选择对话框"""

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QMessageBox, QRadioButton, QButtonGroup, QWidget
)

from models.database import db

UNGROUPED_SENTINEL = 0


class GroupSelectDialog(QDialog):
    """选择已有分组、新建分组，或移出分组。

    三种去向各自是一个选项，选中哪个就只有哪个的输入框可用，
    避免「选了下拉框却在输入框里打了字」这种说不清用哪个的状态。
    """

    def __init__(
        self,
        parent,
        project_id: int,
        title: str = "选择分组",
        allow_ungroup: bool = False,
        allow_skip: bool = False,
    ):
        super().__init__(parent)
        self.project_id = project_id
        self.allow_ungroup = allow_ungroup
        self.allow_skip = allow_skip
        self._result_group_id: Optional[int] = None
        self._cancelled = True

        self.setWindowTitle(title)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        heading = QLabel(title)
        heading.setObjectName("h2")
        layout.addWidget(heading)

        self.mode_group = QButtonGroup(self)

        # ---- 选项 1：已有分组 ----
        self.radio_existing = QRadioButton("放入已有分组")
        self.mode_group.addButton(self.radio_existing)
        layout.addWidget(self.radio_existing)

        self.group_combo = QComboBox()
        group_count = self._refresh_groups()
        layout.addWidget(self._indent(self.group_combo))

        # ---- 选项 2：新建分组 ----
        self.radio_new = QRadioButton("新建分组")
        self.mode_group.addButton(self.radio_new)
        layout.addWidget(self.radio_new)

        self.new_group_input = QLineEdit()
        self.new_group_input.setPlaceholderText("输入新分组名称")
        layout.addWidget(self._indent(self.new_group_input))

        # ---- 选项 3：移出分组 ----
        if allow_ungroup:
            self.radio_ungroup = QRadioButton("移出分组（变为未分组）")
            self.mode_group.addButton(self.radio_ungroup)
            layout.addWidget(self.radio_ungroup)

            ungroup_hint = QLabel("图片仍留在项目里，只是不属于任何分组")
            ungroup_hint.setObjectName("caption")
            layout.addWidget(self._indent(ungroup_hint))
        else:
            self.radio_ungroup = None

        # 项目还没有任何分组时，默认停在「新建」，省得用户先选中再改
        if group_count == 0:
            self.radio_new.setChecked(True)
            empty_hint = QLabel("当前项目还没有分组")
            empty_hint.setObjectName("caption")
            layout.addWidget(empty_hint)
        else:
            self.radio_existing.setChecked(True)

        # 直接操作某个输入控件时，自动选中它对应的选项
        self.group_combo.activated.connect(lambda _: self.radio_existing.setChecked(True))
        self.new_group_input.textEdited.connect(lambda _: self.radio_new.setChecked(True))
        for radio in self.mode_group.buttons():
            radio.toggled.connect(self._sync_inputs)
        self._sync_inputs()

        layout.addSpacing(4)

        btn_row = QHBoxLayout()
        if allow_skip:
            skip_btn = QPushButton("不分组")
            skip_btn.setObjectName("ghost")
            skip_btn.setToolTip("继续操作，但图片保持未分组")
            skip_btn.clicked.connect(self._on_skip)
            btn_row.addWidget(skip_btn)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("secondary")
        cancel_btn.setToolTip("放弃本次操作，不做任何改动")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("primary")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

    def _indent(self, widget: QWidget) -> QWidget:
        """把输入控件缩进到所属单选项下面。"""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(24, 0, 0, 0)
        row.addWidget(widget)
        return holder

    def _sync_inputs(self):
        """只让当前选中方式的输入控件可用。"""
        self.group_combo.setEnabled(self.radio_existing.isChecked())
        self.new_group_input.setEnabled(self.radio_new.isChecked())

    def _refresh_groups(self) -> int:
        """填充已有分组下拉框，返回用户自建分组的数量。"""
        self.group_combo.clear()
        groups = db.get_project_image_groups(self.project_id)
        counts = db.get_group_image_counts(self.project_id)
        ungrouped_count = counts.get(None, 0)
        self.group_combo.addItem(f"未分组 ({ungrouped_count})", UNGROUPED_SENTINEL)
        for group in groups:
            count = counts.get(group['id'], 0)
            self.group_combo.addItem(f"{group['name']} ({count})", group['id'])
        return len(groups)

    def _on_skip(self):
        self._result_group_id = None
        self._cancelled = False
        self.accept()

    def _on_accept(self):
        if self.radio_ungroup and self.radio_ungroup.isChecked():
            self._result_group_id = None
            self._cancelled = False
            self.accept()
            return

        if self.radio_new.isChecked():
            name = self.new_group_input.text().strip()
            if not name:
                QMessageBox.warning(self, "提示", "请输入新分组名称")
                self.new_group_input.setFocus()
                return
            try:
                self._result_group_id = db.create_image_group(self.project_id, name)
            except ValueError as e:
                QMessageBox.warning(self, "提示", str(e))
                return
        else:
            group_id = self.group_combo.currentData()
            if group_id == UNGROUPED_SENTINEL:
                self._result_group_id = None
            else:
                self._result_group_id = group_id

        self._cancelled = False
        self.accept()

    def was_cancelled(self) -> bool:
        return self._cancelled

    def get_selected_group_id(self) -> Optional[int]:
        """返回分组 ID；None 表示未分组。"""
        return self._result_group_id


def ask_import_group(parent, project_id: int) -> tuple[bool, Optional[int]]:
    """导入前询问是否放入分组。

    一个弹窗就能给出三种去向：选分组 / 不分组 / 取消导入，
    不再先弹一个「要不要分组」的是非框、再弹一个选择框。

    Returns:
        (proceed, group_id)
        - proceed=False: 用户取消导入
        - proceed=True, group_id=None: 导入但不分组
        - proceed=True, group_id=int: 导入到指定分组
    """
    dialog = GroupSelectDialog(
        parent,
        project_id,
        title="选择导入分组",
        allow_ungroup=False,
        allow_skip=True,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.was_cancelled():
        return False, None
    return True, dialog.get_selected_group_id()
