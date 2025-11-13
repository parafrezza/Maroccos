# Maroccos Copilot Instructions
## Architecture
- GUI/ (PySide6) orchestrates multi-device control via ApplicationController in GUI/core/controller.py; keep heavy work in ThreadPoolExecutor and emit Qt signals back to avoid blocking the UI thread.
- GUI/services/player_registry.py polls /status and listens to UDP beacons; extend PlayerRecord fields and update status_text formatting when adding new status keys.
- GUI/services/file_server.py hosts a local HTTP server for uploads; reuse FileServerService instead of writing ad-hoc servers.
- headless-player/app.py wraps FastAPI routes, UDP control, updater logic, and pluggable media backends (gst/mpv/cvlc/off); guard new code with HAS_GST checks because tests run with the stub implementations.
- OFF-player/ integrates via the headless "off" backend; the server auto-detects OFF-player/bin executables or honours OFF_PLAYER_EXE overrides when launching the C++ player.
- massive_update.py provides discovery, bundle building, and update orchestration reused by the GUI; adjust it alongside GUI flows when changing rollout semantics or network scanning behaviour.
- docs/time_sync.md and GUI/services/time_sync.py implement lightweight clock sync; reuse TimeSyncSupervisor data for scheduled playback instead of rolling bespoke logic.
## Workflows
- Create per-component virtualenvs and install GUI/requirements.txt or headless-player/requirements.txt; on Raspberry Pi use scripts/prepare_venv.sh to strip heavyweight extras (PyQt5/python-vlc) or pre-download wheels.
- Run the GUI with python GUI/main.py; set GUI_SKIP_STARTUP_DISCOVERY=1 when testing without reachable players to avoid long scans.
- Run the headless server via python headless-player/app.py; configure MEDIA_DIR, APP_PORT, OFF_PLAYER_EXE, USE_KMS, etc. through environment variables.
- Package Windows GUI executables using tools/build_gui_exe.ps1; it injects settings.json and TigerVNC binaries and fails fast if a settings file is missing.
- Build headless Windows binaries with tools/build_headless_windows.ps1 (supports --UseVenv and --BuildDebug) after ensuring requirements are installed.
- Use scripts/package_release.sh to assemble OFF-player + headless tarballs; it guards against missing openFrameworks .so copies unless PACKAGE_ALLOW_MISSING_OF_SO=1.
## Testing
- pytest.ini at the repo root targets headless-player/tests and GUI/tests; run pytest from the root to execute both suites.
- GUI tests expect no real network; set NO_NETWORK_DISCOVERY=1 and GUI_SKIP_STARTUP_DISCOVERY=1 in CI to bypass discovery threads.
- headless tests cover updater/bundle flows (e.g. test_bundle_exclusions ensures headless_venv is excluded); keep those assertions aligned when adjusting packaging or release scripts.
- Mock-friendly HTTP helpers live in GUI/services/api_client.py; prefer ApiClient during tests and features to keep headers (X-API-Key) consistent with the server.
## Patterns & Conventions
- Persisted settings go through GUI/core/settings_store.py; add parsing/serialization paths when introducing new AppConfig fields and avoid storing derived scan_ranges.
- Player status badges originate in GUI/services/player_registry.py; update its status_text logic whenever new timing fields or frameworks are introduced.
- The headless updater is two-phase (POST /download_update then /update) with progress fields exposed in /status; GUI flows and tests rely on update_stage and update_progress names.
- When adding media backends ensure fade/overlay code degrades gracefully; follow gst/mpv/cvlc implementations and respect overlay fallback semantics when HAS_GST is false.
- Logging writes to both CONFIG_DIR/logs and MEDIA_DIR/_logs; use logging.getLogger configured in app.py instead of print statements to preserve dual sinks.
## References
- README.md outlines components and quickstarts; docs/time_sync.md and docs/manifest_index.md capture synchronization and update manifest contracts referenced by the GUI and headless player.
- Packaging helpers live in tools/ (build_gui_exe.ps1, build_headless_windows.ps1, list_latest_bundle.py) and scripts/ (generate_index_manifest.py, provision_pi.sh); reuse them rather than re-implementing build logic.
