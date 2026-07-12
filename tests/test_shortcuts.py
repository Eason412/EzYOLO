# -*- coding: utf-8 -*-
"""快捷键：设置页收得下的键，消费端就必须认得。

以前设置页会老老实实存下 DELETE / SPACE / 方向键并显示「已经生效」，
可消费端比的是 event.text()——这几个键根本没有可打印字符
（分别是 '\x7f'、' '、''），于是键是哑的，用户还以为改成功了。
现在两边都按键码比对。

组合键（Ctrl+S）明确拒绝：消费端是单键匹配，收下也用不了。

运行：
    python -m pytest tests/test_shortcuts.py -q
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys

from PyQt6.QtCore import Qt, QEvent, QSettings
from PyQt6.QtGui import QKeyEvent

from gui.pages.settings_page import (
    SHORTCUTS, event_matches_shortcut, normalize_shortcut, shortcut_key_code,
)

_app = _bootstrap.app()


def key_event(qt_key, text="", modifiers=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, qt_key, modifiers, text)


def test_named_keys_are_accepted():
    for name in ("DELETE", "SPACE", "UP", "DOWN", "LEFT", "RIGHT", "BACKSPACE"):
        assert normalize_shortcut(name) == name, name
        assert shortcut_key_code(name) is not None, name


def test_letters_and_digits_are_accepted():
    assert normalize_shortcut("w") == "W"
    assert normalize_shortcut("3") == "3"


def test_combos_are_rejected():
    """消费端是单键匹配，组合键不能收。"""
    for combo in ("Ctrl+S", "Alt+F4", "Shift+A"):
        assert normalize_shortcut(combo) == "", combo
        assert shortcut_key_code(combo) is None, combo


def test_garbage_is_rejected():
    for bad in ("", "   ", "NOPE", "不是键"):
        assert normalize_shortcut(bad) == ""
        assert shortcut_key_code(bad) is None


def test_named_keys_actually_match_their_events():
    """这就是原来那个 bug：这些键 event.text() 匹配不上，键码才匹配得上。"""
    cases = [
        ("DELETE", Qt.Key.Key_Delete, "\x7f"),
        ("SPACE", Qt.Key.Key_Space, " "),
        ("UP", Qt.Key.Key_Up, ""),
        ("LEFT", Qt.Key.Key_Left, ""),
        ("W", Qt.Key.Key_W, "w"),
        ("3", Qt.Key.Key_3, "3"),
    ]
    for stored, qt_key, text in cases:
        event = key_event(qt_key, text)
        assert event_matches_shortcut(event, stored), f"{stored} 匹配不上自己的按键事件"
        # 旧写法（拿 event.text() 比）对无字符的键必然失效
        if stored in ("DELETE", "SPACE", "UP", "LEFT"):
            assert event.text().upper() != stored


def test_shortcut_does_not_fire_with_ctrl_alt_meta():
    """单键快捷键不能被 Ctrl+W 这类组合键误触发（Ctrl+Z 撤销要还能用）。"""
    for modifier in (
        Qt.KeyboardModifier.ControlModifier,
        Qt.KeyboardModifier.AltModifier,
        Qt.KeyboardModifier.MetaModifier,
    ):
        event = key_event(Qt.Key.Key_W, "w", modifier)
        assert not event_matches_shortcut(event, "W")


def test_wrong_key_does_not_match():
    event = key_event(Qt.Key.Key_P, "p")
    assert not event_matches_shortcut(event, "W")


def test_defaults_all_resolve():
    """设置页列出来的默认键位，一个都不能是哑的。"""
    for setting_key, name, default in SHORTCUTS:
        assert shortcut_key_code(default) is not None, f"{name} 的默认键 {default} 解析不了"


def test_settings_are_isolated_from_real_user_settings():
    """测试用的 QSettings 必须落在临时目录里。"""
    settings = QSettings("EzYOLO", "Settings")
    assert str(_bootstrap.SETTINGS_DIR) in settings.fileName(), settings.fileName()
    assert settings.format() == QSettings.Format.IniFormat

    settings.setValue("delete_shortcut", "SPACE")
    assert QSettings("EzYOLO", "Settings").value("delete_shortcut") == "SPACE"


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(globals()))
