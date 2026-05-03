# Architecture

TF2 Idle Farmer is a Windows-focused Python automation tool for cycling
through saved Steam accounts, launching Team Fortress 2, idling on configured
servers, and recording weekly item drops.

## Directory structure

```text
TF2_Idle_Farmer/
├── .gitignore
├── LICENSE
├── README.md                             # User-facing setup guide, usage notes, and troubleshooting
├── main.py                               # Application entry point, scheduler, and full farming orchestrator
├── pyproject.toml                        # Project metadata, build backend, dependencies, and tool settings
├── requirements.txt                      # Python runtime dependencies
├── config/
│   ├── accounts.example.txt              # Example account list format (login or login:SteamID64)
│   ├── accounts.txt                      # Local Steam login names (with optional Steam ID), ignored by git
│   ├── servers.example.txt               # Example idle server list format
│   ├── servers.txt                       # Local TF2 idle server list, one IP:PORT per line, ignored by git
│   └── settings.toml                     # User-facing runtime config: paths, timing values, behavior toggles, optional Steam API key
├── data/
│   └── drops.json                        # Persisted drop history per Steam account, auto-created at runtime
├── docs/
│   ├── ARCHITECTURE.md                   # This file
│   └── Resources.md                      # External links for finding TF2 idle servers
├── logs/
│   └── farmer.log                        # Main rotating runtime log (+ up to 3 rotated backups)
└── modules/
    ├── __init__.py
    ├── _win_input.py                     # PostMessage keyboard input routed directly to the TF2 window
    ├── constants.py                      # Central registry of all program-level constants (no magic numbers elsewhere)
    ├── drop_tracker.py                   # Parses TF2 console.log and stores drop records in data/drops.json
    ├── human_behavior.py                 # Random waits, MOTD dismissal, mouse movement, and idle micro-actions
    ├── logger.py                         # Shared console and rotating-file logger setup
    ├── steam_inventory.py                # Steam Web Inventory API client for before/after drop detection
    ├── steam_manager.py                  # Steam process control and loginusers.vdf account switching
    └── tf2_manager.py                    # TF2 launch options, autoexec.cfg generation, process detection, and shutdown
```

## Configuration vs. constants — design boundary

The project separates two distinct kinds of tuneable values:

**`config/settings.toml`** — *user-facing runtime configuration.*
Values the end user is expected to edit: file paths, idle durations, startup
timeouts, behaviour toggles, and the optional Steam API key. Changing these
requires no code knowledge.

**`modules/constants.py`** — *developer-facing program constants.*
Values that are part of the application logic and should not need to change
between machines: Steam and TF2 process names, launch flags, regex patterns,
Bézier curve parameters, MOTD key sequences, and log rotation limits. Changing
these requires understanding the codebase.

A useful rule of thumb: if changing a value is a *configuration decision* (the
user wants longer idles), it belongs in `settings.toml`. If it is a *code
decision* (the Bézier curve needs more steps), it belongs in `constants.py`.

## Runtime flow

1. `main.py` loads `config/settings.toml`, `config/accounts.txt`, and
   `config/servers.txt`. Each account entry is parsed as `{"login", "steam_id"}` —
   the Steam ID is optional and enables inventory-based drop detection.
2. For each account, `steam_manager.py` stops Steam if needed and marks the
   target account as `MostRecent` in `loginusers.vdf`.
3. `steam_manager.py` launches Steam in silent login mode and waits for it to
   stabilise.
4. `tf2_manager.py` picks a server, writes TF2's `autoexec.cfg`, and launches
   TF2 through Steam with low-resource launch options defined in `constants.py`.
5. `drop_tracker.py` clears the TF2 `console.log` before the session so stale
   drop messages are not counted again.
6. **`steam_inventory.py`** takes a pre-session inventory snapshot via the
   Steam Web API (skipped if no API key or Steam ID is configured).
7. `human_behavior.py` dismisses the server MOTD and keeps the session alive
   with randomised idle timing and optional micro-actions.
8. `drop_tracker.py` tails `console.log` live via `ConsoleLogWatcher` as a
   fallback in case inventory tracking is unavailable.
9. After the idle window, TF2 is quit and `autoexec.cfg` is deleted.
10. **`steam_inventory.py`** takes a post-session snapshot. The diff (new items)
    is the authoritative drop list. If either snapshot is unavailable, the
    `ConsoleLogWatcher` result is used instead.
11. `drop_tracker.py` appends the record to `data/drops.json` and prints a
    weekly summary. Steam is quit before the next account starts.

## Core modules

### `main.py`

Owns the high-level lifecycle. It handles CLI arguments, optional scheduled
mode, configuration loading, account/server iteration, session error handling,
and emergency cleanup. Account parsing supports two formats:

```
my_login                    → {"login": "my_login", "steam_id": None}
my_login:76561198XXXXXXXXX  → {"login": "my_login", "steam_id": "76561198..."}
```

Inventory snapshots are taken around the idle session and diffed to produce the
drop list. If the API is not configured or fails, the session continues with the
console.log fallback — no session is lost.

### `modules/steam_inventory.py`

Fetches TF2 inventory data from the Steam Web Inventory API
(`/inventory/{steamid}/440/2`). Returns a `set[str]` of item names (with
quantity encoded as `"Name (xN)"` for stacked items) or `None` on any failure.
Retries up to 3 times with a 5-second delay; HTTP 403 (private inventory) exits
immediately without retrying. Never raises exceptions — always safe to call.

### `modules/constants.py`

Central registry of every program-level constant. No other module contains
magic numbers or hardcoded strings — they all import from here. Constants are
grouped by domain (Steam, TF2, human behaviour, logging, scheduler) and each
entry carries a comment explaining its units and effect. This is the first file
to read when tuning the automation behaviour at the code level.

### `modules/steam_manager.py`

Controls Steam-specific automation. It edits `loginusers.vdf`, launches Steam
with the flags defined in `constants.py` (`-silent`, `-noreactlogin`,
`-login <username>`), waits for Steam processes using the poll intervals and
stabilisation delay from `constants.py`, and exits or force-kills Steam when
required.

### `modules/tf2_manager.py`

Controls TF2-specific automation. It generates `autoexec.cfg` from the template
in `constants.py`, appends the target idle server, launches app ID `440`
through Steam using the launch options list from `constants.py`, detects TF2
processes, and terminates TF2 at the end of the session.

### `modules/human_behavior.py`

Contains timing and input helpers that make sessions less rigid. It provides
random sleeps, repeated MOTD dismissal key presses, optional Bézier-curve mouse
movement, and occasional harmless key presses during long idle windows. All
tuning knobs (step counts, delays, key lists, action probability) are imported
from `constants.py`.

### `modules/drop_tracker.py`

Owns drop persistence. It clears and parses TF2's `console.log` using compiled
regex patterns from `constants.py`, runs a `ConsoleLogWatcher` background
thread that tails the file live during the session, and writes records to
`data/drops.json`. Acts as a fallback when inventory API tracking is
unavailable. Also builds weekly summaries from saved history.

### `modules/logger.py`

Creates the shared logger used across the project. Rotation size, backup count,
and log format are imported from `constants.py`. Logs are written to stdout and
to `logs/farmer.log`.

## Drop detection — two-layer strategy

TF2 item drops are not written to `console.log` (they appear as in-game chat
messages that `-condebug` does not capture). The project uses two complementary
strategies:

**Layer 1 — Steam Inventory API (primary, when configured)**
`steam_inventory.get_inventory()` is called before TF2 launches and again after
TF2 quits. The set difference is the authoritative list of new items. Requires
a free Steam Web API key in `settings.toml` and a Steam ID per account in
`accounts.txt`. Inventory must be set to Public.

**Layer 2 — console.log watcher (fallback)**
`ConsoleLogWatcher` tails `console.log` during the session and `parse_console_log`
does a final scan at the end. This catches drops only when the patterns in
`DROP_LOG_PATTERNS` match — which is not guaranteed for all TF2 versions. Used
automatically when the API is not configured or fails.

Items found by both layers are merged; the API result takes precedence.

## Configuration and generated state

Configuration lives in `config/`. The example files document the expected
format, while local account and server files are intentionally kept out of git
because they are machine-specific and may contain Steam IDs.

Generated runtime state lives in `data/` and `logs/`. These files are created
or updated by the application and can be deleted when a fresh local state is
needed.

## External dependencies

The project uses:

- `psutil` for Steam and TF2 process discovery and termination.
- `pyautogui` for MOTD dismissal, mouse movement, and small idle interactions.
- `vdf` for reading and writing Steam's `loginusers.vdf`.
- `schedule` for weekly scheduled execution via `python main.py --schedule`.
- `tomllib` from Python 3.11+, or `tomli` on Python 3.10, for TOML settings.
- Standard library `urllib` for Steam Inventory API requests (no extra dependency).