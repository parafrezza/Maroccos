import os
import sys
import io
import tarfile
import zipfile
import pytest

# Assicura che "headless-player" sia sul path
THIS_DIR = os.path.dirname(__file__)
PROJ_DIR = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
sys.path.insert(0, PROJ_DIR)

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # type: ignore

# Importa l'app FastAPI
from app import app as fastapi_app  # type: ignore


def _sniff_and_validate_archive(body: bytes):
    assert len(body) > 16, "Archivio vuoto o troppo piccolo"
    if body[:2] == b"\x1f\x8b":  # gzip
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as t:
            members = t.getmembers()
            assert len(members) > 0, "Archivio tar.gz senza contenuti"
    elif body[:4] == b"PK\x03\x04":  # zip
        with zipfile.ZipFile(io.BytesIO(body), mode="r") as z:
            names = z.namelist()
            assert len(names) > 0, "Archivio zip senza contenuti"
    else:
        pytest.fail("Formato archivio sconosciuto")


def test_packages_targets():
    client = TestClient(fastapi_app)
    r = client.get("/packages/targets")
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert isinstance(j.get("targets"), list) and len(j["targets"]) >= 3
    assert isinstance(j.get("recommended"), str)


def test_system_os():
    client = TestClient(fastapi_app)
    r = client.get("/system/os")
    assert r.status_code == 200
    j = r.json()
    # Campi minimi attesi
    for k in ("system", "release", "machine"):
        assert k in j


def test_off_archive_download():
    client = TestClient(fastapi_app)
    r = client.get("/off/archive")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "").lower()
    body = r.content
    _sniff_and_validate_archive(body)
