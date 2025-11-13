#!/usr/bin/env python3
import sys
import os
import io
import time
import json
import socket
import tarfile
import zipfile
from contextlib import closing

BASE_HTTP = "http://127.0.0.1:8080"


def _port_open(host: str, port: int, timeout=0.3) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _http_get(path: str, timeout=5):
    import urllib.request
    req = urllib.request.Request(BASE_HTTP + path, headers={"User-Agent": "smoke-test/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), resp.headers, resp.read()


def _sniff_and_validate_archive(body: bytes):
    if len(body) < 16:
        raise AssertionError("Archivio vuoto o troppo piccolo")
    if body[:2] == b"\x1f\x8b":  # gzip
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as t:
            members = t.getmembers()
            if not members:
                raise AssertionError("Archivio tar.gz senza contenuti")
    elif body[:4] == b"PK\x03\x04":  # zip
        with zipfile.ZipFile(io.BytesIO(body), mode="r") as z:
            names = z.namelist()
            if not names:
                raise AssertionError("Archivio zip senza contenuti")
    else:
        raise AssertionError("Formato archivio sconosciuto")


def run_http_smoke():
    print("[HTTP] Testing against", BASE_HTTP)
    # /packages/targets
    code, headers, body = _http_get("/packages/targets")
    assert code == 200, f"packages/targets status {code}"
    j = json.loads(body.decode("utf-8"))
    assert j.get("ok") is True and isinstance(j.get("targets"), list) and j.get("recommended"), "packages/targets payload non valido"

    # /system/os
    code, headers, body = _http_get("/system/os")
    assert code == 200, f"system/os status {code}"
    j = json.loads(body.decode("utf-8"))
    for k in ("system", "release", "machine"):
        assert k in j, f"system/os payload manca {k}"

    # /off/archive
    code, headers, body = _http_get("/off/archive")
    assert code == 200, f"off/archive status {code}"
    cd = headers.get("Content-Disposition", headers.get("content-disposition", ""))
    assert "attachment" in cd.lower(), "off/archive: manca content-disposition attachment"
    _sniff_and_validate_archive(body)



def run_inprocess_smoke():
    print("[LOCAL] Testing in-process via TestClient")
    # Aggiunge headless-player al path per import app.py
    THIS = os.path.dirname(__file__)
    ROOT = os.path.abspath(os.path.join(THIS, os.pardir))
    sys.path.insert(0, ROOT)

    try:
        from fastapi.testclient import TestClient  # type: ignore
    except Exception as e:
        raise RuntimeError("FastAPI TestClient non disponibile: installa fastapi[all]") from e

    from app import app as fastapi_app  # type: ignore

    client = TestClient(fastapi_app)

    r = client.get("/packages/targets")
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True and isinstance(j.get("targets"), list) and j.get("recommended")

    r = client.get("/system/os")
    assert r.status_code == 200
    j = r.json()
    for k in ("system", "release", "machine"):
        assert k in j

    r = client.get("/off/archive")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "").lower()
    _sniff_and_validate_archive(r.content)


if __name__ == "__main__":
    t0 = time.time()
    try:
        if _port_open("127.0.0.1", 8080):
            try:
                run_http_smoke()
            except Exception as e:
                print("[HTTP] fallback to LOCAL:", e)
                run_inprocess_smoke()
        else:
            run_inprocess_smoke()
        print("SMOKE: PASS in %.2fs" % (time.time() - t0))
        sys.exit(0)
    except Exception as e:
        print("SMOKE: FAIL", e)
        sys.exit(1)
