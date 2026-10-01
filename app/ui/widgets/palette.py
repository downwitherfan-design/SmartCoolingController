"""Command palette and global search (spec §83, §84).

``Ctrl+K`` opens a fuzzy-searchable list of pages, commands, sensors and
profiles.  Ranking favours: exact prefix > word-boundary match > substring,
with a small bonus for pages so navigation feels immediate.

The palette is presentation-only: it emits :attr:`CommandPalette.invoked` with
the chosen entry's id and the owning window performs the action.  That keeps
command wiring in one place and makes the palette reusable for global search.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.design import SPACE, Theme
from app.ui.i18n import Translator
from app.ui.icons import make_icon


@dataclass(slots=True)
class Command:
    """One palette entry."""

    id: str
    label: str
    category: str
    icon: str = "chevron_right"
    keywords: tuple[str, ...] = ()
    hint: str = ""


@dataclass(slots=True)
class _Scored:
    command: Command
    score: int
    field: str = ""


def _fuzzy_score(needle: str, haystack: str) -> int:
    """Return a relevance score; higher is better, 0 means no match."""
    if not needle:
        return 1
    needle = needle.lower()
    haystack_lower = haystack.lower()
    if haystack_lower == needle:
        return 1000
    if haystack_lower.startswith(needle):
        return 800 - len(haystack)
    index = haystack_lower.find(needle)
    if index >= 0:
        boundary = index == 0 or not haystack_lower[index - 1].isalnum()
        return (600 if boundary else 400) - index
    # Subsequence match (e.g. "fctl" → "Fan Control").
    position = 0
    for char in needle:
        position = haystack_lower.find(char, position)
        if position < 0:
            return 0
        position += 1
    return 120


class CommandPalette(QFrame):
    """Modal-ish floating palette."""

    invoked = Signal(str)
    closed = Signal()

    def __init__(self, theme: Theme, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.tr_ = translator
        self._commands: list[Command] = []
        self._results: list[_Scored] = []

        self.setObjectName("CommandPalette")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.input = QLineEdit()
        self.input.setObjectName("PaletteInput")
        self.input.setPlaceholderText(translator.t("palette.placeholder"))
        self.input.textChanged.connect(self._refresh)
        self.input.installEventFilter(self)
        layout.addWidget(self.input)

        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.itemActivated.connect(self._activate)
        self.list.itemClicked.connect(self._activate)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.list, 1)

        self.hint_label = QLabel("")
        self.hint_label.setProperty("role", "caption")
        self.hint_label.setContentsMargins(SPACE["lg"], SPACE["sm"], SPACE["lg"], SPACE["sm"])
        layout.addWidget(self.hint_label)

        self.setMinimumWidth(520)
        self.resize(560, 420)

    # -- api ---------------------------------------------------------------

    def set_commands(self, commands: list[Command]) -> None:
        self._commands = commands
        self._refresh(self.input.text())

    def set_placeholder(self, text: str) -> None:
        self.input.setPlaceholderText(text)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self._refresh(self.input.text())

    def open(self) -> None:
        self.input.clear()
        self._refresh("")
        self.show()
        self.input.setFocus(Qt.FocusReason.PopupFocusReason)

    # -- search ------------------------------------------------------------

    def _refresh(self, text: str) -> None:
        text = text.strip()
        scored: list[_Scored] = []
        for command in self._commands:
            best = _fuzzy_score(text, command.label)
            field = command.label
            for keyword in command.keywords:
                score = _fuzzy_score(text, keyword)
                if score > best:
                    best = score
                    field = keyword
            if best > 0:
                if command.category == "page":
                    best += 25
                scored.append(_Scored(command, best, field))
        scored.sort(key=lambda item: (-item.score, item.command.label))
        self._results = scored[:60]
        self._populate(text)

    def _populate(self, query: str) -> None:
        self.list.clear()
        if not self._results:
            item = QListWidgetItem(self.tr_.t("palette.empty"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            self.hint_label.setText("")
            return
        for scored in self._results:
            command = scored.command
            item = QListWidgetItem(f"{command.label}")
            item.setData(Qt.ItemDataRole.UserRole, command.id)
            icon = make_icon(command.icon, self.theme.color("text_muted"), 16)
            item.setIcon(icon)
            tooltip = command.hint or command.category
            item.setToolTip(tooltip)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.hint_label.setText(f"{len(self._results)} · {query}" if query else f"{len(self._results)}")

    def _activate(self, item: QListWidgetItem) -> None:
        command_id = item.data(Qt.ItemDataRole.UserRole)
        if command_id:
            self.invoked.emit(command_id)
        self.close()

    # -- events ------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if (
            obj is self.input
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
        ):
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                delta = 1 if event.key() == Qt.Key.Key_Down else -1
                row = self.list.currentRow()
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row + delta)))
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list.currentItem()
                if item is not None:
                    self._activate(item)
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)


def default_commands(translator: Translator, *, pages: list[tuple[str, str, str]] | None = None) -> list[Command]:
    """Build the standard page + command catalogue (spec §84)."""
    t = translator.t
    commands: list[Command] = []
    for key, label_key, icon in pages or _DEFAULT_PAGES:
        commands.append(
            Command(
                id=f"page:{key}",
                label=t(label_key),
                category="page",
                icon=icon,
                keywords=(key,),
            )
        )
    commands.extend(
        [
            Command("action:start_session", t("sessions.start"), "command", "play", ("session", "record")),
            Command("action:stop_session", t("sessions.stop"), "command", "stop", ("session",)),
            Command("action:profile:silent", t("fan.profile") + " · Silent", "command", "fan", ("silent", "quiet")),
            Command("action:profile:balanced", t("fan.profile") + " · Balanced", "command", "fan", ("balanced",)),
            Command("action:profile:performance", t("fan.profile") + " · Performance", "command", "fan", ("performance",)),
            Command("action:profile:turbo", t("fan.profile") + " · Turbo", "command", "fan", ("turbo", "max")),
            Command("action:reconnect", t("common.reconnect"), "command", "refresh", ("esp32", "link")),
            Command("action:export", t("common.export"), "command", "export", ("csv", "json", "save")),
            Command("action:diagnostics", t("diag.title"), "command", "diagnostics", ("support", "bundle")),
            Command("action:compact", t("shortcut.compact"), "command", "grid", ("mini", "small")),
            Command("action:pause", t("tray.pause"), "command", "pause", ("stop", "telemetry")),
            Command("action:theme", t("settings.theme"), "command", "moon", ("dark", "light")),
        ]
    )
    return commands


_DEFAULT_PAGES = [
    ("dashboard", "nav.dashboard", "dashboard"),
    ("telemetry", "nav.telemetry", "telemetry"),
    ("fan", "nav.fan", "fan"),
    ("diagram", "nav.diagram", "diagram"),
    ("history", "nav.history", "history"),
    ("sessions", "nav.sessions", "sessions"),
    ("esp32", "nav.esp32", "esp32"),
    ("sensors", "nav.sensors", "sensors"),
    ("alerts", "nav.alerts", "alerts"),
    ("diagnostics", "nav.diagnostics", "diagnostics"),
    ("settings", "nav.settings", "settings"),
    ("about", "nav.about", "about"),
]


__all__ = ["Command", "CommandPalette", "default_commands"]
