"""Simple HTTP file server used for media and bundle uploads."""

from __future__ import annotations

import posixpath
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from GUI.core.logger import get_logger


_LOG = get_logger(__name__)


class FileServerService:
    """Serve a directory tree over HTTP in the background."""

    def __init__(self, root: Path, port: int) -> None:
        self._root = root
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> None:
        if self._httpd:
            return
        handler = self._build_handler()
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self._port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="file-server", daemon=True)
        self._thread.start()
        _LOG.info("File server serving %s on port %s", self._root, self._port)

    def stop(self) -> None:
        if not self._httpd:
            return
        self._httpd.shutdown()
        if self._thread:
            self._thread.join(timeout=2)
        self._httpd.server_close()
        self._httpd = None
        self._thread = None

    def _build_handler(self) -> type[SimpleHTTPRequestHandler]:
        root = self._root

        class Handler(SimpleHTTPRequestHandler):
            def translate_path(self, path: str) -> str:  # noqa: D401
                rel_path = posixpath.normpath(unquote(path))
                parts = [part for part in rel_path.split("/") if part]
                resolved = root
                for part in parts:
                    resolved = resolved / part
                return str(resolved)

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                # Evita rumore in console; lascia solo un debug opzionale
                try:
                    _LOG.debug("SERVE %s - " + format, self.address_string(), *args)
                except Exception:
                    pass

            def copyfile(self, source, outputfile) -> None:
                # Proteggi da ConnectionResetError/BrokenPipeError quando il client chiude la connessione durante l'invio
                try:
                    super().copyfile(source, outputfile)
                except (ConnectionResetError, BrokenPipeError):
                    # Client ha chiuso la connessione: ignora
                    pass

        return Handler
