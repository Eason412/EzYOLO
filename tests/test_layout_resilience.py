# -*- coding: utf-8 -*-
"""布局韧性回归：三种支持尺寸下，控件不许被自己的样式或写死的数字挤坏。

test_ui_layout.py 只问「文字有没有超出盒子」，问不到三件事：

  1. 带下拉菜单的 QToolButton，右边那 24px 菜单区是画在按钮里面的——
     文字宽度没超过按钮宽度，却已经压到箭头底下了。
  2. setFixedHeight(32) 把高度钉死。开发机的字体正好装得下，换一台
     字体度量更高的 macOS / Windows 就是纵向切字，而且怎么改字号都不动。
  3. 导入页缩略图网格的 gridSize 是写死的 184px。视口宽度不是它的整数倍时，
     右边永远剩一条放不下第五列的空带——用户看到的就是「幽灵列」。

    python tests/test_layout_resilience.py
    python -m pytest tests/test_layout_resilience.py -q
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QWIDGETSIZE_MAX,
    QAbstractButton,
    QListWidget,
    QStyle,
    QStyleOptionToolButton,
    QToolButton,
)

from gui.main_window import MainWindow  # noqa: E402
from gui.pages.import_page import _ResponsiveThumbnailGrid  # noqa: E402
from gui.workflow import STEP_ANNOTATE, STEP_IMPORT  # noqa: E402

_app = _bootstrap.app()
db = _bootstrap.db

# 最小支持尺寸、用户报缺口的那台机器、14 寸笔记本
SIZES = ((1100, 720), (1182, 752), (1470, 832))
REPORTED_SIZE = (1182, 752)

# 胶囊按钮的内边距契约（见 annotate_page._toolbar_chip_style: padding 4px 10px）：
# 文字左边 10px，右边至少同样的 10px 才不会贴着菜单区；再留 6px 给字体度量的机器差异。
LEFT_PADDING = 10
RIGHT_BREATHING = 10
SAFETY = 6

MIN_CONTROL_HEIGHT = 32
# 一行字上下各留 6px，才不会顶着边框
TEXT_VERTICAL_PADDING = 12

# 缩略图网格：一格允许在这个区间里随视口伸缩，但不许退回一个写死的数字
GRID_WIDTH_RANGE = (156, 220)
ICON_WIDTH_RANGE = (120, 160)
# 完整列排完之后，右边最多只准剩这么点边距
MAX_UNUSED_WIDTH = 12

_IMAGE_TEMP_DIR = tempfile.TemporaryDirectory(prefix="ezyolo-resilience-")
_IMAGE_DIR = Path(_IMAGE_TEMP_DIR.name)


def _settle(rounds=5):
    for _ in range(rounds):
        _app.processEvents()


def _temp_image() -> Path:
    """一张临时 JPEG。数据库里每条记录文件名不同，但都指向它。"""
    path = _IMAGE_DIR / "resilience.jpg"
    if not path.exists():
        image = QImage(320, 240, QImage.Format.Format_RGB888)
        image.fill(QColor(120, 120, 130))
        image.save(str(path))
    return path


def _seed_project(image_count: int) -> int:
    project_id = _bootstrap.create_temp_project(
        name="布局韧性测试项目",
        project_type="detect",
        classes=[{'id': 0, 'name': '猫', 'color': '#FF3B30'}],
    )
    path = _temp_image()
    for index in range(image_count):
        db.add_image(
            project_id, f"resilience-{index:03d}.jpg", str(path), width=320, height=240
        )
    return project_id


def _open_window(project_id: int, page_index: int, size) -> MainWindow:
    from gui import main_window as main_window_module
    main_window_module.db = db

    window = MainWindow()
    window.load_projects(select_id=project_id)
    window.resize(*size)
    window.show()
    window.switch_page(page_index)
    _settle()
    return window


def _wait_for_thumbnails(page, timeout=10.0):
    """等缩略图后台线程跑完——网格尺寸要在图标真的进去之后才量。"""
    deadline = time.monotonic() + timeout
    while getattr(page, 'load_worker', None) is not None and time.monotonic() < deadline:
        _app.processEvents()
    _settle()


def _describe(button) -> str:
    return f"{type(button).__name__}({button.text()!r}, {button.width()}x{button.height()})"


def _menu_rect_width(button) -> int:
    """按钮自己那套样式里，下拉菜单区实际占了多宽——不猜，问 style。"""
    option = QStyleOptionToolButton()
    button.initStyleOption(option)
    rect = button.style().subControlRect(
        QStyle.ComplexControl.CC_ToolButton,
        option,
        QStyle.SubControl.SC_ToolButtonMenu,
        button,
    )
    return rect.width()


def _menu_buttons(page):
    return [
        button for button in page.findChildren(QToolButton)
        if button.isVisible() and button.menu() is not None and button.text().strip()
    ]


def _split_button_problems(page, size, task_type):
    problems = []
    for button in _menu_buttons(page):
        metrics = button.fontMetrics()
        text_width = metrics.horizontalAdvance(button.text())
        menu_width = _menu_rect_width(button)
        needed_width = text_width + menu_width + LEFT_PADDING + RIGHT_BREATHING + SAFETY
        if button.width() < needed_width:
            problems.append(
                f"[{size}/{task_type}] {_describe(button)} 的文字压进了下拉区："
                f"文字 {text_width}px + 菜单 {menu_width}px + 左内边距 {LEFT_PADDING}px "
                f"+ 右侧留白 {RIGHT_BREATHING}px + 余量 {SAFETY}px = {needed_width}px，"
                f"按钮只有 {button.width()}px"
            )

        needed_height = max(
            MIN_CONTROL_HEIGHT,
            button.sizeHint().height(),
            metrics.height() + TEXT_VERTICAL_PADDING,
        )
        if button.height() < needed_height:
            problems.append(
                f"[{size}/{task_type}] {_describe(button)} 高度不足："
                f"需要 {needed_height}px，只有 {button.height()}px"
            )
    return problems


def test_split_menu_buttons_reserve_space_for_their_arrow():
    """带下拉的工具按钮，文字不许伸进右边的菜单区。

    「画方框」「画多边形」的宽度目前是「文字 + 48」拍出来的：菜单区自己就要 24px，
    剩下 24px 要分给左右两边的内边距——右边只剩 0。文字没有超出按钮边框，所以
    旧的切字检查看不见它；用户看见的是文字直接顶在下拉箭头上。
    """
    project_id = _seed_project(3)
    try:
        problems = []
        for size in SIZES:
            window = _open_window(project_id, STEP_ANNOTATE, size)
            try:
                page = window.annotate_page
                # detect → 画方框，segment → 画多边形，两个标签都得量到
                for task_type in ("detect", "segment"):
                    page.task_combo.setCurrentText(task_type)
                    _settle()
                    assert page.btn_draw_tool.isVisible(), f"[{size}/{task_type}] 绘制工具应该显示"
                    buttons = _menu_buttons(page)
                    assert page.btn_draw_tool in buttons and page.btn_delete in buttons, (
                        f"[{size}/{task_type}] 没量到带菜单的工具按钮：{[b.text() for b in buttons]}"
                    )
                    problems += _split_button_problems(page, size, task_type)
            finally:
                window.close()

        assert not problems, "下拉按钮的文字压住了箭头:\n" + "\n".join(problems)
    finally:
        db.delete_project(project_id)


def test_topbar_controls_are_not_height_locked():
    """顶栏控件的高度必须是「至少 32」，不能是「正好 32」。

    setFixedHeight(32) 在开发机上看着没问题，是因为这台机器的字体正好塞得进去。
    换一台字形更高的 macOS / Windows，同一行字要 34~36px，控件却被钉死在 32——
    上下各切掉一点，而且改字号也救不回来。最大高度必须留空。
    """
    project_id = _seed_project(3)
    window = None
    try:
        window = _open_window(project_id, STEP_ANNOTATE, REPORTED_SIZE)
        page = window.annotate_page

        targets = [
            ("当前图片标签", page.image_name_label),
            ("标注方式下拉", page.task_combo),
            ("工具栏容器", page.toolbar),
        ]
        visible_buttons = [button for button in page.toolbar_buttons if button.isVisible()]
        targets += [
            (f"工具按钮 {button.text()!r}", button)
            for button in visible_buttons
        ]

        problems = []
        for label, widget in targets:
            if widget.maximumHeight() != QWIDGETSIZE_MAX:
                problems.append(
                    f"{label} 的高度被钉死了：maximumHeight={widget.maximumHeight()}"
                    f"（应为 QWIDGETSIZE_MAX，高度得能跟着字体长）"
                )
            # QStyleSheetStyle 会把控件属性里的 minimumHeight 显示成样式表盒模型的
            # 下限（QComboBox 在 macOS 上会报 30），但它的 minimumSizeHint 已经按
            # 字体、内边距和边框算出 33px。真正决定布局能不能压扁它的是两者较大值。
            effective_minimum = max(widget.minimumHeight(), widget.minimumSizeHint().height())
            if effective_minimum < MIN_CONTROL_HEIGHT:
                problems.append(
                    f"{label} 的有效最小高度只有 {effective_minimum}px，"
                    f"点击区应该至少 {MIN_CONTROL_HEIGHT}px"
                )
            if widget.height() < effective_minimum:
                problems.append(
                    f"{label} 实际只有 {widget.height()}px，低于有效最小高度 "
                    f"{effective_minimum}px"
                )

        button_height = max(button.height() for button in visible_buttons)
        expected_separator_height = max(1, button_height - 8)
        if page.toolbar_separator.height() != expected_separator_height:
            problems.append(
                f"工具栏分隔线高度 {page.toolbar_separator.height()}px 没跟着按钮 "
                f"{button_height}px 变化（应为 {expected_separator_height}px）"
            )

        assert not problems, "顶栏控件的高度写死了:\n" + "\n".join(problems)
    finally:
        if window is not None:
            window.close()
        db.delete_project(project_id)


def _grid_problems(page, size):
    """网格必须把视口宽度用满：完整列排完之后不许剩下一条放不下下一列的空带。"""
    grid = page.image_list.gridSize()
    viewport_width = page.image_list.viewport().width()
    problems = []

    if grid.width() <= 0:
        problems.append(f"[{size}] gridSize 宽度是 {grid.width()}，网格没有生效")
        return problems, 0, grid.width()

    columns = viewport_width // grid.width()
    unused = viewport_width - columns * grid.width()

    horizontal_max = page.image_list.horizontalScrollBar().maximum()
    if horizontal_max != 0:
        problems.append(
            f"[{size}] 缩略图网格出现横向滚动：maximum={horizontal_max}（应为 0）"
        )

    if unused > MAX_UNUSED_WIDTH:
        problems.append(
            f"[{size}] 网格右边空了 {unused}px 放不下一列（视口 {viewport_width}px，"
            f"一格 {grid.width()}px，排了 {columns} 列）——这就是用户看到的幽灵列"
        )

    if viewport_width >= GRID_WIDTH_RANGE[0] * 3:
        if not (GRID_WIDTH_RANGE[0] <= grid.width() <= GRID_WIDTH_RANGE[1]):
            problems.append(
                f"[{size}] 一格宽 {grid.width()}px，超出了 "
                f"{GRID_WIDTH_RANGE[0]}~{GRID_WIDTH_RANGE[1]}px 的合理区间"
            )

    icon_width = page.image_list.iconSize().width()
    if not (ICON_WIDTH_RANGE[0] <= icon_width <= ICON_WIDTH_RANGE[1]):
        problems.append(
            f"[{size}] 缩略图宽 {icon_width}px，超出了 "
            f"{ICON_WIDTH_RANGE[0]}~{ICON_WIDTH_RANGE[1]}px 的合理区间"
        )

    return problems, columns, grid.width()


def test_import_grid_fills_the_viewport_at_every_size():
    """导入页缩略图网格跟着视口走，不留幽灵列。

    gridSize 现在是写死的 184x208。1182px 宽的窗口里，视口宽度不是 184 的整数倍，
    右边剩下的那条空带装不下第五列——用户看到的就是「有位置却不放图」。
    """
    project_id = _seed_project(48)  # 足够多，纵向滚动条一定出来
    window = None
    try:
        window = _open_window(project_id, STEP_IMPORT, SIZES[0])
        page = window.import_page
        _wait_for_thumbnails(page)

        problems = []
        layouts = {}
        for size in SIZES:
            window.resize(*size)
            _settle()
            _wait_for_thumbnails(page)

            assert page.image_list.isVisible(), f"[{size}] 缩略图网格应该显示"
            assert page.image_list.verticalScrollBar().maximum() > 0, (
                f"[{size}] 图片没多到需要纵向滚动，这一条没测到东西"
            )

            size_problems, columns, grid_width = _grid_problems(page, size)
            problems += size_problems
            layouts[size] = (grid_width, columns)

            if size == REPORTED_SIZE and columns < 5:
                problems.append(
                    f"[{size}] 只排了 {columns} 列（视口 "
                    f"{page.image_list.viewport().width()}px），至少应该排下 5 列"
                )

        # 窗口一变宽，网格必须有反应——要么格子变宽，要么多排一列
        if len(set(layouts.values())) <= 1:
            problems.append(
                f"窗口从 {SIZES[0]} 拉到 {SIZES[-1]}，网格纹丝不动：{layouts}"
            )

        assert not problems, "导入页缩略图网格没有跟着视口走:\n" + "\n".join(problems)
    finally:
        if window is not None:
            window.close()
        db.delete_project(project_id)


def test_import_grid_reacts_when_only_the_viewport_width_changes():
    """滚动条或系统边距只改 viewport 时，也要立刻重排，不必拖动主窗口。

    setViewportMargins 模拟 Windows 常驻滚动条占掉一条宽度：外层列表尺寸不变，
    只有内部 viewport 变窄。QAbstractScrollArea 会把这种变化送进 resizeEvent，
    响应式网格必须重新计算格宽，避免旧列数突然掉一列后留下大空带。
    """
    grid = _ResponsiveThumbnailGrid()
    try:
        grid.setViewMode(QListWidget.ViewMode.IconMode)
        grid.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        grid.resize(890, 500)
        grid.show()
        _settle()

        outer_width = grid.width()
        viewport_before = grid.viewport().width()
        grid_before = grid.gridSize().width()

        grid.setViewportMargins(0, 0, 18, 0)
        _settle()

        viewport_after = grid.viewport().width()
        grid_after = grid.gridSize().width()
        assert grid.width() == outer_width, "这个测试只该改变 viewport，不该改变外层列表"
        assert viewport_after == viewport_before - 18, (
            f"viewport 没按预期缩小 18px：{viewport_before} -> {viewport_after}"
        )
        assert grid_after != grid_before, (
            f"viewport 已变窄，格宽却还停在 {grid_before}px，没有重新计算"
        )

        columns = viewport_after // grid_after
        unused = viewport_after - columns * grid_after
        assert unused <= MAX_UNUSED_WIDTH, (
            f"viewport 单独变窄后又出现 {unused}px 幽灵空带"
        )
    finally:
        grid.close()


def _text_button_problems(page, size, page_name):
    """所有带文字的按钮（不只是 QPushButton）都得装得下自己的文字。

    旧的切字检查只找 QLabel 和 QPushButton——QToolButton 不是 QPushButton 的子类，
    整条工具栏都在检查范围之外。这里按 QAbstractButton 扫，把那个盲区堵上。
    """
    problems = []
    checked = 0
    for button in page.findChildren(QAbstractButton):
        if not button.isVisible() or button.width() <= 0 or button.height() <= 0:
            continue
        text = button.text()
        if not text.strip():
            continue
        checked += 1

        metrics = button.fontMetrics()
        if button.height() < metrics.height():
            problems.append(
                f"[{size}/{page_name}] {_describe(button)} 纵向切字："
                f"一行字要 {metrics.height()}px，按钮只有 {button.height()}px"
            )
        needed_width = button.minimumSizeHint().width()
        if button.width() < needed_width:
            problems.append(
                f"[{size}/{page_name}] {_describe(button)} 横向切字："
                f"最小需要 {needed_width}px，按钮只有 {button.width()}px"
            )
    if checked == 0:
        problems.append(f"[{size}/{page_name}] 没找到任何可见文字按钮，检查可能空跑")
    return problems


def test_every_text_bearing_button_fits_its_text():
    """导入页和标注页上每个有文字的按钮，横竖都装得下自己的文字。"""
    project_id = _seed_project(6)
    try:
        problems = []
        for size in SIZES:
            window = _open_window(project_id, STEP_IMPORT, size)
            try:
                _wait_for_thumbnails(window.import_page)
                problems += _text_button_problems(window.import_page, size, "导入页")

                window.switch_page(STEP_ANNOTATE)
                _settle()
                problems += _text_button_problems(window.annotate_page, size, "标注页")
            finally:
                window.close()

        assert not problems, "有按钮装不下自己的文字:\n" + "\n".join(problems)
    finally:
        db.delete_project(project_id)


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
