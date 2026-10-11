"""Unit tests for the psutil back-end's storage sampling.

The mount topology is expensive to enumerate and almost never changes, so it is
cached; per-mount *usage* must still be read every sample so free-space stays
truthful.  These tests pin both halves of that contract.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.hardware.psutil_backend import PsutilBackend


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> PsutilBackend:
    calls = {"partitions": 0, "usage": 0}

    def fake_partitions(all: bool = False):
        calls["partitions"] += 1
        return [
            SimpleNamespace(device="/dev/sda1", mountpoint="/", fstype="ext4"),
            SimpleNamespace(device="/dev/sdb1", mountpoint="/data", fstype="ext4"),
        ]

    def fake_usage(mountpoint: str):
        calls["usage"] += 1
        return SimpleNamespace(percent=42.0, used=1, free=1, total=2)

    monkeypatch.setattr("app.hardware.psutil_backend.psutil.disk_partitions", fake_partitions)
    monkeypatch.setattr("app.hardware.psutil_backend.psutil.disk_usage", fake_usage)

    b = PsutilBackend()
    b.open()
    b._calls = calls  # type: ignore[attr-defined]
    return b


def test_storage_usage_is_read_fresh_every_sample(backend: PsutilBackend) -> None:
    backend._read_storage(time.time())
    backend._read_storage(time.time())
    # Two samples, two mounts each -> usage queried four times, never cached.
    assert backend._calls["usage"] == 4


def test_mount_list_is_cached_across_samples(backend: PsutilBackend) -> None:
    backend._read_storage(time.time())
    first = backend._calls["partitions"]
    for _ in range(20):
        backend._read_storage(time.time())
    # The expensive enumeration happened once, not once per sample.
    assert backend._calls["partitions"] == first == 1


def test_mount_list_refreshes_after_the_ttl(backend: PsutilBackend) -> None:
    backend._read_storage(time.time())
    assert backend._calls["partitions"] == 1
    # Age the cache past the TTL and confirm the next read re-scans.
    backend._partitions_at = time.monotonic() - (PsutilBackend._PARTITIONS_TTL_S + 1)
    backend._read_storage(time.time())
    assert backend._calls["partitions"] == 2


def test_transient_partition_failure_keeps_the_previous_list(
    backend: PsutilBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend._read_storage(time.time())
    assert backend._partitions is not None

    def boom(all: bool = False):
        raise OSError("enumeration failed")

    monkeypatch.setattr("app.hardware.psutil_backend.psutil.disk_partitions", boom)
    backend._partitions_at = time.monotonic() - (PsutilBackend._PARTITIONS_TTL_S + 1)
    # A refresh failure must not flap the visible storage tiles to empty.
    backend._read_storage(time.time())
    assert backend._partitions is not None and len(backend._partitions) == 2
