"""Structured logging with rotation and secret redaction (spec §93, §231).

Two sinks are configured:

* a rotating file handler in the ``Logs`` directory (never in the repository),
* a console handler that is only attached for developer/console contexts.

A :class:`RedactionFilter` scrubs anything that looks like a key or token from
log records so a diagnostic bundle can never leak credentials.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import re
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(threadName)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 5

#: The credential key names we treat as secret-bearing.  Kept explicit (rather
#: than a bare ``key``) so ordinary text like "the shortcut key" is untouched.
_SECRET_NAMES = (
    r"hmac[_\-]?key|secret[_\-]?key|secret|passphrase|password|passwd|pwd|"
    r"token|api[_\-]?key|api[_\-]?secret|access[_\-]?key|access[_\-]?token|"
    r"client[_\-]?secret|private[_\-]?key|pairing[_\-]?code|session[_\-]?token|"
    r"credential[s]?|auth[_\-]?token"
)

#: Patterns for values that must never reach a log file.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # PEM private keys, possibly spanning several lines.
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "<redacted-private-key>",
    ),
    # A quoted key and a quoted value — the shape JSON/YAML/dict-repr secrets
    # take, e.g. ``"token": "abcdef"``.  The earlier "key[:=]value" rule missed
    # these (the quote between the key and the colon defeated it), which leaked
    # credentials into logs and support bundles.
    (
        re.compile(
            rf"(?i)([\"']?(?:{_SECRET_NAMES})[\"']?\s*[:=]\s*)([\"'])([^\"'\n]{{0,200}})(\2)"
        ),
        r"\1\2<redacted>\4",
    ),
    # An unquoted key and value, e.g. ``token=abc`` or ``api_key: sk-live``.
    (
        re.compile(rf"(?i)({_SECRET_NAMES})\s*[:=]\s*\S+"),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?i)authorization:\s*\S+.*"), "authorization: <redacted>"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "<redacted-hex>"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"), "Bearer <redacted>"),
    # Credentials embedded in a URL (https://user:token@host/…) leak the same
    # way whether the secret is a password or an API token, so redact the whole
    # userinfo password component and leave the scheme/user/host readable.
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+):[^/\s@]+@"),
        r"\1:<redacted>@",
    ),
    # Webhook URLs (Slack/Discord/…) carry their secret as a long opaque path
    # segment. Require some non-lowercase entropy so a readable slug like
    # ".../installation-guide" is left alone, but over-redacting is the safe
    # direction to fail.
    (
        re.compile(
            r"(https?://[^\s\"'<>]+/)(?=[A-Za-z0-9_\-]*[0-9A-Z])([A-Za-z0-9_\-]{16,})(?=[\"'\s<>]|$)"
        ),
        r"\1<redacted>",
    ),
)


class RedactionFilter(logging.Filter):
    """Scrub credential-like substrings from formatted messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def redact_text(text: str) -> str:
    """Apply every redaction pattern to ``text``.

    Exposed separately from the logging filter so tools (support bundles, log
    exports) can scrub arbitrary strings with exactly the same rules.
    """
    redacted = text
    for pattern, replacement in _REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


_configured = False


def configure_logging(
    log_dir: Path | None,
    *,
    level: str = "INFO",
    console: bool = False,
    developer: bool = False,
) -> logging.Logger:
    """Configure the root logger once and return it.

    Parameters
    ----------
    log_dir:
        Directory for the rotating file log.  ``None`` disables file logging
        (useful in tests).
    level:
        Root log level name.
    console:
        Attach a stderr handler (console/developer contexts only).
    developer:
        When true, verbose third-party loggers are enabled.
    """
    global _configured
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if _configured:
        return root

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    redactor = RedactionFilter()

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "smartcooling.log",
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.addFilter(redactor)
        root.addHandler(stream)

    if not developer:
        for noisy in ("websockets", "asyncio", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def reset_logging() -> None:
    """Tear down handlers (test helper)."""
    global _configured
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()
    _configured = False


__all__ = ["RedactionFilter", "configure_logging", "get_logger", "reset_logging"]
