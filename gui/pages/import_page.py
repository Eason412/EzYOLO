# -*- coding: utf-8 -*-
"""
数据导入页面（第 1 步）

页面只负责一件事：把图片弄进当前项目。
项目的选择/新建/删除入口在主窗口侧边栏，这里不再重复放一个项目下拉框。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGridLayout, QFrame, QFileDialog, QProgressBar,
    QMenu, QMessageBox, QComboBox, QLineEdit, QListWidget, QListWidgetItem,
    QDialog, QStackedWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QSize
from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QFont, QIcon, QFontMetrics
import cv2
import numpy as np
import threading
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable
import os

from gui.styles import COLORS
from models.database import db
from core.import_manager import ImportManager
from core.annotation_importer import AnnotationImporter
from gui.widgets.loading_dialog import LoadingOverlay
from gui.widgets.group_select_dialog import GroupSelectDialog, ask_import_group
from gui.widgets.task_type_dialog import ask_task_type, task_type_label
from gui.widgets.workflow_widgets import EmptyState


# 数据导入线程：文件夹 / 多图 / 视频，实际工作全部委托给 core.import_manager，
# 主线程不做逐文件复制、解码或数据库写入。
class ImportWorkerThread(QThread):
    """通用后台导入线程"""

    progress_updated = pyqtSignal(int, str)
    # success, error_message, cancelled, imported, skipped
    # 注意：不能叫 finished —— QThread 自带同名信号（线程真正退出时才发），
    # 定义同名信号会把它遮住，导致没人能再监听「线程真的跑完了」这件事，
    # 从而在还没运行结束时就被提前释放引用，触发
    # "QThread: Destroyed while thread is still running"。
    result_ready = pyqtSignal(bool, str, bool, int, int)

    def __init__(self, project_id, group_id, kind, source, frame_interval=1):
        super().__init__()
        self.project_id = project_id
        self.group_id = group_id
        self.kind = kind  # 'folder' | 'images' | 'video'
        self.source = source
        self.frame_interval = frame_interval
        self._cancel_event = threading.Event()

    def cancel(self):
        """请求取消：最迟在下一个文件/帧边界停止。"""
        self._cancel_event.set()

    def run(self):
        """运行导入"""
        try:
            import_manager = ImportManager(self.project_id, group_id=self.group_id)

            def progress_callback(progress, message):
                self.progress_updated.emit(progress, message)

            if self.kind == 'folder':
                imported, skipped = import_manager.import_folder(
                    self.source, progress_callback=progress_callback,
                    cancel_event=self._cancel_event,
                )
            elif self.kind == 'images':
                imported, skipped = import_manager.import_images(
                    self.source, progress_callback=progress_callback,
                    cancel_event=self._cancel_event,
                )
            elif self.kind == 'video':
                imported, skipped = import_manager.import_video(
                    self.source, frame_interval=self.frame_interval,
                    progress_callback=progress_callback,
                    cancel_event=self._cancel_event,
                )
            else:
                raise ValueError(f"未知导入类型: {self.kind}")

            self.result_ready.emit(True, "", self._cancel_event.is_set(), imported, skipped)
        except Exception as e:
            self.result_ready.emit(False, str(e), False, 0, 0)


class ImageLoadWorker(QThread):
    """图片加载工作线程"""
    
    # 信号：进度更新、单个图片加载完成、全部完成
    progress = pyqtSignal(int, int)  # 当前进度, 总数
    image_loaded = pyqtSignal(int, object, str)  # 索引, 缩略图, 存储路径
    finished_loading = pyqtSignal()
    
    def __init__(self, image_tasks: List[Tuple[int, Dict]]):
        super().__init__()
        self.image_tasks = image_tasks
        self._is_running = True
    
    def run(self):
        """在后台线程中加载图片"""
        total = len(self.image_tasks)
        
        for task_index, (row_index, image_data) in enumerate(self.image_tasks):
            if not self._is_running:
                break
            
            storage_path = image_data.get('storage_path', '')
            pixmap = None
            
            if storage_path and os.path.exists(storage_path):
                try:
                    # 使用OpenCV加载，比QPixmap更快
                    img = cv2.imread(storage_path)
                    if img is not None:
                        # 直接缩小到缩略图尺寸，减少内存占用
                        img = cv2.resize(img, (160, 160))
                        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        h, w, ch = img.shape
                        bytes_per_line = ch * w
                        qt_image = QImage(img.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
                        pixmap = QPixmap.fromImage(qt_image)
                except Exception:
                    pass
            
            # 如果加载失败，创建空白图
            if pixmap is None or pixmap.isNull():
                pixmap = QPixmap(160, 160)
                pixmap.fill(QColor(COLORS['sidebar']))
            
            # 发送信号到主线程更新UI
            self.image_loaded.emit(row_index, pixmap, storage_path)
            self.progress.emit(task_index + 1, total)
            
            # 每加载10张图片休眠一下，让UI有机会更新
            if task_index % 10 == 0:
                self.msleep(1)
        
        self.finished_loading.emit()
    
    def stop(self):
        """停止加载"""
        self._is_running = False


class ImportPage(QWidget):
    """数据导入页面"""

    # 项目列表变了（新建 / 删除），附带之后应该选中的项目 id（没有则 None）
    projects_changed = pyqtSignal(object)
    # 当前项目的图片或标注数量变了，主窗口据此刷新流程进度
    project_data_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.current_project_id = None
        self.images = []
        self.thumbnail_cache = {}
        self.load_worker = None
        self._image_load_generation = 0
        self.thumbnail_widgets = []  # 存储缩略图控件引用

        # 导入任务状态：与上面的缩略图加载状态（load_worker/_image_load_generation）
        # 完全分离，切页、刷新缩略图都不应该影响这一组状态
        self._active_import_thread = None
        # 被取消/作废但线程还没真正跑完的（比如切项目）：必须继续持有引用，
        # 直到 QThread 原生 finished 信号确认线程真的退出了才能放手，
        # 否则会在线程仍在运行时被 GC，触发 "QThread: Destroyed while thread is still running"。
        self._retired_import_threads = []
        self._import_busy = False
        self._import_generation = 0
        self._import_finalize_pending = False
        self._import_finalize_summary = ""
        self._import_finalize_cancelled = False
        self._import_before_ids = set()

        self.init_ui()
        self.refresh_view_filter_options()
        self.update_project_bar()
        self.update_view_mode()

    def _remove_cached_thumbnails(self, storage_paths):
        """按路径移除缩略图缓存。"""
        for path in storage_paths:
            if path in self.thumbnail_cache:
                del self.thumbnail_cache[path]

    def stop_image_loading(self, reset_progress: bool = True):
        """停止当前缩略图加载线程，并可选清理进度状态。"""
        self._image_load_generation += 1
        if self.load_worker and self.load_worker.isRunning():
            self.load_worker.stop()
            self.load_worker.wait()
        self.load_worker = None
        if reset_progress and hasattr(self, 'progress_bar'):
            self.progress_bar.setVisible(False)
            self.progress_bar.setValue(0)
    
    def init_ui(self):
        """初始化界面"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 18, 24, 18)
        main_layout.setSpacing(14)

        # 项目信息条：当前项目叫什么、要做哪种任务
        self.project_bar = self.create_project_bar()
        main_layout.addWidget(self.project_bar)

        # 工具栏：左边加数据，右边管数据
        self.toolbar = self.create_toolbar()
        main_layout.addWidget(self.toolbar)

        # 导入任务状态条（默认隐藏）：只反映“导入任务”本身，
        # 跟下面缩略图加载用的 progress_bar 是两套独立状态
        self.import_status_frame = self.create_import_status_bar()
        self.import_status_frame.setVisible(False)
        main_layout.addWidget(self.import_status_frame)

        # 缩略图加载进度条（默认隐藏）
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

        # 图片区：没项目 / 没图片 / 图片网格，三选一
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.create_no_project_state())   # 0
        self.view_stack.addWidget(self.create_no_image_state())     # 1
        self.view_stack.addWidget(self.create_image_grid())         # 2
        main_layout.addWidget(self.view_stack, 1)

        # 状态栏
        self.status_bar = self.create_status_bar()
        main_layout.addWidget(self.status_bar)

    def create_project_bar(self) -> QFrame:
        """项目信息条：项目名 + 任务类型 + 删除项目"""
        bar = QFrame()
        bar.setObjectName("card")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        self.project_name_label = QLabel("未选择项目")
        self.project_name_label.setObjectName("h2")
        # 名字可能很长，不能让它把任务类型 / 删除按钮挤出去，超出部分省略并放进 tooltip
        self.project_name_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.project_name_label.setMaximumWidth(280)
        layout.addWidget(self.project_name_label)

        self.btn_task_type = QPushButton("任务类型：未设置")
        self.btn_task_type.setToolTip("决定标注工具和训练方式，一般选「目标检测」")
        self.btn_task_type.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_task_type.clicked.connect(self.change_task_type)
        layout.addWidget(self.btn_task_type)

        layout.addStretch()

        self.btn_delete_project = QPushButton("删除项目")
        self.btn_delete_project.setObjectName("danger")
        self.btn_delete_project.setToolTip("连同项目里的图片和标注一起删除，不可恢复")
        self.btn_delete_project.clicked.connect(self.delete_current_project)
        layout.addWidget(self.btn_delete_project)

        return bar

    def create_toolbar(self) -> QFrame:
        """创建工具栏"""
        toolbar = QFrame()
        toolbar.setObjectName("toolbar")

        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        # 主操作：绝大多数人是导入一个文件夹
        self.btn_import_folder = QPushButton("导入文件夹")
        self.btn_import_folder.setObjectName("primary")
        self.btn_import_folder.setToolTip("把一个文件夹里的图片一次性全部导入")
        self.btn_import_folder.clicked.connect(self.import_folder)
        layout.addWidget(self.btn_import_folder)

        self.btn_import_images = QPushButton("导入图片")
        self.btn_import_images.setToolTip("选择单张或多张图片导入")
        self.btn_import_images.clicked.connect(self.import_images)
        layout.addWidget(self.btn_import_images)

        self.btn_import_video = QPushButton("导入视频")
        self.btn_import_video.setToolTip("从视频里每隔若干帧抽一张图片导入")
        self.btn_import_video.clicked.connect(self.import_video)
        layout.addWidget(self.btn_import_video)

        self.btn_import_annotations = QPushButton("导入标注")
        self.btn_import_annotations.setToolTip("导入已标注数据（YOLO / COCO / VOC 格式）")
        self.btn_import_annotations.clicked.connect(self.import_annotations)
        layout.addWidget(self.btn_import_annotations)

        layout.addStretch()

        filter_label = QLabel("筛选")
        filter_label.setObjectName("caption")
        layout.addWidget(filter_label)

        self.view_combo = QComboBox()
        self.view_combo.setMinimumWidth(110)
        self.view_combo.currentTextChanged.connect(self.filter_images)
        layout.addWidget(self.view_combo)

        self.btn_move_group = QPushButton("移动分组")
        self.btn_move_group.setToolTip("把选中图片移到指定分组")
        self.btn_move_group.clicked.connect(self.move_selected_to_group)
        layout.addWidget(self.btn_move_group)

        self.btn_delete_selected = QPushButton("删除选中")
        self.btn_delete_selected.clicked.connect(self.delete_selected_images)
        layout.addWidget(self.btn_delete_selected)

        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("danger")
        self.btn_clear.setToolTip("删除当前项目里的全部图片")
        self.btn_clear.clicked.connect(self.clear_all_images)
        layout.addWidget(self.btn_clear)

        return toolbar

    def create_import_status_bar(self) -> QFrame:
        """导入任务状态条：状态文字 + 进度条 + 取消按钮。

        只在有导入任务时出现，跟缩略图加载进度条（self.progress_bar）
        是两套独立状态，互不清空对方。
        """
        frame = QFrame()
        frame.setObjectName("card")

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        self.import_status_label = QLabel("")
        self.import_status_label.setObjectName("caption")
        top_row.addWidget(self.import_status_label, 1)

        self.btn_cancel_import = QPushButton("取消导入")
        self.btn_cancel_import.setObjectName("danger")
        self.btn_cancel_import.setVisible(False)
        self.btn_cancel_import.clicked.connect(self._cancel_active_import)
        top_row.addWidget(self.btn_cancel_import)

        layout.addLayout(top_row)

        self.import_progress_bar = QProgressBar()
        self.import_progress_bar.setTextVisible(False)
        layout.addWidget(self.import_progress_bar)

        return frame

    def create_no_project_state(self) -> EmptyState:
        """还没有项目时的引导。"""
        state = EmptyState(
            "还没有项目",
            "新建一个项目开始。",
        )
        state.add_action("新建项目", self.create_new_project, primary=True)
        return state

    def create_no_image_state(self) -> EmptyState:
        """项目里还没有图片时的引导。"""
        state = EmptyState(
            "还没有图片",
            "",
        )
        state.add_action("导入文件夹", self.import_folder, primary=True)
        state.add_action("导入图片", self.import_images)
        state.add_action("导入视频", self.import_video)
        return state

    def create_image_grid(self) -> QListWidget:
        """图片缩略图网格。"""
        self.image_list = QListWidget()
        self.image_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.image_list.setIconSize(QSize(160, 160))
        self.image_list.setSpacing(10)
        self.image_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.image_list.setMovement(QListWidget.Movement.Static)
        self.image_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.image_list.setUniformItemSizes(True)
        self.image_list.setGridSize(QSize(184, 208))
        self.image_list.itemClicked.connect(self.on_image_clicked)
        # 注意：这里不写 item 的 background-color，
        # 让代码里 setBackground 设的「已标注」底色能显示出来
        self.image_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {COLORS['background']};
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
                padding: 8px;
            }}
            QListWidget::item {{
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
                padding: 6px;
            }}
            QListWidget::item:selected {{
                border: 1px solid {COLORS['primary']};
            }}
        """)
        return self.image_list

    def _apply_item_status(self, item: QListWidgetItem, image_data: Dict):
        """让「哪些图已经标过」在网格里一眼看得出来：勾号 + 绿字，不只靠颜色。"""
        filename = image_data['filename']
        annotated = image_data.get('status') == 'annotated'

        if annotated:
            item.setText(f"✓ {filename}")
            item.setForeground(QColor(COLORS['success']))
            item.setBackground(QColor(COLORS['success_soft']))
        else:
            item.setText(filename)
            item.setForeground(QColor(COLORS['text_secondary']))
            item.setBackground(QColor(COLORS['panel']))

    def create_status_bar(self) -> QFrame:
        """创建状态栏"""
        status_bar = QFrame()
        status_bar.setObjectName("card")

        layout = QHBoxLayout(status_bar)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(16)

        self.status_total = QLabel("共 0 张图片")
        layout.addWidget(self.status_total)

        self.status_annotated = QLabel("已标注 0")
        self.status_annotated.setStyleSheet(f"color: {COLORS['success']};")
        layout.addWidget(self.status_annotated)

        self.status_pending = QLabel("未标注 0")
        self.status_pending.setStyleSheet(f"color: {COLORS['text_secondary']};")
        layout.addWidget(self.status_pending)

        layout.addStretch()

        self.btn_refresh_status = QPushButton("刷新")
        self.btn_refresh_status.setObjectName("ghost")
        self.btn_refresh_status.setToolTip("重新读取当前项目的图片与标注状态")
        self.btn_refresh_status.clicked.connect(self.force_refresh_images)
        layout.addWidget(self.btn_refresh_status)

        return status_bar

    # ==================== 项目 ====================

    def set_project(self, project_id):
        """由主窗口调用：切换当前项目。"""
        self.stop_image_loading()
        if self._import_busy:
            # 项目context变了，旧项目的导入任务不再有意义：只请求取消、
            # 把这一批回调标记作废，但线程本身可能还在跑（cancel() 只是设个
            # 事件，最迟在下一个文件/帧边界才会真正退出）——不能在这里就丢掉
            # 引用，否则 QThread 对象会在仍在运行时被 GC，触发
            # "QThread: Destroyed while thread is still running"。
            # 把它转移到「已作废但还没退出」的列表里继续持有，真正退出
            # （原生 finished 信号）时再释放，见 _on_import_thread_actually_finished。
            if self._active_import_thread is not None:
                self._active_import_thread.cancel()
                self._retired_import_threads.append(self._active_import_thread)
                self._active_import_thread = None
            self._import_generation += 1
            self._end_import_ui()
        self.current_project_id = project_id
        self.images = []
        self.image_list.clear()
        self.thumbnail_widgets.clear()

        self.refresh_view_filter_options()
        self.update_project_bar()

        if project_id:
            self.load_project_images()
        else:
            self.update_status_bar()
            self.update_view_mode()

    def update_project_bar(self):
        """刷新项目信息条与按钮可用性。"""
        has_project = bool(self.current_project_id)

        project = db.get_project(self.current_project_id) if has_project else None
        if project:
            name = project.get('name', '未命名项目')
            self.btn_task_type.setText(f"任务类型：{task_type_label(project.get('type'))}")
        else:
            name = "未选择项目"
            self.btn_task_type.setText("任务类型：未设置")

        metrics = QFontMetrics(self.project_name_label.font())
        elided = metrics.elidedText(name, Qt.TextElideMode.ElideRight, self.project_name_label.maximumWidth())
        self.project_name_label.setText(elided)
        self.project_name_label.setToolTip(name)

        for button in (
            self.btn_task_type, self.btn_delete_project,
            self.btn_import_folder, self.btn_import_images,
            self.btn_import_video, self.btn_import_annotations,
            self.btn_move_group, self.btn_delete_selected, self.btn_clear,
            self.btn_refresh_status, self.view_combo,
        ):
            button.setEnabled(has_project)

        # 导入任务进行中：即使有项目，也不能再启动新导入或破坏当前项目
        if self._import_busy:
            self._set_import_controls_enabled(False)

    def update_view_mode(self):
        """在「没项目 / 没图片 / 有图片」三种状态之间切换。"""
        has_project = bool(self.current_project_id)

        # 没有项目时，工具栏和状态栏全是灰的、没意义，直接收起来，只留一句引导
        self.project_bar.setVisible(has_project)
        self.toolbar.setVisible(has_project)
        self.status_bar.setVisible(has_project)

        if not has_project:
            self.view_stack.setCurrentIndex(0)
        elif not self.images:
            self.view_stack.setCurrentIndex(1)
        else:
            self.view_stack.setCurrentIndex(2)

    def force_refresh_images(self):
        """刷新按钮：强制重读一次。"""
        if self.current_project_id:
            self.load_project_images()

    def refresh_project_images(self):
        """由主窗口在进入本页时调用：数据变了才重建列表。"""
        if not self.current_project_id:
            return

        latest = db.get_project_images(self.current_project_id)
        latest_signature = [(img['id'], img.get('status')) for img in latest]
        current_signature = [(img['id'], img.get('status')) for img in self.images]
        if latest_signature == current_signature:
            return

        self.load_project_images()

    def change_task_type(self):
        """修改当前项目的任务类型。"""
        if not self.current_project_id:
            return

        project = db.get_project(self.current_project_id)
        current = (project or {}).get('type') or 'detect'

        task_type = ask_task_type(self, current=current, title="修改任务类型")
        if not task_type:
            return

        db.update_project(self.current_project_id, type=task_type)
        self.update_project_bar()

    def create_new_project(self):
        """创建新项目：先起名字，再选任务类型。"""
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "新建项目", "给项目起个名字（比如「安全帽检测」）：")
        if not ok or not name.strip():
            return

        task_type = ask_task_type(self, current='detect', title="这个项目要做什么")
        if not task_type:
            return

        project_id = db.create_project(
            name=name.strip(),
            description="",
            project_type=task_type,
            classes=[]
        )
        # 交给主窗口去刷新下拉框并选中新项目
        self.projects_changed.emit(project_id)

    def delete_current_project(self):
        """删除当前项目。"""
        if not self.current_project_id:
            return

        project = db.get_project(self.current_project_id)
        project_name = (project or {}).get('name', '')

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除项目「{project_name}」吗？\n\n"
            "项目里的所有图片和标注都会一起删除，无法恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self.stop_image_loading()
            project_storage_path = (project or {}).get('storage_path', '')

            db.delete_project(self.current_project_id)

            if project_storage_path and os.path.exists(project_storage_path):
                try:
                    import shutil
                    shutil.rmtree(project_storage_path)
                except Exception as e:
                    # 文件夹删不掉不影响项目已经删除的事实
                    print(f"删除项目文件夹失败: {e}")

            removed_storage_paths = [img.get('storage_path', '') for img in self.images]
            self.current_project_id = None
            self.image_list.clear()
            self.images = []
            self.thumbnail_widgets.clear()
            self._remove_cached_thumbnails(removed_storage_paths)

            self.update_status_bar()
            self.update_project_bar()
            self.update_view_mode()
            self.projects_changed.emit(None)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"删除项目失败: {str(e)}")

    def load_project_images(self):
        """加载项目图像 - 使用多线程"""
        if not self.current_project_id:
            return

        # 全量重载会把最新数据（含刚导入的）一次性读回来，之前那个
        # 「导入后台正在整理缩略图」的收尾就不用再等了，直接结束掉，
        # 避免它的世代被下面的 stop_image_loading 作废后再也等不到回调、卡住不收尾。
        if self._import_finalize_pending:
            self._end_import_ui(self._import_finalize_summary, cancelled=self._import_finalize_cancelled)

        # 停止之前的加载，并创建新的加载世代
        self.stop_image_loading(reset_progress=False)

        # 清空列表
        self.image_list.clear()
        self.thumbnail_widgets.clear()

        # 从数据库获取图片列表（很快）
        self.images = db.get_project_images(self.current_project_id)
        self.update_status_bar()
        self.update_view_mode()

        if not self.images:
            self.progress_bar.setVisible(False)
            return

        # 先创建所有列表项（显示占位符）
        uncached_tasks = []
        for index, image_data in enumerate(self.images):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, image_data['id'])
            item.setToolTip(f"{image_data['filename']}\n{image_data.get('width', 0)}x{image_data.get('height', 0)}")

            self._apply_item_status(item, image_data)

            # 设置项目大小提示，确保即使没有图标也有足够高度
            item.setSizeHint(QSize(180, 200))

            storage_path = image_data.get('storage_path', '')
            cached_pixmap = self.thumbnail_cache.get(storage_path)
            if cached_pixmap is not None and not cached_pixmap.isNull():
                item.setIcon(QIcon(cached_pixmap))
            else:
                uncached_tasks.append((index, image_data))

            self.image_list.addItem(item)

        self.filter_images(self.view_combo.currentText())

        if not uncached_tasks:
            self.progress_bar.setVisible(False)
            return

        self._start_thumbnail_worker(uncached_tasks)

    def _start_thumbnail_worker(self, tasks: List[Tuple[int, Dict]], on_finished: Callable[[], None] = None):
        """启动缩略图后台加载线程。

        跟导入任务状态完全独立：这里只负责把 tasks（(行号, 图片记录) 列表）
        对应的缩略图在后台生成好，不做任何导入相关判断。
        """
        self.stop_image_loading(reset_progress=False)
        current_generation = self._image_load_generation

        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(tasks))
        self.progress_bar.setValue(0)

        self.load_worker = ImageLoadWorker(tasks)
        self.load_worker.image_loaded.connect(
            lambda index, pixmap, storage_path, generation=current_generation: self.on_image_loaded(
                generation, index, pixmap, storage_path
            )
        )
        self.load_worker.progress.connect(
            lambda current, total, generation=current_generation: self.on_load_progress(
                generation, current, total
            )
        )
        self.load_worker.finished_loading.connect(
            lambda generation=current_generation, cb=on_finished: self.on_load_finished(generation, cb)
        )
        self.load_worker.start()

    def on_image_loaded(self, generation: int, index: int, pixmap: QPixmap, storage_path: str):
        """单个图片加载完成回调（在主线程执行）"""
        if generation != self._image_load_generation:
            return
        if index < self.image_list.count():
            item = self.image_list.item(index)
            if item:
                # 设置图标
                icon = QIcon(pixmap)
                item.setIcon(icon)
                # 缓存
                self.thumbnail_cache[storage_path] = pixmap

    def on_load_progress(self, generation: int, current: int, total: int):
        """加载进度回调"""
        if generation != self._image_load_generation:
            return
        self.progress_bar.setValue(current)

    def on_load_finished(self, generation: int, on_finished: Callable[[], None] = None):
        """加载完成回调"""
        if generation != self._image_load_generation:
            return
        worker = self.load_worker
        self.load_worker = None
        if worker is not None:
            # finished_loading 跟 ImportWorkerThread 的 result_ready 是同一类问题：
            # 在 run() 返回前手动 emit，不代表线程已经真正退出。这里同步等一下
            # （此时线程本来就快跑完了，代价可以忽略），确保丢引用前线程已经
            # 真正结束，避免它在还在运行时被 GC 掉。
            worker.wait()
        self.progress_bar.setVisible(False)

        if hasattr(self, 'loading_overlay'):
            self.loading_overlay.hide_loading()
            self.loading_overlay.deleteLater()
            delattr(self, 'loading_overlay')

        if on_finished:
            on_finished()

    def _build_image_list_item(self, image_data: Dict) -> QListWidgetItem:
        """创建图片列表项（占位图标）"""
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, image_data['id'])

        group_id = image_data.get('group_id')
        group_name = "未分组"
        if group_id:
            group = db.get_image_group(group_id)
            if group:
                group_name = group['name']

        item.setToolTip(
            f"{image_data['filename']}\n"
            f"{image_data.get('width', 0)}x{image_data.get('height', 0)}\n"
            f"分组: {group_name}"
        )

        self._apply_item_status(item, image_data)

        item.setSizeHint(QSize(180, 200))
        return item

    def _append_imported_images(self, before_image_ids: set,
                                 on_thumbnails_ready: Callable[[], None] = None) -> int:
        """导入后仅增量追加新图片，返回追加数量。

        缩略图改为丢给后台的 ImageLoadWorker 生成，不在 UI 线程同步 cv2.imread；
        `on_thumbnails_ready` 保证恰好被调用一次——没有新图/缩略图全部命中缓存时
        同步调用，否则等后台线程真正做完再调用。
        """
        if not self.current_project_id:
            if on_thumbnails_ready:
                on_thumbnails_ready()
            return 0

        latest_images = db.get_project_images(self.current_project_id)
        # 同时排除 before_image_ids（导入前已有的）和 self.images 里已存在的
        # （可能被中途的一次全量重载提前补上了），避免同一张图重复出现
        existing_ids = {img.get('id') for img in self.images}
        new_images = [
            img for img in latest_images
            if img.get('id') not in before_image_ids and img.get('id') not in existing_ids
        ]
        if not new_images:
            if on_thumbnails_ready:
                on_thumbnails_ready()
            return 0

        self.images.extend(new_images)

        uncached_tasks = []
        for image_data in new_images:
            item = self._build_image_list_item(image_data)
            self.image_list.addItem(item)
            row_index = self.image_list.count() - 1

            storage_path = image_data.get('storage_path', '')
            cached_pixmap = self.thumbnail_cache.get(storage_path)
            if cached_pixmap is not None and not cached_pixmap.isNull():
                item.setIcon(QIcon(cached_pixmap))
            else:
                uncached_tasks.append((row_index, image_data))

        self.update_status_bar()
        self.update_view_mode()
        self.filter_images(self.view_combo.currentText())

        if uncached_tasks:
            self._start_thumbnail_worker(uncached_tasks, on_finished=on_thumbnails_ready)
        elif on_thumbnails_ready:
            on_thumbnails_ready()

        return len(new_images)

    def refresh_view_filter_options(self):
        """刷新筛选下拉框（含分组列表）。"""
        current_filter = self.view_combo.currentText() if hasattr(self, 'view_combo') else "全部"
        self.view_combo.blockSignals(True)
        self.view_combo.clear()
        self.view_combo.addItem("全部", "all")
        self.view_combo.addItem("未标注", "pending")
        self.view_combo.addItem("已标注", "annotated")
        self.view_combo.addItem("未分组", "ungrouped")

        if self.current_project_id:
            groups = db.get_project_image_groups(self.current_project_id)
            counts = db.get_group_image_counts(self.current_project_id)
            for group in groups:
                count = counts.get(group['id'], 0)
                label = f"分组: {group['name']} ({count})"
                self.view_combo.addItem(label, group['id'])

        restored = False
        for i in range(self.view_combo.count()):
            if self.view_combo.itemText(i) == current_filter:
                self.view_combo.setCurrentIndex(i)
                restored = True
                break
        if not restored:
            self.view_combo.setCurrentIndex(0)
        self.view_combo.blockSignals(False)

    
    def filter_images(self, filter_text: str):
        """筛选图像"""
        filter_data = self.view_combo.currentData()
        for i in range(self.image_list.count()):
            item = self.image_list.item(i)
            image_id = item.data(Qt.ItemDataRole.UserRole)
            
            image_data = next((img for img in self.images if img['id'] == image_id), None)
            if not image_data:
                continue
            
            status = image_data.get('status', 'pending')
            group_id = image_data.get('group_id')
            visible = True

            if filter_data == "all":
                visible = True
            elif filter_data == "pending":
                visible = status == 'pending'
            elif filter_data == "annotated":
                visible = status == 'annotated'
            elif filter_data == "ungrouped":
                visible = group_id is None
            elif isinstance(filter_data, int):
                visible = group_id == filter_data
            else:
                if filter_text == "全部":
                    visible = True
                elif filter_text == "未标注":
                    visible = status == 'pending'
                elif filter_text == "已标注":
                    visible = status == 'annotated'
                elif filter_text == "未分组":
                    visible = group_id is None
                elif filter_text.startswith("分组:"):
                    visible = False
                    if group_id:
                        group = db.get_image_group(group_id)
                        if group and f"分组: {group['name']}" in filter_text:
                            visible = True

            item.setHidden(not visible)
    
    def on_image_clicked(self, item: QListWidgetItem):
        """图像点击事件"""
        image_id = item.data(Qt.ItemDataRole.UserRole)
        # TODO: 实现图像预览或编辑
        pass
    
    def update_status_bar(self):
        """更新状态栏，并把进度变化告诉主窗口"""
        total = len(self.images)
        annotated = sum(1 for img in self.images if img.get('status') == 'annotated')
        pending = total - annotated

        self.status_total.setText(f"共 {total} 张图片")
        self.status_annotated.setText(f"已标注 {annotated}")
        self.status_pending.setText(f"未标注 {pending}")

        self.project_data_changed.emit()

    # ==================== 导入任务状态 ====================
    # 下面这组方法管理「导入任务」本身的状态（进行中 / 取消 / 完成），
    # 跟缩略图加载状态（load_project_images / on_load_finished 那组）完全分开。

    def _set_import_controls_enabled(self, enabled: bool):
        """导入中要禁掉的，只有会启动重复导入、或者会破坏当前项目的操作。"""
        for button in (
            self.btn_import_folder, self.btn_import_images,
            self.btn_import_video, self.btn_import_annotations,
            self.btn_delete_project, self.btn_clear,
        ):
            button.setEnabled(enabled)

    def _start_import_ui(self, message: str, indeterminate: bool = True):
        """进入「导入中」状态：用户点确认导入后，一个事件循环内就要看到这个。"""
        self._import_busy = True
        self._import_finalize_pending = False

        self.import_status_frame.setVisible(True)
        self.import_status_label.setText(message)
        if indeterminate:
            self.import_progress_bar.setRange(0, 0)  # 总量未知：忙碌态
        else:
            self.import_progress_bar.setRange(0, 100)
            self.import_progress_bar.setValue(0)

        self.btn_cancel_import.setVisible(True)
        self.btn_cancel_import.setEnabled(True)
        self._set_import_controls_enabled(False)

    def _set_import_progress(self, progress: int, message: str = None):
        """更新导入状态文字 / 进度；progress < 0 表示总量未知，切到忙碌态。"""
        if message is not None:
            self.import_status_label.setText(message)
        if progress is None or progress < 0:
            self.import_progress_bar.setRange(0, 0)
        else:
            self.import_progress_bar.setRange(0, 100)
            self.import_progress_bar.setValue(min(max(progress, 0), 100))

    def _end_import_ui(self, summary: str = None, cancelled: bool = False):
        """结束导入（成功/取消/失败都会走这里）：恢复控件，取消按钮收起。

        `summary` 有值时，把结果留在页面里（页内状态），不用弹窗打断操作；
        没有值（比如失败场景，调用方会另外弹错误框）时直接把状态条收起来。
        `cancelled` 为真时不把进度条拉满到 100%——取消是半途而废，不是
        「普通完成」，进度条应该停在中断时的真实进度上，不能误导成已完成。

        注意：这里不清空 self._active_import_thread —— 线程是否真的退出了
        由 QThread 原生 finished 信号决定（见 _on_import_thread_actually_finished），
        这里只是 UI 收尾，跟线程生命周期分开管理。
        """
        self._import_busy = False
        self._import_finalize_pending = False

        self.btn_cancel_import.setVisible(False)
        if summary:
            self.import_status_label.setText(summary)
            if not cancelled:
                self.import_progress_bar.setRange(0, 100)
                self.import_progress_bar.setValue(100)
            self.import_status_frame.setVisible(True)
        else:
            self.import_status_frame.setVisible(False)

        self.update_project_bar()

    def _cancel_active_import(self):
        """取消按钮：请求后台线程停止，最迟在下一个文件/帧边界生效。"""
        if self._active_import_thread is not None:
            self._active_import_thread.cancel()
            self.btn_cancel_import.setEnabled(False)
            self.import_status_label.setText("正在取消…")

    def _on_import_thread_actually_finished(self, thread):
        """QThread 原生 finished：线程真的退出了，这里才是唯一安全释放引用的地方。

        跟 result_ready（业务结果，run() 返回前手动 emit）分开：无论正常完成、
        失败、取消，还是切项目导致的作废，最终都会走到这里——只有这里能保证
        `thread.isRunning()` 已经是 False，不会出现线程还在跑就被 GC / deleteLater
        的情况。
        """
        if self._active_import_thread is thread:
            self._active_import_thread = None
        if thread in self._retired_import_threads:
            self._retired_import_threads.remove(thread)
        # finished 信号发出时线程已经在退出的路上，wait() 在这里只是确保万无
        # 一失（此时通常立即返回），之后再安全地交给 Qt 事件循环销毁对象。
        thread.wait()
        thread.deleteLater()

    def _start_data_import(self, kind: str, source, group_id,
                            frame_interval: int = 1, initial_message: str = "",
                            indeterminate: bool = True):
        """统一入口：文件夹 / 多图 / 视频导入都从这里起后台线程。"""
        if not self.current_project_id:
            return
        if self._import_busy:
            QMessageBox.information(self, "提示", "已有导入任务在进行，请稍候")
            return

        self._import_generation += 1
        generation = self._import_generation
        self._import_before_ids = {img.get('id') for img in self.images}

        self._start_import_ui(initial_message, indeterminate=indeterminate)

        thread = ImportWorkerThread(
            self.current_project_id, group_id, kind, source, frame_interval=frame_interval
        )
        thread.progress_updated.connect(
            lambda progress, message, g=generation: self._on_import_progress(g, progress, message)
        )
        thread.result_ready.connect(
            lambda success, error, cancelled, imported, skipped, g=generation, t=thread:
            self._on_import_worker_finished(g, t, success, error, cancelled, imported, skipped)
        )
        # QThread 原生 finished：只有它才代表线程真的退出了（result_ready 是我们
        # 自己在 run() 返回前手动 emit 的业务结果，不代表 OS 线程已经结束）。
        # 只有在这里才真正释放引用，避免 "Destroyed while thread is still running"。
        thread.finished.connect(lambda t=thread: self._on_import_thread_actually_finished(t))
        self._active_import_thread = thread
        thread.start()

    def _on_import_progress(self, generation: int, progress: int, message: str):
        if generation != self._import_generation:
            return
        self._set_import_progress(progress, message)

    def _on_import_worker_finished(self, generation: int, thread, success: bool, error: str,
                                    cancelled: bool, imported: int, skipped: int):
        # result_ready 是线程自己在 run() 返回前手动 emit 的，这一刻 run() 几乎
        # 已经跑完但严格意义上还没退出（QThread 原生 finished/isRunning() 变
        # False 要稍后才会发生）。同步 wait() 一下，把这个窗口关掉——此时线程
        # 本来就即将结束，等待成本可以忽略不计，但能确保调用方（包括后面可能
        # 立刻释放 self 的场景）拿到结果时线程已经真正退出，不会有人在这个
        # 窗口期把仍在运行的 QThread 销毁掉。
        thread.wait()

        if generation != self._import_generation:
            return

        if not success:
            self._end_import_ui()
            QMessageBox.critical(self, "导入失败", f"导入过程中发生错误:\n{error}")
            return

        prefix = "已取消" if cancelled else "导入完成"
        summary = f"{prefix}：成功导入 {imported} 张，跳过 {skipped} 张"

        if imported > 0:
            self._import_finalize_pending = True
            self._import_finalize_summary = summary
            self._import_finalize_cancelled = cancelled
            self._set_import_progress(-1, "正在整理缩略图…")
            appended = self._append_imported_images(
                self._import_before_ids,
                on_thumbnails_ready=lambda g=generation, s=summary, c=cancelled: self._finish_import_finalization(g, s, c),
            )
            if appended == 0:
                # 兜底：数据库里应该有新图但没识别出来，回退全量重载保证界面和数据库一致
                # （on_thumbnails_ready 在这条分支里已经同步跑过，导入态已经收尾了）
                self.load_project_images()
        else:
            self._end_import_ui(summary, cancelled=cancelled)

        self.refresh_view_filter_options()

    def _finish_import_finalization(self, generation: int, summary: str, cancelled: bool = False):
        """后台缩略图整理真正完成后才收尾——这时才算导入任务完全结束。"""
        if generation != self._import_generation:
            return
        self._end_import_ui(summary, cancelled=cancelled)

    def import_folder(self):
        """导入文件夹"""
        if not self.current_project_id:
            QMessageBox.warning(self, "提示", "请先选择或创建一个项目")
            return
        
        folder_path = QFileDialog.getExistingDirectory(
            self, "选择图像文件夹", "",
            QFileDialog.Option.ShowDirsOnly
        )
        
        if folder_path:
            proceed, group_id = ask_import_group(self, self.current_project_id)
            if not proceed:
                return
            self.process_folder_import(folder_path, group_id=group_id)
    
    def import_images(self):
        """导入单张或多张图片"""
        if not self.current_project_id:
            QMessageBox.warning(self, "提示", "请先选择或创建一个项目")
            return
        
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "",
            "图像文件 (*.jpg *.jpeg *.png *.bmp *.tiff *.webp);;所有文件 (*.*)"
        )
        
        if file_paths:
            proceed, group_id = ask_import_group(self, self.current_project_id)
            if not proceed:
                return
            self.process_image_import(file_paths, group_id=group_id)
    
    def import_video(self):
        """导入视频"""
        if not self.current_project_id:
            QMessageBox.warning(self, "提示", "请先选择或创建一个项目")
            return
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*.*)"
        )
        
        if file_path:
            proceed, group_id = ask_import_group(self, self.current_project_id)
            if not proceed:
                return
            self.process_video_import(file_path, group_id=group_id)
    
    def process_video_import(self, file_path: str, group_id: int = None):
        """处理视频导入：抽帧间隔问完，后台线程负责剩下的一切。"""
        if not self.current_project_id:
            return

        from PyQt6.QtWidgets import QInputDialog
        interval, ok = QInputDialog.getInt(
            self, "抽帧设置",
            "请输入抽帧间隔（每隔多少帧抽取一帧）:",
            value=30, min=1, max=1000
        )

        if not ok:
            return

        self._start_data_import(
            'video', file_path, group_id, frame_interval=interval,
            initial_message=f"正在打开视频: {Path(file_path).name}",
        )

    def import_annotations(self):
        """导入已有标注"""
        if not self.current_project_id:
            QMessageBox.warning(self, "提示", "请先选择或创建一个项目")
            return
        
        # 检查项目是否有任务标签
        project = db.get_project(self.current_project_id)
        if not project:
            QMessageBox.warning(self, "提示", "项目信息获取失败")
            return
        
        task_type = project.get('type')
        if not task_type or task_type not in ['detect', 'segment', 'pose', 'classify']:
            task_type = ask_task_type(self, current='detect', title="这个项目要做什么")
            if not task_type:
                return
            db.update_project(self.current_project_id, type=task_type)
            self.update_project_bar()

        # 选择标注格式
        from PyQt6.QtWidgets import QRadioButton

        dialog = QDialog(self)
        dialog.setWindowTitle("选择标注格式")
        dialog.setMinimumWidth(360)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        label = QLabel("你的标注文件是哪种格式？")
        label.setObjectName("subtitle")
        layout.addWidget(label)

        yolo_radio = QRadioButton("YOLO 格式")
        yolo_radio.setToolTip("一张图片配一个 .txt")
        yolo_radio.setChecked(True)
        layout.addWidget(yolo_radio)

        coco_radio = QRadioButton("COCO 格式")
        coco_radio.setToolTip("整个数据集一个 .json")
        layout.addWidget(coco_radio)

        voc_radio = QRadioButton("Pascal VOC 格式")
        voc_radio.setToolTip("一张图片配一个 .xml")
        layout.addWidget(voc_radio)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0)
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("primary")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        proceed, group_id = ask_import_group(self, self.current_project_id)
        if not proceed:
            return
        
        if yolo_radio.isChecked():
            self.import_yolo_annotations(group_id=group_id)
        elif coco_radio.isChecked():
            self.import_coco_annotations(group_id=group_id)
        elif voc_radio.isChecked():
            self.import_voc_annotations(group_id=group_id)
    
    def import_yolo_annotations(self, group_id: int = None):
        """导入YOLO标注"""
        labels_dir = QFileDialog.getExistingDirectory(
            self, "选择YOLO标签文件夹 (labels)", "",
            QFileDialog.Option.ShowDirsOnly
        )
        
        if not labels_dir:
            return
        
        reply = QMessageBox.question(
            self, "选择图像文件夹",
            "是否需要选择对应的图像文件夹？\n（如果标签文件和图像文件在同一目录，可选择否）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        images_dir = None
        if reply == QMessageBox.StandardButton.Yes:
            images_dir = QFileDialog.getExistingDirectory(
                self, "选择图像文件夹 (images)", "",
                QFileDialog.Option.ShowDirsOnly
            )
        
        # 检查项目是否已经有标注
        project_images = db.get_project_images(self.current_project_id)
        has_annotations = False
        for image in project_images:
            annotations = db.get_image_annotations(image['id'])
            if annotations:
                has_annotations = True
                break
        
        # 如果有标注，提示是否覆盖
        overwrite = False
        if has_annotations:
            reply = QMessageBox.question(
                self, "覆盖标注",
                "项目中已经存在标注，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                overwrite = True
        
        # 显示加载动画
        self.loading_overlay = LoadingOverlay(self, "正在导入YOLO标注...")
        self.loading_overlay.show_loading()
        
        # 创建后台线程来执行导入操作
        from PyQt6.QtCore import QThread, pyqtSignal
        
        class AnnotationImportThread(QThread):
            """标注导入线程"""
            
            finished = pyqtSignal(bool, str, int, int)
            
            def __init__(self, project_id, labels_dir, images_dir, overwrite, group_id=None):
                super().__init__()
                self.project_id = project_id
                self.labels_dir = labels_dir
                self.images_dir = images_dir
                self.overwrite = overwrite
                self.group_id = group_id
            
            def run(self):
                """运行导入"""
                try:
                    from core.annotation_importer import AnnotationImporter
                    importer = AnnotationImporter(self.project_id, group_id=self.group_id)
                    imported, skipped = importer.import_yolo_annotations(
                        self.labels_dir, self.images_dir, self.overwrite
                    )
                    self.finished.emit(True, "导入成功", imported, skipped)
                except Exception as e:
                    self.finished.emit(False, f"导入失败: {e}", 0, 0)
        
        # 创建并启动线程
        self.import_thread = AnnotationImportThread(
            self.current_project_id, labels_dir, images_dir, overwrite, group_id=group_id
        )
        self.import_thread.finished.connect(self.on_annotation_import_finished)
        self.import_thread.start()
    
    def on_annotation_import_finished(self, success, message, imported, skipped):
        """标注导入完成回调"""
        # 隐藏加载动画
        if hasattr(self, 'loading_overlay'):
            self.loading_overlay.hide_loading()
            self.loading_overlay.deleteLater()
            delattr(self, 'loading_overlay')
        
        # 重新加载项目图片
        self.load_project_images()
        self.refresh_view_filter_options()
        
        # 显示结果
        if success:
            QMessageBox.information(
                self, "导入完成",
                f"YOLO标注导入完成！\n成功导入: {imported} 个标注\n跳过: {skipped} 个"
            )
        else:
            QMessageBox.critical(self, "导入失败", message)
    
    def import_coco_annotations(self, group_id: int = None):
        """导入COCO标注"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择COCO标注文件", "",
            "JSON文件 (*.json);;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        # 检查项目是否已经有标注
        project_images = db.get_project_images(self.current_project_id)
        has_annotations = False
        for image in project_images:
            annotations = db.get_image_annotations(image['id'])
            if annotations:
                has_annotations = True
                break
        
        # 如果有标注，提示是否覆盖
        overwrite = False
        if has_annotations:
            reply = QMessageBox.question(
                self, "覆盖标注",
                "项目中已经存在标注，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                overwrite = True
        
        # 显示加载动画
        self.loading_overlay = LoadingOverlay(self, "正在导入COCO标注...")
        self.loading_overlay.show_loading()
        
        # 创建后台线程来执行导入操作
        from PyQt6.QtCore import QThread, pyqtSignal
        
        class AnnotationImportThread(QThread):
            """标注导入线程"""
            
            finished = pyqtSignal(bool, str, int, int)
            
            def __init__(self, project_id, file_path, overwrite, group_id=None):
                super().__init__()
                self.project_id = project_id
                self.file_path = file_path
                self.overwrite = overwrite
                self.group_id = group_id
            
            def run(self):
                """运行导入"""
                try:
                    from core.annotation_importer import AnnotationImporter
                    importer = AnnotationImporter(self.project_id, group_id=self.group_id)
                    imported, skipped = importer.import_coco_annotations(
                        self.file_path, self.overwrite
                    )
                    self.finished.emit(True, "导入成功", imported, skipped)
                except Exception as e:
                    self.finished.emit(False, f"导入失败: {e}", 0, 0)
        
        # 创建并启动线程
        self.import_thread = AnnotationImportThread(
            self.current_project_id, file_path, overwrite, group_id=group_id
        )
        self.import_thread.finished.connect(self.on_annotation_import_finished)
        self.import_thread.start()
    
    def import_voc_annotations(self, group_id: int = None):
        """导入VOC标注"""
        voc_dir = QFileDialog.getExistingDirectory(
            self, "选择VOC标注文件夹 (Annotations)", "",
            QFileDialog.Option.ShowDirsOnly
        )
        
        if not voc_dir:
            return
        
        # 检查项目是否已经有标注
        project_images = db.get_project_images(self.current_project_id)
        has_annotations = False
        for image in project_images:
            annotations = db.get_image_annotations(image['id'])
            if annotations:
                has_annotations = True
                break
        
        # 如果有标注，提示是否覆盖
        overwrite = False
        if has_annotations:
            reply = QMessageBox.question(
                self, "覆盖标注",
                "项目中已经存在标注，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                overwrite = True
        
        # 显示加载动画
        self.loading_overlay = LoadingOverlay(self, "正在导入VOC标注...")
        self.loading_overlay.show_loading()
        
        # 创建后台线程来执行导入操作
        from PyQt6.QtCore import QThread, pyqtSignal
        
        class AnnotationImportThread(QThread):
            """标注导入线程"""
            
            finished = pyqtSignal(bool, str, int, int)
            
            def __init__(self, project_id, voc_dir, overwrite, group_id=None):
                super().__init__()
                self.project_id = project_id
                self.voc_dir = voc_dir
                self.overwrite = overwrite
                self.group_id = group_id
            
            def run(self):
                """运行导入"""
                try:
                    from core.annotation_importer import AnnotationImporter
                    importer = AnnotationImporter(self.project_id, group_id=self.group_id)
                    imported, skipped = importer.import_voc_annotations(
                        self.voc_dir, self.overwrite
                    )
                    self.finished.emit(True, "导入成功", imported, skipped)
                except Exception as e:
                    self.finished.emit(False, f"导入失败: {e}", 0, 0)
        
        # 创建并启动线程
        self.import_thread = AnnotationImportThread(
            self.current_project_id, voc_dir, overwrite, group_id=group_id
        )
        self.import_thread.finished.connect(self.on_annotation_import_finished)
        self.import_thread.start()
    
    def process_folder_import(self, folder_path: str, group_id: int = None):
        """处理文件夹导入：扫描、复制、写库全部丢给后台线程。"""
        if not self.current_project_id:
            return

        folder_name = Path(folder_path).name or folder_path
        self._start_data_import(
            'folder', folder_path, group_id,
            initial_message=f"正在准备导入文件夹: {folder_name}",
        )

    def process_image_import(self, file_paths: List[str], group_id: int = None):
        """处理图像导入：复制、写库全部丢给后台线程。"""
        if not self.current_project_id:
            return

        total = len(file_paths)
        self._start_data_import(
            'images', file_paths, group_id,
            initial_message=f"正在导入 0/{total} 张图片",
            indeterminate=False,
        )

    def clear_all_images(self):
        """清空所有图像"""
        if not self.images:
            return
        
        reply = QMessageBox.question(
            self, "确认清空",
            f"确定要删除当前项目中的所有 {len(self.images)} 张图片吗？\n此操作不可恢复！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.stop_image_loading()
            deleted = 0
            failed = 0
            
            # 使用副本迭代，避免删除过程中修改原列表导致遍历异常
            images_snapshot = list(self.images)
            for image in images_snapshot:
                if db.delete_image(image['id']):
                    deleted += 1
                else:
                    failed += 1

            # 全部删除成功时，直接本地清空，避免触发整页重载
            if failed == 0:
                removed_storage_paths = [img.get('storage_path', '') for img in self.images]
                self.images.clear()
                self.image_list.clear()
                self.thumbnail_widgets.clear()
                self._remove_cached_thumbnails(removed_storage_paths)
                self.update_status_bar()
                self.update_view_mode()
            else:
                # 部分失败时回退到全量重载，确保UI与数据库一致
                self.load_project_images()
            
            if failed == 0:
                QMessageBox.information(self, "清空完成", f"已成功删除 {deleted} 张图片")
            else:
                QMessageBox.warning(self, "清空完成", f"成功删除 {deleted} 张，失败 {failed} 张")
    
    def move_selected_to_group(self):
        """将选中图片移动到指定分组"""
        selected_items = self.image_list.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先选择要移动的图片")
            return
        if not self.current_project_id:
            return

        dialog = GroupSelectDialog(
            self,
            self.current_project_id,
            title="移动分组",
            allow_ungroup=True,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.was_cancelled():
            return

        group_id = dialog.get_selected_group_id()
        image_ids = [item.data(Qt.ItemDataRole.UserRole) for item in selected_items]
        updated = db.assign_images_to_group(image_ids, group_id)

        for image_id in image_ids:
            image_data = next((img for img in self.images if img['id'] == image_id), None)
            if image_data:
                image_data['group_id'] = group_id

        group_label = "未分组"
        if group_id is not None:
            group = db.get_image_group(group_id)
            if group:
                group_label = group['name']

        for item in selected_items:
            image_id = item.data(Qt.ItemDataRole.UserRole)
            image_data = next((img for img in self.images if img['id'] == image_id), None)
            if image_data:
                item.setToolTip(
                    f"{image_data['filename']}\n"
                    f"{image_data.get('width', 0)}x{image_data.get('height', 0)}\n"
                    f"分组: {group_label}"
                )

        self.refresh_view_filter_options()
        self.filter_images(self.view_combo.currentText())

        QMessageBox.information(
            self, "移动完成",
            f"已将 {updated} 张图片移动到「{group_label}」"
        )

    def delete_selected_images(self):
        """删除选中的图片"""
        selected_items = self.image_list.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先选择要删除的图片")
            return
        
        count = len(selected_items)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的 {count} 张图片吗？\n此操作不可恢复！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.stop_image_loading()
        deleted = 0
        failed = 0
        deleted_ids = []
        
        for item in selected_items:
            image_id = item.data(Qt.ItemDataRole.UserRole)
            if db.delete_image(image_id):
                deleted += 1
                deleted_ids.append(image_id)
            else:
                failed += 1

        # 仅移除已成功删除的项，避免每次删除都整页重载
        if deleted_ids:
            deleted_id_set = set(deleted_ids)

            # 先更新内存数据
            removed_storage_paths = {
                img.get('storage_path', '')
                for img in self.images
                if img.get('id') in deleted_id_set
            }
            self.images = [img for img in self.images if img.get('id') not in deleted_id_set]

            # 清理缩略图缓存
            for path in removed_storage_paths:
                if path in self.thumbnail_cache:
                    del self.thumbnail_cache[path]

            # 再移除列表项（倒序删除避免索引变化）
            rows_to_remove = []
            for i in range(self.image_list.count()):
                item = self.image_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) in deleted_id_set:
                    rows_to_remove.append(i)
            for row in reversed(rows_to_remove):
                self.image_list.takeItem(row)

            self.update_status_bar()
            self.update_view_mode()

        if failed == 0:
            QMessageBox.information(self, "删除完成", f"已成功删除 {deleted} 张图片")
        else:
            QMessageBox.warning(self, "删除完成", f"成功删除 {deleted} 张，失败 {failed} 张")
