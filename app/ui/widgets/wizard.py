"""First-run welcome wizard (spec §53, §54, §240).

Three short pages, no more: where the data lives, how the app looks, and which
language to speak.  Everything chosen here is written straight to the config so
the main window can be built from a fully-formed :class:`AppConfig`.

The wizard is deliberately non-blocking for the rest of the app: it returns the
patched sections and the caller decides when to persist them, which keeps the
"portable vs standard" choice testable without touching the disk.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.enums import AnimationLevel, DataMode, Language, ThemeMode
from app.ui.design import RADIUS, SPACE
from app.ui.i18n import Translator
from app.ui.icons import make_icon
from app.ui.widgets.atoms import Card
from app.ui.widgets.forms import FormRow, SettingsCombo, Switch


class WelcomeWizard(QWidget):
    """A modal-looking, frameless onboarding dialog.

    Emits :attr:`finished` with the configuration patch when the user completes
    or skips the flow.
    """

    finished = Signal(dict)

    def __init__(self, theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WelcomeWizard")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.theme = theme
        self.tr_ = translator
        self.setMinimumSize(680, 520)
        self.resize(720, 560)

        self._mode = DataMode.PORTABLE
        self._theme = ThemeMode.DARK
        self._accent = "cyan"
        self._animation = AnimationLevel.NORMAL
        self._language = Language.ENGLISH

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])

        self.shell = Card(variant="raised")
        outer.addWidget(self.shell)
        body = QVBoxLayout(self.shell)
        body.setContentsMargins(SPACE["xxl"], SPACE["xxl"], SPACE["xxl"], SPACE["xxl"])
        body.setSpacing(SPACE["lg"])

        header = QHBoxLayout()
        header.setSpacing(SPACE["md"])
        brand = QLabel()
        brand.setPixmap(make_icon("fan", theme.color("primary"), 40).pixmap(40, 40))
        header.addWidget(brand)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = QLabel()
        self.title.setProperty("role", "title")
        titles.addWidget(self.title)
        self.subtitle = QLabel()
        self.subtitle.setProperty("role", "caption")
        self.subtitle.setWordWrap(True)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        body.addLayout(header)

        self.steps = QStackedWidget()
        self.steps.addWidget(self._build_location_step())
        self.steps.addWidget(self._build_look_step())
        self.steps.addWidget(self._build_language_step())
        body.addWidget(self.steps, 1)

        self.dots = QLabel()
        self.dots.setProperty("role", "caption")
        self.dots.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.dots)

        footer = QHBoxLayout()
        footer.setSpacing(SPACE["sm"])
        self.skip_button = QPushButton()
        self.skip_button.setProperty("variant", "quiet")
        self.skip_button.clicked.connect(self._skip)
        footer.addWidget(self.skip_button)
        footer.addStretch(1)
        self.back_button = QPushButton()
        self.back_button.setProperty("variant", "ghost")
        self.back_button.clicked.connect(self._back)
        footer.addWidget(self.back_button)
        self.next_button = QPushButton()
        self.next_button.setProperty("variant", "primary")
        self.next_button.clicked.connect(self._next)
        footer.addWidget(self.next_button)
        body.addLayout(footer)

        self.retranslate()
        self.apply_theme()

    # -- steps -------------------------------------------------------------

    def _build_location_step(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])
        self.step1_title = QLabel()
        self.step1_title.setProperty("role", "heading")
        layout.addWidget(self.step1_title)
        self.step1_body = QLabel()
        self.step1_body.setProperty("role", "caption")
        self.step1_body.setWordWrap(True)
        layout.addWidget(self.step1_body)

        card = Card()
        inner = QVBoxLayout(card)
        inner.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        self.mode_combo = SettingsCombo(
            [
                (self.tr_.t("welcome.portable"), DataMode.PORTABLE),
                (self.tr_.t("welcome.standard"), DataMode.STANDARD),
            ],
            current=DataMode.PORTABLE,
        )
        self.mode_combo.changed.connect(self._on_mode)
        inner.addWidget(self.mode_combo)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_look_step(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])
        self.step2_title = QLabel()
        self.step2_title.setProperty("role", "heading")
        layout.addWidget(self.step2_title)
        self.step2_body = QLabel()
        self.step2_body.setProperty("role", "caption")
        self.step2_body.setWordWrap(True)
        layout.addWidget(self.step2_body)

        card = Card()
        inner = QVBoxLayout(card)
        inner.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        inner.setSpacing(SPACE["sm"])
        self.theme_combo = SettingsCombo(
            [(self.tr_.t("settings.theme.dark"), ThemeMode.DARK),
             (self.tr_.t("settings.theme.light"), ThemeMode.LIGHT)],
            current=ThemeMode.DARK,
        )
        self.theme_combo.changed.connect(self._on_theme)
        self._theme_row = FormRow(self.theme, self.tr_, self.tr_.t("settings.theme"), self.theme_combo)
        inner.addWidget(self._theme_row)

        self.animation_switch = Switch(self.theme, checked=True)
        self.animation_switch.toggled.connect(self._on_animation)
        self.animation_row = FormRow(
            self.theme, self.tr_, self.tr_.t("settings.animations"), self.animation_switch
        )
        inner.addWidget(self.animation_row)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_language_step(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])
        self.step3_title = QLabel()
        self.step3_title.setProperty("role", "heading")
        layout.addWidget(self.step3_title)
        self.step3_body = QLabel()
        self.step3_body.setProperty("role", "caption")
        self.step3_body.setWordWrap(True)
        layout.addWidget(self.step3_body)

        card = Card()
        inner = QVBoxLayout(card)
        inner.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        self.language_combo = SettingsCombo(
            [("English", Language.ENGLISH), ("فارسی", Language.PERSIAN)],
            current=Language.ENGLISH,
        )
        self.language_combo.changed.connect(self._on_language)
        inner.addWidget(self.language_combo)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    # -- state -------------------------------------------------------------

    def _on_mode(self, value) -> None:
        self._mode = value

    def _on_theme(self, value) -> None:
        self._theme = value

    def _on_animation(self, checked: bool) -> None:
        self._animation = AnimationLevel.NORMAL if checked else AnimationLevel.OFF

    def _on_language(self, value) -> None:
        self._language = value
        self.tr_.set_language(value)
        self.retranslate()

    def _patch(self) -> dict:
        return {
            "data": {"mode": self._mode},
            "appearance": {
                "theme": self._theme,
                "accent": self._accent,
                "animation_level": self._animation,
            },
            "localization": {"language": self._language},
            "general": {"first_run_completed": True},
        }

    # -- navigation --------------------------------------------------------

    def _index(self) -> int:
        return self.steps.currentIndex()

    def _next(self) -> None:
        if self._index() >= self.steps.count() - 1:
            self._emit_finished()
            return
        self.steps.setCurrentIndex(self._index() + 1)
        self._sync()

    def _back(self) -> None:
        if self._index() == 0:
            return
        self.steps.setCurrentIndex(self._index() - 1)
        self._sync()

    def _skip(self) -> None:
        self._emit_finished()

    def _emit_finished(self) -> None:
        self.finished.emit(self._patch())
        self.close()

    def _sync(self) -> None:
        index = self._index()
        self.back_button.setEnabled(index > 0)
        self.next_button.setText(
            self.tr_.t("welcome.finish") if index == self.steps.count() - 1
            else self.tr_.t("welcome.next")
        )
        self.dots.setText("  ".join("●" if i == index else "○" for i in range(self.steps.count())))

    # -- theming / i18n ----------------------------------------------------

    def retranslate(self) -> None:
        self.title.setText(self.tr_.t("welcome.title"))
        self.subtitle.setText(self.tr_.t("welcome.subtitle"))
        self.step1_title.setText(self.tr_.t("welcome.step1.title"))
        self.step1_body.setText(self.tr_.t("welcome.step1.body"))
        self.step2_title.setText(self.tr_.t("welcome.step2.title"))
        self.step2_body.setText(self.tr_.t("welcome.step2.body"))
        self.step3_title.setText(self.tr_.t("welcome.step3.title"))
        self.step3_body.setText(self.tr_.t("welcome.step3.body"))
        self.skip_button.setText(self.tr_.t("welcome.skip"))
        self.back_button.setText(self.tr_.t("welcome.back"))
        self._sync()

    def apply_theme(self) -> None:
        palette = self.theme.palette
        self.shell.setStyleSheet(
            f"""
            #WelcomeWizard Card[card="raised"] {{
                background: {palette.surface_raised};
                border: 1px solid {palette.border_strong};
                border-radius: {RADIUS['xl']}px;
            }}
            """
        )

    def set_theme(self, theme) -> None:
        self.theme = theme
        self.animation_switch.set_theme(theme)
        self.apply_theme()


__all__ = ["WelcomeWizard"]
