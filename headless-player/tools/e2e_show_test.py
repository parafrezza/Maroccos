"""
End-to-end test: push assets to players, apply playlist (preload first), wait readiness, then trigger faststart go in sync.

Defaults:
- Targets: 192.168.1.167, 192.168.1.204 (override via --targets)
- Media: headless-player/media/testbars_10s.mp4, headless-player/media/testbars_10_TC.mp4 (override via --media)
- Clear-before: true by default (disable via --no-clear)
- Start delay: 5s (override via --delay)

Usage (PowerShell):
  python headless-player/tools/e2e_show_test.py --targets 192.168.1.167 192.168.1.204 --delay 5

This script prints readiness with device IP and name to match the GUI list.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from pathlib import Path
import socket
import threading
from typing import Iterable, Optional, List
import http.server
import socketserver

import requests


DEFAULT_PORT = 8080
DEFAULT_UDP_PORT = 7788
TIMEOUT = (2.0, 8.0)  # (connect, read)


def _url(host: str, path: str) -> str:
	if host.startswith("http://") or host.startswith("https://"):
		base = host.rstrip("/")
	else:
		base = f"http://{host}:{DEFAULT_PORT}"
	if not path.startswith("/"):
		path = "/" + path
	return base + path


def _req(host: str, method: str, path: str, *, params=None, json_body=None, files=None, timeout=TIMEOUT):
	url = _url(host, path)
	try:
		r = requests.request(method=method.upper(), url=url, params=params, json=json_body, files=files, timeout=timeout)
		r.raise_for_status()
		try:
			return True, r.json()
		except Exception:
			return True, {"ok": True, "text": r.text}
	except requests.RequestException as e:
		return False, {"ok": False, "error": str(e), "url": url}


def stop(host: str):
	ok, data = _req(host, "POST", "/stop")
	return ok, data


def status(host: str):
	ok, data = _req(host, "GET", "/status")
	return ok, data


def healthz(host: str):
	ok, data = _req(host, "GET", "/healthz")
	return ok, data


def maintenance_fix_permissions(host: str):
	ok, data = _req(host, "POST", "/maintenance/fix_permissions")
	return ok, data


def framework_get(host: str):
	ok, data = _req(host, "GET", "/framework")
	return ok, data


def change_framework(host: str, name: str):
	body = {"name": name}
	ok, data = _req(host, "POST", "/change_framework", json_body=body)
	return ok, data


def wait_framework(host: str, name: str, timeout_s: float = 6.0, interval: float = 0.25):
	deadline = time.time() + max(0.1, timeout_s)
	last = None
	name = str(name)
	while time.time() < deadline:
		ok, data = framework_get(host)
		if ok:
			last = data
			cur = (data.get("current") or data.get("framework") or "").lower()
			if cur == name.lower():
				return True, data
		time.sleep(interval)
	return False, last or {}


def media_clear(host: str):
	ok, data = _req(host, "POST", "/media/clear", params={"confirm": 1})
	return ok, data


def upload_asset(host: str, local_path: Path, filename: Optional[str] = None):
	if not filename:
		filename = local_path.name
	files = {"file": (filename, open(local_path, "rb"))}
	try:
		ok, data = _req(host, "POST", "/upload_asset", params={"filename": filename}, files=files, timeout=(3.0, 30.0))
		return ok, data
	finally:
		try:
			files["file"][1].close()
		except Exception:
			pass


def playlist_apply(host: str, items: list[str], loop: bool = True):
	body = {"items": items, "loop": loop}
	ok, data = _req(host, "POST", "/playlist/apply", json_body=body)
	return ok, data


def faststart_go(host: str, *, seconds: float = 1.0, in_time: Optional[float] = None):
	params = {"seconds": seconds}
	if in_time is not None:
		params["in_time"] = in_time
	ok, data = _req(host, "POST", "/faststart/go", params=params)
	return ok, data


def log_udp_subscribe(host: str, listen_host: str, listen_port: int, persist: bool = False):
	body = {"host": listen_host, "port": int(listen_port), "persist": bool(persist)}
	ok, data = _req(host, "POST", "/log/udp/subscribe", json_body=body)
	return ok, data


def log_udp_unsubscribe(host: str, persist: bool = False):
	body = {"persist": bool(persist)}
	ok, data = _req(host, "POST", "/log/udp/unsubscribe", json_body=body)
	return ok, data


def download_asset_post(host: str, url: str, filename: Optional[str] = None):
	body = {"url": url}
	if filename:
		body["filename"] = filename
	ok, data = _req(host, "POST", "/download_asset", json_body=body, timeout=(3.0, 60.0))
	return ok, data


def wait_states(host: str, states: Iterable[str], timeout_s: float = 5.0, interval: float = 0.25):
	deadline = time.time() + max(0.1, timeout_s)
	states = {s.lower() for s in states}
	last = None
	while time.time() < deadline:
		ok, st = status(host)
		if ok:
			last = st
			ps = str(st.get("player_state") or "").lower()
			if ps in states:
				return True, st
		time.sleep(interval)
	return False, last or {}


def wait_ready(host: str, expected_first: Optional[str], timeout_s: float = 30.0, interval: float = 0.5):
	deadline = time.time() + max(0.1, timeout_s)
	last = None
	while time.time() < deadline:
		ok, st = status(host)
		if ok:
			last = st
			if st.get("show_ready"):
				rf = str(st.get("ready_for") or "")
				if not expected_first or not rf or rf.endswith(Path(expected_first).name):
					return True, st
		time.sleep(interval)
	return False, last or {}


def resolve_media_list(default_paths: list[Path]) -> list[Path]:
	out: list[Path] = []
	for p in default_paths:
		if p.exists() and p.is_file():
			out.append(p)
	return out


def ip_and_name(host: str):
	ip = None; name = None
	ok_h, hz = healthz(host)
	if ok_h:
		ip = hz.get("ip")
	ok_s, st = status(host)
	if ok_s:
		name = st.get("name") or st.get("device_name")
	return ip or host, name or ""


class UdpCollector:
	"""Minimal UDP JSON log collector.

	Binds to (bind_host, port) and collects JSON lines from devices using /log/udp/subscribe.
	Stores entries as dicts: { 'recv': local_time, 'msg': payload_dict, 'from': (ip, port) }.
	"""

	def __init__(self, bind_host: str, port: int) -> None:
		self.bind_host = bind_host
		self.port = int(port)
		self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		# Allow quick reuse if re-running
		try:
			self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		except Exception:
			pass
		self._sock.bind((bind_host, int(port)))
		self._stop = threading.Event()
		self._thr: Optional[threading.Thread] = None
		self.events: list[dict] = []

	def start(self) -> None:
		if self._thr and self._thr.is_alive():
			return
		self._thr = threading.Thread(target=self._loop, name=f"udp-log-{self.port}", daemon=True)
		self._thr.start()

	def _loop(self):
		while not self._stop.is_set():
			try:
				self._sock.settimeout(0.5)
				data, addr = self._sock.recvfrom(65536)
				now = time.time()
				try:
					payload = json.loads(data.decode("utf-8", errors="ignore"))
				except Exception:
					payload = {"_raw": data[:64].hex()}
				self.events.append({"recv": now, "msg": payload, "from": addr})
			except socket.timeout:
				continue
			except Exception:
				if not self._stop.is_set():
					continue
		try:
			self._sock.close()
		except Exception:
			pass

	def stop(self) -> None:
		self._stop.set()
		try:
			self._sock.close()
		except Exception:
			pass
		if self._thr:
			try:
				self._thr.join(timeout=1.0)
			except Exception:
				pass

	def snapshot(self) -> list[dict]:
		return list(self.events)


def _infer_local_ip_for(target_host: str) -> str:
	try:
		s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		# doesn't need to be reachable; used to infer the outbound interface
		s.connect((target_host, 9))
		ip = s.getsockname()[0]
		s.close()
		return ip
	except Exception:
		return "127.0.0.1"


def main(argv: list[str]) -> int:
	parser = argparse.ArgumentParser(description="E2E show test: push → apply → ready → faststart go")
	parser.add_argument("--targets", nargs="+", default=["192.168.1.167", "192.168.1.204"], help="Target player IP/hostnames")
	parser.add_argument("--delay", type=float, default=5.0, help="Start delay seconds for faststart/go")
	parser.add_argument("--no-clear", action="store_true", help="Skip media clear before upload")
	parser.add_argument("--media", nargs="*", default=[], help="Explicit media files to upload (defaults to two testbars)")
	parser.add_argument("--loop", action="store_true", help="Set playlist loop on (default true)")
	parser.add_argument("--no-loop", action="store_true", help="Force playlist loop off")
	parser.add_argument("--listen-host", default="", help="UDP bind host for log collection (default: auto)")
	parser.add_argument("--listen-port", type=int, default=DEFAULT_UDP_PORT, help="UDP bind port for log collection")
	parser.add_argument("--no-subscribe", action="store_true", help="Do not subscribe devices to UDP logs")
	parser.add_argument("--verify-timeout", type=float, default=5.0, help="Timeout (s) after GO to confirm state=playing")
	parser.add_argument("--report-json", type=str, default="", help="Optional path to save a JSON report of timings/results")
	parser.add_argument("--report-csv", type=str, default="", help="Optional path to save a CSV summary per device")
	parser.add_argument("--frameworks", nargs="*", default=["mpv", "gst", "cvlc"], help="Frameworks to test in sequence (e.g. mpv gst cvlc)")
	parser.add_argument("--rounds", type=int, default=1, help="How many times to repeat the full sweep")
	parser.add_argument("--interactive", action="store_true", help="Ask visual confirmation prompts after each framework run")
	parser.add_argument("--no-interactive", action="store_true", help="Disable interactive prompts")
	parser.add_argument("--allow-partial", action="store_true", help="Proceed even if not all devices are ready; continue with ready ones")
	args = parser.parse_args(argv)

	# Default media from repo
	repo_root = Path(__file__).resolve().parents[2]
	default_media = [
		# repo_root / "headless-player" / "media" / "testbars_10s.mp4",
		repo_root / "headless-player" / "media" / "testbars_10_TC.mp4",
	]
	media_paths = [Path(m) for m in (args.media if args.media else [])]
	if not media_paths:
		media_paths = resolve_media_list(default_media)
	if not media_paths:
		print("[E2E] Nessun media valido trovato. Specifica --media.", file=sys.stderr)
		return 2
	first_name = media_paths[0].name

	loop_flag = True
	if args.no_loop:
		loop_flag = False
	elif args.loop:
		loop_flag = True

	print(f"[E2E] Targets: {', '.join(args.targets)}")
	print(f"[E2E] Media: {[p.name for p in media_paths]}")
	print(f"[E2E] Clear before: {not args.no_clear}")
	print(f"[E2E] Loop: {loop_flag}")

	# Helpers for per-run report naming
	def _suffix_path(p: str | None, fw: str) -> str | None:
		if not p:
			return None
		path = Path(p)
		stem = path.stem
		suf = path.suffix
		return str(path.with_name(f"{stem}-fw-{fw}{suf}"))

	# Prepare UDP log collection
	listener: Optional[UdpCollector] = None
	listen_ip = args.listen_host.strip()
	if not listen_ip:
		listen_ip = _infer_local_ip_for(args.targets[0])
	if not args.no_subscribe:
		try:
			listener = UdpCollector("0.0.0.0", int(args.listen_port))
			listener.start()
			print(f"[E2E] UDP log collector on {listen_ip}:{args.listen_port}")
		except Exception as e:
			print(f"[E2E] UDP collector error: {e}")
			listener = None
		# Subscribe each device
		for h in args.targets:
			ok, res = log_udp_subscribe(h, listen_ip, int(args.listen_port), persist=False)
			tag = h
			print(f"  - subscribe {tag}: {'ok' if ok else 'FAIL'}")

	# Lightweight HTTP file server (for /download_asset fallback)
	httpd_ref = {"server": None, "port": 0}

	def ensure_http_server(root_dir: Path, host_ip: str, port: int = 8899):
		if httpd_ref["server"] is not None:
			return httpd_ref["port"]
		Handler = http.server.SimpleHTTPRequestHandler
		# Python 3.7+: directory parameter available
		def handler_factory(*args, **kwargs):
			return Handler(*args, directory=str(root_dir), **kwargs)
		try:
			httpd = socketserver.TCPServer(("0.0.0.0", int(port)), handler_factory)
		except OSError:
			# Riprova con porta successiva
			httpd = socketserver.TCPServer(("0.0.0.0", 0), handler_factory)
			port = httpd.server_address[1]
		thr = threading.Thread(target=httpd.serve_forever, daemon=True)
		thr.start()
		httpd_ref["server"] = httpd
		httpd_ref["port"] = port
		print(f"[E2E] HTTP server per download asset su {host_ip}:{port} root={root_dir}")
		return port

	def run_once_for_framework(framework: str) -> int:
		print(f"\n[E2E] === Framework: {framework} ===")
		# Switch framework on all targets
		for h in args.targets:
			ok, res = change_framework(h, framework)
			print(f"  - change_framework {h} -> {framework}: {'ok' if ok else 'FAIL'} {res.get('error','')}")
		for h in args.targets:
			ok, st = wait_framework(h, framework, timeout_s=10.0)
			print(f"  - wait_framework {h}: {'ok' if ok else 'TIMEOUT'} current={st.get('current')}")

		# Fresh per-framework report structure
		report = {
			"started_at": time.time(),
			"framework": framework,
			"targets": [],
			"media": [p.name for p in media_paths],
			"delay": float(args.delay),
		}
		for h in args.targets:
			report["targets"].append({
				"host_called": h,
				"device_ip": None,
				"device_name": None,
				"steps": {},
			})

		# 1) Stop all
		print("[E2E] STOP tutti i player…")
		with cf.ThreadPoolExecutor(max_workers=min(8, len(args.targets))) as ex:
			list(ex.map(lambda h: stop(h), args.targets))

		# Attendi stato fermo/pausa breve (alcuni backend possono mettere in pausa su nero)
		for idx, h in enumerate(args.targets):
			ok, st = wait_states(h, states=("stopped", "paused"), timeout_s=5.0)
			dev_ip, name = ip_and_name(h)
			# Mostra l'host chiamato (h) e l'IP riportato dal device
			left = f"{h} -> {dev_ip}"
			print(f"  - {left} {('[' + name + ']') if name else ''}: state={st.get('player_state')} ok={ok}")
			# Update report identity and stop step
			report["targets"][idx]["device_ip"] = dev_ip
			report["targets"][idx]["device_name"] = name
			report["targets"][idx]["steps"]["stop"] = {"ok": bool(ok), "at": time.time(), "state": st.get("player_state")}

		# 2) Clear media (optional)
		if not args.no_clear:
			print("[E2E] Svuoto media sui device…")
			for h in args.targets:
				ok, res = media_clear(h)
				if not ok:
					print(f"  - {h}: clear fallito: {res.get('error')}")
				else:
					print(f"  - {h}: clear ok")

		# 3) Upload assets to each host (in parallel)
		print("[E2E] Upload asset sui device…")
		def _upload_all_for_host(h: str):
			results = []
			for p in media_paths:
				ok, res = upload_asset(h, p, filename=p.name)
				if not ok:
					# Prova fix-permessi anche su 500 generico e ritenta una volta
					maintenance_fix_permissions(h)
					time.sleep(0.5)
					ok, res = upload_asset(h, p, filename=p.name)
				# Fallback: se ancora fallisce, prova /download_asset via HTTP locale
				if not ok:
					try:
						port = ensure_http_server(p.parent, listen_ip)
						url = f"http://{listen_ip}:{port}/{p.name}"
						ok, res = download_asset_post(h, url, filename=p.name)
					except Exception as e:
						res = {"ok": False, "error": str(e)}
				results.append((p.name, ok, res))
			return h, results

		uploads = []
		with cf.ThreadPoolExecutor(max_workers=min(8, len(args.targets))) as ex:
			for h, results in ex.map(_upload_all_for_host, args.targets):
				uploads.append((h, results))
		for h, results in uploads:
			dev_ip, name = ip_and_name(h)
			for fname, ok, res in results:
				left = f"{h} -> {dev_ip}"
				print(f"  - {left} {('[' + name + ']') if name else ''}: {fname} -> {'ok' if ok else 'FAIL'} {res.get('error','')}")
			# Update report for this host uploads
			idx = args.targets.index(h)
			report["targets"][idx]["steps"]["upload"] = {
				"at": time.time(),
				"files": [{"name": fname, "ok": bool(ok)} for fname, ok, _ in results],
			}

		# 4) Apply playlist on each host
		print("[E2E] Applico playlist e preparo il primo elemento (faststart)…")
		items = [p.name for p in media_paths]
		for h in args.targets:
			ok, res = playlist_apply(h, items, loop=loop_flag)
			dev_ip, name = ip_and_name(h)
			left = f"{h} -> {dev_ip}"
			prepared_name = Path(res.get('prepared','') or '').name if ok else None
			if ok:
				print(f"  - {left} {('[' + name + ']') if name else ''}: prepared={prepared_name}")
			else:
				print(f"  - {left} {('[' + name + ']') if name else ''}: FAIL apply: {res.get('error')}")
			# report
			idx = args.targets.index(h)
			report["targets"][idx]["steps"]["apply"] = {"ok": bool(ok), "at": time.time(), "prepared": prepared_name}

		# 5) Wait readiness on all
		print("[E2E] Attendo readiness show (tutti i device)…")
		all_ready = True
		ready_hosts: list[str] = []
		for h in args.targets:
			ok, st = wait_ready(h, expected_first=first_name, timeout_s=30.0)
			dev_ip, name = ip_and_name(h)
			rf = st.get("ready_for")
			left = f"{h} -> {dev_ip}"
			print(f"  - {left} {('[' + name + ']') if name else ''}: ready={st.get('show_ready')} for={Path(str(rf)).name if rf else '-'} ok={ok}")
			idx = args.targets.index(h)
			report["targets"][idx]["steps"]["ready"] = {"ok": bool(ok), "at": time.time(), "for": Path(str(rf)).name if rf else None}
			if ok:
				ready_hosts.append(h)
			else:
				all_ready = False

		# Determina i target per GO/VERIFY
		if all_ready:
			go_targets = list(args.targets)
		else:
			if args.allow_partial and ready_hosts:
				print(f"[E2E] Alcuni device non sono pronti, proseguo con: {', '.join(ready_hosts)}")
				go_targets = list(ready_hosts)
				report["partial_ready"] = True
				report["go_targets"] = list(go_targets)
			else:
				print("[E2E] NON tutti i device sono pronti. Interrompo qui (salvo report).")
				# Save partial report before aborting this framework
				report["finished_at"] = time.time()
				report["aborted"] = "not_ready"
				rj = _suffix_path(args.report_json, framework)
				rc = _suffix_path(args.report_csv, framework)
				if rj:
					try:
						Path(rj).parent.mkdir(parents=True, exist_ok=True)
						with open(rj, "w", encoding="utf-8") as f:
							json.dump(report, f, ensure_ascii=False, indent=2)
						print(f"[E2E] Report JSON salvato in: {rj}")
					except Exception as e:
						print(f"[E2E] Errore salvataggio report JSON: {e}")
				if rc:
					try:
						import csv
						Path(rc).parent.mkdir(parents=True, exist_ok=True)
						with open(rc, "w", newline="", encoding="utf-8") as f:
							w = csv.writer(f)
							w.writerow(["host_called", "device_ip", "device_name", "stop_ok", "apply_ok", "ready_ok", "go_ok", "verify_ok"])
							for t in report["targets"]:
								steps = t.get("steps", {})
								w.writerow([
									t.get("host_called"),
									t.get("device_ip"),
									t.get("device_name"),
									steps.get("stop", {}).get("ok"),
									steps.get("apply", {}).get("ok"),
									steps.get("ready", {}).get("ok"),
									steps.get("go", {}).get("ok"),
									steps.get("verify_playing", {}).get("ok"),
								])
						print(f"[E2E] Report CSV salvato in: {rc}")
					except Exception as e:
						print(f"[E2E] Errore salvataggio report CSV: {e}")
				return 3

		# 6) Trigger faststart go synchronously with delay
		delay = max(0.0, float(args.delay))
		in_time = time.time() + delay
		print(f"[E2E] LANCIO SHOW tra {delay:.1f}s (epoch={in_time:.3f})…")
		for h in go_targets:
			ok, res = faststart_go(h, seconds=1.0, in_time=in_time)
			dev_ip, name = ip_and_name(h)
			left = f"{h} -> {dev_ip}"
			print(f"  - {left} {('[' + name + ']') if name else ''}: faststart_go -> {'ok' if ok else 'FAIL'}")
			idx = args.targets.index(h)
			report["targets"][idx]["steps"]["go"] = {"ok": bool(ok), "at": time.time(), "in_time": in_time}

		# 7) Verify playing state shortly after GO
		verify_deadline = in_time + max(0.0, float(args.verify_timeout))
		now = time.time()
		if now < in_time:
			time.sleep(in_time - now + 0.1)
		print(f"[E2E] Verifica stato=playing entro {verify_deadline - time.time():.1f}s…")
		for h in go_targets:
			ok, st = wait_states(h, states=("playing",), timeout_s=max(0.2, verify_deadline - time.time()))
			dev_ip, name = ip_and_name(h)
			left = f"{h} -> {dev_ip}"
			print(f"  - {left} {('[' + name + ']') if name else ''}: state={st.get('player_state')} ok={ok}")
			idx = args.targets.index(h)
			report["targets"][idx]["steps"]["verify_playing"] = {"ok": bool(ok), "at": time.time(), "state": st.get("player_state")}

		# Optional: show a brief digest of collected UDP log events
		if listener:
			time.sleep(1.0)
			events = listener.snapshot()
			print("[E2E] UDP logs (recent):")
			# Group by device name if present
			for ev in events[-50:]:
				msg = ev.get("msg") or {}
				dev = msg.get("device") or "?"
				t_dev = msg.get("t")
				kind = msg.get("kind") or "log"
				level = msg.get("level") or "INFO"
				text = msg.get("msg") or ""
				recv = ev.get("recv")
				print(f"  - {dev} | recv={recv:.3f} dev_t={t_dev} | {level}/{kind}: {text}")
			# attach to report
			report["udp_logs_count"] = len(events)
			try:
				for h in args.targets:
					log_udp_unsubscribe(h, persist=False)
			except Exception:
				pass
			try:
				listener.stop()
			except Exception:
				pass

		# Save report if requested (per-framework suffix)
		report["finished_at"] = time.time()
		rj = _suffix_path(args.report_json, framework)
		rc = _suffix_path(args.report_csv, framework)
		if rj:
			try:
				Path(rj).parent.mkdir(parents=True, exist_ok=True)
				with open(rj, "w", encoding="utf-8") as f:
					json.dump(report, f, ensure_ascii=False, indent=2)
				print(f"[E2E] Report JSON salvato in: {rj}")
			except Exception as e:
				print(f"[E2E] Errore salvataggio report JSON: {e}")
		if rc:
			try:
				import csv
				Path(rc).parent.mkdir(parents=True, exist_ok=True)
				with open(rc, "w", newline="", encoding="utf-8") as f:
					w = csv.writer(f)
					w.writerow(["host_called", "device_ip", "device_name", "stop_ok", "apply_ok", "ready_ok", "go_ok", "verify_ok"])
					for t in report["targets"]:
						steps = t.get("steps", {})
						w.writerow([
							t.get("host_called"),
							t.get("device_ip"),
							t.get("device_name"),
							steps.get("stop", {}).get("ok"),
							steps.get("apply", {}).get("ok"),
							steps.get("ready", {}).get("ok"),
							steps.get("go", {}).get("ok"),
							steps.get("verify_playing", {}).get("ok"),
						])
				print(f"[E2E] Report CSV salvato in: {rc}")
			except Exception as e:
				print(f"[E2E] Errore salvataggio report CSV: {e}")

		# Interactive prompts (optional) and save to report
		do_interactive = args.interactive and not args.no_interactive
		if do_interactive:
			try:
				ans1 = input("Riccardo, hai visto il fade-in (scomparsa overlay)? (y/n) ").strip().lower()
			except Exception:
				ans1 = ""
			try:
				ans2 = input("Riccardo, sono partiti in sincrono? (y/n) ").strip().lower()
			except Exception:
				ans2 = ""
			print(f"[E2E] Conferme: fade_in={ans1 or '-'} sync_start={ans2 or '-'}")
			report["operator_confirmations"] = {
				"fade_in_raw": ans1,
				"sync_start_raw": ans2,
				"fade_in_yes": ans1 in ("y", "s"),
				"sync_start_yes": ans2 in ("y", "s"),
			}

		print("[E2E] Fatto.")
		return 0

	# Sweep frameworks/rounds
	rounds = max(1, int(args.rounds or 1))
	frameworks: List[str] = [str(x) for x in (args.frameworks or []) if str(x).strip()]
	for r in range(1, rounds + 1):
		print(f"\n[E2E] ===== Round {r}/{rounds} =====")
		for fw in frameworks:
			try:
				run_once_for_framework(fw)
			except KeyboardInterrupt:
				print("[E2E] Interrotto da tastiera.")
				return 130
			except Exception as e:
				print(f"[E2E] Errore run fw={fw}: {e}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main(sys.argv[1:]))

