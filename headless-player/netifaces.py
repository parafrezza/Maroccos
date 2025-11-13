"""
Compatibility shim per `netifaces` che su Windows usa `psutil`.

Espone l'API minima usata dal progetto:
- AF_INET
- interfaces() -> list[str]
- ifaddresses(iface) -> dict[int, list[dict]]

Se è installato il vero pacchetto `netifaces` (site-packages) e NON stiamo
importando questo file stesso, allora re-esportiamo il reale, altrimenti
forniamo l'implementazione basata su psutil.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import socket

_THIS_FILE = Path(__file__).resolve()
_spec = importlib.util.find_spec("netifaces")

if _spec is not None and getattr(_spec, "origin", None):
    try:
        origin_path = Path(_spec.origin).resolve()
    except Exception:  # path non risolvibile, usa shim
        origin_path = _THIS_FILE
else:
    origin_path = _THIS_FILE

if origin_path != _THIS_FILE:
    # Esiste un netifaces reale diverso da questo file: re-esporta quello
    import importlib

    _real = importlib.import_module("netifaces")
    AF_INET = _real.AF_INET  # type: ignore[attr-defined]

    def interfaces():  # type: ignore[override]
        return _real.interfaces()  # type: ignore[attr-defined]

    def ifaddresses(iface: str):  # type: ignore[override]
        return _real.ifaddresses(iface)  # type: ignore[attr-defined]

else:
    # Shim basato su psutil
    try:
        import psutil  # type: ignore
    except Exception:  # pragma: no cover - surfaced a runtime se manca
        psutil = None  # type: ignore

    AF_INET = socket.AF_INET

    def interfaces() -> list[str]:
        """Ritorna la lista dei nomi interfaccia."""
        if psutil is None:  # type: ignore
            return []
        return list(psutil.net_if_addrs().keys())  # type: ignore[attr-defined]

    def ifaddresses(iface: str) -> dict[int, list[dict]]:
        """Ritorna una mappa simile a netifaces.ifaddresses.

        Chiavi: famiglia (es. socket.AF_INET). Valori: lista di dict con almeno 'addr'.
        Per AF_INET include anche 'netmask' e 'broadcast' se disponibili.
        """
        result: dict[int, list[dict]] = {}
        if psutil is None:  # type: ignore
            return result
        addrs = psutil.net_if_addrs().get(iface, [])  # type: ignore[attr-defined]
        for a in addrs:
            fam = a.family
            lst = result.setdefault(fam, [])
            if fam == socket.AF_INET:
                lst.append(
                    {
                        "addr": a.address,
                        "netmask": getattr(a, "netmask", None),
                        "broadcast": getattr(a, "broadcast", None),
                    }
                )
            else:
                lst.append({"addr": a.address})
        return result

__all__ = ["AF_INET", "interfaces", "ifaddresses"]

