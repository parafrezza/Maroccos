# UDP Interface

Headless Player exposes a lightweight UDP control channel that accepts plain-text
commands and JSON payloads. This document lists every command currently
implemented in `headless-player/app.py` (branch `recuperato`).

## Defaults
- The listener binds to the port stored in `settings.json` (`UDP_PORT`, default
  `7777`). At runtime the effective port is visible via `GET /udp/status` and in
  UDP replies as `port=<n>`.
- Plain-text commands are case-insensitive ASCII strings. Tokens are separated
  by whitespace. Key/value arguments use the form `key=value`.
- JSON commands must be UTF-8 objects containing a `cmd` key. Optional
  `in_time` supports absolute epoch seconds (`>=1e9`) or relative delays in
  seconds.
- Replies: most plain-text commands acknowledge with `OK ...` or `ERR ...`. Only
  `STATUS`, `PLAYLIST STATUS`, and `time` return rich payloads. JSON commands
  other than `time` are fire-and-forget.

## Plain-Text Commands

| Command | Arguments | Description | Reply |
| --- | --- | --- | --- |
| `STATUS` | none | Report playback state and current item | `OK playing=<0/1> current=<name> port=<port>` |
| `PLAY` | none | Resume the active pipeline | `OK PLAY` |
| `PLAY <file> [loop=on] [fade=0.5] [fade_out=1.0]` | Relative or absolute media path plus optional key/value overrides | Shortcut for `PLAYFILE` | `OK PLAYFILE` |
| `PLAYFILE <file> [loop=on] [fade=0.5] [fade_out=1.0]` | Same semantics as HTTP `/play` body | Starts playback, hides splash first | `OK PLAYFILE <file>` |
| `STOP [seconds]` | Optional fade duration | Without seconds stops immediately; with seconds triggers `visual_ftb` then stop | `OK STOP` |
| `PAUSE` | none | Pause playback | `OK PAUSE` |
| `RESUME` / `PLAYRESUME` | none | Resume playback | `OK RESUME` |
| `NEXT` / `PREV` | none | Playlist navigation with wrap-around | `OK NEXT` / `OK PREV` |
| `SET <index>` | Zero-based index | Jump to playlist slot | `OK SET <index>` or `ERR ...` |
| `JUMP <n>` | One-based track number | Convenience alias for GUI numbering | `OK JUMP <n>` or `ERR ...` |
| `LOOP <state>` | `on/off/true/false/1/0` | Toggle playlist loop flag | `OK LOOP <0/1>` |
| `GO_TO_START` (`GOTO_START`, `REWIND`) | none | Pause and seek to start of current item | `OK REWIND` |
| `STARTUP [MAC ...]` | Optional list of MAC addresses | Fire Wake-on-LAN packets using configured defaults when omitted | `OK STARTUP` |
| `FADE <seconds>` | Seconds ≥ 0 | Persist global fade defaults (`overlay_fade_*`, `autoplay_fade_seconds`) | `OK FADE DEFAULT <seconds>` |
| `FADE <target> <seconds>` | Target alpha (0..1) and duration | Legacy overlay fade helper | `OK FADE <target>` or `ERR ...` |
| `BRIGHTNESS <value> <seconds>` | Value (0..1 or 0..100) and duration | Fade visual brightness to absolute value | `OK BRIGHTNESS <value>` |
| `OVERLAY_SHOW [alpha]` | Optional alpha (default 1.0) | Ensure overlay visible | `OK OVERLAY_SHOW <alpha>` |
| `OVERLAY_HIDE` | none | Hide overlay | `OK OVERLAY_HIDE` |
| `OVERLAY_ALPHA <alpha>` | Alpha (0..1) | Shorthand for `OVERLAY_SHOW` with explicit alpha | `OK OVERLAY_ALPHA <alpha>` or `ERR ...` |
| `SPLASH_SHOW [text]` | Optional text | Request splash screen (best-effort) | `OK SPLASH_SHOW` |
| `SPLASH_HIDE` | none | Hide splash screen | `OK SPLASH_HIDE` |
| `DISPLAY <spec>` | Any of `WIDTHxHEIGHT@HZ`, individual `width=`, `height=`, `refresh=`, plus flags `force_discovery`, `dry_run`, `legacy` | Applies arbitrary display mode using the WinAPI first, then fallback if requested | `OK DISPLAY` or `ERR DISPLAY usage` |
| `FORCE_720P [options]` | Same arguments as `DISPLAY`; defaults width/height to 1280x720 | Shortcut for `/display/mode/force_720p` | `OK FORCE_720P` |
| `FASTSTART <file>` | Media filename or absolute path | Preload media for instant start (`/faststart/prepare`) | `OK FASTSTART <file>` or `ERR ...` |
| `FASTGO [seconds]` | Optional delay before cut | Trigger faststart playback (`/faststart/go`) | `OK FASTGO` |
| `PLAYLIST APPLY items=a.mp4,b.mp4 [loop=on]` | Items separated by commas or provided via `items=`. Optional loop flag. | Replace playlist contents and loop flag | `OK PLAYLIST <count>` or `ERR ...` |
| `PLAYLIST STATUS` (`PLAYLIST INFO`) | none | Dump current playlist state | JSON blob (same as `/playlist/status`) |
| `AUTOPLAY <state> [delay=5] [restart=off]` | State toggles (`on/off/...`), optional delay seconds, optional restart flag | Update autoplay configuration and re-schedule timers | `OK AUTOPLAY <0/1>` |

### Display Command Notes
- Specs can be provided as `1920x1080@60`, `width=1920 height=1080 refresh=60`,
  or mixed forms. Missing refresh defaults to the current value.
- Flags: `legacy`/`exe` forces the legacy executable, `force_discovery` rebuilds
  the display device map, `dry`/`dry_run` validates without applying.

### Playlist Helpers
- `PLAYLIST APPLY` accepts absolute or relative paths; items are stored exactly
  as provided.
- `PLAYLIST STATUS` replies with the same JSON payload exposed via
  `GET /playlist/status`.

## JSON Commands

All JSON packets are objects with `cmd` plus optional arguments. Most commands
schedule actions in the GLib loop; only `time` answers directly.

| `cmd` | Required Keys | Optional Keys | Description | Reply |
| --- | --- | --- | --- | --- |
| `time` | — | `t0` | SNTP-style probe returning `{ok,t0,t1,t2}` | JSON response |
| `ping` | — | `duration_ms` (default 200) | Visual flash | none |
| `play` | `filename` **or** `path` | `loop`, `fade_in_seconds`, `fade_out_seconds` | Start playback (hides splash) | none |
| `pause` | — | — | Pause playback | none |
| `resume` | — | — | Resume playback | none |
| `stop` | — | `seconds` | Stop immediately or fade-to-black first | none |
| `next` / `prev` | — | — | Playlist navigation | none |
| `jump` | `index` | — | Jump to zero-based playlist index | none |
| `jump_track` | `n` (or `track`/`number`) | — | Jump using one-based numbering | none |
| `loop` | `on` | — | Toggle playlist loop | none |
| `brightness` | `value` | `seconds` | Fade brightness to absolute value | none |
| `go_to_start` (`goto_start`, `rewind`) | — | — | Pause and seek to start | none |
| `startup` | — | `macs`, `broadcast`, `port` | Wake-on-LAN helper | none |
| `splash_show` | — | `text` | Show splash (best-effort) | none |
| `splash_hide` | — | — | Hide splash | none |
| `shutdown` | — | — | Request graceful shutdown | none |

> Tip: include `"in_time": 5` to schedule a command 5 seconds in the future,
> or `"in_time": 1731500000` for an absolute epoch.

## Related HTTP Endpoints

Several UDP commands mirror REST endpoints that remain available when HTTP is
reachable:
- `POST /display/mode/apply` and `POST /display/mode/force_720p`
- `POST /faststart/prepare`, `POST /faststart/go`
- `POST /playlist/apply`, `GET /playlist/status`
- `POST /autoplay` and `GET /autoplay`
- `POST /visual/brightness`, `POST /visual/ftb`, `POST /visual/fade_in`
- `POST /system/startup`
- `POST /media/sync` *(new)* to command the player to download a ZIP bundle (or single file) into its `media/` directory.

Use the HTTP API when possible; UDP is designed for lightweight control and
offline scenarios.

