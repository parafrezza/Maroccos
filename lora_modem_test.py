#!/usr/bin/env python3
"""
lora_speed_test.py - Versione corretta per evitare conflitti

Test per determinare la velocità massima di trasmissione dei modem EBYTE E90-DTU.
Invia pacchetti con intervalli progressivamente più piccoli e misura i tempi di risposta.

esempio di utilizzo:
--mode scan --auto-expand  --verbose 3 --max-loss 100.0
--mode roundrobin_opt --payload-mode bin -v 1 --auto-expand --verbose 2

"""

import argparse
import socket
import threading
import time
import statistics
from datetime import datetime
from collections import defaultdict
import struct

# === COLORI TERMINALE (ANSI) ===
try:
    from colorama import init as _colorama_init, Fore, Style
    _colorama_init(autoreset=False)
    C_OK    = Fore.GREEN
    C_TX    = Fore.CYAN
    C_RX    = Fore.GREEN
    C_ERR   = Fore.RED
    C_WARN  = Fore.YELLOW
    C_INFO  = Fore.WHITE
    C_STAT  = Fore.MAGENTA
    C_DIM   = Style.DIM
    C_RST   = Style.RESET_ALL
    _COLOR_ENABLED = True
except Exception as _e:
    print("[INFO] Colori disabilitati:", _e, "- installa con: pip install colorama")
    C_OK = C_TX = C_RX = C_ERR = C_WARN = C_INFO = C_STAT = C_DIM = C_RST = ""
    _COLOR_ENABLED = False

def color_tag(tag):
    return {
        "TX": C_TX,
        "RX": C_RX,
        "ERR": C_ERR,
        "WARN": C_WARN,
        "INFO": C_INFO,
        "STAT": C_STAT,
        "OK": C_OK,
    }.get(tag, C_INFO)

def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def cprint(tag, msg):
    print(f"{color_tag(tag)}[{ts()}] [{tag}]{C_RST} {msg}")

# Aggiungi FACOLTATIVO: mapping livello -> etichetta
_LOG_LEVEL_TAG = {0:"I",1:"V",2:"VV"}

def safe_int(x, default=None):
    try:
        return int(x)
    except:
        return default

MAGIC_BIN = 0xA5

# === Helper stabilità (aggiungi se non già presenti) ===
def _stable(res, loss_max, rtt_max):
    return (res and res.get('loss') is not None and
            res['loss'] <= loss_max and
            res.get('avg_rtt') is not None and
            res['avg_rtt'] <= rtt_max)

def _stability_score(res, loss_max, rtt_max):
    if not res or res.get('avg_rtt') is None:
        return 0.0
    s_loss = max(0.0, 1.0 - (res['loss'] / loss_max)) if loss_max > 0 else 0.0
    s_rtt  = max(0.0, 1.0 - (res['avg_rtt'] / rtt_max)) if rtt_max > 0 else 0.0
    return 0.6 * s_loss + 0.4 * s_rtt

class SpeedTester:
    def __init__(self, listen_ip, listen_port, target_ip, target_port, expect_from, payload_mode="text"):
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.target_ip = target_ip
        self.target_port = target_port
        self.expect_from = expect_from
        self.payload_mode = payload_mode  # "text" | "bin"
        self.sent_packets = {}
        self.received_packets = {}
        self.results = defaultdict(list)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()

    def listener(self):
        """Listener thread per ricevere risposte"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.listen_ip, self.listen_port))
        except OSError as e:
            cprint("ERR", f"bind {self.listen_ip}:{self.listen_port} -> {e}. Prova --listen-ip 0.0.0.0")
            return
        cprint("INFO", f"In ascolto su {self.listen_ip}:{self.listen_port}")
        sock.settimeout(1.0)

        while not self.stop_event.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception as e:
                cprint("ERR", f"Eccezione socket: {e}")
                continue

            src_ip, src_port = addr
            cprint("RX", f"RAW da {src_ip}:{src_port} -> {data!r}")

            if self.expect_from and src_ip != self.expect_from:
                cprint("WARN", f"Scartato IP inatteso: {src_ip}")
                continue

            # Autodetect binario
            seq_num = None
            if len(data) >= 7 and data[0] == MAGIC_BIN:
                # formato binario
                try:
                    _, seq_num, ts_sent = struct.unpack(">BHI", data[:7])
                    # niente uso diretto di ts_sent qui, solo seq
                except struct.error:
                    cprint("WARN", f"Payload binario malformato len={len(data)}")
                    continue
            else:
                # fallback testo
                if b'#' not in data:
                    continue
                try:
                    text = data.decode('utf-8', errors='replace')
                except:
                    continue
                if '#' not in text:
                    continue
                for token in text.replace('#', ' #').split():
                    if token.startswith('#'):
                        candidate = safe_int(token[1:])
                        if candidate is not None:
                            seq_num = candidate
                            break
                if seq_num is None:
                    continue

            receive_time = time.time()
            with self.lock:
                if seq_num in self.sent_packets:
                    send_time = self.sent_packets[seq_num]
                    dt_ms = (receive_time - send_time) * 1000
                    self.received_packets[seq_num] = receive_time
                    cprint("OK", f"seq #{seq_num} RTT={dt_ms:.1f}ms")
                else:
                    cprint("WARN", f"seq #{seq_num} NON ATTESO (tardivo/conflitto)")

        sock.close()
            
    def test_interval(self, interval, packet_count=5):
        cprint("STAT", f"=== TEST interval {interval:.3f}s ({packet_count} pkt) mode={self.payload_mode} ===")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        start_seq = int(time.time() * 1000) % 65536  # per bin uint16

        with self.lock:
            self.sent_packets.clear()
            self.received_packets.clear()

        for i in range(packet_count):
            seq = (start_seq + i) & 0xFFFF
            if self.payload_mode == "bin":
                # 7 byte payload
                ts_low = int(time.time() * 1000) & 0xFFFFFFFF
                payload = struct.pack(">BHI", MAGIC_BIN, seq, ts_low)
            else:
                payload = f"PING #{seq} T={ts()}".encode('utf-8')
            t_send = time.time()
            with self.lock:
                self.sent_packets[seq] = t_send
            try:
                sock.sendto(payload, (self.target_ip, self.target_port))
                cprint("TX", f"seq #{seq} ({'bin' if self.payload_mode=='bin' else 'txt'} len={len(payload)})")
            except Exception as e:
                cprint("ERR", f"invio seq #{seq}: {e}")
            if i < packet_count - 1:
                time.sleep(interval)

        extra_wait = max(3.0, interval * packet_count + 1.0)
        cprint("INFO", f"Attendo risposte {extra_wait:.1f}s...")
        time.sleep(extra_wait)
        sock.close()

        response_times = []
        with self.lock:
            for seq, t_send in self.sent_packets.items():
                if seq in self.received_packets:
                    rtt = (self.received_packets[seq] - t_send) * 1000
                    response_times.append(rtt)

        sent = packet_count
        recv = len(response_times)
        loss = (sent - recv) / sent * 100.0

        if response_times:
            avg_rt = sum(response_times)/len(response_times)
            min_rt = min(response_times)
            max_rt = max(response_times)
            tag = "OK" if loss < 5 else "WARN"
            cprint(tag, f"Risultati: sent={sent} recv={recv} loss={loss:.1f}%")
            cprint("STAT", f"RTT ms: avg={avg_rt:.1f} min={min_rt:.1f} max={max_rt:.1f}")
        else:
            avg_rt = min_rt = max_rt = None
            cprint("ERR", f"Risultati: sent={sent} recv={recv} loss={loss:.1f}% (nessuna risposta)")

        return {
            "interval": interval,
            "sent": sent,
            "received": recv,
            "loss_rate": loss,
            "avg_response_time": avg_rt,
            "min_response_time": min_rt,
            "max_response_time": max_rt
        }
    
    def run_speed_test(self, start_interval=3.0, min_interval=0.5, step_factor=0.8, max_loss_rate=20.0):
        """Esegue il test completo con intervalli progressivamente più piccoli"""
        
        # Avvia listener
        rx_thread = threading.Thread(target=self.listener, daemon=True)
        rx_thread.start()
        time.sleep(2)  # Attendi che il listener si avvii completamente
        
        results = []
        current_interval = start_interval
        
        print(f"[{ts()}] Inizio speed test: da {start_interval}s a {min_interval}s")
        print(f"[{ts()}] Criterio di stop: perdita pacchetti > {max_loss_rate}%")
        print(f"[{ts()}] Fattore riduzione: {step_factor}")
        
        try:
            while current_interval >= min_interval:
                result = self.test_interval(current_interval, packet_count=5)
                results.append(result)
                
                # Se la perdita è troppo alta, fermati
                if result['loss_rate'] > max_loss_rate:
                    print(f"\n[{ts()}] STOP: Perdita pacchetti troppo alta ({result['loss_rate']:.1f}%)")
                    break
                    
                # Riduci l'intervallo
                current_interval = round(current_interval * step_factor, 3)
                
                # Pausa più lunga tra test per evitare conflitti residui
                time.sleep(3)
                
        except KeyboardInterrupt:
            print(f"\n[{ts()}] Test interrotto dall'utente")
            
        finally:
            self.stop_event.set()
            time.sleep(1)  # Dà tempo al thread di chiudersi
            
        # Stampa riepilogo finale
        self.print_summary(results)
        return results
    
    def print_summary(self, results):
        print(f"\n{C_STAT}{'='*70}{C_RST}")
        cprint("STAT", "RIEPILOGO SPEED TEST")
        print(f"{C_STAT}{'='*70}{C_RST}")
        header = f"{'Intervallo':>12} {'Persi%':>8} {'Tempo avg':>12} {'Tempo min':>12} {'Tempo max':>12} {'Throughput':>12}"
        print(C_INFO + header + C_RST)
        print('-'*70)

        best_interval = None
        best_throughput = 0

        for r in results:
            if r['avg_response_time'] is not None:
                throughput = 1000 / r['interval'] if r['loss_rate'] < 5 else 0
                if throughput > best_throughput:
                    best_throughput = throughput
                    best_interval = r['interval']
                line = (f"{r['interval']:>10.3f}s {r['loss_rate']:>7.1f}% "
                        f"{r['avg_response_time']:>10.1f}ms {r['min_response_time']:>10.1f}ms "
                        f"{r['max_response_time']:>10.1f}ms {throughput:>10.1f} pkt/s")
                color = C_OK if r['loss_rate'] < 5 else (C_WARN if r['loss_rate'] < 20 else C_ERR)
                print(color + line + C_RST)
            else:
                line = (f"{r['interval']:>10.3f}s {r['loss_rate']:>7.1f}% "
                        f"{'N/A':>10} {'N/A':>10} {'N/A':>10} {'0.0':>10} pkt/s")
                print(C_ERR + line + C_RST)

        if best_interval:
            cprint("OK", f"MIGLIORE: interval={best_interval:.3f}s throughput={best_throughput:.1f} pkt/s")
        else:
            cprint("ERR", "Nessun intervallo funzionante trovato!")

class RoundRobinTester:
    def __init__(self, listen_ip, listen_port, target_ip, target_port, expect_from=None, verbose=0, payload_mode="text"):
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.target_ip = target_ip
        self.target_port = int(target_port)
        self.expect_from = expect_from
        self.verbose = verbose
        self.payload_mode = payload_mode

    def _log(self, level, msg):
        if self.verbose >= level:
            tag = "INFO"
            if "loss=" in msg or "BEST" in msg or "DESC" in msg or "BIN" in msg:
                tag = "STAT"
            if "instabile" in msg or "SCAR" in msg:
                tag = "WARN"
            if "ERRO" in msg or "Nessun" in msg:
                tag = "ERR"
            print(f"{color_tag(tag)}[{ts()}] [{_LOG_LEVEL_TAG.get(level, level)}] {msg}{C_RST}")

    def _open_sockets(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rx.bind((self.listen_ip, self.listen_port))
        rx.settimeout(2.0)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._log(2, f"Sockets aperti (listen {self.listen_ip}:{self.listen_port} -> target {self.target_ip}:{self.target_port})")
        return rx, tx

    def _exchange(self, rx_sock, tx_sock, seq, timeout):
        if self.payload_mode == "bin":
            ts_low = int(time.time()*1000) & 0xFFFFFFFF
            payload = struct.pack(">BHI", MAGIC_BIN, seq & 0xFFFF, ts_low)
        else:
            payload = f"RR #{seq} T={ts()}".encode()
        t0 = time.time()
        try:
            tx_sock.sendto(payload, (self.target_ip, self.target_port))
        except Exception as e:
            self._log(0, f"ERRORE invio seq #{seq}: {e}")
            return None
        self._log(2, f"TX seq #{seq} mode={self.payload_mode} len={len(payload)} (timeout ack={timeout:.2f}s)")
        while True:
            remaining = timeout - (time.time() - t0)
            if remaining <= 0:
                return None
            try:
                rx_sock.settimeout(remaining)
                data, addr = rx_sock.recvfrom(4096)
            except socket.timeout:
                return None
            src_ip, _ = addr
            if self.expect_from and src_ip != self.expect_from:
                continue
            # decode
            if self.payload_mode == "bin":
                if len(data) >= 7 and data[0] == MAGIC_BIN:
                    try:
                        _, seq_r, _ts = struct.unpack(">BHI", data[:7])
                    except struct.error:
                        continue
                    if seq_r == (seq & 0xFFFF):
                        rtt = (time.time() - t0) * 1000.0
                        self._log(2, f"RX seq #{seq_r} RTT={rtt:.1f}ms (bin)")
                        return rtt
                else:
                    # se echo remoto converte o aggiunge testo proviamo fallback testuale
                    try:
                        txt = data.decode(errors='replace')
                        if f"#{seq}" in txt:
                            rtt = (time.time() - t0) * 1000.0
                            self._log(2, f"RX seq #{seq} RTT={rtt:.1f}ms (fallback txt)")
                            return rtt
                    except:
                        pass
            else:
                try:
                    txt = data.decode(errors='replace')
                except:
                    continue
                if f"#{seq}" in txt:
                    rtt = (time.time() - t0) * 1000.0
                    self._log(2, f"RX seq #{seq} RTT={rtt:.1f}ms (txt)")
                    return rtt

    # === AGGIUNTO: finestra di test round-robin ===
    def _run_window(self, interval_s, samples, ack_timeout_s):
        self._log(1, f"Finestra interval={interval_s:.3f}s samples={samples} ack_to={ack_timeout_s:.2f}s mode={self.payload_mode}")
        rtts = []
        seq_base = int(time.time()*1000) & 0xFFFF
        rx, tx = self._open_sockets()
        try:
            for i in range(samples):
                seq = (seq_base + i) & 0xFFFF
                rtt = self._exchange(rx, tx, seq, ack_timeout_s)
                if rtt is not None:
                    rtts.append(rtt)
                else:
                    self._log(2, f"Perdita seq #{seq}")
                if i < samples - 1:
                    time.sleep(interval_s)
        finally:
            rx.close()
            tx.close()
        sent = samples
        recv = len(rtts)
        loss = (sent - recv) / sent * 100.0
        if rtts:
            # semplice filtro outlier (IQR)
            r_sorted = sorted(rtts)
            q1 = r_sorted[int(0.25*(len(r_sorted)-1))]
            q3 = r_sorted[int(0.75*(len(r_sorted)-1))]
            iqr = q3 - q1
            upper = q3 + 1.5*iqr
            filtered = [v for v in rtts if v <= upper] or rtts
            avg = sum(filtered)/len(filtered)
            mn = min(filtered)
            mx = max(filtered)
            jitter = (mx - mn) if len(filtered) > 1 else 0.0
        else:
            avg = mn = mx = jitter = None
        res = {
            "interval": interval_s,
            "sent": sent,
            "recv": recv,
            "loss": loss,
            "avg_rtt": avg,
            "min_rtt": mn,
            "max_rtt": mx,
            "jitter": jitter
        }
        self._log(1, f"WIN interval={interval_s:.3f}s loss={loss:.1f}% avg={avg} mn={mn} mx={mx}")
        return res

    # === AGGIUNTO: espansione iniziale opzionale ===
    def _expand_start(self, start_interval, expand_max, samples_per_step,
                      loss_thr, rtt_thr, ack_factor):
        current = start_interval
        last_res = None
        while current <= expand_max:
            ack_to = max(0.5, current * ack_factor)
            res = self._run_window(current, samples_per_step, ack_to)
            st = _stable(res, loss_thr, rtt_thr)
            sc = _stability_score(res, loss_thr, rtt_thr)
            self._log(0, f"EXPAND test {current:.3f}s stable={st} score={sc:.3f}")
            if st:
                return current, res
            last_res = res
            current *= 2.0
        self._log(0, "EXPAND nessun intervallo stabile entro expand-max")
        return start_interval, last_res

    # === AGGIUNTO: scansione semplice (find_min_interval) ===
    def find_min_interval(self,
                          start_interval=5.0,
                          min_interval=0.05,
                          down_factor=0.7,
                          samples_per_step=5,
                          loss_threshold=5.0,
                          rtt_threshold_ms=1500.0,
                          ack_timeout_factor=3.0,
                          auto_expand=False,
                          expand_max=30.0):
        self._log(0, f"MIN-SCAN start={start_interval}s auto_expand={auto_expand}")
        if auto_expand:
            start_interval, start_res = self._expand_start(start_interval, expand_max,
                                                           samples_per_step, loss_threshold,
                                                           rtt_threshold_ms, ack_timeout_factor)
            if not _stable(start_res, loss_threshold, rtt_threshold_ms):
                self._log(0, "MIN-SCAN abort: nessun intervallo stabile iniziale")
                return start_res
        current = start_interval
        last_good = None
        while current >= min_interval:
            ack_to = max(0.5, current * ack_timeout_factor)
            res = self._run_window(current, samples_per_step, ack_to)
            st = _stable(res, loss_threshold, rtt_threshold_ms)
            sc = _stability_score(res, loss_threshold, rtt_threshold_ms)
            self._log(0, f"SCAN interval={current:.3f}s stable={st} score={sc:.3f}")
            if st:
                last_good = res
                current *= down_factor
            else:
                break
        if last_good:
            self._log(0, f"MIN-SCAN best={last_good['interval']:.3f}s loss={last_good['loss']:.1f}% avgRTT={last_good['avg_rtt']}")
            return last_good
        return res  # ultimo test (fallito)

    # === AGGIUNTO: ricerca ottimale (ex find_optimal_interval) ===
    def find_optimal_interval(self,
                              start_interval=5.0,
                              min_interval=0.05,
                              down_factor=0.3,
                              samples_per_step=2,
                              loss_threshold=5.0,
                              rtt_threshold_ms=1500.0,
                              epsilon=0.02,
                              ack_timeout_factor=3.0,
                              refinement_perc=0.05,
                              auto_expand=False,
                              expand_max=30.0):
        self._log(0, f"OPT start={start_interval}s min={min_interval}s auto_expand={auto_expand}")
        if auto_expand:
            start_interval, start_res = self._expand_start(start_interval, expand_max,
                                                           samples_per_step, loss_threshold,
                                                           rtt_threshold_ms, ack_timeout_factor)
            if not _stable(start_res, loss_threshold, rtt_threshold_ms):
                self._log(0, "OPT abort: nessun intervallo stabile iniziale")
                return start_res
        last_good = None
        first_bad = None
        current = start_interval
        # Discesa
        while current >= min_interval:
            ack_to = max(0.5, current * ack_timeout_factor)
            res = self._run_window(current, samples_per_step, ack_to)
            st = _stable(res, loss_threshold, rtt_threshold_ms)
            sc = _stability_score(res, loss_threshold, rtt_threshold_ms)
            self._log(0, f"DESC interval={current:.3f}s stable={st} score={sc:.3f}")
            if st:
                last_good = (current, res)
                current *= down_factor
            else:
                first_bad = (current, res)
                break
        if last_good is None:
            self._log(0, "OPT nessun stabile")
            return first_bad[1] if first_bad else None
        if first_bad is None:
            self._log(0, "OPT mai fallito: uso ultimo buono")
            return last_good[1]

        # Binary search
        low = last_good[0]     # stabile
        high = first_bad[0]    # instabile
        best = last_good
        self._log(0, f"BIN start low={low:.3f} high={high:.3f}")
        while (high - low) > epsilon:
            mid = (low + high) / 2.0
            if mid < min_interval:
                break
            ack_to = max(0.3, mid * ack_timeout_factor)
            res = self._run_window(mid, samples_per_step, ack_to)
            ok = _stable(res, loss_threshold, rtt_threshold_ms)
            sc_mid = _stability_score(res, loss_threshold, rtt_threshold_ms)
            self._log(0, f"BIN mid={mid:.3f}s ok={ok} score={sc_mid:.3f}")
            if ok:
                sc_best = _stability_score(best[1], loss_threshold, rtt_threshold_ms)
                if sc_mid >= sc_best:
                    best = (mid, res)
                low = mid
            else:
                high = mid

        # Raffinamento
        base_int = best[0]
        self._log(0, f"REF around {base_int:.3f}s +/- {refinement_perc*100:.1f}%")
        candidates = [best]
        for f in (1.0 - refinement_perc, 1.0 + refinement_perc):
            cand = max(min_interval, base_int * f)
            ack_to = max(0.3, cand * ack_timeout_factor)
            res = self._run_window(cand, samples_per_step, ack_to)
            if _stable(res, loss_threshold, rtt_threshold_ms):
                candidates.append((cand, res))
        candidates.sort(key=lambda x: (_stability_score(x[1], loss_threshold, rtt_threshold_ms), x[0]))
        best = candidates[-1]
        final_score = _stability_score(best[1], loss_threshold, rtt_threshold_ms)
        self._log(0, f"BEST interval={best[0]:.3f}s loss={best[1]['loss']:.1f}% avgRTT={best[1]['avg_rtt']:.1f}ms score={final_score:.3f}")
        return best[1]

def run_roundrobin_opt(args):
    tgt_ip, tgt_port_str = args.send_to.split(":", 1)
    rr = RoundRobinTester(args.listen_ip, args.listen_port, tgt_ip, int(tgt_port_str),
                          expect_from=(args.expect_from or None),
                          verbose=getattr(args, "verbose", 0),
                          payload_mode=args.payload_mode)
    res = rr.find_optimal_interval(start_interval=args.start_interval,
                                   min_interval=args.min_interval,
                                   down_factor=args.step_factor,
                                   samples_per_step=6,
                                   loss_threshold=5.0,
                                   rtt_threshold_ms=1500.0,
                                   epsilon=0.02,
                                   auto_expand=args.auto_expand,
                                   expand_max=args.expand_max)
    print("Risultato finale:", res)

def run_round_robin_mode(args):
    tgt_ip, tgt_port_str = args.send_to.split(":", 1)
    rr = RoundRobinTester(args.listen_ip, args.listen_port,
                          tgt_ip, int(tgt_port_str),
                          expect_from=(args.expect_from or None),
                          verbose=getattr(args, "verbose", 0),
                          payload_mode=args.payload_mode)
    res = rr.find_min_interval(start_interval=args.start_interval,
                               min_interval=args.min_interval,
                               down_factor=args.step_factor,
                               samples_per_step=5,
                               loss_threshold=5.0,
                               rtt_threshold_ms=1500.0,
                               ack_timeout_factor=3.0,
                               auto_expand=args.auto_expand,
                               expand_max=args.expand_max)
    print(f"\nRisultato finale round-robin: {res}")

def main():
    ap = argparse.ArgumentParser(description="Speed test per modem LoRa EBYTE E90-DTU")
    ap.add_argument("--mode", choices=["scan", "roundrobin", "roundrobin_opt"], default="roundrobin", help="Modalità test")
    ap.add_argument("--listen-ip", default="0.0.0.0", help="IP locale di ascolto")
    ap.add_argument("--listen-port", type=int, default=8888, help="Porta locale di ascolto")
    ap.add_argument("--send-to", default="192.168.10.201:8886", help="Destinazione (IP:PORT)")
    ap.add_argument("--expect-from", default="", help="IP sorgente atteso (vuoto = qualsiasi)")
    ap.add_argument("--start-interval", type=float, default=8.0, help="Intervallo iniziale")
    ap.add_argument("--min-interval", type=float, default=0.05, help="Intervallo minimo")
    ap.add_argument("--step-factor", type=float, default=0.7, help="Fattore riduzione esponenziale")
    ap.add_argument("--max-loss", type=float, default=20.0, help="(solo scan) perdita max")
    ap.add_argument("--auto-expand", action="store_true",
                    help="Espande automaticamente lo start-interval finché non trova uno stabile")
    ap.add_argument("--expand-max", type=float, default=30.0,
                    help="Intervallo massimo da provare in auto espansione (s)")
    ap.add_argument("-v", "--verbose", type=int, default=0, help="0=base 1=verboso 2=molto")
    ap.add_argument("--payload-mode", choices=["text","bin"], default="text",
                    help="Formato payload: text (compatibilità) o bin (7 byte)")
    args = ap.parse_args()

    if ":" not in args.send_to:
        ap.error("--send-to deve essere IP:PORT")

    if args.mode == "roundrobin":
        run_round_robin_mode(args)
    elif args.mode == "roundrobin_opt":
        run_roundrobin_opt(args)
    else:
        target_ip, target_port_str = args.send_to.split(":", 1)
        target_port = int(target_port_str)
        tester = SpeedTester(args.listen_ip, args.listen_port,
                             target_ip, target_port,
                             args.expect_from or None,
                             payload_mode=args.payload_mode)
        tester.run_speed_test(args.start_interval, args.min_interval,
                              args.step_factor, args.max_loss)

if __name__ == "__main__":
    main()
