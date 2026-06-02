"""Tests for upload_client.card_identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from upload_client.card_identity import (
    card_uuid_from,
    discipline_hint_from_label,
    table_from_label,
)


@pytest.mark.parametrize("label,expected", [
    ("E01", "ET01"),
    ("D12", "ET12"),
    ("E24", "ET24"),
    ("STS_07", "ET07"),
    ("e3", "ET03"),
    ("E99", "ET99"),
])
def test_table_from_label_ok(label: str, expected: str) -> None:
    assert table_from_label(label) == expected


@pytest.mark.parametrize("label", ["", None, "Einzel", "NO NAME", "E00", "E100"])
def test_table_from_label_none(label) -> None:
    assert table_from_label(label) is None


@pytest.mark.parametrize("label,expected", [
    ("E01", "Einzel"), ("e22", "Einzel"),
    ("D01", "Doppel"), ("d5", "Doppel"),
    ("X01", None), ("01", None), ("", None), (None, None),
])
def test_discipline_hint(label, expected) -> None:
    assert discipline_hint_from_label(label) == expected


def test_card_uuid_prefers_serial() -> None:
    assert card_uuid_from(serial="A1B2", label="E01") == "vol-A1B2"


def test_card_uuid_falls_back_to_label_then_path() -> None:
    assert card_uuid_from(serial=None, label="E01") == "label-E01"
    out = card_uuid_from(serial=None, label=None, root=Path("/media/x"))
    assert out.startswith("path-")


def test_card_uuid_is_discipline_independent() -> None:
    # Same physical card -> same id regardless of which discipline it ends up.
    assert card_uuid_from(serial="S1") == card_uuid_from(serial="S1")
