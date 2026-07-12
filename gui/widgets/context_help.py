# -*- coding: utf-8 -*-
"""行内『使用说明』：默认收起，进入页面不占地方；点开才看到编号步骤。

跟 collapsible_section.py 一样不记状态——每次打开页面都从收起开始。
"""

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

_TOGGLE_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)


class _ToggleButton(QPushButton):
    """QPushButton 默认只有 Space 会触发点击；Enter 要显式接管。"""

    def keyPressEvent(self, event):
        if event.key() in _TOGGLE_KEYS:
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

from gui.styles import COLORS, RADIUS_SM

_ASSETS_DIR = Path(__file__).parent.parent / "assets"


def _svg_pixmap(name: str, size: int):
    return QIcon(str(_ASSETS_DIR / name)).pixmap(QSize(size, size))


def _icon_label(name: str, size: int) -> QLabel:
    label = QLabel()
    label.setPixmap(_svg_pixmap(name, size))
    label.setFixedSize(size, size)
    # 图标只是装饰：点击要穿透到下面的整行按钮上
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return label


def _warning_soft_background() -> str:
    """从 COLORS['warning_fill'] 派生一个克制的浅底色，不额外造新配色。"""
    hex_color = COLORS['warning_fill'].lstrip('#')
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, 0.12)"


class ContextHelp(QFrame):
    """可复用的行内使用说明：整行可点/可聚焦，展开后是编号步骤。"""

    def __init__(self, steps, parent=None, risk_steps=None):
        super().__init__(parent)
        self.setObjectName("contextHelp")
        self.setStyleSheet(f"""
            QFrame#contextHelp {{
                background-color: {COLORS['panel']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADIUS_SM}px;
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.toggle = _ToggleButton(self)
        self.toggle.setObjectName("contextHelpToggle")
        self.toggle.setCheckable(True)
        self.toggle.setAutoDefault(False)
        self.toggle.setDefault(False)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.toggle.setStyleSheet(f"""
            QPushButton#contextHelpToggle {{
                background-color: transparent;
                border: none;
                border-radius: {RADIUS_SM}px;
                text-align: left;
                padding: 6px 10px;
            }}
            QPushButton#contextHelpToggle:hover {{
                background-color: {COLORS['hover']};
            }}
            QPushButton#contextHelpToggle:focus {{
                background-color: {COLORS['hover']};
            }}
        """)

        row = QHBoxLayout(self.toggle)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        row.addWidget(_icon_label("book.svg", 16))

        self._label = QLabel("使用说明")
        self._label.setStyleSheet(f"color: {COLORS['text_primary']}; font-weight: 600; border: none;")
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self._label)

        row.addStretch(1)

        self._chevron = _icon_label("chevron_down.svg", 12)
        row.addWidget(self._chevron)

        outer.addWidget(self.toggle)

        self.content = QWidget(self)
        self.content.setVisible(False)
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(10, 0, 10, 8)
        content_layout.setSpacing(4)
        outer.addWidget(self.content)

        self._content_layout = content_layout

        self.toggle.toggled.connect(self._on_toggled)
        self.set_steps(steps, risk_steps=risk_steps)

    def _on_toggled(self, checked: bool):
        self.content.setVisible(checked)
        self._chevron.setPixmap(
            _svg_pixmap("chevron_up.svg" if checked else "chevron_down.svg", 12)
        )

    def set_steps(self, steps, risk_steps=None):
        """替换步骤内容；risk_steps 是需要高风险提示的步骤序号（从 1 开始）。"""
        risk_indices = set(risk_steps or [])

        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # deleteLater() 的实际销毁要等事件循环处理 DeferredDelete，
                # 但 setParent(None) 立刻把它从控件树摘掉，findChildren 不会再看到它
                widget.setParent(None)
                widget.deleteLater()

        for index, text in enumerate(steps, start=1):
            label = QLabel(f"{index}. {text}")
            label.setWordWrap(True)
            if index in risk_indices:
                label.setStyleSheet(
                    f"color: {COLORS['warning']}; border: none; border-radius: {RADIUS_SM}px; "
                    f"padding: 2px 6px; background-color: {_warning_soft_background()};"
                )
            else:
                label.setStyleSheet(f"color: {COLORS['text_secondary']}; border: none;")
            self._content_layout.addWidget(label)

    def is_expanded(self) -> bool:
        return self.toggle.isChecked()

    def set_expanded(self, expanded: bool):
        self.toggle.setChecked(expanded)
