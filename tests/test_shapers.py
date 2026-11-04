"""Tests for response shaping + object extraction (responses/shapers.py).

The shaping pipeline and object extraction are the centerpiece of token
efficiency and correctness, so they get thorough coverage.
"""

from __future__ import annotations

from node804_mcp_toolkit import Pagination

from hpe_msa_mcp.responses.shapers import (
    LeanShape,
    apply_shape,
    extract_objects,
    shape_single,
)

# --- extract_objects --------------------------------------------------------


def test_extract_prefers_named_key() -> None:
    payload = {
        "drives": [{"location": "0.1"}, {"location": "0.2"}],
        "status": [{"response-type": "Success"}],
    }
    objs = extract_objects(payload, "drives")
    assert [o["location"] for o in objs] == ["0.1", "0.2"]


def test_extract_falls_back_to_first_list_key() -> None:
    # Firmware used a different key than we expected; auto-detect finds it.
    payload = {
        "some-new-key": [{"name": "a"}],
        "status": [{"response-type": "Success"}],
    }
    objs = extract_objects(payload, "expected-but-absent")
    assert objs == [{"name": "a"}]


def test_extract_never_returns_status_array() -> None:
    payload = {"status": [{"response-type": "Success", "response": "OK"}]}
    assert extract_objects(payload, None) == []


def test_extract_ignores_non_dict_list_members() -> None:
    payload = {"drives": [{"location": "0.1"}, "junk", 3]}
    objs = extract_objects(payload, "drives")
    assert objs == [{"location": "0.1"}]


# --- apply_shape ------------------------------------------------------------


def test_default_whitelist_drops_unlisted_fields() -> None:
    items = [{"volume-name": "a", "size": 1, "wwn": "abc"}]
    shape = LeanShape(default_fields=("volume-name", "size"), name_field="volume-name")
    result = apply_shape(items, shape=shape)
    assert result["items"][0] == {"volume-name": "a", "size": 1}


def test_verbose_keeps_everything() -> None:
    items = [{"volume-name": "a", "size": 1, "wwn": "abc"}]
    shape = LeanShape(default_fields=("volume-name",), name_field="volume-name")
    result = apply_shape(items, shape=shape, verbose=True)
    assert result["items"][0] == {"volume-name": "a", "size": 1, "wwn": "abc"}


def test_explicit_fields_overrides_default() -> None:
    items = [{"volume-name": "a", "size": 1, "wwn": "abc"}]
    shape = LeanShape(default_fields=("volume-name", "size"), name_field="volume-name")
    result = apply_shape(items, shape=shape, fields=["wwn"])
    assert result["items"][0] == {"wwn": "abc"}


def test_none_values_dropped_by_default() -> None:
    items = [{"name": "a", "health-reason": None}]
    shape = LeanShape(default_fields=("name", "health-reason"))
    result = apply_shape(items, shape=shape)
    assert result["items"][0] == {"name": "a"}


def test_extra_strip_keys_recursive() -> None:
    items = [
        {
            "controller": "A",
            "certificate-text": "-----BEGIN...",
            "nested": {"certificate-text": "x"},
        }
    ]
    shape = LeanShape(name_field="controller", extra_strip_keys=("certificate-text",))
    result = apply_shape(items, shape=shape, verbose=True)
    item = result["items"][0]
    assert "certificate-text" not in item
    assert item["nested"] == {}


def test_pattern_filter_uses_custom_name_field() -> None:
    items = [
        {"volume-name": "sql-data"},
        {"volume-name": "web-logs"},
        {"volume-name": "SQL-backup"},
    ]
    shape = LeanShape(default_fields=("volume-name",), name_field="volume-name")
    result = apply_shape(items, shape=shape, pattern="sql")
    assert result["total"] == 2
    assert {i["volume-name"] for i in result["items"]} == {"sql-data", "SQL-backup"}


def test_pagination_respects_limit_offset() -> None:
    items = [{"location": f"0.{i}"} for i in range(20)]
    shape = LeanShape(default_fields=("location",), name_field="location")
    result = apply_shape(items, shape=shape, pagination=Pagination(limit=5, offset=10))
    assert result["count"] == 5
    assert result["total"] == 20
    assert [i["location"] for i in result["items"]] == [f"0.{i}" for i in range(10, 15)]


def test_response_shape_consistent_when_empty() -> None:
    result = apply_shape([], shape=LeanShape())
    assert result == {"total": 0, "count": 0, "items": []}


# --- shape_single -----------------------------------------------------------


def test_shape_single_returns_dict() -> None:
    item = {"system-name": "san-a", "product-id": "MSA 2060", "vendor-name": "HPE"}
    shape = LeanShape(default_fields=("system-name", "product-id"), name_field="system-name")
    assert shape_single(item, shape=shape) == {"system-name": "san-a", "product-id": "MSA 2060"}


def test_shape_single_handles_none() -> None:
    assert shape_single(None, shape=LeanShape(default_fields=("x",))) == {}


def test_token_reduction_measurable() -> None:
    """A realistic verbose vs lean disk comparison shows a meaningful reduction."""
    verbose_disk = {
        "location": "0.1",
        "serial-number": "ABC123",
        "vendor": "HPE",
        "model": "MB008000JWJRQ",
        "size": "8000.0GB",
        "type": "SAS MDL",
        "status": "Up",
        "health": "OK",
        "health-reason": "",
        "disk-group": "dg01",
        "temperature": "31 C",
        "rpm": "7",
        "extra-uuid": "x" * 36,
        "power-on-hours": "12345",
        "smart": "Enabled",
        "fde-state": "Not Secured",
        "object-name": "drive",
    }
    shape = LeanShape(
        default_fields=("location", "serial-number", "model", "size", "status", "health"),
        name_field="location",
    )
    items = [verbose_disk] * 50
    verbose_size = len(str(apply_shape(items, shape=shape, verbose=True)))
    lean_size = len(str(apply_shape(items, shape=shape)))
    reduction = 100 * (1 - lean_size / verbose_size)
    assert reduction > 40, f"expected >40% reduction, got {reduction:.1f}%"
