#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EzYOLO - 本地YOLO全流程训练软件
主程序入口
"""

import sys
import os
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, qInstallMessageHandler, QtMsgType
from PyQt6.QtGui import QIcon


def qt_message_handler(msg_type, context, message):
    """自定义Qt消息处理器，过滤QFont警告"""
    # 过滤掉QFont::setPointSize的警告
    msg_str = str(message).strip()
    if "QFont::setPointSize" in msg_str and "Point size <= 0" in msg_str:
        return  # 忽略这个警告
    
    # 其他消息正常输出到 stderr（Qt 的默认行为）
    if msg_type in (
        QtMsgType.QtWarningMsg,
        QtMsgType.QtCriticalMsg,
        QtMsgType.QtFatalMsg,
    ):
        print(msg_str, file=sys.stderr)


def main() -> int:
    """主函数"""
    # 安装自定义消息处理器，屏蔽QFont警告
    qInstallMessageHandler(qt_message_handler)
    
    # 启用高DPI支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    
    # 获取应用根目录
    app_root = Path(__file__).parent
    
    # 创建应用
    app = QApplication(sys.argv)
    app.setOrganizationName("EzYOLO")
    app.setApplicationName("EzYOLO")
    app.setApplicationVersion("1.0.0")

    # 必须先解析路径并取得单实例租约，再打开、复制或迁移任何用户数据库。
    from core.app_paths import (
        configure_runtime_paths,
        current_runtime_paths,
        prepare_runtime_directories,
    )
    from core.instance_lease import ApplicationInstanceLease
    from core.storage_migration import StorageMigrationError, prepare_database

    try:
        runtime_paths = current_runtime_paths(app_root)
    except Exception as exc:
        print(f"EzYOLO 无法确定安全的用户数据目录: {exc}", file=sys.stderr)
        return 1

    lease = ApplicationInstanceLease(runtime_paths.app.data_root / "EzYOLO.lock")
    if not lease.try_acquire():
        print("EzYOLO 已在另一个窗口中运行；本次没有打开或迁移数据库。")
        return 0

    try:
        prepare_runtime_directories(runtime_paths)
        preparation = prepare_database(
            legacy_database=app_root / "data" / "EzYOLO.db",
            target_database=runtime_paths.app.database_file,
            backup_root=runtime_paths.app.database_backups_root,
        )
        from models.database import Database, configure_database

        configure_database(
            Database(
                db_path=str(preparation.database_file),
                projects_root=runtime_paths.workspace.projects_root,
            )
        )
        configure_runtime_paths(runtime_paths)

        # GUI 模块会绑定 db 和运行路径，必须在上述装配完成后再导入。
        from gui.main_window import MainWindow
        from gui.styles import get_primary_font_family

        # 按当前系统真实存在的字体设置界面字体。
        font_family = get_primary_font_family()
        if font_family:
            app_font = app.font()
            app_font.setFamily(font_family)
            app.setFont(app_font)

        icon_path = app_root / "icon.png"
        if icon_path.exists():
            app_icon = QIcon(str(icon_path))
            app.setWindowIcon(app_icon)
    
        window = MainWindow()
    
        if 'app_icon' in locals():
            window.setWindowIcon(app_icon)
    
        window.show()
    
        return app.exec()
    except StorageMigrationError as exc:
        print(f"EzYOLO 为保护旧数据已停止启动: {exc}", file=sys.stderr)
        return 1
    finally:
        lease.release()


if __name__ == "__main__":
    raise SystemExit(main())
