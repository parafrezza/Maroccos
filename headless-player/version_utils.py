"""Utility functions for parsing OFF version/status JSON without side-effects.

This file intentionally has no dependencies on the running app or background threads so it
can be imported safely in unit tests.
"""
from __future__ import annotations
from typing import Tuple, Any


def _parse_off_version_from_json(body: str) -> Tuple[Any, Any]:
    """Parse a /version-like body and return (off_value, headless_value).
    Returns (None, None) when parsing failed or fields missing."""
    import json as _json
    try:
        player = _json.loads(body)
        if isinstance(player, dict):
            if "off" in player:
                return player.get("off"), player.get("headless")
            if "version" in player:
                return player.get("version"), player.get("headless")
        return None, None
    except Exception:
        return None, None


def _parse_off_version_from_status(body: str) -> Tuple[Any, Any]:
    """Parse a /status body and return (player_version, None) or (None, None)."""
    import json as _json
    try:
        st = _json.loads(body)
        if isinstance(st, dict) and "player" in st and isinstance(st["player"], dict):
            if "version" in st["player"]:
                return st["player"].get("version"), None
            if "appVersion" in st["player"]:
                return st["player"].get("appVersion"), None
        return None, None
    except Exception:
        return None, None


def aggregate_off_responses(headless_version: str, off_version_body: str | None, off_status_body: str | None) -> dict:
    """Given optional OFF /version body and /status body, return the aggregated version dict.

    This mirrors the behavior of the headless /version endpoint but is pure and easy to test.
    """
    res = {"headless": headless_version}
    if off_version_body is not None:
        off_val, off_headless = _parse_off_version_from_json(off_version_body)
        if off_val is not None:
            res["off"] = off_val
            res["off_source"] = "version"
            if off_headless is not None:
                res["off_headless"] = off_headless
            return res
        res["off_raw"] = off_version_body
    if off_status_body is not None:
        off_val, _ = _parse_off_version_from_status(off_status_body)
        if off_val is not None:
            res["off"] = off_val
            res["off_source"] = "status"
            return res
        res["off_raw_status"] = off_status_body
    res["off"] = None
    return res
