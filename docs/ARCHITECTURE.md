# Architecture

This document is the developer-facing source of truth for how TF2 Idle Farmer
is structured. User setup and troubleshooting live in the root `README.md`.

## Design Boundaries

The project uses only OS/client-level mechanisms:

- Steam launch flags and saved-login state
- TF2 config generation
- Windows keyboard/mouse window messages
- TF2 window screenshots
- process discovery and shutdown
- public Steam inventory endpoint
- TF2 `console.log` as a fallback signal

The project does not use memory reading, DLL injection, process hooks, packet
manipulation, executable patching, or anti-cheat bypass techniques.

## Directory Layout

```text
TF2_Idle_Farmer/
├── main.py
├── pyproject.toml
├── config/
│   ├── accounts.example.txt
│   ├── accounts.txt              # local, git-ignored
│   ├── servers.example.txt
│   ├── servers.txt               # local, git-ignored
│   └── settings.toml
├── data/
│   └── drops.json                # auto-created
├── docs/
│   ├── ARCHITECTURE.md
│   └── Resources.md
├── logs/
│   └── farmer.log
└── modules/
    ├── __init__.py
    ├── _win_input.py
    ├── constants.py
    ├── drop_tracker.py
    ├── human_behavior.py
    ├── logger.py
    ├── steam_inventory.py
    ├── steam_manager.py
    └── tf2_manager.py
```

## Configuration Model

`config/settings.toml` is for user-facing runtime choices:

- local paths
- session timing
- account/server shuffling
- mouse activity
- item-popup dismissal mode and coordinates
- post-launch connection health checks
- server retry count after connection failures
- optional Discord/Telegram notifications
- Steam Inventory API key and post-session polling

`modules/constants.py` is for application defaults and developer-tuned values:

- process names
- TF2 launch flags
- generated `autoexec.cfg` template
- MOTD key sequence defaults
- popup detector defaults
- logging and scheduler constants

If a value is expected to change per machine or user, prefer `settings.toml`.
If a value is part of program behavior and rarely changed, prefer
`constants.py`.

## Session Flow

For each account:

1. `steam_manager.is_game_running()` aborts if a known game process is active.
2. If Steam is already running, `get_active_steam_account()` checks
   `loginusers.vdf`.
3. If needed, Steam is closed, `switch_account()` marks the requested account
   as most recent, and Steam is relaunched.
4. `tf2_manager.generate_autoexec()` writes a temporary `autoexec.cfg` with
   performance settings and `connect <server>`.
5. `drop_tracker.clear_console_log()` deletes stale `console.log`.
6. `steam_inventory.get_inventory()` takes the pre-session inventory snapshot
   when API tracking is configured.
7. `tf2_manager.launch_tf2()` starts TF2 through Steam with low-resource launch
   options.
8. `main._connect_tf2_with_server_retries()` launches TF2 with a generated
   `autoexec.cfg`, waits for map load, and calls
   `tf2_health.check_tf2_connection()`.
9. If the connection check fails, a failure screenshot is saved when enabled,
   optional notifications are sent, TF2 is cleaned up, and the runner tries the
   next configured server up to `max_server_attempts`.
10. If every server attempt fails, Steam is quit and the runner moves on.
11. `human_behavior.dismiss_motd()` sends the startup MOTD
   key sequence.
12. `ConsoleLogWatcher` starts tailing `console.log` as a fallback signal.
13. `human_behavior.idle_session()` sleeps in randomized windows, performs
    optional micro-actions, and optionally dismisses item-drop popups.
14. The watcher stops, TF2 is killed, and generated `autoexec.cfg` is removed.
15. The post-session inventory snapshot is polled several times if needed.
16. Inventory delta is saved when API snapshots are available; otherwise
    `console.log` fallback results are saved.
17. Steam is quit and the runner pauses before the next account.

## Core Modules

### `main.py`

Owns orchestration: CLI flags, scheduler, settings loading, accounts/servers,
session lifecycle, emergency cleanup, inventory polling, and drop persistence.

Important helpers:

- `_try_inventory_snapshot()` returns `Counter[str] | None`.
- `_try_post_session_inventory_snapshot()` retries after TF2 exits because
  Steam inventory updates can lag.
- `_compute_new_items()` formats inventory deltas for `drops.json`.

### `modules/steam_manager.py`

Controls Steam process and account state:

- active game guard
- Steam process discovery
- `loginusers.vdf` account switching
- Steam launch and shutdown

### `modules/tf2_manager.py`

Controls TF2 process state:

- temporary `autoexec.cfg` generation
- Steam `-applaunch 440` launch
- TF2 process detection
- TF2 shutdown
- generated config cleanup

### `modules/steam_inventory.py`

Fetches the public TF2 inventory endpoint and returns item-name quantities as
`Counter[str]`.

The inventory delta is the authoritative drop source when both pre-session and
post-session snapshots are available. This is more reliable than UI parsing or
`console.log` because it reflects the account inventory after Steam processes
the drop.

### `modules/drop_tracker.py`

Owns fallback drop persistence and `console.log` lifecycle:

- deletes stale `console.log` before launch
- tails new `console.log` bytes during the session
- runs a final full-file scan after idle
- saves drop records to `data/drops.json`
- prints weekly summaries

`console.log` is fallback only. It is useful for diagnostics but should not be
treated as the primary drop source.

### `modules/tf2_health.py`

Owns post-launch connection checks before the idle timer starts:

- scans the fresh `console.log` for known success/failure patterns
- optionally screenshots a centered TF2 client-area region for a stable gray
  Source-engine failure dialog
- saves full TF2 client-area screenshots to `logs/connection_failures/` after
  failed server connection attempts when configured
- returns a structured result so `main.py` can skip bad account/server
  sessions cleanly

The screenshot detector is a fallback signal, not OCR. By default, a timeout
with no explicit failure is allowed to continue so sparse `console.log` output
does not cause false skips.

### `modules/notifier.py`

Best-effort outbound alerts:

- Discord webhook via `discord_webhook_url`
- Telegram bot API via `telegram_bot_token` and `telegram_chat_id`
- connection-failure alerts include account, server, attempt count, evidence,
  and local screenshot path when available
- notification failures are logged but never crash a farming run

### `modules/human_behavior.py`

Owns timing and user-like actions:

- random waits
- startup MOTD dismissal
- optional mouse movement
- occasional harmless key presses
- configured item-drop popup dismissal

MOTD and item-drop popups are intentionally separate:

- MOTD is a startup blocker, so it uses a bounded keyboard sequence shortly
  after map load.
- Item-drop popup dismissal happens during idle and must not send blind Enter.
  The default mode is `auto`: screenshot a small TF2 client-area region and
  click the configured client coordinate only when the region looks like a TF2
  popup.

### `modules/_win_input.py`

Windows input helpers:

- find the TF2 top-level window by class name `Valve001`
- send keyboard input to the TF2 window via `PostMessage`
- compute TF2 window/client-area geometry
- click TF2 client-area coordinates by posting mouse messages to the TF2 window

Mouse clicks are addressed to the TF2 window handle and use client-area
coordinates. They do not move the user's real cursor and are not hooks,
injection, or game memory manipulation.

### `modules/constants.py`

Central defaults for application behavior. Avoid scattering magic numbers
across modules.

### `modules/logger.py`

Shared stdout and rotating-file logger.

## Drop Detection Strategy

### Primary: Steam Inventory API

The runner takes a pre-session inventory snapshot before TF2 launches and a
post-session snapshot after TF2 exits. Post-session polling retries several
times because inventory updates may be delayed.

Snapshots are represented as `Counter[str]`, not `set[str]`, so duplicate item
changes are handled correctly:

```text
before: {"Scrap Metal": 2}
after:  {"Scrap Metal": 3}
delta:  ["Scrap Metal"]
```

### Fallback: `console.log`

`ConsoleLogWatcher` tails `console.log` and `parse_console_log()` does a final
scan. This path is used when API tracking is unavailable or failed.

Reliability is limited because TF2 does not consistently write item drops to
`console.log`. Keep this path for fallback/diagnostics, not as the primary
design.

## Popup Dismissal Strategy

Item-drop popup dismissal is separate from drop detection.

Modes:

- `auto` - screenshot a small TF2 client-area region; click only when it looks
  like a centered popup.
- `mouse` - click the configured TF2 client coordinate every check interval.
- `off` - never dismiss item-drop popups during idle.
- `keyboard_fallback` - legacy Enter/Escape behavior, kept only for manual
  troubleshooting.

Default mode is `auto`.

The default coordinate assumes the normal `800x600` TF2 window:

```toml
drop_popup_click_x = 400
drop_popup_click_y = 520
drop_popup_detect_region = [220, 120, 360, 360]
```

If TF2 launch resolution changes, retune these values.

## `console.log` Lifecycle

`console.log` is deleted before each TF2 launch so old lines are not counted.
It is also deleted after drops are saved.

Delete, do not truncate. TF2 can keep an open file descriptor; truncating can
leave null bytes and corrupt later reads.

## Known Bad Approaches

These have already been considered and should not be retried casually:

- **Periodic blind Enter/Escape for item-drop popups.** Enter can interact with
  server votes or menus instead of confirming the drop popup.
- **Using `console.log` as primary drop detection.** Modern TF2 does not
  reliably log item-drop messages.
- **Searching `console.log` for chat-style drop lines.** Lines like
  `Player has found: Item Name` may appear in-game but are not reliably written
  by `-condebug`.
- **Re-adding the TF2 `-console` launch flag.** Keep `-condebug`, but do not
  launch the in-game console by default. When the console has focus, keyboard
  input such as Enter can go to the console instead of the UI popup.
- **Absolute screen coordinates.** They break with different monitor layouts,
  window positions, DPI scaling, and border sizes.
- **OCRing the popup item name as primary detection.** It is heavier, more
  brittle, and less authoritative than inventory diff.
- **Full OpenCV/template matching as the first solution.** Keep the detector
  lightweight unless screenshots prove the simple heuristic is insufficient.
- **Memory reading, DLL injection, hooks, packet manipulation, or binary
  patching.** These are outside the project boundary.
- **Truncating `console.log`.** Delete it instead to avoid stale file-offset
  behavior.
- **Keeping generated `autoexec.cfg` after a run.** It can affect manual TF2
  play by reconnecting to an idle server or applying low-resource settings.

## External Dependencies

- `psutil` - process discovery and termination
- `pyautogui` - screenshots and optional global mouse movement
- `vdf` - reading/writing `loginusers.vdf`
- `schedule` - weekly scheduled execution
- `tomllib` / `tomli` - TOML settings parsing
- `urllib` - Steam inventory HTTP requests
- Windows `ctypes` APIs - TF2 window geometry and input
