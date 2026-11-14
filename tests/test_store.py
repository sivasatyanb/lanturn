from __future__ import annotations

import time

import lantern.store as store_mod
from lantern.store import DatabricksVolumeStore, LocalStore, _token_expiry


def test_token_expiry_is_read_from_the_cli_result():
    now = time.time()
    soon = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now + 600))
    assert abs(_token_expiry({"expiry": soon + ".123456789Z"}) - (now + 600)) < 2     # Go nanoseconds
    assert abs(_token_expiry({"expiry": soon + "Z"}) - (now + 600)) < 2
    assert abs(_token_expiry({"expires_in": 900}) - (now + 900)) < 2
    assert _token_expiry({}) - now <= 300            # unknown lifetime is never trusted for long


def test_rejected_token_is_reminted_once_and_errors_hide_the_host(tmp_path, monkeypatch):
    s = DatabricksVolumeStore(profile="p", cache_dir=tmp_path / "cache")
    minted = []

    def fake_auth(force=False):
        if force or not minted:
            minted.append(f"token-{len(minted) + 1}")
        return "https://example.cloud.databricks.com", minted[-1]

    monkeypatch.setattr(s, "_auth", fake_auth)

    class Resp:
        def __init__(self, code, body=None):
            self.status_code, self._body = code, body

        def json(self):
            return self._body

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(headers["Authorization"])
        if headers["Authorization"].endswith("token-1"):
            return Resp(403)                          # expired token
        return Resp(200, {"schema": 1, "version": 9, "mode": "live", "entries": []})

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    doc = s.read_published()
    assert doc["version"] == 9 and calls == ["Bearer token-1", "Bearer token-2"]
    assert LocalStore(tmp_path / "cache").read_published()["version"] == 9      # last good copy cached

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(500))
    try:
        s.read_published()
        raise AssertionError("expected an error")
    except RuntimeError as exc:
        assert "500" in str(exc) and "databricks.com" not in str(exc)
    assert store_mod.get_store("local").kind == "local"
