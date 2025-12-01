import pytest

from GUI.core.config import NetworkSettings
from GUI.services.player_registry import PlayerRecord, PlayerRegistry


def _make_registry() -> PlayerRegistry:
    settings = NetworkSettings(player_port=8080, ping_interval=0.1)
    return PlayerRegistry(settings)


def test_sync_players_dedupes_same_device_id():
    registry = _make_registry()
    loopback = PlayerRecord(name="Local", ip="127.0.0.1", device_id="dev-1", port=8080)
    lan = PlayerRecord(name="Local", ip="192.168.10.5", device_id="dev-1", port=8080)

    registry.sync_players([loopback, lan])

    players = registry.current_players()
    assert len(players) == 1
    assert players[0].ip == "192.168.10.5"


def test_merge_duplicate_records_prefers_non_loopback():
    registry = _make_registry()
    loopback = PlayerRecord(name="Loop", ip="127.0.0.1", device_id="dev-42", port=8080)
    lan = PlayerRecord(name="Lan", ip="10.0.0.50", device_id="dev-42", port=8080)

    with registry._lock:  # pylint: disable=protected-access
        registry._players = {loopback.ip: loopback, lan.ip: lan}

    removed = []
    registry.playerRemoved.connect(lambda ip: removed.append(ip))

    registry._merge_duplicate_records(loopback)  # type: ignore[attr-defined]

    players = registry.current_players()
    assert len(players) == 1
    assert players[0].ip == "10.0.0.50"
    assert players[0].name == "Lan"
    assert removed == ["127.0.0.1"]
