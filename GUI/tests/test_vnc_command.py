import sys
from pathlib import Path

from GUI.core.config import AppConfig, RemoteSettings
from GUI.core.controller import ApplicationController


class DummyPlayer:
    def __init__(self, ip: str):
        self.ip = ip


def test_vnc_command_build_includes_port_and_shared(tmp_path, monkeypatch):
    # Setup settings path
    settings_path = tmp_path / "settings.json"
    ctrl = ApplicationController(settings_path)
    # Inject dummy viewer (path exists not required for building flags; we will monkeypatch Popen)
    ctrl.state.config.remote = RemoteSettings(
        vnc_viewer_path=Path("vncviewer64-1.15.0.exe"),
        vnc_password="",
        vnc_port=5901,
        vnc_extra_args=["-Shared"],
    )

    launched = {}

    def fake_popen(args, *a, **k):
        launched["args"] = list(args)
        class _P:
            def __init__(self):
                self.pid = 1234
        return _P()

    # Avoid actually starting a process
    monkeypatch.setattr("subprocess.Popen", fake_popen)

    # Force non-macOS simple path
    monkeypatch.setattr(sys, "platform", "win32")

    p = DummyPlayer("10.0.0.5")
    ctrl.open_vnc_viewer(p)

    cmd = launched.get("args", [])
    # Must contain address with double colon and port
    assert any(s.endswith("10.0.0.5::5901") for s in cmd), cmd
    # Must include -Shared either from default or extra args
    assert any(s.startswith("-Shared") for s in cmd), cmd
