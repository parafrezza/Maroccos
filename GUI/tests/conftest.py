# Ensure the repository root (containing the 'GUI' package) is on sys.path
import sys, os
from pathlib import Path

# This file lives in GUI/tests/, so repo root is two levels up
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Disabilita discovery di rete nei test (vedi massive_update.discover_players)
os.environ.setdefault("NO_NETWORK_DISCOVERY", "1")
