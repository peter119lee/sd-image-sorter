from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from routers import updates


class FakeUpdateService:
    def __init__(
        self,
        *,
        status_payload=None,
        apply_payload=None,
        apply_error: Optional[Exception] = None,
        restart_payload=None,
        restart_error: Optional[Exception] = None,
    ):
        self.status_payload = status_payload or {}
        self.apply_payload = apply_payload or {}
        self.apply_error = apply_error
        self.restart_payload = restart_payload
        self.restart_error = restart_error
        self.restart_calls: list[str] = []
        self.channel_payload = {
            "channel_name": "GitHub Default",
            "channel_api_url": "https://api.github.com/repos/peter119lee/sd-image-sorter/releases/latest",
            "channel_web_url": "https://github.com/peter119lee/sd-image-sorter/releases/latest",
            "download_url_prefix": "",
            "has_channel_override": False,
        }
        self.status_calls: list[bool] = []
        self.apply_calls: list[tuple[bool, bool]] = []
        self.proxy_calls: list[tuple[str, str]] = []
        self.reset_calls = 0

    def get_status(self, *, force: bool = False):
        self.status_calls.append(force)
        return self.status_payload

    def get_channel_settings(self):
        return self.channel_payload

    def save_proxy_channel(self, proxy_prefix: str, *, channel_name: str = "Custom Proxy"):
        self.proxy_calls.append((proxy_prefix, channel_name))
        return {
            **self.channel_payload,
            "channel_name": channel_name,
            "download_url_prefix": proxy_prefix,
            "has_channel_override": True,
        }

    def reset_channel_settings(self):
        self.reset_calls += 1
        return self.channel_payload

    def prepare_update(self, *, force_check: bool = False, relaunch: bool = True):
        self.apply_calls.append((force_check, relaunch))
        if self.apply_error is not None:
            raise self.apply_error
        return self.apply_payload

    def restart_app(self, *, reason: str = ""):
        self.restart_calls.append(reason)
        if self.restart_error is not None:
            raise self.restart_error
        if self.restart_payload is not None:
            return self.restart_payload
        return {"status": "unsupported", "reason": "no launcher"}


def test_get_update_status(test_client):
    fake = FakeUpdateService(
        status_payload={
            "current_version": "3.1.0",
            "latest_version": "3.1.1",
            "has_update": True,
            "asset": {"name": "sd-image-sorter-v3.1.1-app-patch.zip"},
        }
    )
    updates.set_update_service(fake)

    response = test_client.get("/api/updates/status?force=true")

    assert response.status_code == 200
    assert response.json()["has_update"] is True
    assert fake.status_calls == [True]


def test_apply_update_returns_scheduled_payload(test_client):
    fake = FakeUpdateService(
        apply_payload={
            "status": "scheduled",
            "current_version": "3.1.0",
            "latest_version": "3.1.1",
            "has_update": True,
        }
    )
    updates.set_update_service(fake)

    response = test_client.post("/api/updates/apply", json={"force_check": True, "relaunch": True})

    assert response.status_code == 200
    assert response.json()["status"] == "scheduled"
    assert fake.apply_calls == [(True, True)]


def test_get_update_channel(test_client):
    fake = FakeUpdateService()
    updates.set_update_service(fake)

    response = test_client.get("/api/updates/channel")

    assert response.status_code == 200
    assert response.json()["channel_name"] == "GitHub Default"


def test_set_update_channel_proxy(test_client):
    fake = FakeUpdateService()
    updates.set_update_service(fake)

    response = test_client.post(
        "/api/updates/channel/proxy",
        json={"proxy_prefix": "https://ghfast.top/", "channel_name": "China Proxy"},
    )

    assert response.status_code == 200
    assert response.json()["download_url_prefix"] == "https://ghfast.top/"
    assert fake.proxy_calls == [("https://ghfast.top/", "China Proxy")]


def test_reset_update_channel(test_client):
    fake = FakeUpdateService()
    updates.set_update_service(fake)

    response = test_client.delete("/api/updates/channel")

    assert response.status_code == 200
    assert fake.reset_calls == 1


def test_restart_app_returns_scheduled_payload(test_client, monkeypatch):
    fake = FakeUpdateService(
        restart_payload={"status": "scheduled", "launcher": "run.bat"},
    )
    updates.set_update_service(fake)
    exit_calls: list[str] = []
    monkeypatch.setattr(updates, "_schedule_process_exit", lambda *args, **kwargs: exit_calls.append("exit"))

    response = test_client.post(
        "/api/updates/restart",
        json={"reason": "model_dependency_install"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "scheduled", "launcher": "run.bat"}
    assert fake.restart_calls == ["model_dependency_install"]
    assert exit_calls == ["exit"]


def test_restart_app_returns_unsupported_without_exiting(test_client, monkeypatch):
    fake = FakeUpdateService()
    updates.set_update_service(fake)
    exit_calls: list[str] = []
    monkeypatch.setattr(updates, "_schedule_process_exit", lambda *args, **kwargs: exit_calls.append("exit"))

    response = test_client.post("/api/updates/restart", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "unsupported"
    assert fake.restart_calls == [""]
    assert exit_calls == []


def test_restart_app_rejects_missing_json_body(test_client):
    fake = FakeUpdateService(restart_payload={"status": "scheduled", "launcher": "run.bat"})
    updates.set_update_service(fake)

    response = test_client.post("/api/updates/restart")

    assert response.status_code == 400
    assert response.json()["type"] == "ValidationError"
    assert fake.restart_calls == []


def test_restart_app_rejects_oversized_reason(test_client):
    fake = FakeUpdateService(restart_payload={"status": "scheduled", "launcher": "run.bat"})
    updates.set_update_service(fake)

    response = test_client.post(
        "/api/updates/restart",
        json={"reason": "x" * 201},
    )

    assert response.status_code == 400
    assert response.json()["type"] == "ValidationError"
    assert fake.restart_calls == []


def test_restart_app_returns_503_on_failure(test_client, monkeypatch):
    fake = FakeUpdateService(restart_error=RuntimeError("cannot spawn worker"))
    updates.set_update_service(fake)
    exit_calls: list[str] = []
    monkeypatch.setattr(updates, "_schedule_process_exit", lambda *args, **kwargs: exit_calls.append("exit"))

    response = test_client.post("/api/updates/restart", json={"reason": "model_dependency_install"})

    assert response.status_code == 503
    assert response.json()["error"] == "cannot spawn worker"
    assert exit_calls == []


def test_apply_update_returns_503_on_failure(test_client):
    fake = FakeUpdateService(apply_error=RuntimeError("boom"))
    updates.set_update_service(fake)

    response = test_client.post("/api/updates/apply", json={"force_check": True, "relaunch": True})

    assert response.status_code == 503
    assert response.json()["error"] == "boom"
