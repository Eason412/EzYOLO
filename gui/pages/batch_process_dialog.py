# -*- coding: utf-8 -*-
"""
批量处理标注对话框
支持批量删除和批量修改标注类别

界面按用户的操作顺序组织：选择范围 → 处理方式 → 确认执行。
弹窗是非模态的：用户要一边在画布上点像素点，一边在这里设置。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QSpinBox, QListWidget,
    QListWidgetItem, QRadioButton, QButtonGroup, QMessageBox,
    QStackedWidget, QWidget, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication
from typing import List, Tuple

from gui.styles import COLORS


class BatchProcessDialog(QDialog):
    """批量处理标注对话框"""

    # 信号定义
    process_requested = pyqtSignal(dict)  # 发送处理请求

    def __init__(self, parent=None, project_classes=None, total_images=0):
        super().__init__(parent)
        self.setWindowTitle("批量处理标注")

        # 项目类别
        self.project_classes = project_classes or []
        self.total_images = total_images

        # 选择的像素点
        self.selected_points = []

        # 初始化UI
        self.init_ui()

        # 窗口尺寸：内容放在滚动区里，窗口再小也不会截断
        available = QGuiApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(520, 480)
        self.resize(560, min(760, int(available.height() * 0.85)))

    def init_ui(self):
        """初始化界面"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 16)
        main_layout.setSpacing(12)

        # 标题与说明
        title = QLabel("批量处理标注")
        title.setObjectName("h2")
        main_layout.addWidget(title)

        desc = QLabel("对指定范围内的图片，批量删除或改写覆盖了指定坐标的标注。")
        desc.setObjectName("subtitle")
        desc.setWordWrap(True)
        main_layout.addWidget(desc)

        # 可滚动的设置区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(14)

        body_layout.addWidget(self.create_scope_group())
        body_layout.addWidget(self.create_operation_group())
        body_layout.addWidget(self.create_confirm_group())
        body_layout.addStretch()

        scroll.setWidget(body)
        main_layout.addWidget(scroll, 1)

        # 底部按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("secondary")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        self.btn_execute = QPushButton("执行批量删除")
        self.btn_execute.setObjectName("primary")
        self.btn_execute.clicked.connect(self.execute_process)
        button_layout.addWidget(self.btn_execute)

        main_layout.addLayout(button_layout)

        # 初始状态更新
        self.on_operation_changed()

    # ==================== 步骤1：选择范围 ====================

    def create_scope_group(self) -> QGroupBox:
        """像素点 + 图片区间，一起构成「要处理哪些标注」。"""
        self.point_group = QGroupBox("1 · 选择范围")
        layout = QVBoxLayout(self.point_group)
        layout.setSpacing(8)

        point_hint = QLabel("在图片上点击目标位置（可多选）。范围内每张图片里，覆盖这些坐标的标注才会被处理。")
        point_hint.setObjectName("caption")
        point_hint.setWordWrap(True)
        layout.addWidget(point_hint)

        self.point_status = QLabel("未选择像素点")
        self.point_status.setStyleSheet(f"color: {COLORS['warning']};")
        layout.addWidget(self.point_status)

        self.selected_points_list = QListWidget()
        self.selected_points_list.setMaximumHeight(96)
        layout.addWidget(self.selected_points_list)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_clear_points = QPushButton("清除所有点")
        self.btn_clear_points.setObjectName("ghost")
        self.btn_clear_points.clicked.connect(self.clear_points)
        self.btn_clear_points.setEnabled(False)
        btn_layout.addWidget(self.btn_clear_points)
        layout.addLayout(btn_layout)

        # 图片区间
        range_layout = QFormLayout()
        range_layout.setContentsMargins(0, 4, 0, 0)

        self.start_image = QSpinBox()
        self.start_image.setRange(1, max(1, self.total_images))
        self.start_image.setValue(1)
        self.start_image.valueChanged.connect(self.update_execute_button)
        range_layout.addRow("起始图片:", self.start_image)

        self.end_image = QSpinBox()
        self.end_image.setRange(1, max(1, self.total_images))
        self.end_image.setValue(self.total_images)
        self.end_image.valueChanged.connect(self.update_execute_button)
        range_layout.addRow("结束图片:", self.end_image)

        range_info = QLabel(f"按当前图片列表顺序计数，共 {self.total_images} 张图片")
        range_info.setObjectName("caption")
        range_layout.addRow(range_info)

        layout.addLayout(range_layout)

        return self.point_group

    # ==================== 步骤2：处理方式 ====================

    def create_operation_group(self) -> QGroupBox:
        """选删除还是改类别，并给出对应的类别选择。"""
        op_group = QGroupBox("2 · 处理方式")
        op_layout = QVBoxLayout(op_group)
        op_layout.setSpacing(8)

        self.op_group = QButtonGroup(self)

        self.rbtn_delete = QRadioButton("删除标注")
        self.rbtn_delete.setChecked(True)
        self.rbtn_delete.toggled.connect(self.on_operation_changed)
        self.op_group.addButton(self.rbtn_delete)
        op_layout.addWidget(self.rbtn_delete)

        self.rbtn_modify = QRadioButton("修改标注类别")
        self.rbtn_modify.toggled.connect(self.on_operation_changed)
        self.op_group.addButton(self.rbtn_modify)
        op_layout.addWidget(self.rbtn_modify)

        # 两种方式各有各的类别选择，用堆叠页切换，避免同时堆一屏控件
        self.class_stack = QStackedWidget()

        # 删除模式：多选要删除的类别
        self.delete_class_widget = QWidget()
        delete_class_layout = QVBoxLayout(self.delete_class_widget)
        delete_class_layout.setContentsMargins(0, 4, 0, 0)
        delete_class_layout.setSpacing(6)

        delete_label = QLabel("删除这些类别的标注（可多选）")
        delete_class_layout.addWidget(delete_label)

        self.delete_class_list = QListWidget()
        self.delete_class_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.delete_class_list.setMinimumHeight(110)
        self.delete_class_list.itemSelectionChanged.connect(self.update_execute_button)
        self.populate_class_list(self.delete_class_list)
        delete_class_layout.addWidget(self.delete_class_list)

        self.class_stack.addWidget(self.delete_class_widget)

        # 修改模式：多选初始类别，单选目标类别
        self.modify_class_widget = QWidget()
        modify_class_layout = QVBoxLayout(self.modify_class_widget)
        modify_class_layout.setContentsMargins(0, 4, 0, 0)
        modify_class_layout.setSpacing(6)

        source_label = QLabel("把这些类别的标注（可多选）")
        modify_class_layout.addWidget(source_label)

        self.source_class_list = QListWidget()
        self.source_class_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.source_class_list.setMinimumHeight(96)
        self.source_class_list.itemSelectionChanged.connect(self.update_execute_button)
        self.populate_class_list(self.source_class_list)
        modify_class_layout.addWidget(self.source_class_list)

        target_label = QLabel("改为这个类别（单选）")
        modify_class_layout.addWidget(target_label)

        self.target_class_list = QListWidget()
        self.target_class_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.target_class_list.setMinimumHeight(96)
        self.target_class_list.itemSelectionChanged.connect(self.update_execute_button)
        self.populate_class_list(self.target_class_list)
        modify_class_layout.addWidget(self.target_class_list)

        self.class_stack.addWidget(self.modify_class_widget)

        op_layout.addWidget(self.class_stack)

        return op_group

    # ==================== 步骤3：确认 ====================

    def create_confirm_group(self) -> QGroupBox:
        """执行前把「将要发生什么」一句话讲清楚。"""
        self.class_group = QGroupBox("3 · 确认")
        layout = QVBoxLayout(self.class_group)
        layout.setSpacing(6)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.risk_label = QLabel()
        self.risk_label.setWordWrap(True)
        self.risk_label.setStyleSheet(f"color: {COLORS['error']};")
        layout.addWidget(self.risk_label)

        return self.class_group

    def populate_class_list(self, list_widget: QListWidget):
        """填充类别列表"""
        list_widget.clear()
        for cls in self.project_classes:
            item = QListWidgetItem(f"{cls['id']}: {cls['name']}")
            item.setData(Qt.ItemDataRole.UserRole, cls['id'])
            # 设置颜色
            color = QColor(cls.get('color', '#808080'))
            item.setForeground(color)
            list_widget.addItem(item)

    def on_operation_changed(self):
        """操作类型改变时"""
        if self.rbtn_delete.isChecked():
            self.class_stack.setCurrentWidget(self.delete_class_widget)
        else:
            self.class_stack.setCurrentWidget(self.modify_class_widget)
        self.update_execute_button()

    def add_point(self, x: int, y: int):
        """添加像素点"""
        self.selected_points.append((x, y))
        self.update_points_display()
        self.update_execute_button()

    def clear_points(self):
        """清除所有像素点"""
        self.selected_points.clear()
        self.update_points_display()
        self.update_execute_button()

    def update_points_display(self):
        """更新像素点显示"""
        self.selected_points_list.clear()
        for i, (x, y) in enumerate(self.selected_points):
            item = QListWidgetItem(f"点 {i+1}: ({x}, {y})")
            self.selected_points_list.addItem(item)

        # 更新状态
        if self.selected_points:
            self.point_status.setText(f"已选择 {len(self.selected_points)} 个像素点")
            self.point_status.setStyleSheet(f"color: {COLORS['success']};")
            self.btn_clear_points.setEnabled(True)
        else:
            self.point_status.setText("未选择像素点")
            self.point_status.setStyleSheet(f"color: {COLORS['warning']};")
            self.btn_clear_points.setEnabled(False)

    def update_execute_button(self):
        """按当前选择刷新主操作按钮、摘要和风险说明"""
        is_delete = self.rbtn_delete.isChecked()
        image_count = max(0, self.end_image.value() - self.start_image.value() + 1)

        self.btn_execute.setText("执行批量删除" if is_delete else "执行批量修改")

        missing = None
        if not self.selected_points:
            missing = "还需在图片上点击至少一个像素点"
        elif self.start_image.value() > self.end_image.value():
            missing = "起始图片不能大于结束图片"
        elif is_delete and not self.delete_class_list.selectedItems():
            missing = "还需选择要删除的类别"
        elif not is_delete and not self.source_class_list.selectedItems():
            missing = "还需选择初始类别"
        elif not is_delete and not self.target_class_list.selectedItems():
            missing = "还需选择目标类别"

        self.btn_execute.setEnabled(missing is None)

        if missing:
            self.summary_label.setText(missing)
            self.summary_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
            self.risk_label.setText("")
            return

        self.summary_label.setStyleSheet(f"color: {COLORS['text_primary']};")
        point_count = len(self.selected_points)
        if is_delete:
            names = self._selected_class_names(self.delete_class_list)
            self.summary_label.setText(
                f"将在第 {self.start_image.value()}–{self.end_image.value()} 张（共 {image_count} 张）图片中，"
                f"删除覆盖这 {point_count} 个坐标、且类别为 {names} 的标注。"
            )
            self.risk_label.setText("删除会直接写入数据库，无法撤销。")
        else:
            sources = self._selected_class_names(self.source_class_list)
            target = self._selected_class_names(self.target_class_list)
            self.summary_label.setText(
                f"将在第 {self.start_image.value()}–{self.end_image.value()} 张（共 {image_count} 张）图片中，"
                f"把覆盖这 {point_count} 个坐标、类别为 {sources} 的标注改为 {target}。"
            )
            self.risk_label.setText("修改会直接写入数据库，无法撤销。")

    def _selected_class_names(self, list_widget: QListWidget) -> str:
        """选中类别的可读名称，供摘要文案使用。"""
        return "、".join(item.text().split(": ", 1)[-1] for item in list_widget.selectedItems())

    def execute_process(self):
        """执行批量处理"""
        if not self.selected_points:
            QMessageBox.warning(self, "提示", "请先选择像素点")
            return

        # 获取处理范围
        start_idx = self.start_image.value() - 1  # 转换为0-based索引
        end_idx = self.end_image.value() - 1

        if start_idx > end_idx:
            QMessageBox.warning(self, "提示", "起始图片不能大于结束图片")
            return

        # 构建处理配置
        config = {
            'points': self.selected_points.copy(),
            'start_idx': start_idx,
            'end_idx': end_idx,
            'operation': 'delete' if self.rbtn_delete.isChecked() else 'modify'
        }

        if self.rbtn_delete.isChecked():
            # 获取要删除的类别
            selected_items = self.delete_class_list.selectedItems()
            if not selected_items:
                QMessageBox.warning(self, "提示", "请选择要删除的类别")
                return
            config['target_classes'] = [item.data(Qt.ItemDataRole.UserRole) for item in selected_items]
        else:
            # 获取初始类别和目标类别
            source_items = self.source_class_list.selectedItems()
            if not source_items:
                QMessageBox.warning(self, "提示", "请选择初始类别")
                return

            target_items = self.target_class_list.selectedItems()
            if not target_items:
                QMessageBox.warning(self, "提示", "请选择目标类别")
                return

            config['source_classes'] = [item.data(Qt.ItemDataRole.UserRole) for item in source_items]
            config['target_class'] = target_items[0].data(Qt.ItemDataRole.UserRole)

        # 最后一道确认：这一步会直接改数据库且不可撤销
        reply = QMessageBox.question(
            self,
            "确认批量删除" if config['operation'] == 'delete' else "确认批量修改",
            f"{self.summary_label.text()}\n\n{self.risk_label.text()}\n\n确定执行吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 发送处理请求
        self.process_requested.emit(config)
        self.accept()

    def get_selected_points(self) -> List[Tuple[int, int]]:
        """获取选择的像素点"""
        return self.selected_points.copy()
