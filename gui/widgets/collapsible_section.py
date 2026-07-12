# -*- coding: utf-8 -*-
"""默认收起的分区。

给「用得着、但不是每次都用」的功能准备的：标注页右栏的样本管理和数据导出
都属于这一类——常驻展开只会把类别和 AI 入口挤到屏幕外面，可它们又不该被藏进
菜单里找不着。收起来只留一行标题，点一下才展开。

没有动画，也不记住展开状态：每次进页面都从收起开始，行为可预期。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QPushButton, QVBoxLayout, QWidget

from gui.styles import COLORS, RADIUS, RADIUS_SM


class CollapsibleSection(QFrame):
    """一张卡片：标题一行，内容默认收起。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title

        self.setObjectName("card")
        self.setStyleSheet(f"""
            QFrame#card {{
                background-color: {COLORS['panel']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADIUS}px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # 标题即开关。整行都能点，不用去瞄那个小三角。
        self.toggle = QPushButton()
        self.toggle.setObjectName("ghost")
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setMinimumHeight(28)
        self.toggle.setStyleSheet(f"""
            QPushButton#ghost {{
                text-align: left;
                padding: 4px 6px;
                border-radius: {RADIUS_SM}px;
                color: {COLORS['text_primary']};
                font-weight: 600;
            }}
            QPushButton#ghost:hover {{
                background-color: {COLORS['hover']};
            }}
            QPushButton#ghost:checked {{
                background-color: transparent;
            }}
        """)
        self.toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self.toggle)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 2)
        content_layout.setSpacing(8)
        self.content.setVisible(False)
        layout.addWidget(self.content)

        self._content_layout = content_layout
        self._on_toggled(False)

    def _on_toggled(self, checked: bool):
        self.content.setVisible(checked)
        self.toggle.setText(f"{'▾' if checked else '▸'}  {self._title}")

    # ==================== 对外 ====================

    def content_layout(self) -> QVBoxLayout:
        """往里放东西用这个。"""
        return self._content_layout

    def add_widget(self, widget: QWidget):
        self._content_layout.addWidget(widget)

    def add_layout(self, layout):
        self._content_layout.addLayout(layout)

    def set_expanded(self, expanded: bool):
        self.toggle.setChecked(expanded)

    def is_expanded(self) -> bool:
        return self.toggle.isChecked()
