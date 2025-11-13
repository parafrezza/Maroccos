from __future__ import annotations

import os, time
import pytest

pytest.importorskip("PySide6", reason="PySide6 non disponibile nell'ambiente di test corrente")

# Assicura flag attivo
os.environ.setdefault("NO_NETWORK_DISCOVERY", "1")

import sys
from pathlib import Path
# Aggiungi repo root al path (due livelli su da GUI/tests)
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import massive_update  # type: ignore


def test_discovery_disabled_returns_empty_list():
    # Simula un set di IP numeroso: deve ritornare subito [] senza aspettare.
    ips = [f"192.168.0.{i}" for i in range(1, 500)]
    t0 = time.time()
    res = massive_update.discover_players(ips, port=8080, timeout=0.2, threads=50, api_key=None, verbose=False)
    elapsed = time.time() - t0
    assert res == []
    # Verifica che abbia impiegato pochissimo (<< 1s)
    assert elapsed < 0.2, f"Discovery disabilitata ha impiegato troppo: {elapsed:.2f}s"
