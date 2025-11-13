#!/usr/bin/env python3
import argparse
import sys
import time
import shutil
import subprocess
from pathlib import Path

# --- Parametri di default ---
DEFAULT_DIR = Path.cwd() / "media"
DEFAULT_FADE = 2.0
DEFAULT_VBIT_MBPS = 4.5
DEFAULT_FPS = 25
STABLE_SECONDS = 8
SCAN_INTERVAL = 2
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}

def log(msg: str):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), msg, flush=True)

def which(tool):
    from shutil import which as _which
    return _which(tool)

def require_ffmpeg():
    if not which("ffmpeg") or not which("ffprobe"):
        log("ERRORE: ffmpeg/ffprobe non trovati nel PATH. Installa ffmpeg e riprova.")
        sys.exit(1)

def have_encoder(name: str) -> bool:
    try:
        out = subprocess.check_output(["ffmpeg", "-hide_banner", "-encoders"], stderr=subprocess.STDOUT)
        return (name.encode() in out)
    except Exception:
        return False

def pick_h264_encoder():
    for enc in ("h264_v4l2m2m", "h264_omx"):
        if have_encoder(enc):
            return enc
    return "libx264"

def ffprobe_duration(path: Path) -> float:
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path)
        ], stderr=subprocess.STDOUT)
        dur = float(out.decode("utf-8", "ignore").strip())
        return max(dur, 0.0)
    except Exception as e:
        log(f"Attenzione: impossibile leggere durata con ffprobe ({e}).")
        return -1.0

def ffprobe_audio_stream_count(path: Path) -> int:
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            str(path)
        ], stderr=subprocess.STDOUT)
        lines = [ln for ln in out.decode("utf-8", "ignore").splitlines() if ln.strip() != ""]
        return len(lines)
    except Exception:
        return 0

def make_output_paths(input_path: Path, out_dir: Path):
    stem = input_path.stem
    video_out = out_dir / f"{stem}_faded.mp4"
    ffmpeg_log = out_dir / f"{stem}_faded_ffmpeg.log"
    def audio_out(idx: int):
        return out_dir / f"{stem}_faded_a{idx}.wav"
    return video_out, ffmpeg_log, audio_out

def is_stable(path: Path, stable_seconds: int) -> bool:
    if not path.exists():
        return False
    size1 = path.stat().st_size
    mtime1 = path.stat().st_mtime
    t0 = time.time()
    while time.time() - t0 < stable_seconds:
        time.sleep(1)
        if not path.exists():
            return False
        size2 = path.stat().st_size
        mtime2 = path.stat().st_mtime
        if size2 != size1 or mtime2 != mtime1:
            size1, mtime1 = size2, mtime2
            t0 = time.time()
    return True

def run_ffmpeg_with_logging(cmd: list, ffmpeg_log_path: Path, step: str):
    """Esegue ffmpeg e duplica lo stderr su console + file log."""
    with ffmpeg_log_path.open("a", encoding="utf-8") as flog:
        flog.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} | {step} ====\n")
        flog.write("CMD: " + " ".join(cmd) + "\n")
        proc = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
            bufsize=1
        )
        assert proc.stderr is not None
        for line in proc.stderr:
            line_stripped = line.rstrip("\n")
            # stampa compatta su console
            print(time.strftime("[%Y-%m-%d %H:%M:%S]"), f"[ffmpeg:{step}]", line_stripped, flush=True)
            # e su file
            flog.write(line)
        proc.wait()
        flog.write(f"EXIT CODE: {proc.returncode}\n")
        return proc.returncode

def build_video_cmd(inp: Path, out: Path, fade_seconds: float, duration: float,
                    vbit_mbps: float, encoder: str, fps: int):
    # Fade su durata
    if duration > 0:
        fade_s = min(fade_seconds, max(0.0, duration / 2.0 - 0.05))
        v_st_out = max(0.0, duration - fade_s)
    else:
        fade_s = fade_seconds
        v_st_out = None

    vfilters = [f"format=yuv420p", f"fade=t=in:st=0:d={fade_s}"]
    if v_st_out is not None and fade_s > 0:
        vfilters.append(f"fade=t=out:st={v_st_out}:d={fade_s}")
    vfilter_str = ",".join(vfilters)

    # Target bitrate per SD card (CBR-ish)
    bps = int(vbit_mbps * 1_000_000)
    maxrate = bps
    bufsize = bps * 2
    gop = max(1, int(fps * 2))  # ~2 secondi

    base = [
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-y",
        "-i", str(inp),
        "-vf", vfilter_str,
        "-an",                      # niente audio nel .mp4
        "-r", str(fps),             # framerate di uscita
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]

    if encoder == "h264_v4l2m2m":
        base += ["-c:v", "h264_v4l2m2m",
                 "-b:v", str(bps), "-maxrate", str(maxrate), "-bufsize", str(bufsize),
                 "-g", str(gop), "-bf", "0"]
    elif encoder == "h264_omx":
        base += ["-c:v", "h264_omx",
                 "-b:v", str(bps), "-maxrate", str(maxrate), "-bufsize", str(bufsize),
                 "-g", str(gop)]
    else:
        base += ["-c:v", "libx264", "-preset", "veryfast",
                 "-b:v", str(bps), "-maxrate", str(maxrate), "-bufsize", str(bufsize),
                 "-g", str(gop), "-profile:v", "high", "-level", "4.0"]

    base += [str(out)]
    return base

def extract_audio_wavs(inp: Path, out_dir: Path, make_audio_out, ffmpeg_log_path: Path):
    n = ffprobe_audio_stream_count(inp)
    if n == 0:
        log("Nessuna traccia audio da estrarre.")
        return
    for i in range(n):
        out_wav = make_audio_out(i+1)
        if out_wav.exists():
            out_wav = out_dir / f"{out_wav.stem}_{int(time.time())}.wav"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-y",
            "-i", str(inp),
            "-map", f"0:a:{i}",
            "-vn",
            "-c:a", "pcm_s16le",
            str(out_wav)
        ]
        log(f"Estrazione audio traccia {i} → {out_wav.name}")
        rc = run_ffmpeg_with_logging(cmd, ffmpeg_log_path, step=f"audio:{i}")
        if rc != 0:
            log(f"ERRORE estrazione audio traccia {i} (rc={rc})")

def process_file(input_path: Path, fade_seconds: float, vbit_mbps: float, fps: int,
                 out_dir: Path, originals_dir: Path, encoder: str):
    if not input_path.exists():
        return
    if input_path.suffix.lower() not in VIDEO_EXTS:
        log(f"Skip (estensione non video): {input_path.name}")
        return
    if input_path.stem.endswith("_faded"):
        log(f"Skip (già _faded): {input_path.name}")
        return

    if not is_stable(input_path, STABLE_SECONDS):
        log(f"File non stabile (o rimosso): {input_path.name}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    originals_dir.mkdir(parents=True, exist_ok=True)

    log(f"Analisi durata con ffprobe: {input_path.name}")
    duration = ffprobe_duration(input_path)

    video_out, ffmpeg_log_path, audio_out_factory = make_output_paths(input_path, out_dir)
    if video_out.exists():
        try: video_out.unlink()
        except Exception: pass

    # 1) Codifica video (senza audio)
    cmd_video = build_video_cmd(input_path, video_out, fade_seconds, duration, vbit_mbps, encoder, fps)
    log(f"Codifica video ({encoder}) → {video_out.name} @ ~{vbit_mbps:.2f} Mbps, {fps} fps, GOP≈{max(1,int(fps*2))}")
    rc = run_ffmpeg_with_logging(cmd_video, ffmpeg_log_path, step="video")
    if rc != 0:
        log(f"ERRORE ffmpeg video su {input_path.name} (rc={rc})")
        if video_out.exists():
            try: video_out.unlink()
            except Exception: pass
        return

    # 2) Estrai WAV per ogni traccia audio (pass-through a PCM)
    extract_audio_wavs(input_path, out_dir, audio_out_factory, ffmpeg_log_path)

    # 3) Sposta originale in originals/
    dest = originals_dir / input_path.name
    if dest.exists():
        dest = originals_dir / f"{input_path.stem}_{int(time.time())}{input_path.suffix}"
    try:
        shutil.move(str(input_path), str(dest))
        log(f"OK: creati {video_out.name} (+ WAV) e spostato originale in {dest}")
    except Exception as e:
        log(f"Attenzione: impossibile spostare l’originale: {e}")

def scan_and_process(root: Path, fade_seconds: float, vbit_mbps: float, fps: int):
    processed = set()
    fade_dir = root / "fade_added"
    originals_dir = root / "originals"

    log(f"Monitoraggio cartella: {root}")
    root.mkdir(parents=True, exist_ok=True)
    fade_dir.mkdir(parents=True, exist_ok=True)
    originals_dir.mkdir(parents=True, exist_ok=True)

    encoder = pick_h264_encoder()
    log(f"Encoder selezionato: {encoder}")

    while True:
        try:
            for p in root.iterdir():
                if p.is_dir():
                    if p.name in ("fade_added", "originals"):
                        continue
                    continue
                if p.suffix.lower() in VIDEO_EXTS and p not in processed:
                    processed.add(p)
                    process_file(p, fade_seconds, vbit_mbps, fps, fade_dir, originals_dir, encoder)
            time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            log("Interrotto dall’utente. Esco.")
            break
        except Exception as e:
            log(f"Errore nel loop: {e}")
            time.sleep(SCAN_INTERVAL)

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description=("Sorveglia una cartella, applica fade in/out video, codifica H.264 ottimizzata per VLC su Raspberry, "
                     "salva in fade_added/ (senza audio) ed estrae WAV separati; sposta gli originali in originals/.")
    )
    parser.add_argument("-d", "--dir", default=str(DEFAULT_DIR),
                        help=f"Cartella da sorvegliare (default: {DEFAULT_DIR})")
    parser.add_argument("-f", "--fade", type=float, default=DEFAULT_FADE,
                        help=f"Secondi di fade-in/out video (default: {DEFAULT_FADE})")
    parser.add_argument("--vbit", type=float, default=DEFAULT_VBIT_MBPS,
                        help=f"Bitrate video target in Mbps (default: {DEFAULT_VBIT_MBPS})")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS,
                        help=f"Framerate di uscita (default: {DEFAULT_FPS})")
    args = parser.parse_args()

    watch_dir = Path(args.dir).resolve()
    require_ffmpeg()
    log(f"Avvio con fade={args.fade}s, vbit≈{args.vbit} Mbps, fps={args.fps}, dir={watch_dir}")
    scan_and_process(watch_dir, args.fade, args.vbit, args.fps)

if __name__ == "__main__":
    main()
