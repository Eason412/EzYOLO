# -*- coding: utf-8 -*-
import _bootstrap  # noqa: F401

from gui.styles import (
    COLORS,
    LIGHT_COLORS,
    DARK_COLORS,
    THEME_LIGHT,
    THEME_DARK,
    get_theme,
    set_theme,
    normalize_theme,
)


def test_normalize_theme():
    assert normalize_theme('dark') == THEME_DARK
    assert normalize_theme('暗色') == THEME_DARK
    assert normalize_theme('light') == THEME_LIGHT
    assert normalize_theme('') == THEME_LIGHT


def test_set_theme_updates_active_palette():
    set_theme(THEME_DARK)
    assert get_theme() == THEME_DARK
    assert COLORS['background'] == DARK_COLORS['background']

    set_theme(THEME_LIGHT)
    assert get_theme() == THEME_LIGHT
    assert COLORS['background'] == LIGHT_COLORS['background']


def test_dark_palette_has_core_tokens():
    for key in ('background', 'panel', 'text_primary', 'primary', 'border'):
        assert key in DARK_COLORS
        assert DARK_COLORS[key] != LIGHT_COLORS[key]
