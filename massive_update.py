#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aggiornamento massivo dei player:
 1. (Default) Genera l'ultimo bundle con make_bundle.py, altrimenti usa uno zip esistente o un URL remoto.
 2. Scopre i player (/status) o usa target forniti.
 3. Mostra elenco player con versione attuale (/version -> manifest).
 4. Chiede conferma e applica update via /admin/update/apply.
 5. (Opzionale) Verifica post-restart che la versione corrisponda.

Uso basico:
    python massive_update.py
Opzioni principali:
    --port-player 8081             Porta HTTP dei player.
    --scan 192.168.1.0/24,...      Reti da scandire (default: autodetect + reti comuni)
    --api-key XYZ                  API key per endpoint admin/* (se configurata nei player)
    --serve-port 8000              Porta HTTP locale per esporre il bundle (se non si passa --bundle-url)
    --timeout 0.35                 Timeout richieste status/version
    --threads 80                   Concorrenza scan
    --no-scan                      Salta discovery (usa --targets)
    --targets ip1,ip2,...          Lista IP manuale
    --auto-yes                     Non chiedere conferma
    --no-build                     Non eseguire make_bundle.py (usa zip esistente)
    --bundle-path PATH             Percorso zip bundle locale da usare (salta build)
    --bundle-url  URL              URL remoto del bundle (salta build e file server locale)
    --verify                       Dopo l'apply, verifica che ogni player riporti la nuova versione
    --verify-timeout 120           Tempo max (s) per la verifica per player

Richiede 'requests'.
"""

import argparse, concurrent.futures, ipaddress, json, os, queue, re, socket, subprocess, sys, threading, time, hashlib
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import List, Dict, Optional

try:
    import requests
except ImportError:
    print("[ERRORE] Richiede il pacchetto 'requests'. Installare e riprovare.")
    sys.exit(2)

# SSH opzionale per fix permessi lato device (fallback automatico)
try:
    import paramiko  # type: ignore
except Exception:
    paramiko = None  # type: ignore

SCRIPT_ROOT = Path(__file__).resolve().parent
APP_DIR = SCRIPT_ROOT / 'headless-player'
RELEASES_DIR = APP_DIR / 'releases'
MAKE_BUNDLE = SCRIPT_ROOT / 'make_bundle.py'

LOG_LOCK = threading.Lock()

def log(msg: str):
    with LOG_LOCK:
        ts = time.strftime('%H:%M:%S')
        print(f"[{ts}] {msg}")

class BundleResult:
    def __init__(self):
        self.done = threading.Event()
        self.zip_path: Optional[Path] = None
        self.zip_sha: Optional[str] = None
        self.version: Optional[str] = None
        self.error: Optional[str] = None

# --- Bundle builder thread ---

def build_bundle_async(res: BundleResult):
    if not MAKE_BUNDLE.is_file():
        res.error = f"make_bundle.py non trovato: {MAKE_BUNDLE}"
        res.done.set(); return
    cmd = [sys.executable, str(MAKE_BUNDLE)]
    log(f"Avvio build bundle: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True, cwd=str(SCRIPT_ROOT))
        pattern_created = re.compile(r'^Creato: (.+)$')
        pattern_sha = re.compile(r'^SHA256 \(zip\): ([0-9a-fA-F]{64})$')
        while True:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.01)
                continue
            line = line.rstrip('\n')
            log(f"BUNDLE> {line}")
            m1 = pattern_created.search(line)
            if m1:
                p = Path(m1.group(1).strip())
                if p.is_file():
                    res.zip_path = p
                    # estrai versione dal nome bundle_v<VER>_<hash>.zip
                    mver = re.search(r"bundle_v([0-9A-Za-z_.:-]+)_", p.name)
                    if mver:
                        res.version = mver.group(1)
            m2 = pattern_sha.search(line)
            if m2:
                res.zip_sha = m2.group(1).lower()
        rc = proc.wait()
        if rc != 0 and not res.error:
            res.error = f"make_bundle.py exit code {rc}"
    except Exception as e:
        res.error = str(e)
    finally:
        res.done.set()
        log("Build bundle terminata")

def find_latest_bundle() -> Optional[Path]:
    if RELEASES_DIR.is_dir():
        zips = sorted(RELEASES_DIR.glob('bundle_v*_*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)
        return zips[0] if zips else None
    return None

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

# --- Network helpers ---

def local_primary_ip() -> str:
    # Metodo affidabile: socket UDP verso internet (non esegue traffico reale oltre handshake di routing)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def derive_default_networks() -> List[str]:
    ip = local_primary_ip()
    nets = set()
    try:
        parts = ip.split('.')
        if len(parts) == 4:
            nets.add(f"{parts[0]}.{parts[1]}.{parts[2]}.0/24")
    except Exception:
        pass
    # Aggiungi reti comuni se l'IP locale è loopback
    nets.update(['192.168.0.0/24','192.168.1.0/24','10.0.0.0/24','172.16.0.0/24'])
    return sorted(nets)


def expand_networks(nets: List[str]) -> List[str]:
    hosts: List[str] = []
    for n in nets:
        try:
            net = ipaddress.ip_network(n, strict=False)
            log(f"Espandendo rete {n} -> {net} ({net.num_addresses} indirizzi)")
            
            # Limita /16 o più grandi (troppo) – taglia a primi 512 host
            if net.num_addresses > 512:
                log(f"ATTENZIONE: Rete {n} troppo grande ({net.num_addresses} IP), limitando a 512")
                count = 0
                for h in net.hosts():
                    hosts.append(str(h))
                    count += 1
                    if count >= 512:
                        break
            else:
                hosts.extend(str(h) for h in net.hosts())
        except Exception as e:
            log(f"ERRORE: Rete invalida '{n}': {e}")
    return hosts

# --- Discovery ---

def probe_player(ip: str, port: int, timeout: float, api_key: Optional[str], verbose: bool = False):
    base = f"http://{ip}:{port}"
    headers = {}
    if api_key:
        headers['X-API-KEY'] = api_key  # per /admin/* servirà, /status no
    url_status = f"{base}/status"
    
    if verbose:
        log(f"DEBUG: Probing {ip}:{port}/status...")
    
    try:
        r = requests.get(url_status, timeout=timeout)
        if verbose:
            log(f"DEBUG: {ip} response status: {r.status_code}")
        
        if r.status_code != 200:
            if verbose:
                log(f"DEBUG: {ip} returned HTTP {r.status_code}")
            return None
            
        try:
            js = r.json()
        except Exception as e:
            if verbose:
                log(f"DEBUG: {ip} invalid JSON response: {e}")
            return None
            
        # Controlla se è un player valido: o ha 'ok': true, o ha i campi caratteristici del player
        is_valid_player = js.get('ok') or ('version_current' in js and 'player_state' in js)
        
        if not is_valid_player:
            if verbose:
                log(f"DEBUG: {ip} non è un player valido, contenuto JSON: {js}")
            return None
            
        if verbose and 'version_current' in js:
            log(f"DEBUG: {ip} è un player valido - versione: {js.get('version_current')}, stato: {js.get('player_state')}")
            
        if verbose:
            log(f"DEBUG: {ip} status OK, fetching version...")
            
        # prova recupero versione: prima da status, poi da /version
        ver = js.get('version_current')  # nuovo: prova dal campo status
        
        if not ver:
            try:
                rv = requests.get(f"{base}/version", timeout=timeout)
                if rv.ok:
                    txt = rv.json().get('manifest')
                    if txt:
                        for line in txt.splitlines():
                            if line.startswith('version='):
                                ver = line.split('=',1)[1].strip(); break
                                
                if verbose:
                    log(f"DEBUG: {ip} version da /version endpoint: {ver}")
            except Exception as e:
                if verbose:
                    log(f"DEBUG: {ip} error getting version da /version: {e}")
                pass
        elif verbose:
            log(f"DEBUG: {ip} version da status: {ver}")
            
        return {
            'ip': ip,
            'port': port,
            'status': js,
            'version': ver
        }
    except requests.exceptions.ConnectTimeout:
        if verbose:
            log(f"DEBUG: {ip} connection timeout ({timeout}s)")
        return None
    except requests.exceptions.ConnectionError as e:
        if verbose:
            log(f"DEBUG: {ip} connection error: {e}")
        return None
    except Exception as e:
        if verbose:
            log(f"DEBUG: {ip} unexpected error: {type(e).__name__}: {e}")
        return None


def discover_players(ips: List[str], port: int, timeout: float, threads: int, api_key: Optional[str], verbose: bool = False):
    log(f"Inizio discovery su {len(ips)} indirizzi (porta {port}) threads={threads} timeout={timeout}s")
    if verbose:
        log(f"DEBUG: Reti da scansionare: {ips[:10]}{'...' if len(ips) > 10 else ''}")
        log(f"DEBUG: Usando {threads} thread con timeout {timeout}s per richiesta")
    
    found = []
    errors = {'timeout': 0, 'connection': 0, 'http_error': 0, 'other': 0}
    t0 = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(probe_player, ip, port, timeout, api_key, verbose and len(ips) <= 20): ip for ip in ips}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            done += 1
            if done % 50 == 0 or (verbose and done % 10 == 0):
                log(f"Scan progresso: {done}/{len(ips)} - Trovati: {len(found)}")
            
            res = fut.result()
            if res:
                log(f"✓ Trovato player {res['ip']} versione={res['version']}")
                found.append(res)
            elif verbose and len(ips) <= 50:
                ip = futs[fut]
                # Non possiamo determinare il tipo di errore qui, ma lo facciamo nella probe_player
                
    elapsed = time.time() - t0
    log(f"Discovery completata: {len(found)} player trovati in {elapsed:.1f}s")
    log(f"Statistiche: {len(found)} successi, {len(ips) - len(found)} fallimenti su {len(ips)} tentativi")
    
    if verbose:
        log(f"DEBUG: Velocità media: {len(ips)/elapsed:.1f} IP/s")
        
    return found

# --- HTTP server per servire il bundle ---

class _SilentHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        log(f"SERVE> {self.address_string()} {format%args}")


def start_file_server(directory: Path, port: int):
    os.chdir(str(directory))
    srv = ThreadingHTTPServer(('0.0.0.0', port), _SilentHTTPRequestHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    log(f"File server attivo su 0.0.0.0:{port} dir={directory}")
    return srv

# --- Update massivo ---

def apply_update(players: List[Dict], version: str, url: str, sha: Optional[str], api_key: Optional[str], timeout=3.0, verbose: bool = False):
    results = []
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['X-API-KEY'] = api_key
    
    log(f"Avvio update a 2 fasi per {len(players)} player (version={version})")
    log(f"FASE 1: Download del bundle da {url}")
    
    # FASE 1: Download del bundle
    download_payload = {'version': version, 'url': url}
    if sha:
        download_payload['sha256'] = sha
    
    if verbose:
        log(f"DEBUG UPDATE: Headers: {headers}")
        log(f"DEBUG UPDATE: Download payload: {download_payload}")
    
    for p in players:
        ip = p['ip']; port = p['port']
        download_endpoint = f"http://{ip}:{port}/download_update"
        
        if verbose:
            log(f"DEBUG UPDATE: FASE 1 - Chiamando {download_endpoint}")
            
        try:
            # FASE 1: Download
            r = requests.post(download_endpoint, headers=headers, data=json.dumps(download_payload), timeout=timeout*2)  # timeout più lungo per download
            
            if verbose:
                log(f"DEBUG UPDATE: {ip} download response status: {r.status_code}")
                
            try:
                response_json = r.json() if r.content else None
                if verbose and response_json:
                    log(f"DEBUG UPDATE: {ip} download response JSON: {response_json}")
            except Exception as json_err:
                if verbose:
                    log(f"DEBUG UPDATE: {ip} invalid JSON download response: {json_err}")
                    log(f"DEBUG UPDATE: {ip} raw download response: {r.text[:200]}")
                response_json = None
                
            download_ok = r.status_code == 200 and (response_json and response_json.get('ok'))
            
            if not download_ok:
                status_msg = f"HTTP {r.status_code}"
                if response_json:
                    error_msg = response_json.get('error') or response_json.get('message') or 'download fallito'
                    status_msg += f" - {error_msg}"
                log(f"✗ Download {ip}: FAIL ({status_msg})")
                results.append({'ip': ip, 'port': port, 'response': response_json, 'ok': False, 'status_code': r.status_code, 'phase': 'download'})
                continue
                
            log(f"✓ Download {ip}: OK - Bundle scaricato")

            # Attendi che il device segnali 'downloaded' (o 100%) prima dell'apply
            status_endpoint = f"http://{ip}:{port}/status"
            wait_deadline = time.time() + 180  # max 3 minuti di attesa
            ready = False
            last_pct = -1
            while time.time() < wait_deadline:
                try:
                    rs = requests.get(status_endpoint, headers=headers, timeout=max(3.0, timeout))
                    if rs.ok:
                        js = rs.json()
                        st = (js or {}).get('update_status')
                        prog = int((js or {}).get('update_progress') or 0)
                        if prog != last_pct and prog % 10 == 0 and prog > 0:
                            # log minimale ogni 10%
                            log(f"… {ip} download {prog}%")
                            last_pct = prog
                        if st in ('downloaded', 'ok') or prog >= 100:
                            ready = True
                            break
                        if st == 'error':
                            err = (js or {}).get('update_error') or 'errore download'
                            log(f"✗ {ip} download errore: {err}")
                            break
                    # non pronto: attesa breve
                    time.sleep(0.8)
                except Exception:
                    time.sleep(1.0)

            # FASE 2: Apply update (con retry se il pacchetto non è ancora visibile)
            apply_endpoint = f"http://{ip}:{port}/update"
            apply_payload = {'restart': True}

            if verbose:
                log(f"DEBUG UPDATE: FASE 2 - Chiamando {apply_endpoint} (ready={ready})")

            max_apply_attempts = 6
            apply_attempt = 0
            apply_ok = False
            apply_response_json = None
            r2 = None
            while apply_attempt < max_apply_attempts and not apply_ok:
                apply_attempt += 1
                try:
                    # L'apply può richiedere più tempo (copia file + riavvio). Aumenta timeout minimo a 10s.
                    r2 = requests.post(apply_endpoint, headers=headers, data=json.dumps(apply_payload), timeout=max(timeout, 10.0))
                    try:
                        apply_response_json = r2.json() if r2.content else None
                    except Exception:
                        apply_response_json = None

                    apply_ok = r2.status_code == 200 and (apply_response_json and apply_response_json.get('ok'))
                    if apply_ok:
                        break

                    # Se il device risponde 400 Nessun pacchetto scaricato, attendi e riprova
                    msg = (apply_response_json or {}).get('error') or (apply_response_json or {}).get('message') or ''
                    if r2.status_code == 400 and 'Nessun pacchetto scaricato' in msg:
                        time.sleep(2.0)
                        continue
                    # Se permessi negati, prova una riparazione veloce e ritenta una volta
                    msg_lower = (msg or "").lower()
                    if (r2.status_code in (403, 500)) and ("permesso negato" in msg_lower or "permission" in msg_lower):
                        if verbose:
                            log(f"DEBUG UPDATE: {ip} apply PermissionError: provo /maintenance/fix_permissions e retry")
                        try:
                            fx = requests.post(f"http://{ip}:{port}/maintenance/fix_permissions", headers=headers, timeout=5.0)
                            # Poll breve fino a ok/error
                            t_dead = time.time() + 20.0
                            while time.time() < t_dead:
                                st = requests.get(f"http://{ip}:{port}/maintenance/status", headers=headers, timeout=3.0)
                                if st.ok:
                                    js = st.json() or {}
                                    s = js.get("status") or js.get("maintenance_status")
                                    if s in ("ok", "error"):
                                        break
                                time.sleep(0.8)
                        except Exception as _exc:
                            if verbose:
                                log(f"DEBUG UPDATE: {ip} fix_permissions errore: {_exc}")
                        # Effettua un solo retry immediato
                        try:
                            r2 = requests.post(apply_endpoint, headers=headers, data=json.dumps(apply_payload), timeout=max(timeout, 10.0))
                            apply_response_json = r2.json() if r2.content else None
                            apply_ok = r2.status_code == 200 and (apply_response_json and apply_response_json.get('ok'))
                            if apply_ok:
                                break
                            msg = (apply_response_json or {}).get('error') or (apply_response_json or {}).get('message') or ''
                        except Exception:
                            pass

                        # Fallback 2: se ancora PermissionError, tenta fix via SSH (extra/extra)
                        if not apply_ok and (paramiko is not None):
                            if verbose:
                                log(f"DEBUG UPDATE: {ip} retry fallito: tento fix SSH permessi e nuovo retry apply")
                            try:
                                client = paramiko.SSHClient()  # type: ignore
                                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # type: ignore
                                client.connect(hostname=ip, port=22, username="extra", password="extra", timeout=8)
                                cmd = (
                                    "sudo -S -p '' sh -c "
                                    "'mkdir -p /opt/headless-player/media; "
                                    "chown -R video:video /opt/headless-player; "
                                    "chmod -R g+rwX /opt/headless-player; "
                                    "setfacl -R -m u:video:rwx,g:video:rwx /opt/headless-player 2>/dev/null || true; "
                                    "setfacl -dR -m u:video:rwx,g:video:rwx /opt/headless-player 2>/dev/null || true'"
                                )
                                stdin, stdout, stderr = client.exec_command(cmd, get_pty=False)
                                try:
                                    stdin.write("extra\n")
                                    stdin.flush()
                                except Exception:
                                    pass
                                # attende fine comando
                                _ = stdout.channel.recv_exit_status()  # type: ignore[attr-defined]
                            except Exception as _exc:
                                if verbose:
                                    log(f"DEBUG UPDATE: {ip} SSH fix fallito: {_exc}")
                            finally:
                                try:
                                    client.close()
                                except Exception:
                                    pass
                            # Ritenta apply ancora una volta
                            try:
                                r2 = requests.post(apply_endpoint, headers=headers, data=json.dumps(apply_payload), timeout=max(timeout, 10.0))
                                apply_response_json = r2.json() if r2.content else None
                                apply_ok = r2.status_code == 200 and (apply_response_json and apply_response_json.get('ok'))
                                if apply_ok:
                                    break
                                msg = (apply_response_json or {}).get('error') or (apply_response_json or {}).get('message') or ''
                            except Exception:
                                pass

                    # altri errori: non vale la pena ritentare
                    break
                except requests.exceptions.ConnectionError:
                    # Probabile riavvio in corso -> consideriamo apply riuscita
                    apply_ok = True
                    apply_response_json = {'ok': True, 'restarting': True}
                    break
                except Exception:
                    time.sleep(1.0)

            if apply_ok:
                log(f"✓ Update {ip}: OK - Player si sta riavviando")
                results.append({'ip': ip, 'port': port, 'response': apply_response_json, 'ok': True, 'status_code': (r2.status_code if r2 else 200), 'phase': 'apply'})
            else:
                status_msg = f"HTTP {(r2.status_code if r2 else 'n/a')}"
                if apply_response_json:
                    error_msg = apply_response_json.get('error') or apply_response_json.get('message') or 'apply fallito'
                    status_msg += f" - {error_msg}"
                log(f"✗ Apply {ip}: FAIL ({status_msg})")
                results.append({'ip': ip, 'port': port, 'response': apply_response_json, 'ok': False, 'status_code': (r2.status_code if r2 else -1), 'phase': 'apply'})
                
        except requests.exceptions.ConnectTimeout:
            error_msg = f"connection timeout ({timeout}s)"
            results.append({'ip': ip, 'port': port, 'error': error_msg, 'ok': False, 'phase': 'download'})
            log(f"✗ Update {ip}: ERRORE {error_msg}")
        except requests.exceptions.ConnectionError as e:
            # Se siamo nella fase apply e la connessione si chiude, probabilmente il player si sta riavviando (successo!)
            if 'apply_endpoint' in locals():  # significa che siamo arrivati alla fase apply
                log(f"✓ Update {ip}: OK - Player si sta riavviando (connessione chiusa durante apply)")
                results.append({'ip': ip, 'port': port, 'response': {'ok': True, 'restarting': True}, 'ok': True, 'phase': 'apply'})
            else:
                error_msg = f"connection error: {e}"
                results.append({'ip': ip, 'port': port, 'error': error_msg, 'ok': False, 'phase': 'download'})
                log(f"✗ Update {ip}: ERRORE {error_msg}")
        except Exception as e:
            error_msg = f"unexpected error: {type(e).__name__}: {e}"
            results.append({'ip': ip, 'port': port, 'error': error_msg, 'ok': False, 'phase': 'download'})
            log(f"✗ Update {ip}: ERRORE {error_msg}")
            
    return results

def stream_progress(players: List[Dict], port: int, api_key: Optional[str], version: str, duration: float = 300.0, verbose: bool = False):
    """MVP: poll /admin/update/status e /admin/update/log per ogni player e mostra progress compatto.
    Si ferma quando tutti sono in fase finale (ok/error/warning) o scade duration."""
    headers = {}
    if api_key:
        headers['X-API-KEY'] = api_key
    start = time.time()
    states = {p['ip']: {'phase': None, 'progress': 0, 'done': False, 'next': 0} for p in players}
    final_phases = {"ok","error","warning"}
    def fmt_line(ip, st):
        ph = st.get('phase') or '-'
        prog = st.get('progress')
        msg = st.get('message') or ''
        return f"{ip:>15} | {prog:3d}% | {ph:<10} | {msg[:40]}"
    print("\nPROGRESSO UPDATE (polling)\nIP             | %   | PHASE      | MSG")
    poll_interval = 1.5
    try:
        while time.time() - start < duration:
            all_done = True
            for p in players:
                ip = p['ip']
                base = f"http://{ip}:{port}"
                st = states[ip]
                if st['done']:
                    continue
                all_done = False
                try:
                    # Usa /status invece di /admin/update/status - le info sono già lì
                    r = requests.get(f"{base}/status", timeout=1.8, headers=headers)
                    if verbose:
                        log(f"DEBUG PROGRESS: {ip}/status -> HTTP {r.status_code}")
                    if r.ok:
                        js = r.json()
                        if verbose and js:
                            log(f"DEBUG PROGRESS: {ip} status: {js}")
                        # Mappiamo i campi del player ai nostri campi progress
                        if js:
                            # update_status può essere: 'idle', 'downloading', 'installing', 'done', 'error'
                            update_status = js.get('update_status', 'idle')
                            update_progress = js.get('update_progress', 0)
                            update_error = js.get('update_error')
                            
                            # Mappa stati del player alle nostre fasi
                            phase_map = {
                                'idle': 'idle',
                                'downloading': 'download', 
                                'installing': 'install',
                                'done': 'ok',
                                'error': 'error'
                            }
                            st['phase'] = phase_map.get(update_status, update_status)
                            st['progress'] = update_progress or st.get('progress', 0)
                            st['message'] = update_error or f"Status: {update_status}"
                            
                            # Considera terminati gli stati finali
                            if update_status in ['done', 'error']:
                                st['done'] = True
                    elif verbose:
                        log(f"DEBUG PROGRESS: {ip}/status failed: {r.status_code} {r.text[:100]}")
                        
                    # Non c'è log incrementale, usiamo solo status
                        
                    if st.get('phase') in final_phases:
                        st['done'] = True
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    # prima volta: segna unreachable, altrimenti conserva ultimo stato
                    if st.get('phase') is None:
                        st['phase'] = 'unreachable'
                        st['progress'] = 0
                        st['message'] = f'no response: {type(e).__name__}'
                        st['done'] = True
            # redraw summary table
            print("\n" + "-"*68)
            for ip, st in states.items():
                print(fmt_line(ip, st))
            if all(s['done'] for s in states.values()):
                break
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\n[INTERRUZIONE] Monitoraggio interrotto dall'utente.")
    print("\nFINE MONITORING.")

def wait_and_verify(players: List[Dict], expected_version: str, timeout_per_host: float, port: int):
    """Poll /version fino a expected_version o timeout per ciascun player."""
    outcomes = []
    for p in players:
        ip = p['ip']
        base = f"http://{ip}:{port}"
        t0 = time.time()
        got = None
        while time.time() - t0 < timeout_per_host:
            try:
                r = requests.get(f"{base}/version", timeout=2.0)
                if r.ok and r.headers.get('content-type','').lower().startswith('application/json'):
                    man = r.json().get('manifest')
                    if man:
                        for line in man.splitlines():
                            if line.startswith('version='):
                                got = line.split('=',1)[1].strip(); break
                        if got == expected_version:
                            break
            except Exception:
                pass
            time.sleep(2.0)
        outcomes.append({'ip': ip, 'ok': got == expected_version, 'version': got})
    return outcomes

# --- Main ---

def parse_args():
    ap = argparse.ArgumentParser(description="Aggiornamento massivo player")
    ap.add_argument('--port-player', type=int, default=8080)
    ap.add_argument('--scan', help='Lista reti CIDR separate da virgola')
    ap.add_argument('--api-key')
    ap.add_argument('--serve-port', type=int, default=8000)
    ap.add_argument('--timeout', type=float, default=2.0)
    ap.add_argument('--threads', type=int, default=80)
    ap.add_argument('--no-scan', action='store_true')
    ap.add_argument('--targets', help='Lista IP manuale separata da virgola')
    ap.add_argument('--auto-yes', action='store_true')
    ap.add_argument('--no-build', action='store_true')
    ap.add_argument('--bundle-path', help='Percorso locale del bundle zip (salta build)')
    ap.add_argument('--bundle-url', help='URL remoto del bundle (salta build e file server)')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--verify-timeout', type=float, default=120.0)
    ap.add_argument('--verbose', '-v', action='store_true', help='Output verbose per debug')
    return ap.parse_args()


def main():
    args = parse_args()
    
    if args.verbose:
        log(f"DEBUG: Script avviato con parametri: port={args.port_player}, timeout={args.timeout}, threads={args.threads}")
    
    bundle_res = BundleResult()
    # Strategia bundle: URL remoto > path locale > build > ultimo zip
    use_remote_url = bool(args.bundle_url)
    if not use_remote_url and not args.no_build and not args.bundle_path:
        t_build = threading.Thread(target=build_bundle_async, args=(bundle_res,), daemon=True)
        t_build.start()

    if args.no_scan and not args.targets:
        log('--no-scan specificato ma nessun --targets. Nulla da fare.')
        return

    players = []
    if args.targets:
        targets = [x.strip() for x in args.targets.split(',') if x.strip()]
        if args.verbose:
            log(f"DEBUG: Target manuali specificati: {targets}")
        players = discover_players(targets, args.port_player, args.timeout, args.threads, args.api_key, args.verbose)
    elif not args.no_scan:
        if args.scan:
            nets = [x.strip() for x in args.scan.split(',') if x.strip()]
            if args.verbose:
                log(f"DEBUG: Reti specificate dall'utente: {nets}")
        else:
            local_ip = local_primary_ip()
            nets = derive_default_networks()
            if args.verbose:
                log(f"DEBUG: IP locale rilevato: {local_ip}")
                log(f"DEBUG: Reti derivate automaticamente: {nets}")
        
        log(f"Espansione delle reti {nets} in indirizzi IP...")
        ips = expand_networks(nets)
        if args.verbose:
            log(f"DEBUG: Totale IP da scansionare: {len(ips)}")
            if len(ips) <= 20:
                log(f"DEBUG: IP list: {ips}")
            else:
                log(f"DEBUG: Primi 10 IP: {ips[:10]}")
                log(f"DEBUG: Ultimi 10 IP: {ips[-10:]}")
        
        players = discover_players(ips, args.port_player, args.timeout, args.threads, args.api_key, args.verbose)

    # Determina bundle da usare
    version = None
    sha = None
    bundle_url = None
    zip_path = None
    if args.bundle_url:
        bundle_url = args.bundle_url
        # estrai versione e sha dal nome, se possibile
        m = re.search(r"bundle_v([0-9A-Za-z_.:-]+)_([0-9a-fA-F]{64})\.zip$", bundle_url)
        if m:
            version = m.group(1); sha = m.group(2).lower()
    elif args.bundle_path:
        zip_path = Path(args.bundle_path)
        if not zip_path.is_file():
            log(f"Bundle non trovato: {zip_path}")
            return
        m = re.search(r"bundle_v([0-9A-Za-z_.:-]+)_([0-9a-fA-F]{64})\.zip$", zip_path.name)
        if m:
            version = m.group(1); sha = m.group(2).lower()
        else:
            # calcola sha se non nel nome
            try:
                sha = sha256_of(zip_path)
            except Exception:
                sha = None
    else:
        # Build o fallback all'ultimo zip se no-build
        log('Attendo completamento build bundle...')
        bundle_res.done.wait()
        if bundle_res.error:
            log(f"ERRORE build bundle: {bundle_res.error}")
            # fallback: ultimo zip in releases
            log("Tentativo fallback al bundle più recente in releases...")
            latest = find_latest_bundle()
            if latest is None:
                log("ERRORE: Nessun bundle trovato nella cartella releases e build fallita")
                return
            log(f"Uso bundle esistente: {latest}")
            zip_path = latest
            m = re.search(r"bundle_v([0-9A-Za-z_.:-]+)_([0-9a-fA-F]{64})\.zip$", latest.name)
            if m:
                version = m.group(1); sha = m.group(2).lower()
            else:
                # Se non c'è hash nel nome, calcoliamolo
                log("Calcolo SHA256 del bundle esistente...")
                try:
                    sha = sha256_of(latest)
                    version = "unknown"  # versione non determinabile
                except Exception as e:
                    log(f"ERRORE calcolo SHA256: {e}")
                    return
        else:
            zip_path = bundle_res.zip_path
            version = bundle_res.version
            sha = bundle_res.zip_sha
            log(f"Build bundle completata: {zip_path}")
            
        if not zip_path or not Path(zip_path).is_file():
            log('ERRORE: Impossibile determinare path bundle')
            return

    if not version:
        log('Versione non determinata dal bundle. Interrompo per sicurezza.')
        return

    log('='*70)
    log('PLAYER TROVATI:')
    for i,p in enumerate(players):
        log(f"[{i}] {p['ip']} v={p.get('version')}")
    log('='*70)
    if not players:
        log('Nessun player trovato. Esco.')
        return

    if not args.auto_yes:
        ans = input('Procedere con UPDATE di tutti i player elencati? [y/N] ').strip().lower()
        if ans not in ('y','yes'): 
            log('Annullato dall\'utente.')
            return

    # Prepara URL bundle: remoto o locale
    srv = None
    if not bundle_url:
        releases_dir = Path(zip_path).parent
        srv = start_file_server(releases_dir, args.serve_port)
        local_ip = local_primary_ip()
        bundle_url = f"http://{local_ip}:{args.serve_port}/{Path(zip_path).name}"
        log(f"Bundle disponibile su: {bundle_url}")
    else:
        log(f"Uso bundle remoto: {bundle_url}")

    results = apply_update(players, version, bundle_url, sha, args.api_key, verbose=args.verbose)
    # Avvia monitoraggio progressi (MVP sempre attivo)
    stream_progress(players, args.port_player, args.api_key, version, verbose=args.verbose)

    # Summary
    ok_count = sum(1 for r in results if r.get('ok'))
    log('='*50)
    log(f"RISULTATO UPDATE: {ok_count}/{len(results)} successi")
    for r in results:
        if r.get('ok'): continue
        log(f"FAIL {r['ip']} -> {r.get('response') or r.get('error')}")
    log('='*50)

    # Verifica post-restart
    if args.verify:
        log(f"Verifica version su ciascun player (attesa max {args.verify_timeout}s per host)...")
        checks = wait_and_verify(players, version, args.verify_timeout, args.port_player)
        okc = sum(1 for c in checks if c['ok'])
        log(f"VERIFICA: {okc}/{len(checks)} allineati a version={version}")
        for c in checks:
            if not c['ok']:
                log(f"MISMATCH {c['ip']}: got={c['version']} expected={version}")

    try:
        srv.shutdown()
    except Exception:
        pass

if __name__ == '__main__':
    main()
