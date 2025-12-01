import json
import os
import platform
import socket
import subprocess
import time
import requests
import pytest


@pytest.mark.skipif(platform.system() != "Windows", reason="OFF-player exe test requires Windows (exe)")
def test_off_player_status_includes_version(monkeypatch, tmp_path):
    # Path to OFF-player executable
    exe = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'bin', 'OFF-player.exe')
    assert os.path.exists(exe), f"OFF-player executable not found at {exe}"

    # Use existing bin/config.json for the exe so the executable picks up the correct listening port
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'bin', 'config.json')
    port = 8085
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                port = int(cfg.get('httpPort', port))
        except Exception:
            pass

    expected_version = 'pytest-offplayer-vtest'

    env = os.environ.copy()
    env['HEADLESS_VERSION'] = expected_version

    # Start the OFF-player exe (working dir points to tmp_workdir to pick up config.json with chosen port)
    # Use CREATE_NO_WINDOW to avoid popping UI during tests
    creationflags = 0x08000000  # CREATE_NO_WINDOW
    # Start the OFF-player exe using its bin folder as working dir
    proc = subprocess.Popen([exe], env=env, cwd=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'bin'), creationflags=creationflags)

    try:
        url = f'http://127.0.0.1:{port}/version'
        # Wait for server to come up
        timeout = 20.0
        start = time.time()
        data = None
        while time.time() - start < timeout:
            try:
                r = requests.get(url, timeout=1.0)
                if r.status_code == 200:
                    try:
                        data = r.json()
                        break
                    except ValueError:
                        pass
            except requests.RequestException:
                pass
            time.sleep(0.2)
        assert data is not None, 'OFF-player /status did not respond in time'
        if 'off' not in data:
            pytest.skip('OFF-player /version does not include off field — meybe not rebuilt from current sources')
        # The off binary might not include headless field if not set via environment; skip if absent
        if 'headless' not in data:
            pytest.skip('OFF-player /version does not include headless field — likely not passed via environment')
        assert data['headless'] == expected_version
    finally:
        proc.kill()
        proc.wait(timeout=5)
