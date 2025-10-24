#!/usr/bin/env python3
import os, shutil, tempfile, time, zipfile, hashlib
from pathlib import Path

# Cartelle / pattern esclusi
# Escludi ambienti virtuali locali e cartelle non necessarie dal bundle
EXCLUDED_DIRS     = {'.git', '__pycache__', 'media', 'releases', 'docs', 'doc', 'headless_venv', 'venv'}
EXCLUDED_FILES    = {'make_bundle.py'}
EXCLUDED_SUFFIXES = {'.md', '.MD', '.markdown'}  # documentazione

OUTPUT_DIR  = 'releases'
APP_NAME    = 'headless-player'
ENTRY_FILE  = 'app.py'
PY_MIN      = '3.9'
LATEST_LINK = 'latest'
HASH_LEN_IN_NAME = 64   # usa hash completo per integrità


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(dst_dir: Path, version: str, sha256: str):
    # Usa ora locale per built_at per coerenza con la versione
    manifest = (
        f"version={version}\n"
        f"built_at={time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime())}\n"
        f"app={APP_NAME}\n"
        f"entry={ENTRY_FILE}\n"
        f"python_min={PY_MIN}\n"
        f"sha256={sha256}\n"
    )
    (dst_dir / 'manifest.txt').write_text(manifest, encoding='utf-8')


def should_exclude(path: Path) -> bool:
    name = path.name
    if path.is_dir() and name in EXCLUDED_DIRS:
        return True
    if path.is_file():
        if name in EXCLUDED_FILES:
            return True
        if path.suffix in EXCLUDED_SUFFIXES:
            return True
    return False


def collect_sources(src_root: Path, dst_root: Path):
    for item in src_root.iterdir():
        if should_exclude(item):
            continue
        if item.is_dir():
            rel_dir = dst_root / item.name
            rel_dir.mkdir(parents=True, exist_ok=True)
            for root, dirs, files in os.walk(item):
                root_p = Path(root)
                dirs[:] = [d for d in dirs if not should_exclude(root_p / d)]
                for fn in files:
                    src_f = root_p / fn
                    if should_exclude(src_f):
                        continue
                    dst_f = dst_root / src_f.relative_to(src_root)
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_f, dst_f)
        else:
            try:
                shutil.copy(item, dst_root / item.name)
            except OSError as e:
                print(f"Errore durante la copia di {item}: {e}")


def include_default_media(app_root: Path, dst_root: Path):
    """Include in bundle i file neri di default se presenti in media/ (nomi supportati)."""
    try:
        candidates = [
            app_root / 'media' / 'black_1280_720.png',
            app_root / 'media' / 'black.png',
        ]
        dst_media = dst_root / 'media'
        copied = False
        for src in candidates:
            if src.is_file():
                dst_media.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst_media / src.name)
                print(f"[BUNDLE] Incluso {src.relative_to(app_root)}")
                copied = True
        if not copied:
            print("[BUNDLE] Nessun file nero default trovato in media/")
    except Exception as e:
        print(f"[BUNDLE] Impossibile includere file neri: {e}")


def _bump_semver(txt: str) -> str | None:
    if not txt.startswith('v'):
        return None
    try:
        parts = txt[1:].split('.')
        while len(parts) < 3:
            parts.append('0')
        maj, minor, patch = map(int, parts[:3])
        patch += 1
        return f"v{maj}.{minor}.{patch}"
    except Exception:
        return None


def auto_version(base_dir: Path | None = None, *, bump: bool = True) -> str:
    base_dir = base_dir or Path('.')
    version_file = base_dir / 'VERSION'
    if version_file.is_file():
        txt = version_file.read_text(encoding='utf-8').strip()
        if txt:
            if bump:
                next_ver = _bump_semver(txt)
                if next_ver:
                    version_file.write_text(next_ver + '\n', encoding='utf-8')
                    print(f"[VERSION] Incremento automatico: {txt} -> {next_ver}")
                    return next_ver
                generated = time.strftime('%Y.%m.%d-%H%M%S', time.localtime())
                version_file.write_text(generated + '\n', encoding='utf-8')
                print(f"[VERSION] Contenuto VERSION non valido '{txt}', uso {generated}")
                return generated
            return txt
    # Ora locale invece di UTC
    generated = time.strftime('%Y.%m.%d-%H%M%S', time.localtime())
    if bump and version_file.parent.is_dir():
        version_file.write_text(generated + '\n', encoding='utf-8')
        print(f"[VERSION] VERSION mancante: scritto {generated}")
    return generated


def create_zip_with_hash(src_root: Path, bundle_name: str, version: str, out_dir: Path) -> Path:
    """Crea zip senza hash, calcola hash, rinomina includendo hash."""
    tmp_zip = out_dir / f"{bundle_name}_temp.zip"
    with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(src_root):
            root_p = Path(root)
            for fn in files:
                abspath = root_p / fn
                rel = abspath.relative_to(src_root.parent)
                z.write(abspath, arcname=str(rel))
    digest = sha256_of(tmp_zip)
    short = digest[:HASH_LEN_IN_NAME]
    final_zip = out_dir / f"bundle_v{version}_{short}.zip"
    tmp_zip.replace(final_zip)
    return final_zip, digest


def update_latest_pointer(out_dir: Path, target_zip: Path):
    link_path = out_dir / LATEST_LINK
    try:
        if link_path.is_symlink() or link_path.exists():
            if link_path.is_dir() and not link_path.is_symlink():
                shutil.rmtree(link_path)
            else:
                link_path.unlink()
        link_path.symlink_to(target_zip.name)
    except Exception:
        # fallback: copia file come latest.zip
        shutil.copy2(target_zip, out_dir / 'latest.zip')


def main():
    script_root = Path(__file__).resolve().parent  # directory dove risiede lo script (root progetto)
    app_root = script_root / APP_NAME              # cartella reale dell'app
    if not app_root.is_dir():
        raise SystemExit(f"Cartella applicazione non trovata: {app_root}")

    version = auto_version(app_root)
    out_dir = app_root / OUTPUT_DIR
    out_dir.mkdir(exist_ok=True, parents=True)

    bundle_folder_name = f"{APP_NAME}-v{version}"
    tmpdir = Path(tempfile.mkdtemp())
    try:
        dst_root = tmpdir / bundle_folder_name
        dst_root.mkdir(parents=True)

        # Colleziona SOLO i sorgenti dentro la cartella applicazione
        collect_sources(app_root, dst_root)
        # Include selettivamente asset media di default (nero)
        include_default_media(app_root, dst_root)
        # manifest scritto DOPO che calcoliamo hash dell'archivio; quindi prima creiamo zip, poi calcoliamo hash, poi aggiorniamo manifest e rigeneriamo.
        # Per includere hash nel manifest dobbiamo: creare zip SENZA manifest, calcolare hash? -> hash cambierebbe dopo manifest.
        # Strategia: scrivere manifest provvisorio senza sha, creare zip, calcolare hash contenuti (manifest privo hash), riscrivere manifest con sha, ricreare zip.
        # 1. manifest placeholder
        write_manifest(dst_root, version, sha256='pending')

        # 2. primo zip per hash contenuti senza campo sha reale
        first_zip, _ = create_zip_with_hash(dst_root, bundle_folder_name, version, out_dir)
        # Calcoliamo hash dei contenuti (escludendo che cambi dopo). Poiché rimettiamo hash nel manifest, rigeneriamo lo zip finale.
        # Riapriamo per estrarre hash dei file (già fatto). Usiamo hash del primo zip come hash contenuti.
        content_hash = sha256_of(first_zip)
        # Rimuoviamo primo zip (lo rigeneriamo con manifest completo)
        first_zip.unlink(missing_ok=True)

        # 3. Aggiorna manifest con hash definitivo
        (dst_root / 'manifest.txt').unlink(missing_ok=True)
        write_manifest(dst_root, version, sha256=content_hash)

        # 4. Crea zip finale e calcola hash definitivo (che includerà ora il campo sha256 nel manifest). Usiamo questo hash per il nome.
        final_zip, final_hash = create_zip_with_hash(dst_root, bundle_folder_name, version, out_dir)

        update_latest_pointer(out_dir, final_zip)

        print(f"Creato: {final_zip}")
        print(f"SHA256 (zip): {final_hash}")
        print(f"Manifest sha256 (contenuti): {content_hash}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    main()