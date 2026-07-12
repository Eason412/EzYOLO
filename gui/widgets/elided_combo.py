# -*- coding: utf-8 -*-
"""会省略的下拉框

QComboBox 自己不省略：文字比框宽时它就直接裁掉——中文会被拦腰切掉半个字，
看着像渲染坏了。项目名是用户随便起的，长度不受控，侧栏又是固定宽度，
所以这里自己画一遍文字，装不下就用省略号收尾。

同目录的 ElidedLabel 是同一个思路，只是那个针对 QLabel。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter


class ElidedComboBox(QComboBox):
    """当前选项装不下时以省略号结尾。"""

    def elided_text(self) -> str:
        """当前选项真正会被画出来的那串字（装得下就是原文）。"""
        option = QStyleOptionComboBox()
        self.initStyleOption(option)

        # 文字能用的宽度要问样式要：右边的箭头区是样式表给的内边距，
        # 自己按控件总宽度算会算多，正好又把最后一个字切掉。
        text_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        return QFontMetrics(self.font()).elidedText(
            option.currentText, Qt.TextElideMode.ElideRight, text_rect.width()
        )

    def paintEvent(self, _event):
        painter = QStylePainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))

        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = self.elided_text()

        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
