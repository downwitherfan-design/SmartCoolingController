"""Design system for SmartCooling Controller (spec §22, §23, §82, §130).

This module is the single source of truth for every visual token.  Pages never
hard-code a colour, radius or font: they ask the theme.  That keeps the look
consistent and makes theme/accent switching a one-line change.

Colour psychology behind the palette
------------------------------------
* **Graphite base** (``#0B0E13``) — a near-black neutral.  Dark surfaces reduce
  glare during long monitoring sessions and let the accent hues read as
  signal rather than decoration.
* **Deep navy surfaces** (``#121826`` / ``#1A2333``) — cooler than neutral grey;
  blue reads as *trustworthy instrument* and recedes behind data.
* **Cyan primary** (``#22D3EE``) — "cooling".  Cyan is the hue of ice and
  precision instruments, so the primary accent reinforces the product's job.
* **Violet secondary** (``#A78BFA``) — reserved for *derived/estimated* values,
  a deliberate "this is computed, not measured" cue (spec §108).
* **Amber / red** — caution and critical only.  Amber warns without shouting;
  red is scarce so it keeps its alarm value.

Accessibility: thermal state is never communicated by colour alone — every
badge also carries a label and a shape/glyph (spec §29, §130).  The palette
targets WCAG AA contrast for text on its intended surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.core.enums import (
    AlertSeverity,
    AnimationLevel,
    BackendStatus,
    ConnectionState,
    FanResponseState,
    FanState,
    HealthStatus,
    MetricState,
    PerformanceMode,
    ThemeMode,
    ThermalState,
)

# --------------------------------------------------------------------------
# Scales
# --------------------------------------------------------------------------

#: 4-px based spacing scale — generous whitespace is a premium signal (spec §22).
SPACE = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32, "xxxl": 48}

#: Corner radii — soft but not pill-shaped; pill shapes are reserved for badges.
RADIUS = {"sm": 6, "md": 10, "lg": 14, "xl": 20, "pill": 999}

#: Type scale in points (Qt uses points for fonts).
TYPE = {
    "display": 34,
    "title": 22,
    "heading": 17,
    "subheading": 14,
    "body": 13,
    "small": 12,
    "caption": 11,
    "mono": 13,
}

#: Font families.  Windows-first with graceful fallbacks; the Persian stack is
#: chosen for correct Arabic-script shaping (spec §82).  All are freely
#: redistributable or ship with Windows.
FONT_UI = '"Segoe UI Variable", "Segoe UI", Inter, "Noto Sans", sans-serif'
FONT_FA = '"Vazirmatn", "Segoe UI", "Tahoma", "Noto Naskh Arabic", sans-serif'
FONT_MONO = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace'


@dataclass(frozen=True, slots=True)
class Palette:
    """A complete colour set for one theme."""

    name: str
    # Base surfaces, from furthest back to closest to the user.
    bg: str
    surface: str
    surface_alt: str
    surface_raised: str
    # Lines and separators.
    border: str
    border_strong: str
    # Text.
    text: str
    text_muted: str
    text_faint: str
    # Brand accents.
    primary: str
    primary_hover: str
    primary_pressed: str
    primary_soft: str
    secondary: str
    secondary_soft: str
    warm: str
    # Semantic.
    success: str
    warning: str
    danger: str
    info: str
    # Chart series (order matters: first series is the most important metric).
    series: tuple[str, ...]
    #: Whether text-on-surface is dark (light theme) — drives icon tints.
    is_light: bool = False
    #: Subtle overlay used for hover rows and selection.
    hover: str = ""
    selection: str = ""

    def with_accent(self, primary: str, primary_hover: str, primary_pressed: str, soft: str) -> Palette:
        return replace(
            self,
            primary=primary,
            primary_hover=primary_hover,
            primary_pressed=primary_pressed,
            primary_soft=soft,
        )


#: Named accent colours users can pick (spec §23).  Each entry is
#: ``(base, hover, pressed, soft)``.
ACCENTS: dict[str, tuple[str, str, str, str]] = {
    "cyan": ("#22D3EE", "#38DDF5", "#0FB6D0", "rgba(34, 211, 238, 0.14)"),
    "azure": ("#3B82F6", "#5B9BFF", "#2563EB", "rgba(59, 130, 246, 0.15)"),
    "violet": ("#A78BFA", "#BBA6FF", "#8B6FE8", "rgba(167, 139, 250, 0.15)"),
    "emerald": ("#34D399", "#4FE0AC", "#22B57F", "rgba(52, 211, 153, 0.14)"),
    "amber": ("#FBBF24", "#FCCB4D", "#E0A613", "rgba(251, 191, 36, 0.15)"),
    "rose": ("#FB7185", "#FC8A9B", "#E75A70", "rgba(251, 113, 133, 0.15)"),
}

DARK = Palette(
    name="dark",
    bg="#0B0E13",
    surface="#121826",
    surface_alt="#161D2C",
    surface_raised="#1A2333",
    border="rgba(148, 163, 184, 0.13)",
    border_strong="rgba(148, 163, 184, 0.26)",
    text="#E8EDF6",
    text_muted="#9FB0C9",
    text_faint="#63748F",
    primary="#22D3EE",
    primary_hover="#38DDF5",
    primary_pressed="#0FB6D0",
    primary_soft="rgba(34, 211, 238, 0.14)",
    secondary="#A78BFA",
    secondary_soft="rgba(167, 139, 250, 0.14)",
    warm="#FBBF24",
    success="#34D399",
    warning="#FBBF24",
    danger="#F87171",
    info="#60A5FA",
    series=(
        "#22D3EE",
        "#A78BFA",
        "#FBBF24",
        "#34D399",
        "#FB7185",
        "#60A5FA",
        "#F472B6",
        "#94A3B8",
    ),
    is_light=False,
    hover="rgba(148, 163, 184, 0.08)",
    selection="rgba(34, 211, 238, 0.16)",
)

LIGHT = Palette(
    name="light",
    bg="#F4F6FB",
    surface="#FFFFFF",
    surface_alt="#FAFBFE",
    surface_raised="#FFFFFF",
    border="rgba(15, 23, 42, 0.10)",
    border_strong="rgba(15, 23, 42, 0.20)",
    text="#0F172A",
    text_muted="#516074",
    text_faint="#8A97A8",
    primary="#0891B2",
    primary_hover="#0AA3C7",
    primary_pressed="#077A96",
    primary_soft="rgba(8, 145, 178, 0.12)",
    secondary="#7C3AED",
    secondary_soft="rgba(124, 58, 237, 0.10)",
    warm="#D97706",
    success="#059669",
    warning="#D97706",
    danger="#DC2626",
    info="#2563EB",
    series=(
        "#0891B2",
        "#7C3AED",
        "#D97706",
        "#059669",
        "#DC2626",
        "#2563EB",
        "#DB2777",
        "#64748B",
    ),
    is_light=True,
    hover="rgba(15, 23, 42, 0.05)",
    selection="rgba(8, 145, 178, 0.14)",
)


@dataclass(frozen=True, slots=True)
class Theme:
    """Resolved theme: a palette plus the user's accent and motion choices."""

    palette: Palette
    accent: str = "cyan"
    animation: AnimationLevel = AnimationLevel.NORMAL
    show_glow: bool = True
    reduced: bool = False
    #: Lightweight performance mode: trade rendering fidelity for throughput
    #: (no antialiasing, downsampled + clipped traces) on older hardware.
    lightweight: bool = False
    _thermal: dict[ThermalState, str] = field(default_factory=dict, repr=False)

    # -- colour helpers ----------------------------------------------------

    def color(self, token: str) -> str:
        """Resolve a semantic token name to a hex/rgba string."""
        mapping = {
            "bg": self.palette.bg,
            "surface": self.palette.surface,
            "surface_alt": self.palette.surface_alt,
            "surface_raised": self.palette.surface_raised,
            "border": self.palette.border,
            "border_strong": self.palette.border_strong,
            "text": self.palette.text,
            "text_muted": self.palette.text_muted,
            "text_faint": self.palette.text_faint,
            "primary": self.palette.primary,
            "secondary": self.palette.secondary,
            "warm": self.palette.warm,
            "success": self.palette.success,
            "warning": self.palette.warning,
            "danger": self.palette.danger,
            "info": self.palette.info,
        }
        return mapping.get(token, self.palette.text)

    def series_color(self, index: int) -> str:
        series = self.palette.series
        return series[index % len(series)]

    def thermal_color(self, state: ThermalState) -> str:
        """Sequential cool→critical ramp for a thermal state."""
        return {
            ThermalState.COOL: self.palette.info,
            ThermalState.NORMAL: self.palette.success,
            ThermalState.WARM: self.palette.warm,
            ThermalState.HIGH: "#FB923C",
            ThermalState.CRITICAL: self.palette.danger,
        }[state]

    def severity_color(self, severity: AlertSeverity) -> str:
        return {
            AlertSeverity.INFORMATION: self.palette.info,
            AlertSeverity.WARNING: self.palette.warning,
            AlertSeverity.CRITICAL: self.palette.danger,
        }[severity]

    def health_color(self, status: HealthStatus) -> str:
        return {
            HealthStatus.HEALTHY: self.palette.success,
            HealthStatus.WARNING: self.palette.warning,
            HealthStatus.ERROR: self.palette.danger,
            HealthStatus.UNKNOWN: self.palette.text_faint,
        }[status]

    def connection_color(self, state: ConnectionState) -> str:
        if state is ConnectionState.CONNECTED:
            return self.palette.success
        if state in (ConnectionState.CONNECTING, ConnectionState.PAIRING):
            return self.palette.warning
        if state in (ConnectionState.DEGRADED, ConnectionState.STALE):
            return self.palette.warm
        if state is ConnectionState.DISCONNECTED:
            return self.palette.text_faint
        return self.palette.danger

    def fan_color(self, state: FanState) -> str:
        if state in (FanState.RUNNING, FanState.BOOSTING):
            return self.palette.primary
        if state is FanState.STARTING:
            return self.palette.info
        if state in (FanState.STOPPED, FanState.UNKNOWN):
            return self.palette.text_faint
        return self.palette.danger

    def response_color(self, state: FanResponseState) -> str:
        if state is FanResponseState.NORMAL:
            return self.palette.success
        if state is FanResponseState.LOWER_THAN_EXPECTED:
            return self.palette.warning
        if state is FanResponseState.NO_RESPONSE:
            return self.palette.danger
        return self.palette.text_faint

    def metric_color(self, state: MetricState, provenance_default: str = "text") -> str:
        """Colour for a value based on its trustworthiness."""
        if state is MetricState.SIMULATED:
            return self.palette.secondary
        if state is MetricState.STALE:
            return self.palette.warning
        if state is MetricState.ERROR:
            return self.palette.danger
        if state is MetricState.UNAVAILABLE:
            return self.palette.text_faint
        if state is MetricState.DERIVED:
            return self.palette.secondary
        return self.color(provenance_default)

    def backend_color(self, status: BackendStatus) -> str:
        return {
            BackendStatus.READY: self.palette.success,
            BackendStatus.DEGRADED: self.palette.warning,
            BackendStatus.INITIALISING: self.palette.info,
            BackendStatus.UNAVAILABLE: self.palette.text_faint,
            BackendStatus.ERROR: self.palette.danger,
            BackendStatus.UNKNOWN: self.palette.text_faint,
        }[status]

    # -- motion helpers ----------------------------------------------------

    @property
    def motion(self) -> bool:
        """Whether non-essential animation should run."""
        return not self.reduced and self.animation is not AnimationLevel.OFF

    def duration(self, base_ms: int) -> int:
        """Scale an animation duration by the user's motion preference."""
        if not self.motion:
            return 0
        scale = {
            AnimationLevel.LOW: 0.5,
            AnimationLevel.NORMAL: 1.0,
            AnimationLevel.HIGH: 1.35,
        }.get(self.animation, 1.0)
        return max(0, int(base_ms * scale))


def build_theme(
    mode: ThemeMode,
    accent: str = "cyan",
    animation: AnimationLevel = AnimationLevel.NORMAL,
    show_glow: bool = True,
    performance: PerformanceMode = PerformanceMode.NORMAL,
) -> Theme:
    """Create a :class:`Theme` from user configuration."""
    palette = LIGHT if mode is ThemeMode.LIGHT else DARK
    key = accent if accent in ACCENTS else "cyan"
    base, hover, pressed, soft = ACCENTS[key]
    palette = palette.with_accent(base, hover, pressed, soft)
    reduced = animation is AnimationLevel.OFF
    lightweight = performance is PerformanceMode.LIGHTWEIGHT
    # Lightweight mode promises less motion as well as less chart detail, so
    # cap the motion at LOW unless the user turned animation off entirely.
    effective_animation = (
        AnimationLevel.LOW if lightweight and not reduced else animation
    )
    return Theme(
        palette=palette,
        accent=key,
        animation=effective_animation,
        show_glow=show_glow,
        reduced=reduced,
        lightweight=lightweight,
    )


__all__ = [
    "ACCENTS",
    "DARK",
    "LIGHT",
    "Palette",
    "RADIUS",
    "SPACE",
    "TYPE",
    "Theme",
    "build_theme",
    "FONT_FA",
    "FONT_MONO",
    "FONT_UI",
]
