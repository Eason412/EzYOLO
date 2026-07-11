# -*- coding: utf-8 -*-
"""布局测试：两种窗口尺寸下，七个页面都不许把文字或按钮切掉。

冒烟测试只管「页面能不能打开」，这里管「打开之后有没有被切」。
判据只有一条，但足够狠：一个控件如果画出来的文字比它自己的盒子还宽，
用户就会看到半个字——不管它是标签还是按钮。

    python tests/test_ui_layout.py
    python -m pytest tests/test_ui_layout.py -q
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import QLabel, QPushButton  # noqa: E402
from PyQt6.QtGui import QImage, QColor  # noqa: E402

from gui.workflow import (  # noqa: E402
    STEP_IMPORT, STEP_ANNOTATE, STEP_TRAIN, STEP_RESULT, STEP_TEST,
    PAGE_SETTINGS, PAGE_ABOUT,
)
from gui.main_window import MainWindow  # noqa: E402

_app = _bootstrap.app()
db = _bootstrap.db

# 要覆盖的窗口尺寸：最小支持尺寸，和一台 14 寸笔记本的常见可用区域
SIZES = [(1100, 720), (1470, 832)]

PAGES = [
    STEP_IMPORT, STEP_ANNOTATE, STEP_TRAIN, STEP_RESULT, STEP_TEST,
    PAGE_SETTINGS, PAGE_ABOUT,
]

# 字体渲染在不同机器上有 1px 级别的出入，留一点余量，只抓真正切字的情况
SLACK = 2

_IMAGE_DIR = Path(tempfile.mkdtemp(prefix="ezyolo-layout-"))


def seed_project() -> int:
    """一个「什么都有」的项目：有图、有标注、有类别，页面才会显示满内容。"""
    project_id = _bootstrap.create_temp_project(
        name="布局测试项目",
        project_type="detect",
        classes=[{'id': 0, 'name': '猫', 'color': '#FF3B30'}],
    )
    for i in range(3):
        path = _IMAGE_DIR / f"layout{i}.jpg"
        image = QImage(320, 240, QImage.Format.Format_RGB888)
        image.fill(QColor(120, 120, 130))
        image.save(str(path))

        image_id = db.add_image(project_id, path.name, str(path), width=320, height=240)
        db.add_annotation(
            image_id=image_id,
            project_id=project_id,
            class_id=0,
            class_name="猫",
            annotation_type="bbox",
            data={'x': 0.5, 'y': 0.5, 'width': 0.4, 'height': 0.4},
        )
        db.update_image_status(image_id, 'annotated')
    return project_id


def make_window() -> MainWindow:
    from gui import main_window as mw
    mw.db = db
    return MainWindow()


def _skip_widget(widget) -> bool:
    text = widget.text()
    if not text.strip():
        return True
    # 富文本（HTML）用字体度量算尺寸没有意义
    if '<' in text and '>' in text:
        return True
    # 显示图片的标签，text() 只是占位说明
    if isinstance(widget, QLabel):
        pixmap = widget.pixmap()
        if pixmap is not None and not pixmap.isNull():
            return True
    return False


def _text_is_clipped(widget) -> bool:
    """控件里的文字有没有横向超出它自己的盒子。"""
    if _skip_widget(widget):
        return False

    if isinstance(widget, QLabel):
        # 会换行的标签靠高度容纳文字，不存在横向切字
        if widget.wordWrap():
            return False
        needed = widget.fontMetrics().horizontalAdvance(widget.text())
        margins = widget.contentsMargins()
        available = widget.width() - margins.left() - margins.right()
        return needed > available + SLACK

    # 按钮：minimumSizeHint 已经把文字宽度和内边距算进去了
    return widget.width() + SLACK < widget.minimumSizeHint().width()


def _needed_height(widget) -> int:
    """这个控件要把文字完整画出来，至少得有多高。

    横向切字看宽度，纵向切字看的是「一行字的高度」——macOS 上一个 13px 的
    中文字形连基线一起要 18~19px，控件只有 11px 高时上下都会被削掉。
    会换行的标签按当前宽度问 heightForWidth，行数变了也算得对。
    """
    if isinstance(widget, QLabel) and widget.wordWrap() and widget.width() > 0:
        return widget.heightForWidth(widget.width())
    return widget.fontMetrics().height()


def _text_is_vertically_clipped(widget) -> bool:
    """文字有没有被上下切掉：可用高度装不下一行（或换行后的若干行）字。"""
    if _skip_widget(widget):
        return False

    margins = widget.contentsMargins()
    available = widget.height() - margins.top() - margins.bottom()
    return _needed_height(widget) > available + SLACK


def _describe(widget) -> str:
    return (
        f"{type(widget).__name__}(objectName={widget.objectName()!r}, "
        f"text={widget.text()[:40]!r}, "
        f"size={widget.width()}x{widget.height()})"
    )


def _clipped_widgets(page):
    found = []
    for widget in page.findChildren((QLabel, QPushButton)):
        if not widget.isVisible():
            continue
        if widget.width() <= 0 or widget.height() <= 0:
            continue
        if _text_is_clipped(widget):
            found.append(f"横向切字 {_describe(widget)}")
        if _text_is_vertically_clipped(widget):
            found.append(
                f"纵向切字 {_describe(widget)} "
                f"需要 {_needed_height(widget)}px，只有 {widget.height()}px"
            )
    return found


def _check_all_pages(window, width, height):
    problems = []
    window.resize(width, height)
    window.show()
    for _ in range(3):
        _app.processEvents()

    for index in PAGES:
        window.switch_page(index)
        for _ in range(3):
            _app.processEvents()

        # 从窗口往下找：侧边栏、页头和当前页面一起查；
        # 堆栈里没显示的页面 isVisible() 为假，会被跳过
        for problem in _clipped_widgets(window):
            problems.append(f"[{width}x{height} 第 {index} 页] {problem}")

        page = window.content_stack.currentWidget()
        # 页面不该比窗口还宽——宽出去的部分用户根本看不到
        if page.width() > window.width():
            problems.append(
                f"[{width}x{height} 第 {index} 页] 页面宽度 {page.width()} 超过窗口 {window.width()}"
            )
    return problems


def test_no_clipped_text_at_min_size():
    """1100x720（应用支持的最小窗口）下不许切字。"""
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)

    problems = _check_all_pages(window, 1100, 720)
    assert not problems, "有控件的文字被切掉:\n" + "\n".join(problems)

    window.close()
    db.delete_project(project_id)


def test_no_clipped_text_at_laptop_size():
    """1470x832 下同样不许切字（也顺带确认放大后不塌）。"""
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)

    problems = _check_all_pages(window, 1470, 832)
    assert not problems, "有控件的文字被切掉:\n" + "\n".join(problems)

    window.close()
    db.delete_project(project_id)


def _nav_label_problems(index, item):
    """一步里的三个标签（状态符号 / 名称 / 进展），横竖都不许被切。

    符号那一列以前是 setFixedWidth(16) ——一个写死的数字。'✓' 在 Cocoa 上会落到
    别的字体里，字宽跟着系统走，写死就有被竖着切掉半个的风险。所以这里按每个
    标签自己的字体度量去量，而不是拿 16 去比。
    """
    problems = []
    visible = item.contentsRect()

    for role, label in (("符号", item.mark), ("名称", item.name), ("进展", item.status)):
        text = label.text()
        if not text.strip():
            continue

        metrics = label.fontMetrics()

        needed_h = metrics.height()
        if label.height() + SLACK < needed_h:
            problems.append(
                f"第 {index} 步的{role}「{text}」被纵向切掉："
                f"需要 {needed_h}px，只有 {label.height()}px"
            )

        needed_w = metrics.horizontalAdvance(text)
        if label.width() + SLACK < needed_w:
            problems.append(
                f"第 {index} 步的{role}「{text}」被横向切掉："
                f"需要 {needed_w}px，只有 {label.width()}px"
            )

        # 控件自己画得再大，超出父控件可见区域一样看不到
        geo = label.geometry()
        if geo.bottom() > visible.bottom() + SLACK:
            problems.append(
                f"第 {index} 步的{role}超出可见区域（下）：{geo.bottom()} > {visible.bottom()}"
            )
        if geo.right() > visible.right() + SLACK:
            problems.append(
                f"第 {index} 步的{role}超出可见区域（右）：{geo.right()} > {visible.right()}"
            )
    return problems


def _step_nav_problems(window):
    """五步导航：每一步的符号、名称和进展都得完整显示。

    StepNavItem 是一个内部放了布局的 QPushButton。QPushButton 的 sizeHint 只按
    它自己那段（空的）文本算，根本不看子布局——父布局照这个高度给格子，里面
    两行字就被压扁。所以这里既查控件够不够高，也查它报出去的尺寸提示对不对。
    """
    problems = []
    for index, item in window.step_nav.items.items():
        content = item.layout().minimumSize().height()
        if item.minimumSizeHint().height() < content:
            problems.append(
                f"第 {index} 步的 minimumSizeHint 高度 {item.minimumSizeHint().height()} "
                f"低于内容需要的 {content}px——父布局会照这个值把它压扁"
            )
        if item.height() < content:
            problems.append(
                f"第 {index} 步实际高度 {item.height()} 装不下内容需要的 {content}px"
            )

        problems += _nav_label_problems(index, item)
    return problems


def test_step_nav_text_is_not_clipped():
    """五个步骤的符号、名称和进展，在两种窗口尺寸下都不许被切（横竖都算）。"""
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)

    problems = []
    for width, height in SIZES:
        window.resize(width, height)
        window.show()
        for _ in range(3):
            _app.processEvents()
        problems += [f"[{width}x{height}] {p}" for p in _step_nav_problems(window)]

    assert not problems, "流程导航的文字被切:\n" + "\n".join(problems)

    window.close()
    db.delete_project(project_id)


def test_step_nav_marks_fit_in_every_state():
    """四种状态的符号（✓ ● ○）都得装得下——符号那一列的宽度不是写死的 16px。"""
    from gui.workflow import DONE, CURRENT, READY, LOCKED
    from gui.widgets.workflow_widgets import STATE_MARK

    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.resize(1100, 720)
    window.show()
    for _ in range(3):
        _app.processEvents()

    problems = []
    for state in (DONE, CURRENT, READY, LOCKED):
        for index, item in window.step_nav.items.items():
            item.apply_state(state, "已标注 3/3")
        for _ in range(3):
            _app.processEvents()

        for index, item in window.step_nav.items.items():
            assert item.mark.text() == STATE_MARK[state]
            problems += [f"[{state}] {p}" for p in _nav_label_problems(index, item)]

    assert not problems, "某个状态下的导航文字被切:\n" + "\n".join(problems)

    window.close()
    db.delete_project(project_id)


def test_step_nav_height_is_computed_not_hardcoded():
    """字号一大，格子必须跟着变高——高度是按字体度量算出来的，不是拍一个数字顶上去。

    这是「根因到底修没修」的判据。Cocoa / 高 DPI 上同样一段 13px 的中文，
    字形比别的平台高、也比别的平台宽；只要高度还是从布局（→ 字体度量）来的，
    这些机器上就自动够用。反过来，哪天有人把高度改回一个写死的常量，
    这个测试会当场失败。

    直接换构造期的字号，而不是事后改 QLabel 的 font：布局把 minimumSize 缓存了，
    事后改字体量不到；何况平台字体本来就是控件建出来的那一刻定下的。
    """
    from gui.widgets.workflow_widgets import StepNavItem
    from gui.workflow import WORKFLOW_STEPS, CURRENT

    step = WORKFLOW_STEPS[0]

    small = StepNavItem(step)
    small.apply_state(CURRENT, "3 张图片")

    original = (StepNavItem.NAME_PX, StepNavItem.STATUS_PX)
    try:
        StepNavItem.NAME_PX = original[0] + 8
        StepNavItem.STATUS_PX = original[1] + 8
        big = StepNavItem(step)
        big.apply_state(CURRENT, "3 张图片")
    finally:
        StepNavItem.NAME_PX, StepNavItem.STATUS_PX = original

    assert big.minimumSizeHint().height() > small.minimumSizeHint().height(), (
        f"字号大了 8px，格子的最小高度却没变"
        f"（{small.minimumSizeHint().height()}px → {big.minimumSizeHint().height()}px）"
        "——高度多半是写死的，字一大就会被切"
    )
    # 符号那一列的宽度同样跟着字体走，不是写死的 16px
    assert big.mark.minimumWidth() > small.mark.minimumWidth(), (
        "字号变大，状态符号那一列的宽度没跟着变——宽度是写死的，'✓' 会被切"
    )

    # 大字号下真摆出来，横竖都不许切
    big.resize(220, big.sizeHint().height())
    big.show()
    for _ in range(3):
        _app.processEvents()

    problems = _nav_label_problems(STEP_IMPORT, big)
    assert not problems, "字号调大之后文字被切:\n" + "\n".join(problems)

    big.close()
    small.close()


def test_gate_text_is_not_width_locked():
    """前置条件页的说明不能被写死宽度——窄窗口下要能跟着缩，而不是被切掉。"""
    window = make_window()
    window.resize(1100, 720)
    window.show()
    _app.processEvents()

    # 没有项目 → 标注页被挡住，显示 Gate
    window.switch_page(STEP_ANNOTATE)
    for _ in range(3):
        _app.processEvents()

    gate = window.gate
    assert gate.title.wordWrap() and gate.reason.wordWrap()

    # 卡片可以有上限，但不能是「最小宽度 == 最大宽度」的死宽度
    assert gate.minimumWidth() < gate.maximumWidth()

    for problem in _clipped_widgets(gate):
        raise AssertionError(f"前置条件页文字被切: {problem}")

    window.close()


def test_startup_notice_stays_quiet_when_nothing_is_wrong():
    """数据自检没差异时，窗口里不该出现任何通知条。"""
    window = make_window()
    assert not window.notice.isVisible()
    window.close()


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
