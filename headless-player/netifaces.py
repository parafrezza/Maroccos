"""
Compatibility shim for `netifaces` that falls back to using `psutil` on platforms
where the native `netifaces` wheel is not available (Windows in our project case).

This module is placed in the package directory so that `import netifaces` will
prefer this shim when a system/site package is not available or when we want to
use a psutil-based implementation on Windows.

Provides the minimal API used by the project:
- AF_INET constant
- interfaces() -> list[str]
- ifaddresses(iface) -> dict[int, list[dict]]

If a real `netifaces` package is installed, this shim will re-export it.
"""

try:
    # If a real netifaces is installed, prefer it
    import netifaces as _real_netifaces
    # Re-export everything for compatibility
    from netifaces import *  # noqa: F401,F403
except Exception:
    import socket
    import sys
    try:
        import psutil
    except Exception:  # pragma: no cover - will surface at runtime if psutil missing
        psutil = None

    AF_INET = socket.AF_INET

    def interfaces():
        """Return a list of network interface names."""
        if psutil is None:
            return []
        return list(psutil.net_if_addrs().keys())

    def ifaddresses(iface: str):
        """Return a mapping similar to netifaces.ifaddresses.

        The mapping keys are address family integers (e.g. socket.AF_INET) and
        the values are lists of dicts with at least the 'addr' key. For AF_INET
        entries we also include 'netmask' and 'broadcast' when available.
        """
        result = {}
        if psutil is None:
            return result
        addrs = psutil.net_if_addrs().get(iface, [])
        for a in addrs:
            fam = a.family
            lst = result.setdefault(fam, [])
            if fam == socket.AF_INET:
                lst.append({
                    "addr": a.address,
                    "netmask": getattr(a, "netmask", None),
                    "broadcast": getattr(a, "broadcast", None),
                })
            else:
                lst.append({"addr": a.address})

    # Provide a small subset of helper names used by callers
    __all__ = ["AF_INET", "interfaces", "ifaddresses"]

# End of shim
