"""Support-bundle redaction (spec §91, §204).

A support bundle is attached to public bug reports, so it is a security
boundary: it must scrub secrets both by key name (``{"password": ...}``) and by
the shape of a value (a URL with embedded userinfo inside a log line).  These
tests exercise ``redact`` directly and through a real ``DiagnosticsService``.
"""

from __future__ import annotations

import json

from app.services.health import DiagnosticsService, redact


def test_redact_by_key_name() -> None:
    bundle = {"api_token": "abc", "password": "hunter2", "host": "10.0.0.5"}
    scrubbed = redact(bundle)
    assert scrubbed["api_token"] == "<redacted>"
    assert scrubbed["password"] == "<redacted>"
    assert scrubbed["host"] == "10.0.0.5"


def test_redact_nested_and_lists() -> None:
    bundle = {"a": [{"secret": "s"}, {"token": "t"}], "b": {"deep": {"api_key": "k"}}}
    scrubbed = redact(bundle)
    assert scrubbed["a"][0]["secret"] == "<redacted>"
    assert scrubbed["a"][1]["token"] == "<redacted>"
    assert scrubbed["b"]["deep"]["api_key"] == "<redacted>"


def test_redact_by_value_shape_inside_a_string() -> None:
    # A credential can hide inside an otherwise ordinary log line; the shared
    # text rules must catch it even when the key name looks harmless.
    bundle = {"recent_diagnostics": [{"message": "connect https://admin:t0p-s3cret@h/api"}]}
    scrubbed = redact(bundle)
    assert "t0p-s3cret" not in json.dumps(scrubbed)
    assert "<redacted" in scrubbed["recent_diagnostics"][0]["message"]


def test_redact_leaves_non_string_scalars_alone() -> None:
    assert redact({"n": 3, "f": 1.5, "b": True, "none": None}) == {
        "n": 3,
        "f": 1.5,
        "b": True,
        "none": None,
    }


class _FakeDatabase:
    path = "/tmp/x.db"

    def size_bytes(self) -> int:
        return 1024

    def get_meta(self, *_a) -> str:
        return "3"


class _FakeCount:
    def count(self) -> int:
        return 7


class _FakePersistence:
    database = _FakeDatabase()
    snapshots = _FakeCount()

    def integrity_check(self) -> bool:
        return True

    class alerts:
        @staticmethod
        def list_recent(_n):
            return []

    class diagnostics:
        @staticmethod
        def recent(_n):
            return [{"message": "token=supersecretvalue"}]


class _FakePaths:
    mode = type("M", (), {"value": "user"})()

    def __init__(self) -> None:
        self.root = "/data"
        self.logs = "/data/logs"
        self.database = "/data/state.db"


def test_support_bundle_redacts_secrets_end_to_end() -> None:
    service = DiagnosticsService(
        paths=_FakePaths(), persistence=_FakePersistence(), registry=None
    )
    bundle = service.support_bundle()
    blob = json.dumps(bundle)
    assert "supersecretvalue" not in blob
    assert "<redacted" in blob
