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

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QLabel, QPushButton  # noqa: E402
from PyQt6.QtGui import QImage, QColor, QFontMetrics  # noqa: E402

from gui.workflow import (  # noqa: E402
    STEP_IMPORT, STEP_ANNOTATE, STEP_TRAIN, STEP_RESULT, STEP_TEST,
    PAGE_SETTINGS, PAGE_ABOUT,
)
from gui.main_window import MainWindow, GATE_INDEX  # noqa: E402

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


def test_long_project_name_is_elided_not_cut_in_half():
    """侧栏项目名装不下时要以省略号收尾，不能把最后一个中文字切掉一半。

    上面那套切字检查只看 QLabel 和 QPushButton，下拉框漏在外面——
    而 QComboBox 恰恰是不会自己省略的：文字比框宽就直接裁掉。给下拉箭头
    留出内边距之后，长项目名正好会露出半个字。
    """
    long_name = "安全帽检测很长的项目名字测试用例"
    project_id = _bootstrap.create_temp_project(name=long_name, project_type="detect")

    window = make_window()
    window.load_projects(select_id=project_id)
    window.resize(1100, 720)
    window.show()
    for _ in range(3):
        _app.processEvents()

    combo = window.project_combo
    metrics = QFontMetrics(combo.font())

    # 前提：这个名字确实装不下，否则这个测试什么都没测到
    assert metrics.horizontalAdvance(long_name) > combo.width(), "名字应该长到装不下"

    painted = combo.elided_text()
    assert painted != long_name, "装不下却原样画出来，最后一个字会被切掉"
    assert painted.endswith("…"), f"应该用省略号收尾，实际是 {painted!r}"
    assert metrics.horizontalAdvance(painted) <= combo.width(), "省略之后仍然超出框宽"

    # 完整名字要还能看得到
    assert combo.itemData(combo.currentIndex(), Qt.ItemDataRole.ToolTipRole) == long_name

    window.close()
    db.delete_project(project_id)


def test_project_identity_is_not_repeated_on_the_pages():
    """项目名只由左侧流程栏负责。标注页和导入页不再各放一份。

    同一屏把项目名写三遍不会让人更清楚自己在哪，只会把宽度从真正要看的东西
    （标注页的画布、导入页的图片区）里抠走。
    """
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.resize(1100, 720)
    window.show()
    for _ in range(3):
        _app.processEvents()

    assert not hasattr(window.annotate_page, 'project_name_label'), \
        "标注页信息条不该再有「当前项目」这一格"
    assert not hasattr(window.import_page, 'project_bar'), \
        "导入页不该再有项目信息卡片"

    # 侧栏那一份还在——不能把项目身份整个弄丢
    assert window.project_combo is not None

    window.close()
    db.delete_project(project_id)


def test_annotate_toolbar_is_one_quiet_row_without_group_captions():
    """标注工具栏是一行控件，不是一张带小标题的卡片。

    「标注工具」「修改」这两行常驻小标题各占一行高度，三五个按钮不需要目录；
    卡片边框还把它框成一个和画布平起平坐的区块——画布因此矮了一截。
    """
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.resize(1100, 720)
    window.show()
    window.switch_page(STEP_ANNOTATE)
    for _ in range(3):
        _app.processEvents()

    page = window.annotate_page

    captions = [label.text() for label in page.toolbar.findChildren(QLabel)]
    assert "标注工具" not in captions and "修改" not in captions, \
        f"工具栏里不该再有常驻的分组标题：{captions}"

    # 一行：所有可见按钮的垂直区间必须互相重叠
    visible = [b for b in page.toolbar_buttons if b.isVisible()]
    assert len(visible) >= 3
    tops = {b.y() for b in visible}
    assert len(tops) == 1, f"工具栏按钮不在同一行上：{[(b.text(), b.y()) for b in visible]}"

    # 高度一致：QToolButton（带下拉箭头）和普通 QPushButton 默认对不齐
    heights = {b.height() for b in visible}
    assert len(heights) == 1, \
        f"按钮高度不一致，中间那根分隔线两边会高低不平：{[(b.text(), b.height()) for b in visible]}"

    window.close()
    db.delete_project(project_id)


def test_advanced_and_destructive_panels_start_collapsed():
    """样本管理（会真删图片文件）和数据导出默认收起，不常驻占地方。"""
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.resize(1100, 720)
    window.show()
    window.switch_page(STEP_ANNOTATE)
    for _ in range(3):
        _app.processEvents()

    page = window.annotate_page

    # 收起时里面的按钮是看不见的，但控件还在——展开就能用，没有被删掉
    assert not page.btn_delete_random_samples.isVisible()
    assert not page.btn_export_dataset.isVisible()
    assert not page.btn_batch_process.isVisible(), "批处理已经挪进样本管理（进阶）里"

    # 类别和 AI 入口是主路径，必须一进来就看得见
    assert page.class_list.isVisible()
    assert page.btn_auto_label.isVisible()

    window.close()
    db.delete_project(project_id)


def test_expanded_sections_do_not_clip_their_contents():
    """收起只是默认值，不是把问题藏起来——展开之后一样不许切字。"""
    from gui.widgets.collapsible_section import CollapsibleSection

    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.resize(1100, 720)
    window.show()
    window.switch_page(STEP_ANNOTATE)
    for _ in range(3):
        _app.processEvents()

    sections = window.annotate_page.right_panel.findChildren(CollapsibleSection)
    assert len(sections) == 2, f"应该有样本管理和数据导出两个折叠区，实际 {len(sections)}"

    for section in sections:
        assert not section.is_expanded(), f"{section.toggle.text()} 默认应该是收起的"
        section.set_expanded(True)
    for _ in range(3):
        _app.processEvents()

    problems = []
    for section in sections:
        problems += _clipped_widgets(section)
    assert not problems, "折叠区展开后文字被切:\n" + "\n".join(problems)

    window.close()
    db.delete_project(project_id)


def test_import_page_keeps_task_type_and_hides_rare_actions_in_a_menu():
    """导入页：任务类型是工具栏上的一个胶囊，罕用和破坏性的动作进「管理」菜单。"""
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.resize(1100, 720)
    window.show()
    window.switch_page(STEP_IMPORT)
    for _ in range(3):
        _app.processEvents()

    page = window.import_page

    assert page.btn_task_type.isVisible()
    assert "目标检测" in page.btn_task_type.text(), page.btn_task_type.text()

    labels = [a.text() for a in page.manage_menu.actions() if a.text()]
    for wanted in ("移动分组", "删除选中的图片", "清空全部图片", "删除项目"):
        assert wanted in labels, f"「{wanted}」应该在管理菜单里：{labels}"

    # 破坏性的两个要看得出来是破坏性的
    for action in page.destructive_actions:
        assert action.property('destructive') is True
        assert not action.icon().isNull(), f"{action.text()} 应该带一个红点"

    window.close()
    db.delete_project(project_id)


def test_header_hidden_on_import_and_annotate_business_pages():
    """导入 / 标注是业务页：左侧 StepNav 已经承担了流程和下一步导航，
    PageHeader 整块（标题、序号、说明、下一步按钮）不该再显示第二遍。"""
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.resize(1100, 720)
    window.show()
    for _ in range(3):
        _app.processEvents()

    window.switch_page(STEP_IMPORT)
    for _ in range(3):
        _app.processEvents()
    assert not window.header.isVisible(), "导入页不该显示 PageHeader"

    window.switch_page(STEP_ANNOTATE)
    for _ in range(3):
        _app.processEvents()
    assert not window.header.isVisible(), "标注页不该显示 PageHeader"

    window.close()
    db.delete_project(project_id)


def test_header_visible_on_non_business_pages():
    """训练、结果、测试、设置、关于继续显示 PageHeader。"""
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.resize(1100, 720)
    window.show()
    for _ in range(3):
        _app.processEvents()

    for index in (STEP_TRAIN, STEP_RESULT, STEP_TEST, PAGE_SETTINGS, PAGE_ABOUT):
        window.switch_page(index)
        for _ in range(3):
            _app.processEvents()
        assert window.header.isVisible(), f"第 {index} 页应该显示 PageHeader"

    window.close()
    db.delete_project(project_id)


def test_header_visible_when_gate_blocks_annotate_without_project():
    """没有项目时进标注页会被 StepGate 挡住——挡住的是业务页，不是页头。
    Gate 本身的上下文（第几步、说明）仍然要靠 PageHeader 显示。"""
    window = make_window()
    window.resize(1100, 720)
    window.show()
    _app.processEvents()

    window.switch_page(STEP_ANNOTATE)
    for _ in range(3):
        _app.processEvents()

    assert window.content_stack.currentIndex() == GATE_INDEX
    assert window.header.isVisible(), "Gate 挡住业务页时，PageHeader 应该继续显示"

    window.close()


def test_import_toolbar_moves_up_without_header():
    """页头隐藏后，工具栏是导入页第一个主要区块，离窗口顶部应该只剩页面自己的
    上边距（约 16~18px），而不是页头（标题+说明）撑出来的六七十像素。"""
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.resize(1100, 720)
    window.show()
    window.switch_page(STEP_IMPORT)
    for _ in range(3):
        _app.processEvents()

    toolbar = window.import_page.toolbar
    top_y = toolbar.mapTo(window, toolbar.rect().topLeft()).y()
    assert top_y < 40, (
        f"工具栏离窗口顶部还有 {top_y}px，页头似乎没有真正隐藏（应该只剩页面自身的上边距）"
    )

    window.close()
    db.delete_project(project_id)


def test_train_page_config_panel_width_is_fixed_across_sizes():
    """训练页左边配置栏宽度固定 360px，窗口变宽时增量都该给右边监控面板，
    不再靠 QSplitter 拖来拖去导致卡片横向漂移。"""
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.switch_page(STEP_TRAIN)

    page = window.train_page
    assert hasattr(page, 'config_panel') and hasattr(page, 'monitor_panel')

    widths = {}
    for width, height in SIZES:
        window.resize(width, height)
        window.show()
        for _ in range(3):
            _app.processEvents()
        widths[width] = page.config_panel.width()

        assert page.monitor_panel.isVisible()
        assert page.monitor_panel.width() >= 340

        problems = _clipped_widgets(page)
        assert not problems, f"[{width}x{height}] 训练页文字被切:\n" + "\n".join(problems)

    assert widths[1100] == 360, f"1100 宽时左栏应该是 360px，实际 {widths[1100]}"
    assert widths[1470] == 360, f"1470 宽时左栏应该仍是 360px，实际 {widths[1470]}"

    window.close()
    db.delete_project(project_id)


def test_train_page_template_row_does_not_scroll_horizontally():
    """训练模板这一行（下拉 + 套用 + 管理）挤在固定 360px 左栏里，只许纵向滚动。

    以前这一行把 scroll_content 撑得比视口宽，QScrollArea 底部会冒出一条横向
    滚动条，基础配置右边的控件（边框、下拉箭头）跟着被裁掉一截。真正的判据
    不是「有没有滚动条控件」，而是内容本身有没有比视口宽——横向滚动条被关掉
    之后，撑宽的内容会变成永久裁切，比留着滚动条更糟。
    """
    project_id = seed_project()
    window = make_window()
    window.load_projects(select_id=project_id)
    window.switch_page(STEP_TRAIN)

    page = window.train_page
    scroll = page.config_scroll

    for width, height in SIZES:
        window.resize(width, height)
        window.show()
        for _ in range(3):
            _app.processEvents()

        assert scroll.horizontalScrollBar().maximum() == 0, (
            f"[{width}x{height}] 配置栏出现横向滚动，maximum="
            f"{scroll.horizontalScrollBar().maximum()}（应为 0）"
        )

        viewport_width = scroll.viewport().width()
        for widget, label in (
            (page.template_combo, "训练模板下拉"),
            (page.btn_apply_template, "「套用」按钮"),
            (page.btn_template_menu, "「管理」按钮"),
        ):
            top_left = widget.mapTo(scroll.viewport(), widget.rect().topLeft())
            bottom_right = widget.mapTo(scroll.viewport(), widget.rect().bottomRight())
            assert top_left.x() >= 0 and bottom_right.x() <= viewport_width + SLACK, (
                f"[{width}x{height}] {label}超出了 scroll viewport 可见范围: "
                f"x={top_left.x()}..{bottom_right.x()}，viewport 宽度 {viewport_width}"
            )

        for btn in (page.btn_apply_template, page.btn_template_menu):
            assert not _text_is_clipped(btn), (
                f"[{width}x{height}] {btn.text()!r} 按钮的文字被切掉了"
            )

    window.close()
    db.delete_project(project_id)


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
