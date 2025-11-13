import json
import importlib.util
import sys
from pathlib import Path


def _import_mu() -> object:
    """Importa il modulo massive_updater dal percorso locale (senza richiedere pacchetti)."""
    repo_root = Path(__file__).resolve().parents[1]
    mod_path = repo_root / "tools" / "massive_updater.py"
    spec = importlib.util.spec_from_file_location("massive_updater", str(mod_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["massive_updater"] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod

mu = _import_mu()


def test_bump_version_str_happy():
    assert mu.bump_version_str("v1.2.3") == "v1.2.4"
    assert mu.bump_version_str("v0.0.9") == "v0.0.10"


def test_bump_version_str_invalid():
    assert mu.bump_version_str("1.2.3") == "v0.1.0"
    assert mu.bump_version_str("vX.Y.Z").startswith("v0.")


def test_should_exclude_rules():
    assert mu.should_exclude(Path("venv"))
    assert mu.should_exclude(Path("venv/site-packages/foo"))
    assert mu.should_exclude(Path("__pycache__/a.pyc"))
    assert mu.should_exclude(Path("something.log"))
    assert not mu.should_exclude(Path("src/app.py"))


def test_make_release_zip(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "releases").mkdir(parents=True)
    (root / "media").mkdir(parents=True)
    (root / "VERSION").write_text("v0.1.0\n")
    (root / "app.py").write_text("print('x')\n")
    monkeypatch.setattr(mu, "repo_root", lambda: root)
    zpath = mu.make_release_zip("v0.1.1")
    assert zpath.exists()
    assert (root / "releases" / "latest.zip").exists()


def test_build_media_index(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "media").mkdir(parents=True)
    (root / "media" / "video.mp4").write_text("bin")
    (root / "media" / ".gitkeep").write_text("")
    monkeypatch.setattr(mu, "repo_root", lambda: root)
    idx = mu.build_media_index(root)
    assert isinstance(idx, dict)
    assert idx.get("count") == 1
    assert (root / mu.MEDIA_SYNC_INDEX).exists()


def test_get_local_ip_format():
    ip = mu.get_local_ip()
    assert isinstance(ip, str)
    assert len(ip.split(".")) == 4


def test_start_http_server(tmp_path):
    # Avvia server su porta effimera (0) e poi spegne
    srv = mu.start_http_server(0, tmp_path)
    assert srv is not None
    srv.shutdown()
