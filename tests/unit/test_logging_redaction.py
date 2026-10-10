"""Log redaction must scrub credentials without mangling ordinary text.

Spec §93/§231: no secret may reach a log file. Support bundles and log exports
reuse :func:`redact_text`, so these rules are a security boundary, not just a
formatting nicety.
"""

from __future__ import annotations

import logging

import pytest

from app.core.logging_setup import RedactionFilter, redact_text


@pytest.mark.parametrize(
    "raw",
    [
        "password=hunter2",
        "api_key: sk-live-abc123",
        "token = 0123456789abcdef0123456789abcdef",
        "Authorization: Basic dXNlcjpwYXNz",
        "sent header Bearer eyJhbGciOi.jwt.token",
        'webhook "https://hooks.example.com/T000/B000/XXXXXXXXXXXXXXXXXX"',
        "connect ws://user:s3cr3t@10.0.0.5:81/telemetry",
        "https://admin:t0p-s3cret@controller.local/api",
    ],
)
def test_secret_shaped_values_are_redacted(raw: str) -> None:
    scrubbed = redact_text(raw)
    assert "<redacted" in scrubbed
    for secret in ("hunter2", "sk-live-abc123", "s3cr3t", "t0p-s3cret", "eyJhbGciOi"):
        assert secret not in scrubbed


def test_url_credentials_keep_the_host_readable() -> None:
    scrubbed = redact_text("connecting to https://admin:t0p-s3cret@controller.local/api")
    assert "controller.local" in scrubbed
    assert "admin" in scrubbed
    assert "t0p-s3cret" not in scrubbed


def test_ordinary_text_is_untouched() -> None:
    text = "connected to 10.0.0.5:81 after 0 reconnects; fan at 1800 RPM"
    assert redact_text(text) == text


def test_filter_rewrites_the_record_before_handlers_see_it() -> None:
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("smartcooling.test.redaction")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addFilter(RedactionFilter())
    handler = _Capture()
    logger.addHandler(handler)
    try:
        logger.info("handshake failed: token=%s", "supersecretvalue")
    finally:
        logger.removeHandler(handler)

    assert records == ["handshake failed: token=<redacted>"]
    assert "supersecretvalue" not in records[0]
