# Architecture

TF2 Idle Farmer is a Windows-focused Python automation tool for cycling through saved Steam accounts, launching Team Fortress 2, idling on configured servers, and recording weekly item drops.

## Directory structure

```text
TF2_Idle_Farmer/
├── main.py                               # Entry point, scheduler, farming orchestrator
├── pyproject.toml                        # Project metadata, dependencies, tool settings
├── config/
│   ├── accounts.example.txt              # Example: login or login:SteamID64
│   ├── accounts.txt                      # Local account list (git-ignored)
│   ├── servers.example.txt               # Example idle server list
│   ├── servers.txt                       # Local server list, one IP:PORT per line (git-ignored)
│   └── settings.toml                     # User-facing config: paths, timing, behaviour toggles, API key
├── data/
│   └── drops.json                        # Persisted drop history per account (auto-created)
├── docs/
│   ├── ARCHITECTURE.md                   # This file
│   └── Resources.md                      # External links for finding TF2 idle servers
├── logs/
│   └── farmer.log                        # Rotating runtime log (+ up to 3 backups)
└── modules/
    ├── __init__.py
    ├── _win_input.py                     # PostMessage keyboard input routed directly to the TF2 window
    ├── constants.py                      # Central registry of all program-level constants
    ├── drop_tracker.py                   # console.log parsing, ConsoleLogWatcher, drops.json persistence
    ├── human_behavior.py                 # Random waits, MOTD dismissal, mouse movement, idle micro-actions
    ├── logger.py                         # Shared console + rotating-file logger
    ├── steam_inventory.py                # Steam Web Inventory API client for before/after drop detection
    ├── steam_manager.py                  # Steam process control and loginusers.vdf account switching
    └── tf2_manager.py                    # TF2 launch, autoexec.cfg generation, process detection, shutdown
```

## Configuration vs. constants

**`config/settings.toml`** — user-facing runtime configuration. Values the end user is expected to edit: paths, idle durations, startup timeouts, behaviour toggles, optional Steam API key.

**`modules/constants.py`** — developer-facing program constants. Values that are part of the application logic: process names, launch flags, regex patterns, Bézier curve parameters, MOTD key sequences, log rotation limits.

Rule of thumb: if changing a value is a *user decision* (longer idles), it belongs in `settings.toml`. If it is a *code decision* (Bézier curve step count), it belongs in `constants.py`.

## Runtime flow

### Pre-session safety checks

Before touching Steam or TF2, `main.py` runs two checks for each account:

1. **Active game guard** — `steam_manager.is_game_running()` scans for known game processes (TF2, CS2, Dota 2, …). If any are found, the session is aborted with a warning rather than risk interrupting active play.

2. **Active account check** — if Steam is already running, `steam_manager.get_active_steam_account()` reads `loginusers.vdf` to identify the current account:
   - Already the correct account → skip shutdown and re-login, proceed directly to step 4.
   - Wrong account (or unknown) → quit Steam, then continue with steps 2–3 below.

### Per-account session

1. *(Skipped if already logged in)* `steam_manager.switch_account()` edits `loginusers.vdf` to set the target account as `MostRecent`.
2. *(Skipped if already logged in)* `steam_manager.launch_steam()` starts Steam in silent login mode and waits for it to stabilise.
3. `tf2_manager.generate_autoexec()` writes `autoexec.cfg` (performance caps + `connect <server>`). `drop_tracker.clear_console_log()` deletes any existing `console.log` so stale messages are not re-counted.
4. `steam_inventory.get_inventory()` takes a pre-session snapshot (skipped if no API key or Steam ID).
5. `tf2_manager.launch_tf2()` starts TF2 via `-applaunch 440` with the launch options in `constants.py`.
6. `human_behavior.dismiss_motd()` dismisses the server MOTD so the drop timer starts. A `ConsoleLogWatcher` thread begins tailing `console.log` in the background.
7. `human_behavior.idle_session()` holds the session for the configured duration with randomised sleeps, optional mouse movement, and occasional harmless key presses.
8. TF2 is killed, `autoexec.cfg` is deleted. `steam_inventory.get_inventory()` takes a post-session snapshot; the diff is the authoritative drop list. `console.log` watcher results are used if the API is unavailable.
9. `drop_tracker.save_drop()` appends the record to `data/drops.json`. `drop_tracker.clear_console_log()` deletes `console.log` (drops already saved; prevents growth from manual TF2 launches between farming runs).
10. Steam is quit. A random pause separates accounts.

## Core modules

### `main.py`

Owns the high-level lifecycle: CLI arguments, optional scheduler, config loading, account/server iteration, pre-session safety checks, and emergency cleanup. Account entries support two formats:

```
my_login                    → {"login": "my_login", "steam_id": None}
my_login:76561198XXXXXXXXX  → {"login": "my_login", "steam_id": "76561198..."}
```

### `modules/steam_manager.py`

Controls Steam-specific automation. Key functions:

- `get_active_steam_account(vdf_path)` — reads `loginusers.vdf` and returns the login name currently marked `MostRecent`, or `None`. Used to decide whether a Steam restart is needed.
- `is_game_running()` — scans running processes against `_GAME_PROCESS_NAMES` to detect active play before a session starts.
- `switch_account()` — edits `loginusers.vdf` to activate the target account without touching `RememberPassword` on other accounts.
- `launch_steam()` / `quit_steam()` / `wait_for_steam_ready()` — process lifecycle with graceful exit + force-kill fallback.

### `modules/tf2_manager.py`

Controls TF2-specific automation. Generates `autoexec.cfg` from the template in `constants.py`, launches app ID `440` via Steam, detects TF2 processes, and terminates TF2. `cleanup_autoexec()` deletes the generated file after each session (also called on crash).

### `modules/steam_inventory.py`

Fetches TF2 inventory from `https://steamcommunity.com/inventory/{steamid}/440/2`. Returns a `set[str]` of item names (`"Name (xN)"` for stacked items) or `None` on failure. Retries up to 3 times (5 s delay); HTTP 403 exits immediately. Never raises — always safe to call.

### `modules/drop_tracker.py`

Owns drop persistence and console.log interaction:

- `clear_console_log()` — deletes (not truncates) `console.log`. Deletion is required because TF2 keeps its file descriptor open; truncating leaves null bytes that corrupt subsequent log lines.
- `ConsoleLogWatcher` — background thread that tails `console.log` during the session, processing only new bytes each poll.
- `parse_console_log()` / `check_and_save()` — final scan and persistence fallback when the inventory API is unavailable.
- `save_drop()` / `get_weekly_summary()` / `print_weekly_summary()` — `drops.json` read/write and reporting.

### `modules/human_behavior.py`

Timing and input helpers: random sleeps, repeated MOTD dismissal, Bézier-curve mouse movement, and occasional harmless key presses during idle windows. All tuning knobs imported from `constants.py`.

### `modules/_win_input.py`

Sends `WM_KEYDOWN` / `WM_KEYUP` via `PostMessage` directly to the TF2 window handle (found by class name `Valve001`). The user's active window retains focus. Key names follow the pyautogui convention and map to Virtual-Key codes via `_VK_MAP`.

### `modules/constants.py`

Central registry of every program-level constant — no magic numbers or hardcoded strings anywhere else. Constants are grouped by domain (Steam, TF2, human behaviour, logging, scheduler) and each carries a comment with units and effect.

### `modules/logger.py`

Creates the shared logger used across the project. Rotation size, backup count, and format are imported from `constants.py`. Writes to stdout and `logs/farmer.log`.

## Drop detection — two-layer strategy

**Layer 1 — Steam Inventory API (primary)**
`get_inventory()` is called before TF2 launches and after TF2 quits. The set difference is the authoritative drop list. Requires a Steam Web API key in `settings.toml`, a SteamID64 per account in `accounts.txt`, and a Public inventory.

**Layer 2 — console.log watcher (fallback)**
`ConsoleLogWatcher` tails `console.log` during the session; `parse_console_log()` does a final scan at the end. Used automatically when the API is not configured or fails. Reliability depends on TF2 writing drop messages that match `DROP_LOG_PATTERNS` — not guaranteed across all versions.

Items found by both layers are merged; the API result takes precedence.

## console.log lifecycle

`console.log` is deleted at two points each session:

- **Before launch** (`clear_console_log`) — removes stale lines from previous sessions so they are not re-detected.
- **After drops are saved** (`clear_console_log`) — removes the file once its contents are no longer needed, preventing unbounded growth from manual TF2 launches between farming runs.

If the farmer is interrupted before the post-session delete, the pre-session delete on the next run acts as a safety net.

## External dependencies

- `psutil` — process discovery and termination
- `pyautogui` — MOTD dismissal, mouse movement
- `vdf` — reading and writing `loginusers.vdf`
- `schedule` — weekly scheduled execution
- `tomllib` (stdlib 3.11+) / `tomli` (3.10 backport) — TOML settings parsing
- `urllib` (stdlib) — Steam Inventory API requests