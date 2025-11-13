import os, socket, json, time
import pytest

from GUI.core.config import NetworkSettings
from GUI.services.player_registry import PlayerRegistry

@pytest.mark.timeout(10)
def test_beacon_discovery_inserts_record():
    # Abilita listener (default), imposta porta nota
    os.environ['HEADLESS_BEACON_PORT'] = '48999'
    os.environ.pop('HEADLESS_BEACON_LISTENER', None)  # assicurati non disabilitato

    settings = NetworkSettings(player_port=8080, ping_interval=1.0)
    reg = PlayerRegistry(settings)
    reg.start()

    # Invio beacon simulato
    payload = {
        'type': 'headless_beacon',
        'version': 'vTEST',
        'name': 'BeaconPlayer',
        'http_port': 8080,
        'ip': '127.0.0.2',  # ip artificiale, listener userà addr fallback se non valido in rete
    }
    data = json.dumps(payload).encode('utf-8')

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.sendto(data, ('255.255.255.255', 48999))
    s.close()

    # Attendi inserimento
    found = None
    deadline = time.time() + 5
    while time.time() < deadline:
        players = reg.current_players()
        for p in players:
            if p.name == 'BeaconPlayer' and p.version == 'vTEST':
                found = p
                break
        if found:
            break
        time.sleep(0.1)
    reg.stop()
    assert found is not None, 'BeaconPlayer non inserito via beacon'
    assert found.discovered_via == 'beacon'
    assert found.state == 'online'
