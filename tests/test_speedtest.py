"""Tests for watcher.speedtest (Ookla JSON parse + persistence)."""

from __future__ import annotations

import json

import pytest

from watcher.speedtest import (
    parse_ookla_json,
    read_speedtest,
    run_speedtest,
    speedtest_path_for,
    write_speedtest,
)

SAMPLE = json.dumps({
    "ping": {"latency": 12.3},
    "download": {"bandwidth": 12_500_000},   # bytes/s -> 100.0 Mbit/s
    "upload": {"bandwidth": 6_250_000},      # bytes/s -> 50.0 Mbit/s
    "server": {"name": "Init7 Winterthur"},
})


def test_parse_converts_bytes_per_s_to_mbit():
    r = parse_ookla_json(SAMPLE)
    assert r.download_mbit_s == 100.0
    assert r.upload_mbit_s == 50.0
    assert r.ping_ms == 12.3
    assert r.server == "Init7 Winterthur"
    assert r.ok is True


def test_run_with_injected_runner():
    r = run_speedtest(runner=lambda cmd: SAMPLE)
    assert r.ok is True
    assert r.download_mbit_s == 100.0
    # injected command carries the non-interactive license flags
    captured = {}
    run_speedtest(runner=lambda cmd: (captured.setdefault("cmd", cmd), SAMPLE)[1])
    assert "--accept-license" in captured["cmd"]
    assert "--accept-gdpr" in captured["cmd"]


def test_run_handles_bad_json():
    r = run_speedtest(runner=lambda cmd: "not json")
    assert r.ok is False
    assert r.download_mbit_s == 0.0
    assert r.error


def test_persistence_round_trip(tmp_path):
    path = speedtest_path_for(tmp_path)
    original = parse_ookla_json(SAMPLE)
    write_speedtest(path, original)
    loaded = read_speedtest(path)
    assert loaded is not None
    assert loaded.download_mbit_s == 100.0
    assert loaded.upload_mbit_s == 50.0
    assert loaded.ok is True


def test_read_missing_returns_none(tmp_path):
    assert read_speedtest(speedtest_path_for(tmp_path)) is None
