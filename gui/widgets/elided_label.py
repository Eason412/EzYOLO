# -*- coding: utf-8 -*-
"""单行省略标签

QMacStyle 下 QFormLayout 对「会换行的长值标签」行高计算不可靠：文字折行后
裁切、与相邻行重叠。长值一律用这个控件——永远单行，宽度不够时按省略模式
截断，完整内容放 tooltip。高度恒等于一行文字，不会再撑坏或压塌所在行。
"""

from PyQt6.QtWidgets import QLabel, QSizePolicy
from PyQt6.QtCore import Qt


class ElidedLabel(QLabel):
    """单行标签：装不下时省略显示，鼠标悬停看全文。"""

    def __init__(self, text: str = "", mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
                 parent=None):
        super().__init__(parent)
        self._full_text = ""
        self._mode = mode
        # 最小宽度不跟着文字长度走，布局才能自由压缩这一列
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(48)
        self.setText(text)

    def setText(self, text: str):
        self._full_text = text
        self.setToolTip(text)
        self._update_elided()

    def fullText(self) -> str:
        return self._full_text

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(48)
        return hint

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self):
        rect = self.contentsRect()
        elided = self.fontMetrics().elidedText(self._full_text, self._mode, max(0, rect.width()))
        super().setText(elided)
