# -*- coding: utf-8 -*-
"""
加载对话框组件
显示加载动画和进度信息

两种形态：
    LoadingDialog  —— 独立的模态加载窗，用于阻塞式等待
    LoadingOverlay —— 盖在某个控件上的加载遮罩

两者都不带取消：调用方需要让用户中断时，应改用带取消按钮的 QProgressDialog，
所以这里明确告诉用户「完成后会自动关闭」，而不是留一个看不出能不能停的转圈。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QWidget, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen

from gui.styles import COLORS, RADIUS


class LoadingSpinner(QLabel):
    """加载旋转动画"""

    def __init__(self, size: int = 64, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setStyleSheet("background-color: transparent; border: none;")
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.timer.start(50)  # 每50ms更新一次

    def rotate(self):
        """旋转动画"""
        self.angle = (self.angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        """绘制旋转的圆环"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 绘制背景圆环
        pen = QPen(QColor(COLORS['border']))
        pen.setWidth(4)
        painter.setPen(pen)
        painter.drawEllipse(8, 8, self.width() - 16, self.height() - 16)

        # 绘制旋转的弧
        pen = QPen(QColor(COLORS['primary']))
        pen.setWidth(4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        painter.translate(self.width() // 2, self.height() // 2)
        painter.rotate(self.angle)
        painter.translate(-self.width() // 2, -self.height() // 2)

        painter.drawArc(8, 8, self.width() - 16, self.height() - 16, 0, 120 * 16)


def _card_style() -> str:
    """加载卡片的样式。

    只匹配 #loading_card 本身，不然 QWidget 选择器会把边框和圆角
    传染给里面的每一个子控件（文字和进度条都会被套上一圈边框）。
    """
    return f"""
        QWidget#loading_card {{
            background-color: {COLORS['panel']};
            border: 1px solid {COLORS['border']};
            border-radius: {RADIUS}px;
        }}
    """


def _elevate(widget: QWidget):
    """把卡片从浅色遮罩上托起来。

    浅底白卡只靠 1px 边框分不开，得有一点投影；
    这也是浅色遮罩能成立的前提——不用压黑背景，照样看得出「上面盖了一层」。
    """
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(28)
    shadow.setOffset(0, 6)
    shadow.setColor(QColor(0, 0, 0, 46))
    widget.setGraphicsEffect(shadow)


class LoadingDialog(QDialog):
    """加载对话框"""

    def __init__(self, parent=None, title: str = "加载中", message: str = "请稍候..."):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.init_ui(message)

        # 内容决定高度：文案长了会换行，不会被截断
        self.setMinimumWidth(320)
        self.adjustSize()

        # 居中显示
        if parent:
            self.move(
                parent.x() + (parent.width() - self.width()) // 2,
                parent.y() + (parent.height() - self.height()) // 2
            )

    def init_ui(self, message: str):
        """初始化界面"""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # 圆角卡片容器
        container = QWidget()
        container.setObjectName("loading_card")
        container.setStyleSheet(_card_style())
        _elevate(container)
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # 加载动画
        self.spinner = LoadingSpinner(56)
        layout.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignCenter)

        # 当前状态
        self.message_label = QLabel(message)
        self.message_label.setObjectName("title")
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.message_label)

        # 说明：不可取消，完成后自动关闭
        hint = QLabel("完成后会自动关闭")
        hint.setObjectName("caption")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        # 进度条：只有拿到确定进度时才出现
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

    def set_message(self, message: str):
        """设置消息文本"""
        self.message_label.setText(message)

    def set_progress(self, value: int):
        """设置进度"""
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(value)

    def showEvent(self, event):
        """显示事件"""
        super().showEvent(event)
        # 确保对话框在父窗口中央
        if self.parent():
            parent_rect = self.parent().geometry()
            self.move(
                parent_rect.center().x() - self.width() // 2,
                parent_rect.center().y() - self.height() // 2
            )


class LoadingOverlay(QWidget):
    """加载遮罩层 - 用于在控件上方显示加载状态"""

    def __init__(self, parent: QWidget, message: str = "加载中..."):
        super().__init__(parent)
        self.setObjectName("loading_scrim")
        self.setGeometry(parent.rect())

        # 遮罩用应用自己的浅色，不是 75% 的黑：整个界面都是浅色的，
        # 一压黑就像换了个应用。半透明的画布色 + 卡片投影，
        # 一样看得出「下面的东西现在点不了」，但不砸眼睛。
        # 只作用在遮罩自身，卡片和文字保持不透明。
        self.setStyleSheet(f"""
            QWidget#loading_scrim {{
                background-color: rgba(245, 245, 247, 216);
            }}
        """ + _card_style())

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QWidget()
        card.setObjectName("loading_card")
        card.setMinimumWidth(240)
        _elevate(card)
        layout.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(10)

        # 加载动画
        self.spinner = LoadingSpinner(48)
        card_layout.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignCenter)

        # 当前状态
        self.label = QLabel(message)
        self.label.setObjectName("title")
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.label)

        hint = QLabel("完成后会自动消失")
        hint.setObjectName("caption")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(hint)

        self.hide()

        # 使用定时器确保动画流畅
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update)

    def show_loading(self, message: str = None):
        """显示加载遮罩"""
        if message:
            self.label.setText(message)
        self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        self.update_timer.start(16)  # 约60fps

    def hide_loading(self):
        """隐藏加载遮罩"""
        self.update_timer.stop()
        self.hide()
