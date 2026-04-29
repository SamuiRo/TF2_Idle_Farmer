# Architecture

TF2 Idle Farmer is a Windows-focused Python automation tool for cycling
through saved Steam accounts, launching Team Fortress 2, idling on configured
servers, and recording weekly item drops.

## Directory structure

```text
TF2_Idle_Farmer/
├── .gitignore                            # Git ignore rules for Python, IDE files, logs, and local runtime secrets
├── LICENSE                               # Project license
├── README.md                             # User-facing setup guide, usage notes, and troubleshooting
├── main.py                               # Application entry point, scheduler, and full farming orchestrator
├── pyproject.toml                        # Python project metadata, build backend, dependencies, and tool settings
├── requirements.txt                      # Python runtime dependencies
├── config/
│   ├── accounts.example.txt              # Example Steam account list format
│   ├── accounts.txt                      # Local Steam login names, one per line, ignored by git
│   ├── servers.example.txt               # Example idle server list format
│   ├── servers.txt                       # Local TF2 idle server list, one IP:PORT per line, ignored by git
│   └── settings.toml                     # Local paths, timing values, and behavior toggles
├── data/
│   └── drops.json                        # Persisted drop history per Steam account, auto-created at runtime
├── docs/
│   ├── ARCHITECTURE.md                   # Project architecture and directory map
│   ├── PLAN.md                           # Development plan and implementation notes
│   └── Resources.md                      # External links for finding TF2 idle servers and related resources
├── logs/
│   ├── farmer.log                        # Main rotating runtime log, auto-created at runtime
│   ├── farmer.log.1                      # Rotated log backup, created when farmer.log reaches size limit
│   ├── farmer.log.2                      # Older rotated log backup
│   └── farmer.log.3                      # Oldest retained rotated log backup
└── modules/
    ├── __init__.py                       # Marks modules as a Python package
    ├── drop_tracker.py                   # Parses TF2 console.log and stores drop records in data/drops.json
    ├── human_behavior.py                 # Random waits, MOTD dismissal, mouse movement, and idle micro-actions
    ├── logger.py                         # Shared console and rotating-file logger setup
    ├── steam_manager.py                  # Steam process control and loginusers.vdf account switching
    └── tf2_manager.py                    # TF2 launch options, autoexec.cfg generation, process detection, and shutdown
```

## Runtime flow

1. `main.py` loads `config/settings.toml`, `config/accounts.txt`, and
   `config/servers.txt`.
2. For each configured account, `steam_manager.py` stops Steam if needed and
   marks the target account as `MostRecent` in `loginusers.vdf`.
3. `steam_manager.py` launches Steam in silent login mode and waits for it to
   stabilise.
4. `tf2_manager.py` picks a server, writes TF2's `autoexec.cfg`, and launches
   TF2 through Steam with low-resource launch options.
5. `drop_tracker.py` clears the TF2 `console.log` before the session so stale
   drop messages are not counted again.
6. `human_behavior.py` dismisses the server MOTD and keeps the session alive
   with randomized idle timing and optional micro-actions.
7. `drop_tracker.py` parses the fresh `console.log`, appends the session result
   to `data/drops.json`, and prints a weekly summary.
8. `tf2_manager.py` and `steam_manager.py` shut down TF2 and Steam before the
   next account starts.

## Core modules

### `main.py`

Owns the high-level lifecycle. It handles CLI arguments, optional scheduled
mode, configuration loading, account/server iteration, session error handling,
and emergency cleanup.

### `modules/steam_manager.py`

Controls Steam-specific automation. It edits `loginusers.vdf`, launches Steam
with `-silent`, `-noreactlogin`, and `-login <username>`, waits for Steam
processes, and exits or kills Steam when required.

### `modules/tf2_manager.py`

Controls TF2-specific automation. It generates `autoexec.cfg`, appends the
target idle server, launches app ID `440` through Steam, detects TF2 processes,
and terminates TF2 at the end of the session.

### `modules/human_behavior.py`

Contains timing and input helpers that make sessions less rigid. It provides
random sleeps, repeated MOTD dismissal key presses, optional mouse movement,
and occasional harmless key presses during long idle windows.

### `modules/drop_tracker.py`

Owns drop persistence. It clears and parses TF2's `console.log`, extracts item
names from drop messages, writes records to `data/drops.json`, and builds
weekly summaries from saved history.

### `modules/logger.py`

Creates the shared logger used across the project. Logs are written to stdout
and to `logs/farmer.log` with 5 MB rotation and three retained backups.

## Configuration and generated state

Configuration lives in `config/`. The example files document the expected
format, while local account and server files are intentionally kept out of git
because they are machine-specific.

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
