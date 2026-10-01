"""Qt stylesheet generator (spec §23).

One function turns a :class:`~app.ui.design.Theme` into the application-wide
QSS.  No widget embeds its own colours; this file is the only place that knows
how the theme maps onto Qt selectors.  That makes the light/dark switch and
accent changes instant and keeps the look coherent.
"""

from __future__ import annotations

from app.ui.design import FONT_FA, FONT_MONO, FONT_UI, RADIUS, SPACE, TYPE, Theme

#: Object names that receive a "card" treatment.  Pages mark their containers
#: with ``setObjectName("card")`` and get the layered look for free.
CARD_OBJECTS = ("card", "Card", "panel", "Panel")


def build_stylesheet(theme: Theme, *, rtl: bool = False) -> str:
    """Return the full application stylesheet for ``theme``."""
    p = theme.palette
    font_family = FONT_FA if rtl else FONT_UI
    s = SPACE
    r = RADIUS

    return f"""
/* =====================================================================
   SmartCooling Controller — generated stylesheet
   Base: {p.name}   Accent: {theme.accent}
   ===================================================================== */

* {{
    font-family: {font_family};
    font-size: {TYPE['body']}pt;
    outline: none;
}}

QWidget {{
    color: {p.text};
    background: transparent;
}}

QMainWindow, QDialog, #Root {{
    background: {p.bg};
}}

QToolTip {{
    background: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {r['sm']}px;
    padding: {s['sm']}px {s['md']}px;
    font-size: {TYPE['small']}pt;
}}

/* ---------------------------------------------------------------- text */

QLabel {{ background: transparent; }}
QLabel[role="display"] {{
    font-size: {TYPE['display']}pt;
    font-weight: 600;
    color: {p.text};
}}
QLabel[role="title"] {{
    font-size: {TYPE['title']}pt;
    font-weight: 600;
    color: {p.text};
}}
QLabel[role="heading"] {{
    font-size: {TYPE['heading']}pt;
    font-weight: 600;
    color: {p.text};
}}
QLabel[role="subheading"] {{
    font-size: {TYPE['subheading']}pt;
    font-weight: 600;
    color: {p.text_muted};
    letter-spacing: 0.4px;
}}
QLabel[role="caption"] {{
    font-size: {TYPE['caption']}pt;
    color: {p.text_faint};
    letter-spacing: 0.3px;
}}
QLabel[role="muted"] {{ color: {p.text_muted}; }}
QLabel[role="faint"] {{ color: {p.text_faint}; }}
QLabel[role="mono"] {{
    font-family: {FONT_MONO};
    font-size: {TYPE['mono']}pt;
}}
QLabel[role="accent"] {{ color: {p.primary}; }}

/* --------------------------------------------------------------- cards */

#card, #Card, #panel, #Panel, QFrame[card="true"] {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {r['lg']}px;
}}
QFrame[card="raised"] {{
    background: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: {r['lg']}px;
}}
QFrame[card="flat"] {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {r['md']}px;
}}

#Rail {{
    background: {p.surface};
    border-right: 1px solid {p.border};
}}
#Rail[side="right"] {{
    border-right: none;
    border-left: 1px solid {p.border};
}}
#TopBar {{
    background: {p.surface};
    border-bottom: 1px solid {p.border};
}}
#StatusBar {{
    background: {p.surface_alt};
    border-top: 1px solid {p.border};
}}

/* -------------------------------------------------------------- buttons */

QPushButton {{
    background: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {r['md']}px;
    padding: {s['sm']}px {s['lg']}px;
    font-size: {TYPE['body']}pt;
    font-weight: 500;
    min-height: 30px;
}}
QPushButton:hover {{
    background: {p.hover};
    border-color: {p.primary};
}}
QPushButton:pressed {{
    background: {p.primary_soft};
}}
QPushButton:disabled {{
    color: {p.text_faint};
    background: {p.surface_alt};
    border-color: {p.border};
}}
QPushButton[variant="primary"] {{
    background: {p.primary};
    color: {p.bg};
    border: 1px solid {p.primary};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: {p.primary_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {p.primary_pressed}; }}
QPushButton[variant="ghost"] {{
    background: transparent;
    border: 1px solid transparent;
    color: {p.text_muted};
}}
QPushButton[variant="ghost"]:hover {{
    background: {p.hover};
    color: {p.text};
}}
QPushButton[variant="danger"] {{
    background: transparent;
    color: {p.danger};
    border: 1px solid {p.danger};
}}
QPushButton[variant="danger"]:hover {{ background: rgba(248, 113, 113, 0.14); }}
QPushButton[variant="quiet"] {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    color: {p.text_muted};
}}
QPushButton:checked {{
    background: {p.primary_soft};
    border-color: {p.primary};
    color: {p.text};
}}
QPushButton[segmented="true"] {{
    border-radius: {r['sm']}px;
    padding: {s['xs']}px {s['md']}px;
    min-height: 26px;
}}

QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r['sm']}px;
    padding: {s['xs']}px;
    color: {p.text_muted};
}}
QToolButton:hover {{ background: {p.hover}; color: {p.text}; }}
QToolButton:checked {{ background: {p.primary_soft}; color: {p.primary}; }}

/* ----------------------------------------------------------- nav items */

#NavItem {{
    background: transparent;
    border: none;
    border-radius: {r['md']}px;
    color: {p.text_muted};
    padding: {s['sm']}px {s['md']}px;
    text-align: left;
    font-size: {TYPE['body']}pt;
    font-weight: 500;
    min-height: 34px;
}}
#NavItem:hover {{ background: {p.hover}; color: {p.text}; }}
#NavItem:checked {{
    background: {p.primary_soft};
    color: {p.primary};
    font-weight: 600;
}}
#NavSection {{
    color: {p.text_faint};
    font-size: {TYPE['caption']}pt;
    font-weight: 600;
    letter-spacing: 1.2px;
    padding: {s['md']}px {s['md']}px {s['xs']}px {s['md']}px;
}}

/* --------------------------------------------------------------- inputs */

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.surface_alt};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {r['md']}px;
    padding: {s['xs']}px {s['sm']}px;
    selection-background-color: {p.primary};
    selection-color: {p.bg};
    min-height: 28px;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {p.primary};
    background: {p.surface_raised};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {p.text_faint};
    background: {p.surface};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {p.text_muted};
    margin-right: {s['sm']}px;
}}
QComboBox QAbstractItemView {{
    background: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: {r['sm']}px;
    padding: {s['xs']}px;
    selection-background-color: {p.primary_soft};
    selection-color: {p.text};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}

/* ---------------------------------------------------------- check/radio */

QCheckBox, QRadioButton {{ spacing: {s['sm']}px; color: {p.text}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {p.border_strong};
    background: {p.surface_alt};
}}
QCheckBox::indicator {{ border-radius: {r['sm']}px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p.primary};
    border-color: {p.primary};
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {p.primary}; }}

/* ---------------------------------------------------------------- range */

QSlider::groove:horizontal {{
    height: 4px;
    background: {p.surface_raised};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {p.primary};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {p.text};
    border: 2px solid {p.primary};
    width: 14px; height: 14px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {p.primary}; }}

QProgressBar {{
    background: {p.surface_raised};
    border: none;
    border-radius: {r['sm']}px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {p.primary};
    border-radius: {r['sm']}px;
}}

/* ---------------------------------------------------------------- lists */

QListView, QTableView, QTreeView {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {r['md']}px;
    outline: none;
    alternate-background-color: {p.surface_alt};
}}
QListView::item, QTreeView::item {{ padding: {s['xs']}px; border-radius: {r['sm']}px; }}
QListView::item:hover, QTreeView::item:hover {{ background: {p.hover}; }}
QListView::item:selected, QTreeView::item:selected, QTableView::item:selected {{
    background: {p.primary_soft};
    color: {p.text};
}}
QHeaderView::section {{
    background: {p.surface_alt};
    color: {p.text_muted};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: {s['xs']}px {s['sm']}px;
    font-weight: 600;
    font-size: {TYPE['caption']}pt;
}}
QTableCornerButton::section {{ background: {p.surface_alt}; border: none; }}

/* ------------------------------------------------------------ scrollbar */

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong};
    border-radius: 5px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.primary}; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.border_strong};
    border-radius: 5px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.primary}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* --------------------------------------------------------------- tabs */

QTabWidget::pane {{
    border: 1px solid {p.border};
    border-radius: {r['md']}px;
    background: {p.surface};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {p.text_muted};
    border: none;
    padding: {s['sm']}px {s['lg']}px;
    margin-right: {s['xs']}px;
    font-weight: 500;
}}
QTabBar::tab:hover {{ color: {p.text}; }}
QTabBar::tab:selected {{
    color: {p.primary};
    border-bottom: 2px solid {p.primary};
    font-weight: 600;
}}

/* -------------------------------------------------------------- menus */

QMenu {{
    background: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: {r['md']}px;
    padding: {s['xs']}px;
}}
QMenu::item {{
    padding: {s['sm']}px {s['lg']}px;
    border-radius: {r['sm']}px;
    color: {p.text};
}}
QMenu::item:selected {{ background: {p.primary_soft}; color: {p.text}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: {s['xs']}px {s['sm']}px; }}

QMenuBar {{ background: {p.surface}; border-bottom: 1px solid {p.border}; }}
QMenuBar::item {{ padding: {s['xs']}px {s['md']}px; background: transparent; border-radius: {r['sm']}px; }}
QMenuBar::item:selected {{ background: {p.hover}; }}

/* ---------------------------------------------------------- groupbox */

QGroupBox {{
    background: transparent;
    border: 1px solid {p.border};
    border-radius: {r['lg']}px;
    margin-top: {s['md']}px;
    padding-top: {s['md']}px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: {s['md']}px;
    padding: 0 {s['xs']}px;
    color: {p.text_muted};
}}

/* ------------------------------------------------------------- splitter */

QSplitter::handle {{ background: {p.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* -------------------------------------------------------------- misc */

QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {p.text_muted}; font-size: {TYPE['caption']}pt; }}
QToolBar {{ background: {p.surface}; border: none; spacing: {s['xs']}px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QFrame[role="divider"] {{ background: {p.border}; max-height: 1px; border: none; }}
QFrame[role="vdivider"] {{ background: {p.border}; max-width: 1px; border: none; }}

#Badge {{
    border-radius: {r['pill']}px;
    padding: 2px {s['sm']}px;
    font-size: {TYPE['caption']}pt;
    font-weight: 600;
}}

#ChartToolbar QPushButton {{ min-height: 24px; padding: 2px {s['sm']}px; font-size: {TYPE['caption']}pt; }}

#CommandPalette {{
    background: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: {r['xl']}px;
}}
#PaletteInput {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {p.border};
    border-radius: 0;
    padding: {s['md']}px {s['lg']}px;
    font-size: {TYPE['heading']}pt;
}}

#SplashTitle {{ font-size: {TYPE['display']}pt; font-weight: 700; color: {p.text}; }}
#SplashSubtitle {{ font-size: {TYPE['subheading']}pt; color: {p.text_muted}; letter-spacing: 2px; }}
"""


__all__ = ["build_stylesheet", "CARD_OBJECTS"]
