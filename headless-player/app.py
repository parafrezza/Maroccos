import os, io, threading, socket, shutil, tarfile, zipfile, time, tempfile, platform, sys, errno, hashlib, logging, warnings, uuid
import requests
from pathlib import Path
import shutil
import subprocess
import csv
from typing import Optional, Any, Callable
from urllib.request import urlopen, Request
from urllib.parse import urlparse
from urllib.parse import urlparse, unquote
from collections import deque
from logging.handlers import RotatingFileHandler
import traceback

# Setup early warning filters before importing FastAPI/Starlette
try:
    warnings.filterwarnings(
        "ignore",
        message=r"Please use `import python_multipart` instead\.",
        category=PendingDeprecationWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=r"\s*on_event is deprecated, use lifespan event handlers instead\.",
        category=DeprecationWarning,
    )
except Exception:
    pass

from fastapi import FastAPI, Query, Body
from contextlib import asynccontextmanager
from fastapi import UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
try:
    from starlette.background import BackgroundTask
except Exception:
    BackgroundTask = None  # type: ignore
import asyncio
import uvicorn
from uvicorn import Config, Server

try:
    import uvloop  # type: ignore
    _UVLOOP_AVAILABLE = True
except Exception:
    _UVLOOP_AVAILABLE = False

from PIL import Image, ImageDraw, ImageFont
import netifaces
import json
import re

HAS_GST = False
try:
    import gi
    gi.require_version("Gst", "1.0")
    gi.require_version("GObject", "2.0")
    from gi.repository import Gst, GObject, GLib  # type: ignore
    HAS_GST = True
except Exception:
    # Provide lightweight shims so the module can import and the app can run
    # in environments without GStreamer/PyGObject. The shims implement only
    # the API surface used outside of GStreamer-specific flows (scheduling,
    # basic constants). Full GStreamer functionality will remain unavailable
    # unless real gi/Gst are installed.
    import threading
    class _DummyGLib:
        class MainLoop:
            def __init__(self):
                self._running = False
            def run(self):
                self._running = True
                while self._running:
                    time.sleep(0.5)
            def quit(self):
                self._running = False

        @staticmethod
        def idle_add(func, *args, **kwargs):
            try:
                threading.Thread(target=lambda: func(*args, **kwargs), daemon=True).start()
            except Exception:
                pass
            return None

        @staticmethod
        def timeout_add(ms, func, *args, **kwargs):
            try:
                t = threading.Timer(ms / 1000.0, lambda: func(*args, **kwargs))
                t.daemon = True
                t.start()
                return None
            except Exception:
                return None

        @staticmethod
        def source_remove(id_):
            # No-op for the dummy implementation
            return True

    class _DummyGst:
        SECOND = 1000000000
        class State:
            NULL = 0
            PLAYING = 1
            PAUSED = 2

        class MessageType:
            ERROR = 0
            EOS = 1
            WARNING = 2
            INFO = 3

        class SeekFlags:
            FLUSH = 1
            KEY_UNIT = 2

        class Format:
            TIME = 0

        @staticmethod
        def init(argv=None):
            return None

        @staticmethod
        def parse_launch(desc):
            raise RuntimeError("GStreamer not available in this environment")

        class Buffer:
            @staticmethod
            def new_allocate(_, size, _2):
                return bytearray(size)

    Gst = _DummyGst
    GLib = _DummyGLib
    GObject = None

# Nota: se HAS_GST è True, Gst/GLib sono già importati sopra.
# Evita import non condizionati che rompono ambienti senza PyGObject.
# ---------- Config ----------
APP_DIR = Path(__file__).resolve().parent

# Silence known third-party warnings in test/dev environments
try:
    warnings.filterwarnings(
        "ignore",
        message=r"\s*on_event is deprecated, use lifespan event handlers instead\.",
        category=DeprecationWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=r"Please use `import python_multipart` instead\.",
        category=PendingDeprecationWarning,
    )
except Exception:
    pass

def _user_config_dir() -> Path:
    """Ritorna una cartella di configurazione scrivibile dall'utente.
    Windows: %LOCALAPPDATA%\\Maroccos\\headless-player (fallback %APPDATA%).
    macOS: ~/Library/Application Support/Maroccos/headless-player
    Linux: $XDG_CONFIG_HOME/Maroccos/headless-player (fallback ~/.config/...)
    """
    try:
        sysname = platform.system().lower()
    except Exception:
        sysname = ""
    try:
        if sysname.startswith("win"):
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
            p = Path(base) / "Maroccos" / "headless-player"
        elif sysname.startswith("darwin") or sysname.startswith("mac"):
            p = Path.home() / "Library" / "Application Support" / "Maroccos" / "headless-player"
        else:
            base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
            p = Path(base) / "Maroccos" / "headless-player"
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        # Fallback: cartella dell'app
        return APP_DIR

def _resolve_user_media_dir() -> Path:
    """Determina una cartella media accessibile all'utente.
    Priorità: ENV MEDIA_DIR > Desktop/media (se possibile) > Documents/media > fallback APP_DIR/media.
    Imposta anche MEDIA_DIR_SOURCE per diagnosi.
    """
    global MEDIA_DIR_SOURCE
    MEDIA_DIR_SOURCE = "auto"

    # 0) Override via env
    try:
        env_dir = os.environ.get("MEDIA_DIR")
        if env_dir:
            p = Path(env_dir).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            MEDIA_DIR_SOURCE = "env"
            return p
    except Exception as e:
        print(f"[MEDIA] Ignoro MEDIA_DIR da env per errore: {e}", flush=True)

    # 1) Desktop/media (utente corrente)
    try:
        home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    except Exception:
        home = Path.home()
    desktop = home / "Desktop" / "media"
    documents = home / "Documents" / "media"

    # Su macOS/Windows, preferisci una cartella già esistente su Desktop per allineamento UX
    try:
        if desktop.exists() and desktop.is_dir():
            desktop.mkdir(parents=True, exist_ok=True)
            MEDIA_DIR_SOURCE = "desktop"
            return desktop
    except Exception:
        pass

    # Se non esiste, prova a crearla su Desktop; se fallisce, ripiega su Documents
    for base, label in ((desktop, "desktop"), (documents, "documents")):
        try:
            base.mkdir(parents=True, exist_ok=True)
            MEDIA_DIR_SOURCE = label
            return base
        except Exception:
            continue

    # 2) Fallback: cartella locale dell'app
    try:
        local = APP_DIR / "media"
        local.mkdir(parents=True, exist_ok=True)
        MEDIA_DIR_SOURCE = "fallback"
        return local
    except Exception:
        MEDIA_DIR_SOURCE = "app_dir"
        return APP_DIR

_MAC_CLEAN = re.compile(r"[^0-9A-Fa-f]")

def _normalize_mac_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    cleaned = re.sub(_MAC_CLEAN, "", candidate)
    if len(cleaned) != 12:
        return None
    return ":".join(cleaned[i:i+2].upper() for i in range(0, 12, 2))

def _normalize_mac_collection(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        entries = [entry for entry in re.split(r"[\s,;]+", value) if entry]
    elif isinstance(value, (list, tuple, set)):
        entries = list(value)
    else:
        entries = [value]
    normalized: list[str] = []
    for entry in entries:
        mac = _normalize_mac_value(entry)
        if mac:
            normalized.append(mac)
    return normalized

MEDIA_DIR = _resolve_user_media_dir()
# Log iniziale della cartella media selezionata
try:
    print(f"[MEDIA] Base: {MEDIA_DIR} (source={globals().get('MEDIA_DIR_SOURCE', 'auto')})", flush=True)
except Exception:
    pass
VERSION_FILE = APP_DIR / "VERSION"
VERSION = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "v0.1.0"
USE_KMS = os.environ.get("USE_KMS", "1") == "1"
USE_HW_DECODER = os.environ.get("USE_HW_DECODER", "1") == "1"
TARGET_WIDTH = int(os.environ.get("TARGET_WIDTH", "1280"))
TARGET_HEIGHT = int(os.environ.get("TARGET_HEIGHT", "720"))
TARGET_FPS = os.environ.get("TARGET_FPS", "25/1")
FADE_INTERVAL_MS = 40
VIDEO_PATH = str(MEDIA_DIR / "orcanmado.mp4")
APP_PORT = int(os.environ.get("APP_PORT", "8080"))
SPLASH_BLACK = os.environ.get("SPLASH_BLACK", "0") == "1"
# Config utente: migra da legacy APP_DIR/config.json se presente
CONFIG_DIR = _user_config_dir()
LEGACY_CONFIG_FILE = APP_DIR / "config.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_DIR = CONFIG_DIR / "logs"
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
LOG_FILE = LOG_DIR / "headless-player.log"

_log_handlers = []
try:
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _log_handlers.append(file_handler)
except Exception as log_err:
    print(f"[LOG] File logging disabilitato: {log_err}", flush=True)

try:
    media_log_dir = MEDIA_DIR / "_logs"
    media_log_dir.mkdir(parents=True, exist_ok=True)
    media_log_path = media_log_dir / "headless-player.log"
    share_handler = RotatingFileHandler(media_log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    share_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _log_handlers.append(share_handler)
except Exception as log_share_err:
    print(f"[LOG] Impossibile creare log condiviso: {log_share_err}", flush=True)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
_log_handlers.append(stream_handler)

logging.basicConfig(level=logging.INFO, handlers=_log_handlers)
off_logger = logging.getLogger("headless-player.off")
_WINDOWS_MISSING_DEP_CODE = 0xC0000135
_WINDOWS_OFF_DEPENDENCIES = (
    "libcurl-4.dll",
    "libfreetype-6.dll",
    "libfreeimage-3.dll",
    "glew32.dll",
)

def _off_dependency_hint() -> str:
    return (
        "Assicurati che OFF-player.exe carichi i DLL necessari: "
        + ", ".join(_WINDOWS_OFF_DEPENDENCIES)
        + ". Copia i file nella stessa cartella di OFF-player o mettili nel PATH di sistema."
    )
try:
    # Crash handlers: log su file e Desktop + minidump (Windows)
    import faulthandler, atexit
    from datetime import datetime

    def _public_desktop_dir() -> Path:
        try:
            p = os.environ.get("PUBLIC")
            if p:
                d = Path(p) / "Desktop"
                d.mkdir(parents=True, exist_ok=True)
                return d
        except Exception:
            pass
        # Fallback standard
        dflt = Path("C:/Users/Public/Desktop") if _is_windows else Path.home() / "Desktop"
        try:
            dflt.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return dflt

    def _safe_write_text(path: Path, content: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            with open(path, "w", encoding="utf-8", errors="ignore") as f:
                f.write(content)
        except Exception:
            # last resort
            try:
                with open(path, "wb") as f:
                    f.write(content.encode("utf-8", "ignore"))
            except Exception:
                pass

    def _write_minidump_windows(path: Path) -> bool:
        if not _is_windows:
            return False
        try:
            import ctypes, msvcrt
            from ctypes import wintypes
            dbghelp = ctypes.WinDLL("DbgHelp.dll")
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            MiniDumpWriteDump = dbghelp.MiniDumpWriteDump
            MiniDumpWriteDump.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ]
            MiniDumpWriteDump.restype = wintypes.BOOL

            hProc = kernel32.GetCurrentProcess()
            pid = kernel32.GetCurrentProcessId()
            with open(path, "wb") as f:
                hFile = msvcrt.get_osfhandle(f.fileno())
                MiniDumpWithFullMemory = 0x00000002
                ok = MiniDumpWriteDump(hProc, pid, hFile, MiniDumpWithFullMemory, None, None, None)
                return bool(ok)
        except Exception as e:
            try:
                print(f"[CRASH] Minidump Windows fallito: {e}", flush=True)
            except Exception:
                pass
            return False

    def _install_crash_handlers() -> None:
        # Faulthandler: cattura tracebacks su crash nativi
        try:
            fh_path = LOG_DIR / "faulthandler.log"
            fh_file = open(fh_path, "a", buffering=1)
            faulthandler.enable(fh_file)
        except Exception as e:
            try:
                print(f"[CRASH] Faulthandler non abilitato: {e}", flush=True)
            except Exception:
                pass

        def _dump_all(reason: str, exc_text: str | None = None):
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            hdr = [
                f"Headless Player Crash Report",
                f"When: {ts}",
                f"Version: {VERSION}",
                f"OS: {platform.platform()} ({platform.machine()})",
                f"Python: {platform.python_version()}",
                f"Reason: {reason}",
                "",
            ]
            body = "\n".join(hdr) + (exc_text or "")
            # Scrivi in logs
            try:
                log_path = LOG_DIR / f"headless-player_crash_{ts}.txt"
                _safe_write_text(log_path, body)
            except Exception:
                pass
            # Copia su Desktop pubblico
            try:
                desk_path = _public_desktop_dir() / f"headless-player_crash_{ts}.txt"
                _safe_write_text(desk_path, body)
            except Exception:
                pass
            # Minidump Windows in LOG_DIR e copia su Desktop
            if _is_windows:
                try:
                    dmp = LOG_DIR / f"headless-player_{ts}.dmp"
                    if _write_minidump_windows(dmp):
                        try:
                            shutil.copy2(dmp, _public_desktop_dir() / dmp.name)
                        except Exception:
                            pass
                except Exception:
                    pass

        _prev_hook = sys.excepthook
        def _excepthook(exc_type, exc, tb):
            try:
                exc_text = "".join(traceback.format_exception(exc_type, exc, tb))
                _dump_all("unhandled_exception", exc_text)
            except Exception:
                pass
            try:
                if callable(_prev_hook):
                    _prev_hook(exc_type, exc, tb)
            except Exception:
                pass

        sys.excepthook = _excepthook

        # Threading exceptions (Python 3.8+)
        try:
            def _thread_hook(args):
                try:
                    exc_text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
                    _dump_all(f"thread_exception:{getattr(args, 'thread', None)}", exc_text)
                except Exception:
                    pass
            threading.excepthook = _thread_hook  # type: ignore[attr-defined]
        except Exception:
            pass

        # Dump su termination anomala
        try:
            atexit.register(lambda: None)
        except Exception:
            pass

    _install_crash_handlers()
except Exception as _crh_err:
    try:
        print(f"[CRASH] Installazione crash handler fallita: {_crh_err}", flush=True)
    except Exception:
        pass
try:
    if LEGACY_CONFIG_FILE.exists() and not CONFIG_FILE.exists():
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            shutil.copy2(LEGACY_CONFIG_FILE, CONFIG_FILE)
            print(f"[CONFIG] Migrato config.json in {CONFIG_FILE}", flush=True)
        except Exception as me:
            print(f"[CONFIG] Migrazione config.json fallita: {me}", flush=True)
except Exception:
    pass
_IDLE_CACHE_DIR = APP_DIR / ".idle"
IDLE_BLACK_IMAGE = _IDLE_CACHE_DIR / "idle_black.png"

# Unique identifiers (persistent device id and per-process instance id)
DEVICE_ID_FILE = CONFIG_DIR / "device_id"
DEVICE_ID = None  # type: ignore
INSTANCE_ID = uuid.uuid4().hex

def _ensure_device_id() -> str:
    global DEVICE_ID
    if DEVICE_ID:
        return DEVICE_ID
    # Try from config.json first
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text())
            did = data.get("device_id")
            if isinstance(did, str) and did:
                DEVICE_ID = did
                return DEVICE_ID
    except Exception:
        pass
    # Try from dedicated file
    try:
        if DEVICE_ID_FILE.exists():
            did = DEVICE_ID_FILE.read_text(encoding="utf-8").strip()
            if did:
                DEVICE_ID = did
                return DEVICE_ID
    except Exception:
        pass
    # Generate and persist
    try:
        did = uuid.uuid4().hex
        DEVICE_ID = did
        try:
            DEVICE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
            DEVICE_ID_FILE.write_text(did, encoding="utf-8")
        except Exception:
            pass
        try:
            # also record in config for completeness
            data = {}
            if CONFIG_FILE.exists():
                try:
                    data = json.loads(CONFIG_FILE.read_text())
                except Exception:
                    data = {}
            data["device_id"] = did
            CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception:
            pass
        return did
    except Exception:
        # Last resort: ephemeral
        DEVICE_ID = uuid.uuid4().hex
        return DEVICE_ID

# Directory preferita per file di update scaricati (writable dall'utente del servizio)
def _pick_update_dir() -> Path:
    candidates = []
    # 1) Sotto /var/tmp/headless-player
    candidates.append(Path("/var/tmp/headless-player"))
    # 2) Sotto temp di sistema
    candidates.append(Path(tempfile.gettempdir()) / "headless-player")
    # 3) APP_DIR se scrivibile (ultimo fallback)
    candidates.append(APP_DIR)
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            test = d / ".wcheck"
            with open(test, "w") as f:
                f.write("ok")
            test.unlink(missing_ok=True)
            return d
        except Exception:
            continue
    # proprio ultimo fallback: temp corrente
    return Path(tempfile.gettempdir())

# ---- Overlay KMS opzionale (plane separato) ----
# Abilita un overlay full-screen nero con alpha regolabile via kmssink, utile per FTB/fade-in
# anche quando il backend video principale non è GStreamer (es. cvlc).
OVERLAY_ENABLED = os.environ.get("OVERLAY_ENABLED", "0") == "1"
OVERLAY_USE_KMS = os.environ.get("OVERLAY_USE_KMS", "1") == "1"
# Impostare un plane-id valido è consigliato per garantire l'uso di un overlay plane sopra al video.
# Usa `modetest` o equivalente per scoprire plane disponibili. 0 = auto/non specificato.
OVERLAY_KMS_PLANE_ID = int(os.environ.get("OVERLAY_KMS_PLANE_ID", "0"))
# Di default non impostiamo zpos per massima compatibilità (alcune build di kmssink non espongono questa property)
OVERLAY_ZPOS = int(os.environ.get("OVERLAY_ZPOS", "-1"))

from backends import BACKEND_CLASSES, available_backends

# Default framework: per Windows (e in generale per questa distribuzione) preferisci "off"
# così da controllare OFF-player.exe; la persistenza in config.json può sovrascrivere.
current_framework = {"name": "off", "backend": None}
UDP_ENABLED = True

# --- UDP Log streaming to GUI ---
LOG_UDP_ENABLED = False
LOG_UDP_HOST = ""
LOG_UDP_PORT = 7788
# --- Startup Beacon (auto-discovery) ---
# Invia piccoli pacchetti UDP broadcast all'avvio per permettere alla GUI
# di auto-scoprire il player senza scan pesanti.
BEACON_ENABLED = True
BEACON_PORT = int(os.environ.get("HEADLESS_BEACON_PORT", "47999"))

def _emit_discovery_beacon(reason: str = "startup", retries: int = 3, delay: float = 0.4) -> bool:
    """Broadcast a lightweight discovery packet so the GUI can pick us up quickly."""
    try:
        env_flag = os.environ.get("HEADLESS_BEACON_ENABLED")
        if env_flag in {"0", "false", "False"}:
            return False
        if not globals().get("BEACON_ENABLED", True):
            return False
        payload = {
            "type": "headless_beacon",
            "version": VERSION,
            "name": DEVICE_NAME,
            "http_port": APP_PORT,
            "ip": None,
            "ts": time.time(),
            "reason": reason,
        }
        try:
            payload["ip"] = get_ip()
        except Exception:
            payload["ip"] = None
        data = json.dumps(payload).encode("utf-8")
        port = int(os.environ.get("HEADLESS_BEACON_PORT", str(globals().get("BEACON_PORT", 47999))))
        last_exc: Exception | None = None
        for attempt in range(max(1, retries)):
            s = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.settimeout(0.25)
                s.sendto(data, ("255.255.255.255", port))
                try:
                    s.close()
                except Exception:
                    pass
                print(f"[BEACON] Broadcast ({reason}) inviato su porta {port}", flush=True)
                return True
            except Exception as exc:
                last_exc = exc
                try:
                    if s is not None:
                        s.close()
                except Exception:
                    pass
                time.sleep(delay)
        if last_exc:
            print(f"[BEACON] Invio beacon ({reason}) fallito: {last_exc}", flush=True)
        return False
    except Exception as fatal:
        try:
            print(f"[BEACON] Errore interno beacon ({reason}): {fatal}", flush=True)
        except Exception:
            pass
        return False

# Identità dispositivo (persistita in config)
DEVICE_NAME = os.environ.get("DEVICE_NAME", "")

# Durate overlay configurabili (default 1s)
OVERLAY_FADE_OUT_ON_PLAY_S = float(os.environ.get("OVERLAY_FADE_OUT_ON_PLAY_S", "1.0"))
OVERLAY_FADE_IN_ON_STOP_S = float(os.environ.get("OVERLAY_FADE_IN_ON_STOP_S", "1.0"))

VLC_HTTP_HOST = os.environ.get("VLC_HTTP_HOST", "127.0.0.1")
VLC_HTTP_PORT = int(os.environ.get("VLC_HTTP_PORT", "8090"))
VLC_HTTP_USER = os.environ.get("VLC_HTTP_USER", "")
VLC_HTTP_PASSWORD = os.environ.get("VLC_HTTP_PASSWORD", "vlcpass")
VLC_EXTRA_ARGS = [arg for arg in os.environ.get("VLC_EXTRA_ARGS", "").split() if arg]
# OFF-player endpoint di default (per backend "off" e launcher interno)
OFF_HOST = os.environ.get("OFF_HOST", "127.0.0.1")
try:
    OFF_PORT = int(os.environ.get("OFF_PORT", "8082"))  # evita conflitto con APP_PORT=8080
except Exception:
    OFF_PORT = 8082
try:
    OFF_UDP_PORT = int(os.environ.get("OFF_UDP_PORT", "47877"))
except Exception:
    OFF_UDP_PORT = 47877
# Autostart OFF-player: di default su macOS abilitiamo l'autostart; altrove disabilitato
try:
    _plat = platform.system().lower()
except Exception:
    _plat = ""
_is_darwin = _plat.startswith("darwin") or _plat.startswith("mac")
_is_windows = _plat.startswith("win") or "windows" in _plat
# Abilita OFF_AUTOSTART di default su macOS e Windows (può essere disattivato via env/config)
OFF_AUTOSTART = os.environ.get("OFF_AUTOSTART", "1" if (_is_darwin or _is_windows) else "0") in {"1", "true", "True"}
# Watchdog per processi orfani di OFF-player (di default attivo su Windows)
OFF_WATCHDOG_ENABLED = os.environ.get("OFF_WATCHDOG_ENABLED", "1" if _is_windows else "0") in {"1", "true", "True"}

STARTUP_BROADCAST = os.environ.get("STARTUP_BROADCAST", "255.255.255.255")
try:
    STARTUP_PORT = int(os.environ.get("STARTUP_PORT", "9"))
except Exception:
    STARTUP_PORT = 9
STARTUP_MACS = _normalize_mac_collection(os.environ.get("STARTUP_MACS", ""))

# ---------- Display mode detection (system resolution) ----------
_DISPLAY_MODE_CACHE: dict[str, Any] = {"value": None, "ts": 0.0}
_DISPLAY_MODE_LOCK = threading.Lock()


def _format_display_mode_label(mode: dict[str, Any]) -> str:
    try:
        width = int(mode.get("width") or 0)
    except Exception:
        width = 0
    try:
        height = int(mode.get("height") or 0)
    except Exception:
        height = 0
    if width <= 0 or height <= 0:
        return "-"
    label = f"{width}x{height}"
    refresh_raw = mode.get("refresh_hz")
    if refresh_raw is None:
        refresh_raw = mode.get("refresh") if mode.get("refresh") is not None else mode.get("hz")
    try:
        refresh_val = float(refresh_raw) if refresh_raw not in (None, "", 0, 0.0) else None
    except Exception:
        refresh_val = None
    if isinstance(refresh_val, (int, float)) and refresh_val and refresh_val > 0:
        rounded = round(refresh_val)
        if abs(refresh_val - rounded) < 0.05:
            label += f" @ {int(rounded)} Hz"
        else:
            label += f" @ {refresh_val:.2f} Hz"
    if bool(mode.get("interlaced")):
        label += " (interlaced)"
    return label


def _build_display_mode(raw: dict[str, Any] | None, *, source_fallback: str = "", refreshed_at: float | None = None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    def _coerce_int(value: Any) -> int:
        try:
            if value is None or value == "":
                return 0
            return int(float(value))
        except Exception:
            return 0

    width = _coerce_int(raw.get("width") or raw.get("w"))
    height = _coerce_int(raw.get("height") or raw.get("h"))
    refresh_candidate = raw.get("refresh_hz")
    if refresh_candidate is None:
        refresh_candidate = raw.get("refresh") if raw.get("refresh") is not None else raw.get("hz")
    try:
        refresh = float(refresh_candidate)
        if refresh <= 0:
            refresh = None
    except Exception:
        refresh = None
    interlaced = bool(raw.get("interlaced", False))
    source = str(raw.get("source") or source_fallback or "")

    data: dict[str, Any] = {
        "width": width,
        "height": height,
        "refresh_hz": refresh,
        "interlaced": interlaced,
    }
    if source:
        data["source"] = source
    data["valid"] = bool(width > 0 and height > 0)
    try:
        tw = int(TARGET_WIDTH)
        th = int(TARGET_HEIGHT)
        data["target_width"] = tw
        data["target_height"] = th
        data["matches_target"] = bool(data["valid"] and width == tw and height == th)
    except Exception:
        pass
    data["target_fps"] = TARGET_FPS
    data["label"] = _format_display_mode_label(data)
    if refreshed_at is None:
        refreshed_at = time.time()
    try:
        data["refreshed_at"] = float(refreshed_at)
    except Exception:
        data["refreshed_at"] = refreshed_at
    return data


def _display_mode_with_age(data: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    if now is None:
        now = time.time()
    result = dict(data)
    refreshed_at = result.get("refreshed_at", now)
    try:
        age = max(0.0, now - float(refreshed_at))
    except Exception:
        age = 0.0
    result["age_ms"] = int(age * 1000)
    return result


def _probe_display_mode_windows() -> dict[str, Any] | None:
    if not _is_windows:
        return None
    try:
        import ctypes
    except ImportError:
        return None
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
    except Exception:
        return None
    try:
        hdc = user32.GetDC(None)
    except Exception:
        hdc = None
    if not hdc:
        return None
    HORZRES = 8
    VERTRES = 10
    VREFRESH = 116
    try:
        width = gdi32.GetDeviceCaps(hdc, HORZRES)
        height = gdi32.GetDeviceCaps(hdc, VERTRES)
        refresh = gdi32.GetDeviceCaps(hdc, VREFRESH)
    except Exception:
        width = height = refresh = 0
    finally:
        try:
            user32.ReleaseDC(None, hdc)
        except Exception:
            pass
    if width <= 0 or height <= 0:
        return None
    payload = {
        "width": int(width),
        "height": int(height),
        "refresh_hz": float(refresh) if isinstance(refresh, (int, float)) and refresh > 0 else None,
        "interlaced": False,
        "source": "win32.GetDeviceCaps",
    }
    return payload


def _probe_display_mode_linux() -> dict[str, Any] | None:
    try:
        plat = globals().get("_plat", platform.system().lower())
    except Exception:
        plat = ""
    if not plat.startswith("linux"):
        return None
    if shutil.which("xrandr") is None:
        return None
    try:
        proc = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, timeout=0.8)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    for raw_line in proc.stdout.splitlines():
        if "*" not in raw_line:
            continue
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        mode_token = parts[0]
        if "x" not in mode_token:
            continue
        width_token, height_token = mode_token.split("x", 1)
        try:
            width = int(float(width_token))
        except Exception:
            continue
        height_digits = "".join(ch for ch in height_token if ch.isdigit())
        if not height_digits:
            continue
        try:
            height = int(float(height_digits))
        except Exception:
            continue
        refresh_val: float | None = None
        for token in parts[1:]:
            if "*" not in token:
                continue
            cleaned = token.replace("*", "").replace("+", "").rstrip(",")
            try:
                refresh_val = float(cleaned)
                break
            except Exception:
                continue
        return {
            "width": width,
            "height": height,
            "refresh_hz": refresh_val,
            "interlaced": False,
            "source": "xrandr",
        }
    return None


def _probe_display_mode() -> dict[str, Any] | None:
    if _is_windows:
        return _probe_display_mode_windows()
    try:
        plat = globals().get("_plat", platform.system().lower())
    except Exception:
        plat = ""
    if plat.startswith("linux"):
        return _probe_display_mode_linux()
    return None


def get_display_mode(force: bool = False) -> dict[str, Any] | None:
    with _DISPLAY_MODE_LOCK:
        now = time.time()
        cache_value = _DISPLAY_MODE_CACHE.get("value")
        cache_ts = float(_DISPLAY_MODE_CACHE.get("ts") or 0.0)
        if not force and isinstance(cache_value, dict) and (now - cache_ts) < 5.0:
            return _display_mode_with_age(cache_value, now=now)

        raw = _probe_display_mode()
        _DISPLAY_MODE_CACHE["ts"] = now
        if not raw:
            _DISPLAY_MODE_CACHE["value"] = None
            return None

        built = _build_display_mode(raw, source_fallback=str(raw.get("source", "system")), refreshed_at=now)
        if not built:
            _DISPLAY_MODE_CACHE["value"] = None
            return None
        _DISPLAY_MODE_CACHE["value"] = built
        return _display_mode_with_age(built, now=now)


_SET_RESOLUTION_CACHE: dict[str, Any] = {"path": None, "ts": 0.0, "candidates": []}
_SET_RESOLUTION_LOCK = threading.Lock()


def _coerce_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
        num = int(float(value))
    except Exception:
        return None
    return num if num > 0 else None


def _parse_refresh_to_int(value: Any) -> int | None:
    if value is None:
        return None
    candidate: float | None
    if isinstance(value, (int, float)):
        candidate = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            candidate = float(text)
        except Exception:
            if "/" in text:
                num, denom = text.split("/", 1)
                try:
                    candidate = float(num) / float(denom)
                except Exception:
                    return None
            else:
                return None
    if candidate is None or candidate <= 0:
        return None
    rounded = round(candidate)
    if abs(candidate - rounded) < 0.05:
        return int(rounded)
    return int(candidate)


def _target_refresh_hz() -> int | None:
    try:
        return _parse_refresh_to_int(globals().get("TARGET_FPS"))
    except Exception:
        return None


def _discover_set_resolution_executable(*, force: bool = False) -> tuple[Path | None, list[str]]:
    if not _is_windows:
        return (None, [])
    now = time.time()
    with _SET_RESOLUTION_LOCK:
        cached_path = _SET_RESOLUTION_CACHE.get("path")
        cached_ts = float(_SET_RESOLUTION_CACHE.get("ts") or 0.0)
        if cached_path and not force and (now - cached_ts) < 60.0:
            candidate_path = Path(str(cached_path))
            if candidate_path.is_file():
                return candidate_path, list(_SET_RESOLUTION_CACHE.get("candidates") or [])

        candidates: list[Path] = []
        for env_key in ("SET_RESOLUTION_EXE", "SETRESOLUTION_EXE"):
            env_val = os.environ.get(env_key)
            if env_val:
                try:
                    candidates.append(Path(env_val))
                except Exception:
                    pass

        base_root = APP_DIR.parent
        candidates.extend(
            [
                APP_DIR / "SetResolution.exe",
                APP_DIR / "tools" / "SetResolution.exe",
                base_root / "SetResolution.exe",
                base_root / "tools" / "SetResolution" / "SetResolution.exe",
                base_root / "tools" / "windows" / "SetResolution" / "SetResolution.exe",
            ]
        )

        seen: set[str] = set()
        checked: list[str] = []
        found: Path | None = None

        for cand in candidates:
            if not cand:
                continue
            try:
                path = cand if isinstance(cand, Path) else Path(cand)
            except Exception:
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            checked.append(str(path))
            if path.is_file():
                found = path
                break

        if found is None:
            search_roots = [base_root / "tools", base_root / "assets", APP_DIR]
            for root in search_roots:
                try:
                    if not root.exists():
                        continue
                except Exception:
                    continue
                try:
                    for idx, cand in enumerate(root.rglob("SetResolution.exe")):
                        path = cand
                        key = str(path).lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        checked.append(str(path))
                        if path.is_file():
                            found = path
                            break
                        if idx >= 25:
                            break
                except Exception:
                    continue
                if found is not None:
                    break

        _SET_RESOLUTION_CACHE["path"] = str(found) if found else None
        _SET_RESOLUTION_CACHE["ts"] = now
        _SET_RESOLUTION_CACHE["candidates"] = checked
        return found, checked


def _truncate_text(value: Any, *, limit: int = 2000) -> str:
    try:
        text = "" if value is None else str(value)
    except Exception:
        text = ""
    if limit <= 0:
        return text
    if len(text) <= limit:
        return text
    suffix = f"... (trimmed {len(text) - limit + 3} chars)"
    head = text[: max(0, limit - len(suffix))]
    return head + suffix
try:
    OFF_WATCHDOG_INTERVAL = max(10.0, float(os.environ.get("OFF_WATCHDOG_INTERVAL", "45")))
except Exception:
    OFF_WATCHDOG_INTERVAL = 45.0
# Assicura che le immagini restino a schermo: imposta image-duration lungo se non presente
if not any(a.startswith("--image-duration") for a in VLC_EXTRA_ARGS):
    VLC_EXTRA_ARGS.extend(["--image-duration=36000"])  # 10 ore
# Mitigazione errori ALSA/XDG: usa aout dummy e disabilita audio se non specificato
if not any(a.startswith("--aout=") for a in VLC_EXTRA_ARGS):
    VLC_EXTRA_ARGS.extend(["--aout=dummy", "--no-audio"])  # headless: niente audio

# ---------- Config persistence & backend init (ripristinate) ----------
def _set_startup_macs(source: Any | None) -> list[str]:
    global STARTUP_MACS
    if source is None:
        STARTUP_MACS = []
    else:
        STARTUP_MACS = _normalize_mac_collection(source)
    return STARTUP_MACS

def _resolve_startup_targets(source: Any | None = None) -> list[str]:
    if source is None:
        return list(STARTUP_MACS)
    return _normalize_mac_collection(source)

def load_persisted_framework():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            fw = data.get("framework")
            if fw and fw in BACKEND_CLASSES:
                current_framework["name"] = fw
                print(f"[CONFIG] Framework persistito: {fw}", flush=True)
            global USE_KMS, USE_HW_DECODER, TARGET_WIDTH, TARGET_HEIGHT, TARGET_FPS, SPLASH_BLACK, UDP_ENABLED, VIDEO_PATH, VLC_HTTP_PASSWORD, DEVICE_NAME, LOG_UDP_ENABLED, LOG_UDP_HOST, LOG_UDP_PORT, OVERLAY_FADE_OUT_ON_PLAY_S, OVERLAY_FADE_IN_ON_STOP_S, MEDIA_DIR, MEDIA_DIR_SOURCE, UDP_PORT, BEACON_ENABLED, BEACON_PORT, STARTUP_BROADCAST, STARTUP_PORT
            # MEDIA_DIR (opzionale) – accetta sia "media_dir" che "MEDIA_DIR"
            try:
                # L'ambiente ha priorità: se MEDIA_DIR è impostata, non sovrascrivere dal config
                env_mdir = os.environ.get("MEDIA_DIR")
                if env_mdir:
                    p = Path(str(env_mdir)).expanduser().resolve()
                    p.mkdir(parents=True, exist_ok=True)
                    MEDIA_DIR = p
                    MEDIA_DIR_SOURCE = "env"
                else:
                    mdir = data.get("media_dir", data.get("MEDIA_DIR"))
                    if mdir:
                        # Normalizza eventuale path Windows su macOS
                        raw_mdir = str(mdir)
                        try:
                            plat = platform.system().lower()
                        except Exception:
                            plat = ""
                        if (plat.startswith("darwin") or plat.startswith("mac")) and ("\\" in raw_mdir or ":\\" in raw_mdir or raw_mdir.startswith("C:")):
                            # Forza Desktop/media su macOS per path Windows errato
                            fixed = Path.home() / "Desktop" / "media"
                            fixed.mkdir(parents=True, exist_ok=True)
                            MEDIA_DIR = fixed
                            MEDIA_DIR_SOURCE = "config_windows_to_desktop"
                        else:
                            p = Path(raw_mdir).expanduser()
                            p.mkdir(parents=True, exist_ok=True)
                            MEDIA_DIR = p
                            MEDIA_DIR_SOURCE = "config"
            except Exception:
                pass
            if "USE_KMS" in data: USE_KMS = bool(data.get("USE_KMS"))
            if "USE_HW_DECODER" in data: USE_HW_DECODER = bool(data.get("USE_HW_DECODER"))
            if "TARGET_WIDTH" in data: TARGET_WIDTH = int(data.get("TARGET_WIDTH") or TARGET_WIDTH)
            if "TARGET_HEIGHT" in data: TARGET_HEIGHT = int(data.get("TARGET_HEIGHT") or TARGET_HEIGHT)
            if "TARGET_FPS" in data: TARGET_FPS = str(data.get("TARGET_FPS") or TARGET_FPS)
            if "SPLASH_BLACK" in data: SPLASH_BLACK = bool(data.get("SPLASH_BLACK"))
            if data.get("last_media"):
                lp = Path(data.get("last_media"))
                if lp.exists():
                    VIDEO_PATH = str(lp)
            # UDP_ENABLED rimosso: UDP è sempre attivo (default True)
            # UDP_ENABLED = bool(data.get("udp_enabled", data.get("UDP_ENABLED", True)))
            autoplay["enabled"] = bool(data.get("autoplay_enabled", data.get("AUTOPLAY_ENABLED", False)))
            if password := data.get("vlc_password"):
                VLC_HTTP_PASSWORD = str(password)
            if name := data.get("device_name"):
                DEVICE_NAME = str(name)
            # Overlay durations (optional)
            try:
                OVERLAY_FADE_OUT_ON_PLAY_S = float(data.get("overlay_fade_out_on_play_s", data.get("OVERLAY_FADE_OUT_ON_PLAY_S", OVERLAY_FADE_OUT_ON_PLAY_S)))
            except Exception:
                pass
            try:
                OVERLAY_FADE_IN_ON_STOP_S = float(data.get("overlay_fade_in_on_stop_s", data.get("OVERLAY_FADE_IN_ON_STOP_S", OVERLAY_FADE_IN_ON_STOP_S)))
            except Exception:
                pass
            # Autoplay fade seconds (optional)
            try:
                globals()["AUTOPLAY_FADE_SECONDS"] = float(data.get("autoplay_fade_seconds", data.get("AUTOPLAY_FADE_SECONDS", globals().get("AUTOPLAY_FADE_SECONDS", 1.0))))
            except Exception:
                pass
            # Log UDP config (optional)
            LOG_UDP_ENABLED = bool(data.get("log_udp_enabled", data.get("LOG_UDP_ENABLED", False)))
            LOG_UDP_HOST = str(data.get("log_udp_host", data.get("LOG_UDP_HOST", LOG_UDP_HOST or "")))
            try:
                LOG_UDP_PORT = int(data.get("log_udp_port", data.get("LOG_UDP_PORT", LOG_UDP_PORT)))
            except Exception:
                pass
            # Beacon config (optional)
            try:
                BEACON_ENABLED = bool(data.get("beacon_enabled", data.get("BEACON_ENABLED", BEACON_ENABLED)))
            except Exception:
                pass
            try:
                cand = data.get("beacon_port", data.get("BEACON_PORT", BEACON_PORT))
                if cand is not None:
                    BEACON_PORT = int(cand)
            except Exception:
                pass
            # Porta UDP comandi principale
            try:
                udp_cfg = data.get("udp_port", data.get("UDP_PORT"))
                if udp_cfg is not None:
                    udp_candidate = int(udp_cfg)
                    if 1024 <= udp_candidate <= 65535:
                        UDP_PORT = udp_candidate
                        print(f"[CONFIG] UDP_PORT configurato da file: {UDP_PORT}", flush=True)
                    else:
                        print(f"[CONFIG] udp_port fuori range ({udp_cfg}) - uso default {UDP_PORT}", flush=True)
            except Exception:
                print(f"[CONFIG] udp_port invalido ({udp_cfg}) - uso default {UDP_PORT}", flush=True)
            # OFF autostart flag (opzionale)
            try:
                globals()["OFF_AUTOSTART"] = bool(data.get("off_autostart", data.get("OFF_AUTOSTART", OFF_AUTOSTART)))
            except Exception:
                pass
            # OFF UDP port (opzionale)
            try:
                cand_udp = data.get("OFF_UDP_PORT", data.get("off_udp_port"))
                if cand_udp is not None:
                    globals()["OFF_UDP_PORT"] = int(cand_udp)
            except Exception:
                pass

            if "startup_macs" in data or "STARTUP_MACS" in data:
                _set_startup_macs(data.get("startup_macs", data.get("STARTUP_MACS")))
            if "startup_broadcast" in data or "STARTUP_BROADCAST" in data:
                val = data.get("startup_broadcast", data.get("STARTUP_BROADCAST"))
                if val is not None:
                    STARTUP_BROADCAST = str(val)
            if "startup_port" in data or "STARTUP_PORT" in data:
                val = data.get("startup_port", data.get("STARTUP_PORT"))
                try:
                    if val is not None:
                        STARTUP_PORT = int(val)
                except Exception:
                    pass

            # Log finale della cartella media selezionata dopo config/env
            try:
                print(f"[MEDIA] Selezionata: {MEDIA_DIR} (source={globals().get('MEDIA_DIR_SOURCE', 'auto')})", flush=True)
            except Exception:
                pass

            # Se abbiamo corretto un path Windows su macOS, aggiorna config.json per persistenza
            try:
                if globals().get('MEDIA_DIR_SOURCE') == 'config_windows_to_desktop':
                    data["media_dir"] = str(MEDIA_DIR)
                    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                    print(f"[MEDIA] Config aggiornato: media_dir -> {MEDIA_DIR}", flush=True)
            except Exception as e:
                print(f"[MEDIA] Impossibile aggiornare config: {e}", flush=True)
        except Exception as e:
            print(f"[CONFIG] Lettura config fallita: {e}", flush=True)
    # Always ensure device id exists
    try:
        _ensure_device_id()
    except Exception:
        pass

def _persist_framework(name: str):
    try:
        data = {}
        if CONFIG_FILE.exists():
            try: data = json.loads(CONFIG_FILE.read_text())
            except Exception: data = {}
        data["framework"] = name
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"[CONFIG] Salvato framework: {name}", flush=True)
    except Exception as e:
        print(f"[CONFIG] Errore salvataggio: {e}", flush=True)

def persist_settings(extra: dict | None = None):
    try:
        data = {}
        if CONFIG_FILE.exists():
            try: data = json.loads(CONFIG_FILE.read_text())
            except Exception: data = {}
        data.update({
            "framework": current_framework["name"],
            "MEDIA_DIR": str(MEDIA_DIR),
            "USE_KMS": USE_KMS,
            "USE_HW_DECODER": USE_HW_DECODER,
            "TARGET_WIDTH": TARGET_WIDTH,
            "TARGET_HEIGHT": TARGET_HEIGHT,
            "TARGET_FPS": TARGET_FPS,
            "SPLASH_BLACK": SPLASH_BLACK,
            "last_media": VIDEO_PATH,
            # "udp_enabled": UDP_ENABLED,  # Rimosso: UDP sempre attivo
            "autoplay_enabled": autoplay.get("enabled", False),
            "vlc_password": VLC_HTTP_PASSWORD,
            "device_name": DEVICE_NAME,
            "log_udp_enabled": LOG_UDP_ENABLED,
            "log_udp_host": LOG_UDP_HOST,
            "log_udp_port": LOG_UDP_PORT,
            "overlay_fade_out_on_play_s": OVERLAY_FADE_OUT_ON_PLAY_S,
            "overlay_fade_in_on_stop_s": OVERLAY_FADE_IN_ON_STOP_S,
            "autoplay_fade_seconds": AUTOPLAY_FADE_SECONDS,
            "OFF_UDP_PORT": OFF_UDP_PORT,
            "OFF_HOST": OFF_HOST,
            "OFF_PORT": OFF_PORT,
            "off_autostart": OFF_AUTOSTART,
            "udp_port": UDP_PORT,
            "beacon_enabled": BEACON_ENABLED,
            "beacon_port": BEACON_PORT,
            "startup_macs": STARTUP_MACS,
            "startup_broadcast": STARTUP_BROADCAST,
            "startup_port": STARTUP_PORT,
        })
        # Rimuovi chiavi legacy non più supportate
        for legacy_key in ("osc_enabled", "OSC_ENABLED"):
            data.pop(legacy_key, None)
        if extra: data.update(extra)
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[CONFIG] Persist settings error: {e}", flush=True)

# ---------- GUI UDP Log helpers ----------
def set_log_udp_target(host: str, port: int, enabled: bool, persist: bool = False):
    global LOG_UDP_HOST, LOG_UDP_PORT, LOG_UDP_ENABLED
    LOG_UDP_HOST = str(host or "")
    try:
        LOG_UDP_PORT = int(port)
    except Exception:
        pass
    LOG_UDP_ENABLED = bool(enabled)
    if persist:
        try:
            persist_settings({
                "log_udp_enabled": LOG_UDP_ENABLED,
                "log_udp_host": LOG_UDP_HOST,
                "log_udp_port": LOG_UDP_PORT,
            })
        except Exception:
            pass

def gui_log(msg: str, level: str = "INFO", kind: str = "event", data: dict | None = None):
    """Invia una riga di log alla GUI via UDP se configurato.
    Formato: JSON { t, level, kind, device, msg, data }
    """
    if not LOG_UDP_ENABLED or not LOG_UDP_HOST:
        return
    try:
        payload = {
            "t": time.time(),
            "level": level,
            "kind": kind,
            "device": DEVICE_NAME or get_ip(),
            "msg": str(msg),
        }
        if data:
            try:
                # evitiamo oggetti non serializzabili
                payload["data"] = json.loads(json.dumps(data, default=str))
            except Exception:
                payload["data"] = {"_raw": str(data)}
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.05)
        s.sendto(json.dumps(payload, ensure_ascii=False).encode("utf-8"), (LOG_UDP_HOST, int(LOG_UDP_PORT)))
        try:
            s.close()
        except Exception:
            pass
    except Exception:
        # non interrompere il flusso in caso di errori di rete
        pass

def init_backend(name: str, persist: bool = False):
    """Inizializza il backend richiesto con fallback sicuri.
    Ordine fallback: richiesto → off → cvlc → gst.
    Su ambienti senza GStreamer è preferibile evitare gst come fallback automatico,
    perché il backend gst è sempre "istanziabile" ma fallirà a runtime senza GI.
    """
    tried: list[str] = []
    def _try(n: str) -> bool:
        cls = BACKEND_CLASSES.get(n)
        if cls is None:
            return False
        try:
            be = cls(controller=globals())
        except Exception as exc:
            print(f"[BACKEND] Init fallita per {n}: {exc}", flush=True)
            return False
        current_framework["name"] = n
        current_framework["backend"] = be
        print(f"[BACKEND] Inizializzato backend: {n}", flush=True)
        try:
            gui_log("backend_init", data={"name": n})
        except Exception:
            pass
        if persist:
            _persist_framework(n)
        return True

    # 1) Prova quello richiesto
    if name and name not in tried:
        tried.append(name)
        if _try(name):
            return
    # 2) Fallback a off (se diverso dal richiesto)
    if name != "off" and "off" not in tried and _try("off"):
        return
    # 3) Fallback a cvlc
    if "cvlc" not in tried and _try("cvlc"):
        return
    # 4) Ultimo fallback a gst
    if "gst" not in tried and _try("gst"):
        return
    # 5) Nessun backend disponibile
    raise RuntimeError("Nessun backend inizializzabile (pyqt non disponibile o gst/cvlc mancanti)")

def ensure_backend():
    if current_framework["backend"] is None:
        # Persist per correggere eventuali framework non avviabili (es. pyqt senza plugin Qt)
        init_backend(current_framework["name"], persist=True)

# Carica config e backend verrà fatto dopo l'inizializzazione degli stati globali

# ---------- UDP Listener opzionale ----------
UDP_PORT = 7777            # Porta configurata (desiderata)
UDP_ACTUAL_PORT = None     # Porta effettiva (può differire per fallback)
UDP_ERROR_MESSAGE = None   # Messaggio di errore se il bind fallisce

def _udp_bind_scan(port: int, attempts: int = 10) -> tuple[int | None, str | None]:
    """Tenta bind su port, se fallisce prova porte successive (port+1,...)."""
    last_err = None
    for p in range(int(port), int(port) + int(attempts)):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("0.0.0.0", p))
            s.setblocking(False)
            try:
                s.close()
            except Exception:
                pass
            return p, None
        except Exception as e:
            last_err = str(e)
        finally:
            try:
                s.close()
            except Exception:
                pass
    return None, last_err


def _udp_handle_plain_text(
    raw: str,
    *,
    addr: tuple[str, int] | None,
    schedule: Callable[[Callable[[], Any]], None],
    send_reply: Callable[[str], None],
) -> bool:
    stripped = raw.strip()
    if not stripped:
        return False
    tokens = stripped.split()
    if not tokens:
        return False
    cmd_token = tokens[0]
    cmd = cmd_token.upper()
    args_tokens = tokens[1:]
    remainder = stripped[len(cmd_token):].strip()

    print(f"[UDP-PLAIN] {raw}", flush=True)

    def _parse_bool(value: str | None, default: bool = False) -> bool:
        if value is None:
            return default
        normalized = value.strip().lower()
        return normalized in {"1", "true", "on", "yes", "enable", "enabled"}

    def _parse_float(value: str | None, default: float | None = None) -> float | None:
        if value is None:
            return default
        try:
            return float(value)
        except Exception:
            return default

    def _parse_int(value: str | None, default: int | None = None) -> int | None:
        if value is None:
            return default
        try:
            return int(float(value))
        except Exception:
            return default

    def _split_kv(input_tokens: list[str]) -> tuple[dict[str, str], list[str]]:
        kv: dict[str, str] = {}
        rest: list[str] = []
        for tok in input_tokens:
            if "=" in tok:
                key, value = tok.split("=", 1)
                kv[key.strip().lower()] = value.strip()
            else:
                rest.append(tok)
        return kv, rest

    def _display_payload_from_tokens(tokens: list[str], suffix: str) -> dict[str, Any] | None:
        width: int | None = None
        height: int | None = None
        refresh: int | None = None
        force_discovery = False
        use_legacy = False
        dry_run = False

        spec_candidates = list(tokens)
        if suffix and suffix not in spec_candidates:
            spec_candidates.insert(0, suffix)

        kv_args, free_tokens = _split_kv(spec_candidates)

        if "width" in kv_args:
            width = _parse_int(kv_args.get("width"))
        if "height" in kv_args:
            height = _parse_int(kv_args.get("height"))
        if "refresh" in kv_args:
            refresh = _parse_int(kv_args.get("refresh"))
        if "refresh_hz" in kv_args:
            refresh = _parse_int(kv_args.get("refresh_hz"))
        if "hz" in kv_args:
            refresh = _parse_int(kv_args.get("hz"))
        if "legacy" in kv_args:
            use_legacy = _parse_bool(kv_args.get("legacy"), True)
        if "exe" in kv_args:
            use_legacy = _parse_bool(kv_args.get("exe"), True)
        if "force_discovery" in kv_args or "discovery" in kv_args:
            force_discovery = _parse_bool(kv_args.get("force_discovery", kv_args.get("discovery")), True)
        if "dry" in kv_args or "dry_run" in kv_args:
            dry_run = _parse_bool(kv_args.get("dry_run", kv_args.get("dry")), True)

        for item in list(free_tokens):
            lower = item.lower()
            if "x" in lower:
                try:
                    wh, _, tail = lower.partition("@")
                    w, _, h = wh.partition("x")
                    if width is None:
                        width = int(w)
                    if height is None:
                        height = int(h)
                    if tail and refresh is None:
                        refresh = int(float(tail))
                    continue
                except Exception:
                    pass
            val_as_int = _parse_int(item)
            if val_as_int is not None:
                if width is None:
                    width = val_as_int
                elif height is None:
                    height = val_as_int
                elif refresh is None:
                    refresh = val_as_int
            elif item.upper() in {"LEGACY", "EXE"}:
                use_legacy = True
            elif item.upper() in {"DRY", "DRYRUN", "TEST"}:
                dry_run = True
            elif item.upper() in {"DISCOVERY", "FORCE_DISCOVERY"}:
                force_discovery = True

        if width is None or height is None:
            return None
        payload: dict[str, Any] = {"width": width, "height": height}
        if refresh is not None:
            payload["refresh_hz"] = refresh
        if force_discovery:
            payload["force_discovery"] = True
        if dry_run:
            payload["dry_run"] = True
        if use_legacy:
            payload["use_legacy"] = True
        return payload

    if cmd == "PLAY":
        if not args_tokens:
            schedule(resume_play)
            send_reply("OK PLAY")
            return True
        filename_token = args_tokens[0]
        kv_args, _ = _split_kv(args_tokens[1:])
        loop_flag = _parse_bool(kv_args.get("loop")) if "loop" in kv_args else None
        fade_in = _parse_float(kv_args.get("fade"), _parse_float(kv_args.get("fade_in"), 0.5))
        fade_out = _parse_float(kv_args.get("fade_out"))

        def _do_play_alias(filename=filename_token, loop_flag=loop_flag, fade_in=fade_in, fade_out=fade_out):
            try:
                kwargs: dict[str, Any] = {
                    "filename": filename,
                    "loop": loop_flag,
                    "fade_in_seconds": fade_in if fade_in is not None else 0.5,
                    "fade_out_seconds": fade_out,
                    "hide_splash_first": True,
                }
                api_play(**kwargs)
            except Exception as exc:
                print(f"[UDP] PLAYFILE error: {exc}", flush=True)

        schedule(_do_play_alias)
        send_reply("OK PLAYFILE")
        return True
    if cmd == "STOP":
        stop_secs = _parse_float(args_tokens[0]) if args_tokens else None
        if stop_secs and stop_secs > 0:
            schedule(lambda seconds=stop_secs: api_visual_ftb(seconds))
        else:
            schedule(api_stop)
        send_reply("OK STOP")
        return True
    if cmd == "NEXT":
        schedule(api_playlist_next)
        send_reply("OK NEXT")
        return True
    if cmd == "PREV":
        schedule(api_playlist_prev)
        send_reply("OK PREV")
        return True
    if cmd == "SET":
        idx = _parse_int(args_tokens[0]) if args_tokens else None
        if idx is not None:
            schedule(lambda idx=idx: api_playlist_jump(index=idx))
            send_reply(f"OK SET {idx}")
        else:
            send_reply("ERR SET missing index")
        return True
    if cmd == "JUMP":
        track = _parse_int(args_tokens[0]) if args_tokens else None
        if track is not None:
            idx = max(0, track - 1)
            schedule(lambda idx=idx: api_playlist_jump(index=idx))
            send_reply(f"OK JUMP {track}")
        else:
            send_reply("ERR JUMP missing index")
        return True
    if cmd == "LOOP":
        on = 1 if (args_tokens and _parse_bool(args_tokens[0], True)) else 0
        schedule(lambda: api_playlist_loop(on=on))
        send_reply(f"OK LOOP {on}")
        return True
    if cmd == "PAUSE":
        schedule(pause_play)
        send_reply("OK PAUSE")
        return True
    if cmd in {"RESUME", "PLAYRESUME"}:
        schedule(resume_play)
        send_reply("OK RESUME")
        return True
    if cmd in {"GO_TO_START", "GOTO_START", "REWIND"}:
        schedule(api_go_to_start)
        send_reply("OK REWIND")
        return True
    if cmd == "STARTUP":
        targets = args_tokens if args_tokens else None
        schedule(lambda macs=targets: _trigger_startup(macs=macs))
        send_reply("OK STARTUP")
        return True
    if cmd == "FADE":
        if len(args_tokens) == 1:
            secs = _parse_float(args_tokens[0])
            if secs is not None and secs >= 0:

                def _do(seconds=secs):
                    try:
                        globals()["OVERLAY_FADE_OUT_ON_PLAY_S"] = float(seconds)
                        globals()["OVERLAY_FADE_IN_ON_STOP_S"] = float(seconds)
                        globals()["AUTOPLAY_FADE_SECONDS"] = float(seconds)
                        persist_settings({
                            "overlay_fade_out_on_play_s": float(seconds),
                            "overlay_fade_in_on_stop_s": float(seconds),
                            "autoplay_fade_seconds": float(seconds),
                        })
                    except Exception:
                        pass
                    return False

                schedule(_do)
                send_reply(f"OK FADE DEFAULT {secs}")
            else:
                send_reply("ERR FADE seconds invalid")
            return True
        if len(args_tokens) >= 2:
            target = _parse_float(args_tokens[0])
            seconds = _parse_float(args_tokens[1], 1.0)
            if target is not None:
                schedule(lambda target=target, seconds=seconds or 1.0: api_overlay_fade(target, seconds))
                send_reply(f"OK FADE {target}")
            else:
                send_reply("ERR FADE target invalid")
            return True
        send_reply("ERR FADE usage")
        return True
    if cmd == "BRIGHTNESS":
        if len(args_tokens) >= 2:
            value = _parse_float(args_tokens[0])
            seconds = _parse_float(args_tokens[1], 0.5)
            if value is not None:
                schedule(lambda v=value, s=seconds or 0.5: api_visual_brightness(v, s))
                send_reply(f"OK BRIGHTNESS {value}")
            else:
                send_reply("ERR BRIGHTNESS invalid value")
        else:
            send_reply("ERR BRIGHTNESS usage")
        return True
    if cmd == "OVERLAY_SHOW":
        alpha = _parse_float(args_tokens[0], 1.0) if args_tokens else 1.0
        schedule(lambda alpha=alpha: api_overlay_show(alpha or 1.0))
        send_reply(f"OK OVERLAY_SHOW {alpha}")
        return True
    if cmd == "OVERLAY_HIDE":
        schedule(api_overlay_hide)
        send_reply("OK OVERLAY_HIDE")
        return True
    if cmd == "OVERLAY_ALPHA":
        alpha = _parse_float(args_tokens[0]) if args_tokens else None
        if alpha is not None:
            schedule(lambda alpha=alpha: api_overlay_show(alpha))
            send_reply(f"OK OVERLAY_ALPHA {alpha}")
        else:
            send_reply("ERR OVERLAY_ALPHA invalid")
        return True
    if cmd == "SPLASH_SHOW":
        text = remainder if remainder else ""
        if text:
            schedule(lambda txt=text: print(f"[SPLASH] testo richiesto via UDP: {txt}", flush=True))
        schedule(api_show_splash)
        send_reply("OK SPLASH_SHOW")
        return True
    if cmd == "SPLASH_HIDE":
        schedule(api_hide_splash)
        send_reply("OK SPLASH_HIDE")
        return True
    if cmd == "STATUS":
        try:
            playing = fsm.get("state") == "playing"
            cur = None
            try:
                if playlist.get("index", -1) >= 0 and playlist.get("items"):
                    cur = Path(playlist["items"][playlist["index"]]).name
            except Exception:
                pass
            port = UDP_ACTUAL_PORT
            reply = f"OK playing={int(playing)} current={cur or '-'} port={port}"
            send_reply(reply)
        except Exception:
            send_reply("ERR STATUS")
        return True
    if cmd in {"FORCE_720P", "FORCE720P"}:
        payload = _display_payload_from_tokens(args_tokens, remainder)
        if payload is None:
            payload = {"width": 1280, "height": 720}
        payload.setdefault("width", 1280)
        payload.setdefault("height", 720)

        def _do_force(payload=payload):
            try:
                api_display_mode_force_720p(payload)
            except Exception as exc:
                print(f"[UDP] FORCE_720P error: {exc}", flush=True)

        schedule(_do_force)
        send_reply("OK FORCE_720P")
        return True
    if cmd == "DISPLAY":
        payload = _display_payload_from_tokens(args_tokens, remainder)
        if payload is None:
            send_reply("ERR DISPLAY usage")
            return True

        def _do_display(payload=payload):
            try:
                api_display_mode_apply(payload)
            except Exception as exc:
                print(f"[UDP] DISPLAY error: {exc}", flush=True)

        schedule(_do_display)
        send_reply("OK DISPLAY")
        return True
    if cmd == "FASTSTART":
        media = remainder or (args_tokens[0] if args_tokens else "")
        media = media.strip()
        if not media:
            send_reply("ERR FASTSTART missing filename")
            return True

        def _do_faststart(media=media):
            try:
                if os.path.isabs(media):
                    api_faststart_prepare(path=media)
                else:
                    api_faststart_prepare(filename=media)
            except Exception as exc:
                print(f"[UDP] FASTSTART error: {exc}", flush=True)

        schedule(_do_faststart)
        send_reply(f"OK FASTSTART {media}")
        return True
    if cmd == "FASTGO":
        seconds = _parse_float(args_tokens[0], 0.0) if args_tokens else 0.0

        def _do_fastgo(seconds=seconds):
            try:
                if seconds and seconds > 0:
                    api_faststart_go(seconds=seconds)
                else:
                    api_faststart_go()
            except Exception as exc:
                print(f"[UDP] FASTGO error: {exc}", flush=True)

        schedule(_do_fastgo)
        send_reply("OK FASTGO")
        return True
    if cmd == "PLAYFILE":
        kv_args, remainder_tokens = _split_kv(args_tokens)
        filename = remainder_tokens[0] if remainder_tokens else (kv_args.get("filename") or kv_args.get("file"))
        if not filename and remainder:
            filename = remainder_tokens[0] if remainder_tokens else remainder.split()[0]
        if not filename:
            send_reply("ERR PLAYFILE missing filename")
            return True
        loop_flag = None
        if "loop" in kv_args:
            loop_flag = _parse_bool(kv_args.get("loop"), True)
        fade_in = _parse_float(kv_args.get("fade"), _parse_float(kv_args.get("fade_in"), 0.5))
        fade_out = _parse_float(kv_args.get("fade_out"))

        def _do_playfile(filename=filename, loop_flag=loop_flag, fade_in=fade_in, fade_out=fade_out):
            try:
                kwargs: dict[str, Any] = {
                    "filename": filename,
                    "loop": loop_flag,
                    "fade_in_seconds": fade_in if fade_in is not None else 0.5,
                    "fade_out_seconds": fade_out,
                    "hide_splash_first": True,
                }
                api_play(**kwargs)
            except Exception as exc:
                print(f"[UDP] PLAYFILE error: {exc}", flush=True)

        schedule(_do_playfile)
        send_reply(f"OK PLAYFILE {filename}")
        return True
    if cmd == "PLAYLIST":
        if not args_tokens:
            send_reply("ERR PLAYLIST usage")
            return True
        sub_cmd = args_tokens[0].upper()
        sub_args = args_tokens[1:]
        if sub_cmd == "APPLY":
            kv_args, rest_tokens = _split_kv(sub_args)
            items_spec = kv_args.get("items") or (rest_tokens[0] if rest_tokens else remainder)
            if not items_spec:
                send_reply("ERR PLAYLIST items required")
                return True
            items = [part.strip() for part in items_spec.split(",") if part.strip()]
            if not items:
                send_reply("ERR PLAYLIST no valid items")
                return True
            loop_flag = _parse_bool(kv_args.get("loop"), True)

            def _do_playlist_apply(items=items, loop_flag=loop_flag):
                try:
                    payload = {"items": list(items), "loop": bool(loop_flag)}
                    api_playlist_apply(payload)
                except Exception as exc:
                    print(f"[UDP] PLAYLIST APPLY error: {exc}", flush=True)

            schedule(_do_playlist_apply)
            send_reply(f"OK PLAYLIST {len(items)}")
            return True
        if sub_cmd in {"STATUS", "INFO"}:
            try:
                status = api_playlist_status()
                send_reply(json.dumps(status))
            except Exception:
                send_reply("ERR PLAYLIST STATUS")
            return True
        send_reply("ERR PLAYLIST unknown subcommand")
        return True
    if cmd == "AUTOPLAY":
        if not args_tokens:
            send_reply("ERR AUTOPLAY usage")
            return True
        state_token = args_tokens[0]
        kv_args, _ = _split_kv(args_tokens[1:])
        enabled = _parse_bool(state_token, True)
        delay = _parse_float(kv_args.get("delay"))
        restart = _parse_bool(kv_args.get("restart"), True)

        def _do_autoplay(enabled=enabled, restart=restart, delay=delay):
            try:
                _autoplay_set_enabled(enabled, restart=restart, delay=delay)
            except Exception as exc:
                print(f"[UDP] AUTOPLAY error: {exc}", flush=True)

        schedule(_do_autoplay)
        send_reply(f"OK AUTOPLAY {int(enabled)}")
        return True

    return False

def _udp_thread():
    global UDP_ACTUAL_PORT, UDP_ERROR_MESSAGE
    
    desired = int(UDP_PORT)
    bound, err = _udp_bind_scan(desired, attempts=10)
    if bound is None:
        error_msg = f"Impossibile usare porta {desired} (+fallback). Ultimo errore: {err}"
        print(f"[UDP] Errore bind: {error_msg}", flush=True)
        UDP_ACTUAL_PORT = None
        UDP_ERROR_MESSAGE = error_msg
        return
    UDP_ACTUAL_PORT = bound
    UDP_ERROR_MESSAGE = None  # Reset errore se bind ha successo
    print(f"[UDP] Thread avviato su porta {UDP_ACTUAL_PORT} (configurata={desired})", flush=True)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0", UDP_ACTUAL_PORT))
        # Usa una timeout breve per evitare busy loop e spam di EAGAIN (Errno 35) su macOS
        # Nota: settimeout(x) imposta socket in modalità bloccante con timeout x secondi
        s.settimeout(0.2)
    except Exception as e:
        print(f"[UDP] Race bind finale fallita: {e}", flush=True)
        UDP_ACTUAL_PORT = None
        return
    
    # Loop principale UDP (sempre attivo)
    while True:
        try:
            data, addr = s.recvfrom(8192)
            raw = data.decode("utf-8", errors="ignore").strip()
            if not raw:
                continue
            # Prova JSON, fallback a testo semplice
            try:
                payload = json.loads(raw)
                is_json = True
            except Exception:
                is_json = False
            if not is_json:
                def _reply(message: str) -> None:
                    try:
                        s.sendto(message.encode("utf-8"), addr)
                    except Exception:
                        pass

                def _schedule(func: Callable[[], Any]) -> None:
                    schedule_action(None, func)

                if _udp_handle_plain_text(raw, addr=addr, schedule=_schedule, send_reply=_reply):
                    continue

                print(f"[UDP-PLAIN] Ignoro comando: {raw}", flush=True)
                continue
            cmd = payload.get("cmd") if isinstance(payload, dict) else None
            if not cmd:
                continue
            in_time = payload.get("in_time")
            # Log dettagliato solo in debug per evitare flood nei log di produzione,
            # specialmente per i probe di sync (cmd=time).
            try:
                logger = logging.getLogger(__name__)
                logger.debug("[UDP] Cmd=%s from=%s in_time=%s", cmd, addr, in_time)
            except Exception:
                pass
            try:
                # Mantieni il forwarding alla GUI ma con livello DEBUG, così la
                # GUI può filtrare o abilitare solo quando necessario.
                gui_log(f"UDP cmd={cmd}", level="DEBUG", kind="udp", data={"from": addr[0], "in_time": in_time})
            except Exception:
                pass
            # Lightweight time query for app-level sync (SNTP-like)
            if cmd == "time":
                try:
                    t0 = int(payload.get("t0") or 0)
                except Exception:
                    t0 = 0
                t1 = time.time_ns()
                t2 = time.time_ns()
                try:
                    s.sendto(json.dumps({"ok": True, "t0": t0, "t1": t1, "t2": t2}).encode("utf-8"), addr)
                except Exception:
                    pass
                continue
            if cmd == "ping":
                duration = int(payload.get("duration_ms", 200))
                schedule_action(in_time, lambda: api_ping(duration))
            elif cmd in {"go_to_start", "goto_start", "rewind"}:
                schedule_action(in_time, api_go_to_start)
            elif cmd == "play":
                path = payload.get("path"); filename = payload.get("filename"); loop = payload.get("loop")
                fade_in = float(payload.get("fade_in_seconds", 0.5)); fade_out = payload.get("fade_out_seconds")
                def _do():
                    try:
                        api_play(path=path, filename=filename, loop=loop, hide_splash_first=True,
                                 fade_in_seconds=fade_in, fade_out_seconds=fade_out, in_time=None)
                    except Exception as e:
                        print(f"[UDP] Errore play: {e}", flush=True)
                schedule_action(in_time, _do)
            elif cmd == "pause":
                schedule_action(in_time, pause_play)
            elif cmd == "resume":
                schedule_action(in_time, resume_play)
            elif cmd == "stop":
                stop_secs = 0.0
                try:
                    candidate = payload.get("seconds")
                    if candidate is not None:
                        stop_secs = float(candidate)
                except Exception:
                    stop_secs = 0.0
                if stop_secs > 0.0:
                    schedule_action(in_time, lambda seconds=stop_secs: api_visual_ftb(seconds))
                else:
                    schedule_action(in_time, api_stop)
            elif cmd == "next":
                schedule_action(in_time, api_playlist_next)
            elif cmd == "prev":
                schedule_action(in_time, api_playlist_prev)
            elif cmd == "jump":
                try:
                    index = int(payload.get("index"))
                    schedule_action(in_time, lambda: api_playlist_jump(index=index))
                except Exception:
                    pass
            elif cmd == "jump_track":
                try:
                    # Accept keys: n, track, number (1-based)
                    raw = payload.get("n", payload.get("track", payload.get("number")))
                    t = int(raw)
                    idx = max(0, t - 1)
                    schedule_action(in_time, lambda idx=idx: api_playlist_jump(index=idx))
                except Exception:
                    pass
            elif cmd == "loop":
                on = 1 if payload.get("on", True) else 0
                schedule_action(in_time, lambda: api_playlist_loop(on=on))
            elif cmd == "overlay_fade":
                target = float(payload.get("target", 0.0)); seconds = float(payload.get("seconds", 1.0))
                schedule_action(in_time, lambda: api_overlay_fade(target, seconds))
            elif cmd == "brightness":
                try:
                    value = float(payload.get("value"))
                except Exception:
                    value = None
                if value is None:
                    continue
                seconds = float(payload.get("seconds", 0.5))
                schedule_action(in_time, lambda v=value, s=seconds: api_visual_brightness(v, s))
            elif cmd == "overlay_show":
                alpha = float(payload.get("alpha", 1.0))
                schedule_action(in_time, lambda: api_overlay_show(alpha))
            elif cmd == "overlay_hide":
                schedule_action(in_time, api_overlay_hide)
            elif cmd == "splash_show":
                # facoltativo: text nel payload
                txt = str(payload.get("text", ""))
                if txt:
                    print(f"[SPLASH] testo payload: {txt}", flush=True)
                schedule_action(in_time, api_show_splash)
            elif cmd == "splash_hide":
                schedule_action(in_time, api_hide_splash)
            elif cmd == "startup":
                macs = payload.get("macs")
                broadcast = payload.get("broadcast")
                port = payload.get("port")
                schedule_action(in_time, lambda macs=macs, b=broadcast, p=port: _trigger_startup(macs=macs, broadcast=b, port=p))
            elif cmd == "shutdown":
                schedule_action(in_time, api_shutdown)
        except socket.timeout:
            # Nessun pacchetto arrivato entro il timeout: loop tranquillo
            continue
        except BlockingIOError:
            # Non-blocking residual (in caso di modifiche): cedi CPU e continua
            time.sleep(0.01)
            continue
        except OSError as e:
            # Ignora EAGAIN/EWOULDBLOCK senza loggare
            if getattr(e, 'errno', None) in (errno.EAGAIN, errno.EWOULDBLOCK):
                time.sleep(0.01)
                continue
            print(f"[UDP] Errore loop (OS): {e}", flush=True)
        except Exception as e:
            print(f"[UDP] Errore loop: {e}", flush=True)
    try:
        s.close()
    except Exception:
        pass
    print("[UDP] Thread terminato", flush=True)

def udp_status():
    return {
        "ok": True,
        "enabled": True,  # UDP sempre attivo
        "configured_port": int(UDP_PORT),
        "actual_port": int(UDP_ACTUAL_PORT) if UDP_ACTUAL_PORT else None,
        "fallback_used": (UDP_ACTUAL_PORT is not None and int(UDP_ACTUAL_PORT) != int(UDP_PORT)),
        "error_message": UDP_ERROR_MESSAGE if UDP_ERROR_MESSAGE else None,
    }

def udp_rebind(port: int | None = Body(None)):  # type: ignore
    global UDP_PORT
    if port is not None:
        try:
            if 1024 <= int(port) <= 65535:
                UDP_PORT = int(port)
            else:
                return JSONResponse(status_code=400, content={"ok": False, "error": "Porta fuori range (1024-65535)"})
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "Porta invalida"})
    threading.Thread(target=_udp_thread, daemon=True).start()
    try:
        persist_settings()
    except Exception:
        pass
    return udp_status()

# ---------- Time endpoint (multi-platform, no deps) ----------
def api_time_now():
    try:
        return {"ok": True, "now_ns": int(time.time_ns())}
    except Exception:
        # Fallback using seconds
        return {"ok": True, "now_ns": int(time.time() * 1e9)}

# ---------- Scheduling helper ----------
def schedule_action(in_time: float | None, func, *args, **kwargs):
    """Programma l'esecuzione di func.
    in_time può essere:
      - None o 0: immediato
      - valore >= 1e9: epoch secondi
      - valore piccolo (<1e9): interpretato come delay relativo in secondi
    Ritorna delay effettivo.
    """
    # Usa un token per poter invalidare tutte le azioni pianificate (hard stop)
    created_epoch = globals().get("SCHEDULE_EPOCH", 0)

    def _guarded_call():
        # Se nel frattempo è stato incrementato l'epoch, salta l'azione
        if created_epoch != globals().get("SCHEDULE_EPOCH", 0):
            return False
        try:
            func(*args, **kwargs)
        except Exception:
            pass
        return False

    if not in_time:
        GLib.idle_add(_guarded_call)
        return 0.0
    try:
        t = float(in_time)
        # Tolleranza: se arrivano millisecondi (es. epoch ms), convertili in secondi
        if t > 1e10:  # ~anno 2286 in secondi; qualsiasi valore maggiore è probabilmente ms
            t = t / 1000.0
    except Exception:
        GLib.idle_add(_guarded_call)
        return 0.0
    now = time.time()
    if t >= 1e9:  # epoch
        delay = max(0.0, t - now)
    else:
        delay = max(0.0, t)
    if delay == 0:
        GLib.idle_add(_guarded_call)
    else:
        use_timer = False
        if os.environ.get("PYTEST_CURRENT_TEST"):
            use_timer = True
        ms = int(delay * 1000)
        if not use_timer:
            try:
                GLib.timeout_add(ms, _guarded_call)
            except Exception:
                use_timer = True
        if use_timer:
            try:
                t = threading.Timer(delay, lambda: GLib.idle_add(_guarded_call))
                t.daemon = True
                t.start()
            except Exception:
                GLib.timeout_add(ms, _guarded_call)
    return delay
# Crea un video di test se non esiste
def create_test_video():
    if not Path(VIDEO_PATH).exists():
        print(f"[INIT] Creo video di test: {VIDEO_PATH}", flush=True)
        # Crea un video di test usando GStreamer
        cmd = f'gst-launch-1.0 videotestsrc pattern=0 num-buffers=150 ! "video/x-raw,width=1280,height=720,framerate=25/1" ! x264enc ! h264parse ! mp4mux ! filesink location="{VIDEO_PATH}"'
        os.system(cmd)
        if Path(VIDEO_PATH).exists():
            print(f"[INIT] Video di test creato: {VIDEO_PATH}", flush=True)
        else:
            print(f"[INIT] ERRORE: Impossibile creare video di test", flush=True)

# Disabilita la creazione automatica del video test salvo esplicita richiesta (CREATE_TEST_VIDEO=1)
if os.environ.get("CREATE_TEST_VIDEO", "0") == "1":
    create_test_video()
# ----------------------------

Gst.init(None)
# GObject.threads_init()

@asynccontextmanager
async def _lifespan(app: FastAPI):
    print("[STARTUP] Avvio applicazione", flush=True)
    loop = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
        except Exception:
            loop = None
    except Exception:
        loop = None
    if loop is not None:
        globals()["ASYNC_LOOP"] = loop

    def start_glib_loop():
        try:
            print("[STARTUP] Avvio main loop GLib", flush=True)
            threading.Thread(target=main_loop.run, daemon=True).start()
            time.sleep(0.5)
            print("[STARTUP] Main loop avviato (splash su richiesta)", flush=True)
        except Exception as e:
            print(f"[STARTUP] GLib thread errore: {e}", flush=True)

    threading.Thread(target=start_glib_loop, daemon=True).start()
    # Avvio UDP (sempre attivo)
    try:
        threading.Thread(target=_udp_thread, daemon=True).start()
    except Exception as e:
        print(f"[STARTUP] UDP thread errore: {e}", flush=True)
    if autoplay.get("enabled"):
        GLib.idle_add(lambda: (_autoplay_schedule_initial(), False)[1])
    else:
        def _manual_bootstrap():
            _autoplay_prepare_manual_bootstrap()
            return False
        GLib.timeout_add(250, _manual_bootstrap)

    # Overlay probe + show
    def _overlay_boot_probe():
        try:
            if OVERLAY_ENABLED:
                if OVERLAY_USE_KMS and int(OVERLAY_KMS_PLANE_ID) <= 0:
                    print("[STARTUP] Overlay abilitato ma plane non impostato: eseguo auto-probe…", flush=True)
                    res = overlay_probe(auto_persist=True)
                    print(f"[STARTUP] Overlay probe: {res}", flush=True)
                try:
                    overlay_show(alpha=1.0)
                except Exception as e:
                    print(f"[STARTUP] Overlay show fallito: {e}", flush=True)
        except Exception as e:
            print(f"[STARTUP] Overlay probe errore: {e}", flush=True)
    pass  # overlay boot probe removed

    # OFF autostart
    def _off_autostart():
        try:
            if globals().get("OFF_AUTOSTART") and current_framework.get("name") == "off":
                print("[STARTUP] OFF autostart attivo: avvio OFF-player…", flush=True)
                res = _off_start()
                print(f"[STARTUP] OFF autostart esito: {res}", flush=True)
                if res.get("ok") and res.get("running"):
                    ready = _wait_off_http_ready(res.get("port"), timeout=6.0, interval=0.25)
                    if ready:
                        print("[STARTUP] OFF-player risponde all'HTTP", flush=True)
                    else:
                        print("[STARTUP] OFF-player non risponde ancora all'HTTP (timeout)", flush=True)
        except Exception as e:
            print(f"[STARTUP] OFF autostart errore: {e}", flush=True)
    threading.Thread(target=_off_autostart, daemon=True).start()

    # Startup UDP beacon (best-effort) per auto-discovery della GUI
    def _startup_beacon():
        try:
            _emit_discovery_beacon(reason="startup")
        except Exception as e:
            try:
                print(f"[BEACON] Errore invio beacon: {e}", flush=True)
            except Exception:
                pass
    try:
        threading.Thread(target=_startup_beacon, name="headless-beacon", daemon=True).start()
    except Exception:
        pass

    # Yield to run app
    try:
        yield
    finally:
        # Best-effort shutdown hooks
        try:
            globals()["UDP_ENABLED"] = False
        except Exception:
            pass
        try:
            GLib.idle_add(lambda: (main_loop.quit(), False)[1])
        except Exception:
            pass

app = FastAPI(title="Headless Video Player", lifespan=_lifespan)
"""Registrazione dinamica extra endpoint UDP (status/rebind) dopo creazione app."""
try:
    app.add_api_route("/udp/status", udp_status, methods=["GET"])
    app.add_api_route("/udp/rebind", udp_rebind, methods=["POST"])
    # /time endpoint: current server time in ns (see docs/time_sync.md)
    app.add_api_route("/time", api_time_now, methods=["GET"])
except Exception:
    pass

main_loop = GLib.MainLoop()
player = {"pipeline": None, "vb": None, "alpha": None, "loop": True, "state": "stopped"}
# Simple explicit FSM view for client UIs: idle | preparing | playing | stopping
fsm = {"state": "idle", "previous": None, "last_change": time.time(), "reason": None, "action": None, "error": None, "end_monitor_id": None}

def _fsm_set(state: str, *, reason: str | None = None, action: str | None = None, error: str | None = None) -> None:
    try:
        prev = fsm.get("state")
        fsm.update({
            "previous": prev,
            "state": state,
            "last_change": time.time(),
            "reason": reason,
            "action": action,
            "error": error,
        })
    except Exception:
        pass

# Helper wrappers per transizioni uniformi della FSM
def fsm_preparing(reason: str, action: str | None = None) -> None:
    _fsm_set("preparing", reason=reason, action=action)

def fsm_playing(reason: str | None = None, action: str | None = None) -> None:
    _fsm_set("playing", reason=reason, action=action)

def fsm_stopping(reason: str | None = None) -> None:
    _fsm_set("stopping", reason=reason)

def fsm_idle(reason: str | None = None, error: str | None = None) -> None:
    _fsm_set("idle", reason=reason, error=error)

# Helper wrappers per transizioni uniformi della FSM
def fsm_preparing(reason: str, action: str | None = None) -> None:
    _fsm_set("preparing", reason=reason, action=action)

def fsm_playing(reason: str | None = None, action: str | None = None) -> None:
    _fsm_set("playing", reason=reason, action=action)

def fsm_stopping(reason: str | None = None) -> None:
    _fsm_set("stopping", reason=reason)

def fsm_idle(reason: str | None = None, error: str | None = None) -> None:
    _fsm_set("idle", reason=reason, error=error)
playlist = {"items": [], "index": -1, "loop": True}
splash = {"pipeline": None, "src": None, "vb": None, "active": False}  # Tracking splash (se attivo copre lo schermo)
preloaded = {"path": None, "pipeline": None, "vb": None, "alpha": None}  # Pipeline pre-caricata per prossimo elemento playlist
fade_seq = 0  # Sequenziatore per cancellare fade sovrapposti

# Overlay controller (pipeline separata su plane dedicato)
overlay = {"pipeline": None, "alpha": None, "active": False, "current_alpha": 0.0}
# Sequenziatore per annullare fade overlay sovrapposti
overlay_fade_seq = 0
# Ultimo risultato del probe overlay (per /overlay/status)
overlay_probe_info = {"detected": False, "method": None, "plane_id": None, "zpos_supported": None, "error": None}

# Fast-start state
faststart = {"prepared_path": None}

# HUD visibility state (OFF-player HUD texts)
HUD_MODE = 0

# Show readiness state (usato dalla GUI per LED verde dopo push playlist)
show_state = {"ready": False, "for": None, "ts": None}

# Test mode state
test_mode = {"pipeline": None, "src": None, "active": False, "ticker": None, "x": 0, "y": 0}

# Schedules (per exporre in /status e cancellare al completamento)
schedule_info = {
    "play_at": None,          # epoch seconds
    "overlay_fade_at": None,  # epoch seconds
}

# Token globale per invalidare tutte le azioni pianificate: incrementato su hard stop
SCHEDULE_EPOCH = 0

def cancel_all_schedules():
    """Invalida tutte le azioni pianificate e ferma timer/monitor noti.

    - incrementa SCHEDULE_EPOCH per invalidare i callback pianificati via schedule_action
    - azzera schedule_info
    - cancella timer di autoplay e il monitor
    - ferma il ticker di test mode
    """
    try:
        globals()["SCHEDULE_EPOCH"] = int(globals().get("SCHEDULE_EPOCH", 0)) + 1
    except Exception:
        globals()["SCHEDULE_EPOCH"] = 1
    try:
        schedule_info["play_at"] = None
        schedule_info["overlay_fade_at"] = None
    except Exception:
        pass

# ---------- OFF-player process management ----------
ASYNC_LOOP: Optional[asyncio.AbstractEventLoop] = None
_exit_requested = threading.Event()
off_proc = {"p": None, "port": None, "path": None, "reader": None, "requested_stop_ts": None}


def _collect_off_pids_windows() -> list[int]:
    if not _is_windows:
        return []
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return []
    pids: list[int] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = next(csv.reader([line]))
        except Exception:
            continue
        if not row:
            continue
        name = row[0].strip().lower()
        if name not in {"off-player.exe", "off-player_debug.exe"}:
            continue
        try:
            pids.append(int(row[1]))
        except Exception:
            continue
    return pids


def _off_process_watchdog_loop() -> None:
    while True:
        try:
            if not globals().get("OFF_WATCHDOG_ENABLED", False) or not _is_windows:
                time.sleep(OFF_WATCHDOG_INTERVAL)
                continue
            tracked = None
            proc = off_proc.get("p")
            if proc and getattr(proc, "poll", lambda: 1)() is None:
                tracked = proc.pid
            pids = _collect_off_pids_windows()
            if not pids:
                time.sleep(OFF_WATCHDOG_INTERVAL)
                continue
            killable = [pid for pid in pids if pid != tracked]
            if tracked is None:
                killable = pids
            for pid in killable:
                try:
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
                    print(f"[OFF-WATCHDOG] Terminato OFF-player orfano PID={pid}", flush=True)
                except Exception as exc:
                    print(f"[OFF-WATCHDOG] Impossibile terminare PID {pid}: {exc}", flush=True)
        except Exception as exc:
            print(f"[OFF-WATCHDOG] Errore watchdog: {exc}", flush=True)
        time.sleep(OFF_WATCHDOG_INTERVAL)


if OFF_WATCHDOG_ENABLED and _is_windows:
    threading.Thread(target=_off_process_watchdog_loop, name="off-watchdog", daemon=True).start()
off_logs: deque[str] = deque(maxlen=500)

def _off_recent_logs(limit: int = 5) -> str:
    try:
        entries = list(off_logs)[-limit:]
    except Exception:
        entries = []
    cleaned = [str(entry) for entry in entries if entry]
    if not cleaned:
        return ""
    return " | ".join(cleaned)

def _log_missing_dependency_hint(code: int, path: str | None) -> None:
    if not _is_windows or code != _WINDOWS_MISSING_DEP_CODE:
        return
    try:
        hint = _off_dependency_hint()
        off_logger.error(
            "OFF-player manca di dipendenze Windows (code=%s path=%s). %s PATH=%s",
            code,
            path,
            hint,
            os.environ.get("PATH"),
        )
    except Exception:
        try:
            off_logger.error("OFF-player manca di dipendenze Windows (code=%s path=%s)", code, path)
        except Exception:
            pass


def request_headless_exit(reason: str, exit_code: int | None = None) -> None:
    if _exit_requested.is_set():
        return
    _exit_requested.set()
    print(f"[OFF-WATCH] headless-player terminating: {reason}", flush=True)
    try:
        GLib.idle_add(main_loop.quit)
    except Exception:
        pass
    loop = globals().get("ASYNC_LOOP")
    if loop is not None:
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass

    def _final_kill():
        time.sleep(1.0)
        try:
            code = 0 if exit_code is None else int(exit_code)
        except Exception:
            code = 0
        if code < 0:
            code = 0
        os._exit(code)

    # Invece di os._exit immediato, chiedi a uvicorn di spegnersi pulito se il server è avviato
    def _graceful():
        # Attendi che il main loop termini (flag rudimentale) o timeout 5s
        for _ in range(50):
            # Se main_loop non gira più (nessuna API diretta: usiamo _exit_requested come proxy dopo idle_add quit)
            if _exit_requested.is_set():
                break
            time.sleep(0.1)
        srv = globals().get("UVICORN_SERVER")
        if srv:
            print("[SHUTDOWN] Segnalo should_exit a uvicorn", flush=True)
            try:
                srv.should_exit = True
            except Exception:
                pass
        else:
            os._exit(0 if exit_code is None else int(exit_code or 0))
    threading.Thread(target=_graceful, name="headless-exit", daemon=True).start()


def _handle_off_process_exit(proc_obj: subprocess.Popen | None) -> None:
    if proc_obj is None:
        return
    exit_code: int | None = None
    try:
        exit_code = proc_obj.poll()
    except Exception:
        pass
    try:
        if off_proc.get("reader") is threading.current_thread():
            off_proc["reader"] = None
    except Exception:
        pass
    is_current = off_proc.get("p") is proc_obj
    suppress = False
    ts = off_proc.get("requested_stop_ts")
    if ts is not None:
        try:
            suppress = (time.time() - float(ts)) <= 10.0
        except Exception:
            suppress = True
    if suppress:
        off_proc["requested_stop_ts"] = None
    if is_current:
        off_proc["p"] = None
    if suppress:
        return  # uscita richiesta esplicitamente (stop richiesto via API)
    if not is_current:
        # Processo figlio non è quello attuale (race?)
        print(f"[OFF-WATCH] Processo OFF uscito ma non è quello corrente (code={exit_code})", flush=True)
        return
    off_proc["requested_stop_ts"] = None
    # Nuovo comportamento: auto-restart OFF salvo troppi fallimenti
    reason = f"OFF-player process exited (code={exit_code})"
    ok_user = (exit_code == 0)
    if ok_user:
        off_logger.info("OFF-player exited cleanly (code=0)")
        print(f"[OFF-WATCH] OFF-player chiuso dall'utente (code=0): esco anche da headless-player.", flush=True)
        # Se l'utente ha chiuso la finestra (tipicamente con 'q'), termina anche headless-player
        try:
            request_headless_exit("OFF-player closed by user", exit_code=0)
        except Exception:
            # Fallback hard-exit se la richiesta non passa
            try:
                os._exit(0)
            except Exception:
                pass
        return
    # Fallimento inatteso: applica backoff di restart
    retries = off_proc.setdefault("retries", 0)
    first_fail = off_proc.setdefault("first_fail_ts", time.time())
    off_proc["retries"] = retries + 1
    elapsed = time.time() - first_fail
    MAX_RETRIES = 3
    WINDOW = 60.0  # secondi
    _log_missing_dependency_hint(exit_code or 0, off_proc.get("path"))
    recent = _off_recent_logs()
    off_logger.error(
        "OFF-player terminated unexpectedly (code=%s) retry=%s elapsed=%.1fs. Recent output: %s",
        exit_code,
        off_proc["retries"],
        elapsed,
        recent or "<none>",
    )
    print(f"[OFF-WATCH] OFF-player terminato inatteso (code={exit_code}) retry={off_proc['retries']} elapsed={elapsed:.1f}s", flush=True)
    if off_proc["retries"] > MAX_RETRIES and elapsed < WINDOW:
        off_logger.warning("Not restarting OFF-player after %s failures in %.1fs", off_proc["retries"], elapsed)
        print(f"[OFF-WATCH] Troppi fallimenti in {WINDOW}s (>{MAX_RETRIES}). Non riavvio. Headless resta vivo.", flush=True)
        return
    if elapsed >= WINDOW:
        # reset finestra
        off_proc["retries"] = 1
        off_proc["first_fail_ts"] = time.time()
    # Pianifica restart dopo piccolo delay
    delay = min(5.0 * off_proc["retries"], 20.0)
    print(f"[OFF-WATCH] Pianifico riavvio OFF fra {delay:.1f}s", flush=True)
    def _restart():
        try:
            _off_start()
        except Exception as e:
            print(f"[OFF-WATCH] Restart OFF fallito: {e}", flush=True)
    threading.Timer(delay, _restart).start()

# --- EGL/KMS preflight checks ---
def _has_drm_devices() -> bool:
    try:
        d = Path("/dev/dri")
        if not d.exists():
            return False
        # render node or primary node are both acceptable
        return any((d / name).exists() for name in ("renderD128", "card0", "card1"))
    except Exception:
        return False

def _modules_loaded(names: tuple[str, ...] = ("vc4", "v3d")) -> bool:
    try:
        with open("/proc/modules", "r") as f:
            txt = f.read()
        txt = txt or ""
        return any((n in txt) for n in names)
    except Exception:
        return False

def _in_groups(group_names: tuple[str, ...] = ("video", "render")) -> bool:
    try:
        import grp
        gids = set(os.getgroups())
        for name in group_names:
            try:
                gid = grp.getgrnam(name).gr_gid
                if gid in gids:
                    return True
            except KeyError:
                continue
        return False
    except Exception:
        # Non bloccare se non riusciamo a risolvere i gruppi
        return True

def _egl_preconditions_ok() -> tuple[bool, list[str]]:
    missing: list[str] = []
    if not _has_drm_devices():
        missing.append("/dev/dri (card0/renderD128) assente")
    if not _modules_loaded():
        missing.append("moduli vc4/v3d non caricati (KMS)")
    if not _in_groups():
        missing.append("utente non nel gruppo 'video'/'render'")
    return (len(missing) == 0, missing)

def _port_in_use(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False

def _find_free_port(start: int = 8082, limit: int = 8200) -> int:
    p = int(start)
    while p <= limit:
        if not _port_in_use(p) and p != int(APP_PORT):
            return p
        p += 1
    return int(start)

def _wait_off_http_ready(port: int | None = None, timeout: float = 5.0, interval: float = 0.2) -> bool:
    """Attende che l'HTTP server di OFF-player risponda, entro timeout secondi."""
    target_port = int(port or globals().get("OFF_PORT", 8082))
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        try:
            with socket.create_connection((OFF_HOST, target_port), timeout=0.5):
                return True
        except OSError:
            time.sleep(max(0.05, interval))
        except Exception:
            time.sleep(max(0.05, interval))
    return False

def _default_off_binary() -> str | None:
    """Cerca l'eseguibile di OFF-player in posizioni comuni.
    Ordine di ricerca:
     1) Variabile d'ambiente OFF_PLAYER_EXE (se esiste ed è valida)
     2) Layout sviluppo: <repo>/OFF-player/bin/OFF-player[.exe]
     3) Layout installer attuale: {app}/OFF-player[.exe]
     4) Layout alternativo: {app}/OFF-player/bin/(...)
     5) macOS .app bundle nelle stesse posizioni
    """
    # 0) Override via env
    try:
        env_exe = os.environ.get("OFF_PLAYER_EXE")
        if env_exe and Path(env_exe).exists():
            print(f"[OFF-BINARY] Usando OFF_PLAYER_EXE da env: {env_exe}", flush=True)
            return str(Path(env_exe))
    except Exception:
        pass

    # Identifica piattaforma
    try:
        sysname = platform.system().lower()
    except Exception:
        sysname = ""

    # Base root: se frozen usa la cartella dell'eseguibile, altrimenti usa la parent di APP_DIR
    try:
        base_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else APP_DIR.parent
    except Exception:
        base_root = APP_DIR.parent

    # Candidati di directory da esplorare
    candidates_dirs: list[Path] = []
    # installer attuale: eseguibile direttamente nella root {app}
    candidates_dirs.append(base_root)
    # layout pacchetto: {app}/bin (OFF-player dentro bin/)
    try:
        candidates_dirs.append(base_root / "bin")
    except Exception:
        pass
    # alternativa: {app}\OFF-player\bin
    candidates_dirs.append(base_root / "OFF-player" / "bin")
    # repo-like: risali uno o più livelli e cerca OFF-player/bin
    try:
        candidates_dirs.append(base_root.parent / "OFF-player" / "bin")
    except Exception:
        pass
    try:
        candidates_dirs.append(base_root.parent.parent / "OFF-player" / "bin")
    except Exception:
        pass
    try:
        # tipicamente: <repo>/OFF-player/bin quando eseguibile è in headless-player/dist/<bundle>
        candidates_dirs.append(base_root.parents[2] / "OFF-player" / "bin")
    except Exception:
        pass

    # Nomi possibili per file eseguibili
    win_names = ("OFF-player.exe",)
    nix_names = ("OFF-player",)

    # 1) Windows: cerca .exe
    if sysname.startswith("win") or "windows" in sysname:
        for d in candidates_dirs:
            for name in win_names:
                p = d / name
                if p.exists():
                    print(f"[OFF-BINARY] Trovato eseguibile Windows: {p}", flush=True)
                    return str(p)

    # 2) macOS: cerca nei bundle .app e poi binari diretti
    if sysname.startswith("darwin") or sysname.startswith("mac"):
        app_paths = (
            "OFF-player.app/Contents/MacOS/OFF-player",
            "OFF-player_debug.app/Contents/MacOS/OFF-player_debug",
        )
        for d in candidates_dirs:
            for rel in app_paths:
                p = d / rel
                if p.exists():
                    print(f"[OFF-BINARY] Trovato eseguibile macOS: {p}", flush=True)
                    return str(p)
        # in mancanza del bundle, prova i binari lisci
        for d in candidates_dirs:
            for name in nix_names:
                p = d / name
                if p.exists():
                    print(f"[OFF-BINARY] Trovato eseguibile: {p}", flush=True)
                    return str(p)

    # 3) Linux/Unix generico
    for d in candidates_dirs:
        for name in nix_names:
            p = d / name
            if p.exists():
                print(f"[OFF-BINARY] Trovato eseguibile: {p}", flush=True)
                return str(p)

    # Nessun match
    print("[OFF-BINARY] Nessun eseguibile trovato nelle posizioni note:", flush=True)
    try:
        for d in candidates_dirs:
            print(f"  - {d}", flush=True)
    except Exception:
        pass
    try:
        dirs = ", ".join(str(d) for d in candidates_dirs if d)
        off_logger.warning("OFF-player binary not found; checked directories: %s", dirs or "<none>")
    except Exception:
        off_logger.warning("OFF-player binary not found; directory list unavailable")
    return None

def _ensure_off_config(bin_dir: Path, port: int) -> None:
    """Crea/aggiorna il config.json dell'eseguibile OFF-player."""

    def _update(path: Path) -> None:
        try:
            data = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                except Exception:
                    data = {}
            path.parent.mkdir(parents=True, exist_ok=True)
            # Allinea la mediaDir all'attuale MEDIA_DIR assoluta, così OFF-player usa la stessa cartella utente
            try:
                data["mediaDir"] = str(MEDIA_DIR)
            except Exception:
                data.setdefault("mediaDir", "media")
            udp_default = int(data.get("udpPort", 7777))
            if udp_default == int(UDP_PORT):
                udp_default = 7778
            data["udpPort"] = int(udp_default)
            data["httpPort"] = int(port)
            data.setdefault("startPaused", True)
            data.setdefault("loopEach", True)
            data.setdefault("targetFps", 25)
            data.setdefault("autoReloadOnChange", True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as exc:
            raise RuntimeError(str(exc))

    try:
        data_cfg = bin_dir / "data" / "config.json"
        root_cfg = bin_dir / "config.json"
        # Aggiorna i config dentro al bundle (.app/Contents/MacOS/...)
        _update(data_cfg)
        try:
            _update(root_cfg)
        except Exception:
            pass
        # Aggiorna anche il config esterno in OFF-player/bin/data/config.json per consistenza
        try:
            outer_bin = bin_dir.parent.parent.parent  # .../OFF-player/bin
            outer_data_cfg = outer_bin / "data" / "config.json"
            _update(outer_data_cfg)
        except Exception:
            pass
    except Exception as e:
        print(f"[OFF-LAUNCH] Errore scrittura config OFF: {e}", flush=True)

def _off_start(path: str | None = None, port: int | None = None) -> dict:
    if off_proc.get("p") and off_proc["p"].poll() is None:
        return {"ok": True, "running": True, "pid": off_proc["p"].pid, "port": off_proc.get("port"), "path": off_proc.get("path")}
    exe = path or _default_off_binary()
    if not exe or not Path(exe).exists():
        off_logger.error("OFF-player binary not available (resolved path=%s)", exe)
        return {"ok": False, "error": "OFF-player non trovato. Compila ed esegui OFF-player prima, o specifica 'path'"}
    # Scegli porta libera evitando conflitto con APP_PORT
    p = int(port) if port else _find_free_port(start=int(globals().get("OFF_PORT", 8082)))
    # Aggiorna config.json nella cartella bin
    bin_dir = Path(exe).parent
    _ensure_off_config(bin_dir, p)
    try:
        env = os.environ.copy()
        # Determina il comando di lancio (fallback headless con xvfb se DISPLAY assente)
        cmd = [exe]
        want_egl = False
        try:
            if platform.system().lower().startswith("linux") and not os.environ.get("DISPLAY"):
                # Se richiesto EGL (OF_USE_EGLWINDOW/ OFF_USE_EGL), NON usare xvfb-run
                want_egl = os.environ.get("OF_USE_EGLWINDOW") in {"1","true","True"} or os.environ.get("OFF_USE_EGL") in {"1","true","True"}
                if not want_egl and shutil.which("xvfb-run"):
                    print("[OFF-BINARY] DISPLAY mancante: uso xvfb-run per avvio headless", flush=True)
                    cmd = [
                        "xvfb-run",
                        "-a",
                        "-s",
                        "-screen 0 1280x720x24",
                        exe,
                    ]
                elif want_egl:
                    ok, miss = _egl_preconditions_ok()
                    if not ok:
                        print(f"[OFF-BINARY] EGL richiesto ma precondizioni mancanti: {', '.join(miss)}", flush=True)
                        fallback_ok = os.environ.get("OFF_EGL_FALLBACK", "1") in {"1", "true", "True"}
                        if fallback_ok and shutil.which("xvfb-run"):
                            print("[OFF-BINARY] Fallback automatico a xvfb-run", flush=True)
                            cmd = [
                                "xvfb-run",
                                "-a",
                                "-s",
                                "-screen 0 1280x720x24",
                                exe,
                            ]
        except Exception:
            pass
        # Avvia OFF-player catturando stdout per inoltro log alla GUI
        proc = subprocess.Popen(cmd, cwd=str(bin_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
        off_proc.update({"p": proc, "port": p, "path": exe, "reader": None, "requested_stop_ts": None})
        # Thread che inoltra le righe di log verso la GUI (come per CVLC)
        def _reader():
            try:
                import io
                f = io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="ignore") if proc.stdout else None
                while proc.poll() is None and f:
                    line = f.readline()
                    if not line:
                        break
                    l = line.strip()
                    if not l:
                        continue
                    try:
                        # Accoda in buffer locale e inoltra alla GUI
                        try:
                            off_logs.append(l)
                        except Exception:
                            pass
                        gui_log("off", level="INFO", kind="framework", data={"line": l})
                    except Exception:
                        pass
            except Exception as e:
                try:
                    gui_log("off", level="ERROR", kind="framework", data={"error": str(e)})
                except Exception:
                    pass
            finally:
                _handle_off_process_exit(proc)
        t = threading.Thread(target=_reader, name="off-log-reader", daemon=True)
        t.start()
        off_proc["reader"] = t

        def _log_startup_failure() -> None:
            try:
                code = proc.poll()
            except Exception:
                return
            if code is None or code == 0:
                return
            _log_missing_dependency_hint(code, exe)
            recent = _off_recent_logs()
            off_logger.error(
                "OFF-player exited immediately after launch (code=%s path=%s). Recent output: %s",
                code,
                exe,
                recent or "<none>",
            )

        threading.Timer(0.75, _log_startup_failure).start()

        # Se è stato richiesto EGL, verifica crash rapido e applica fallback automatico
        try:
            if want_egl and os.environ.get("OFF_EGL_FALLBACK", "1") in {"1", "true", "True"}:
                time.sleep(1.2)
                code = proc.poll()
                if code is not None and code != 0:
                    print(f"[OFF-BINARY] EGL ha terminato prematuramente (exit={code}). Riavvio con xvfb-run.", flush=True)
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    # Riavvia con xvfb-run se disponibile
                    if shutil.which("xvfb-run"):
                        cmd_fb = [
                            "xvfb-run",
                            "-a",
                            "-s",
                            "-screen 0 1280x720x24",
                            exe,
                        ]
                        proc2 = subprocess.Popen(cmd_fb, cwd=str(bin_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
                        off_proc.update({"p": proc2, "requested_stop_ts": None})
                        # Ricollega il lettore log al nuovo processo
                        def _reader2():
                            try:
                                import io
                                f2 = io.TextIOWrapper(proc2.stdout, encoding="utf-8", errors="ignore") if proc2.stdout else None
                                while proc2.poll() is None and f2:
                                    line = f2.readline()
                                    if not line:
                                        break
                                    l = line.strip()
                                    if not l:
                                        continue
                                    try:
                                        off_logs.append(l)
                                        gui_log("off", level="INFO", kind="framework", data={"line": l})
                                    except Exception:
                                        pass
                            except Exception as e:
                                try:
                                    gui_log("off", level="ERROR", kind="framework", data={"error": str(e)})
                                except Exception:
                                    pass
                            finally:
                                _handle_off_process_exit(proc2)
                        t2 = threading.Thread(target=_reader2, name="off-log-reader", daemon=True)
                        t2.start()
                        off_proc["reader"] = t2
        except Exception:
            pass
        # Aggiorna OFF_PORT globale e persisti
        try:
            globals()["OFF_PORT"] = int(p)
            persist_settings()
        except Exception:
            pass
        return {"ok": True, "running": True, "pid": proc.pid, "port": p, "path": exe}
    except Exception as e:
        off_logger.exception("OFF-player launch failed (path=%s port=%s)", exe, p)
        return {"ok": False, "error": f"Launch failed: {e}", "path": exe, "port": p}

def _off_stop() -> dict:
    proc = off_proc.get("p")
    if not proc or proc.poll() is not None:
        off_proc.update({"p": None})
        off_proc["requested_stop_ts"] = None
        return {"ok": True, "running": False}
    off_proc["requested_stop_ts"] = time.time()
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=1.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    off_proc.update({"p": None})
    # Non serve join del reader (daemon), ma puliamo il riferimento
    try:
        off_proc["reader"] = None
    except Exception:
        pass
    return {"ok": True, "running": False}
    # Autoplay timers
    try:
        _autoplay_cancel_timer()
        _autoplay_cancel_monitor()
    except Exception:
        pass
    # Test mode ticker
    try:
        if test_mode.get("ticker"):
            try:
                GLib.source_remove(test_mode["ticker"])
            except Exception:
                pass
            test_mode["ticker"] = None
    except Exception:
        pass

AUTOPLAY_SPLASH_DELAY_S = 5.0
AUTOPLAY_FADE_SECONDS = 1.0
AUTOPLAY_END_LEAD_NS = int(1 * Gst.SECOND)
autoplay = {
    "enabled": False,
    "timer_id": None,
    "monitor_id": None,
    "fade_started": False,
    "forced_loop_prev": None,
}

# Carica configurazione persistita e backend una volta che gli stati globali sono pronti
load_persisted_framework()
ensure_backend()

current_update = {
    "available": None,
    "status": "idle",
    "log": [],
    "callback_url": None,
    "progress": 0,
    "error": None,
    "bytes_total": 0,
    "bytes_done": 0,
    "stage": "idle",  # idle|planned|downloading|verifying|staging|applying|restarting|ok|error
    "plan": [],        # elenco asset previsti
    "assets": [],      # dettagli asset scaricati/staging
    # metriche di alto livello
    "started_at": None,
    "download_started_at": None,
    "download_ended_at": None,
    "applying_started_at": None,
    "completed_at": None,
}

# ---------- Idle black helper (sempre nero quando non c'è splash e non suona nulla) ----------
def _ensure_black_image() -> Path:
    """Assicura la presenza di un'immagine nera 1280x720 in una cartella interna e ritorna il path."""
    target = IDLE_BLACK_IMAGE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return target
        # Genera PNG nero
        img = Image.new("RGB", (TARGET_WIDTH or 1280, TARGET_HEIGHT or 720), (0, 0, 0))
        img.save(target)
        return target
    except Exception:
        return target

def show_idle_black() -> bool:
    """Mostra un nero stabile quando il player è idle.
    Priorità:
      1) Overlay KMS, se abilitato (copre sempre lo schermo indipendentemente dal backend)
      2) Backend CVLC: prepara PNG nero e metti in pausa sul primo frame
      3) Backend GST: avvia una pipeline su PNG nero (imagefreeze) in PLAYING
    Ritorna True se è riuscito a mostrare il nero, False altrimenti.
    """
    try:
        # Non interferire se splash è attivo: lo splash copre già lo schermo
        if splash.get("active"):
            return True
        # 1) Overlay se disponibile
        if OVERLAY_ENABLED and OVERLAY_USE_KMS:
            try:
                overlay_show(alpha=1.0)
                return True
            except Exception as oe:
                print(f"[IDLE] Overlay nero non disponibile: {oe}", flush=True)
        # Assicura un'immagine nera disponibile
        black = _ensure_black_image()
        ensure_backend()
        be = current_framework.get("backend")
        name = current_framework.get("name")
        if name == "cvlc" and be:
            try:
                be.faststart_prepare(str(black))
                player["state"] = "paused"
                return True
            except Exception as exc:
                print(f"[IDLE] cvlc prepare black fallita: {exc}", flush=True)
        # 3) GST fallback: costruisci pipeline di immagine nera
        if name == "gst":
            try:
                # Usa la normale pipeline per immagini (imagefreeze)
                p, vb, af = build_player_pipeline(str(black))
                # Se c'è una pipeline attiva, fermala
                if player.get("pipeline"):
                    try:
                        player["pipeline"].set_state(Gst.State.NULL)
                    except Exception:
                        pass
                player["pipeline"] = p
                player["vb"] = vb
                player["alpha"] = af
                # Mostra immediatamente il frame nero
                p.set_state(Gst.State.PLAYING)
                player["state"] = "paused"
                return True
            except Exception as ge:
                print(f"[IDLE] GST nero fallback fallito: {ge}", flush=True)
        return False
    except Exception as e:
        print(f"[IDLE] Errore show_idle_black: {e}", flush=True)
        return False


def _apply_idle_black_cover() -> bool:
    """Prova a mantenere lo schermo nero senza perdere il media precaricato.

    Preferisce l'overlay; in assenza prova a ridurre temporaneamente la luminosità
    del backend (se supportato). Restituisce True se è stata applicata una copertura."""
    try:
        if OVERLAY_ENABLED:
            overlay_show(alpha=1.0)
            return True
    except Exception as exc:
        print(f"[AUTOPLAY] Overlay nero non disponibile ({exc})", flush=True)

    try:
        if current_framework.get("name") != "gst":
            ensure_backend()
            backend = current_framework.get("backend")
            if backend and hasattr(backend, "visual_fade_to"):
                prev = globals().get("_LAST_VISUAL_BRIGHTNESS")
                try:
                    prev_value = float(prev) if prev is not None else 1.0
                except Exception:
                    prev_value = 1.0
                backend.visual_fade_to(0.0, 0.0)
                try:
                    globals().update({
                        "_AUTO_IDLE_BLACK_RESTORE": prev_value,
                        "_LAST_VISUAL_BRIGHTNESS": 0.0,
                    })
                except Exception:
                    pass
                return True
    except Exception as exc:
        print(f"[AUTOPLAY] Visual fade per nero non disponibile ({exc})", flush=True)

    return False


def _restore_idle_black_cover(backend: Any | None = None) -> None:
    """Ripristina luminosità/overlay se era stata applicata una copertura nera."""
    try:
        pending = globals().get("_AUTO_IDLE_BLACK_RESTORE")
    except Exception:
        pending = None
    if pending is None:
        return
    try:
        target = float(pending)
    except Exception:
        target = 1.0

    if backend is None:
        try:
            ensure_backend()
            backend = current_framework.get("backend")
        except Exception:
            backend = None

    if not backend or not hasattr(backend, "visual_fade_to"):
        return

    try:
        backend.visual_fade_to(target, 0.0)
        globals()["_AUTO_IDLE_BLACK_RESTORE"] = None
        globals()["_LAST_VISUAL_BRIGHTNESS"] = target
    except Exception as exc:
        print(f"[AUTOPLAY] Ripristino brightness dopo cover fallito: {exc}", flush=True)

def _dim_backend_before_play(backend: Any | None = None) -> None:
    """Abbassa temporaneamente la luminosità del backend (se supportato) prima di far partire il primo frame."""
    if backend is None:
        try:
            ensure_backend()
            backend = current_framework.get("backend")
        except Exception:
            backend = None
    if not backend or not hasattr(backend, "visual_fade_to"):
        return
    try:
        prev = float(globals().get("_LAST_VISUAL_BRIGHTNESS", 1.0))
    except Exception:
        prev = 1.0
    try:
        globals().update({
            "_AUTO_IDLE_BLACK_RESTORE": prev,
            "_LAST_VISUAL_BRIGHTNESS": 0.0,
        })
    except Exception:
        pass
    try:
        backend.visual_fade_to(0.0, 0.0)
    except Exception:
        pass

# Avvia in background un tentativo iniziale di mostrare il nero se lo splash non è attivo
def _idle_bootstrap():
    try:
        # breve attesa per permettere al servizio di inizializzarsi
        time.sleep(1.0)
        show_idle_black()
    except Exception:
        pass

threading.Thread(target=_idle_bootstrap, name="idle-black", daemon=True).start()

# ---------- Autoplay helpers ----------
def _autoplay_log(msg: str) -> None:
    print(f"[AUTOPLAY] {msg}", flush=True)


def _autoplay_cancel_timer() -> None:
    timer_id = autoplay.get("timer_id")
    if timer_id:
        try:
            GLib.source_remove(timer_id)
        except Exception:
            pass
        autoplay["timer_id"] = None


def _autoplay_cancel_monitor() -> None:
    monitor_id = autoplay.get("monitor_id")
    if monitor_id:
        try:
            GLib.source_remove(monitor_id)
        except Exception:
            pass
        autoplay["monitor_id"] = None


def _autoplay_collect_media() -> list[str]:
    items: list[str] = []
    for entry in sorted(MEDIA_DIR.iterdir()):
        if not entry.is_file():
            continue
        try:
            if validate_media_file(entry):
                items.append(str(entry))
        except Exception as exc:
            _autoplay_log(f"Errore analizzando {entry.name}: {exc}")
    return items


def _autoplay_schedule_initial(delay: float | None = None) -> None:
    if not autoplay.get("enabled"):
        return
    delay = AUTOPLAY_SPLASH_DELAY_S if delay is None else max(0.0, delay)
    _autoplay_cancel_timer()

    def _launcher() -> bool:
        autoplay["timer_id"] = None
        _autoplay_launch()
        return False

    timeout_ms = max(1, int(delay * 1000))
    autoplay["timer_id"] = GLib.timeout_add(timeout_ms, _launcher)


def _autoplay_launch() -> None:
    if not autoplay.get("enabled"):
        return
    if not splash.get("active"):
        try:
            show_idle_black()
        except Exception:
            pass
    items = _autoplay_collect_media()
    if not items:
        _autoplay_log("Nessun file valido in media/: riprovo tra 30s")
        _autoplay_schedule_initial(delay=30.0)
        return
    playlist["items"] = items
    playlist["index"] = 0
    playlist["loop"] = True
    # Autoplay needs per-item EOS to advance, so temporarily disable backend loop while it runs.
    if autoplay.get("forced_loop_prev") is None:
        autoplay["forced_loop_prev"] = player.get("loop", True)
    if player.get("loop"):
        set_loop(False)
    stop_play()
    global VIDEO_PATH
    VIDEO_PATH = items[0]
    _autoplay_log(f"Avvio autoplay ({len(items)} elementi) -> {Path(VIDEO_PATH).name}")
    start_play(AUTOPLAY_FADE_SECONDS)

def _autoplay_prepare_manual_bootstrap() -> None:
    """Prepara playlist e fast-start quando l'autoplay è disattivato."""
    if autoplay.get("enabled"):
        return
    try:
        items = _autoplay_collect_media()
    except Exception as exc:
        print(f"[AUTOPLAY] Errore raccolta media iniziale: {exc}", flush=True)
        return
    if not items:
        print("[AUTOPLAY] Nessun file valido in media/: player resta in attesa", flush=True)
        return
    playlist["items"] = items
    playlist["index"] = 0
    playlist["loop"] = True
    global VIDEO_PATH
    VIDEO_PATH = items[0]
    print(f"[AUTOPLAY] Modalità manuale: playlist pronta ({len(items)} elementi), primo={Path(VIDEO_PATH).name}", flush=True)
    try:
        if player.get("pipeline"):
            stop_play()
    except Exception:
        pass
    try:
        if current_framework.get("name") != "off":
            result = api_faststart_prepare(path=VIDEO_PATH)
        else:
            result = {"ok": True, "prepared": VIDEO_PATH}
    except Exception as exc:
        print(f"[AUTOPLAY] faststart_prepare iniziale fallita: {exc}", flush=True)
        return
    else:
        if isinstance(result, JSONResponse):
            try:
                status = int(result.status_code)
            except Exception:
                status = 500
            if status >= 400:
                body = result.body
                if isinstance(body, bytes):
                    try:
                        body = body.decode("utf-8", "ignore")
                    except Exception:
                        pass
                print(f"[AUTOPLAY] faststart_prepare iniziale errore ({status}): {body}", flush=True)
                return
    player["state"] = "paused"
    faststart["prepared_path"] = VIDEO_PATH
    cover_applied = False
    try:
        cover_applied = _apply_idle_black_cover()
    except Exception:
        cover_applied = False
    if cover_applied:
        try:
            if splash.get("active"):
                hide_splash()
        except Exception:
            pass
    else:
        print("[AUTOPLAY] Cover nero non applicata: lascio lo splash attivo/visibile", flush=True)
    try:
        show_state.update({"ready": True, "for": VIDEO_PATH, "ts": time.time()})
        gui_log("show_ready", level="INFO", kind="playlist", data={"for": VIDEO_PATH})
    except Exception:
        pass
    print("[AUTOPLAY] Modalità manuale: primo elemento precaricato, player in pausa", flush=True)


def _autoplay_start_monitor() -> None:
    if autoplay.get("monitor_id"):
        return

    def _tick() -> bool:
        if not autoplay.get("enabled"):
            autoplay["monitor_id"] = None
            return False
        pipe = player.get("pipeline")
        if not pipe or player.get("state") != "playing":
            autoplay["fade_started"] = False
            return True
        try:
            ok_dur, duration = pipe.query_duration(Gst.Format.TIME)  # type: ignore[attr-defined]
        except Exception:
            ok_dur, duration = False, 0
        try:
            ok_pos, position = pipe.query_position(Gst.Format.TIME)  # type: ignore[attr-defined]
        except Exception:
            ok_pos, position = False, 0
        if ok_dur and ok_pos and duration and duration > 0:
            remaining = duration - position
            if 0 <= remaining <= AUTOPLAY_END_LEAD_NS and not autoplay.get("fade_started"):
                _autoplay_trigger_fade_out()
        return True

    autoplay["monitor_id"] = GLib.timeout_add(250, _tick)


def _autoplay_trigger_fade_out() -> None:
    autoplay["fade_started"] = True
    _autoplay_log("Fade-out finale asset")
    # Preferisci overlay nero (aggiungi nero) per coerenza cross-backend
    try:
        if OVERLAY_ENABLED:
            overlay_fade_to(1.0, AUTOPLAY_FADE_SECONDS)
            return
    except Exception:
        pass
    # Fallback: pipeline alpha/videobalance
    if player.get("alpha"):
        fade_opacity_to(0.0, AUTOPLAY_FADE_SECONDS)
    if player.get("vb"):
        fade_to(-1.0, AUTOPLAY_FADE_SECONDS)


def _autoplay_on_play_started(path: str) -> None:
    if not autoplay.get("enabled"):
        return
    autoplay["fade_started"] = False
    _autoplay_start_monitor()
    try:
        name = Path(path).name
    except Exception:
        name = path
    _autoplay_log(f"Riproduzione {name}")


def _autoplay_set_enabled(enabled: bool, restart: bool = True, delay: float | None = None) -> None:
    enabled = bool(enabled)
    if enabled == autoplay.get("enabled") and not restart:
        persist_settings({"autoplay_enabled": enabled})
        return
    autoplay["enabled"] = enabled
    if not enabled:
        _autoplay_log("Autoplay disattivato")
        _autoplay_cancel_timer()
        _autoplay_cancel_monitor()
        autoplay["fade_started"] = False
        # Restore loop setting if autoplay forced it off previously.
        prev_loop = autoplay.get("forced_loop_prev")
        if prev_loop is not None:
            set_loop(prev_loop)
            autoplay["forced_loop_prev"] = None
        persist_settings({"autoplay_enabled": False})
        return
    _autoplay_log("Autoplay attivato")
    autoplay["forced_loop_prev"] = None
    _autoplay_cancel_timer()
    autoplay["fade_started"] = False
    if restart:
        _autoplay_cancel_monitor()
        if player.get("state") == "playing":
            stop_play()
        if not splash.get("active"):
            try:
                show_idle_black()
            except Exception:
                pass
        _autoplay_schedule_initial(delay)
    else:
        _autoplay_start_monitor()
    persist_settings({"autoplay_enabled": True})

# ---------- Maintenance ----------
maintenance = {
    "status": "idle",  # idle | running | ok | error
    "log": [],
    "error": None,
}

def log_maintenance(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    maintenance["log"].append(line)
    maintenance["log"] = maintenance["log"][-300:]
def log_update(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    current_update["log"].append(line)
    current_update["log"] = current_update["log"][-200:]
    if current_update["callback_url"]:
        try:
            import urllib.request
            req = urllib.request.Request(
                current_update["callback_url"],
                data=json.dumps({"message": line, "status": current_update["status"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

def _updates_base_dir() -> Path:
    try:
        root = Path(tempfile.gettempdir()) / "headless-player" / "updates"
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception:
        return APP_DIR / "updates"

def _prune_old_updates(keep: int = 3):
    base = _updates_base_dir()
    try:
        items = sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
        for p in items[keep:]:
            try:
                shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass

def _prune_old_backups(keep: int = 2):
    """Mantiene solo gli ultimi 'keep' backup (cartelle sotto updates/backups)."""
    try:
        root = _updates_base_dir() / "backups"
        if not root.exists():
            return
        items = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
        for p in items[keep:]:
            try:
                shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass

def _ps_quote(s: str) -> str:
    try:
        return "'" + str(s).replace("'", "''") + "'"
    except Exception:
        return "'" + str(s) + "'"

def _windows_elevated_apply_zip(zip_path: Path, target_dir: Path, restart: bool = True) -> dict:
    """Prepara e lancia uno script PowerShell elevato che:
    - estrae lo ZIP in staging
    - sposta in backup i file destinazione che verrebbero sovrascritti
    - copia i nuovi file con robocopy
    - in caso di errore ripristina dal backup
    - opzionalmente riavvia il processo Python
    """
    try:
        tmp = Path(os.environ.get("TEMP", str(Path.home())))
        tmp.mkdir(parents=True, exist_ok=True)
        script = tmp / "maroccos_elevated_apply.ps1"
        pid = os.getpid()
        restart_exe = sys.executable if restart else ""
        restart_args = f"-m uvicorn app:app --host 0.0.0.0 --port {APP_PORT}" if restart else ""
        workdir = str(APP_DIR)
        # Prepara dir backup dedicata
        try:
            backups_root = _updates_base_dir() / "backups"
            backups_root.mkdir(parents=True, exist_ok=True)
        except Exception:
            backups_root = Path(os.environ.get("TEMP", str(Path.home()))) / "headless-player" / "updates" / "backups"
            backups_root.mkdir(parents=True, exist_ok=True)
        backup_dir = backups_root / time.strftime("%Y%m%d-%H%M%S")

        ps_lines = [
            "param(\n  [string]$ZipPath,\n  [string]$TargetDir,\n  [int]$WaitPid=0,\n  [string]$RestartExe=\"\",\n  [string]$RestartArgs=\"\",\n  [string]$WorkDir=\"\",\n  [string]$BackupDir=\"\"\n)",
            "$ErrorActionPreference='Stop'",
            "if($WaitPid -gt 0){ try{ Wait-Process -Id $WaitPid -ErrorAction SilentlyContinue -Timeout 20 }catch{}; Start-Sleep -Seconds 1 }",
            "$staging=Join-Path ([System.IO.Path]::GetTempPath()) ('maroccos-staging-'+[guid]::NewGuid().ToString())",
            "New-Item -ItemType Directory -Path $staging | Out-Null",
            "Expand-Archive -LiteralPath $ZipPath -DestinationPath $staging -Force",
            "if($BackupDir -and $BackupDir.Length -gt 0){ New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null }",
            "$files = Get-ChildItem -Path $staging -Recurse -File",
            "foreach($f in $files){ $rel = $f.FullName.Substring($staging.Length).TrimStart('\\','/'); $dest = Join-Path $TargetDir $rel; if(Test-Path -LiteralPath $dest){ $b = Join-Path $BackupDir $rel; $bd = Split-Path -Path $b -Parent; New-Item -ItemType Directory -Path $bd -Force | Out-Null; Move-Item -LiteralPath $dest -Destination $b -Force } }",
            "$args=@($staging,$TargetDir,'/E','/NFL','/NDL','/NJH','/NJS','/NP','/R:2','/W:1')",
            "$p=Start-Process -FilePath robocopy -ArgumentList $args -Wait -PassThru",
            "$rc=$p.ExitCode",
            "if($rc -ge 8){ try{ $bfiles = Get-ChildItem -Path $BackupDir -Recurse -File }catch{ $bfiles=@() }; foreach($b in $bfiles){ $rel = $b.FullName.Substring($BackupDir.Length).TrimStart('\\','/'); $dest = Join-Path $TargetDir $rel; $dd = Split-Path -Path $dest -Parent; New-Item -ItemType Directory -Path $dd -Force | Out-Null; Move-Item -LiteralPath $b.FullName -Destination $dest -Force }; try{ Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue }catch{}; exit $rc }",
            "try{ Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue }catch{}",
            "if($RestartExe -and $RestartExe.Length -gt 0){ $psi=New-Object System.Diagnostics.ProcessStartInfo; $psi.FileName=$RestartExe; if($RestartArgs){ $psi.Arguments=$RestartArgs }; if($WorkDir){ $psi.WorkingDirectory=$WorkDir }; $psi.WindowStyle='Hidden'; [System.Diagnostics.Process]::Start($psi) | Out-Null }",
            "exit 0",
        ]
        script.write_text("\n".join(ps_lines), encoding="utf-8")
        cmd = [
            "powershell","-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-Command",
            (
                "Start-Process -Verb RunAs -FilePath 'powershell.exe' -ArgumentList "
                + _ps_quote(f"-NoProfile -ExecutionPolicy Bypass -File {script} -ZipPath {zip_path} -TargetDir {target_dir} -WaitPid {pid} -RestartExe {restart_exe} -RestartArgs {restart_args} -WorkDir {workdir} -BackupDir {backup_dir}")
                + " -WindowStyle Hidden -PassThru | Out-Null"
            )
        ]
        # Prune vecchi backup prima di lanciare lo script, così la cartella resta ordinata
        try:
            _prune_old_backups(keep=2)
        except Exception:
            pass
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(tmp), creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)))
        return {"ok": True, "script": str(script), "backup_dir": str(backup_dir)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _handle_elevated_zip_apply(apath: Path, restart: bool = True) -> dict:
    """Gestisce l'apply elevato di un archivio ZIP su Windows e aggiorna current_update.

    Se l'applier elevato fallisce, ritorna {ok: False, error: ...} per permettere fallback.
    In caso di successo, aggiorna lo stato e termina il processo per consentire la copia.
    """
    log_update(f"Avvio apply elevato ZIP: {apath}")
    # Prima di procedere fermiamo OFF-player se è in esecuzione così da rimuovere il lock sul binario
    try:
        if off_proc.get("p") and off_proc["p"].poll() is None:
            stop_res = _off_stop()
            log_update(f"Richiesto stop OFF-player prima dell'apply elevato: {stop_res}")
            # Attendi massimo 3s rilascio file
            for _ in range(30):
                p = off_proc.get("p")
                if not p or p.poll() is not None:
                    break
                time.sleep(0.1)
            # Se ancora vivo tenta kill forte
            p = off_proc.get("p")
            if p and p.poll() is None:
                try:
                    p.kill()
                    log_update("OFF-player kill forzato (persistente dopo terminate)")
                except Exception:
                    pass
    except Exception as _e:
        log_update(f"Stop OFF-player pre-apply elevato fallito (ignoro): {_e}")
    try:
        applier = globals().get("ELEVATED_ZIP_APPLIER", _windows_elevated_apply_zip)
    except Exception:
        applier = _windows_elevated_apply_zip
    try:
        res = applier(apath, APP_DIR, restart=restart)
    except Exception as ee:
        return {"ok": False, "error": str(ee)}
    if not res.get("ok"):
        return res
    current_update.update({
        "status": "ok",
        "stage": "restarting",
        "completed_at": time.time(),
        "elevated": {
            "script": res.get("script"),
            "mode": "windows_zip_runas",
            "backup_dir": res.get("backup_dir"),
        }
    })
    log_update("Script apply elevato avviato: termino per completare aggiornamento…")
    try:
        _emit_discovery_beacon(reason="post-update-restart")
    except Exception:
        pass
    try:
        time.sleep(0.3)
    except Exception:
        pass
    os._exit(0)
    return {"ok": True}

def get_ip():
    for iface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(iface).get(netifaces.AF_INET, [])
        for a in addrs:
            ip = a.get('addr')
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    return "0.0.0.0"

def get_eth_wifi_ips():
    """Ritorna (eth_ip, wifi_ip) se presenti, altrimenti None."""
    eth_ip = None
    wifi_ip = None
    for iface in netifaces.interfaces():
        iname = (iface or "").lower()
        addrs = netifaces.ifaddresses(iface).get(netifaces.AF_INET, [])
        for a in addrs:
            ip = a.get('addr')
            if not ip or ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if iname.startswith(("eth", "enp", "eno", "ens")):
                if not eth_ip:
                    eth_ip = ip
            elif iname.startswith(("wlan", "wl")):
                if not wifi_ip:
                    wifi_ip = ip
    return eth_ip, wifi_ip

# ---------- Splash via appsrc ----------
def build_splash_pipeline():
    """Crea la pipeline splash con fallback automatico da kmssink ad autovideosink."""
    preferred = "kmssink" if USE_KMS else "autovideosink"
    print(f"[SPLASH] Tentativo creazione pipeline, preferito: {preferred}", flush=True)
    tried = []
    for sink in [preferred, "autovideosink"]:
        if sink in tried:
            continue
        tried.append(sink)
        try:
            desc = (
                f"appsrc name=src is-live=true format=time caps=video/x-raw,format=RGB,width={TARGET_WIDTH},height={TARGET_HEIGHT},framerate=30/1 "
                f"! videoconvert ! videobalance name=svb ! imagefreeze ! {sink}"
            )
            print(f"[SPLASH] Pipeline desc: {desc}", flush=True)
            pipeline = Gst.parse_launch(desc)
            print(f"[SPLASH] Pipeline creata con successo usando {sink}", flush=True)
            return pipeline, pipeline.get_by_name("src"), pipeline.get_by_name("svb")
        except Exception as e:
            print(f"[SPLASH] Errore creazione pipeline con {sink}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            continue
    raise RuntimeError("Impossibile creare pipeline splash")

def push_splash_frame(appsrc):
    print("[SPLASH] Generazione frame splash", flush=True)
    img = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 0))
    if not SPLASH_BLACK:
        eth_ip, wifi_ip = get_eth_wifi_ips()
        name_line = f"Nome: {DEVICE_NAME}" if DEVICE_NAME else None
        text = (
            (f"{name_line}\n" if name_line else "") +
            f"eth -> {eth_ip or '-'}\n"
            f"wifi -> {wifi_ip or '-'}\n"
            f"Versione: {VERSION}"
        )
        print(f"[SPLASH] Testo: {text}", flush=True)
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 32)
        except:
            font = ImageFont.load_default()
        # Centra il testo
        try:
            w, h = d.multiline_textbbox((0,0), text, font=font, align="center")[2:]
        except Exception:
            w, h = d.multiline_textsize(text, font=font)
        d.multiline_text(((TARGET_WIDTH - w) // 2, (TARGET_HEIGHT - h) // 2), text, fill=(255, 255, 255), font=font, align="center")
    buf = Gst.Buffer.new_allocate(None, TARGET_WIDTH * TARGET_HEIGHT * 3, None)
    buf.fill(0, img.tobytes())
    now = int(time.time() * Gst.SECOND)
    buf.pts = buf.dts = now
    result = appsrc.emit("push-buffer", buf)
    print(f"[SPLASH] Frame inviato, risultato: {result}", flush=True)

def push_custom_splash_frame(appsrc, extra_line: str | None = None):
    """Disegna lo stesso splash ma con una riga extra (es. PING!)"""
    img = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 0))
    eth_ip, wifi_ip = get_eth_wifi_ips()
    name_line = f"Nome: {DEVICE_NAME}" if DEVICE_NAME else None
    base = (
        (f"{name_line}\n" if name_line else "") +
        f"eth -> {eth_ip or '-'}\n"
        f"wifi -> {wifi_ip or '-'}\n"
        f"Versione: {VERSION}"
    )
    text = base if not extra_line else (base + f"\n{extra_line}")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 32)
    except:
        font = ImageFont.load_default()
    try:
        w, h = d.multiline_textbbox((0,0), text, font=font, align="center")[2:]
    except Exception:
        w, h = d.multiline_textsize(text, font=font)
    d.multiline_text(((TARGET_WIDTH - w) // 2, (TARGET_HEIGHT - h) // 2), text, fill=(255, 255, 255), font=font, align="center")
    buf = Gst.Buffer.new_allocate(None, TARGET_WIDTH * TARGET_HEIGHT * 3, None)
    buf.fill(0, img.tobytes())
    now = int(time.time() * Gst.SECOND)
    buf.pts = buf.dts = now
    appsrc.emit("push-buffer", buf)

def show_splash_until_play():
    print("[SPLASH] Avvio splash permanente", flush=True)
    # Se stiamo usando OFF-player come backend, delega lo splash a OFF (solo se già avviato e pronto)
    try:
        if current_framework.get("name") == "off" and current_framework.get("backend") is not None:
            # Verifica se OFF-player è in esecuzione prima di delegare
            proc = off_proc.get("p")
            if not proc or proc.poll() is not None:
                print("[SPLASH] OFF-player non ancora avviato, uso splash locale", flush=True)
            else:
                # OFF-player è avviato: prova a delegare con retry (potrebbe non essere ancora pronto)
                be = current_framework.get("backend")
                if not _wait_off_http_ready(timeout=4.0, interval=0.25):
                    print("[SPLASH] OFF-player avviato ma HTTP non pronto, uso splash locale", flush=True)
                    raise RuntimeError("off_not_ready")
                try:
                    name_line = f"Nome: {DEVICE_NAME}" if DEVICE_NAME else None
                    eth_ip, wifi_ip = get_eth_wifi_ips()
                    text = (
                        (f"{name_line}\n" if name_line else "") +
                        f"eth -> {eth_ip or '-'}\n" +
                        f"wifi -> {wifi_ip or '-'}\n" +
                        f"Versione: {VERSION}"
                    )
                except Exception:
                    text = f"Versione: {VERSION}"
                
                # Retry con backoff breve (max 3 tentativi)
                success = False
                for attempt in range(3):
                    try:
                        if hasattr(be, "splash_show"):
                            be.splash_show(text)
                        else:
                            # chiamata diretta a metodi del backend se esposti diversamente
                            getattr(be, "splash_show")(text)  # type: ignore
                        success = True
                        print(f"[SPLASH] Delega a OFF riuscita al tentativo {attempt + 1}", flush=True)
                        # Considera splash come mostrato (idle)
                        try:
                            fsm_idle("splash_delegated_off")
                        except Exception:
                            pass
                        return
                    except Exception as e:
                        if attempt < 2:  # ancora tentativi disponibili
                            print(f"[SPLASH] Tentativo {attempt + 1} fallito, riprovo tra 0.5s...", flush=True)
                            time.sleep(0.5)
                        else:
                            print(f"[SPLASH] Delega a OFF fallita dopo 3 tentativi: {e}, uso splash locale", flush=True)
    except Exception:
        pass
    # Fallback cross‑platform: se GStreamer non è disponibile, non creare pipeline splash.
    if not HAS_GST:
        print("[SPLASH] GStreamer non disponibile: salto splash e imposto idle black/back-end", flush=True)
        try:
            # Prova a mostrare un nero stabile tramite backend/overlay
            show_idle_black()
        except Exception:
            pass
        try:
            fsm_idle("splash_skipped_no_gst")
        except Exception:
            pass
        return
    try:
        pipeline, src, vb = build_splash_pipeline()
        splash["pipeline"] = pipeline
        splash["src"] = src
        splash["vb"] = vb
        splash["active"] = True
        pipeline.set_state(Gst.State.PLAYING)
        GLib.idle_add(push_splash_frame, src)
        print("[SPLASH] Splash avviato (attivo fino al primo play)", flush=True)
        fsm_idle("splash_shown")
    except Exception as exc:
        print(f"[SPLASH] ERRORE avvio splash: {exc}", flush=True)
        # Fallback: prova a mostrare idle black e continua senza splash
        try:
            show_idle_black()
        except Exception:
            pass
        try:
            fsm_idle("splash_unavailable")
        except Exception:
            pass

def hide_splash():
    # Se backend OFF attivo, chiedi di nascondere lo splash anche a OFF-player (solo se in esecuzione)
    try:
        if current_framework.get("name") == "off" and current_framework.get("backend") is not None:
            # Verifica se OFF-player è in esecuzione prima di delegare
            proc = off_proc.get("p")
            if proc and proc.poll() is None:
                be = current_framework.get("backend")
                try:
                    if hasattr(be, "splash_hide"):
                        be.splash_hide()
                    else:
                        getattr(be, "splash_hide")()  # type: ignore
                except Exception:
                    pass
    except Exception:
        pass
    if splash["active"] and splash["pipeline"]:
        print("[SPLASH] Chiudo splash", flush=True)
        splash["pipeline"].set_state(Gst.State.NULL)
        splash["pipeline"] = None
        splash["src"] = None
        splash["vb"] = None
        splash["active"] = False
        print("[SPLASH] Splash chiuso", flush=True)

 

# ---------- Overlay KMS (plane separato) ----------
def build_overlay_pipeline():
    """Crea pipeline overlay full-screen nero con alpha regolabile.
    - Preferisce kmssink; in fallback usa autovideosink
    - Imposta plane-id/zpos solo se la property è supportata dal sink (evita parse_error)
    """
    sink_name = "kmssink" if OVERLAY_USE_KMS else "autovideosink"
    caps = f"video/x-raw,format=ARGB,width={TARGET_WIDTH},height={TARGET_HEIGHT},framerate={TARGET_FPS}"
    # Non passare plane-id/zpos nella stringa; imposteremo le property via set_property se disponibili
    desc = (
        f"videotestsrc pattern=black is-live=true ! {caps} ! alpha name=olpha alpha=1.0 ! {sink_name} name=olsink"
    )
    print(f"[OVERLAY] Pipeline: {desc}", flush=True)
    p = Gst.parse_launch(desc)
    alpha = p.get_by_name("olpha")
    sink = p.get_by_name("olsink")
    # Imposta proprietà se presenti
    try:
        if OVERLAY_USE_KMS and sink is not None:
            if OVERLAY_KMS_PLANE_ID > 0:
                try:
                    sink.set_property("plane-id", int(OVERLAY_KMS_PLANE_ID))
                    print(f"[OVERLAY] plane-id={OVERLAY_KMS_PLANE_ID}", flush=True)
                except Exception as e:
                    print(f"[OVERLAY] plane-id non supportato: {e}", flush=True)
            if OVERLAY_ZPOS >= 0:
                try:
                    sink.set_property("zpos", int(OVERLAY_ZPOS))
                    print(f"[OVERLAY] zpos={OVERLAY_ZPOS}", flush=True)
                except Exception as e:
                    print(f"[OVERLAY] zpos non supportato: {e}", flush=True)
    except Exception:
        pass
    return p, alpha

def overlay_show(alpha: float = 1.0):
    """Mostra overlay nero (alpha 1.0 = pieno nero)."""
    if not OVERLAY_ENABLED:
        raise RuntimeError("Overlay disabilitato")
    target_alpha = max(0.0, min(1.0, float(alpha)))
    if overlay["active"] and overlay["pipeline"]:
        try:
            overlay["pipeline"].set_state(Gst.State.PLAYING)
        except Exception:
            pass
        if overlay["alpha"]:
            try:
                overlay["alpha"].set_property("alpha", target_alpha)
            except Exception:
                pass
        overlay["current_alpha"] = target_alpha
        return
    # Se non abbiamo ancora un plane_id valido su KMS, prova autodetect prima di creare la pipeline
    if OVERLAY_USE_KMS and int(OVERLAY_KMS_PLANE_ID) <= 0:
        try:
            res = overlay_probe(auto_persist=True)
            if res.get("detected"):
                print(f"[OVERLAY] Plane rilevato automaticamente: id={res.get('plane_id')}", flush=True)
        except Exception as probe_exc:
            print(f"[OVERLAY] Probe plane fallita (proseguo comunque): {probe_exc}", flush=True)
    # Crea pipeline overlay; fallback automatico ad autovideosink se kmssink non disponibile
    try:
        p, a = build_overlay_pipeline()
    except Exception as e:
        print(f"[OVERLAY] Creazione con KMS fallita: {e} -> fallback ad autovideosink", flush=True)
        try:
            old_kms = OVERLAY_USE_KMS
        except Exception:
            old_kms = True
        try:
            globals()["OVERLAY_USE_KMS"] = False
            p, a = build_overlay_pipeline()
        finally:
            try:
                globals()["OVERLAY_USE_KMS"] = old_kms
            except Exception:
                pass
    try:
        overlay["pipeline"] = p
        overlay["alpha"] = a
        overlay["active"] = True
        if overlay["alpha"]:
            overlay["alpha"].set_property("alpha", target_alpha)
        p.set_state(Gst.State.PLAYING)
        overlay["current_alpha"] = target_alpha
        print("[OVERLAY] Attivato", flush=True)
    except Exception as e:
        overlay["pipeline"] = None
        overlay["alpha"] = None
        overlay["active"] = False
        print(f"[OVERLAY] ERRORE creazione: {e}", flush=True)
        raise

def overlay_hide():
    if overlay["active"] and overlay["pipeline"]:
        try:
            overlay["pipeline"].set_state(Gst.State.NULL)
        except Exception:
            pass
        overlay["pipeline"] = None
        overlay["alpha"] = None
        overlay["active"] = False
    overlay["current_alpha"] = 0.0
    print("[OVERLAY] Disattivato", flush=True)
    # Invalida eventuali fade pendenti
    try:
        globals()["overlay_fade_seq"] = int(globals().get("overlay_fade_seq", 0)) + 1
    except Exception:
        pass

def overlay_set_alpha(value: float):
    if not overlay["active"] or not overlay["alpha"]:
        return False
    try:
        val = max(0.0, min(1.0, float(value)))
        overlay["alpha"].set_property("alpha", val)
        overlay["current_alpha"] = val
        return True
    except Exception:
        return False

def overlay_fade_to(target: float, duration_s: float = 1.0):
    if not overlay["active"] or not overlay["alpha"]:
        start = overlay.get("current_alpha", target)
        overlay_show(alpha=max(0.0, min(1.0, float(start))))
    if duration_s <= 0:
        overlay_set_alpha(target)
        return True
    try:
        cur = float(overlay["alpha"].get_property("alpha"))
    except Exception:
        cur = float(overlay.get("current_alpha", target))
    steps = max(1, int(duration_s / (FADE_INTERVAL_MS / 1000.0)))
    step_val = (max(0.0, min(1.0, target)) - cur) / steps
    # Token di cancellazione
    try:
        globals()["overlay_fade_seq"] = int(globals().get("overlay_fade_seq", 0)) + 1
    except Exception:
        globals()["overlay_fade_seq"] = 1
    my_seq = int(globals().get("overlay_fade_seq", 0))
    seq = {"i": 0, "val": cur}
    def stepper():
        # Cancella se è stato lanciato un altro fade
        if my_seq != int(globals().get("overlay_fade_seq", 0)):
            return False
        if not overlay["active"] or not overlay["alpha"]:
            return False
        seq["val"] += step_val
        final = (seq["i"] + 1) >= steps
        new_val = max(0.0, min(1.0, target if final else seq["val"]))
        overlay["alpha"].set_property("alpha", new_val)
        overlay["current_alpha"] = new_val
        seq["i"] += 1
        return not final
    GLib.timeout_add(FADE_INTERVAL_MS, stepper)
    return True

# ---------- Overlay auto-detect (KMS plane) ----------
def _probe_overlay_plane_sysfs() -> tuple[int | None, bool | None]:
    """Prova a rilevare un plane overlay da sysfs (/sys/class/drm/...).
    Ritorna (plane_id, zpos_supported).
    """
    try:
        root = Path("/sys/class/drm")
        if not root.exists():
            return None, None
        paths = list(root.glob("card*-plane*"))
        paths += list(root.glob("card*/plane*"))
        candidates: list[tuple[int, bool]] = []
        for p in paths:
            try:
                match = re.search(r"plane(?:-|)(\d+)", p.name)
                if not match:
                    continue
                plane_id = int(match.group(1))
            except Exception:
                continue
            try:
                t = (p / "type").read_text().strip()
            except Exception:
                t = ""
            if t.lower() == "overlay":
                zpos_supported = (p / "zpos").exists() or (p / "zpos_range").exists()
                candidates.append((plane_id, zpos_supported))
        if candidates:
            # Preferisci quello che dichiara zpos
            candidates.sort(key=lambda x: (not x[1], x[0]))
            return candidates[0][0], candidates[0][1]
        return None, None
    except Exception:
        return None, None

def _probe_overlay_plane_modetest() -> tuple[int | None, bool | None]:
    """Fallback: usa 'modetest -p' se disponibile per trovare un plane 'Overlay'."""
    try:
        out = subprocess.run(["bash", "-lc", "modetest -p 2>/dev/null"], capture_output=True, text=True, timeout=2)
        txt = out.stdout or ""
        if not txt:
            return None, None
        # Trova blocchi 'plane id N' e guarda se include 'type: Overlay'
        plane_blocks = re.split(r"(?m)^\s*plane id ", txt)
        best_id: int | None = None
        for block in plane_blocks:
            m = re.match(r"(\d+).*", block)
            if not m:
                continue
            pid = int(m.group(1))
            if re.search(r"(?mi)type\s*:\s*Overlay", block):
                best_id = pid
                break
        return (best_id, None)
    except Exception:
        return None, None

def overlay_probe(auto_persist: bool = True) -> dict:
    """Rileva automaticamente un plane overlay KMS e aggiorna OVERLAY_KMS_PLANE_ID se trovato."""
    info = {"detected": False, "method": None, "plane_id": None, "zpos_supported": None, "error": None}
    if not OVERLAY_USE_KMS:
        info["error"] = "OVERLAY_USE_KMS=0"
        overlay_probe_info.update(info)
        return info
    # 1) Sysfs
    pid, zpos_sup = _probe_overlay_plane_sysfs()
    if pid:
        try:
            globals()["OVERLAY_KMS_PLANE_ID"] = int(pid)
        except Exception:
            pass
        info.update({"detected": True, "method": "sysfs", "plane_id": pid, "zpos_supported": zpos_sup})
        overlay_probe_info.update(info)
        try:
            if auto_persist:
                persist_settings({"OVERLAY_KMS_PLANE_ID": OVERLAY_KMS_PLANE_ID})
        except Exception:
            pass
        print(f"[OVERLAY] Plane auto-rilevato via sysfs: id={pid}, zpos_supported={zpos_sup}", flush=True)
        return info
    # 2) modetest fallback
    pid2, zpos_sup2 = _probe_overlay_plane_modetest()
    if pid2:
        try:
            globals()["OVERLAY_KMS_PLANE_ID"] = int(pid2)
        except Exception:
            pass
        info.update({"detected": True, "method": "modetest", "plane_id": pid2, "zpos_supported": zpos_sup2})
        overlay_probe_info.update(info)
        try:
            if auto_persist:
                persist_settings({"OVERLAY_KMS_PLANE_ID": OVERLAY_KMS_PLANE_ID})
        except Exception:
            pass
        print(f"[OVERLAY] Plane auto-rilevato via modetest: id={pid2}", flush=True)
        return info
    info["error"] = "Nessun plane overlay trovato"
    overlay_probe_info.update(info)
    print("[OVERLAY] Auto-rilevamento plane fallito", flush=True)
    return info

# ---------- Player pipeline ----------
def decoder_name():
    res = os.popen("gst-inspect-1.0 v4l2h264dec 2>/dev/null").read()
    return "v4l2h264dec" if "v4l2h264dec" in res else "avdec_h264"

# Handler bus GStreamer minimale, definito in anticipo per evitare NameError in ambienti/test che invocano pipeline presto
def on_bus_message(bus, message, data):
    try:
        t = message.type
        if hasattr(Gst, "MessageType"):
            if t == Gst.MessageType.ERROR:
                try:
                    err, dbg = message.parse_error()
                    print(f"[GST] ERROR: {err} dbg={dbg}", flush=True)
                except Exception:
                    print("[GST] ERROR", flush=True)
            elif t == Gst.MessageType.EOS:
                print("[GST] EOS", flush=True)
    except Exception:
        # In ambienti stub/minimali ignoriamo eventuali errori del bus
        pass

def build_player_pipeline(path):
    sink = "kmssink" if USE_KMS else "autovideosink"
    dec = "decodebin"
    # Supporta immagini statiche (png/jpg/bmp) usando imagefreeze per mantenere un frame costante
    try:
        is_image = str(Path(path).suffix or "").lower() in {".png", ".jpg", ".jpeg", ".bmp"}
    except Exception:
        is_image = False
    # Nota: su RPi potresti usare v4l2h264dec se disponibile (già funzione decoder_name se serve)
    if USE_HW_DECODER:
        # Preferisci decodebin (che può usare hw) per compatibilità; potremmo forzare caps in futuro
        dec = "decodebin"
    caps_filter = f"video/x-raw,width={TARGET_WIDTH},height={TARGET_HEIGHT},framerate={TARGET_FPS}"
    # Nota: path quotato per evitare errori con spazi o caratteri speciali
    if is_image:
        desc = (
            f"filesrc location=\"{path}\" ! queue ! {dec} name=dec ! videoconvert ! imagefreeze ! videoconvert ! videoscale ! "
            f"videorate ! {caps_filter} ! videoconvert ! video/x-raw,format=RGBA ! alpha name=af ! videoconvert ! "
            f"videobalance name=vb ! {sink}"
        )
    else:
        desc = (
            f"filesrc location=\"{path}\" ! queue ! {dec} name=dec ! queue ! videoconvert ! videoscale ! "
            f"videorate ! {caps_filter} ! videoconvert ! video/x-raw,format=RGBA ! alpha name=af ! videoconvert ! "
            f"videobalance name=vb ! {sink}"
        )
    print(f"[PLAYER] Pipeline: {desc}", flush=True)
    try:
        pipeline = Gst.parse_launch(desc)
        vb = pipeline.get_by_name("vb")
        af = pipeline.get_by_name("af")
        bus = pipeline.get_bus()
        try:
            bus.add_signal_watch()
            bus.connect("message", on_bus_message, None)
        except Exception:
            # Ambienti senza main loop/signals (stub/Windows)
            pass
        decbin = pipeline.get_by_name("dec")
        if decbin:
            def on_pad_added(db, pad):
                caps = pad.get_current_caps() or pad.query_caps()
                if not caps:
                    return
                s = caps.get_structure(0)
                if not s or not s.get_name().startswith("video/"):
                    return
                if s.has_field("framerate"):
                    try:
                        ok, num, den = s.get_fraction("framerate")
                        if ok and num and den:
                            real = f"{num}/{den}"
                            if real != TARGET_FPS:
                                print(f"[FRAMERATE] Sorgente {real} (target {TARGET_FPS})", flush=True)
                    except Exception as fe:
                        print(f"[FRAMERATE] Errore lettura framerate: {fe}", flush=True)
                if s.has_field("width") and s.has_field("height"):
                    w = s.get_int("width")[1]; h = s.get_int("height")[1]
                    print(f"[VIDEO] Stream {w}x{h}", flush=True)
            decbin.connect("pad-added", on_pad_added)
        return pipeline, vb, af
    except Exception as e:
        # Se fallisce con kmssink e USE_KMS attivo prova fallback
        if USE_KMS:
            print(f"[PLAYER] Errore con kmssink: {e} -> fallback autovideosink", flush=True)
            try:
                fb_desc = desc.replace("kmssink", "autovideosink")
                pipeline = Gst.parse_launch(fb_desc)
                vb = pipeline.get_by_name("vb")
                af = pipeline.get_by_name("af")
                bus = pipeline.get_bus()
                try:
                    bus.add_signal_watch()
                    bus.connect("message", on_bus_message, None)
                except Exception:
                    pass
                decbin = pipeline.get_by_name("dec")
                if decbin:
                    def on_pad_added(db, pad):
                        caps = pad.get_current_caps() or pad.query_caps()
                        if not caps:
                            return
                        s = caps.get_structure(0)
                        if not s or not s.get_name().startswith("video/"):
                            return
                        if s.has_field("framerate"):
                            try:
                                ok, num, den = s.get_fraction("framerate")
                                if ok and num and den:
                                    real = f"{num}/{den}"
                                    if real != TARGET_FPS:
                                        print(f"[FRAMERATE] Sorgente {real} (target {TARGET_FPS})", flush=True)
                            except Exception as fe:
                                print(f"[FRAMERATE] Errore lettura framerate: {fe}", flush=True)
                        if s.has_field("width") and s.has_field("height"):
                            w = s.get_int("width")[1]; h = s.get_int("height")[1]
                            print(f"[VIDEO] Stream {w}x{h}", flush=True)
                    decbin.connect("pad-added", on_pad_added)
                print("[PLAYER] Fallback autovideosink attivo", flush=True)
                return pipeline, vb, af
            except Exception as e2:
                print(f"[PLAYER] Fallback autovideosink fallito: {e2}", flush=True)
        print(f"[PLAYER] ERRORE nella creazione pipeline: {e}", flush=True)
        raise

def pipeline_has_kmssink(p: Any) -> bool:
    try:
        it = p.iterate_elements()
        ok, elem = it.next()
        while ok:
            try:
                fac = elem.get_factory()
                if fac and fac.get_name() == "kmssink":
                    return True
            except Exception:
                pass
            ok, elem = it.next()
    except Exception:
        pass
    return False

def on_bus_message(bus, msg, data):
    t = msg.type
    if t == Gst.MessageType.EOS:
        print("[PLAYER] Fine del video (EOS)", flush=True)
        # Avanzamento playlist se presente
        if playlist["items"]:
            next_index = playlist["index"] + 1
            if next_index >= len(playlist["items"]):
                if playlist["loop"]:
                    next_index = 0
                else:
                    # Fine playlist
                    if player["pipeline"]:
                        player["pipeline"].set_state(Gst.State.NULL)
                    player["state"] = "stopped"
                    print("[PLAYER] Fine playlist", flush=True)
                    return
            playlist["index"] = next_index
            next_path = playlist["items"][next_index]
            print(f"[PLAYLIST] Avanzo a: {next_path}", flush=True)
            # Aggiorna path e adotta se precaricata
            global VIDEO_PATH
            VIDEO_PATH = next_path
            if not adopt_preloaded(next_path):
                stop_play()
                if autoplay.get("enabled"):
                    start_play(AUTOPLAY_FADE_SECONDS)
                else:
                    start_play_with_path(next_path)
            return
        # Nessuna playlist: comportamento originale loop singolo
        if player["loop"] and player["pipeline"]:
            print("[PLAYER] Riavvio loop", flush=True)
            player["pipeline"].seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
        else:
            stop_play()
            print("[PLAYER] Riproduzione terminata", flush=True)
            if not splash["active"]:
                # Se lo splash è nascosto, mostra il nero in pausa per mantenere un'immagine stabile
                try:
                    if not show_idle_black():
                        print("[PLAYER] Idle black fallback non disponibile", flush=True)
                except Exception as exc:
                    print(f"[PLAYER] Idle black fallback errore: {exc}", flush=True)
    elif t == Gst.MessageType.ERROR:
        err, debug = msg.parse_error()
        print(f"[PLAYER] GST ERROR: {err}", flush=True)
        print(f"[PLAYER] Debug info: {debug}", flush=True)
        # Fallback automatico: se kmssink va in errore (permessi DRM o altro), passa ad autovideosink
        try:
            if USE_KMS and player["pipeline"] and pipeline_has_kmssink(player["pipeline"]):
                print("[PLAYER] KMS errore/permessi: fallback ad autovideosink", flush=True)
                # Disattiva KMS e ricrea pipeline sullo stesso media
                globals()["USE_KMS"] = False
                if player["pipeline"]:
                    player["pipeline"].set_state(Gst.State.NULL)
                # Ricrea pipeline e riparti
                def restart_after_fallback():
                    try:
                        ensure_pipeline()
                        if player["vb"]:
                            player["vb"].set_property("brightness", -1.0)
                        player["pipeline"].set_state(Gst.State.PLAYING)
                        player["state"] = "playing"
                        print("[PLAYER] Ripartenza con autovideosink effettuata", flush=True)
                    except Exception as fe:
                        print(f"[PLAYER] Fallback start failed: {fe}", flush=True)
                GLib.idle_add(restart_after_fallback)
                return
        except Exception as _:
            pass
        if player["pipeline"]:
            stop_play()
        else:
            player["state"] = "error"
        if not splash["active"]:
            try:
                if not show_idle_black():
                    print("[PLAYER] Idle black fallback non disponibile", flush=True)
            except Exception as exc:
                print(f"[PLAYER] Idle black fallback errore: {exc}", flush=True)
    elif t == Gst.MessageType.WARNING:
        warn, debug = msg.parse_warning()
        print(f"[PLAYER] GST WARNING: {warn}", flush=True)
        print(f"[PLAYER] Debug info: {debug}", flush=True)
    elif t == Gst.MessageType.INFO:
        info, debug = msg.parse_info()
        print(f"[PLAYER] GST INFO: {info}", flush=True)

    # Se test mode è attivo, ignora messaggi player

def ensure_pipeline():
    if player["pipeline"]:
        return
    print(f"[PLAYER] Creo pipeline per: {VIDEO_PATH}", flush=True)
    p, vb, af = build_player_pipeline(VIDEO_PATH)
    player["pipeline"] = p
    player["vb"] = vb
    player["alpha"] = af
    print("[PLAYER] Pipeline creata", flush=True)

def prepare_pipeline_for(path: str):
    """Crea pipeline per 'path', la porta al primo frame visibile e la lascia in PAUSED.
    Obiettivo: al termine di prepare il contenuto sia già presentato sullo schermo (dietro l'overlay)
    per un GO immediato e senza flash.
    """
    # Libera eventualmente un preload precedente
    if preloaded["pipeline"]:
        try:
            preloaded["pipeline"].set_state(Gst.State.NULL)
        except Exception:
            pass
        preloaded.update({"path": None, "pipeline": None, "vb": None, "alpha": None})
    p, vb, af = build_player_pipeline(path)

    # Tenta di abilitare la visualizzazione del frame di preroll (se supportato dal sink)
    try:
        it = p.iterate_elements()
        ok, elem = it.next()
        while ok:
            try:
                elem.set_property("show-preroll-frame", True)
            except Exception:
                pass
            ok, elem = it.next()
    except Exception:
        pass

    # Preroll in PAUSED
    p.set_state(Gst.State.PAUSED)
    # Assicurati di essere esattamente all'inizio
    try:
        p.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
    except Exception:
        pass

    # Breve kick in PLAYING per far presentare il primo frame, poi torna in PAUSED
    def _kick_play_then_pause():
        try:
            p.set_state(Gst.State.PLAYING)
        except Exception:
            # anche se fallisce, memorizza comunque la pipeline
            pass

        def _pause_back():
            try:
                p.set_state(Gst.State.PAUSED)
            except Exception:
                pass
            return False

        # ~80ms sono in genere sufficienti per presentare il primo frame
        GLib.timeout_add(80, _pause_back)
        return False

    GLib.idle_add(_kick_play_then_pause)

    preloaded.update({"path": path, "pipeline": p, "vb": vb, "alpha": af})
    print(f"[FASTSTART] Preparata pipeline con primo frame presentato e in PAUSED per {path}", flush=True)
    fsm_preparing("prepare_pipeline", action="faststart_prepare")

def adopt_preloaded(path: str) -> bool:
    """Se esiste una pipeline precaricata per 'path', adottala come player principale.
    Ritorna True se adottata, False altrimenti.
    """
    try:
        if not preloaded.get("pipeline") or preloaded.get("path") != path:
            return False
        # Chiudi eventuale pipeline corrente
        if player.get("pipeline"):
            try:
                player["pipeline"].set_state(Gst.State.NULL)
            except Exception:
                pass
        # Adotta la precaricata
        player["pipeline"] = preloaded["pipeline"]
        player["vb"] = preloaded.get("vb")
        player["alpha"] = preloaded.get("alpha")
        player["state"] = "playing"
        # Nascondi splash
        if splash.get("active"):
            hide_splash()
        # Porta in PLAYING dalla PAUSED prerollata
        player["pipeline"].set_state(Gst.State.PLAYING)
    finally:
        # Sgancia sempre lo stato preloaded
        preloaded.update({"path": None, "pipeline": None, "vb": None, "alpha": None})
    print("[FASTSTART] Adottata pipeline precaricata", flush=True)
    fsm_playing("gst_adopted_preloaded")
    fsm_playing("gst_adopted_preloaded")
    return True

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


def _looks_like_image(header: bytes, suffix: str) -> bool:
    """Apply simple magic-number checks for common image formats."""

    if suffix in {".png"}:
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix in {".bmp"}:
        return header.startswith(b"BM")
    if suffix in {".gif"}:
        return header.startswith(b"GIF87a") or header.startswith(b"GIF89a")
    if suffix in {".webp"}:
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    if suffix in {".tif", ".tiff"}:
        return header.startswith(b"II*\x00") or header.startswith(b"MM\x00*")
    return False


def validate_media_file(path: Path) -> bool:
    if not path.exists():
        print(f"[PLAYER] ERRORE: File non trovato: {path}", flush=True); return False
    if not path.is_file():
        print(f"[PLAYER] ERRORE: Percorso non è un file: {path}", flush=True); return False
    suffix = path.suffix.lower()
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
    except Exception as e:
        print(f"[PLAYER] ERRORE lettura header {path}: {e}", flush=True)
        return False

    # Consenti immagini supportate
    if suffix in _IMAGE_EXTENSIONS:
        if _looks_like_image(header, suffix):
            return True
        # Accetta anche immagini con header valido ma estensione differente
        if header.startswith(b"\x89PNG") or header.startswith(b"\xff\xd8\xff") or header.startswith(b"GIF"):
            return True
        if header.startswith(b"RIFF") and header[8:12] in {b"WEBP", b"AVI "}:
            # WEBP o alcuni TIFF/AVI embedded
            return True
        print(f"[PLAYER] WARN: Immagine con header non riconosciuto ({header[:8].hex()}); accetto {path}", flush=True)
        return True

    if len(header) < 12:
        print(f"[PLAYER] ERRORE: File troppo corto: {path}", flush=True); return False
    # MP4: bytes 4-8 = 'ftyp'
    if header[4:8] == b'ftyp':
        return True
    # Matroska
    if header.startswith(b'\x1a\x45\xdf\xa3'):
        return True
    # AVI: 'RIFF....AVI '
    if header.startswith(b'RIFF') and header[8:12] == b'AVI ':
        return True
    print(f"[PLAYER] ERRORE: Header non riconosciuto ({header[:8].hex()}) per {path}", flush=True)
    return False

def start_play_with_path(path: str, *, action: str | None = None):
    global VIDEO_PATH
    VIDEO_PATH = path
    start_play(action=action)

def start_play(fade_in_seconds: float = 0.5, *, action: str | None = None):
    print(f"[PLAYER] Avvio riproduzione: {VIDEO_PATH}", flush=True)
    fsm_preparing("start_play", action=(action or "play"))
    # Badge/telemetria azione per la GUI
    try:
        if action in {"next", "prev", "play", "faststart_go"}:
            gui_log("action_badge", data={"action": action})
    except Exception:
        pass
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend is None:
            print("[PLAYER] Nessun backend disponibile", flush=True)
            fsm_idle("backend_missing", error="no_backend")
            return
        try:
            _restore_idle_black_cover(backend)
        except Exception:
            pass
        try:
            backend.play(VIDEO_PATH, player.get("loop"), fade_in_seconds)
        except Exception as exc:
            print(f"[PLAYER] Errore backend: {exc}", flush=True)
            player["state"] = "error"
            fsm_idle("play_failed", error=str(exc))
            return
        # Per backend non-GST (es. cvlc): esegui fade-out overlay SOLO quando il contenuto risulta davvero in playing
        try:
            if OVERLAY_ENABLED:
                def _fade_when_ready(retries=[0]):
                    try:
                        be = current_framework.get("backend")
                        ready = bool(be and hasattr(be, "is_playing") and be.is_playing())
                    except Exception:
                        ready = False
                    if ready:
                        # If a static brightness has been requested via overlay, keep it
                        try:
                            br = globals().get("_LAST_VISUAL_BRIGHTNESS")
                            if isinstance(br, (int, float)):
                                brf = float(br)
                                if 0.0 <= brf < 0.999:
                                    # Desired overlay alpha is 1 - brightness; do not remove overlay
                                    target_alpha = max(0.0, min(1.0, 1.0 - brf))
                                    try:
                                        overlay_fade_to(target_alpha, max(0.05, float(OVERLAY_FADE_OUT_ON_PLAY_S)))
                                    except Exception:
                                        pass
                                    return False
                        except Exception:
                            pass
                        try:
                            gui_log("overlay_fade_out_ready", data={"duration": OVERLAY_FADE_OUT_ON_PLAY_S})
                        except Exception:
                            pass
                        overlay_fade_to(0.0, max(0.05, float(OVERLAY_FADE_OUT_ON_PLAY_S)))
                        def _hide():
                            try:
                                overlay_hide()
                                gui_log("overlay_hidden_after_start")
                            except Exception:
                                pass
                            return False
                        GLib.timeout_add(int(max(0.2, OVERLAY_FADE_OUT_ON_PLAY_S) * 1000 + 100), _hide)
                        return False
                    # riprova per ~3s (30 tentativi ogni 100ms)
                    retries[0] += 1
                    if retries[0] >= 30:
                        try:
                            gui_log("overlay_fade_out_fallback", level="WARNING", data={"duration": 0.2})
                        except Exception:
                            pass
                        # fallback: esegui comunque fade-out breve per evitare fantasma prompt
                        overlay_fade_to(0.0, 0.2)
                        GLib.timeout_add(400, lambda: (overlay_hide(), False)[1])
                        return False
                    return True
                GLib.timeout_add(100, _fade_when_ready)
        except Exception:
            pass
        persist_settings()
        fsm_playing("backend_started", action=(action or "play"))
        _autoplay_on_play_started(VIDEO_PATH)
        return
    media_path = Path(VIDEO_PATH)
    if not validate_media_file(media_path):
        player["state"] = "error"; return
    _reset_visual_effects_before_play()
    if not adopt_preloaded(VIDEO_PATH):
        ensure_pipeline()
        # Inizializza a nero: preferisci alpha se disponibile, altrimenti brightness
        if player["alpha"]:
            try: player["alpha"].set_property("alpha", 0.0)
            except Exception: pass
        if player["vb"]:
            try: player["vb"].set_property("brightness", -1.0)
            except Exception: pass
        if splash["active"]:
            hide_splash()
        player["pipeline"].set_state(Gst.State.PLAYING)
        player["state"] = "playing"
        if playlist["items"]:
            schedule_preload()
        print("[PLAYER] Riproduzione avviata (nuova pipeline)", flush=True)
        fsm_playing("gst_pipeline_started", action=(action or "play"))
    else:
        print("[PLAYER] Riproduzione avviata (pipeline precaricata)", flush=True)
        fsm_playing("gst_adopted_preloaded", action=(action or "play"))
    # Fade-in automatico
    if fade_in_seconds and fade_in_seconds > 0:
        if player["alpha"]:
            fade_opacity_to(1.0, fade_in_seconds, start_from=0.0)
        elif player["vb"]:
            fade_to(0.0, fade_in_seconds, start_from=-1.0)
    # Persist last media
    persist_settings()
    _autoplay_on_play_started(VIDEO_PATH)
    # Una volta che si avvia la riproduzione, la readiness per il "go" può considerarsi consumata
    try:
        show_state.update({"ready": False})
    except Exception:
        pass

    # Start end-of-media monitor to pre-fade overlay (GST only)
    _start_end_monitor()

def _start_end_monitor():
    """Monitor remaining time and trigger overlay fade-in shortly before end (GST only)."""
    try:
        # Cancel previous monitor
        mid = fsm.get("end_monitor_id")
        if mid:
            try:
                GLib.source_remove(mid)
            except Exception:
                pass
            fsm["end_monitor_id"] = None
        if current_framework["name"] != "gst":
            return
        p = player.get("pipeline")
        if not p:
            return
        lead_ns = int(max(0.2, float(OVERLAY_FADE_IN_ON_STOP_S)) * Gst.SECOND)

        def _tick():
            try:
                if player.get("state") != "playing":
                    return True
                ok_dur, duration = p.query_duration(Gst.Format.TIME)  # type: ignore[attr-defined]
                ok_pos, position = p.query_position(Gst.Format.TIME)  # type: ignore[attr-defined]
            except Exception:
                ok_dur, ok_pos, duration, position = False, False, 0, 0
            if ok_dur and ok_pos and duration and duration > 0:
                remaining = duration - position
                if 0 <= remaining <= lead_ns:
                    try:
                        if OVERLAY_ENABLED:
                            overlay_show(alpha=0.0)
                            overlay_fade_to(1.0, max(0.05, float(OVERLAY_FADE_IN_ON_STOP_S)))
                            gui_log("overlay_prefade_before_eos", data={"lead_ns": lead_ns})
                    except Exception:
                        pass
                    # keep monitoring, but we did the fade
                    return False
            return True

        fsm["end_monitor_id"] = GLib.timeout_add(200, _tick)
    except Exception:
        pass

def pause_play():
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "pause"):
            try:
                backend.pause()
            except Exception as exc:
                print(f"[PLAYER] Pause backend fallita: {exc}", flush=True)
        try:
            gui_log("pause")
        except Exception:
            pass
        return
    if player["pipeline"]:
        player["pipeline"].set_state(Gst.State.PAUSED)
        player["state"] = "paused"
        try:
            gui_log("pause")
        except Exception:
            pass

def resume_play():
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "resume"):
            try:
                _restore_idle_black_cover(backend)
            except Exception:
                pass
            try:
                backend.resume()
            except Exception as exc:
                print(f"[PLAYER] Resume backend fallita: {exc}", flush=True)
        try:
            gui_log("resume")
        except Exception:
            pass
        return
    if player["pipeline"]:
        player["pipeline"].set_state(Gst.State.PLAYING)
        player["state"] = "playing"
        try:
            gui_log("resume")
        except Exception:
            pass

def stop_play():
    fsm_stopping("stop_play")
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "stop"):
            try:
                backend.stop()
            except Exception as exc:
                print(f"[PLAYER] Stop backend fallita: {exc}", flush=True)
        player["state"] = "stopped"
        player["vb"] = None
        player["alpha"] = None
    else:
        if player["pipeline"]:
            player["pipeline"].set_state(Gst.State.NULL)
            player["state"] = "stopped"
            player["pipeline"] = None
        player["vb"] = None
        player["alpha"] = None
    try:
        gui_log("stop")
    except Exception:
        pass
    # Se lo splash è nascosto, riporta a nero: prima prova con overlay (fade-in morbido), poi idle black
    try:
        if not splash.get("active"):
            try:
                if OVERLAY_ENABLED:
                    gui_log("overlay_fade_in_on_stop", data={"duration": OVERLAY_FADE_IN_ON_STOP_S})
                    overlay_fade_to(1.0, max(0.05, float(OVERLAY_FADE_IN_ON_STOP_S)))
            except Exception:
                pass
            try:
                gui_log("idle_black_show")
            except Exception:
                pass
            show_idle_black()
    except Exception:
        pass
    fsm_idle("stopped")

def set_loop(on: bool):
    player["loop"] = bool(on)
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "set_loop"):
            try:
                backend.set_loop(player["loop"])
            except Exception as exc:
                print(f"[PLAYER] Set loop backend fallita: {exc}", flush=True)

def fade_to(target: float, duration_s: float = 1.0, on_complete=None, start_from: float | None = None):
    """Esegue fade non bloccante sul brightness.
    target: valore finale (-1..1)
    duration_s: durata in secondi
    on_complete: callback chiamata al termine
    start_from: se definito forza brightness iniziale prima di iniziare
    """
    global fade_seq
    vb = player["vb"]
    if not vb or duration_s <= 0:
        if vb:
            vb.set_property("brightness", target)
        if on_complete:
            GLib.idle_add(on_complete)
        return
    if start_from is not None:
        vb.set_property("brightness", start_from)
    cur = vb.get_property("brightness")
    steps = max(1, int(duration_s / (FADE_INTERVAL_MS / 1000.0)))
    step_val = (target - cur) / steps
    fade_seq += 1
    my_seq = fade_seq

    def stepper(i=[0], val=[cur]):
        if my_seq != fade_seq:
            return False  # Cancellato da nuovo fade
        if not player["vb"]:
            return False
        val[0] += step_val
        final = (i[0] + 1) >= steps
        vb.set_property("brightness", max(-1.0, min(1.0, target if final else val[0])))
        i[0] += 1
        if final:
            if on_complete:
                GLib.idle_add(on_complete)
            return False
        return True

    GLib.timeout_add(FADE_INTERVAL_MS, stepper)

def fade_opacity_to(target: float, duration_s: float = 1.0, on_complete=None, start_from: float | None = None):
    """Fade sull'opacità (elemento alpha), range 0..1. Fallback: no-op se alpha mancante."""
    global fade_seq
    af = player["alpha"]
    if not af or duration_s <= 0:
        if af:
            af.set_property("alpha", max(0.0, min(1.0, target)))
        if on_complete:
            GLib.idle_add(on_complete)
        return
    if start_from is not None:
        af.set_property("alpha", start_from)
    try:
        cur = float(af.get_property("alpha"))
    except Exception:
        cur = 1.0
    steps = max(1, int(duration_s / (FADE_INTERVAL_MS / 1000.0)))
    step_val = (target - cur) / steps
    fade_seq += 1
    my_seq = fade_seq

    def stepper(i=[0], val=[cur]):
        if my_seq != fade_seq:
            return False
        if not player["alpha"]:
            return False
        val[0] += step_val
        final = (i[0] + 1) >= steps
        af.set_property("alpha", max(0.0, min(1.0, target if final else val[0])))
        i[0] += 1
        if final:
            if on_complete:
                GLib.idle_add(on_complete)
            return False
        return True

    GLib.timeout_add(FADE_INTERVAL_MS, stepper)

def ping_on_player(duration_ms: int = 120):
    """Effetto lampo veloce sul player: preferisci alpha (black flash) o brightness boost."""
    try:
        # Preferisci saturazione alta (flash "bianco")
        if player["vb"] and player["vb"] is not None:
            vb = player["vb"]
            cur_sat = None
            try:
                cur_sat = float(vb.get_property("saturation"))
            except Exception:
                cur_sat = None
            if cur_sat is not None:
                vb.set_property("saturation", 2.0)
                def restore():
                    try:
                        vb.set_property("saturation", cur_sat)
                    except Exception:
                        pass
                    return False
                GLib.timeout_add(max(10, duration_ms), restore)
                return True
        if player["alpha"]:
            af = player["alpha"]
            cur = float(af.get_property("alpha"))
            af.set_property("alpha", 0.0)
            def restore():
                try:
                    af.set_property("alpha", cur)
                except Exception:
                    pass
                return False
            GLib.timeout_add(max(10, duration_ms), restore)
            return True
        elif player["vb"]:
            vb = player["vb"]
            cur = float(vb.get_property("brightness"))
            boost = max(-1.0, min(1.0, cur + 0.6))
            vb.set_property("brightness", boost)
            def restore():
                try:
                    vb.set_property("brightness", cur)
                except Exception:
                    pass
                return False
            GLib.timeout_add(max(10, duration_ms), restore)
            return True
    except Exception:
        return False
    return False

def push_white_splash_frame(appsrc):
    """Pusha un frame di splash completamente bianco (flash)."""
    img = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (255, 255, 255))
    buf = Gst.Buffer.new_allocate(None, TARGET_WIDTH * TARGET_HEIGHT * 3, None)
    buf.fill(0, img.tobytes())
    now = int(time.time() * Gst.SECOND)
    buf.pts = buf.dts = now
    appsrc.emit("push-buffer", buf)

def ping_on_splash(duration_ms: int = 120):
    """Effetto lampo sempre visibile: frame bianco temporaneo, poi ripristino splash."""
    try:
        if not splash.get("active"):
            return False

        # NB: gli endpoint /test/on e /test/off sono definiti a livello modulo (vedi più sotto)
        appsrc = splash.get("src")
        vb = splash.get("vb")
        if not appsrc:
            return False
        # 1) Boost visivo immediato sul videobalance (più affidabile del solo frame)
        restored = {"done": False}
        cur_bri = None; cur_sat = None
        if vb:
            try:
                cur_bri = float(vb.get_property("brightness"))
            except Exception:
                cur_bri = None
            try:
                cur_sat = float(vb.get_property("saturation"))
            except Exception:
                cur_sat = None
            try:
                if cur_bri is not None:
                    vb.set_property("brightness", 1.0)
                if cur_sat is not None:
                    vb.set_property("saturation", 2.0)
            except Exception:
                pass
        # 2) Inoltre pusha un frame bianco
        GLib.idle_add(push_white_splash_frame, appsrc)
        # Restore dopo la durata
        def restore():
            if restored["done"]:
                return False
            restored["done"] = True
            try:
                # Ripristina frame splash
                push_splash_frame(appsrc)
            except Exception:
                pass
            # Ripristina proprietà vb
            try:
                if vb and (cur_bri is not None):
                    vb.set_property("brightness", cur_bri)
                if vb and (cur_sat is not None):
                    vb.set_property("saturation", cur_sat)
            except Exception:
                pass
            return False
        GLib.timeout_add(max(20, duration_ms), restore)
        return True
    except Exception:
        return False

# ---------- Test mode (pattern dinamico) endpoints (definiti a livello modulo) ----------
def build_test_pipeline():
    sink = "kmssink" if USE_KMS else "autovideosink"
    caps = f"video/x-raw,format=RGB,width={TARGET_WIDTH},height={TARGET_HEIGHT},framerate={TARGET_FPS}"
    desc = f"appsrc name=testsrc is-live=true format=time ! {caps} ! videoconvert ! {sink}"
    print(f"[TEST] Pipeline: {desc}", flush=True)
    p = Gst.parse_launch(desc)
    src = p.get_by_name("testsrc")
    return p, src

def _test_push_frame():
    try:
        if not test_mode["active"] or not test_mode["src"]:
            return False
        W, H = TARGET_WIDTH, TARGET_HEIGHT
        img = Image.new("RGB", (W, H), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        y = test_mode["y"] % H
        x = test_mode["x"] % W
        draw.line([(0, y), (W-1, y)], fill=(255, 255, 255))
        draw.line([(x, 0), (x, H-1)], fill=(255, 0, 0))
        buf = Gst.Buffer.new_allocate(None, W * H * 3, None)
        buf.fill(0, img.tobytes())
        now = int(time.time() * Gst.SECOND)
        buf.pts = buf.dts = now
        test_mode["src"].emit("push-buffer", buf)
        test_mode["y"] = (y + 5) % H
        test_mode["x"] = (x + 7) % W
    except Exception as e:
        print(f"[TEST] push error: {e}", flush=True)
        return False
    return True

@app.post("/test/on")
def api_test_on(
    width: int | None = Query(None),
    height: int | None = Query(None),
    offset_x: int | None = Query(None, alias="offsetX"),
    offset_y: int | None = Query(None, alias="offsetY"),
    speed: float | None = Query(None),
    payload: dict[str, Any] | None = Body(None),
):
    body = payload or {}

    def _merge_int(current: int | None, *keys: str) -> int | None:
        for key in keys:
            if key in body and body[key] is not None:
                try:
                    return int(body[key])
                except Exception:
                    continue
        return current

    def _merge_float(current: float | None, *keys: str) -> float | None:
        for key in keys:
            if key in body and body[key] is not None:
                try:
                    return float(body[key])
                except Exception:
                    continue
        return current

    width = _merge_int(width, "width")
    height = _merge_int(height, "height")
    offset_x = _merge_int(offset_x, "offset_x", "offsetX")
    offset_y = _merge_int(offset_y, "offset_y", "offsetY")
    speed = _merge_float(speed, "speed")

    if current_framework["name"] == "off":
        try:
            ensure_backend()
            backend = current_framework.get("backend")
            if backend and hasattr(backend, "test_on"):
                if test_mode.get("ticker"):
                    try:
                        GLib.source_remove(test_mode["ticker"])
                    except Exception:
                        pass
                    test_mode["ticker"] = None
                if test_mode.get("pipeline"):
                    try:
                        test_mode["pipeline"].set_state(Gst.State.NULL)
                    except Exception:
                        pass
                    test_mode["pipeline"] = None
                backend.test_on(width=width, height=height, offset_x=offset_x, offset_y=offset_y, speed=speed)
                test_mode.update({"pipeline": None, "src": None, "active": True})
                print(
                    f"[TEST] Delegato a OFF-player (width={width}, height={height}, offset=({offset_x},{offset_y}), speed={speed})",
                    flush=True,
                )
                return {"ok": True, "framework": "off"}
            return JSONResponse(status_code=500, content={"ok": False, "error": "Backend OFF non disponibile"})
        except Exception as exc:
            return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    try:
        # Stop player corrente per evitare conflitti KMS
        if player.get("pipeline"):
            try:
                player["pipeline"].set_state(Gst.State.NULL)
            except Exception:
                pass
            player.update({"pipeline": None, "vb": None, "alpha": None, "state": "stopped"})
        # Nascondi splash
        if splash.get("active"):
            hide_splash()
        p, src = build_test_pipeline()
        test_mode.update({"pipeline": p, "src": src, "active": True, "x": 0, "y": 0})
        p.set_state(Gst.State.PLAYING)
        # Ticker a ~25fps
        if test_mode["ticker"]:
            try: GLib.source_remove(test_mode["ticker"])
            except Exception: pass
        test_mode["ticker"] = GLib.timeout_add(40, _test_push_frame)
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.post("/test/off")
def api_test_off():
    if current_framework["name"] == "off":
        try:
            ensure_backend()
            backend = current_framework.get("backend")
            if backend and hasattr(backend, "test_off"):
                if test_mode.get("ticker"):
                    try:
                        GLib.source_remove(test_mode["ticker"])
                    except Exception:
                        pass
                    test_mode["ticker"] = None
                if test_mode.get("pipeline"):
                    try:
                        test_mode["pipeline"].set_state(Gst.State.NULL)
                    except Exception:
                        pass
                    test_mode["pipeline"] = None
                backend.test_off()
                test_mode.update({"pipeline": None, "src": None, "active": False})
                print("[TEST] Delegato a OFF-player (off)", flush=True)
                return {"ok": True, "framework": "off"}
            return JSONResponse(status_code=500, content={"ok": False, "error": "Backend OFF non disponibile"})
        except Exception as exc:
            return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    try:
        if test_mode.get("ticker"):
            try: GLib.source_remove(test_mode["ticker"])
            except Exception: pass
            test_mode["ticker"] = None
        if test_mode.get("pipeline"):
            try: test_mode["pipeline"].set_state(Gst.State.NULL)
            except Exception: pass
        test_mode.update({"pipeline": None, "src": None, "active": False})
        # Ripristina il nero di idle
        if not splash.get("active"):
            try:
                show_idle_black()
            except Exception:
                pass
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

def _reset_visual_effects_before_play():
    """Ripristina valori standard per evitare che un ping residuo alteri il contenuto."""
    try:
        if player.get("vb"):
            try:
                player["vb"].set_property("saturation", 1.0)
            except Exception:
                pass
    except Exception:
        pass

@app.post("/ping")
def api_ping(duration_ms: int = Query(200), in_time: float | None = Query(None)):
    """Lampo visivo opzionalmente schedulato (in_time epoch o delay)."""
    result = {"ok": True, "scheduled": False, "state": player.get("state"), "delay": 0.0}
    def do_ping():
        scheduled = False
        if player.get("pipeline"):
            scheduled = ping_on_player(duration_ms)
        elif splash.get("active"):
            scheduled = ping_on_splash(duration_ms)
        result["scheduled"] = bool(scheduled)
    delay = schedule_action(in_time, do_ping)
    result["delay"] = delay
    return result

@app.get("/ping")
def api_ping_get(duration_ms: int = Query(200), in_time: float | None = Query(None)):
    return api_ping(duration_ms, in_time)

def schedule_preload():
    """Precarica la prossima entry playlist in stato PAUSED per ridurre il gap."""
    if not playlist["items"]:
        return
    if playlist["index"] < 0:
        return
    # Calcola prossimo indice
    next_index = playlist["index"] + 1
    if next_index >= len(playlist["items"]):
        if playlist["loop"]:
            next_index = 0
        else:
            return
    next_path = playlist["items"][next_index]
    if preloaded["path"] == next_path:
        return  # già pronto
    # Libera eventuale preload precedente
    if preloaded["pipeline"]:
        try:
            preloaded["pipeline"].set_state(Gst.State.NULL)
        except Exception:
            pass
        preloaded.update({"path": None, "pipeline": None, "vb": None})
    try:
        print(f"[PRELOAD] Precarico: {next_path}", flush=True)
        p, vb, af = build_player_pipeline(next_path)
        # Stato PAUSED per preroll (veloce start successivo)
        p.set_state(Gst.State.PAUSED)
        preloaded.update({"path": next_path, "pipeline": p, "vb": vb, "alpha": af})
    except Exception as e:
        print(f"[PRELOAD] Errore preload {next_path}: {e}", flush=True)

# ---------- Fast-start endpoints ----------
@app.post("/faststart/prepare")
def api_faststart_prepare(
    path: Optional[str] = Body(None, embed=True),
    filename: Optional[str] = Body(None, embed=True),
):
    global VIDEO_PATH
    # Se la funzione è invocata internamente (non via HTTP), i default Body() possono arrivare come oggetti truthy
    # Normalizza a None quando non sono stringhe
    if not (isinstance(path, str) or path is None):
        path = None
    if not (isinstance(filename, str) or filename is None):
        filename = None
    # Risolve il path come in /play
    chosen = None
    if path and filename:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Usa solo uno tra 'path' e 'filename'"})
    if path:
        # Normalizza eventuale path Windows su macOS/Linux (backslash -> slash)
        if os.name != 'nt':
            path = path.replace('\\', '/')
        p = Path(path)
        if not p.exists():
            return JSONResponse(status_code=404, content={"ok": False, "error": f"File non trovato: {path}"})
        chosen = str(p)
    elif filename:
        # Normalizza filename: se è un path assoluto, estrai solo il basename
        # (può accadere quando il player remoto ritorna il suo path locale)
        # Se filename contiene un path Windows completo, estrai solo basename
        raw = filename
        if raw and (":" in raw or "\\" in raw or "/" in raw):
            # sostituisci backslash per compat e prendi solo name
            norm = raw.replace('\\', '/')
            try:
                fname = Path(norm).name
            except Exception:
                fname = norm.split('/')[-1]
        else:
            try:
                fname = Path(raw).name if raw else raw
            except Exception:
                fname = str(raw)
        # Rimuovi eventuali sequenze tipo C: prefix rimaste nel nome
        if fname and ':' in fname:
            # es: C:UsersriccaDesktopmediafoo.mp4 -> prendi segmenti finali
            parts = [seg for seg in fname.replace(':', '/').split('/') if seg]
            if parts:
                fname = parts[-1]
        p = MEDIA_DIR / fname
        if not p.exists():
            return JSONResponse(status_code=404, content={"ok": False, "error": f"File non trovato: {p}"})
        chosen = str(p)
    else:
        chosen = VIDEO_PATH
    # Overlay a nero
    try:
        if OVERLAY_ENABLED:
            overlay_show(alpha=1.0)
    except Exception as e:
        print(f"[FASTSTART] Overlay non disponibile: {e}", flush=True)
    if current_framework["name"] == "gst":
        try:
            prepare_pipeline_for(chosen)
            faststart["prepared_path"] = chosen
            try:
                gui_log(f"faststart_prepare GST", data={"path": chosen})
            except Exception:
                pass
            return {"ok": True, "prepared": chosen}
        except Exception as e:
            try:
                gui_log(f"faststart_prepare error", level="ERROR", data={"path": chosen, "error": str(e)})
            except Exception:
                pass
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    elif current_framework["name"] == "cvlc":
        try:
            ensure_backend()
            backend = current_framework.get("backend")
            assert backend is not None
            # Prepara media: play+pauza+seek 0 per mostrare primo frame
            backend.faststart_prepare(chosen)  # type: ignore[attr-defined]
            faststart["prepared_path"] = chosen
            try:
                gui_log("faststart_prepare CVLC", data={"path": chosen})
            except Exception:
                pass
            _fsm_set("preparing")
            return {"ok": True, "prepared": chosen}
        except Exception as e:
            try:
                gui_log("faststart_prepare error", level="ERROR", data={"path": chosen, "error": str(e)})
            except Exception:
                pass
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    elif current_framework["name"] == "mpv":
        try:
            ensure_backend()
            backend = current_framework.get("backend")
            assert backend is not None
            backend.faststart_prepare(chosen)  # type: ignore[attr-defined]
            faststart["prepared_path"] = chosen
            try:
                gui_log("faststart_prepare MPV", data={"path": chosen})
            except Exception:
                pass
            return {"ok": True, "prepared": chosen}
        except Exception as e:
            try:
                gui_log("faststart_prepare error", level="ERROR", data={"path": chosen, "error": str(e)})
            except Exception:
                pass
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    # Altri backend: best-effort
    faststart["prepared_path"] = chosen
    return {"ok": True, "prepared": chosen, "note": "preload best-effort"}

@app.post("/faststart/go")
def api_faststart_go(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    target = faststart.get("prepared_path") or VIDEO_PATH
    if not target:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Nessun media pronto"})
    def _go_now():
        # Dichiarazione globale all'inizio della funzione annidata per evitare SyntaxError
        global VIDEO_PATH
        if current_framework["name"] == "gst":
            VIDEO_PATH = target
            if not adopt_preloaded(target):
                # Fallback: start normal
                start_play(fade_in_seconds=0.0)
            # Fade-out overlay
            try:
                if OVERLAY_ENABLED:
                    overlay_fade_to(0.0, seconds)
                    def _hide():
                        try: overlay_hide()
                        except Exception: pass
                        return False
                    GLib.timeout_add(int(max(0.0, seconds) * 1000) + 50, _hide)
            except Exception as e:
                print(f"[FASTSTART] Overlay fade error: {e}", flush=True)
            # Assicura stato in PLAYING anche su GST
            try:
                if player.get("pipeline"):
                    try:
                        player["pipeline"].set_state(Gst.State.PLAYING)
                    except Exception:
                        pass
                player["state"] = "playing"
            except Exception:
                pass
            faststart["prepared_path"] = None
            try:
                gui_log("faststart_go GST", data={"seconds": seconds, "path": target})
                gui_log("playing", data={"path": target, "backend": "gst", "loop": player.get("loop")})
            except Exception:
                pass
            fsm_playing("faststart_go", action="faststart_go")
            _start_end_monitor()
            return {"ok": True}
        # cvlc/vlc/mpv
        try:
            ensure_backend()
            backend = current_framework.get("backend")
            if not backend:
                raise RuntimeError("Backend non disponibile")
            try:
                _restore_idle_black_cover(backend)
            except Exception:
                pass
            # 1) Prova ripresa veloce se già preparato, altrimenti play diretto
            resumed = False
            if current_framework["name"] in {"cvlc", "mpv"} and faststart.get("prepared_path"):
                try:
                    backend.faststart_go()  # type: ignore[attr-defined]
                    resumed = True
                except Exception:
                    resumed = False
            if not resumed:
                backend.play(target, loop=player.get("loop", False), fade_in=0.0)
            # 2) Verifica best-effort e fallback a resume/play se ancora in pausa
            try:
                playing = bool(getattr(backend, "is_playing", lambda: True)())
            except Exception:
                playing = True
            if not playing:
                try:
                    # tenta un resume esplicito (se supportato)
                    getattr(backend, "resume", lambda: None)()
                except Exception:
                    pass
                try:
                    playing = bool(getattr(backend, "is_playing", lambda: True)())
                except Exception:
                    playing = True
                if not playing:
                    # forza un play esplicito come ultima spiaggia
                    backend.play(target, loop=player.get("loop", False), fade_in=0.0)
            # Aggiorna stato locale e VIDEO_PATH
            try:
                VIDEO_PATH = target
                player["state"] = "playing"
            except Exception:
                pass
            # Overlay fade
            if OVERLAY_ENABLED:
                overlay_fade_to(0.0, seconds)
                def _hide():
                    try: overlay_hide()
                    except Exception: pass
                    return False
                GLib.timeout_add(int(max(0.0, seconds) * 1000) + 50, _hide)
            faststart["prepared_path"] = None
            try:
                gui_log("faststart_go %s" % current_framework["name"].upper(), data={"seconds": seconds, "path": target})
                gui_log("playing", data={"path": target, "backend": current_framework["name"], "loop": player.get("loop")})
            except Exception:
                pass
            fsm_playing("faststart_go", action="faststart_go")
            fsm_playing("faststart_go", action="faststart_go")
            return {"ok": True}
        except Exception as e:
            try:
                gui_log("faststart_go error", level="ERROR", data={"seconds": seconds, "path": target, "error": str(e)})
            except Exception:
                pass
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

    if in_time:
        try:
            t = float(in_time)
        except Exception:
            t = None
        if t:
            schedule_info["play_at"] = t if t >= 1e9 else (time.time() + max(0.0, t))
            def _clear_and_go():
                try: _go_now()
                finally:
                    schedule_info["play_at"] = None
                return False
            delay = schedule_action(in_time, _clear_and_go)
            return {"ok": True, "scheduled": True, "delay": delay}
    return _go_now()

# --- Backward-compatibility aliases (older GUI/clients) ---
@app.post("/faststart_prepare")
def api_faststart_prepare_legacy(
    path: Optional[str] = Body(None, embed=True),
    filename: Optional[str] = Body(None, embed=True),
):
    """Compat: alias di /faststart/prepare per client legacy."""
    return api_faststart_prepare(path=path, filename=filename)

# ---------- Update helpers ----------
def download_to(path: Path, url: str, report=None):
    req = Request(url, headers={"User-Agent": "headless-player"})

    def _is_win_lock_error(exc: BaseException) -> bool:
        try:
            winerr = getattr(exc, "winerror", None)
            if isinstance(winerr, int) and winerr in (32, 33):  # sharing violation / lock violation
                return True
        except Exception:
            pass
        s = str(exc).lower()
        return ("used by another process" in s) or ("being used by" in s) or ("permission denied" in s)

    def _safe_replace(src: Path, dst: Path, attempts: int = 25, delay: float = 0.2) -> None:
        last_exc: Exception | None = None
        for i in range(max(1, attempts)):
            try:
                os.replace(src, dst)
                return
            except PermissionError as e:
                last_exc = e
                if not _is_win_lock_error(e):
                    raise
            except OSError as e:
                last_exc = e
                if not _is_win_lock_error(e):
                    raise
            # Ritenta (Windows: file temporaneamente bloccato da AV/indicizzazione)
            time.sleep(delay)
        # Ultimo tentativo dopo chmod best-effort
        try:
            try:
                os.chmod(dst, 0o644)
            except Exception:
                pass
            os.replace(src, dst)
            return
        except Exception:
            if last_exc:
                raise last_exc
            raise

    tmp_path = path.with_suffix(path.suffix + ".part")
    with urlopen(req, timeout=30) as r, open(tmp_path, "wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        got = 0
        while True:
            chunk = r.read(8192)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if report:
                report(got, total)
    # Sostituzione atomica con retry per Windows
    _safe_replace(tmp_path, path)

def apply_update(archive: Path):
    """Applica un archivio aggiornamento con staging, backup e rollback best-effort.

    - Estrae in dir temporanea fuori da APP_DIR
    - Per ogni file, crea backup dell'originale se esiste, poi sostituisce in modo sicuro
    - In caso di eccezione a metà, ripristina da backup i file già sostituiti e rimuove i file nuovi
    - Mantiene la cartella backup per analisi successive e la espone in current_update["backup_dir"]
    """
    # Prima di procedere, se OFF-player è in esecuzione proviamo a fermarlo per liberare lock sui binari
    try:
        if off_proc.get("p") and off_proc["p"].poll() is None:
            log_update("Arresto OFF-player prima dell'apply non elevato…")
            _ = _off_stop()
            # attesa breve per rilascio file
            for _i in range(30):
                p = off_proc.get("p")
                if not p or p.poll() is not None:
                    break
                time.sleep(0.1)
            p = off_proc.get("p")
            if p and p.poll() is None:
                try:
                    p.kill()
                    log_update("OFF-player kill forzato (persistente dopo terminate)")
                except Exception:
                    pass
    except Exception as _e:
        log_update(f"Stop OFF-player pre-apply non elevato fallito (ignoro): {_e}")
    # Usa una dir temporanea fuori da APP_DIR per evitare problemi di permessi
    tmp = Path(tempfile.mkdtemp(prefix="release_tmp_", dir=str(_pick_update_dir())))

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            # Estrai selettivamente escludendo dotfiles e artefatti noti (.gitignore, .gitattributes, __MACOSX, .DS_Store)
            for zi in z.infolist():
                n = zi.filename.replace("\\", "/")
                bn = n.rsplit("/", 1)[-1]
                if not n:
                    continue
                # Esclusioni
                if "/__MACOSX" in n or n.startswith("__MACOSX/"):
                    continue
                if bn in {".gitignore", ".gitattributes", ".editorconfig", ".DS_Store", "Thumbs.db"}:
                    continue
                if bn.endswith((".pyc", ".pyo")):
                    continue
                try:
                    z.extract(zi, path=tmp)
                except Exception as ee:
                    # Best-effort: salta file problematico non critico
                    if bn.startswith("."):
                        continue
                    raise
    else:
        try:
            with tarfile.open(archive) as t:
                def _is_safe(member: tarfile.TarInfo) -> bool:
                    n = str(member.name)
                    bn = n.rsplit("/", 1)[-1]
                    if "/__MACOSX" in n or n.startswith("__MACOSX/"):
                        return False
                    if bn in {".gitignore", ".gitattributes", ".editorconfig", ".DS_Store", "Thumbs.db"}:
                        return False
                    if bn.endswith((".pyc", ".pyo")):
                        return False
                    return True
                safe_members = [m for m in t.getmembers() if _is_safe(m)]
                t.extractall(tmp, members=safe_members)
        except Exception:
            raise RuntimeError("Formato update non supportato (zip/tar.*)")

    # Se l'archivio contiene una sola directory di livello superiore, scendi di un livello
    src_root = tmp
    try:
        entries = [p for p in tmp.iterdir() if p.name not in {"__MACOSX", "._"}]
        if len(entries) == 1 and entries[0].is_dir():
            src_root = entries[0]
    except Exception:
        pass

    # Prepara backup root con timestamp
    backups_root = _updates_base_dir() / "backups"
    try:
        backups_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    backup_dir = backups_root / time.strftime("%Y%m%d-%H%M%S")
    changes: list[tuple[str, Path, Path | None]] = []  # (op, target_path, backup_path)

    def _restore_on_error():
        # ripristina al contrario
        for op, target, bkp in reversed(changes):
            try:
                if op == "replaced" and bkp and bkp.exists():
                    # rimuovi target e rimetti backup
                    try:
                        target.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(str(bkp), str(target))
                    except Exception:
                        pass
                elif op == "created":
                    try:
                        target.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass
            except Exception:
                pass

    try:
        for root, dirs, files in os.walk(src_root):
            rel = Path(root).relative_to(src_root)
            dest_dir = APP_DIR / rel
            dest_dir.mkdir(parents=True, exist_ok=True)
            for name in files:
                relpath = (rel / name).as_posix()
                # Esclusioni
                if relpath.startswith("headless_venv/") or relpath == "headless_venv":
                    continue
                if relpath.startswith("media/"):
                    continue
                # Skippa dotfiles e artefatti noti
                name_lc = name.lower()
                if name in {".gitignore", ".gitattributes", ".editorconfig", ".ds_store", "thumbs.db"}:
                    continue
                if name_lc.endswith((".pyc", ".pyo")):
                    continue
                src_file = Path(root) / name
                dst_file = dest_dir / name
                # Se esiste, sposta in backup prima di sostituire
                if dst_file.exists():
                    bkp_path = backup_dir / rel / name
                    try:
                        bkp_path.parent.mkdir(parents=True, exist_ok=True)
                        bkp_path.write_bytes(b"")  # crea placeholder per riservare il path
                        bkp_path.unlink(missing_ok=True)  # pulisci placeholder
                    except Exception:
                        pass
                    try:
                        # move atomico in backup (mantiene permessi/mtime)
                        os.makedirs(bkp_path.parent, exist_ok=True)
                        os.replace(str(dst_file), str(bkp_path))
                        changes.append(("replaced", dst_file, bkp_path))
                    except PermissionError as e:
                        raise PermissionError(f"Permesso negato su '{dst_file}'. Esegui /maintenance/fix_permissions e riprova.") from e
                    except Exception as e:
                        raise RuntimeError(f"Errore creando backup di '{dst_file}': {e}") from e
                else:
                    # segna creazione nuovo file per eventuale rollback
                    changes.append(("created", dst_file, None))
                # Copia sicura: in .part poi replace
                try:
                    tmp_dst = dst_file.with_suffix(dst_file.suffix + ".part~")
                    shutil.copy2(src_file, tmp_dst)
                    try:
                        os.replace(str(tmp_dst), str(dst_file))
                    except Exception:
                        # rimuovi tmp e propaga
                        try:
                            tmp_dst.unlink(missing_ok=True)  # type: ignore[arg-type]
                        except Exception:
                            pass
                        raise
                except PermissionError as e:
                    _restore_on_error()
                    raise PermissionError(f"Permesso negato su '{dst_file}'. Esegui /maintenance/fix_permissions e riprova.") from e
                except Exception as e:
                    _restore_on_error()
                    raise RuntimeError(f"Errore copiando '{src_file}' -> '{dst_file}': {e}") from e

        # Successo: mantieni backup per audit ed esponi path
        try:
            current_update["backup_dir"] = str(backup_dir)
            current_update["backed_up_files"] = len([1 for op, _, _ in changes if op == "replaced"])
        except Exception:
            pass
        # Pruning vecchi backup (mantieni 2)
        try:
            _prune_old_backups(keep=2)
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def _safe_unlink(path: Path, attempts: int = 20, delay: float = 0.2) -> None:
    """Rimuove un file con retry/backoff per gestire blocchi temporanei su Windows.
    Se non riesce, rinomina a .stale e prosegue.
    """
    if not path.exists():
        return
    last_exc: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            path.unlink()
            return
        except Exception as e:
            last_exc = e
            time.sleep(delay)
    # Fallback: rinomina e lascia il file per cleanup successivo
    try:
        stale = path.with_suffix(path.suffix + f".stale-{int(time.time())}")
        os.replace(str(path), str(stale))
    except Exception:
        if last_exc:
            raise last_exc
        raise

# ---------- FastAPI ----------
@app.on_event("startup")
def on_start():
    print("[STARTUP] Avvio applicazione", flush=True)
    loop = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
        except Exception:
            loop = None
    except Exception:
        loop = None
    if loop is not None:
        globals()["ASYNC_LOOP"] = loop
    def start_glib_loop():
        print("[STARTUP] Avvio main loop GLib", flush=True)
        threading.Thread(target=main_loop.run, daemon=True).start()
        time.sleep(0.5)
        print("[STARTUP] Main loop avviato (splash su richiesta)", flush=True)
    threading.Thread(target=start_glib_loop, daemon=True).start()
    # Avvio UDP (sempre attivo)
    threading.Thread(target=_udp_thread, daemon=True).start()
    if autoplay.get("enabled"):
        GLib.idle_add(lambda: (_autoplay_schedule_initial(), False)[1])
    else:
        def _manual_bootstrap():
            _autoplay_prepare_manual_bootstrap()
            return False
        GLib.timeout_add(250, _manual_bootstrap)
    # Overlay: auto-probe plane al boot (best-effort) se abilitato e non configurato
    def _overlay_boot_probe():
        try:
            if OVERLAY_ENABLED:
                # Prova auto-probe (best-effort) e attiva comunque overlay nero stabile
                if OVERLAY_USE_KMS and int(OVERLAY_KMS_PLANE_ID) <= 0:
                    print("[STARTUP] Overlay abilitato ma plane non impostato: eseguo auto-probe…", flush=True)
                    res = overlay_probe(auto_persist=True)
                    print(f"[STARTUP] Overlay probe: {res}", flush=True)
                try:
                    overlay_show(alpha=1.0)
                except Exception as e:
                    print(f"[STARTUP] Overlay show fallito: {e}", flush=True)
            if OVERLAY_ENABLED:
                # Prova auto-probe (best-effort) e attiva comunque overlay nero stabile
                if OVERLAY_USE_KMS and int(OVERLAY_KMS_PLANE_ID) <= 0:
                    print("[STARTUP] Overlay abilitato ma plane non impostato: eseguo auto-probe…", flush=True)
                    res = overlay_probe(auto_persist=True)
                    print(f"[STARTUP] Overlay probe: {res}", flush=True)
                try:
                    overlay_show(alpha=1.0)
                except Exception as e:
                    print(f"[STARTUP] Overlay show fallito: {e}", flush=True)
        except Exception as e:
            print(f"[STARTUP] Overlay probe errore: {e}", flush=True)
    pass  # overlay boot probe removed
    # OFF-player autostart su macOS (o se OFF_AUTOSTART abilitato) quando il backend attivo è "off"
    def _off_autostart():
        try:
            if globals().get("OFF_AUTOSTART") and current_framework.get("name") == "off":
                print("[STARTUP] OFF autostart attivo: avvio OFF-player…", flush=True)
                res = _off_start()
                print(f"[STARTUP] OFF autostart esito: {res}", flush=True)
                if res.get("ok") and res.get("running"):
                    ready = _wait_off_http_ready(res.get("port"), timeout=6.0, interval=0.25)
                    if ready:
                        print("[STARTUP] OFF-player risponde all'HTTP", flush=True)
                    else:
                        print("[STARTUP] OFF-player non risponde ancora all'HTTP (timeout)", flush=True)
        except Exception as e:
            print(f"[STARTUP] OFF autostart errore: {e}", flush=True)
    threading.Thread(target=_off_autostart, daemon=True).start()

@app.get("/healthz")
def healthz():
    return {"status": "ok", "state": player["state"], "version": VERSION, "ip": get_ip()}

@app.get("/status")
def status():
    # Esponi stato player, media corrente e schedulazioni note
    current_name = None
    try:
        if VIDEO_PATH:
            current_name = Path(VIDEO_PATH).name
    except Exception:
        current_name = None
    # Rileva OS semplificato (windows|linux|darwin|raspberrypi)
    try:
        sysname = platform.system().lower()
    except Exception:
        sysname = "unknown"
    simple_os = sysname
    if sysname.startswith("linux"):
        try:
            model_path = Path("/sys/firmware/devicetree/base/model")
            if model_path.is_file():
                model_txt = model_path.read_text(errors="ignore").lower()
                if "raspberry" in model_txt:
                    simple_os = "raspberrypi"
        except Exception:
            pass
    # Derive brightness for UI if available (0..1)
    br_val = None
    try:
        # Prefer last explicit request
        br_val = float(globals().get("_LAST_VISUAL_BRIGHTNESS")) if globals().get("_LAST_VISUAL_BRIGHTNESS") is not None else None
    except Exception:
        br_val = None
    if br_val is None:
        # If overlay is active, invert alpha to approximate brightness
        try:
            if globals().get("overlay") and overlay.get("active"):
                br_val = max(0.0, min(1.0, 1.0 - float(overlay.get("current_alpha") or 0.0)))
        except Exception:
            pass
    if br_val is None and current_framework["name"] == "gst":
        # Map vb brightness -1..1 -> 0..1
        try:
            vb = player.get("vb")
            if vb:
                b = float(vb.get_property("brightness"))
                br_val = max(0.0, min(1.0, (b + 1.0) / 2.0))
        except Exception:
            pass
    media_available = False
    media_count = 0
    try:
        files = [
            p
            for p in MEDIA_DIR.iterdir()
            if p.is_file() and p.name not in {".DS_Store", "_sentinel_desktop.txt"}
        ]
        media_count = len(files)
        media_available = media_count > 0
    except Exception:
        pass

    resp = {
        "version_current": VERSION,
        "version_available": current_update["available"],
        "player_state": player["state"],
        # Unique identifiers
        "device_id": _ensure_device_id(),
        "instance_id": INSTANCE_ID,
        "loop_enabled": bool(player.get("loop")),
        "fsm_state": fsm.get("state", "unknown"),
        "fsm_action": fsm.get("action"),
        "fsm": {
            "state": fsm.get("state"),
            "previous": fsm.get("previous"),
            "last_change": fsm.get("last_change"),
            "reason": fsm.get("reason"),
            "action": fsm.get("action"),
            "error": fsm.get("error"),
        },
        "splash_active": splash["active"],
        "device_name": DEVICE_NAME,
        "name": DEVICE_NAME,
        "current_media": current_name,
        "media_available": media_available,
        "media_count": media_count,
    "update_status": current_update["status"],
        "autoplay_enabled": autoplay.get("enabled", False),
        "update_progress": current_update.get("progress"),
        "update_error": current_update.get("error"),
    "update_error_type": current_update.get("error_type"),
        "update_bytes_done": current_update.get("bytes_done"),
        "update_bytes_total": current_update.get("bytes_total"),
    "last_apply_http": current_update.get("last_apply_http"),
        "last_logs": current_update["log"][-10:],
    "update_stage": current_update.get("stage"),
    "update_plan": current_update.get("plan"),
    "update_assets": current_update.get("assets"),
        "update_metrics": {
            "started_at": current_update.get("started_at"),
            "download_started_at": current_update.get("download_started_at"),
            "download_ended_at": current_update.get("download_ended_at"),
            "applying_started_at": current_update.get("applying_started_at"),
            "completed_at": current_update.get("completed_at"),
        },
        "update_backup_dir": current_update.get("backup_dir"),
        "update_backed_up_files": current_update.get("backed_up_files"),
        "update_elevated": current_update.get("elevated"),
        "maintenance_status": maintenance["status"],
        "maintenance_last_logs": maintenance["log"][-10:],
        "faststart": {
            "prepared": bool(faststart.get("prepared_path")),
            "path": faststart.get("prepared_path"),
        },
        "scheduled": {
            "play_at": schedule_info.get("play_at"),
        },
        "log_udp": {
            "enabled": LOG_UDP_ENABLED,
            "host": LOG_UDP_HOST,
            "port": LOG_UDP_PORT,
        },
        "hud": {
            "mode": int(globals().get("HUD_MODE", 0) or 0),
            "visible": bool(int(globals().get("HUD_MODE", 0) or 0) > 0),
            "supported": False,
        },
        "timing": {
            # Default timing fields; GUI may inject measured skew from its local sync
            "synced": False,
            "method": None,
            "offset_ms": 0,   # legacy name retained for compatibility
            "skew_ms": 0,     # preferred name for UI/clients
        },
        "show_ready": bool(show_state.get("ready")),
        "ready_for": show_state.get("for"),
        "playlist_loop": bool(playlist.get("loop", False)),
        "os": simple_os,
        "arch": platform.machine(),
    }

    try:
        resp["display_mode"] = get_display_mode()
    except Exception:
        resp["display_mode"] = None

    try:
        resp["framework"] = current_framework.get("name")
    except Exception:
        resp["framework"] = None

    # Se il backend attivo è OFF, includi lo stato remoto di OFF per uniformare la GUI
    try:
        if resp.get("framework") == "off":
            # Riusa l'handler esistente per evitare codice duplicato
            off = off_status()
            resp["off"] = off
            try:
                hud_block = resp.get("hud") or {}
                hud_block["supported"] = True
                resp["hud"] = hud_block
            except Exception:
                pass
            # Se disponibile, esponi alcuni campi utili in chiaro
            player_info = (off or {}).get("player") or {}
            if isinstance(player_info, dict):
                try:
                    off_hud = player_info.get("hud")
                    if isinstance(off_hud, dict):
                        hud_block = resp.get("hud") or {}
                        mode_val = off_hud.get("mode")
                        mode_int: int
                        if isinstance(mode_val, (int, float)):
                            mode_int = int(mode_val)
                        elif "visible" in off_hud:
                            mode_int = 2 if bool(off_hud.get("visible")) else 0
                        else:
                            mode_int = int(hud_block.get("mode", globals().get("HUD_MODE", 0)) or 0)
                        if mode_int < 0:
                            mode_int = 0
                        if mode_int > 2:
                            mode_int = 2
                        hud_block["mode"] = mode_int
                        hud_block["visible"] = bool(mode_int > 0)
                        try:
                            globals()["HUD_MODE"] = mode_int
                        except Exception:
                            pass
                        hud_block["supported"] = True
                        resp["hud"] = hud_block
                except Exception:
                    pass
                # OFF ritorna "playing" (bool), "file" (string), "loop" (bool)
                is_playing = player_info.get("playing")
                resp["off_playing"] = is_playing
                resp["off_current_media"] = player_info.get("file")
                resp["off_loop"] = player_info.get("loop")
                # Aggiorna anche player_state per compatibilità con la GUI
                if is_playing:
                    resp["player_state"] = "playing"
                else:
                    resp["player_state"] = "idle"
                # Aggiorna current_media con il file corrente di OFF
                if player_info.get("file"):
                    resp["current_media"] = player_info.get("file")
                try:
                    disp_raw = player_info.get("display")
                    disp_norm = _build_display_mode(disp_raw, source_fallback="off.status", refreshed_at=time.time())
                    if disp_norm:
                        disp_with_age = _display_mode_with_age(disp_norm)
                        player_info["display"] = disp_with_age
                        if not resp.get("display_mode"):
                            resp["display_mode"] = disp_with_age
                except Exception:
                    pass
    except Exception:
        pass

    if br_val is not None:
        try:
            resp["brightness"] = float(br_val)
        except Exception:
            pass
    return resp

@app.get("/components")
def api_components():
    """Esponi le versioni/hash dei componenti locali utili all'update orchestrato."""
    comps: dict[str, Any] = {}
    # headless_player
    comps["headless_player"] = {
        "version": VERSION,
        "path": str(APP_DIR),
    }
    # off_player (best-effort)
    try:
        off_path = APP_DIR.parent / "OFF-player" / "bin" / ("OFF-player.exe" if os.name == "nt" else "OFF-player")
        if off_path.exists():
            info = {"path": str(off_path), "size": off_path.stat().st_size}
            try:
                info["sha256"] = _sha256_of(off_path)
            except Exception:
                pass
            comps["off_player"] = info
    except Exception:
        pass
    # provisioning script (Windows)
    try:
        prov = APP_DIR.parent / "tools" / "windows" / "provision_player.ps1"
        if prov.exists():
            ver = None
            try:
                head = prov.read_text(encoding="utf-8", errors="ignore").splitlines()[:5]
                for line in head:
                    m = re.search(r"version\s*:\s*([\w\.-]+)", line, re.IGNORECASE)
                    if m:
                        ver = m.group(1)
                        break
            except Exception:
                pass
            comps["provision_script"] = {
                "version": ver,
                "sha256": _sha256_of(prov),
                "size": prov.stat().st_size,
                "path": str(prov),
            }
    except Exception:
        pass
    return {"ok": True, "components": comps}

@app.post("/update/plan")
def api_update_plan(assets: list[dict] = Body(..., embed=True)):
    """Riceve un piano di asset da scaricare/applicare. Ogni asset: {id,url,sha256,size,type,os?,arch?}."""
    if not isinstance(assets, list) or not assets:
        return JSONResponse(status_code=400, content={"ok": False, "error": "assets mancante"})
    # Validazione minima
    plan = []
    for a in assets:
        if not isinstance(a, dict):
            continue
        if not a.get("id") or not a.get("url"):
            continue
        plan.append({
            "id": a.get("id"),
            "url": a.get("url"),
            "sha256": a.get("sha256"),
            "size": a.get("size", 0),
            "type": (a.get("type") or "auto"),
            "os": a.get("os"),
            "arch": a.get("arch"),
        })
    if not plan:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Nessun asset valido"})
    current_update.update({
        "status": "planned",
        "stage": "planned",
        "plan": plan,
        "assets": [],
        "bytes_done": 0,
        "bytes_total": sum((a.get("size") or 0) for a in plan),
        "progress": 0,
        "error": None,
    })
    log_update(f"Piano aggiornamento ricevuto: {len(plan)} asset")
    return {"ok": True, "planned": plan}

def _download_with_progress(url: str, dest: Path, on_progress=None):
    req = Request(url, headers={"User-Agent": "Maroccos-Updater/1.0"})
    with urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length", "0") or 0)
        done = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    return dest

@app.post("/update/fetch")
def api_update_fetch(start_immediately: bool = Body(True, embed=True)):
    plan = list(current_update.get("plan") or [])
    if not plan:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Nessun piano definito"})
    if current_update.get("stage") in {"downloading","applying"}:
        return JSONResponse(status_code=409, content={"ok": False, "error": "Update già in corso"})

    base = _updates_base_dir() / time.strftime("%Y%m%d-%H%M%S")
    base.mkdir(parents=True, exist_ok=True)
    _prune_old_updates()

    def worker():
        try:
            t0 = time.time()
            current_update["started_at"] = t0
            current_update["stage"] = "downloading"
            current_update["status"] = "downloading"
            current_update["assets"] = []
            current_update["bytes_done"] = 0
            total = int(current_update.get("bytes_total") or 0)
            current_update["download_started_at"] = time.time()
            cumulative_done = 0
            for i, a in enumerate(plan):
                aid = a.get("id")
                url = a.get("url")
                atype = (a.get("type") or "auto").lower()
                sanitized_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(aid)) if aid else f"asset_{i}"
                suffix = ""
                try:
                    parsed = urlparse(url or "")
                    hint_name = Path(unquote(parsed.path or "")).name
                    if hint_name:
                        suffix = "".join(Path(hint_name).suffixes)
                except Exception:
                    suffix = ""
                if not suffix:
                    if atype in {"zip"}:
                        suffix = ".zip"
                    elif atype in {"tar", "tar.gz", "tgz"}:
                        suffix = ".tar.gz" if atype != "tgz" else ".tgz"
                    elif atype in {"tar.bz2", "tbz", "tbz2"}:
                        suffix = ".tar.bz2" if atype == "tar.bz2" else ".tbz2"
                dest_name = f"asset_{i}_{sanitized_id}{suffix}"
                dest = base / dest_name
                log_update(f"Scarico asset {i+1}/{len(plan)}: {aid} -> {url}")
                asset_start = cumulative_done
                a_dl_t0 = time.time()

                def onp(d, t):
                    # d = bytes scaricati di questo asset; t = size dichiarata (può essere 0 se sconosciuto)
                    current_update["bytes_done"] = asset_start + d
                    if total > 0:
                        current_update["progress"] = round((current_update["bytes_done"] / total) * 100, 2)
                try:
                    _download_with_progress(url, dest, on_progress=onp)
                except Exception as de:
                    current_update.update({"status": "error", "stage": "error", "error": str(de)})
                    log_update(f"Download fallito: {aid} -> {de}")
                    return
                # verifica integrità
                if a.get("sha256"):
                    try:
                        digest = _sha256_of(dest)
                        if digest.lower() != str(a.get("sha256")).lower():
                            current_update.update({"status": "error", "stage": "error", "error": f"sha256 mismatch {aid}"})
                            log_update(f"SHA256 mismatch {aid}: atteso {a.get('sha256')} got {digest}")
                            return
                    except Exception as he:
                        current_update.update({"status": "error", "stage": "error", "error": str(he)})
                        log_update(f"SHA256 errore per {aid}: {he}")
                        return
                # registrazione asset
                a_dl_t1 = time.time()
                try:
                    real_size = dest.stat().st_size
                except Exception:
                    real_size = int(a.get("size") or 0)
                asset_rec = {
                    "id": aid,
                    "path": str(dest),
                    "type": atype,
                    "size": real_size,
                    "download_started_at": a_dl_t0,
                    "download_ended_at": a_dl_t1,
                    "download_duration_s": round(max(0.0, a_dl_t1 - a_dl_t0), 3),
                }
                try:
                    asset_rec["sha256"] = _sha256_of(dest)
                except Exception:
                    pass
                # speed approssimata
                try:
                    dur = max(0.001, a_dl_t1 - a_dl_t0)
                    asset_rec["download_speed_bps"] = int(real_size / dur)
                except Exception:
                    pass
                current_update["assets"].append(asset_rec)
                # aggiorna cumulativo (usa size dichiarata o dimensione effettiva)
                try:
                    sz_decl = int(a.get("size") or 0)
                    if sz_decl > 0:
                        cumulative_done += sz_decl
                    else:
                        cumulative_done += dest.stat().st_size
                except Exception:
                    try:
                        cumulative_done += dest.stat().st_size
                    except Exception:
                        pass
            # staging
            current_update["download_ended_at"] = time.time()
            current_update["stage"] = "staging"
            log_update("Download completati, avvio apply…")
            # APPLY
            current_update["applying_started_at"] = time.time()
            current_update["stage"] = "applying"
            current_update["status"] = "applying"
            # Give observers a chance to observe the transition even on fast paths
            try:
                time.sleep(0.05)
            except Exception:
                pass
            # Ordine: per default come in plan
            for a in current_update["assets"]:
                apath = Path(a["path"])
                atype = a.get("type")
                if os.name == "nt" and atype in {"exe", "msi"} or apath.suffix.lower() in {".exe", ".msi"}:
                    # usa handler installatore windows
                    current_update["pkg_path"] = str(apath)
                    res = update_apply(restart=False)
                    if isinstance(res, JSONResponse) and res.status_code != 200:
                        return
                else:
                    # zip/tar apply (con percorso elevato su Windows se necessario)
                    if os.name == "nt" and (apath.suffix.lower() == ".zip" or (atype or "").lower() == "zip"):
                        res = _handle_elevated_zip_apply(apath, restart=True)
                        if not res.get("ok"):
                            current_update.update({"status": "error", "stage": "error", "error": f"elevated_apply_failed: {res.get('error')}"})
                            log_update(f"Apply elevato fallito {a['id']}: {res.get('error')}")
                            return
                        return
                    else:
                        try:
                            apply_update(apath)
                        except Exception as e:
                            current_update.update({"status": "error", "stage": "error", "error": str(e)})
                            log_update(f"Apply asset fallito {a['id']}: {e}")
                            return
            # fine
            current_update.update({"status": "ok", "stage": "ok", "completed_at": time.time()})
            log_update("Aggiornamento completato")
        finally:
            pass

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "started": True}

@app.post("/hide_splash")
def api_hide_splash():
    if splash["active"]:
        hide_splash()
        # Se non sta suonando nulla, mostra immagine nera di idle
        try:
            if player.get("state") in {"stopped", "idle", "unknown", None}:
                show_idle_black()
        except Exception:
            pass
        return {"ok": True, "message": "Splash nascosto"}
    else:
        return {"ok": True, "message": "Splash già nascosto"}

@app.post("/show_splash")
def api_show_splash():
    try:
        if not splash.get("active"):
            show_splash_until_play()
        return {"ok": True, "message": "Splash mostrato"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

# ---------- Device name ----------
@app.get("/device/name")
def api_get_device_name():
    return {"ok": True, "name": DEVICE_NAME}

@app.post("/device/name")
def api_set_device_name(name: Optional[str] = Body(None, embed=True)):
    global DEVICE_NAME
    if not name:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Campo 'name' richiesto"})
    DEVICE_NAME = str(name)
    persist_settings({"device_name": DEVICE_NAME})
    return {"ok": True, "name": DEVICE_NAME}

# ---------- UDP Log subscribe (REST) ----------
@app.get("/log/udp/status")
def api_log_udp_status():
    return {"ok": True, "enabled": LOG_UDP_ENABLED, "host": LOG_UDP_HOST, "port": LOG_UDP_PORT}

@app.post("/log/udp/subscribe")
def api_log_udp_subscribe(host: Optional[str] = Body(None, embed=True), port: Optional[int] = Body(None, embed=True), persist: bool = Body(False, embed=True)):
    if not host:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Campo 'host' richiesto"})
    try:
        p = int(port) if port is not None else LOG_UDP_PORT
    except Exception:
        p = LOG_UDP_PORT
    set_log_udp_target(host, p, enabled=True, persist=persist)
    try:
        gui_log("log_subscribe", data={"host": host, "port": p})
    except Exception:
        pass
    return {"ok": True, "enabled": LOG_UDP_ENABLED, "host": LOG_UDP_HOST, "port": LOG_UDP_PORT}

@app.post("/log/udp/unsubscribe")
def api_log_udp_unsubscribe(persist: bool = Body(False, embed=True)):
    set_log_udp_target("", LOG_UDP_PORT, enabled=False, persist=persist)
    return {"ok": True, "enabled": LOG_UDP_ENABLED}

_WINDOWS_SHUTDOWN_CMDS = [
    ["shutdown", "/s", "/t", "0"],
    ["powershell", "-NoProfile", "-Command", "Stop-Computer"],
]
_UNIX_SHUTDOWN_CMDS = [
    ["sudo", "-n", "loginctl", "poweroff"],
    ["sudo", "-n", "systemctl", "poweroff"],
    ["sudo", "-n", "shutdown", "-h", "now"],
    ["sudo", "-n", "poweroff"],
    ["loginctl", "poweroff"],
    ["systemctl", "poweroff"],
    ["shutdown", "-h", "now"],
    ["poweroff"],
]
_WINDOWS_REBOOT_CMDS = [
    ["shutdown", "/r", "/t", "0"],
    ["powershell", "-NoProfile", "-Command", "Restart-Computer"],
]
_UNIX_REBOOT_CMDS = [
    ["sudo", "-n", "loginctl", "reboot"],
    ["sudo", "-n", "systemctl", "reboot"],
    ["sudo", "-n", "reboot"],
    ["loginctl", "reboot"],
    ["systemctl", "reboot"],
    ["reboot"],
]

def _iter_shutdown_cmds() -> list[list[str]]:
    if _is_windows:
        return list(_WINDOWS_SHUTDOWN_CMDS + _UNIX_SHUTDOWN_CMDS)
    return list(_UNIX_SHUTDOWN_CMDS + _WINDOWS_SHUTDOWN_CMDS)

def _iter_reboot_cmds() -> list[list[str]]:
    if _is_windows:
        return list(_WINDOWS_REBOOT_CMDS + _UNIX_REBOOT_CMDS)
    return list(_UNIX_REBOOT_CMDS + _WINDOWS_REBOOT_CMDS)

def _build_magic_packet(mac: str) -> bytes:
    clean = mac.replace(":", "")
    if len(clean) != 12:
        raise ValueError(f"invalid MAC format: {mac}")
    raw = bytes.fromhex(clean)
    return b"\xFF" * 6 + raw * 16

def _send_magic_packets(macs: list[str], broadcast: str, port: int) -> dict:
    report = {"sent": [], "errors": [], "broadcast": broadcast, "port": port}
    if not macs:
        report["errors"].append({"error": "No startup MACs configured"})
        return report
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)
        for mac in macs:
            try:
                packet = _build_magic_packet(mac)
            except Exception as exc:
                report["errors"].append({"mac": mac, "error": str(exc)})
                continue
            try:
                sock.sendto(packet, (broadcast, port))
            except Exception as exc:
                report["errors"].append({"mac": mac, "error": str(exc)})
            else:
                report["sent"].append(mac)
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return report

def _trigger_startup(macs: Any | None = None, broadcast: Optional[str] = None, port: Optional[int] = None) -> dict:
    targets = _resolve_startup_targets(macs)
    if not targets:
        msg = "Nessun MAC di startup disponibile"
        print(f"[STARTUP] {msg}", flush=True)
        return {"ok": False, "macs": [], "sent": [], "errors": [{"error": msg}], "broadcast": broadcast or STARTUP_BROADCAST, "port": port or STARTUP_PORT}
    bcast = broadcast or STARTUP_BROADCAST
    try:
        port_value = int(port) if port is not None else STARTUP_PORT
    except Exception:
        port_value = STARTUP_PORT
    print(f"[STARTUP] Wake-on-LAN verso {targets} tramite {bcast}:{port_value}", flush=True)
    report = _send_magic_packets(targets, bcast, port_value)
    report.update({"macs": targets, "ok": bool(report["sent"])})
    return report

@app.post("/shutdown")
def api_shutdown():
    """Richiede lo spegnimento del sistema. Non riavvia il solo servizio."""
    print("[SHUTDOWN] Richiesta shutdown di sistema", flush=True)

    result = {"ok": False, "message": "", "attempts": []}

    def worker():
        for cmd in _iter_shutdown_cmds():
            try:
                print(f"[SHUTDOWN] Eseguo: {' '.join(cmd)}", flush=True)
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, text=True)
                print("[SHUTDOWN] Comando inviato con successo", flush=True)
                # Se il sistema accetta il comando, chiudiamo l'event loop per una terminazione pulita
                GLib.idle_add(main_loop.quit)
                result.update({"ok": True, "message": "Shutdown in corso", "attempts": []})
                return
            except subprocess.CalledProcessError as exc:
                msg = (exc.stderr or exc.stdout or "").strip()
                print(f"[SHUTDOWN] Comando fallito ({' '.join(cmd)}): {msg}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": msg})
            except subprocess.TimeoutExpired:
                print(f"[SHUTDOWN] Timeout eseguendo {' '.join(cmd)}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": "timeout"})
            except FileNotFoundError:
                print(f"[SHUTDOWN] Comando non trovato: {cmd[0]}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": "not_found"})
            except Exception as exc:
                print(f"[SHUTDOWN] Errore imprevisto con {' '.join(cmd)}: {exc}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": str(exc)})
        result.update({"ok": False, "message": "Nessun comando shutdown accettato"})

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    # Risposta immediata 202; l'handler chiamante può attendersi la disconnessione
    return JSONResponse(status_code=202, content={"ok": True, "message": "Shutdown richiesto"})


@app.post("/system/startup")
def system_startup(
    macs: Optional[list[str]] = Body(None, embed=True),
    broadcast: Optional[str] = Body(None, embed=True),
    port: Optional[int] = Body(None, embed=True),
):
    """Invia magic packet ai player noti oppure alla lista mac specificata."""
    result = _trigger_startup(macs=macs, broadcast=broadcast, port=port)
    return result



@app.post("/play")
def api_play(
    path: Optional[str] = Body(None, embed=True),
    filename: Optional[str] = Body(None, embed=True),
    loop: Optional[bool] = Body(None, embed=True),
    hide_splash_first: bool = Body(True, embed=True),
    fade_in_seconds: float = Body(0.5, embed=True),
    fade_out_seconds: Optional[float] = Body(None, embed=True),
    in_time: Optional[float] = Body(None, embed=True),
    # Fallback da query string (se body vuoto)
    path_q: Optional[str] = Query(None),
    filename_q: Optional[str] = Query(None),
    loop_q: Optional[int] = Query(None),
):
    """Avvia la riproduzione.
    Parametri accettati (JSON body):
      - path: percorso assoluto (deve stare dentro la media dir o essere file leggibile)
      - filename: nome di un file dentro media/ (esclusivo con path)
      - loop: abilita/disabilita loop (facoltativo)
      - hide_splash_first: default True, nasconde lo splash se attivo
    Precedenza: path > filename > default (VIDEO_PATH attuale).
    """
    global VIDEO_PATH

    chosen = None
    # Se non arrivano dal body, usa i parametri query
    path = path or path_q
    filename = filename or filename_q
    if loop is None and loop_q is not None:
        loop = bool(loop_q)
    if path and filename:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Usa solo uno tra 'path' e 'filename'"})

    if path:
        p = Path(path)
        if not p.exists():
            return JSONResponse(status_code=404, content={"ok": False, "error": f"File non trovato: {path}"})
        # Non forzare che sia in media, ma avvisa se fuori
        if not str(p).startswith(str(MEDIA_DIR)):
            print(f"[PLAY] WARNING: file fuori da media/: {p}", flush=True)
        chosen = str(p)
    elif filename:
        p = MEDIA_DIR / filename
        if not p.exists():
            # Fallback intelligente: se filename non esiste (es. vecchio default clip.mp4), prova a scansionare media/
            print(f"[PLAY] '{filename}' non trovato in media/. Provo fallback su altri file presenti…", flush=True)
            items = []
            for fp in sorted(MEDIA_DIR.iterdir()):
                if fp.is_file() and validate_media_file(fp):
                    items.append(str(fp))
            if items:
                playlist["items"] = items
                playlist["index"] = 0
                chosen = items[0]
                print(f"[PLAY] Fallback: avvio {chosen}", flush=True)
            else:
                return JSONResponse(status_code=404, content={"ok": False, "error": f"File non trovato in media/: {filename}"})
        else:
            chosen = str(p)
    else:
        # fallback: playlist se esiste; altrimenti scansione rapida di media/
        if playlist["items"]:
            if 0 <= playlist["index"] < len(playlist["items"]):
                chosen = playlist["items"][playlist["index"]]
            else:
                playlist["index"] = 0
                chosen = playlist["items"][0]
        else:
            items = []
            for p in sorted(MEDIA_DIR.iterdir()):
                if p.is_file() and validate_media_file(p):
                    items.append(str(p))
            if items:
                playlist["items"] = items
                playlist["index"] = 0
                chosen = items[0]
            elif Path(VIDEO_PATH).exists():
                chosen = VIDEO_PATH
            else:
                return JSONResponse(status_code=404, content={"ok": False, "error": "Nessun file in media/: carica un video o specifica filename"})

    if chosen:
        # Stop pipeline corrente se cambia file
        if chosen != VIDEO_PATH:
            stop_play()
        VIDEO_PATH = chosen
        # Se il file è in playlist, aggiorna l'indice corrente alla sua posizione
        try:
            if playlist["items"]:
                if chosen in playlist["items"]:
                    playlist["index"] = playlist["items"].index(chosen)
                else:
                    # se non è presente, resetta index coerentemente
                    playlist["index"] = -1
        except Exception:
            pass

    # Non toccare qui lo splash o la pipeline: ci pensa start_play()

    if loop is None and not autoplay.get("enabled"):
        # In modalità manuale riespetta il default di riprodurre un singolo media in loop
        loop = True
    if loop is not None:
        set_loop(loop)
    def start_logic():
        # Se backend non gst delega
        if current_framework["name"] != "gst":
            # Se il framework è OFF, assicurati che il processo OFF-player sia in esecuzione
            try:
                if current_framework.get("name") == "off":
                    st = off_status()
                    running = bool((st or {}).get("running"))
                    if not running:
                        print("[PLAY] OFF-player non in esecuzione, avvio...", flush=True)
                        # Avvia OFF-player e attendi brevemente che l'HTTP risponda
                        res = _off_start()
                        if not res.get("ok"):
                            error_msg = res.get("error", "OFF-player non avviabile")
                            print(f"[PLAY] Errore avvio OFF-player: {error_msg}", flush=True)
                            return {"ok": False, "error": error_msg}
                        print(f"[PLAY] OFF-player avviato: pid={res.get('pid')}, port={res.get('port')}", flush=True)
                        ready = _wait_off_http_ready(res.get("port"), timeout=6.0, interval=0.25)
                        if ready:
                            print("[PLAY] OFF-player HTTP pronto", flush=True)
                        else:
                            print("[PLAY] OFF-player HTTP non pronto entro timeout", flush=True)
                    else:
                        print(f"[PLAY] OFF-player già in esecuzione (pid={st.get('pid')})", flush=True)
            except Exception as e:
                print(f"[PLAY] Errore controllo OFF-player: {e}", flush=True)
            ensure_backend()
            backend = current_framework.get("backend")
            if backend:
                try:
                    backend.play(VIDEO_PATH, loop if loop is not None else player.get("loop"), fade_in_seconds)
                except Exception as e:
                    return {"ok": False, "error": str(e)}
                try:
                    gui_log("play", data={"path": VIDEO_PATH, "backend": current_framework["name"], "loop": player.get("loop")})
                except Exception:
                    pass
                return {"ok": True, "playing": VIDEO_PATH, "backend": current_framework["name"], "scheduled": False}
        # GStreamer path
        start_play(fade_in_seconds)
        try:
            gui_log("play", data={"path": VIDEO_PATH, "backend": current_framework["name"], "loop": player.get("loop")})
        except Exception:
            pass
    # Nota: non restituire qui; consenti al ramo di scheduling/fade-out di eseguire.

    # Fade-out + scheduling: se in_time futuro, pianifica fade-out prima
    if in_time:
        try:
            target = float(in_time)
        except Exception:
            target = None
        if target:
            now = time.time()
            # calcola delay complessivo
            if target >= 1e9:
                delay_total = max(0.0, target - now)
            else:
                delay_total = max(0.0, target)
            # se fade_out richiesto, anticipa
            if fade_out_seconds and fade_out_seconds > 0 and player.get("pipeline"):
                fade_start_delay = max(0.0, delay_total - fade_out_seconds)
                # programma fade_start_delay -> fade out, completato esegue start logic alla deadline
                def fade_then_schedule_start():
                    def after_fade():
                        # avvia reale all'orario (se c'è residuo di tempo lo aspetta)
                        residual = max(0.0, delay_total - fade_out_seconds - (time.time() - now))
                        def _clear_and_start():
                            try:
                                start_logic()
                            finally:
                                schedule_info["play_at"] = None
                            return False
                        if residual > 0:
                            GLib.timeout_add(int(residual * 1000), _clear_and_start)
                        else:
                            _clear_and_start()
                    if player.get("alpha"):
                        fade_opacity_to(0.0, fade_out_seconds, on_complete=after_fade)
                    elif player.get("vb"):
                        fade_to(-1.0, fade_out_seconds, on_complete=after_fade)
                    else:
                        after_fade()
                # Registra il play_at pianificato per rendere l'operazione idempotente lato GUI
                try:
                    schedule_info["play_at"] = target if target >= 1e9 else (time.time() + delay_total)
                except Exception:
                    pass
                GLib.timeout_add(int(fade_start_delay * 1000), lambda: (fade_then_schedule_start(), False)[1])
                try:
                    gui_log("play_scheduled", data={"path": VIDEO_PATH, "backend": current_framework["name"], "at": target, "fade_out_s": fade_out_seconds})
                except Exception:
                    pass
                return {"ok": True, "scheduled": True, "playing": VIDEO_PATH, "backend": current_framework["name"], "in_time": in_time, "fade_out_s": fade_out_seconds}
            else:
                # nessun fade out: programma direttamente start
                def _clear_and_start():
                    try:
                        start_logic()
                    finally:
                        schedule_info["play_at"] = None
                    return False
                # Registra il play_at pianificato per rendere l'operazione idempotente lato GUI
                try:
                    schedule_info["play_at"] = target if target >= 1e9 else (time.time() + delay_total)
                except Exception:
                    pass
                schedule_action(in_time, _clear_and_start)
                try:
                    gui_log("play_scheduled", data={"path": VIDEO_PATH, "backend": current_framework["name"], "at": target})
                except Exception:
                    pass
                return {"ok": True, "scheduled": True, "playing": VIDEO_PATH, "backend": current_framework["name"], "in_time": in_time}

    # esecuzione immediata con eventuale fade-out breve
    if fade_out_seconds and fade_out_seconds > 0:
        # Backend GST: fade native
        if current_framework["name"] == "gst" and player.get("pipeline"):
            def after():
                start_logic()
            if player.get("alpha"):
                fade_opacity_to(0.0, fade_out_seconds, on_complete=after)
            elif player.get("vb"):
                fade_to(-1.0, fade_out_seconds, on_complete=after)
            else:
                start_logic()
            try:
                gui_log("play", data={"path": VIDEO_PATH, "backend": current_framework["name"], "fade_out_s": fade_out_seconds})
            except Exception:
                pass
            return {"ok": True, "scheduled": False, "playing": VIDEO_PATH, "backend": current_framework["name"], "fade_out_s": fade_out_seconds}
        # Backend alternativi: prova a usare metodo fade_out se esiste
        elif current_framework["name"] != "gst":
            ensure_backend()
            be = current_framework.get("backend")
            if hasattr(be, "fade_out") and callable(getattr(be, "fade_out")) and be and hasattr(be, "is_playing") and be.is_playing():
                try:
                    def after_alt():
                        start_logic()
                    # Esegue fade_out poi start nuovo contenuto (che farà fade_in interno se richiesto)
                    be.fade_out(fade_out_seconds)
                    # Pianifica lo start dopo la durata
                    GLib.timeout_add(int(fade_out_seconds * 1000), lambda: (after_alt(), False)[1])
                    return {"ok": True, "scheduled": False, "playing": VIDEO_PATH, "backend": current_framework["name"], "fade_out_s": fade_out_seconds}
                except Exception:
                    pass
        # Se non si può fare fade out, procedi subito
        return start_logic()
    else:
        return start_logic()

@app.get("/play")
def api_play_get(
    path: Optional[str] = Query(None),
    filename: Optional[str] = Query(None),
    loop: Optional[int] = Query(None),
    fade_in_seconds: float = Query(0.5),
    fade_out_seconds: Optional[float] = Query(None),
    in_time: Optional[float] = Query(None),
):
    loop_bool = (loop == 1) if loop is not None else None
    # Riutilizza la stessa logica del POST
    return api_play(path, filename, loop_bool, True, fade_in_seconds, fade_out_seconds, in_time, None, None, None)

@app.post("/play_at")
def api_play_at(
    at: float = Query(...),
    path: Optional[str] = Body(None, embed=True),
    filename: Optional[str] = Body(None, embed=True),
    loop: Optional[bool] = Body(None, embed=True),
    hide_splash_first: bool = Body(True, embed=True),
    fade_in_seconds: float = Body(0.5, embed=True),
    fade_out_seconds: Optional[float] = Body(None, embed=True),
):
    # Wrapper esplicito per chiarezza
    return api_play(path, filename, loop, hide_splash_first, fade_in_seconds, fade_out_seconds, at, None, None, None)

@app.get("/play_at")
def api_play_at_get(
    at: float = Query(...),
    path: Optional[str] = Query(None),
    filename: Optional[str] = Query(None),
    loop: Optional[int] = Query(None),
    fade_in_seconds: float = Query(0.5),
    fade_out_seconds: Optional[float] = Query(None),
):
    loop_bool = (loop == 1) if loop is not None else None
    return api_play(path, filename, loop_bool, True, fade_in_seconds, fade_out_seconds, at, None, None, None)

@app.post("/fade_out_current")
def api_fade_out_current(seconds: float = Body(1.0, embed=True)):
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "fade_out"):
            try:
                try:
                    if hasattr(backend, "is_playing") and not backend.is_playing():
                        return {"ok": False, "error": "Nessuna riproduzione attiva (fade_out_current ignorato)"}
                except Exception:
                    pass
                backend.fade_out(seconds)
                return {"ok": True}
            except Exception as exc:
                print(f"[PLAYER] Fade-out backend fallita: {exc}", flush=True)
                return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": "Backend fade_out non supportato"}
    if player["alpha"]:
        fade_opacity_to(0.0, seconds)
    else:
        fade_to(-1.0, seconds)
    return {"ok": True}

# ---------- Backend info & switch ----------
@app.get("/framework")
def framework_get():
    return {"ok": True, "current": current_framework["name"], "available": available_backends()}

@app.post("/change_framework")
def change_framework(payload: dict = Body(...)):
    name = payload.get("name") if isinstance(payload, dict) else None
    in_time = payload.get("in_time") if isinstance(payload, dict) else None
    if not name:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Campo 'name' richiesto"})
    if name == current_framework["name"]:
        # comunque puoi schedulare un restart interno (stop/start) se voluto
        def restart_same():
            backend = current_framework.get("backend")
            try:
                if backend and hasattr(backend, "is_playing") and backend.is_playing():
                    backend.stop()
            except Exception:
                print("[BACKEND] Stop backend corrente fallito", flush=True)
            try:
                if backend and hasattr(backend, "shutdown"):
                    backend.shutdown()
            except Exception:
                print("[BACKEND] Shutdown backend corrente fallito", flush=True)
            try:
                init_backend(name, persist=True)
            except Exception as exc:
                print(f"[BACKEND] Restart dello stesso framework fallito: {exc}", flush=True)
            return False

        delay = schedule_action(in_time, restart_same)
        return {
            "ok": True,
            "message": "Gia' attivo",
            "current": current_framework["name"],
            "scheduled": bool(in_time),
            "delay": delay,
        }
    if name not in available_backends():
        return JSONResponse(status_code=404, content={"ok": False, "error": f"Backend '{name}' non registrato"})

    def do_switch():
        try:
            # stop backend corrente
            try:
                if current_framework["backend"]:
                    current_framework["backend"].stop()
            except Exception:
                pass
            init_backend(name, persist=True)
            # Se splash ancora attivo lo lasciamo; nuova riproduzione avverrà su richiesta
        except Exception as e:
            print(f"[BACKEND] Switch error: {e}", flush=True)
        return False

    delay = schedule_action(in_time, do_switch)
    return {"ok": True, "scheduled": bool(in_time), "delay": delay, "target": name, "current": current_framework["name"] if in_time else name}

# ---------- OFF-player process endpoints ----------
@app.get("/off/status")
def off_status():
    p = off_proc.get("p")
    res = {
        "ok": True,
        "running": bool(p and p.poll() is None),
        "pid": (p.pid if p and p.poll() is None else None),
        "port": off_proc.get("port"),
        "path": off_proc.get("path"),
    }
    # Aggiungi player status remoto se raggiungibile
    try:
        if res["running"]:
            import urllib.request, json as _json
            url = f"http://{OFF_HOST}:{int(globals().get('OFF_PORT', 8082))}/status"
            with urllib.request.urlopen(url, timeout=0.6) as r:
                body = r.read().decode("utf-8", errors="ignore")
            try:
                res["player"] = _json.loads(body)
            except Exception:
                res["player_raw"] = body
    except Exception:
        pass
    return res

@app.post("/off/start")
def off_start(payload: dict = Body(None)):
    path = None
    port = None
    if isinstance(payload, dict):
        path = payload.get("path")
        try:
            port = int(payload.get("port")) if payload.get("port") is not None else None
        except Exception:
            port = None
    res = _off_start(path=path, port=port)
    # Se parte, aggiorna OFF_PORT per il backend "off"
    if res.get("ok") and res.get("running"):
        try:
            globals()["OFF_PORT"] = int(res.get("port") or globals().get("OFF_PORT", 8082))
            persist_settings()
        except Exception:
            pass
        # Se il backend corrente non è "off", passa automaticamente a "off" (persistendo)
        try:
            if current_framework.get("name") != "off":
                init_backend("off", persist=True)
        except Exception as e:
            print(f"[OFF-START] Auto-switch a 'off' fallito: {e}", flush=True)
        # Allinea la directory di OFF alla media dir dell'app headless
        try:
            ensure_backend()
            be = current_framework.get("backend")
            from pathlib import Path as _P
            media_dir = str(_P(MEDIA_DIR).resolve())
            if be and hasattr(be, "set_directory"):
                be.set_directory(media_dir)
                # Reload playlist lato OFF è implicito; opzionalmente possiamo fare una play se richiesto
        except Exception as e:
            print(f"[OFF-START] Sync media dir su OFF fallito: {e}", flush=True)
        # Aggiorna splash di OFF con info IP e versione per uniformità con lo splash convenzionale
        try:
            eth_ip, wifi_ip = get_eth_wifi_ips()
            text = (
                f"Versione -> {VERSION}\n"
                f"HTTP -> {OFF_HOST}:{int(globals().get('OFF_PORT', 8082))}\n"
                f"eth -> {eth_ip or '-'}\n"
                f"wifi -> {wifi_ip or '-'}\n"
            )
            import urllib.parse, urllib.request
            base = f"http://{OFF_HOST}:{int(globals().get('OFF_PORT', 8082))}"
            urllib.request.urlopen(base + "/splash/text?text=" + urllib.parse.quote(text), timeout=0.6).read()
        except Exception as e:
            print(f"[OFF-START] Set splash text OFF fallito: {e}", flush=True)
    return res

@app.post("/off/process/stop")
def off_process_stop():
    return _off_stop()

@app.get("/off/process/stop")
def off_process_stop_get():
    return _off_stop()


@app.get("/off/autostart")
def off_autostart_status():
    return {"ok": True, "enabled": bool(globals().get("OFF_AUTOSTART", False))}


@app.post("/off/autostart")
def off_autostart_update(payload: dict = Body(...)):
    enabled = bool((payload or {}).get("enabled"))
    globals()["OFF_AUTOSTART"] = enabled
    try:
        persist_settings({"off_autostart": enabled})
    except Exception:
        pass
    return {"ok": True, "enabled": enabled}

# ---------- OFF-player direct control passthrough ----------
@app.post("/off/pause")
def off_pause():
    try:
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "pause"):
            be.pause()
            return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return JSONResponse(status_code=400, content={"ok": False, "error": "backend non disponibile"})

@app.get("/off/pause")
def off_pause_get():
    return off_pause()

@app.post("/off/resume")
def off_resume():
    try:
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "resume"):
            be.resume()
            return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return JSONResponse(status_code=400, content={"ok": False, "error": "backend non disponibile"})

@app.get("/off/resume")
def off_resume_get():
    return off_resume()

@app.post("/off/play")
def off_play(path: Optional[str] = Body(None, embed=True)):
    try:
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "play"):
            be.play(path)
            return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return JSONResponse(status_code=400, content={"ok": False, "error": "backend non disponibile"})

@app.get("/off/play")
def off_play_get(path: Optional[str] = Query(None)):
    return off_play(path)

@app.post("/off/stop")
def off_stop_playback():
    try:
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "stop"):
            be.stop()
            return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return JSONResponse(status_code=400, content={"ok": False, "error": "backend non disponibile"})

@app.get("/off/stop")
def off_stop_playback_get():
    return off_stop_playback()

@app.post("/off/next")
def off_next():
    try:
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "next"):
            be.next()
            return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return JSONResponse(status_code=400, content={"ok": False, "error": "backend non disponibile"})

@app.get("/off/next")
def off_next_get():
    return off_next()

@app.post("/off/prev")
def off_prev():
    try:
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "prev"):
            be.prev()
            return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return JSONResponse(status_code=400, content={"ok": False, "error": "backend non disponibile"})

@app.get("/off/prev")
def off_prev_get():
    return off_prev()

@app.post("/off/set")
def off_set(index: int = Query(...)):
    try:
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "set_index"):
            be.set_index(index)
            return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return JSONResponse(status_code=400, content={"ok": False, "error": "backend non disponibile"})

@app.get("/off/set")
def off_set_get(index: int = Query(...)):
    return off_set(index)

@app.post("/off/dir")
def off_dir(path: str = Query(...)):
    try:
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "set_directory"):
            be.set_directory(path)
            return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return JSONResponse(status_code=400, content={"ok": False, "error": "backend non disponibile"})

@app.get("/off/dir")
def off_dir_get(path: str = Query(...)):
    return off_dir(path)

@app.post("/off/reload")
def off_reload():
    try:
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "set_directory"):
            # Lato OFF, /reload è un POST separato. OffBackend non espone metodo dedicato: usa /dir con path corrente? No.
            # Qui effettuiamo una chiamata diretta a /reload sull'istanza OFF.
            import urllib.request
            url = f"http://{OFF_HOST}:{int(globals().get('OFF_PORT', 8082))}/reload"
            urllib.request.urlopen(url, timeout=0.6).read()
            return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return JSONResponse(status_code=400, content={"ok": False, "error": "backend non disponibile"})

@app.get("/off/reload")
def off_reload_get():
    return off_reload()

@app.post("/off/loop")
def off_loop(on: int = Query(1)):
    try:
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "set_loop"):
            be.set_loop(on == 1)
            return {"ok": True, "loop": (on == 1)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return JSONResponse(status_code=400, content={"ok": False, "error": "backend non disponibile"})

@app.get("/off/loop")
def off_loop_get(on: int = Query(1)):
    return off_loop(on)

@app.get("/off/playlist")
def off_playlist():
    """Proxy per playlist di OFF-player. Ritorna lista vuota se OFF non è ancora pronto."""
    try:
        import urllib.request, json as _json
        port = int(globals().get('OFF_PORT', 8082))
        url = f"http://{OFF_HOST}:{port}/playlist"
        with urllib.request.urlopen(url, timeout=0.8) as r:
            body = r.read().decode("utf-8", errors="ignore")
        try:
            data = _json.loads(body)
            # OFF-player ritorna {"dir": "...", "count": N, "index": I, "items": [...]}
            # La GUI si aspetta direttamente la lista items
            if isinstance(data, dict) and "items" in data:
                return data["items"]
            return data
        except Exception as parse_err:
            print(f"[OFF/PLAYLIST] Errore parsing JSON: {parse_err}", flush=True)
            return JSONResponse(status_code=200, content={"raw": body})
    except urllib.error.URLError as ue:
        # OFF-player non ancora pronto → ritorna playlist vuota invece di 502
        if "Connection refused" in str(ue.reason) or "timed out" in str(ue.reason).lower():
            return []  # Playlist vuota, OFF si sta avviando
        print(f"[OFF/PLAYLIST] URLError: {ue.reason}", flush=True)
        return JSONResponse(status_code=502, content={"ok": False, "error": str(ue.reason)})
    except Exception as e:
        print(f"[OFF/PLAYLIST] Exception: {type(e).__name__} - {str(e)}", flush=True)
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})

# ------------------------------------------------------------------
# HUD visibility (OFF-player proxy)
# ------------------------------------------------------------------

@app.post("/hud/visible")
def hud_visible(on: Optional[int] = Query(None), mode: Optional[int] = Query(None)):
    """Proxy to OFF-player HUD visibility/mode toggle.
    Supports mode=0|1|2 (hidden|minimal|full); legacy on=1|0 is accepted.
    """
    try:
        if current_framework.get("name") != "off":
            return JSONResponse(status_code=404, content={"ok": False, "error": "hud/visible disponibile solo con framework 'off'"})
        import urllib.request, json as _json
        port = int(globals().get('OFF_PORT', 8082))
        selected_mode: int
        if mode is not None:
            try:
                selected_mode = int(mode)
            except Exception:
                selected_mode = 0
        elif on is not None:
            selected_mode = 2 if int(on) == 1 else 0
        else:
            selected_mode = int(globals().get("HUD_MODE", 0) or 0)
        if selected_mode < 0:
            selected_mode = 0
        if selected_mode > 2:
            selected_mode = 2
        on_value = 1 if selected_mode > 0 else 0
        print(f"[HUD_PROXY] dispatch mode={selected_mode} on={on_value} (query mode={mode} on={on})", flush=True)
        url = f"http://{OFF_HOST}:{port}/hud/visible?mode={selected_mode}&on={on_value}"
        with urllib.request.urlopen(url, timeout=0.8) as r:
            body = r.read().decode("utf-8", errors="ignore")
        response_mode = selected_mode
        try:
            res = _json.loads(body)
            mode_field = res.get("mode") if isinstance(res, dict) else None
            if isinstance(mode_field, (int, float)):
                response_mode = int(mode_field)
            elif isinstance(res, dict) and "visible" in res:
                response_mode = 2 if bool(res.get("visible")) else 0
            if response_mode < 0:
                response_mode = 0
            if response_mode > 2:
                response_mode = 2
            globals()["HUD_MODE"] = response_mode
            print(f"[HUD_PROXY] response -> mode={response_mode} payload={res}", flush=True)
            if isinstance(res, dict):
                res.setdefault("mode", response_mode)
                res.setdefault("visible", bool(response_mode > 0))
            return res
        except Exception:
            globals()["HUD_MODE"] = response_mode
            print(f"[HUD_PROXY] response (raw) -> mode={response_mode} body={body}", flush=True)
            return {"ok": True, "raw": body, "mode": response_mode, "visible": bool(response_mode > 0)}
    except Exception as e:
        print(f"[HUD_PROXY] ERROR: {e}", flush=True)
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.get("/hud/visible")
def hud_visible_get(on: Optional[int] = Query(None), mode: Optional[int] = Query(None)):
    return hud_visible(on=on, mode=mode)

# ---------- System info (per GUI) ----------
@app.get("/media/base")
def media_base():
    """Ritorna il path base dei media e la sorgente usata per determinarlo."""
    try:
        path = str(MEDIA_DIR)
        src = globals().get("MEDIA_DIR_SOURCE", "auto")
        exists = Path(MEDIA_DIR).exists()
        return {"path": path, "source": src, "exists": bool(exists)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.get("/system/os")
def system_os():
    try:
        sysname = platform.system()
        release = platform.release()
        machine = platform.machine()
    except Exception:
        sysname, release, machine = "unknown", "", ""
    return {"ok": True, "system": sysname, "release": release, "machine": machine}

# ---------- Packaging helpers (per GUI) ----------
def _detect_target_key() -> str:
    try:
        sysname = platform.system().lower()
        machine = platform.machine().lower()
    except Exception:
        return "unknown"
    if sysname.startswith("darwin") or sysname.startswith("mac"):
        return "darwin-x64" if "arm" not in machine and "aarch" not in machine else "darwin-arm64"
    if sysname.startswith("windows") or sysname.startswith("win"):
        return "windows-x64"
    if sysname.startswith("linux"):
        if "arm" in machine or "aarch" in machine:
            return "linux-arm"
        return "linux-x64"
    return "unknown"

@app.get("/packages/targets")
def packages_targets():
    key = _detect_target_key()
    return {
        "ok": True,
        "recommended": key,
        "targets": [
            {"key": "darwin-x64", "label": "macOS (Intel)", "ext": ".tar.gz"},
            {"key": "darwin-arm64", "label": "macOS (Apple Silicon)", "ext": ".tar.gz"},
            {"key": "linux-x64", "label": "Linux x64", "ext": ".tar.gz"},
            {"key": "linux-arm", "label": "Linux ARM (RPi)", "ext": ".tar.gz"},
            {"key": "windows-x64", "label": "Windows x64", "ext": ".zip"},
        ]
    }

def _off_bin_dir() -> Path:
    return (APP_DIR.parent / "OFF-player" / "bin").resolve()

@app.get("/off/archive")
def off_archive(fmt: str = Query(None, description="zip|tar.gz; default per OS")):
    bindir = _off_bin_dir()
    if not bindir.exists():
        return JSONResponse(status_code=404, content={"ok": False, "error": f"OFF bin non trovato: {bindir}"})
    # Scegli formato
    key = _detect_target_key()
    use_zip = (fmt == "zip") or (fmt is None and key == "windows-x64")
    suffix = ".zip" if use_zip else ".tar.gz"
    # Crea archivio temporaneo
    try:
        tmpdir = Path(tempfile.mkdtemp(prefix="offpkg-"))
        arch_path = tmpdir / f"off-player{suffix}"
        if use_zip:
            with zipfile.ZipFile(str(arch_path), "w", compression=zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(bindir):
                    for f in files:
                        p = Path(root) / f
                        rel = str(p.relative_to(bindir))
                        z.write(str(p), arcname=rel)
        else:
            with tarfile.open(str(arch_path), "w:gz") as tar:
                tar.add(str(bindir), arcname="bin")
        f = open(arch_path, "rb")
        media = "application/zip" if use_zip else "application/gzip"
        filename = arch_path.name
        def _cleanup():
            try:
                f.close()
            except Exception:
                pass
            try:
                # Rimuovi dir temporanea
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass
        bg = BackgroundTask(_cleanup) if BackgroundTask else None
        return StreamingResponse(
            f,
            media_type=media,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
            background=bg,
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": f"Packaging error: {e}"})

@app.post("/pause")
def api_pause():
    pause_play(); return {"ok": True}

@app.post("/resume")
def api_resume():
    resume_play(); return {"ok": True}

@app.post("/go_to_start")
def api_go_to_start():
    """Position the player at time 0 of the current track and pause.
    Strategy:
      - If a backend offers faststart/prepare primitives, use them.
      - Else, for gst rebuild/prep the pipeline at first frame (paused).
    """
    # Determine current path from playlist or fallback to VIDEO_PATH
    try:
        if playlist.get("items") and 0 <= int(playlist.get("index", -1)) < len(playlist["items"]):
            current_path = str(playlist["items"][playlist["index"]])
        else:
            current_path = VIDEO_PATH
    except Exception:
        current_path = VIDEO_PATH
    if not current_path:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Nessun media corrente"})

    if current_framework["name"] != "gst":
        try:
            ensure_backend()
            be = current_framework.get("backend")
            # Prefer dedicated prepare_media if available (CVLC)
            if be and hasattr(be, "prepare_media"):
                be.prepare_media(current_path)  # type: ignore[attr-defined]
                player["state"] = "paused"
                return {"ok": True, "method": "backend"}
            # Fallback: use faststart_prepare endpoint semantics
            res = api_faststart_prepare(path=current_path)
            try:
                if isinstance(res, JSONResponse) and int(res.status_code) >= 400:
                    return res
            except Exception:
                pass
            player["state"] = "paused"
            return {"ok": True, "method": "faststart"}
        except Exception as e:
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

    # GST: rebuild/prep pipeline at first frame and leave paused
    try:
        prepare_pipeline_for(current_path)
        player["state"] = "paused"
        return {"ok": True, "method": "gst"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.post("/loop")
def api_loop(on: int = Query(1)):
    set_loop(on == 1); return {"ok": True, "loop": player["loop"]}

@app.post("/stop")
def api_stop():
    t0 = time.time()
    print("[HEADLESS] POST /stop received", flush=True)
    # Invalida azioni pianificate e ferma tutti i timer noti
    try:
        cancel_all_schedules()
    except Exception:
        pass
    t_cancel = time.time()
    # Ferma riproduzione backend/pipeline e sgancia preload
    try:
        stop_play()
    except Exception:
        pass
    t_after_stop = time.time()
    try:
        if preloaded.get("pipeline"):
            try:
                preloaded["pipeline"].set_state(Gst.State.NULL)
            except Exception:
                pass
        preloaded.update({"path": None, "pipeline": None, "vb": None, "alpha": None})
    except Exception:
        pass
    # Resetta faststart e readiness show
    try:
        faststart["prepared_path"] = None
    except Exception:
        pass
    try:
        show_state.update({"ready": False, "for": None})
    except Exception:
        pass
    # Forza overlay nero pieno (best-effort) per garantire schermo nero immediato
    t_before_overlay = time.time()
    try:
        if OVERLAY_ENABLED:
            overlay_show(alpha=1.0)
        else:
            # Fallback: mostra nero di idle (pipeline dedicata)
            show_idle_black()
    except Exception:
        # Se overlay fallisce, tenta comunque idle black
        try:
            show_idle_black()
        except Exception:
            pass
    # Stato
    player["state"] = "stopped"
    t_end = time.time()
    diagnostics = {}
    try:
        diagnostics = {
            "total_ms": int((t_end - t0) * 1000),
            "cancel_ms": int((t_cancel - t0) * 1000),
            "stop_ms": int((t_after_stop - t0) * 1000),
            "overlay_ms": int((t_end - t_before_overlay) * 1000),
        }
        gui_log("stop_diagnostics", data=diagnostics)
        print(f"[HEADLESS] /stop diagnostics -> {diagnostics}", flush=True)
    except Exception:
        diagnostics = {}
    return {"ok": True, "state": player.get("state", "stopped"), "diagnostics": diagnostics}

@app.post("/fade_in")
def api_fade_in(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    if in_time:
        def _do():
            return api_fade_in(seconds)
        delay = schedule_action(in_time, _do)
        return {"ok": True, "scheduled": True, "delay": delay}
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "fade_in"):
            try:
                # Se il backend supporta lo stato, evita no-op quando non sta riproducendo
                try:
                    if hasattr(backend, "is_playing") and not backend.is_playing():
                        return {"ok": False, "error": "Nessuna riproduzione attiva (fade_in ignorato)"}
                except Exception:
                    pass
                backend.fade_in(seconds)
                return {"ok": True}
            except Exception as exc:
                print(f"[PLAYER] Fade-in backend fallita: {exc}", flush=True)
        return {"ok": False, "error": "Backend fade_in non supportato"}
    if player["alpha"]:
        fade_opacity_to(1.0, seconds)
    else:
        fade_to(0.0, seconds)
    return {"ok": True}

@app.post("/fade_out")
def api_fade_out(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    if in_time:
        def _do():
            return api_fade_out(seconds)
        delay = schedule_action(in_time, _do)
        return {"ok": True, "scheduled": True, "delay": delay}
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "fade_out"):
            try:
                try:
                    if hasattr(backend, "is_playing") and not backend.is_playing():
                        return {"ok": False, "error": "Nessuna riproduzione attiva (fade_out ignorato)"}
                except Exception:
                    pass
                backend.fade_out(seconds)
                return {"ok": True}
            except Exception as exc:
                print(f"[PLAYER] Fade-out backend fallita: {exc}", flush=True)
        return {"ok": False, "error": "Backend fade_out non supportato"}
    if player["alpha"]:
        fade_opacity_to(0.0, seconds)
    else:
        fade_to(-1.0, seconds)
    return {"ok": True}

# ------- Visual fades (backend-specific: cvlc brightness) -------
@app.post("/visual/ftb")
def api_visual_ftb(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    if in_time:
        def _do():
            return api_visual_ftb(seconds)
        delay = schedule_action(in_time, _do)
        return {"ok": True, "scheduled": True, "delay": delay}

    fade_seconds = max(0.0, float(seconds if seconds is not None else 0.0))

    # 1) Fade audio (riutilizza endpoint standard)
    audio_payload = api_fade_out(fade_seconds)

    visual_method: str | None = None
    visual_error: str | None = None

    if current_framework["name"] == "gst":
        # gst utilizza già fade_opacity/fade_to in api_fade_out
        visual_method = "gst"
    else:
        ensure_backend()
        backend = current_framework.get("backend")

        if OVERLAY_ENABLED:
            try:
                overlay_show(alpha=0.0)
                overlay_fade_to(1.0, fade_seconds)
                visual_method = "overlay"
            except Exception as exc:
                print(f"[OVERLAY] FTB fallback a backend: {exc}", flush=True)

        if visual_method is None and backend and hasattr(backend, "visual_fade_out"):
            try:
                backend.visual_fade_out(fade_seconds)
                visual_method = "backend"
            except Exception as exc:
                print(f"[PLAYER] Visual FTB backend fallita: {exc}", flush=True)
                visual_error = str(exc)

        if visual_method is None:
            try:
                fade_to(-1.0, fade_seconds)
                visual_method = "fallback"
            except Exception as exc:
                visual_error = str(exc)

    # 2) Stop finale al termine del fade
    stop_delay = schedule_action(fade_seconds, api_stop)

    ok = bool(audio_payload.get("ok", False)) and visual_error is None
    response: dict[str, object] = {
        "ok": ok,
        "seconds": fade_seconds,
        "stop_delay": stop_delay,
        "audio": audio_payload,
        "visual_method": visual_method,
    }
    if visual_error:
        response["visual_error"] = visual_error
    return response

@app.post("/visual/fade_in")
def api_visual_fade_in(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    if in_time:
        def _do():
            return api_visual_fade_in(seconds)
        delay = schedule_action(in_time, _do)
        return {"ok": True, "scheduled": True, "delay": delay}
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        # Preferisci overlay KMS se disponibile
        if OVERLAY_ENABLED:
            try:
                overlay_show(alpha=1.0)
                overlay_fade_to(0.0, seconds)
                def _hide():
                    try: overlay_hide()
                    except Exception: pass
                    return False
                GLib.timeout_add(int(max(0.0, seconds) * 1000) + 50, _hide)
                return {"ok": True, "method": "overlay"}
            except Exception as e:
                print(f"[OVERLAY] Fade-in fallback a backend: {e}", flush=True)
        if backend and hasattr(backend, "visual_fade_in"):
            try:
                backend.visual_fade_in(seconds)
                return {"ok": True, "method": "backend"}
            except Exception as exc:
                print(f"[PLAYER] Visual Fade-in backend fallita: {exc}", flush=True)
                return {"ok": False, "error": str(exc)}
        # fallback (audio fade-in)
        return api_fade_in(seconds)
    if player["alpha"]:
        fade_opacity_to(1.0, seconds)
    else:
        fade_to(0.0, seconds)
    return {"ok": True}

# ------- Visual brightness absolute (0..1, tolerant of 0..100) -------
@app.post("/visual/brightness")
def api_visual_brightness(value: float = Query(...), seconds: float = Query(0.5), in_time: float | None = Query(None)):
    """Fade visual brightness to an absolute value in [0..1].
    Also accepts 0..100 and clamps. Exposes the last requested value in /status.
    """
    if in_time:
        def _do():
            return api_visual_brightness(value, seconds)
        delay = schedule_action(in_time, _do)
        return {"ok": True, "scheduled": True, "delay": delay}

    try:
        v = float(value)
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "valore brightness non valido"})
    # Accept 0..100 as percentage
    if v > 1.0 and v <= 100.0:
        v = v / 100.0
    v = max(0.0, min(1.0, v))
    s = max(0.0, float(seconds if seconds is not None else 0.0))

    visual_method: str | None = None
    visual_error: str | None = None

    # Persist last requested value for GUI status
    try:
        globals()["_LAST_VISUAL_BRIGHTNESS"] = float(v)
    except Exception:
        pass

    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        # Prefer overlay when available (maps brightness to black overlay alpha)
        if OVERLAY_ENABLED:
            try:
                target_alpha = max(0.0, min(1.0, 1.0 - v))
                overlay_fade_to(target_alpha, s)
                visual_method = "overlay"
            except Exception as exc:
                print(f"[OVERLAY] Brightness fallback a backend: {exc}", flush=True)
        # Backend native support (e.g., cvlc_controller.visual_fade_to)
        if visual_method is None and backend and hasattr(backend, "visual_fade_to"):
            try:
                backend.visual_fade_to(v, s)
                visual_method = "backend"
            except Exception as exc:
                print(f"[PLAYER] Visual brightness backend fallita: {exc}", flush=True)
                visual_error = str(exc)
        # Last-resort: try local video-balance fade mapping 0..1 -> -1..1
        if visual_method is None:
            try:
                vb_target = (v * 2.0) - 1.0
                fade_to(vb_target, s)
                visual_method = "fallback"
            except Exception as exc:
                visual_error = str(exc)
        resp: dict[str, object] = {"ok": visual_error is None, "method": visual_method, "value": v, "seconds": s}
        if visual_error:
            resp["error"] = visual_error
        return resp

    # gst: use video-balance brightness directly
    try:
        vb_target = (v * 2.0) - 1.0
        fade_to(vb_target, s)
        return {"ok": True, "method": "gst", "value": v}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

# ---------- Display mode control (Windows) ----------


class DisplayModeApplyError(RuntimeError):
    """Errore specifico per la modifica della modalità video su Windows."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 500,
        payload: Optional[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.payload = payload or {}


def _winapi_apply_display_mode(
    width: int,
    height: int,
    *,
    refresh_hz: Optional[int] = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Try changing the primary display mode via WinAPI, fallback handled by caller."""
    if not _is_windows:
        raise NotImplementedError("winapi not available on this platform")
    try:
        import ctypes
        from ctypes import wintypes
    except Exception as exc:
        raise RuntimeError(f"ctypes unavailable: {exc}") from exc

    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * 32),
            ("dmSpecVersion", wintypes.WORD),
            ("dmDriverVersion", wintypes.WORD),
            ("dmSize", wintypes.WORD),
            ("dmDriverExtra", wintypes.WORD),
            ("dmFields", wintypes.DWORD),
            ("dmOrientation", wintypes.SHORT),
            ("dmPaperSize", wintypes.SHORT),
            ("dmPaperLength", wintypes.SHORT),
            ("dmPaperWidth", wintypes.SHORT),
            ("dmScale", wintypes.SHORT),
            ("dmCopies", wintypes.SHORT),
            ("dmDefaultSource", wintypes.SHORT),
            ("dmPrintQuality", wintypes.SHORT),
            ("dmColor", wintypes.SHORT),
            ("dmDuplex", wintypes.SHORT),
            ("dmYResolution", wintypes.SHORT),
            ("dmTTOption", wintypes.SHORT),
            ("dmCollate", wintypes.SHORT),
            ("dmFormName", wintypes.WCHAR * 32),
            ("dmLogPixels", wintypes.WORD),
            ("dmBitsPerPel", wintypes.DWORD),
            ("dmPelsWidth", wintypes.DWORD),
            ("dmPelsHeight", wintypes.DWORD),
            ("dmDisplayFlags", wintypes.DWORD),
            ("dmDisplayFrequency", wintypes.DWORD),
            ("dmICMMethod", wintypes.DWORD),
            ("dmICMIntent", wintypes.DWORD),
            ("dmMediaType", wintypes.DWORD),
            ("dmDitherType", wintypes.DWORD),
            ("dmReserved1", wintypes.DWORD),
            ("dmReserved2", wintypes.DWORD),
            ("dmPanningWidth", wintypes.DWORD),
            ("dmPanningHeight", wintypes.DWORD),
        ]

    ENUM_CURRENT_SETTINGS = -1
    CDS_FULLSCREEN = 0x00000004
    CDS_UPDATEREGISTRY = 0x00000001
    DISP_CHANGE_SUCCESSFUL = 0
    DISP_CHANGE_RESTART = 1
    DM_PELSWIDTH = 0x00080000
    DM_PELSHEIGHT = 0x00100000
    DM_DISPLAYFREQUENCY = 0x00400000

    user32 = ctypes.windll.user32

    size_dev_mode = ctypes.sizeof(DEVMODEW)

    current_mode = DEVMODEW()
    current_mode.dmSize = size_dev_mode
    if not user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(current_mode)):
        raise RuntimeError("EnumDisplaySettingsW failed")

    before = {
        "width": int(current_mode.dmPelsWidth),
        "height": int(current_mode.dmPelsHeight),
        "frequency": int(current_mode.dmDisplayFrequency),
        "bits_per_pixel": int(current_mode.dmBitsPerPel),
    }
    requested = {
        "width": int(width),
        "height": int(height),
        "refresh_hz": int(refresh_hz) if refresh_hz else None,
    }
    # Enumerate supported modes for the primary display.
    enumerated: list[tuple[dict[str, int], DEVMODEW]] = []
    idx = 0
    while True:
        mode = DEVMODEW()
        mode.dmSize = size_dev_mode
        if not user32.EnumDisplaySettingsW(None, idx, ctypes.byref(mode)):
            break
        info = {
            "index": idx,
            "width": int(mode.dmPelsWidth),
            "height": int(mode.dmPelsHeight),
            "frequency": int(mode.dmDisplayFrequency),
            "bits_per_pixel": int(mode.dmBitsPerPel),
            "flags": int(mode.dmDisplayFlags),
        }
        enumerated.append((info, mode))
        idx += 1

    if not enumerated:
        raise DisplayModeApplyError("Impossibile enumerare le modalità video", status=500)

    available_modes = [dict(info) for info, _ in enumerated]
    available_summary = available_modes[:50]

    desired_refresh = int(refresh_hz) if refresh_hz else None
    current_freq = before.get("frequency") or 0

    matching_modes = [(info, mode) for info, mode in enumerated if info["width"] == width and info["height"] == height]

    if not matching_modes:
        payload = {
            "requested": requested,
            "available_modes": available_summary,
        }
        raise DisplayModeApplyError("Modalità richiesta non disponibile", status=404, payload=payload)

    prefer_freq: Optional[int]
    if desired_refresh and desired_refresh > 0:
        prefer_freq = desired_refresh
    elif current_freq and current_freq > 0:
        prefer_freq = int(current_freq)
    else:
        prefer_freq = None

    def _sort_key(item: tuple[dict[str, int], DEVMODEW]) -> tuple[int, int, int, int, int]:
        info, _ = item
        freq = int(info.get("frequency") or 0)
        bpp = int(info.get("bits_per_pixel") or 0)
        if desired_refresh and desired_refresh > 0:
            # Prefer exact refresh; freq=0 (driver default) treated as large delta
            delta = 0 if freq == desired_refresh else (abs(freq - desired_refresh) if freq else 10_000)
            exact_bonus = 0 if freq == desired_refresh else 1
        elif prefer_freq and prefer_freq > 0:
            delta = abs(freq - prefer_freq) if freq else 10_000
            exact_bonus = 0 if freq == prefer_freq else 1
        else:
            delta = 0
            exact_bonus = 0
        # Higher bits-per-pixel and frequency preferred (negative for ascending sort)
        return (
            delta,
            exact_bonus,
            -bpp,
            -freq,
            info["index"],
        )

    candidate_modes = sorted(matching_modes, key=_sort_key)
    best_info, best_mode = candidate_modes[0]
    best_info = dict(best_info)

    # Adjust frequency if an explicit refresh was requested but not directly available.
    if desired_refresh and desired_refresh > 0:
        if best_info.get("frequency") != desired_refresh:
            best_info["frequency"] = desired_refresh
            best_mode.dmDisplayFrequency = int(desired_refresh)
            best_mode.dmFields |= DM_DISPLAYFREQUENCY
    elif best_mode.dmDisplayFrequency == 0 and current_freq:
        best_mode.dmDisplayFrequency = int(current_freq)
        best_mode.dmFields |= DM_DISPLAYFREQUENCY

    best_mode.dmFields |= DM_PELSWIDTH | DM_PELSHEIGHT
    best_mode.dmPelsWidth = int(width)
    best_mode.dmPelsHeight = int(height)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "method": "winapi",
            "requested": requested,
            "current": before,
            "candidate": best_info,
            "available_modes": available_summary,
        }

    result = user32.ChangeDisplaySettingsExW(
        None,
        ctypes.byref(best_mode),
        None,
        CDS_FULLSCREEN | CDS_UPDATEREGISTRY,
        None,
    )
    result_code = int(result)
    requires_restart = result_code == DISP_CHANGE_RESTART
    if result_code not in (DISP_CHANGE_SUCCESSFUL, DISP_CHANGE_RESTART):
        payload = {
            "requested": requested,
            "candidate": best_info,
            "result_code": result_code,
        }
        raise DisplayModeApplyError(
            f"ChangeDisplaySettingsExW failed ({result_code})",
            status=500,
            payload=payload,
        )

    updated = DEVMODEW()
    updated.dmSize = size_dev_mode
    if user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(updated)):
        after = {
            "width": int(updated.dmPelsWidth),
            "height": int(updated.dmPelsHeight),
            "frequency": int(updated.dmDisplayFrequency),
            "bits_per_pixel": int(updated.dmBitsPerPel),
        }
    else:
        after = None

    response: dict[str, object] = {
        "ok": True,
        "method": "winapi",
        "requested": requested,
        "mode_before": before,
        "mode_after": after,
        "applied_mode": best_info,
        "result_code": result_code,
        "requires_restart": requires_restart,
    }
    return response


@app.post("/display/mode/apply")
def api_display_mode_apply(payload: dict | None = Body(None)):
    if not _is_windows:
        return JSONResponse(status_code=501, content={"ok": False, "error": "Cambio risoluzione supportato solo su Windows"})

    data = payload or {}

    width = _coerce_positive_int(data.get("width") or data.get("w"))
    if width is None:
        width = _coerce_positive_int(data.get("target_width"))
    if width is None:
        width = _coerce_positive_int(globals().get("TARGET_WIDTH"))

    height = _coerce_positive_int(data.get("height") or data.get("h"))
    if height is None:
        height = _coerce_positive_int(data.get("target_height"))
    if height is None:
        height = _coerce_positive_int(globals().get("TARGET_HEIGHT"))

    refresh_hz = _parse_refresh_to_int(
        data.get("refresh_hz")
        or data.get("refresh")
        or data.get("hz")
        or data.get("fps")
    )
    if refresh_hz is None:
        refresh_hz = _parse_refresh_to_int(data.get("target_refresh") or data.get("target_fps"))
    if refresh_hz is None:
        refresh_hz = _target_refresh_hz()

    if width is None or height is None:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "Parametri width/height mancanti",
                "provided": {k: data.get(k) for k in ("width", "height", "w", "h")},
            },
        )

    requested = {
        "width": width,
        "height": height,
        "refresh_hz": refresh_hz,
        "target_width": _coerce_positive_int(globals().get("TARGET_WIDTH")),
        "target_height": _coerce_positive_int(globals().get("TARGET_HEIGHT")),
        "target_refresh_hz": _target_refresh_hz(),
    }

    force_legacy = bool(data.get("use_legacy") or data.get("force_exe"))
    force_discovery = bool(data.get("force_discovery"))
    dry_run = bool(data.get("dry_run"))

    native_error: Optional[dict[str, Any]] = None
    if not force_legacy:
        native_start = time.time()
        try:
            native_result = _winapi_apply_display_mode(
                int(width),
                int(height),
                refresh_hz=int(refresh_hz) if refresh_hz else None,
                dry_run=dry_run,
            )
            native_result["method"] = "winapi"
            native_result.setdefault("requested", requested)
            native_result.setdefault("requires_restart", False)
            native_result.setdefault("duration_ms", int((time.time() - native_start) * 1000))
            if not dry_run:
                try:
                    gui_log(
                        "display_mode_apply",
                        data={
                            "method": "winapi",
                            "ok": True,
                            "duration_ms": native_result["duration_ms"],
                            "requires_restart": bool(native_result.get("requires_restart")),
                        },
                    )
                except Exception:
                    pass
            return native_result
        except DisplayModeApplyError as exc:
            error_payload: dict[str, Any] = {
                "ok": False,
                "method": "winapi",
                "error": str(exc),
                "requested": requested,
            }
            extra = getattr(exc, "payload", None) or {}
            if isinstance(extra, dict):
                error_payload.update(extra)
            if dry_run:
                error_payload["dry_run"] = True
            status_code = getattr(exc, "status", 500)
            try:
                log_payload = {k: v for k, v in error_payload.items() if k != "available_modes"}
                print(f"[DISPLAY] WinAPI mode change error: {log_payload}", flush=True)
            except Exception:
                pass
            if isinstance(status_code, int) and status_code < 500 and not force_legacy:
                return JSONResponse(status_code=max(400, status_code), content=error_payload)
            error_payload["status"] = int(status_code) if isinstance(status_code, int) else 500
            native_error = error_payload
        except NotImplementedError:
            native_error = {"error": "winapi_not_available", "status": 501}
        except Exception as exc:
            native_error = {"error": "winapi_exception", "message": str(exc), "status": 500}

    exe_path, checked = _discover_set_resolution_executable(force=force_discovery)
    if not exe_path or not exe_path.exists():
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": "Utility SetResolution.exe non trovata",
                "checked_paths": checked,
                "winapi_error": native_error,
            },
        )

    command = [str(exe_path), str(width), str(height)]
    if refresh_hz:
        command.append(str(refresh_hz))

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "executable": str(exe_path),
            "command": command,
            "requested": requested,
            "checked_paths": checked,
            "method": "legacy_exe",
            "winapi_error": native_error,
        }

    start_ts = time.time()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        exit_code = int(proc.returncode)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except Exception as exc:
        error_msg = f"Esecuzione SetResolution fallita: {exc}"
        try:
            gui_log("display_mode_apply_error", level="ERROR", data={"error": error_msg})
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"ok": False, "error": error_msg, "command": command})

    duration_ms = int((time.time() - start_ts) * 1000)
    ok = exit_code == 0

    combined_lower = (stdout + "\n" + stderr).lower()
    requires_restart = exit_code in {3010, 1641} or "necessario un riavvio" in combined_lower or "restart" in combined_lower

    mode_after = None
    try:
        # Force refresh of cached display mode after attempting the change
        mode_after = get_display_mode(force=True)
    except Exception:
        mode_after = None

    response = {
        "ok": ok,
        "command": command,
        "executable": str(exe_path),
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "requires_restart": bool(requires_restart),
        "stdout": _truncate_text(stdout),
        "stderr": _truncate_text(stderr),
        "requested": requested,
        "checked_paths": checked,
        "display_mode_after": mode_after,
        "method": "legacy_exe",
    }
    if native_error:
        response["winapi_error"] = native_error

    try:
        gui_log(
            "display_mode_apply",
            data={
                "command": command,
                "method": "legacy_exe",
                "ok": ok,
                "exit_code": exit_code,
                "requires_restart": bool(requires_restart),
                "duration_ms": duration_ms,
                "winapi_error": native_error,
            },
        )
    except Exception:
        pass

    if not ok:
        return JSONResponse(status_code=500, content=response)
    return response


@app.post("/display/mode/force_720p")
def api_display_mode_force_720p(payload: dict | None = Body(None)):
    """Force common 1280x720 preset via SetResolution helper."""
    # Reuse the generic handler by providing preset dimensions and optional overrides.
    preset = {
        "width": 1280,
        "height": 720,
    }

    data = payload or {}

    # Allow callers to override refresh_hz or force discovery/dry-run behaviour if needed.
    if (refresh := _parse_refresh_to_int(
        data.get("refresh_hz")
        or data.get("refresh")
        or data.get("fps")
    )) is not None:
        preset["refresh_hz"] = refresh

    if bool(data.get("force_discovery")):
        preset["force_discovery"] = True

    if bool(data.get("dry_run")):
        preset["dry_run"] = True

    return api_display_mode_apply(preset)

# ---------- Overlay endpoints ----------
@app.post("/overlay/show")
def api_overlay_show(alpha: float = Query(1.0)):
    return JSONResponse(status_code=410, content={"ok": False, "error": "overlay removed"})

@app.post("/overlay/hide")
def api_overlay_hide():
    return JSONResponse(status_code=410, content={"ok": False, "error": "overlay removed"})

@app.post("/overlay/fade")
def api_overlay_fade(target: float | None = Query(None), seconds: float = Query(1.0), in_time: float | None = Query(None)):
    return JSONResponse(status_code=410, content={"ok": False, "error": "overlay removed"})

@app.post("/overlay/fade_at")
def api_overlay_fade_at(target: float | None = Query(None), seconds: float = Query(1.0), at: float = Query(...)):
    return JSONResponse(status_code=410, content={"ok": False, "error": "overlay removed"})


def _stop_active_media_for_clear(timeout: float = 3.0) -> dict[str, bool]:
    """Force playback halt so media files can be safely removed."""
    info = {"playback_stopped": False, "off_player_stopped": False}
    log = logging.getLogger("headless-player.media")
    try:
        state = str(player.get("state", "")).lower() if isinstance(player, dict) else ""
    except Exception:
        state = ""
    if state in {"playing", "paused", "preparing"}:
        try:
            stop_play()
            info["playback_stopped"] = True
        except Exception as exc:
            log.warning("media_clear: stop_play() failed before cleanup: %s", exc, exc_info=True)
    try:
        autoplay["enabled"] = False
    except Exception:
        pass
    try:
        _autoplay_cancel_timer()
    except Exception:
        pass
    try:
        _autoplay_cancel_monitor()
    except Exception:
        pass
    try:
        proc = off_proc.get("p") if isinstance(off_proc, dict) else None
    except Exception:
        proc = None
    if proc is not None and callable(getattr(proc, "poll", None)) and proc.poll() is None:
        try:
            result = _off_stop()
            info["off_player_stopped"] = bool(result.get("ok"))
        except Exception as exc:
            log.warning("media_clear: _off_stop() failed before cleanup: %s", exc, exc_info=True)
        end_ts = time.time() + max(timeout, 0.0)
        while proc.poll() is None and time.time() < end_ts:
            time.sleep(0.1)
        if proc.poll() is None:
            log.warning("media_clear: OFF-player still running after %.1fs", timeout)
    return info

@app.get("/overlay/status")
def api_overlay_status():
    return JSONResponse(status_code=410, content={"ok": False, "error": "overlay removed"})

@app.post("/overlay/probe")
def api_overlay_probe():
    return JSONResponse(status_code=410, content={"ok": False, "error": "overlay removed"})

@app.get("/media")
def list_media():
    """Elenca i file nella directory media con informazioni dettagliate"""
    files = []
    try:
        for file_path in MEDIA_DIR.iterdir():
            # Filtra file di sistema/non media
            if file_path.name in {'.DS_Store', '_sentinel_desktop.txt'}:
                continue
            if file_path.is_file():
                stat = file_path.stat()
                
                # Leggi i primi bytes per determinare il tipo
                file_type = "unknown"
                with open(file_path, 'rb') as f:
                    header = f.read(16)
                    if header[4:8] == b'ftyp':
                        file_type = "mp4"
                    elif header.startswith(b'<!DOCTYPE') or header.startswith(b'<html'):
                        file_type = "html"
                    elif header.startswith(b'\x1a\x45\xdf\xa3'):
                        file_type = "mkv"
                    elif header.startswith(b'RIFF') and header[8:12] == b'AVI ':
                        file_type = "avi"
                
                files.append({
                    "name": file_path.name,
                    "path": str(file_path),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "type": file_type,
                    "header": header[:8].hex() if len(header) >= 8 else ""
                })
        
        return {"ok": True, "files": files}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.post("/media/clear")
def media_clear(confirm: int = Query(0)):
    """Cancella TUTTI i contenuti nella directory media del device.
    Per sicurezza richiede confirm=1 come query (es: POST /media/clear?confirm=1).
    """
    if confirm != 1:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Conferma mancante: usa ?confirm=1"})
    release_info = _stop_active_media_for_clear()
    try:
        time.sleep(0.2)
    except Exception:
        pass
    removed = 0
    skipped: list[str] = []
    errors = []
    playlist_cleared = False
    protect_current = False
    try:
        current_path = Path(VIDEO_PATH).resolve()
        current_exists = current_path.exists()
    except Exception:
        current_path = None
        current_exists = False
    if current_exists:
        try:
            state = str(player.get("state", "")).lower() if isinstance(player, dict) else ""
        except Exception:
            state = ""
        protect_current = state in {"playing", "paused"}
        if protect_current and (release_info.get("playback_stopped") or release_info.get("off_player_stopped")):
            protect_current = False
    else:
        current_path = None
        current_exists = False
    try:
        for entry in MEDIA_DIR.iterdir():
            try:
                resolved = entry.resolve()
                # Salta il file corrente o directory che lo contengono solo se il player è in esecuzione
                if protect_current and current_path and current_exists:
                    try:
                        if resolved == current_path or current_path.is_relative_to(resolved):
                            skipped.append(entry.name)
                            continue
                    except AttributeError:
                        # Compatibilità Python <3.9: usa workaround manuale
                        try:
                            if resolved == current_path or str(current_path).startswith(str(resolved) + os.sep):
                                skipped.append(entry.name)
                                continue
                        except Exception:
                            pass
                if entry.is_file() or entry.is_symlink():
                    try:
                        _safe_unlink(entry)
                        removed += 1
                    except Exception as e:
                        errors.append(f"{entry.name}: {e}")
                elif entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True); removed += 1
            except Exception as e:
                errors.append(f"{entry.name}: {e}")
        if not protect_current:
            try:
                playlist["items"] = []
                playlist["index"] = -1
                playlist_cleared = True
            except Exception:
                pass
            try:
                faststart["prepared_path"] = None
            except Exception:
                pass
        return {
            "ok": True,
            "removed": removed,
            "errors": errors,
            "skipped": skipped,
            "playlist_cleared": playlist_cleared,
            "active_media_protected": protect_current,
            "playback_stopped": release_info.get("playback_stopped", False),
            "off_player_stopped": release_info.get("off_player_stopped", False),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.get("/media/clear")
def media_clear_get(confirm: int = Query(0)):
    """Alias GET per compatibilità: richiede comunque confirm=1."""
    return media_clear(confirm)


def _safe_extract_zip(zip_path: Path, dest_dir: Path) -> list[str]:
    safe_paths: list[str] = []
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            normalized = Path(member.filename)
            target = dest_dir / normalized
            if not str(target.resolve()).startswith(str(dest_dir.resolve())):
                continue
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            safe_paths.append(str(target.relative_to(dest_dir)))
    return safe_paths


@app.post("/media/sync")
def media_sync(payload: dict[str, Any] = Body(...)):
    """Scarica contenuti media da URL e li deposita in MEDIA_DIR."""
    url = payload.get("url") or os.environ.get("MEDIA_SYNC_URL")
    if not url or not isinstance(url, str):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Campo 'url' obbligatorio"})
    cleanup = bool(payload.get("cleanup"))
    temp_dir = Path(tempfile.mkdtemp())
    parsed = urlparse(url)
    filename = Path(parsed.path).name or "media_bundle"
    downloaded = temp_dir / filename
    try:
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        downloaded.parent.mkdir(parents=True, exist_ok=True)
        with open(downloaded, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    fh.write(chunk)
        if cleanup:
            shutil.rmtree(MEDIA_DIR, ignore_errors=True)
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        extracted = []
        if downloaded.suffix.lower() == ".zip" and zipfile.is_zipfile(downloaded):
            extracted = _safe_extract_zip(downloaded, MEDIA_DIR)
        else:
            dest = MEDIA_DIR / downloaded.name
            shutil.move(str(downloaded), str(dest))
            extracted = [dest.name]
        return {"ok": True, "files": extracted, "count": len(extracted)}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

@app.post("/settings/reload")
def api_settings_reload(data: Optional[dict] = Body(None)):
    """Aggiorna impostazioni runtime e ricrea la pipeline e lo splash se attivo.
    Accetta JSON tipo: {"USE_KMS": false, "USE_HW_DECODER": true, "TARGET_WIDTH": 1280, ... , "restart_play": true, "SPLASH_BLACK": false}
    """
    global USE_KMS, USE_HW_DECODER, TARGET_WIDTH, TARGET_HEIGHT, TARGET_FPS, SPLASH_BLACK, OVERLAY_FADE_OUT_ON_PLAY_S, OVERLAY_FADE_IN_ON_STOP_S, STARTUP_BROADCAST, STARTUP_PORT
    data = data or {}
    changes = {}
    if "USE_KMS" in data:
        USE_KMS = bool(data["USE_KMS"]); changes["USE_KMS"] = USE_KMS
    if "USE_HW_DECODER" in data:
        USE_HW_DECODER = bool(data["USE_HW_DECODER"]); changes["USE_HW_DECODER"] = USE_HW_DECODER
    if "TARGET_WIDTH" in data:
        TARGET_WIDTH = int(data["TARGET_WIDTH"]); changes["TARGET_WIDTH"] = TARGET_WIDTH
    if "TARGET_HEIGHT" in data:
        TARGET_HEIGHT = int(data["TARGET_HEIGHT"]); changes["TARGET_HEIGHT"] = TARGET_HEIGHT
    if "TARGET_FPS" in data:
        TARGET_FPS = str(data["TARGET_FPS"]); changes["TARGET_FPS"] = TARGET_FPS
    if "SPLASH_BLACK" in data:
        SPLASH_BLACK = bool(data["SPLASH_BLACK"]); changes["SPLASH_BLACK"] = SPLASH_BLACK
    # UDP_ENABLED rimosso: UDP sempre attivo, non configurabile
    # if "UDP_ENABLED" in data:
    #     global UDP_ENABLED
    #     UDP_ENABLED = bool(data["UDP_ENABLED"])
    #     changes["UDP_ENABLED"] = UDP_ENABLED
    #     if UDP_ENABLED:
    #         threading.Thread(target=_udp_thread, daemon=True).start()
    if "LOG_UDP_ENABLED" in data or "log_udp_enabled" in data:
        val = bool(data.get("LOG_UDP_ENABLED", data.get("log_udp_enabled")))
        set_log_udp_target(LOG_UDP_HOST, LOG_UDP_PORT, enabled=val, persist=False)
        changes["log_udp_enabled"] = LOG_UDP_ENABLED
    if "LOG_UDP_HOST" in data or "log_udp_host" in data:
        host = str(data.get("LOG_UDP_HOST", data.get("log_udp_host")))
        set_log_udp_target(host, LOG_UDP_PORT, enabled=True, persist=False)
        changes["log_udp_host"] = LOG_UDP_HOST
    if "LOG_UDP_PORT" in data or "log_udp_port" in data:
        try:
            port = int(data.get("LOG_UDP_PORT", data.get("log_udp_port")))
            set_log_udp_target(LOG_UDP_HOST, port, enabled=True, persist=False)
            changes["log_udp_port"] = LOG_UDP_PORT
        except Exception:
            pass
    if "AUTOPLAY_ENABLED" in data or "autoplay_enabled" in data:
        target = data.get("AUTOPLAY_ENABLED", data.get("autoplay_enabled"))
        restart_auto = data.get("restart_autoplay", True)
        _autoplay_set_enabled(bool(target), restart=bool(restart_auto))
        changes["autoplay_enabled"] = autoplay.get("enabled", False)

    # Overlay fade durations (accept both snake_case and UPPERCASE)
    if "overlay_fade_out_on_play_s" in data or "OVERLAY_FADE_OUT_ON_PLAY_S" in data:
        try:
            OVERLAY_FADE_OUT_ON_PLAY_S = float(data.get("overlay_fade_out_on_play_s", data.get("OVERLAY_FADE_OUT_ON_PLAY_S")))
            changes["overlay_fade_out_on_play_s"] = OVERLAY_FADE_OUT_ON_PLAY_S
        except Exception:
            pass
    if "overlay_fade_in_on_stop_s" in data or "OVERLAY_FADE_IN_ON_STOP_S" in data:
        try:
            OVERLAY_FADE_IN_ON_STOP_S = float(data.get("overlay_fade_in_on_stop_s", data.get("OVERLAY_FADE_IN_ON_STOP_S")))
            changes["overlay_fade_in_on_stop_s"] = OVERLAY_FADE_IN_ON_STOP_S
        except Exception:
            pass
    # Autoplay fade seconds
    if "autoplay_fade_seconds" in data or "AUTOPLAY_FADE_SECONDS" in data:
        try:
            globals()["AUTOPLAY_FADE_SECONDS"] = float(data.get("autoplay_fade_seconds", data.get("AUTOPLAY_FADE_SECONDS")))
            changes["autoplay_fade_seconds"] = globals().get("AUTOPLAY_FADE_SECONDS")
        except Exception:
            pass

    if "startup_macs" in data or "STARTUP_MACS" in data:
        updated = _set_startup_macs(data.get("startup_macs", data.get("STARTUP_MACS")))
        changes["startup_macs"] = updated
    if "startup_broadcast" in data or "STARTUP_BROADCAST" in data:
        val = data.get("startup_broadcast", data.get("STARTUP_BROADCAST"))
        if val is not None:
            STARTUP_BROADCAST = str(val)
            changes["startup_broadcast"] = STARTUP_BROADCAST
    if "startup_port" in data or "STARTUP_PORT" in data:
        val = data.get("startup_port", data.get("STARTUP_PORT"))
        if val is not None:
            try:
                STARTUP_PORT = int(val)
                changes["startup_port"] = STARTUP_PORT
            except Exception:
                pass

    restart_play = bool(data.get("restart_play", True))

    # Ricrea pipeline del player
    was_playing = (player["state"] == "playing")
    if player["pipeline"]:
        try:
            player["pipeline"].set_state(Gst.State.NULL)
        except Exception:
            pass
        player["pipeline"] = None; player["vb"] = None
    # Pulisci preload
    preloaded.update({"path": None, "pipeline": None, "vb": None})

    # Ricrea splash con nuovo sink/impostazioni se attivo
    splash_was_active = splash["active"]
    if splash_was_active:
        try:
            hide_splash()
        except Exception:
            pass
        show_splash_until_play()

    if was_playing and restart_play:
        ensure_pipeline();
        if player["vb"]:
            player["vb"].set_property("brightness", -1.0)
        player["pipeline"].set_state(Gst.State.PLAYING)
        player["state"] = "playing"
    else:
        player["state"] = "stopped"
    persist_settings()
    return {"ok": True, "changes": changes, "was_playing": was_playing, "splash_recreated": splash_was_active, "state": player["state"]}

@app.get("/settings/reload")
def api_settings_reload_get(
    USE_KMS: Optional[int] = None,
    USE_HW_DECODER: Optional[int] = None,
    TARGET_WIDTH: Optional[int] = None,
    TARGET_HEIGHT: Optional[int] = None,
    TARGET_FPS: Optional[str] = None,
    SPLASH_BLACK: Optional[int] = None,
    restart_play: int = 1,
    UDP_ENABLED: Optional[int] = None,
    LOG_UDP_ENABLED: Optional[int] = None,
    LOG_UDP_HOST: Optional[str] = None,
    LOG_UDP_PORT: Optional[int] = None,
):
    data = {}
    if USE_KMS is not None: data["USE_KMS"] = bool(USE_KMS)
    if USE_HW_DECODER is not None: data["USE_HW_DECODER"] = bool(USE_HW_DECODER)
    if TARGET_WIDTH is not None: data["TARGET_WIDTH"] = int(TARGET_WIDTH)
    if TARGET_HEIGHT is not None: data["TARGET_HEIGHT"] = int(TARGET_HEIGHT)
    if TARGET_FPS is not None: data["TARGET_FPS"] = str(TARGET_FPS)
    if SPLASH_BLACK is not None: data["SPLASH_BLACK"] = bool(SPLASH_BLACK)
    # if UDP_ENABLED is not None: data["UDP_ENABLED"] = bool(UDP_ENABLED)  # Rimosso: UDP sempre attivo
    if LOG_UDP_ENABLED is not None: data["LOG_UDP_ENABLED"] = bool(LOG_UDP_ENABLED)
    if LOG_UDP_HOST is not None: data["LOG_UDP_HOST"] = str(LOG_UDP_HOST)
    if LOG_UDP_PORT is not None: data["LOG_UDP_PORT"] = int(LOG_UDP_PORT)
    data["restart_play"] = bool(restart_play)
    return api_settings_reload(data)

@app.get("/autoplay")
def api_autoplay_status():
    current = None
    if playlist["items"] and 0 <= playlist["index"] < len(playlist["items"]):
        current = playlist["items"][playlist["index"]]
    return {
        "ok": True,
        "enabled": autoplay.get("enabled", False),
        "playlist_size": len(playlist["items"]),
        "current": current,
    }


@app.post("/autoplay")
def api_autoplay_update(payload: dict = Body(...)):
    if not isinstance(payload, dict):
        payload = {}
    enabled = bool(payload.get("enabled", False))
    restart = payload.get("restart", True)
    delay = payload.get("delay")
    delay_val: float | None
    try:
        delay_val = float(delay) if delay is not None else None
    except (TypeError, ValueError):
        delay_val = None
    _autoplay_set_enabled(enabled, restart=bool(restart), delay=delay_val)
    return api_autoplay_status()

def _safe_media_path(name: str) -> Path:
    """Return a MEDIA_DIR-confined path, stripping any directory tricks."""
    clean = Path(name).name  # drop path components
    clean = clean.replace("\\", "_")
    if clean in {"", ".", ".."}:
        raise ValueError("Nome file non valido")
    dest = MEDIA_DIR / clean
    try:
        dest.resolve().relative_to(MEDIA_DIR.resolve())
    except Exception as exc:
        raise ValueError("Percorso non consentito") from exc
    return dest


def _handle_download_asset(url: str, filename: Optional[str] = None):
    if not url:
        return JSONResponse(status_code=422, content={"ok": False, "error": "Campo 'url' mancante"})
    MEDIA_DIR.mkdir(exist_ok=True)
    name_source = filename or url.split("/")[-1] or "asset.bin"
    try:
        dest = _safe_media_path(name_source.strip())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})

    print(f"[DOWNLOAD] Scarico asset da: {url} -> {dest.name}", flush=True)

    def rep(got, total):
        if total > 0:
            pct = int(got * 100 / total)
            if pct % 20 == 0:
                print(f"[DOWNLOAD] Progresso: {pct}% ({got}/{total} bytes)", flush=True)

    try:
        download_to(dest, url, report=rep)

        # Controlla il file scaricato
        file_size = dest.stat().st_size
        print(f"[DOWNLOAD] File scaricato: {dest} ({file_size} bytes)", flush=True)

        # Controlla i primi bytes per identificare il tipo di file
        with open(dest, 'rb') as f:
            header = f.read(16)
            print(f"[DOWNLOAD] Header file: {header[:8].hex()}", flush=True)

            # Controlla se è un file MP4 valido
            if len(header) >= 8 and header[4:8] == b'ftyp':
                print("[DOWNLOAD] File riconosciuto come MP4", flush=True)
            elif len(header) >= 4 and (header[:4] == b'\x00\x00\x00\x20' or header[:4] == b'\x00\x00\x00\x18'):
                print("[DOWNLOAD] Possibile file MP4 con header diverso", flush=True)
            elif header.startswith(b'<!DOCTYPE') or header.startswith(b'<html'):
                print("[DOWNLOAD] ERRORE: Il server ha restituito HTML invece di un media", flush=True)
                dest.unlink(missing_ok=True)
                return JSONResponse(status_code=502, content={"ok": False, "error": "Sorgente ha restituito HTML"})
            else:
                print(f"[DOWNLOAD] ATTENZIONE: File non riconosciuto come media. Header: {header[:8]}", flush=True)

        return {"ok": True, "stored": str(dest), "size": file_size}
    except Exception as e:
        print(f"[DOWNLOAD] ERRORE: {e}", flush=True)
        dest.unlink(missing_ok=True)
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.post("/download_asset")
def download_asset(payload: dict = Body(...)):
    # Estrai parametri dal JSON body
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Body JSON richiesto"})
    url = payload.get("url")
    filename = payload.get("filename")
    return _handle_download_asset(url, filename)

@app.get("/download_asset")
def download_asset_get(url: str, filename: Optional[str] = None):
    """Alias GET per compatibilità: /download_asset?url=...&filename=..."""
    return _handle_download_asset(url, filename)

@app.post("/upload_asset")
async def upload_asset(file: UploadFile = File(...), filename: Optional[str] = Query(None)):
    """Upload diretto dal client GUI al player.

    Accetta multipart/form-data con campo 'file'.
    Parametro opzionale 'filename' per definire il nome di destinazione in media/.
    """
    MEDIA_DIR.mkdir(exist_ok=True)
    try:
        target_name = (filename or file.filename or "asset.bin").strip()
    except Exception:
        target_name = "asset.bin"
    try:
        dest = _safe_media_path(target_name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    try:
        # Scrivi a chunk per ridurre RAM
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        try:
            os.replace(tmp, dest)
        except PermissionError as e:
            # Prova a forzare i permessi sul file di destinazione se già presente, poi ritenta
            try:
                os.chmod(dest, 0o644)
            except Exception:
                pass
            try:
                os.replace(tmp, dest)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                return JSONResponse(
                    status_code=403,
                    content={
                        "ok": False,
                        "error": f"Permesso negato su '{dest}'. Esegui /maintenance/fix_permissions e riprova.",
                    },
                )
        size = dest.stat().st_size
        print(f"[UPLOAD] Ricevuto file: {dest} ({size} bytes)", flush=True)
        return {"ok": True, "stored": str(dest), "size": size}
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[name-defined]
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.get("/network/ethernet/status")
def ethernet_status():
    """Ritorna stato connessioni ethernet e indirizzi IP v4."""
    info = {"nmcli": None, "ip_addr": None}
    try:
        out = subprocess.check_output(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,IP4.ADDRESS", "connection", "show"], text=True)
        info["nmcli"] = out.strip().splitlines()
    except Exception as e:
        info["nmcli"] = [f"errore: {e}"]
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show"], text=True)
        info["ip_addr"] = out.strip().splitlines()
    except Exception as e:
        info["ip_addr"] = [f"errore: {e}"]
    return {"ok": True, "info": info}

@app.post("/network/ethernet/config")
def ethernet_config(data: dict = Body(...)):
    """Configura IP statico su connessione ethernet principale via nmcli.
    Body: {"ip_cidr": "192.168.1.50/24", "gateway": "", "dns": ""}
    """
    if not isinstance(data, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Body JSON richiesto"})
    ip_cidr = data.get("ip_cidr")
    gateway = data.get("gateway", "")
    dns = data.get("dns", "")
    if not ip_cidr:
        return JSONResponse(status_code=422, content={"ok": False, "error": "Campo 'ip_cidr' mancante"})
    log = []
    try:
        # Trova prima connessione ethernet
        cmd_find = "nmcli -t -f NAME,TYPE connection show | awk -F: '$2==\"ethernet\"{print $1; exit}'"
        p = subprocess.Popen(["bash", "-lc", cmd_find], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = p.communicate(timeout=5)
        if p.returncode != 0:
            return JSONResponse(status_code=500, content={"ok": False, "error": f"nmcli find failed: {err.strip()}"})
        eth_con = out.strip() or ""
        if not eth_con:
            return JSONResponse(status_code=404, content={"ok": False, "error": "Nessuna connessione ethernet trovata"})
        log.append(f"Connessione ethernet: {eth_con}")
        # Applica configurazione con sudo -n
        cmds = [
            f"nmcli con mod '{eth_con}' ipv4.method manual ipv4.addresses '{ip_cidr}'",
            f"nmcli con mod '{eth_con}' ipv4.gateway '{gateway}'",
            f"nmcli con mod '{eth_con}' ipv4.dns '{dns}'",
            f"nmcli con up '{eth_con}'"
        ]
        for c in cmds:
            rc = subprocess.call(["sudo", "-n", "bash", "-lc", c])
            log.append(f"$ {c} -> rc={rc}")
            if rc != 0:
                return JSONResponse(status_code=500, content={"ok": False, "error": f"Comando fallito: {c}", "log": log})
        return {"ok": True, "log": log}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "log": log})

def _start_download_update(url: str, version: Optional[str], callback_url: Optional[str], start_at: Optional[float] = None):
    # Evita doppio avvio se già in corso
    if current_update["status"] == "downloading":
        return {"ok": True, "status": "already-downloading", "progress": current_update.get("progress")}

    current_update["available"] = version or "unknown"
    current_update["callback_url"] = callback_url
    current_update["progress"] = 0
    current_update["error"] = None
    current_update["log"] = current_update["log"][-100:]
    current_update["status"] = "downloading"
    current_update["bytes_done"] = 0
    current_update["bytes_total"] = 0
    # Scegli un percorso scrivibile per il pacchetto scaricato
    upd_dir = _pick_update_dir()
    dest = upd_dir / "update_pkg.bin"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    # Memorizza path per apply successivo
    current_update["pkg_path"] = str(dest)

    log_update(f"Inizio download update: url={url} version={current_update['available']}")

    def worker():
        try:
            # Attendi fino a start_at se fornito
            if start_at:
                delay = max(0.0, float(start_at) - time.time())
                if delay > 0:
                    log_update(f"Attendo {delay:.2f}s per start_at")
                    time.sleep(delay)
            def rep(got, total):
                current_update["bytes_done"] = got
                current_update["bytes_total"] = total
                if total:
                    pct = int(got * 100 / total)
                    if pct != current_update.get("progress"):
                        current_update["progress"] = pct
                        if pct % 10 == 0:
                            log_update(f"Download {pct}% ({got}/{total} bytes)")
            download_to(dest, url, report=rep)

            # Verifica header file scaricato per intercettare HTML (404/errore)
            with open(dest, "rb") as f:
                header = f.read(64)
            if header.startswith(b"<!DOCTYPE") or header.startswith(b"<html"):
                raise RuntimeError("Il server ha restituito HTML (probabile 404/errore) non un archivio update")

            current_update["progress"] = 100
            log_update("Download completato")
            current_update["status"] = "downloaded"
        except Exception as e:
            current_update["status"] = "error"
            current_update["error"] = str(e)
            log_update(f"Errore download: {e}")

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "status": "started", "progress": 0}

@app.post("/download_update")
def download_update_post(payload: dict = Body(...)):
    """Avvia il download via POST JSON: {"url": ..., "version": ..., "callback_url": ...}"""
    url = payload.get("url") if isinstance(payload, dict) else None
    version = payload.get("version") if isinstance(payload, dict) else None
    callback_url = payload.get("callback_url") if isinstance(payload, dict) else None
    start_at = payload.get("start_at") if isinstance(payload, dict) else None
    if not url:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Campo 'url' mancante"})
    return _start_download_update(url, version, callback_url, start_at)

@app.get("/download_update")
def download_update_get(url: str, version: Optional[str] = None, callback_url: Optional[str] = None, start_at: Optional[float] = None):
    """Avvia il download via GET con query params (?url=...&version=...)."""
    if not url:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Parametro 'url' richiesto"})
    return _start_download_update(url, version, callback_url, start_at)

@app.post("/media/playlist")
def api_playlist(loop: bool = Body(True, embed=True)):
    """Crea una playlist con tutti i file validi dentro media/ e avvia dal primo.
    Parametri:
      - loop (bool): se True la playlist riparte da capo.
    Filtra solo file video riconosciuti da validate_media_file.
    """
    items = []
    scan = []
    for p in sorted(MEDIA_DIR.iterdir()):
        if not p.is_file():
            continue
        # esegui check header
        valid = validate_media_file(p)
        # determina tipo
        ftype = "unknown"; header_hex = ""
        try:
            with open(p, 'rb') as fh:
                header = fh.read(16)
                header_hex = header[:8].hex() if len(header) >= 8 else ""
                if len(header) >= 8 and header[4:8] == b'ftyp':
                    ftype = "mp4"
                elif header.startswith(b'\x1a\x45\xdf\xa3'):
                    ftype = "mkv"
                elif len(header) >= 12 and header.startswith(b'RIFF') and header[8:12] == b'AVI ':
                    ftype = "avi"
                elif header.startswith(b'<!DOCTYPE') or header.startswith(b'<html'):
                    ftype = "html"
        except Exception:
            pass
        scan.append({"path": str(p), "valid": valid, "type": ftype, "header": header_hex})
        if valid:
            items.append(str(p))
        else:
            print(f"[PLAYLIST] Ignoro (non valido): {p.name}", flush=True)
    if not items:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Nessun file video valido in media/"})
    playlist["items"] = items
    playlist["index"] = 0
    playlist["loop"] = loop
    print(f"[PLAYLIST] Creata playlist con {len(items)} elementi (loop={loop})", flush=True)
    stop_play()
    start_play_with_path(items[0])
    return {"ok": True, "count": len(items), "current": items[0], "loop": loop, "items": items, "scan": scan}

@app.post("/playlist/apply")
def api_playlist_apply(payload: dict = Body(...)):
    """Applica una playlist esplicita e pre-carica il primo elemento in pausa a t=0.

    Body JSON atteso:
      { "items": ["file1.mp4", "file2.mp4", ...], "loop": true }

    - Gli elementi possono essere nomi file dentro media/ oppure percorsi assoluti già dentro media/.
    - Non avvia la riproduzione: prepara il primo elemento per fast-start.
    - Espone show_ready=true in /status e invia un evento UDP alla GUI se sottoscritta.
    """
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Body JSON richiesto"})
    items_in = payload.get("items")
    loop = bool(payload.get("loop", True))
    if not isinstance(items_in, list) or not items_in:
        return JSONResponse(status_code=422, content={"ok": False, "error": "Campo 'items' mancante o vuoto"})

    resolved: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    for it in items_in:
        try:
            name = str(it).strip()
        except Exception:
            continue
        if not name:
            continue
        p = Path(name)
        if not p.is_absolute():
            p = MEDIA_DIR / name
        # Confina a MEDIA_DIR
        try:
            p = p.resolve()
            if not str(p).startswith(str(MEDIA_DIR.resolve())):
                invalid.append(name); continue
        except Exception:
            invalid.append(name); continue
        if not p.exists():
            missing.append(name); continue
        # opzionale: verifica header valido (best-effort: accetta comunque l'item per stabilità)
        if not validate_media_file(p):
            invalid.append(name)
        resolved.append(str(p))

    if not resolved:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Nessun item valido trovato", "missing": missing, "invalid": invalid})

    # Aggiorna stato playlist ma non avvia
    playlist["items"] = resolved
    playlist["index"] = 0
    playlist["loop"] = loop

    first = resolved[0]
    # Precarica il primo elemento con fast-start per backend (best-effort: non fallire l'API se la preparazione fallisce)
    prepare_error: Optional[str] = None
    try:
        if current_framework["name"] == "gst":
            prepare_pipeline_for(first)
            faststart["prepared_path"] = first
        else:
            ensure_backend()
            be = current_framework.get("backend")
            if be is None:
                raise RuntimeError("Backend non disponibile")
            if hasattr(be, "faststart_prepare"):
                try:
                    be.faststart_prepare(first)  # type: ignore[attr-defined]
                except Exception as bex:
                    prepare_error = str(bex)
                faststart["prepared_path"] = first
            else:
                # Fallback: non tutti i backend supportano faststart esplicito; non avviare
                faststart["prepared_path"] = first
    except Exception as e:
        prepare_error = str(e)
        try:
            gui_log("playlist_apply error", level="ERROR", kind="playlist", data={"error": prepare_error})
        except Exception:
            pass

    # Marca readiness e notifica via UDP log
    try:
        show_state.update({"ready": True, "for": first, "ts": time.time()})
        gui_log("show_ready", level="INFO", kind="playlist", data={"for": first})
    except Exception:
        pass

    resp = {"ok": True, "count": len(resolved), "prepared": first, "loop": loop, "missing": missing, "invalid": invalid}
    if prepare_error:
        resp["prepare_error"] = prepare_error
    return resp

@app.get("/playlist/status")
def api_playlist_status():
    return {
        "ok": True,
        "items": playlist["items"],
        "index": playlist["index"],
        "current": playlist["items"][playlist["index"]] if (playlist["items"] and 0 <= playlist["index"] < len(playlist["items"])) else None,
        "loop": playlist["loop"],
    }


def _maybe_skip_single_track(action: str) -> Optional[dict[str, Any]]:
    """Se la playlist ha un solo elemento, ignora NEXT/PREV per evitare fade inutili."""
    try:
        items = playlist["items"]
    except Exception:
        items = []
    if len(items) != 1:
        return None
    idx = playlist.get("index", 0) if isinstance(playlist, dict) else 0
    if not isinstance(idx, int):
        idx = 0
    if idx < 0:
        idx = 0
    current = items[0]
    msg = "Comando ignorato: playlist con un solo elemento"
    print(f"[PLAYLIST] {action.upper()} ignorato: single-item playlist", flush=True)
    try:
        # Clear any transient action to reflect no-op
        _fsm_set("idle", reason="playlist_skip_single", action=None)
    except Exception:
        pass
    return {
        "ok": True,
        "skipped": True,
        "reason": "single_item",
        "current": current,
        "index": idx,
        "action": action,
        "message": msg,
    }

@app.post("/playlist/next")
def api_playlist_next():
    skipped = _maybe_skip_single_track("next")
    if skipped:
        return skipped
    if current_framework["name"] != "gst":
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "next"):
            try:
                fsm_preparing("playlist_next_backend", action="next")
            except Exception:
                pass
            try:
                be.next()
                try:
                    player["state"] = "playing"
                except Exception:
                    pass
                try:
                    fsm_playing("playlist_next_backend", action="next")
                except Exception:
                    pass
                return {"ok": True}
            except Exception as e:
                try:
                    fsm_idle("playlist_next_failed", error=str(e))
                except Exception:
                    pass
                return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    if not playlist["items"]:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Playlist vuota"})
    if playlist["index"] < 0:
        playlist["index"] = 0
    next_i = playlist["index"] + 1
    if next_i >= len(playlist["items"]):
        if playlist["loop"]:
            next_i = 0
        else:
            return JSONResponse(status_code=400, content={"ok": False, "error": "Fine playlist"})
    playlist["index"] = next_i
    next_path = playlist["items"][next_i]
    print(f"[PLAYLIST] NEXT -> index={next_i} file={next_path}", flush=True)
    start_play_with_path(next_path, action="next")
    return {"ok": True, "current": next_path, "index": next_i}

@app.post("/playlist/prev")
def api_playlist_prev():
    skipped = _maybe_skip_single_track("prev")
    if skipped:
        return skipped
    if current_framework["name"] != "gst":
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "prev"):
            try:
                fsm_preparing("playlist_prev_backend", action="prev")
            except Exception:
                pass
            try:
                be.prev()
                try:
                    player["state"] = "playing"
                except Exception:
                    pass
                try:
                    fsm_playing("playlist_prev_backend", action="prev")
                except Exception:
                    pass
                return {"ok": True}
            except Exception as e:
                try:
                    fsm_idle("playlist_prev_failed", error=str(e))
                except Exception:
                    pass
                return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    if not playlist["items"]:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Playlist vuota"})
    if playlist["index"] < 0:
        playlist["index"] = 0
    prev_i = playlist["index"] - 1
    if prev_i < 0:
        if playlist["loop"]:
            prev_i = len(playlist["items"]) - 1
        else:
            return JSONResponse(status_code=400, content={"ok": False, "error": "Inizio playlist"})
    playlist["index"] = prev_i
    prev_path = playlist["items"][prev_i]
    print(f"[PLAYLIST] PREV -> index={prev_i} file={prev_path}", flush=True)
    start_play_with_path(prev_path, action="prev")
    return {"ok": True, "current": prev_path, "index": prev_i}

@app.post("/playlist/jump")
def api_playlist_jump(index: int = Query(..., description="0-based track index to jump to")):
    """Jump to a specific track in the playlist by index (0-based)."""
    if current_framework["name"] != "gst":
        ensure_backend()
        be = current_framework.get("backend")
        if be and hasattr(be, "set_index"):
            try:
                be.set_index(index)
                return {"ok": True, "index": index}
            except Exception as e:
                return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    if not playlist["items"]:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Playlist vuota"})
    if index < 0 or index >= len(playlist["items"]):
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Indice non valido: {index} (range: 0-{len(playlist['items'])-1})"})
    playlist["index"] = index
    jump_path = playlist["items"][index]
    print(f"[PLAYLIST] JUMP -> index={index} file={jump_path}", flush=True)
    start_play_with_path(jump_path, action="jump")
    return {"ok": True, "current": jump_path, "index": index}

# Rimosso endpoint /playlist/take: NEXT/PREV avviano direttamente la riproduzione

@app.post("/playlist/loop")
def api_playlist_loop(on: int = Query(1)):
    playlist["loop"] = (on == 1)
    if current_framework["name"] != "gst":
        try:
            ensure_backend()
            be = current_framework.get("backend")
            if be and hasattr(be, "set_loop"):
                be.set_loop(playlist["loop"])
        except Exception:
            pass
    return {"ok": True, "loop": playlist["loop"]}

@app.post("/update")
def update_apply(restart: bool = Body(True, embed=True), start_at: Optional[float] = Body(None, embed=True)):
    # Determina percorso del pacchetto scaricato
    pkg_path = current_update.get("pkg_path")
    pkg = Path(pkg_path) if pkg_path else (APP_DIR / "update_pkg.bin")
    if not pkg.exists():
        return JSONResponse(status_code=400, content={"ok": False, "error": "Nessun pacchetto scaricato"})
    current_update["status"] = "applying"
    current_update["stage"] = "applying"
    if start_at:
        delay = max(0.0, float(start_at) - time.time())
        if delay > 0:
            log_update(f"Apply programmata tra {delay:.2f}s (start_at)")
            time.sleep(delay)
    try:
        try:
            pkg_size = pkg.stat().st_size
        except Exception:
            pkg_size = -1
        suffix = pkg.suffix.lower()
        log_update(f"Apply start: path={pkg} size={pkg_size}B type={suffix or 'n/a'} restart={restart}")

        # Path speciale Windows: se il pacchetto è un installer (.exe/.msi), eseguilo in modo silenzioso e termina
        if os.name == "nt" and suffix in (".exe", ".msi"):
            try:
                import shutil as _sh
                # Copia l'installer in una dir temporanea di sistema (per evitare lock in APP_DIR)
                tmp_dir = Path(os.environ.get("TEMP", str(Path.home())))
                tmp_dir.mkdir(parents=True, exist_ok=True)
                inst_path = tmp_dir / ("maroccos_installer" + suffix)
                try:
                    _sh.copy2(pkg, inst_path)
                except Exception:
                    # Fallback: prova semplice copia binaria
                    with open(pkg, "rb") as _rf, open(inst_path, "wb") as _wf:
                        _wf.write(_rf.read())

                # Costruisci comando silenzioso
                if suffix == ".exe":
                    # Inno Setup tipico
                    args = [
                        "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                        (
                            "Start-Process -FilePath '" + str(inst_path).replace("'", "''") + "' "
                            "-ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS','/RESTARTAPPLICATIONS' "
                            "-WindowStyle Hidden -PassThru | Out-Null"
                        ),
                    ]
                    cmd = ["powershell"] + args
                else:
                    # MSI
                    ps = (
                        "Start-Process -FilePath 'msiexec.exe' "
                        "-ArgumentList '/i','" + str(inst_path).replace("'", "''") + "','/qn','/norestart' "
                        "-WindowStyle Hidden -PassThru | Out-Null"
                    )
                    cmd = ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps]

                print(f"[UPDATE] Avvio installer silenzioso: {' '.join(cmd[:3])} …", flush=True)
                try:
                    p = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        cwd=str(tmp_dir),
                        creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)),
                    )
                    current_update["install_pid"] = p.pid
                    log_update(f"Installer avviato PID={p.pid} (detached)")
                except Exception as _pex:
                    print(f"[UPDATE] Avvio installer fallito: {_pex}", flush=True)
                    raise

                # Prova a ripulire il pacchetto locale (best-effort)
                try:
                    _safe_unlink(pkg)
                except Exception:
                    pass

                current_update["status"] = "ok"
                current_update["stage"] = "ok"
                log_update("Installer inviato, termino per completare l'aggiornamento…")
                current_update["last_apply_http"] = 200
                if restart:
                    try:
                        _emit_discovery_beacon(reason="post-update-restart")
                    except Exception:
                        pass
                    os._exit(0)
                else:
                    try:
                        _emit_discovery_beacon(reason="post-update")
                    except Exception:
                        pass
                return {"ok": True, "install": True}
            except Exception as _win_e:
                current_update["status"] = "error"
                current_update["stage"] = "error"
                log_update(f"Errore apply (installer Windows): {_win_e}")
                return JSONResponse(status_code=500, content={"ok": False, "error": str(_win_e)})

        # Percorso standard: pacchetto zip/tar, copia file in APP_DIR
        if os.name == "nt" and suffix == ".zip":
            # su Windows prova percorso elevato per sicurezza
            res = _handle_elevated_zip_apply(pkg, restart=restart)
            if not res.get("ok"):
                # fallback: prova apply normale (potrebbe riuscire se l'app non è in Program Files)
                apply_update(pkg)
            else:
                return {"ok": True, "elevated": True}
        else:
            apply_update(pkg)
        try:
            _safe_unlink(pkg)
        except Exception as _e:
            # Non fatale: file potrebbe essere bloccato da AV/indicizzazione; lasciamo .stale
            try:
                log_update(f"Cleanup update_pkg.bin fallito (non fatale): {_e}")
            except Exception:
                pass
        if (APP_DIR/"VERSION").exists():
            global VERSION
            VERSION = (APP_DIR/"VERSION").read_text().strip()
        current_update["status"] = "ok"
        current_update["stage"] = "ok"
        current_update["last_apply_http"] = 200
        log_update("Update applicato con successo")
        beacon_reason = "post-update-restart" if restart else "post-update"
        try:
            _emit_discovery_beacon(reason=beacon_reason)
        except Exception:
            pass
        if restart:
            log_update("Riavvio servizio…")
            os._exit(0)
        return {"ok": True}
    except Exception as e:
        current_update["status"] = "error"
        current_update["stage"] = "error"
        err_type = type(e).__name__
        current_update["error_type"] = err_type
        msg = str(e)
        if isinstance(e, PermissionError):
            msg += " (PermissionError: verifica antivirus / permessi di scrittura)"
        log_update(f"Errore apply [{err_type}]: {msg}")
        current_update["last_apply_http"] = 500
        return JSONResponse(status_code=500, content={"ok": False, "error": msg, "error_type": err_type})

@app.post("/maintenance/run_setup")
def api_run_setup(force: bool = Body(False, embed=True)):
    if maintenance["status"] == "running" and not force:
        return JSONResponse(status_code=409, content={"ok": False, "error": "setup già in esecuzione"})
    setup_path = str(APP_DIR / "setup.sh")
    if not os.path.exists(setup_path):
        return JSONResponse(status_code=404, content={"ok": False, "error": "setup.sh non trovato"})
    maintenance["status"] = "running"; maintenance["error"] = None
    log_maintenance("Avvio setup.sh…")

    def worker():
        try:
            # Assicurati che sia eseguibile
            try:
                os.chmod(setup_path, 0o755)
            except Exception:
                pass
            # Esegui direttamente lo script con sudo (richiede regola sudoers specifica)
            cmd = ["sudo", "-n", setup_path]
            try:
                p = subprocess.Popen(cmd, cwd=str(APP_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            except Exception:
                # Fallback: prova con sudo bash PATH (se sudoers permette bash)
                p = subprocess.Popen(["sudo", "-n", "/bin/bash", setup_path], cwd=str(APP_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            assert p.stdout is not None
            for line in p.stdout:
                log_maintenance(line.rstrip())
            rc = p.wait()
            if rc == 0:
                maintenance["status"] = "ok"; log_maintenance("setup.sh completato con successo")
            else:
                maintenance["status"] = "error"; maintenance["error"] = f"exit code {rc}"; log_maintenance(f"setup.sh terminato con errore (rc={rc})")
        except Exception as e:
            maintenance["status"] = "error"; maintenance["error"] = str(e); log_maintenance(f"Errore esecuzione setup: {e}")

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "status": maintenance["status"]}

@app.post("/maintenance/fix_permissions")
def api_fix_permissions():
    """Tenta di correggere ownership/permessi su APP_DIR per consentire l'update.

    Linux: usa chown/setfacl (richiede sudoers configurato da setup.sh).
    Windows: usa takeown/icacls per garantire permessi di modifica su APP_DIR.
    Non riavvia la macchina.
    """
    if maintenance["status"] == "running":
        return JSONResponse(status_code=409, content={"ok": False, "error": "maintenance già in esecuzione"})
    maintenance["status"] = "running"; maintenance["error"] = None
    log_maintenance("Fix permissions su APP_DIR…")

    app_dir = str(APP_DIR)
    app_user = os.environ.get("APP_USER", "video")
    app_group = os.environ.get("APP_GROUP", "video")

    def worker():
        try:
            def _run(cmd: list[str], timeout: float = 30.0) -> None:
                log_maintenance(f"Eseguo: {' '.join(cmd)}")
                try:
                    res = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, text=True)
                except subprocess.TimeoutExpired:
                    log_maintenance(f"Timeout eseguendo {' '.join(cmd)}")
                    raise
                except FileNotFoundError as exc:
                    log_maintenance(f"Comando non trovato: {cmd[0]} ({exc})")
                    raise
                except Exception as exc:
                    log_maintenance(f"Errore avviando {' '.join(cmd)}: {exc}")
                    raise
                output = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
                if output:
                    lines = output.splitlines()
                    if len(lines) > 12:
                        log_maintenance("Output (ultime 10 righe):")
                        for line in lines[-10:]:
                            log_maintenance(line)
                    else:
                        for line in lines:
                            log_maintenance(line)
                if res.returncode != 0:
                    raise RuntimeError(f"rc={res.returncode}")

            if os.name == "nt":
                # Determina gli account da autorizzare (supporta SID via prefisso *).
                principals: list[str] = []
                for env_name in ("APP_WINDOWS_ACCOUNT", "APP_WINDOWS_ACCOUNTS", "APP_USER", "APP_GROUP"):
                    val = os.environ.get(env_name)
                    if not val:
                        continue
                    for token in val.replace(";", ",").split(","):
                        token = token.strip()
                        if token:
                            principals.append(token)
                # Aggiungi SID noti per Users e Authenticated Users per compatibilità con sistemi localizzati
                principals.extend(["*S-1-5-32-545", "*S-1-5-11"])
                # Mantieni ordine e rimuovi duplicati
                seen: set[str] = set()
                ordered_principals = []
                for p in principals:
                    if p not in seen:
                        seen.add(p)
                        ordered_principals.append(p)
                if not ordered_principals:
                    ordered_principals = ["*S-1-5-32-545"]

                _run(["takeown", "/F", app_dir, "/R", "/D", "Y"], timeout=120.0)
                _run(["icacls", app_dir, "/inheritance:e"], timeout=60.0)

                grants_ok = False
                for principal in ordered_principals:
                    cmd = ["icacls", app_dir, "/grant", f"{principal}:(OI)(CI)M", "/T", "/C"]
                    try:
                        _run(cmd, timeout=180.0)
                        grants_ok = True
                    except Exception as exc:
                        log_maintenance(f"Grant fallito per {principal}: {exc}")
                try:
                    _run(["icacls", app_dir, "/grant", "Administrators:(OI)(CI)F", "/T", "/C"], timeout=180.0)
                except Exception as exc:
                    log_maintenance(f"Grant fallito per Administrators: {exc}")
                if not grants_ok:
                    raise RuntimeError("Nessun grant applicato")
                maintenance["status"] = "ok"
                log_maintenance("Permessi aggiornati (Windows).")
            else:
                cmds = [
                    ["sudo", "-n", "chown", "-R", f"{app_user}:{app_group}", app_dir],
                    ["sudo", "-n", "setfacl", "-R", "-m", f"u:{app_user}:rwx,g:{app_group}:rwx", app_dir],
                    ["sudo", "-n", "setfacl", "-dR", "-m", f"u:{app_user}:rwx,g:{app_group}:rwx", app_dir],
                ]
                for cmd in cmds:
                    _run(cmd, timeout=30.0)
                maintenance["status"] = "ok"
                log_maintenance("Permessi corretti.")
        except Exception as e:
            maintenance["status"] = "error"; maintenance["error"] = str(e)
        
    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "status": maintenance["status"]}

@app.get("/maintenance/status")
def api_maintenance_status():
    return {"ok": True, "status": maintenance["status"], "error": maintenance["error"], "last_logs": maintenance["log"][-50:]}

@app.post("/system/reboot")
def system_reboot():
    print("[REBOOT] Richiesta reboot di sistema", flush=True)
    result = {"ok": False, "message": "", "attempts": []}

    def worker():
        for cmd in _iter_reboot_cmds():
            try:
                print(f"[REBOOT] Eseguo: {' '.join(cmd)}", flush=True)
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, text=True)
                print("[REBOOT] Comando inviato con successo", flush=True)
                GLib.idle_add(main_loop.quit)
                result.update({"ok": True, "message": "Reboot in corso", "attempts": []})
                return
            except subprocess.CalledProcessError as exc:
                msg = (exc.stderr or exc.stdout or "").strip()
                print(f"[REBOOT] Comando fallito ({' '.join(cmd)}): {msg}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": msg})
            except subprocess.TimeoutExpired:
                print(f"[REBOOT] Timeout eseguendo {' '.join(cmd)}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": "timeout"})
            except FileNotFoundError:
                print(f"[REBOOT] Comando non trovato: {cmd[0]}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": "not_found"})

            except Exception as exc:
                print(f"[REBOOT] Errore imprevisto con {' '.join(cmd)}: {exc}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": str(exc)})
        result.update({"ok": False, "message": "Nessun comando reboot accettato"})

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse(status_code=202, content={"ok": True, "message": "Reboot richiesto"})

@app.get("/system/disk")
def system_disk():
    """Ritorna spazio disco per APP_DIR, MEDIA_DIR e /tmp."""
    def _entry(p: Path) -> dict:
        try:
            usage = shutil.disk_usage(str(p))
            total = int(usage.total)
            used = int(usage.used)
            free = int(usage.free)
            percent = 0.0 if total == 0 else (used * 100.0 / total)
            return {"path": str(p), "total": total, "used": used, "free": free, "percent": percent}
        except Exception as exc:
            return {"path": str(p), "error": str(exc)}
    payload = {
        "ok": True,
        "paths": {
            "app_dir": _entry(APP_DIR),
            "media_dir": _entry(MEDIA_DIR),
            "tmp": _entry(Path("/tmp")),
        },
    }
    return payload

@app.get("/system/egl_diagnostics")
def system_egl_diagnostics():
    """Diagnostica rapida per EGL/KMS su questo dispositivo.
    Ritorna presenza /dev/dri, moduli vc4/v3d, gruppi utente e variabili d'ambiente rilevanti.
    """
    try:
        dri = []
        try:
            d = Path("/dev/dri")
            if d.exists():
                for n in sorted([p.name for p in d.iterdir()]):
                    dri.append(n)
        except Exception:
            pass
        ok, missing = _egl_preconditions_ok()
        env = {
            "DISPLAY": os.environ.get("DISPLAY", ""),
            "EGL_PLATFORM": os.environ.get("EGL_PLATFORM", ""),
            "GBM_DRIVERS_PATH": os.environ.get("GBM_DRIVERS_PATH", ""),
            "OFF_USE_EGL": os.environ.get("OFF_USE_EGL", ""),
            "OF_USE_EGLWINDOW": os.environ.get("OF_USE_EGLWINDOW", ""),
        }
        mods_txt = ""
        try:
            with open("/proc/modules", "r") as f:
                mods_txt = f.read()
        except Exception:
            pass
        return {
            "ok": True,
            "preconditions_ok": ok,
            "missing": missing,
            "dri_nodes": dri,
            "modules_present": {
                "vc4": ("vc4" in mods_txt),
                "v3d": ("v3d" in mods_txt),
            },
            "env": env,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.post("/system/service/restart")
def system_service_restart(name: Optional[str] = Query(None)):
    """Riavvia il servizio systemd dell'applicazione in background.

    Usa APP_SERVICE dall'ambiente se presente, altrimenti "headless-player.service".
    Risponde 202 immediatamente; il processo potrebbe essere terminato dal restart.
    
    NOTA: Per funzionare, l'utente del servizio (es. 'video') deve avere permessi sudo
    senza password per systemctl restart. Creare file in /etc/sudoers.d/:
    echo "video ALL=(ALL) NOPASSWD: /bin/systemctl restart headless-player.service" | sudo tee /etc/sudoers.d/headless-player
    """
    env_name = os.environ.get("APP_SERVICE", "headless-player.service")
    svc = (name or env_name or "headless-player.service").strip()
    if not svc.endswith(".service"):
        svc = f"{svc}.service"
    print(f"[SERVICE] Richiesta restart di {svc}", flush=True)

    def worker():
        # Windows: prova PowerShell, poi SC stop/start, poi NSSM
        if os.name == "nt":
            svc_name = svc.replace(".service", "")
            # 1) PowerShell Restart-Service
            try:
                cmd = [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Try {{ Restart-Service -Name '{svc_name}' -Force -ErrorAction Stop; exit 0 }} Catch {{ Write-Error $_; exit 1 }}",
                ]
                print(f"[SERVICE] Eseguo (Windows): {' '.join(cmd)}", flush=True)
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, text=True)
                print("[SERVICE] Restart inviato con successo (PowerShell)", flush=True)
                return
            except subprocess.CalledProcessError as exc:
                msg = (exc.stderr or exc.stdout or "").strip()
                print(f"[SERVICE] PowerShell Restart-Service fallito: {msg}", flush=True)
            except subprocess.TimeoutExpired:
                print("[SERVICE] Timeout su PowerShell Restart-Service", flush=True)
            except FileNotFoundError:
                print("[SERVICE] powershell non trovato", flush=True)
            except Exception as exc:
                print(f"[SERVICE] Errore PowerShell Restart-Service: {exc}", flush=True)

            # 2) SC stop/start
            try:
                print(f"[SERVICE] Provo 'sc stop {svc_name}'", flush=True)
                subprocess.run(["sc", "stop", svc_name], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, text=True)
            except subprocess.CalledProcessError as exc:
                msg = (exc.stderr or exc.stdout or "").strip()
                print(f"[SERVICE] sc stop fallito: {msg}", flush=True)
            except FileNotFoundError:
                print("[SERVICE] sc.exe non trovato", flush=True)
            except Exception as exc:
                print(f"[SERVICE] Errore sc stop: {exc}", flush=True)
            # attesa breve e start
            try:
                import time as _time
                _time.sleep(2)
            except Exception:
                pass
            try:
                print(f"[SERVICE] Provo 'sc start {svc_name}'", flush=True)
                subprocess.run(["sc", "start", svc_name], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, text=True)
                print("[SERVICE] Restart inviato con successo (sc stop/start)", flush=True)
                return
            except subprocess.CalledProcessError as exc:
                msg = (exc.stderr or exc.stdout or "").strip()
                print(f"[SERVICE] sc start fallito: {msg}", flush=True)
            except FileNotFoundError:
                print("[SERVICE] sc.exe non trovato per start", flush=True)
            except Exception as exc:
                print(f"[SERVICE] Errore sc start: {exc}", flush=True)

            # 3) NSSM restart
            try:
                cmd = ["nssm", "restart", svc_name]
                print(f"[SERVICE] Provo NSSM: {' '.join(cmd)}", flush=True)
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, text=True)
                print("[SERVICE] Restart inviato con successo (NSSM)", flush=True)
                return
            except subprocess.CalledProcessError as exc:
                msg = (exc.stderr or exc.stdout or "").strip()
                print(f"[SERVICE] NSSM restart fallito: {msg}", flush=True)
            except FileNotFoundError:
                print("[SERVICE] nssm non trovato", flush=True)
            except Exception as exc:
                print(f"[SERVICE] Errore NSSM: {exc}", flush=True)

            print("[SERVICE] Nessun metodo di restart ha avuto successo su Windows", flush=True)
            if svc_name.lower() in {"off-player", "off-player.exe"}:
                print("[SERVICE] Comando restart player service ricevuto: riavvio OFF-player", flush=True)
                try:
                    _off_stop()
                    result = _off_start()
                    if result.get("ok"):
                        print(f"[SERVICE] OFF-player riavviato (pid={result.get('pid')})", flush=True)
                    else:
                        print(f"[SERVICE] Riavvio OFF-player fallito: {result.get('error')}", flush=True)
                except Exception as e:
                    print(f"[SERVICE] Riavvio OFF-player generato errore: {e}", flush=True)
            return

        # Linux: systemd/service
        cmds = [
            ["sudo", "-n", "systemctl", "restart", svc],
            ["systemctl", "restart", svc],
            ["sudo", "-n", "service", svc.replace(".service", ""), "restart"],
            ["service", svc.replace(".service", ""), "restart"],
        ]
        for cmd in cmds:
            try:
                print(f"[SERVICE] Eseguo: {' '.join(cmd)}", flush=True)
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, text=True)
                print("[SERVICE] Restart inviato con successo", flush=True)
                return
            except subprocess.CalledProcessError as exc:
                msg = (exc.stderr or exc.stdout or "").strip()
                print(f"[SERVICE] Comando fallito ({' '.join(cmd)}): {msg}", flush=True)
            except subprocess.TimeoutExpired:
                print(f"[SERVICE] Timeout eseguendo {' '.join(cmd)}", flush=True)
            except FileNotFoundError:
                print(f"[SERVICE] Comando non trovato: {cmd[0]}", flush=True)
            except Exception as exc:
                print(f"[SERVICE] Errore imprevisto con {' '.join(cmd)}: {exc}", flush=True)

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse(status_code=202, content={"ok": True, "message": f"Restart richiesto per {svc}"})

@app.get("/logs/cvlc")
def logs_cvlc(lines: int = Query(200, ge=1, le=2000)):
    """Ritorna le ultime N linee del log stderr di cvlc (se disponibile)."""
    path = os.environ.get("CVLC_LOG", "/tmp/cvlc_stderr.log")
    p = Path(path)
    if not p.exists():
        return JSONResponse(status_code=404, content={"ok": False, "error": f"Log non trovato: {path}"})
    try:
        with open(p, "rb") as f:
            try:
                data = f.read()
            except Exception:
                data = b""
        text = data.decode("utf-8", errors="ignore")
        all_lines = text.splitlines()
        tail = all_lines[-int(lines):]
        truncated = len(all_lines) > len(tail)
        return {"ok": True, "path": path, "lines": tail, "size": p.stat().st_size, "truncated": truncated}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.get("/logs/cvlc/stream")
def logs_cvlc_stream(lines: int = Query(50, ge=0, le=2000), format: str = Query("sse")):
    """Stream live del log di CVLC.
    - lines: numero di righe iniziali da tailare prima dello streaming
    - format: "sse" (text/event-stream con 'data:' per riga) oppure "raw" (text/plain)
    """
    path = os.environ.get("CVLC_LOG", "/tmp/cvlc_stderr.log")
    p = Path(path)
    if not p.exists():
        return JSONResponse(status_code=404, content={"ok": False, "error": f"Log non trovato: {path}"})

    async def _gen():
        try:
            with open(p, "rb", buffering=0) as f:
                # Tail iniziale
                if lines and lines > 0:
                    try:
                        data = f.read()
                    except Exception:
                        data = b""
                    text = data.decode("utf-8", errors="ignore")
                    init_lines = text.splitlines()[-int(lines):]
                    for ln in init_lines:
                        if format == "sse":
                            yield ("data: " + ln + "\n\n").encode("utf-8", "ignore")
                        else:
                            yield (ln + "\n").encode("utf-8", "ignore")
                # Segui gli aggiornamenti (tail -f)
                f.seek(0, os.SEEK_END)
                while True:
                    chunk = f.readline()
                    if chunk:
                        try:
                            ln = chunk.decode("utf-8", errors="ignore").rstrip("\n")
                        except Exception:
                            ln = ""
                        if ln:
                            if format == "sse":
                                yield ("data: " + ln + "\n\n").encode("utf-8", "ignore")
                            else:
                                yield (ln + "\n").encode("utf-8", "ignore")
                    else:
                        await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            return
        except Exception:
            # Termina silenziosamente in caso di errore
            return

    media = "text/event-stream" if format == "sse" else "text/plain"
    return StreamingResponse(_gen(), media_type=media)

@app.get("/logs/off")
def logs_off(lines: int = Query(200, ge=1, le=2000)):
    """Ritorna le ultime N linee del log di OFF-player catturate dallo stdout (se in esecuzione)."""
    try:
        tail = list(off_logs)[-int(lines):]
        truncated = len(off_logs) > len(tail)
        return {"ok": True, "lines": tail, "truncated": truncated}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.get("/logs/off/stream")
def logs_off_stream(lines: int = Query(50, ge=0, le=2000), format: str = Query("sse")):
    """Stream live del log di OFF-player (stdout catturato).
    - lines: numero di righe iniziali da tailare prima dello streaming
    - format: "sse" (text/event-stream con 'data:') oppure "raw" (text/plain)
    """

    async def _gen():
        try:
            # Tail iniziale
            snapshot = list(off_logs)
            if lines and lines > 0:
                init = snapshot[-int(lines):]
                for ln in init:
                    if format == "sse":
                        yield ("data: " + ln + "\n\n").encode("utf-8", "ignore")
                    else:
                        yield (ln + "\n").encode("utf-8", "ignore")
            last_len = len(snapshot)
            # Poll per nuovi elementi
            while True:
                await asyncio.sleep(0.3)
                cur = list(off_logs)
                cur_len = len(cur)
                if cur_len < last_len:
                    # deque ha troncato: reset baseline
                    last_len = max(0, cur_len - int(lines or 0))
                if cur_len > last_len:
                    new_lines = cur[last_len:]
                    last_len = cur_len
                    for ln in new_lines:
                        if not ln:
                            continue
                        if format == "sse":
                            yield ("data: " + ln + "\n\n").encode("utf-8", "ignore")
                        else:
                            yield (ln + "\n").encode("utf-8", "ignore")
        except asyncio.CancelledError:
            return
        except Exception:
            return

    media = "text/event-stream" if format == "sse" else "text/plain"
    return StreamingResponse(_gen(), media_type=media)

if __name__ == "__main__":
    # Avvio manuale del server per poter invocare shutdown pulito
    loop_impl = "uvloop" if _UVLOOP_AVAILABLE else "asyncio"
    cfg = Config(app=app, host="0.0.0.0", port=APP_PORT, loop=loop_impl, lifespan="on", log_config=None, log_level="info")
    server = Server(cfg)
    globals()["UVICORN_SERVER"] = server
    server.run()


# .\.venv\bin\activate
