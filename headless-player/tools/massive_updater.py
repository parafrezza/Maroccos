#!/usr/bin/env python3
"""
Massive Updater per headless-player.

Scopi principali:
 - impacchettare la repo corrente in releases/<version>.zip e aggiornare releases/latest.zip
 - avviare un HTTP server locale che serve l'intera repo (incluso /releases e i media)
 - discovery dei player sulla/e subnet locali e orchestrazione update/download/apply
 - sincronizzazione media (clear + download) e verifica su ciascun device
 - operazioni opzionali: cambio backend video sincronizzato, play sincronizzato

Note progettuali:
 - tutte le chiamate HTTP verso i device sono best-effort, con fallback GET dove possibile
 - il tempo per le azioni sincronizzate usa epoch in secondi calcolato localmente (sincronizzare l'orologio)
 - i test unitari patchano repo_root() per evitare side-effect sulla repo reale
"""

import ipaddress, threading, queue, time, sys, json, re, os, shutil
from urllib.request import urlopen, Request
from pathlib import Path
import zipfile
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler  # <-- corretto

# Dipendenze opzionali con fallback per ambienti di test/minimali
try:
    import netifaces  # type: ignore
except Exception:  # pragma: no cover - fallback per ambienti senza netifaces
    netifaces = None  # type: ignore

try:
    from tqdm import tqdm  # type: ignore
except Exception:  # pragma: no cover - fallback "no-op" progress bar
    def tqdm(*args, **kwargs):  # type: ignore
        class _Bar:
            def __init__(self, *a, **k):
                self.n = 0
            def refresh(self):
                pass
            def write(self, msg):
                try:
                    print(msg)
                except Exception:
                    pass
            def close(self):
                pass
        return _Bar()

PORT_PLAYER = 8080        # porta API dei player
PORT_WEB    = 8000        # porta server HTTP locale per servire /releases
TIMEOUT     = 0.5         # timeout HTTP single-shot discovery
DOWNLOAD_START_TIMEOUT = 3 # timeout avvio download su device
MEDIA_SYNC_INDEX = "media_index.json"  # indice dei media serviti dal server HTTP

# ---------------- Repo / Packaging ----------------

# Escludi ambienti virtuali e cartelle non necessarie dall'update
# Nota: la repo contiene un venv locale (es. "headless_venv") che NON deve essere distribuito sui device.
# Aggiornare i file del venv lato device può fallire per permessi e non è portabile tra OS diversi.
EXCLUDE_PREFIXES = [
    "venv",
    "headless_venv",
    "media",
    ".git",
    "__pycache__",
    "release_tmp",
    "releases",
]
EXCLUDE_FILES    = {"update_pkg.bin"}
EXCLUDE_SUFFIXES = (".log",)

def repo_root() -> Path:
    """Ritorna la directory root della repo headless-player.

    Lo script si trova in headless-player/tools/, quindi la root è il padre.
    I test unitari possono patchare questa funzione per lavorare in sandbox.
    """
    return Path(__file__).resolve().parents[1]

def read_version_file(base: Path) -> str:
    """Legge il contenuto di VERSION dalla root fornita.

    Ritorna una stringa tipo "vMAJ.MIN.PATCH"; fallback "v0.1.0" se mancante.
    """
    vfile = base / "VERSION"
    if not vfile.exists():
        return "v0.1.0"
    return vfile.read_text().strip() or "v0.1.0"

def bump_version_str(v: str) -> str:
    """Incrementa la PATCH in una versione nel formato vMAJ.MIN.PATCH.

    - Se input non valido, ritorna "v0.1.0".
    - Mantiene MAJ/MIN, incrementa PATCH di 1.
    """
    if not v.startswith("v"):
        return "v0.1.0"
    try:
        parts = v[1:].split(".")
        while len(parts) < 3:
            parts.append("0")
        maj, min_, pat = map(int, parts[:3])
        pat += 1
        return f"v{maj}.{min_}.{pat}"
    except Exception:
        return "v0.1.0"

def bump_version_file(base: Path) -> str:
    """Aggiorna il file VERSION nella root indicata incrementando la PATCH.

    Ritorna la nuova versione (stringa).
    """
    cur = read_version_file(base)
    new = bump_version_str(cur)
    (base / "VERSION").write_text(new + "\n")
    return new

def should_exclude(rel: Path) -> bool:
    """Ritorna True se un path relativo della repo deve essere escluso dal pacchetto.

    Esclude:
      - cartelle prefissate in EXCLUDE_PREFIXES
      - file elencati in EXCLUDE_FILES
      - suffissi in EXCLUDE_SUFFIXES
    """
    s = rel.as_posix()
    for p in EXCLUDE_PREFIXES:
        if s == p or s.startswith(p + "/"):
            return True
    if rel.name in EXCLUDE_FILES:
        return True
    for suf in EXCLUDE_SUFFIXES:
        if s.endswith(suf):
            return True
    return False

def make_release_zip(version: str) -> Path:
    """Crea releases/<version>.zip con contenuto della repo e aggiorna releases/latest.zip.

    Scrive sempre un file VERSION all'interno dello zip con la versione passata.
    Rispetta should_exclude, non include la VERSION fisica della repo.
    """
    base = repo_root()
    releases = base / "releases"
    releases.mkdir(exist_ok=True)

    zip_path = releases / f"{version}.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # scrive VERSION (già aggiornata su disco)
        zf.writestr("VERSION", f"{version}\n")
        for path in base.rglob("*"):
            rel = path.relative_to(base)
            if should_exclude(rel):
                continue
            if path.is_dir():
                continue
            # evita di includere la VERSION fisica (abbiamo già scritto la versione aggiornata)
            if rel.as_posix() == "VERSION":
                continue
            zf.write(path, rel.as_posix())

    # aggiorna latest.zip
    latest = releases / "latest.zip"
    try:
        if latest.exists():
            latest.unlink()
        shutil.copy2(zip_path, latest)
    except Exception:
        pass

    print(f"[packaging] Creato: {zip_path}")
    print(f"[packaging] Aggiornato: {latest}")
    return zip_path

# ---------------- HTTP Server (serve l’intera repo) ----------------

class QuietHandler(SimpleHTTPRequestHandler):
    """HTTP handler silenzioso per ridurre il rumore in console durante i test/uso."""
    def log_message(self, fmt, *args):
        # meno rumore in console; togli se vuoi log delle richieste
        pass

def start_http_server(port: int, root: Path):
    """Avvia un HTTP server in background che serve l'intera root (ThreadingHTTPServer)."""
    # serve / (repo root), quindi /releases/latest.zip è raggiungibile
    os.chdir(root)
    server = ThreadingHTTPServer(("0.0.0.0", port), QuietHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server

def build_media_index(base: Path) -> dict:
    """Crea un indice JSON dei file in headless-player/media da servire ai device.

    Scrive MEDIA_SYNC_INDEX nella root e ritorna un dict: {count, items[]}.
    Ogni item ha: path relativo, name, size, mtime, url relativa.
    """
    media_dir = base / "media"
    entries = []
    if media_dir.exists():
        for p in sorted(media_dir.rglob("*")):
            if p.is_file():
                rel = p.relative_to(base).as_posix()
                # escludi placeholder come .gitkeep
                if p.name in {".gitkeep", ".keep"}:
                    continue
                entries.append({
                    "path": rel,
                    "name": p.name,
                    "size": p.stat().st_size,
                    "mtime": int(p.stat().st_mtime),
                    "url": f"/" + rel
                })
    index = {"count": len(entries), "items": entries}
    (base / MEDIA_SYNC_INDEX).write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index

def verify_media(players, index_items: list):
    """Verifica su ciascun device che elenco/size media coincidano con l'indice.

    Stampa differenze (mancanti/extra/size diversi). Non solleva eccezioni.
    """
    ref = {it.get("name"): it.get("size") for it in index_items if it.get("name")}
    for pip, _ in players:
        try:
            st = get(f"http://{pip}:{PORT_PLAYER}/media", timeout=10)
            if not isinstance(st, dict) or not st.get("ok"):
                print(f"{pip} verifica: risposta non valida: {st}")
                continue
            dev = {f.get("name"): f.get("size") for f in st.get("files", [])}
            missing = sorted([n for n in ref.keys() if n not in dev])
            extra = sorted([n for n in dev.keys() if n not in ref])
            mismatched = sorted([n for n in ref.keys() if n in dev and int(ref[n] or 0) != int(dev[n] or 0)])
            if not missing and not extra and not mismatched:
                print(f"{pip} verifica: OK ({len(ref)} file)")
            else:
                print(f"{pip} verifica: DIFFERENZE")
                if missing:
                    print(f"  Mancanti ({len(missing)}): {', '.join(missing[:10])}{'…' if len(missing)>10 else ''}")
                if extra:
                    print(f"  Extra ({len(extra)}): {', '.join(extra[:10])}{'…' if len(extra)>10 else ''}")
                if mismatched:
                    print(f"  Size diversi ({len(mismatched)}): {', '.join(mismatched[:10])}{'…' if len(mismatched)>10 else ''}")
        except Exception as e:
            print(f"{pip} verifica: errore {e}")

def get_local_ip() -> str:
    """Ritorna l'IP locale preferendo una subnet configurabile (default 192.168.1.).
    Variabile env: PREFERRED_NET (es 192.168.1. o 10.0.0.). Fallback: prima interfaccia valida.
    """
    preferred_prefix = os.environ.get("PREFERRED_NET", "192.168.1.")
    candidates = []
    preferred = []
    if netifaces is None:
        # Fallback: senza netifaces ritorna loopback
        return "127.0.0.1"
    for iface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(iface).get(netifaces.AF_INET, [])
        for a in addrs:
            ip = a.get("addr")
            if not ip or ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if ip.startswith(preferred_prefix):
                preferred.append(ip)
            candidates.append(ip)
    if preferred:
        # Se più IP nella subnet, sceglie il primo (potremmo migliorare con ordinamento)
        return preferred[0]
    if candidates:
        return candidates[0]
    return "127.0.0.1"

# ---------------- Discovery / HTTP helpers ----------------

def get_subnets():
    """Ritorna una lista di IPv4Network rilevate dalle interfacce locali.

    Se PREFERRED_DISCOVERY è definita (CIDR), filtra per sovrapposizione con quella subnet.
    """
    subs = []
    if netifaces is None:
        return []
    for iface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(iface).get(netifaces.AF_INET, [])
        for a in addrs:
            ip = a.get('addr'); mask = a.get('netmask')
            if not ip or ip.startswith("127.") or ip.startswith("169.254."):
                continue
            try:
                subs.append(ipaddress.IPv4Network(f"{ip}/{mask}", strict=False))
            except Exception:
                pass
    # Se l'utente vuole forzare la subnet 192.168.1.x, mantieni solo quelle che intersecano 192.168.1.0/24
    preferred_cidr = os.environ.get("PREFERRED_DISCOVERY", "192.168.1.0/24").strip()
    try:
        pref_net = ipaddress.IPv4Network(preferred_cidr, strict=False)
        filtered = [s for s in subs if s.overlaps(pref_net)]
        return filtered or subs
    except Exception:
        return subs

def check_host(ip):
    """Ping logico del player: GET /healthz e parse JSON; None se non risponde."""
    url = f"http://{ip}:{PORT_PLAYER}/healthz"
    try:
        with urlopen(Request(url), timeout=TIMEOUT) as r:
            if r.status == 200:
                return json.loads(r.read().decode())
    except Exception:
        pass
    return None

def scan_subnet(subnet):
    """Scansiona tutti gli host della subnet e ritorna lista [(ip, healthz_json)]."""
    hosts = list(subnet.hosts())
    out = []
    q = queue.Queue()
    for ip in hosts:
        q.put(ip)

    def worker():
        while True:
            try:
                ip = q.get_nowait()
            except queue.Empty:
                return
            res = check_host(str(ip))
            if res:
                out.append((str(ip), res))
            q.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(128)]
    for t in threads: t.start()
    q.join()
    return out

def post(url, data=None, timeout=3):
    """HTTP POST JSON con timeout; ritorna dict se JSON, altrimenti {raw: string}."""
    headers, body = {}, None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")
    with urlopen(Request(url, data=body, headers=headers), timeout=timeout) as r:
        raw = r.read().decode()
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}

def get(url, timeout=3):
    """HTTP GET con timeout; ritorna dict se JSON, altrimenti {raw: string}."""
    with urlopen(Request(url), timeout=timeout) as r:
        raw = r.read().decode()
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}

# ---------------- Main ----------------

def main():
    """Entry point CLI interattivo.

    Flusso: build indice media -> HTTP server -> discovery -> (sync opzionale)
    -> cambio framework opzionale -> download/update orchestrati -> apply ->
    reinstall opzionale -> sync media opzionale -> play sincronizzato opzionale.
    """
    base = repo_root()

    # 1) crea indice media e avvia HTTP server sulla repo (serve anche /releases/latest.zip)
    build_media_index(base)
    start_http_server(PORT_WEB, base)
    ip = get_local_ip()
    update_url = f"http://{ip}:{PORT_WEB}/releases/latest.zip"
    media_index_url = f"http://{ip}:{PORT_WEB}/{MEDIA_SYNC_INDEX}"
    print(f"[server] HTTP attivo su http://0.0.0.0:{PORT_WEB} (root: {base})")
    print(f"[server] URL update per i player: {update_url}\n")

    # 3) discovery players
    print("Rilevo subnet…")
    subs = get_subnets()
    if not subs:
        print("Nessuna subnet valida trovata. Il server HTTP resta attivo. Ctrl+C per uscire.")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            return

    print("Scansiono:")
    for s in subs: print(" -", s)

    players = []
    for s in subs:
        hits = scan_subnet(s)
        players.extend(hits)

    if not players:
        print("\nNessun player trovato. Il server HTTP resta attivo. Ctrl+C per uscire.")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            return

    print("\nPlayers trovati:")
    for i, (pip, info) in enumerate(players, 1):
        print(f"[{i}] {pip}  state={info.get('state')}  version={info.get('version')}")

    # MEDIA SYNC sempre disponibile come prima azione
    did_sync_first = False
    try:
        do_sync_first = input("\nVuoi SINCRONIZZARE le cartelle media subito (anche senza aggiornare software)? [y/N] ").strip().lower()
    except Exception:
        do_sync_first = "n"
    if do_sync_first == "y":
        try:
            idx = get(media_index_url, timeout=5)
            if not isinstance(idx, dict) or "items" not in idx:
                print("Indice media non valido, salto sync.")
            else:
                items = idx.get("items", [])
                print(f"Media da sincronizzare: {len(items)} file")
                # 1) clear media su tutti i device
                for pip, _ in players:
                    try:
                        resp = post(f"http://{pip}:{PORT_PLAYER}/media/clear?confirm=1", timeout=10)
                        print(f"{pip} clear -> {resp}")
                    except Exception:
                        # fallback GET
                        try:
                            resp = get(f"http://{pip}:{PORT_PLAYER}/media/clear?confirm=1", timeout=10)
                            print(f"{pip} clear (GET) -> {resp}")
                        except Exception as e2:
                            print(f"{pip} clear -> errore: {e2} (controlla versione endpoint)")
                # 2) download di tutti i media
                print("\nAvvio download media su tutti i device…")
                bars = {pip: tqdm(total=len(items), desc=f"{pip} media", position=i) for i, (pip, _) in enumerate(players)}
                progress = {pip: 0 for pip, _ in players}
                for it in items:
                    rel_url = it.get("url"); name = it.get("name") or os.path.basename(rel_url or "")
                    if not rel_url:
                        continue
                    file_url = f"http://{ip}:{PORT_WEB}{rel_url}"
                    for pip, _ in players:
                        try:
                            resp = post(f"http://{pip}:{PORT_PLAYER}/download_asset", {"url": file_url, "filename": name}, timeout=20)
                            progress[pip] += 1; bars[pip].n = progress[pip]; bars[pip].refresh()
                        except Exception:
                            try:
                                # fallback GET per device non aggiornati
                                resp = get(f"http://{pip}:{PORT_PLAYER}/download_asset?url={file_url}&filename={name}", timeout=20)
                                progress[pip] += 1; bars[pip].n = progress[pip]; bars[pip].refresh()
                            except Exception as e:
                                bars[pip].write(f"{pip} errore download {name}: {e} (url={file_url})")
                for b in bars.values():
                    b.close()
                print("\nSincronizzazione media completata. Avvio verifica…")
                print("\nVerifica: confronto elenco e dimensioni lato device vs server…")
                verify_media(players, items)
                did_sync_first = True
        except Exception as e:
            print(f"Errore durante media sync: {e}")

    # Opzione cambio framework prima del download
    try:
        do_fw = input("\nVuoi CAMBIARE il framework video sui device prima dell'update? [y/N] ").strip().lower()
    except Exception:
        do_fw = "n"
    if do_fw == "y":
        try:
            # interroga ciascun device per conoscere framework attuale (se endpoint presente)
            current = {}
            for pip, _info in players:
                try:
                    fw = get(f"http://{pip}:{PORT_PLAYER}/framework", timeout=2)
                    current[pip] = fw.get("current") if isinstance(fw, dict) else None
                except Exception:
                    current[pip] = None
            print("Framework correnti:")
            for pip, cur in current.items():
                print(f" - {pip}: {cur}")
            target = input("Nome backend target (gst/vlc/pyqt) oppure vuoto per annullare: ").strip()
            if target:
                # sincronizza via in_time 3 secondi nel futuro
                start_at = time.time() + 3
                for pip, _ in players:
                    try:
                        post(f"http://{pip}:{PORT_PLAYER}/change_framework", {"name": target, "in_time": start_at}, timeout=5)
                    except Exception as e:
                        print(f"{pip} change_framework errore: {e}")
                print(f"Richiesto cambio framework -> {target} (start_at={start_at})")
                # attesa breve
                time.sleep(4)
        except Exception as e:
            print(f"Cambio framework fallito/ignorato: {e}")

    yn = input("\nProcedo con DOWNLOAD dell'update su tutti? [y/N] ").strip().lower()
    if yn != "y":
        print("Server HTTP attivo. Ctrl+C per uscire.")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            return

    # 4) DOWNLOAD update (asincrono)
    print("\nDownload update…")
    # Prepara pacchetto solo se stiamo aggiornando
    new_version = bump_version_file(base)
    make_release_zip(new_version)
    bars = {pip: tqdm(total=100, desc=f"{pip} download", position=i) for i, (pip, _) in enumerate(players)}
    # Calcola un start_at comune pochi secondi nel futuro per allineare il download
    start_at = time.time() + 3.0
    for pip, _ in players:
        try:
            data = {"url": update_url, "version": new_version, "start_at": start_at}
            # Prova JSON
            try:
                resp = post(f"http://{pip}:{PORT_PLAYER}/download_update", data, timeout=DOWNLOAD_START_TIMEOUT)
            except Exception:
                # fallback query params
                resp = get(f"http://{pip}:{PORT_PLAYER}/download_update?url={update_url}&version={new_version}&start_at={start_at}", timeout=DOWNLOAD_START_TIMEOUT)
            if isinstance(resp, dict) and resp.get("status") in ("started", "already-downloading"):
                bars[pip].write(f"{pip} avviato download")
            else:
                bars[pip].write(f"{pip} risposta inattesa: {resp}")
        except Exception as e:
            bars[pip].write(f"{pip} errore avvio download: {e}")

    done = set()
    while len(done) < len(players):
        time.sleep(1.5)
        for pip, _ in players:
            if pip in done: continue
            try:
                st = get(f"http://{pip}:{PORT_PLAYER}/status", timeout=5)
                total = st.get("update_bytes_total") or 0
                done_b = st.get("update_bytes_done") or 0
                status = st.get("update_status")
                if total > 0:
                    pct = int(done_b * 100 / total)
                    bars[pip].n = pct; bars[pip].refresh()
                else:
                    # fallback al campo percentuale se presente oppure lascia lo 0
                    prog = st.get("update_progress")
                    if isinstance(prog, int):
                        bars[pip].n = prog; bars[pip].refresh()
                if status in ("downloaded", "ok", "applying"):
                    bars[pip].n = 100; bars[pip].refresh(); done.add(pip)
                elif status == "error":
                    bars[pip].write(f"{pip} errore download: {st.get('update_error')}")
                    done.add(pip)
            except Exception:
                pass
    for b in bars.values(): b.close()

    yn = input("\nProcedo con APPLY (riavvio servizio) su tutti? [y/N] ").strip().lower()
    if yn != "y":
        print("Server HTTP attivo. Ctrl+C per uscire.")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            return

    # 5) APPLY update (riavvio)
    print("\nApplico update… (il servizio si riavvia, healthz tornerà up)")
    bars = {pip: tqdm(total=100, desc=f"{pip} apply", position=i) for i, (pip, _) in enumerate(players)}
    # Anche l'apply può essere allineata
    start_apply = time.time() + 2.0
    for pip, _ in players:
        try:
            post(f"http://{pip}:{PORT_PLAYER}/update", {"restart": True, "start_at": start_apply}, timeout=5)
        except Exception:
            pass  # può chiudere subito perché il processo termina

    done = set()
    start_t = time.time()
    while len(done) < len(players) and time.time() - start_t < 180:
        time.sleep(2)
        for pip, _ in players:
            if pip in done: continue
            info = check_host(pip)
            if info and info.get("version"):
                bars[pip].n = 100; bars[pip].refresh()
                done.add(pip)
    for b in bars.values(): b.close()

    print("\nAggiornamento completato. Il server HTTP resta attivo per eventuali retry.")
    # Opzione reinstall completa (best-effort): chiede se eseguire uno script di setup sul device
    try:
        reinstall = input("\nVuoi tentare una REINSTALLAZIONE COMPLETA post-update (setup.sh) sui dispositivi? [y/N] ").strip().lower()
    except Exception:
        reinstall = "n"
    if reinstall == "y":
        print("\nTentativo reinstallazione remota: richiederà che lo script setup sia presente sul device e invocabile")
        for pip, _ in players:
            try:
                resp = post(f"http://{pip}:{PORT_PLAYER}/maintenance/run_setup", {"force": True}, timeout=10)
                print(f"{pip} -> {resp}")
            except Exception as e:
                print(f"{pip} -> endpoint non disponibile ({e})")

    # 6) MEDIA SYNC opzionale
    try:
        do_sync = "n" if did_sync_first else input("\nVuoi SINCRONIZZARE le cartelle media su tutti i device (cancella e riscarica dal server)? [y/N] ").strip().lower()
    except Exception:
        do_sync = "n"
    if do_sync == "y":
        # Scarica indice dal server (dal nostro file appena creato)
        try:
            idx = get(media_index_url, timeout=5)
            if not isinstance(idx, dict) or "items" not in idx:
                print("Indice media non valido, salto sync.")
            else:
                items = idx.get("items", [])
                print(f"Media da sincronizzare: {len(items)} file")
                # 6.1 svuota media su tutti
                for pip, _ in players:
                    try:
                        resp = post(f"http://{pip}:{PORT_PLAYER}/media/clear?confirm=1", timeout=10)
                        print(f"{pip} clear -> {resp}")
                    except Exception:
                        try:
                            resp = get(f"http://{pip}:{PORT_PLAYER}/media/clear?confirm=1", timeout=10)
                            print(f"{pip} clear (GET) -> {resp}")
                        except Exception as e:
                            print(f"{pip} clear -> errore: {e}")
                # 6.2 invia download di tutti i media
                print("\nAvvio download media su tutti i device…")
                bars = {pip: tqdm(total=len(items), desc=f"{pip} media", position=i) for i, (pip, _) in enumerate(players)}
                progress = {pip: 0 for pip, _ in players}
                for it in items:
                    rel_url = it.get("url")
                    name = it.get("name") or os.path.basename(rel_url or "")
                    if not rel_url:
                        continue
                    file_url = f"http://{ip}:{PORT_WEB}{rel_url}"
                    for pip, _ in players:
                        try:
                            resp = post(f"http://{pip}:{PORT_PLAYER}/download_asset", {"url": file_url, "filename": name}, timeout=20)
                            progress[pip] += 1
                            bars[pip].n = progress[pip]; bars[pip].refresh()
                        except Exception:
                            try:
                                resp = get(f"http://{pip}:{PORT_PLAYER}/download_asset?url={file_url}&filename={name}", timeout=20)
                                progress[pip] += 1
                                bars[pip].n = progress[pip]; bars[pip].refresh()
                            except Exception as e:
                                bars[pip].write(f"{pip} errore download {name}: {e}")
                for b in bars.values(): b.close()
                print("\nSincronizzazione media completata. Avvio verifica…")
                verify_media(players, items)
        except Exception as e:
            print(f"Errore durante media sync: {e}")

    print("\nPremi Ctrl+C per uscire…")
    # Opportunità: play sincronizzato
    try:
        do_play = input("\nVuoi avviare un PLAY sincronizzato su tutti i device? [y/N] ").strip().lower()
    except Exception:
        do_play = "n"
    if do_play == "y":
        try:
            fname = input("Nome file (vuoto=usa playlist/auto): ").strip()
            delay = input("Delay secondi (default 3): ").strip()
            delay_f = 3.0
            if delay:
                try: delay_f = float(delay)
                except Exception: pass
            start_at = time.time() + delay_f
            body = {"in_time": start_at}
            if fname:
                body["filename"] = fname
            for pip, _ in players:
                try:
                    post(f"http://{pip}:{PORT_PLAYER}/play", body, timeout=5)
                except Exception as e:
                    print(f"{pip} play errore: {e}")
            print(f"Play sincronizzato inviato (start_at={start_at})")
        except Exception as e:
            print(f"Play sincronizzato fallito: {e}")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("Bye.")

if __name__ == "__main__":
    main()

# .\.venv\Scripts\activate