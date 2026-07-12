# -*- coding: utf-8 -*-
"""测试沙箱：在 import 任何 gui.* 之前，把会碰真实数据的东西全部换掉。

每个测试文件的第一行都是 `import _bootstrap`。顺序是硬要求：
页面模块在 import 时就把 `db` 和 `QSettings` 绑到自己的模块名字上，晚了就换不掉。

挡住三样东西：
  QSettings   → 临时目录里的 ini 文件，不碰用户真实设置
  数据库      → 临时 .db，不碰 data/EzYOLO.db
  启动期同步  → 只替换临时数据库实例，避免测试扫描真实 projects/ 目录

QSettings 为什么要换掉整个类，而不是 setDefaultFormat + setPath：
macOS 上 NativeFormat 走的是 CFPreferences（cfprefsd），既不认 setPath，也不认
$HOME——QSettings.fileName() 会报一个临时路径，实际却仍然写进用户真实的
~/Library/Preferences/com.ezyolo.Settings.plist。只有强制 IniFormat + 显式文件路径
才真的隔离得掉。
"""

import os
import json
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(APP_ROOT))

SETTINGS_DIR = Path(tempfile.mkdtemp(prefix="ezyolo-settings-"))

import PyQt6.QtCore as _qtcore  # noqa: E402

_RealQSettings = _qtcore.QSettings


class _TempQSettings(_RealQSettings):
    """把 QSettings("EzYOLO", "X") 重定向到 <tmp>/EzYOLO-X.ini。"""

    def __init__(self, *args, **kwargs):
        name = "-".join(a for a in args if isinstance(a, str)) or "default"
        super().__init__(
            str(SETTINGS_DIR / f"{name}.ini"),
            _RealQSettings.Format.IniFormat,
        )


_qtcore.QSettings = _TempQSettings

# --- 数据库：必须在 gui.* 被 import 之前换掉 ---
from models.database import Database  # noqa: E402
import models.database as database_module  # noqa: E402

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
DB_PATH = _tmp_db.name
database_module.db = Database(db_path=DB_PATH)

# 真实实现现在是只读扫描，但界面测试仍不应读取真实 projects/。只替换这个
# 临时实例，不能修改 Database 类本身，否则同一 pytest 进程中的安全测试会被污染。
database_module.db.sync_files_with_database = lambda projects_dir=None: {
    'deleted_db_count': 0,
    'deleted_file_count': 0,
    'total_deleted': 0,
    'orphan_db_count': 0,
    'orphan_disk_count': 0,
    'issues': [],
    'has_issues': False,
}

db = database_module.db

# 测试项目必须连目录也落在临时区。Database.create_project() 的存储目录固定指向
# APP_ROOT/projects，即使数据库本身是临时的也会在真实项目区留下文件；测试被中止时
# atexit 来不及清理。统一从这里直写临时 DB，并把 storage_path 放进 /tmp。
TEMP_PROJECTS_DIR = Path(tempfile.mkdtemp(prefix="ezyolo-test-projects-"))


def create_temp_project(name="测试项目", project_type="detect", classes=None):
    storage = TEMP_PROJECTS_DIR / f"project-{uuid.uuid4().hex}"
    storage.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO projects (name, description, type, classes, storage_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, "", project_type, json.dumps(classes or [], ensure_ascii=False), str(storage)),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

# Database.create_project 总会在 APP_ROOT/projects 下 mkdir 一个真目录——临时数据库
# 拦不住它（路径是按 models/../projects 硬算的）。所以记下开跑前已有的目录，
# 退出时把测试新建的那些删掉，仓库里不留垃圾。只动「本进程新出现的」目录。
_PROJECTS_DIR = APP_ROOT / "projects"
_PREEXISTING_PROJECTS = (
    {p.name for p in _PROJECTS_DIR.iterdir()} if _PROJECTS_DIR.exists() else set()
)


def _cleanup_test_projects():
    import shutil
    shutil.rmtree(TEMP_PROJECTS_DIR, ignore_errors=True)
    if not _PROJECTS_DIR.exists():
        return
    for entry in _PROJECTS_DIR.iterdir():
        if entry.is_dir() and entry.name not in _PREEXISTING_PROJECTS:
            shutil.rmtree(entry, ignore_errors=True)


import atexit  # noqa: E402
atexit.register(_cleanup_test_projects)


def app():
    """拿到（必要时创建）离屏 QApplication。"""
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def run_module_tests(namespace) -> int:
    """支持 `python tests/test_xxx.py` 直接跑，不依赖 pytest。"""
    failures = 0
    for name, func in sorted(namespace.items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    print(f"\n{'all passed' if not failures else f'{failures} failed'}")
    return 1 if failures else 0
