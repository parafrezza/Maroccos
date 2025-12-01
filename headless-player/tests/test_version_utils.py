import sys
from pathlib import Path
base = Path(__file__).resolve().parents[1]
if str(base) not in sys.path:
    sys.path.insert(0, str(base))
from version_utils import _parse_off_version_from_json, _parse_off_version_from_status


def test_parse_off_version_from_json_off_key():
    body = '{"off":"v1.2.3","headless":"v1.2.5"}'
    off, headless = _parse_off_version_from_json(body)
    assert off == "v1.2.3"
    assert headless == "v1.2.5"


def test_parse_off_version_from_json_version_key():
    body = '{"version":"v1.3.0"}'
    off, headless = _parse_off_version_from_json(body)
    assert off == "v1.3.0"
    assert headless is None


def test_parse_off_version_from_json_bad_body():
    body = 'not a json'
    off, headless = _parse_off_version_from_json(body)
    assert off is None
    assert headless is None


def test_parse_off_version_from_status_version_field():
    body = '{"player":{"version":"v2.3.4"}}'
    off, _ = _parse_off_version_from_status(body)
    assert off == "v2.3.4"


def test_parse_off_version_from_status_appVersion_field():
    body = '{"player":{"appVersion":"v9.9.9"}}'
    off, _ = _parse_off_version_from_status(body)
    assert off == "v9.9.9"


def test_parse_off_version_from_status_bad_body():
    body = 'whooops'
    off, _ = _parse_off_version_from_status(body)
    assert off is None


def test_aggregate_off_responses_version_prefers_version():
    headless = "v1.9.9"
    off_version_body = '{"off":"v1.2.3","headless":"v1.2.5"}'
    off_status_body = '{"player":{"version":"v2.3.4"}}'
    from version_utils import aggregate_off_responses
    res = aggregate_off_responses(headless, off_version_body, off_status_body)
    assert res["headless"] == headless
    assert res["off"] == "v1.2.3"
    assert res["off_source"] == "version"


def test_aggregate_off_responses_fallback_to_status():
    headless = "v3.3.3"
    off_version_body = None
    off_status_body = '{"player":{"version":"v2.3.4"}}'
    from version_utils import aggregate_off_responses
    res = aggregate_off_responses(headless, off_version_body, off_status_body)
    assert res["headless"] == headless
    assert res["off"] == "v2.3.4"
    assert res["off_source"] == "status"


def test_aggregate_off_responses_none_when_both_fail():
    headless = "v4.4.4"
    res = __import__("version_utils").version_utils.aggregate_off_responses(headless, None, None) if False else __import__("version_utils").aggregate_off_responses(headless, None, None)
    assert res["headless"] == headless
    assert res["off"] is None
