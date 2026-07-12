# -*- coding: utf-8 -*-
"""
可视化页面

占位页。训练可视化已经并进主流程——曲线和指标在「④ 结果分析」，
在新数据上验证在「⑤ 模型测试」——这里不再单独实现，也没有挂在主窗口里，
保留只是为了兼容旧入口。真进来了就把话说清楚：这页是干什么的、现在什么状态、回哪去。
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame


class VisualizePage(QWidget):
    """可视化页面"""

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        """初始化界面"""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch()

        card = QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(560)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(10)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(10)

        badge = QLabel("占位页")
        badge.setObjectName("step_badge")
        badge_row.addWidget(badge)

        title = QLabel("训练可视化")
        title.setObjectName("h2")
        badge_row.addWidget(title)
        badge_row.addStretch()

        layout.addLayout(badge_row)

        for text in (
            "用途：查看训练曲线、准确率指标和预测示例。",
            "当前状态：这些内容已经并入主流程，本页不再单独提供。",
            "回到主流程：左侧「④ 结果分析」看曲线和指标，「⑤ 模型测试」拿新图片验证模型。",
        ):
            label = QLabel(text)
            label.setObjectName("subtitle")
            label.setWordWrap(True)
            layout.addWidget(label)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(card)
        row.addStretch()
        outer.addLayout(row)

        outer.addStretch()
