# -*- coding: utf-8 -*-
"""
关于页面

辅助页：说清楚 EzYOLO 是什么、能做什么、什么版本、遇到问题找谁。
不堆宣传语，也不重复页头已经写过的标题。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel,
    QGroupBox, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt

VERSION = "1.1.0"
UPDATED = "2026-03-05"
AUTHOR = "xinchen"
GITHUB = "https://github.com/lxinchenl"
EMAIL = "liuxinchen0803@qq.com"

INTRO = "EzYOLO 把 YOLO 的完整流程收进一条固定路线：导入图片 → 标注 → 训练 → 看结果 → 测试。"

# 每一步做什么，对应左侧主流程的五步
CAPABILITIES = [
    ("数据导入", "图片/视频/标注文件"),
    ("数据标注", "手动/SAM/大模型标注"),
    ("模型训练", "本机训练 YOLO"),
    ("结果分析", "曲线、指标、导出"),
    ("模型测试", "验证新数据表现"),
]

HELP_TEXT = "有问题欢迎发邮件或到 GitHub 提 issue。"


class AboutPage(QWidget):
    """关于页面"""

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        """初始化界面"""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # 内容只有几行，铺满宽屏会变成一片空白。固定一列宽度，宽屏时右边留白。
        column = QWidget()
        column.setMaximumWidth(680)
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(14)
        column_layout.addWidget(self.create_intro_group())
        column_layout.addWidget(self.create_capability_group())
        column_layout.addWidget(self.create_version_group())
        column_layout.addWidget(self.create_help_group())

        layout.addWidget(column)
        layout.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)

    def create_intro_group(self) -> QGroupBox:
        """这是什么"""
        group = QGroupBox("这是什么")

        layout = QVBoxLayout(group)

        intro = QLabel(INTRO)
        intro.setObjectName("subtitle")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        return group

    def create_capability_group(self) -> QGroupBox:
        """主要能力：就是主流程的五步

        不用 QFormLayout：QMacStyle 下它给换行标签算的行高不可靠，说明文字
        折行后裁切、和相邻行重叠。显式网格把说明列拉伸到分组框剩余宽度，
        这些短句一行就装下，不再需要换行。
        """
        group = QGroupBox("主要能力")

        grid = QGridLayout(group)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        for row, (name, desc) in enumerate(CAPABILITIES):
            grid.addWidget(QLabel(name), row, 0, Qt.AlignmentFlag.AlignTop)

            desc_label = QLabel(desc)
            desc_label.setObjectName("subtitle")
            desc_label.setWordWrap(True)
            grid.addWidget(desc_label, row, 1)

        return group

    def create_version_group(self) -> QGroupBox:
        """版本信息

        和本页其他分组一样用网格：QFormLayout 在 QMacStyle 下会把整个表单
        水平居中，夹在几个左对齐的分组中间很突兀。
        """
        group = QGroupBox("版本")

        grid = QGridLayout(group)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        for row, (name, value) in enumerate([
            ("版本:", VERSION),
            ("更新日期:", UPDATED),
            ("作者:", AUTHOR),
        ]):
            grid.addWidget(QLabel(name), row, 0)
            grid.addWidget(QLabel(value), row, 1)

        return group

    def create_help_group(self) -> QGroupBox:
        """帮助与反馈

        链接和邮箱走网格 + 列拉伸：QFormLayout 在 QMacStyle 下值列不跟着
        分组框伸缩，GitHub 链接会被过早截断。
        """
        group = QGroupBox("帮助与反馈")

        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel("GitHub:"), 0, 0)
        grid.addWidget(self.create_selectable_label(GITHUB), 0, 1)
        grid.addWidget(QLabel("邮箱:"), 1, 0)
        grid.addWidget(self.create_selectable_label(EMAIL), 1, 1)

        layout.addLayout(grid)

        help_label = QLabel(HELP_TEXT)
        help_label.setObjectName("caption")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        return group

    def create_selectable_label(self, text: str) -> QLabel:
        """联系方式要能复制出去。"""
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setCursor(Qt.CursorShape.IBeamCursor)
        return label
