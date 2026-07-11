# -*- coding: utf-8 -*-
"""
EzYOLO 样式定义

一套集中管理的设计令牌（颜色 / 字体 / 圆角 / 尺寸）+ 全局样式表。
页面通过 objectName 声明「这是什么」，样式在这里统一给出，
避免每个页面各写一份内联 QSS。

外观基调：浅色、克制、层级靠留白和字重拉开，而不是靠色块和粗边框。
表面只有三层——画布（background）、卡片（panel）、控件（inset）；
彩色只留给「主操作」和「状态」。

按钮层级（用 setObjectName 指定）:
    primary   —— 当前页面唯一的主操作（实心蓝）
    (默认)     —— 次级操作（白底描边）
    secondary —— 同默认，历史兼容
    ghost     —— 弱化的第三级操作（无边框）
    danger    —— 破坏性操作（红字描边）
    link      —— 纯文字入口

文字层级（objectName）:
    h1 页面标题 / h2 区块标题 / title 卡片标题
    subtitle 说明 / caption 辅助小字 / muted 弱化 / value 大数字
"""

from typing import List

# ==================== 颜色令牌 ====================

# 语义色有「文字版」和「填充版」两套：
# 同一个亮色在浅底上当填充好看，当小字就糊了（#34C759 在白底上对比度只有 2:1）。
# 页面里 COLORS['success'] / ['error'] / ['warning'] 绝大多数是拿去当 color: 用的，
# 所以这三个键给可读的深色版，亮色版另存 *_fill，只用在圆点、进度条这类填充上。
COLORS = {
    # 表面：画布 → 侧栏 → 卡片 → 控件
    'background': '#F5F5F7',   # 页面画布
    'sidebar': '#EFEFF2',      # 左侧导航，比画布再灰一点
    'panel': '#FFFFFF',        # 卡片 / 分组面板
    'inset': '#FFFFFF',        # 输入框、列表等控件表面
    'track': '#E6E6EB',        # 进度条、滑杆的底槽
    'hover': '#EBEBF0',
    'selected': '#E4EEFF',     # 选中态的蓝色底纹

    # 边框：1px、淡
    'border': '#E3E3E8',
    'border_strong': '#D2D2D7',

    # 强调色
    'primary': '#007AFF',
    'primary_hover': '#1A88FF',
    'primary_pressed': '#0062CC',
    'accent_text': '#0066CC',  # 浅底上的蓝色文字，够对比度
    'secondary': '#6E6E73',

    # 语义色（text 版本，浅底上可读）
    'success': '#248A3D',
    'success_soft': '#E7F6EC',  # 「已标注」这类状态底色
    'warning': '#9A5B00',
    'error': '#D70015',

    # 语义色（fill 版本，用于圆点 / 进度 / 实心块）
    'success_fill': '#34C759',
    'warning_fill': '#FF9500',
    'error_fill': '#FF3B30',

    # 文字
    'text_primary': '#1D1D1F',
    'text_secondary': '#6E6E73',
    'text_disabled': '#AEAEB2',
}

# ==================== 尺寸令牌 ====================

RADIUS = 12         # 卡片
RADIUS_SM = 8       # 控件

CONTROL_HEIGHT = 30      # 按钮 / 输入框统一高度
CONTROL_HEIGHT_LG = 34   # 主操作按钮

# ==================== 字体 ====================

# 每个平台上优先选用的字体，按顺序取第一个真实存在的。
# 只请求系统上真的装了的字体，Qt 才不会报字体缺失警告，
# 也不会悄悄回退到一个难看的默认族。
_FONT_CANDIDATES = [
    "PingFang SC",        # macOS 中文
    "SF Pro Text",        # macOS 西文（较新系统）
    "Helvetica Neue",     # macOS 回退
    "Hiragino Sans GB",
    "Microsoft YaHei",    # Windows
    "Segoe UI",
    "Noto Sans CJK SC",   # Linux
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Arial",
]

# 日志、代码这类等宽场景。Consolas 只有 Windows 有，macOS 上要用 Menlo / SF Mono。
_MONO_CANDIDATES = [
    "SF Mono",
    "Menlo",
    "Consolas",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Courier New",
]

_font_stack_cache: List[str] = []
_mono_stack_cache: List[str] = []


def _available_families() -> set:
    try:
        from PyQt6.QtGui import QFontDatabase

        return set(QFontDatabase.families())
    except Exception:
        return set()


def get_font_stack() -> List[str]:
    """返回当前系统上真实存在的界面字体族列表（结果缓存）。

    需要在 QApplication 创建之后调用；未创建时退化为空列表，
    样式表就不写 font-family，交给 Qt 默认字体。
    """
    global _font_stack_cache
    if _font_stack_cache:
        return _font_stack_cache

    available = _available_families()
    _font_stack_cache = [name for name in _FONT_CANDIDATES if name in available]
    return _font_stack_cache


def get_mono_font_stack() -> List[str]:
    """返回当前系统上真实存在的等宽字体族列表（日志框用）。"""
    global _mono_stack_cache
    if _mono_stack_cache:
        return _mono_stack_cache

    available = _available_families()
    _mono_stack_cache = [name for name in _MONO_CANDIDATES if name in available]
    return _mono_stack_cache


def get_primary_font_family() -> str:
    """返回首选界面字体族名；系统上一个都没有时返回空字符串，交给 Qt 默认字体。"""
    stack = get_font_stack()
    return stack[0] if stack else ""


def mono_font_family_css() -> str:
    """给日志框等等宽场景用：返回可直接写进 QSS 的 font-family 值。

    只列系统上真实存在的字体，避免出现 “missing font family Consolas” 这类警告。
    没有可用等宽字体时返回空字符串，调用方应整行不写 font-family。
    """
    stack = get_mono_font_stack()
    if not stack:
        return ""
    return ", ".join(f"'{name}'" for name in stack)


def _font_family_declaration() -> str:
    """生成 QSS 的 font-family 声明。

    Qt 的样式表不认 CSS 的通用族（sans-serif 会被当成一个真实字体名去找，
    找不到同样会报字体缺失），所以这里只列真实存在的字体；
    一个都没有时就整行不写，交给 Qt 默认字体。
    """
    families = [f'"{name}"' for name in get_font_stack()]
    if not families:
        return ""
    return f"    font-family: {', '.join(families)};\n"


# ==================== 样式表 ====================

def generate_stylesheet(colors: dict) -> str:
    """根据颜色令牌生成全局样式表。"""
    c = colors
    font_family = _font_family_declaration()

    return f"""
/* ---------- 基础 ---------- */
QMainWindow, QDialog {{
    background-color: {c['background']};
    color: {c['text_primary']};
}}

QWidget {{
    background-color: {c['background']};
    color: {c['text_primary']};
{font_family}    font-size: 13px;
}}

QFrame {{
    background-color: {c['panel']};
    border: 1px solid {c['border']};
    border-radius: {RADIUS}px;
}}

/* QStackedWidget / QSplitter 都继承自 QFrame，别让它们套上卡片的边框和底色 */
QStackedWidget {{
    background-color: transparent;
    border: none;
    border-radius: 0px;
}}

QSplitter {{
    background-color: transparent;
    border: none;
}}

QSplitter::handle {{
    background-color: transparent;
}}

QSplitter::handle:horizontal {{
    width: 10px;
}}

QSplitter::handle:vertical {{
    height: 10px;
}}

QScrollArea {{
    background-color: transparent;
    border: none;
}}

QToolTip {{
    background-color: {c['panel']};
    color: {c['text_primary']};
    border: 1px solid {c['border_strong']};
    border-radius: {RADIUS_SM}px;
    padding: 6px 9px;
}}

/* ---------- 文字 ---------- */
QLabel {{
    background-color: transparent;
    border: none;
    color: {c['text_primary']};
    font-size: 13px;
}}

QLabel#h1 {{
    font-size: 21px;
    font-weight: 600;
    color: {c['text_primary']};
}}

QLabel#h2 {{
    font-size: 15px;
    font-weight: 600;
    color: {c['text_primary']};
}}

QLabel#title {{
    font-size: 14px;
    font-weight: 600;
    color: {c['text_primary']};
}}

QLabel#subtitle, QLabel#muted {{
    font-size: 13px;
    color: {c['text_secondary']};
}}

QLabel#caption {{
    font-size: 12px;
    color: {c['text_secondary']};
}}

QLabel#value {{
    font-size: 22px;
    font-weight: 600;
    color: {c['text_primary']};
}}

/* ---------- 卡片 ---------- */
QFrame#card {{
    background-color: {c['panel']};
    border: 1px solid {c['border']};
    border-radius: {RADIUS}px;
}}

QFrame#toolbar {{
    background-color: {c['panel']};
    border: 1px solid {c['border']};
    border-radius: {RADIUS}px;
}}

QFrame#divider {{
    background-color: {c['border']};
    border: none;
    border-radius: 0px;
    max-height: 1px;
    min-height: 1px;
}}

/* ---------- 主窗口结构 ---------- */
QWidget#sidebar {{
    background-color: {c['sidebar']};
    border: none;
    border-right: 1px solid {c['border']};
}}

QWidget#sidebar QLabel {{
    background-color: transparent;
}}

QLabel#brand {{
    font-size: 17px;
    font-weight: 700;
    color: {c['text_primary']};
}}

QLabel#nav_section {{
    font-size: 11px;
    font-weight: 600;
    color: {c['text_disabled']};
}}

QWidget#page_header {{
    background-color: {c['background']};
    border: none;
    border-bottom: 1px solid {c['border']};
}}

QWidget#page_header QLabel {{
    background-color: transparent;
}}

/* 页头的「第 N 步」：灰色小胶囊，不跟主操作抢颜色 */
QLabel#step_badge {{
    background-color: {c['hover']};
    color: {c['text_secondary']};
    border-radius: 5px;
    padding: 2px 7px;
    font-size: 11px;
    font-weight: 600;
}}

/* ---------- 流程导航项 ---------- */
/* 内边距不写在这里：step_item 里面是自己的布局，QSS 的 padding 影响不到子控件，
   只会把按钮的 sizeHint 虚增一截。内边距由 StepNavItem 的布局边距给。 */
QPushButton#step_item {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_SM}px;
    text-align: left;
    padding: 0px;
}}

QPushButton#step_item:hover {{
    background-color: {c['hover']};
}}

QPushButton#step_item:checked {{
    background-color: {c['selected']};
    border: 1px solid transparent;
}}

QPushButton#step_item:disabled {{
    background-color: transparent;
}}

/* 兼容旧的侧边栏按钮样式 */
QPushButton#nav_button {{
    background-color: transparent;
    color: {c['text_primary']};
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 9px 12px;
    text-align: left;
    font-size: 13px;
}}

QPushButton#nav_button:hover {{
    background-color: {c['hover']};
}}

QPushButton#nav_button:checked {{
    background-color: {c['selected']};
    color: {c['text_primary']};
}}

QPushButton#nav_button:disabled {{
    color: {c['text_disabled']};
}}

/* ---------- 按钮层级 ---------- */
/* 默认 = 次级操作：白底描边 */
QPushButton {{
    background-color: {c['panel']};
    color: {c['text_primary']};
    border: 1px solid {c['border_strong']};
    border-radius: {RADIUS_SM}px;
    padding: 5px 14px;
    min-height: {CONTROL_HEIGHT - 12}px;
    font-size: 13px;
}}

QPushButton:hover {{
    background-color: {c['hover']};
}}

QPushButton:pressed {{
    background-color: {c['border']};
}}

QPushButton:disabled {{
    background-color: {c['panel']};
    color: {c['text_disabled']};
    border-color: {c['border']};
}}

QPushButton#secondary {{
    background-color: {c['panel']};
    color: {c['text_primary']};
    border: 1px solid {c['border_strong']};
}}

QPushButton#secondary:hover {{
    background-color: {c['hover']};
}}

/* 主操作：每页最多一个 */
QPushButton#primary {{
    background-color: {c['primary']};
    color: #FFFFFF;
    border: 1px solid {c['primary']};
    font-weight: 600;
}}

QPushButton#primary:hover {{
    background-color: {c['primary_hover']};
    border-color: {c['primary_hover']};
}}

QPushButton#primary:pressed {{
    background-color: {c['primary_pressed']};
    border-color: {c['primary_pressed']};
}}

QPushButton#primary:disabled {{
    background-color: {c['track']};
    color: {c['text_disabled']};
    border-color: {c['track']};
}}

QPushButton#ghost {{
    background-color: transparent;
    color: {c['text_secondary']};
    border: 1px solid transparent;
}}

QPushButton#ghost:hover {{
    background-color: {c['hover']};
    color: {c['text_primary']};
}}

QPushButton#ghost:checked {{
    background-color: {c['selected']};
    color: {c['text_primary']};
}}

QPushButton#danger {{
    background-color: {c['panel']};
    color: {c['error']};
    border: 1px solid {c['border_strong']};
}}

QPushButton#danger:hover {{
    background-color: {c['hover']};
    border-color: {c['error_fill']};
}}

QPushButton#danger:disabled, QPushButton#ghost:disabled {{
    color: {c['text_disabled']};
    border-color: {c['border']};
}}

QPushButton#link {{
    background-color: transparent;
    color: {c['accent_text']};
    border: none;
    padding: 2px 4px;
    text-align: left;
}}

QPushButton#link:hover {{
    color: {c['primary']};
}}

QToolButton {{
    background-color: {c['panel']};
    color: {c['text_primary']};
    border: 1px solid {c['border_strong']};
    border-radius: {RADIUS_SM}px;
    padding: 5px 10px;
}}

QToolButton:hover {{
    background-color: {c['hover']};
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: {c['inset']};
    color: {c['text_primary']};
    border: 1px solid {c['border_strong']};
    border-radius: {RADIUS_SM}px;
    padding: 5px 10px;
    min-height: {CONTROL_HEIGHT - 12}px;
    selection-background-color: {c['primary']};
    selection-color: #FFFFFF;
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {c['primary']};
}}

QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background-color: {c['background']};
    color: {c['text_disabled']};
    border-color: {c['border']};
}}

/* 下拉箭头交给 Qt 自己画：自定义 ::drop-down 会把箭头一起干掉 */

QComboBox QAbstractItemView {{
    background-color: {c['panel']};
    color: {c['text_primary']};
    border: 1px solid {c['border_strong']};
    border-radius: {RADIUS_SM}px;
    padding: 4px;
    outline: none;
    selection-background-color: {c['selected']};
    selection-color: {c['text_primary']};
}}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background-color: transparent;
    border: none;
    width: 16px;
}}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {c['hover']};
}}

QTextEdit {{
    background-color: {c['inset']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: {RADIUS_SM}px;
    padding: 6px;
    selection-background-color: {c['primary']};
    selection-color: #FFFFFF;
}}

/* 勾选框和单选钮：框和填充都得自己画。
   试过交给系统画（去掉 ::indicator 规则）——不行：控件一旦被样式表接管，
   Qt 画的对勾/圆点就只剩一个孤零零的字形，外面那个框来自控件继承下来的背景，
   而背景在这里是 transparent（不设成 transparent，白卡片上每一行都会拖一条灰底）。
   结果是「勾上的只有一个勾、没勾的什么都没有」，单选钮更是整个圈都不见。
   所以框自己画，选中态用实心表示——填满 = 选上了，空的 = 没选，不会看错。 */
QCheckBox, QRadioButton {{
    background-color: transparent;
    color: {c['text_primary']};
    spacing: 8px;
    padding: 2px 0;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {c['border_strong']};
    background-color: {c['inset']};
}}

QCheckBox::indicator {{
    border-radius: 4px;
}}

QRadioButton::indicator {{
    border-radius: 8px;
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {c['primary']};
    border-color: {c['primary']};
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {c['primary']};
}}

QSlider::groove:horizontal {{
    background-color: {c['track']};
    height: 4px;
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background-color: {c['primary']};
    height: 4px;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background-color: #FFFFFF;
    border: 1px solid {c['border_strong']};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 8px;
}}

/* ---------- 列表 ---------- */
QListWidget {{
    background-color: {c['inset']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: {RADIUS_SM}px;
    padding: 4px;
    outline: none;
}}

QListWidget::item {{
    padding: 6px 10px;
    border-radius: 6px;
    border: none;
}}

QListWidget::item:selected {{
    background-color: {c['selected']};
    color: {c['text_primary']};
}}

QListWidget::item:hover {{
    background-color: {c['hover']};
}}

QMenu {{
    background-color: {c['panel']};
    color: {c['text_primary']};
    border: 1px solid {c['border_strong']};
    border-radius: {RADIUS_SM}px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 20px 6px 12px;
    border-radius: 5px;
}}

QMenu::item:selected {{
    background-color: {c['selected']};
}}

QMenu::separator {{
    height: 1px;
    background-color: {c['border']};
    margin: 4px 8px;
}}

/* ---------- 分组框 ---------- */
/* 标题整行落在卡片外面（margin 区），不再骑在边框线上。
   原来 margin-top 只有 14px，标题盒子比它高，边框就从字的下半截穿过去——
   设置页、关于页、批量处理弹窗里每一个分组标题都被划了一道。
   标题移到卡片上方之后，无论卡片底下是什么颜色都不会重叠。
   字重只给标题：写在 QGroupBox 上会连里面的按钮和输入框一起加粗。 */
QGroupBox {{
    background-color: {c['panel']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: {RADIUS}px;
    margin-top: 26px;
    padding: 16px;
    font-size: 13px;
    font-weight: 400;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 2px;
    top: 0px;
    padding: 0 0 8px 0;
    font-size: 13px;
    font-weight: 600;
    color: {c['text_primary']};
    background-color: transparent;
}}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background-color: {c['track']};
    border: none;
    border-radius: 3px;
    text-align: center;
    color: {c['text_primary']};
    height: 6px;
    font-size: 11px;
}}

QProgressBar::chunk {{
    background-color: {c['primary']};
    border-radius: 3px;
}}

/* ---------- 标签页 ---------- */
QTabWidget::pane {{
    background-color: {c['panel']};
    border: 1px solid {c['border']};
    border-radius: {RADIUS_SM}px;
    top: -1px;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {c['text_secondary']};
    padding: 6px 14px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: {RADIUS_SM}px;
    border-top-right-radius: {RADIUS_SM}px;
}}

QTabBar::tab:selected {{
    background-color: {c['panel']};
    color: {c['text_primary']};
    border-color: {c['border']};
    border-bottom-color: {c['panel']};
    font-weight: 600;
}}

QTabBar::tab:hover:!selected {{
    color: {c['text_primary']};
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background-color: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background-color: {c['border_strong']};
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {c['text_disabled']};
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background-color: {c['border_strong']};
    border-radius: 5px;
    min-width: 30px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {c['text_disabled']};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
    width: 0px;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
"""


def get_full_stylesheet(theme: str = 'light') -> str:
    """获取完整样式表。

    应用只有一套浅色外观；theme 参数保留是为了兼容既有调用点。
    """
    return generate_stylesheet(COLORS)
