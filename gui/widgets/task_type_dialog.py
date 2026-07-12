# -*- coding: utf-8 -*-
"""
任务类型选择对话框

原来新建项目、修改任务类型、导入标注三处各写了一份一模一样的对话框，
这里合并成一个，顺便给每种任务补一句大白话解释。
"""

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QRadioButton,
)

# (值, 标题, 解释)
TASK_TYPES = [
    ('detect', '目标检测 detect', '用方框圈出目标的位置，最常用，新手从这个开始。'),
    ('segment', '实例分割 segment', '沿着目标的轮廓描边，能得到精确形状。'),
    ('pose', '关键点检测 pose', '标出目标身上的若干个点，比如人体骨骼关节。'),
    ('classify', '图像分类 classify', '只判断整张图属于哪一类，不定位。'),
]

TASK_LABELS = {value: title for value, title, _ in TASK_TYPES}


def task_type_label(value: str) -> str:
    """把 detect 这种内部值翻成给人看的名字。"""
    return TASK_LABELS.get(value, value or '未设置')


def ask_task_type(parent, current: str = 'detect', title: str = "选择任务类型") -> Optional[str]:
    """弹出任务类型选择框。用户取消时返回 None。"""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(420)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(12)

    hint = QLabel("这个项目要让模型做什么？决定了标注工具和训练方式。")
    hint.setObjectName("subtitle")
    hint.setWordWrap(True)
    layout.addWidget(hint)

    # pose 在旧数据里可能存成 point
    normalized = 'pose' if current == 'point' else (current or 'detect')

    buttons = {}
    for value, label, explain in TASK_TYPES:
        radio = QRadioButton(label)
        radio.setChecked(value == normalized)
        layout.addWidget(radio)

        note = QLabel(explain)
        note.setObjectName("caption")
        note.setWordWrap(True)
        note.setContentsMargins(24, 0, 0, 6)
        layout.addWidget(note)

        buttons[value] = radio

    if not any(radio.isChecked() for radio in buttons.values()):
        buttons['detect'].setChecked(True)

    btn_row = QHBoxLayout()
    btn_row.setContentsMargins(0, 8, 0, 0)
    btn_row.addStretch()

    cancel_btn = QPushButton("取消")
    cancel_btn.clicked.connect(dialog.reject)
    btn_row.addWidget(cancel_btn)

    ok_btn = QPushButton("确定")
    ok_btn.setObjectName("primary")
    ok_btn.setDefault(True)
    ok_btn.clicked.connect(dialog.accept)
    btn_row.addWidget(ok_btn)

    layout.addLayout(btn_row)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    for value, radio in buttons.items():
        if radio.isChecked():
            return value
    return 'detect'
