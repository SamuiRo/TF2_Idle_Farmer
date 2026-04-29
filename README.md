# TF2 Idle Farmer

Automated weekly drop-farming tool for Team Fortress 2.  
Cycles through multiple Steam accounts, launches TF2 in a minimal-resource mode,
idles on a dedicated server for 65–80 minutes (enough to trigger the weekly drop
timer), logs results, and repeats next week.

---

## Requirements

- **Python 3.11+** recommended — `tomllib` is included in the standard library
- **Python 3.10** also works — install the `tomli` backport (see below)
- **Windows** (Steam / TF2 are Windows-only)
- All accounts must already be logged in to Steam at least once on this machine
  (their entries must exist in `loginusers.vdf`)

### Install dependencies

```
pip install -e .
```

**If you are on Python 3.10**, also run:

```
pip install tomli
```

`tomllib` was added to the Python standard library in 3.11. On 3.10 the code
falls back to the third-party `tomli` package which is API-compatible.

---

## Quick start

### 1. Configure accounts

Edit `config/accounts.txt` — one Steam login name per line:

```
my_account_1
my_account_2
```

> Every account must have been logged in manually at least once on this machine
> with **"Remember my password"** checked so Steam can sign in automatically.

### 2. Configure servers

Edit `config/servers.txt` — idle-friendly TF2 servers (one `IP:PORT` per line):

```
103.28.54.100:27015
185.107.96.50:27015
```

### 3. Configure paths

Open `config/settings.toml` and set the paths for your Steam installation.
Use forward slashes `/` — they work on Windows and avoid escape issues.

```toml
[paths]
steam_exe      = "C:/Program Files (x86)/Steam/steam.exe"
loginusers_vdf = "C:/Program Files (x86)/Steam/config/loginusers.vdf"
tf2_cfg_dir    = "C:/Program Files (x86)/Steam/steamapps/common/Team Fortress 2/tf/cfg"
```

> If Steam or TF2 is installed on a different drive, adjust accordingly.
> Example for a secondary library on S:\:
> ```toml
> tf2_cfg_dir = "S:/SteamLibrary/steamapps/common/Team Fortress 2/tf/cfg"
> ```

### 4. Run

**Single run (all accounts once):**
```
python main.py
```

**Scheduled mode (repeats every Monday at 09:00, runs immediately on first start):**
```
python main.py --schedule
```

---

## Project structure

```
tf2-idle-farmer/
├── main.py                  ← entry point & orchestrator
├── config/
│   ├── accounts.txt         ← Steam login names (one per line)
│   ├── servers.txt          ← idle server list (IP:PORT, one per line)
│   └── settings.toml        ← all tuneable settings
├── modules/
│   ├── steam_manager.py     ← switch account, launch/quit Steam
│   ├── tf2_manager.py       ← launch TF2, generate autoexec.cfg, quit
│   ├── human_behavior.py    ← random delays, mouse movement, MOTD dismiss
│   ├── drop_tracker.py      ← parse console.log, save drops.json
│   └── logger.py            ← rotating file + console logger
├── data/
│   └── drops.json           ← drop history per account (auto-created)
├── logs/
│   └── farmer.log           ← run log (auto-created)
└── requirements.txt
```

---

## How it works

For each account in `accounts.txt`:

1. Shut down any running Steam instance
2. Edit `loginusers.vdf` — set this account as `MostRecent` and `RememberPassword = 1`
3. Launch Steam with `-login <username> -silent` so it signs in without showing the account-picker GUI
4. Wait for Steam to fully initialise and complete login (~15 s stabilisation after process detection)
5. Pick a random server from `servers.txt` and generate `autoexec.cfg` with performance tweaks and `connect <server>`
6. Clear `console.log` so stale drops from previous sessions are not re-counted
7. Launch TF2 with minimal-resource flags (`-novid -nosound -sw -low -condebug …`)
8. Wait 20–40 s for the map to load, then automatically dismiss the server MOTD window (8 attempts × 4 s — covers servers with multiple stacked welcome screens)
9. Idle 65–80 min with occasional random mouse moves / key presses every 3–10 min
10. Parse `console.log` for drop messages → save results to `data/drops.json`
11. Quit TF2 → quit Steam → pause → move to next account

> **Note on the MOTD screen:** The welcome popup that appears after connecting
> *must* be dismissed for the drop timer to start. The death-notification popup
> that appears when an item drops does not need to be closed — it disappears on
> its own and does not affect the timer.

---

## Settings reference (`settings.toml`)

```toml
[paths]
steam_exe      = "C:/Program Files (x86)/Steam/steam.exe"
loginusers_vdf = "C:/Program Files (x86)/Steam/config/loginusers.vdf"
tf2_cfg_dir    = "C:/Program Files (x86)/Steam/steamapps/common/Team Fortress 2/tf/cfg"
# Optional: override the console.log path (defaults to tf2_cfg_dir/../console.log)
# console_log  = "C:/.../Team Fortress 2/tf/console.log"

[timing]
idle_duration_min          = 65   # min idle time per account (minutes)
idle_duration_max          = 80   # max idle time per account (minutes)
steam_startup_wait         = 60   # timeout waiting for Steam process (seconds)
steam_warmup_min           = 30   # min extra wait after Steam detected (seconds)
steam_warmup_max           = 60   # max extra wait after Steam detected (seconds)
tf2_startup_wait           = 90   # timeout waiting for TF2 process (seconds)
pause_between_accounts_min = 15   # min pause between accounts (seconds)
pause_between_accounts_max = 35   # max pause between accounts (seconds)

[behavior]
shuffle_accounts = true   # randomise account order each run
shuffle_servers  = true   # pick a random server each session
mouse_activity   = true   # move mouse during idle
```

### Tuning for slow machines / HDD

If TF2 takes longer than 90 s to start, or Steam does not finish logging in
before TF2 launches, increase these values:

```toml
steam_warmup_min   = 45
steam_warmup_max   = 90
tf2_startup_wait   = 150
```

---

## Logs & data

- **`logs/farmer.log`** — full run log with timestamps, rotates at 5 MB (3 backups kept)
- **`data/drops.json`** — cumulative drop history per account

Example `drops.json` entry:

```json
{
  "my_account_1": [
    {
      "date": "2025-04-20",
      "timestamp": "2025-04-20T15:44:55",
      "items": ["The Holy Mackerel"],
      "session_duration_min": 71.3
    }
  ]
}
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: No module named 'tomllib'` | Python 3.10 or older | `pip install tomli` |
| Steam shows account-picker GUI | Account has no saved password | Log in manually once with "Remember my password" checked |
| `[WinError 123] filename syntax incorrect` | Wrong path in `settings.toml` (e.g. `CS:\` instead of `C:\`) | Fix the path; use forward slashes `/` |
| TF2 startup timeout | Slow HDD / first launch after update | Increase `tf2_startup_wait` to `150` or more |
| MOTD not dismissed / drop timer not starting | Server has extra welcome screens | Increase `dismiss_motd` attempts in `main.py` or `interval_sec` |
| No drops recorded after session | `console.log` not written | Verify `-condebug` is in launch options and `tf2_cfg_dir` path is correct |