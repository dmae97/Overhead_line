from __future__ import annotations

from src.data.geo import parse_overpass_power_lines, parse_voltage_value


def test_parse_overpass_power_lines_minimal() -> None:
    data = {
        "elements": [
            {"type": "node", "id": 1, "lat": 37.0, "lon": 127.0},
            {"type": "node", "id": 2, "lat": 37.1, "lon": 127.1},
            {
                "type": "way",
                "id": 10,
                "nodes": [1, 2],
                "tags": {"power": "line", "name": "Test Line", "voltage": "154000"},
            },
        ]
    }

    lines = parse_overpass_power_lines(data)
    assert len(lines) == 1
    assert lines[0].name == "Test Line"
    assert lines[0].power == "line"
    assert lines[0].voltage == "154000"
    assert lines[0].lats == [37.0, 37.1]
    assert lines[0].lons == [127.0, 127.1]


def test_parse_overpass_power_lines_missing_nodes() -> None:
    data = {
        "elements": [
            {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"power": "line"}},
        ]
    }
    assert parse_overpass_power_lines(data) == []


def test_parse_voltage_value() -> None:
    assert parse_voltage_value("22900") == 22900
    assert parse_voltage_value("154000;345000") == 154000
    assert parse_voltage_value("22 kV") == 22000
    assert parse_voltage_value("") is None
