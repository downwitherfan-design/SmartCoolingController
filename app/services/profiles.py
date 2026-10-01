"""Cooling profile library and service (spec §39, §40, §103).

Five built-in profiles ship with the application (Silent, Balanced,
Performance, Turbo and a Custom template).  Built-ins cannot be deleted; they
can be *copied* into an editable custom profile.  All persistence goes through
:class:`~app.persistence.repositories.ProfileRepository`.
"""

from __future__ import annotations

from app.core.enums import CoolingProfileKind, FanControlSource
from app.core.errors import ProfileValidationError
from app.core.events import EventBus
from app.core.logging_setup import get_logger
from app.models.cooling import CoolingProfile, FanCurve
from app.persistence.repositories import ProfileRepository

_log = get_logger(__name__)

TOPIC_PROFILES = "profiles.changed"

#: Temperature → duty control points shared by the built-in curves.
_CURVE_SILENT = [(40, 0), (55, 15), (65, 30), (75, 50), (85, 75), (95, 100)]
_CURVE_BALANCED = [(35, 0), (50, 20), (60, 35), (70, 55), (80, 80), (90, 100)]
_CURVE_PERFORMANCE = [(30, 20), (45, 35), (55, 50), (65, 70), (75, 90), (85, 100)]
_CURVE_TURBO = [(30, 50), (45, 65), (55, 80), (65, 90), (75, 100), (85, 100)]
_CURVE_CUSTOM = [(35, 0), (50, 20), (60, 40), (70, 60), (80, 80), (90, 100)]


def builtin_profiles() -> list[CoolingProfile]:
    """Return the five built-in profiles."""
    definitions = [
        ("silent", "Silent", CoolingProfileKind.SILENT, _CURVE_SILENT, 0.0, 80.0, 6.0, 3.0, 2.0),
        ("balanced", "Balanced", CoolingProfileKind.BALANCED, _CURVE_BALANCED, 0.0, 90.0, 8.0, 5.0, 3.0),
        ("performance", "Performance", CoolingProfileKind.PERFORMANCE, _CURVE_PERFORMANCE, 15.0, 100.0, 12.0, 8.0, 4.0),
        ("turbo", "Turbo", CoolingProfileKind.TURBO, _CURVE_TURBO, 40.0, 100.0, 25.0, 15.0, 5.0),
        ("custom", "Custom", CoolingProfileKind.CUSTOM, _CURVE_CUSTOM, 0.0, 100.0, 10.0, 6.0, 3.0),
    ]
    profiles: list[CoolingProfile] = []
    for key, name, kind, points, min_pwm, max_pwm, up, down, hysteresis in definitions:
        profile = CoolingProfile(
            key=key,
            name=name,
            kind=kind,
            curve=FanCurve.from_points(points, name=name),
            source=FanControlSource.MAX,
            min_pwm=min_pwm,
            max_pwm=max_pwm,
            ramp_up_pct_per_s=up,
            ramp_down_pct_per_s=down,
            hysteresis_c=hysteresis,
            builtin=True,
        )
        profile.validate()
        profiles.append(profile)
    return profiles


class ProfileService:
    """CRUD and selection for cooling profiles."""

    def __init__(self, repository: ProfileRepository, bus: EventBus) -> None:
        self._repo = repository
        self._bus = bus
        self._active_key = "balanced"

    # -- lifecycle ---------------------------------------------------------

    def seed_builtins(self) -> None:
        """Ensure every built-in profile exists in the database."""
        existing = {profile.key for profile in self._repo.list_all()}
        for profile in builtin_profiles():
            if profile.key not in existing:
                self._repo.upsert(profile)
                _log.info("seeded built-in profile '%s'", profile.key)

    # -- queries -----------------------------------------------------------

    def all(self) -> list[CoolingProfile]:
        profiles = self._repo.list_all()
        if not profiles:
            self.seed_builtins()
            profiles = self._repo.list_all()
        return profiles

    def get(self, key: str) -> CoolingProfile | None:
        profile = self._repo.get(key)
        if profile is None:
            profile = next((p for p in builtin_profiles() if p.key == key), None)
        return profile

    @property
    def active_key(self) -> str:
        return self._active_key

    @property
    def active(self) -> CoolingProfile | None:
        return self.get(self._active_key)

    # -- mutations ---------------------------------------------------------

    def set_active(self, key: str) -> CoolingProfile:
        profile = self.get(key)
        if profile is None:
            raise ProfileValidationError(f"unknown profile '{key}'")
        self._active_key = key
        self._bus.publish(TOPIC_PROFILES, {"active": key})
        return profile

    def save(self, profile: CoolingProfile) -> CoolingProfile:
        profile.validate()
        if profile.builtin:
            raise ProfileValidationError("built-in profiles are read-only; copy it first")
        self._repo.upsert(profile)
        self._bus.publish(TOPIC_PROFILES, {"saved": profile.key})
        return profile

    def duplicate(self, key: str, *, new_key: str, new_name: str) -> CoolingProfile:
        source = self.get(key)
        if source is None:
            raise ProfileValidationError(f"unknown profile '{key}'")
        data = source.to_dict()
        data.update({"key": new_key, "name": new_name, "builtin": False, "kind": "custom"})
        copy = CoolingProfile.from_dict(data)
        copy.builtin = False
        return self.save(copy)

    def delete(self, key: str) -> None:
        profile = self.get(key)
        if profile is None:
            return
        if profile.builtin:
            raise ProfileValidationError("built-in profiles cannot be deleted")
        self._repo.delete(key)
        if self._active_key == key:
            self.set_active("balanced")
        self._bus.publish(TOPIC_PROFILES, {"deleted": key})

    def import_profile(self, data: dict) -> CoolingProfile:
        profile = CoolingProfile.from_dict(data)
        profile.builtin = False
        profile.kind = CoolingProfileKind.CUSTOM
        return self.save(profile)


__all__ = ["ProfileService", "builtin_profiles", "TOPIC_PROFILES"]
