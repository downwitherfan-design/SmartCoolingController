"""Exception hierarchy for SmartCooling Controller.

A shallow, explicit hierarchy lets callers distinguish recoverable hardware or
network problems (which should degrade gracefully) from programming errors
(which should surface loudly during development).
"""

from __future__ import annotations


class SmartCoolingError(Exception):
    """Base class for every error raised by this application."""


class ConfigurationError(SmartCoolingError):
    """Configuration is missing, malformed or inconsistent."""


class HardwareError(SmartCoolingError):
    """A hardware monitoring back-end failed."""


class BackendUnavailable(HardwareError):
    """The requested back-end could not be initialised on this machine."""


class SensorNotFound(HardwareError):
    """A requested sensor does not exist."""


class TelemetryError(SmartCoolingError):
    """The telemetry engine encountered a problem."""


class ProtocolError(SmartCoolingError):
    """A wire packet was malformed or violated the protocol contract."""


class AuthenticationError(ProtocolError):
    """Message authentication failed."""


class ProtocolMismatch(ProtocolError):
    """The peer speaks an incompatible protocol version."""


class NetworkError(SmartCoolingError):
    """Transport level failure (socket, websocket, discovery)."""


class PersistenceError(SmartCoolingError):
    """Database read/write/migration failure."""


class MigrationError(PersistenceError):
    """A schema migration could not be applied."""


class BackupError(PersistenceError):
    """Backup or restore failed."""


class ExportError(SmartCoolingError):
    """An export operation failed."""


class ProfileValidationError(SmartCoolingError):
    """An imported or edited profile is invalid."""


class ElevationRequired(HardwareError):
    """The operation needs elevated privileges to expose more metrics."""


__all__ = [
    "AuthenticationError",
    "BackendUnavailable",
    "BackupError",
    "ConfigurationError",
    "ElevationRequired",
    "ExportError",
    "HardwareError",
    "MigrationError",
    "NetworkError",
    "PersistenceError",
    "ProfileValidationError",
    "ProtocolError",
    "ProtocolMismatch",
    "SensorNotFound",
    "SmartCoolingError",
    "TelemetryError",
]
