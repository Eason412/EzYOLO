# -*- coding: utf-8 -*-
"""标注页顶栏：图片、工具和标注方式在同一值行内对齐。

    python tests/test_annotate_topbar_layout.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QLabel  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402
from gui.workflow import STEP_ANNOTATE  # noqa: E402

_app = _bootstrap.app()
db = _bootstrap.db
_IMAGE_DIR = Path(tempfile.mkdtemp(prefix="ezyolo-topbar-"))
SIZES = ((1100, 720), (1470, 832))


def _settle():
    for _ in range(3):
        _app.processEvents()


def _seed_project():
    project_id = _bootstrap.create_temp_project(
        name="顶栏布局测试项目",
        project_type="detect",
        classes=[{'id': 0, 'name': '猫', 'color': '#FF3B30'}],
    )
    filename = "这一张图片的文件名特别特别特别长但工具栏不能被它推走.jpg"
    image_path = _IMAGE_DIR / filename
    image = QImage(320, 240, QImage.Format.Format_RGB888)
    image.fill(QColor(120, 120, 130))
    image.save(str(image_path))
    db.add_image(project_id, filename, str(image_path), width=320, height=240)
    return project_id


def _open_page(width, height):
    project_id = _seed_project()
    from gui import main_window as main_window_module
    main_window_module.db = db

    window = MainWindow()
    window.load_projects(select_id=project_id)
    window.resize(width, height)
    window.show()
    window.switch_page(STEP_ANNOTATE)
    _settle()
    page = window.annotate_page
    page._set_image_list_collapsed(False)
    page.image_list.setCurrentRow(0)
    page.on_image_selected(page.image_list.item(0))
    _settle()
    return window, page, project_id


def _close(window, project_id):
    window.close()
    db.delete_project(project_id)


def _assert_values_share_a_row_without_overlap(page, size):
    values = (page.image_name_label, page.toolbar, page.task_combo)
    y_positions = {widget.y() for widget in values}
    assert len(y_positions) == 1, f"[{size}] 顶栏值行没有对齐：{[(w.objectName(), w.y()) for w in values]}"

    left_to_right = sorted(values, key=lambda widget: widget.x())
    for left, right in zip(left_to_right, left_to_right[1:]):
        assert left.geometry().right() < right.geometry().left(), (
            f"[{size}] 顶栏值重叠：{left.geometry()} 与 {right.geometry()}"
        )

    for caption in page.context_bar.findChildren(QLabel):
        if caption.objectName() == "caption":
            assert caption.width() >= caption.fontMetrics().horizontalAdvance(caption.text()), (
                f"[{size}] 顶栏说明被裁字：{caption.text()}"
            )


def _assert_visible_toolbar_text_fits(page, size, task_type):
    visible = [button for button in page.toolbar_buttons if button.isVisible()]
    assert len(visible) >= 3, f"[{size}/{task_type}] 工具栏少了基础操作"
    assert len({button.y() for button in visible}) == 1, (
        f"[{size}/{task_type}] 工具按钮没有同一行：{[(b.text(), b.y()) for b in visible]}"
    )
    for button in visible:
        assert button.width() >= button.minimumSizeHint().width(), (
            f"[{size}/{task_type}] 工具按钮被裁字：{button.text()}"
        )


def test_topbar_owns_the_toolbar_and_keeps_values_aligned_at_supported_sizes():
    for size in SIZES:
        window, page, project_id = _open_page(*size)
        try:
            captions = [
                label.text() for label in page.context_bar.findChildren(QLabel)
                if label.objectName() == "caption"
            ]
            assert captions == ["当前图片", "工具", "标注方式"], f"[{size}] 顶栏说明不对：{captions}"
            assert page.toolbar.parentWidget() is page.context_bar
            assert not page.center_panel.isAncestorOf(page.toolbar)
            assert page.context_bar.height() < 73, (
                f"[{size}] 顶栏还有旧布局的高度：{page.context_bar.height()}px"
            )
            canvas_top = page.canvas.mapTo(page.center_panel, QPoint(0, 0)).y()
            assert canvas_top <= 10, f"[{size}] 画布顶部还有工具栏占位：{canvas_top}px"

            _assert_values_share_a_row_without_overlap(page, size)
            assert page.image_name_label.width() <= 320
            long_image_name = page.images[0]['filename']
            assert page.image_name_label.text() != page.image_name_label.property('_full_text'), (
                f"[{size}] 长文件名应该省略显示"
            )
            assert page.image_name_label.toolTip().splitlines()[0] == long_image_name, (
                f"[{size}] 长文件名的完整 tooltip 丢失"
            )
            assert page.toolbar.geometry().right() < page.task_combo.geometry().left(), (
                f"[{size}] 长图片名把工具或标注方式挤重叠了"
            )

            if size[0] == 1100:
                common_summary = "第 600/600 张 · 帧 81800 · 已标注"
                page._set_elided_text(page.image_name_label, common_summary)
                _settle()
                assert page.image_name_label.width() >= page.image_name_label.fontMetrics().horizontalAdvance(
                    common_summary
                ), f"[{size}] 常见图片摘要被无必要省略：{page.image_name_label.width()}px"
                assert page.image_name_label.text() == common_summary

            for collapsed in (False, True):
                if collapsed:
                    page.btn_collapse_image_list.click()
                    _settle()
                    assert page.btn_expand_image_list.parentWidget() is page.toolbar
                    assert page.btn_expand_image_list.isVisible(), f"[{size}] 收起后顶栏没有展开入口"
                else:
                    assert page.left_panel.isVisible(), f"[{size}] 图片栏初始状态不该收起"

                for task_type in ("detect", "segment", "pose", "classify"):
                    page.task_combo.setCurrentText(task_type)
                    _settle()
                    _assert_values_share_a_row_without_overlap(page, size)
                    _assert_visible_toolbar_text_fits(page, size, task_type)

                if collapsed:
                    page.btn_expand_image_list.click()
                    _settle()
                    assert page.left_panel.isVisible(), f"[{size}] 顶栏展开入口没有恢复图片栏"
        finally:
            _close(window, project_id)


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
