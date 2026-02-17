# ASUS Fan Control Enhanced

A Python script to dynamically control ASUS laptop fan speeds based on CPU temperature, using the `AsusFanControl.exe` utility.

## Features

- **Dynamic Fan Control** -- adjusts fan speed using configurable multi-point curves with linear interpolation between waypoints.
- **Preset Profiles** -- choose between *Silent*, *Balanced*, and *Performance* fan curves, or define your own custom curve.
- **Temperature Smoothing** -- uses a rolling average over the last N readings to reduce noise from transient spikes.
- **Hysteresis** -- resists decreasing fan speed until the temperature drops by a configurable margin, preventing oscillation.
- **Spike Protection** -- detects sudden temperature jumps and immediately sets fans to 100%.
- **Adaptive Sleep** -- polls more frequently when the CPU is hot and less frequently when it is cool.
- **Startup Validation** -- verifies `AsusFanControl.exe` is present and responsive before entering the control loop.
- **Consecutive Failure Tracking** -- escalates logging severity after repeated read failures and locks fans at 100%.
- **External Configuration** -- all settings live in `config.json`; no need to edit source code.
- **CLI Overrides** -- command-line arguments override config file values on the fly.
- **System Tray Icon** -- enabled by default in interactive sessions, with profile switching, dashboard toggle, and console management.
- **Live Dashboard** -- left-click the tray icon to open a real-time dashboard showing CPU temperature, fan speed, profile, driver status, and more.
- **Console Management** -- console window can be shown/hidden from the tray menu. The close (X) button is disabled to prevent accidentally killing the process, and minimizing the console hides it to the tray.
- **Temperature-Coloured Console Logs** -- terminal lines use a green->yellow->red gradient based on CPU temperature.
- **Windows Toast Notifications** (optional) -- alerts on critical events like overheating or repeated failures.
- **Driver Compatibility Detection** -- detects incompatible ASUS System Control Interface driver updates (versions above 3.1.38.0 cause temperature reads to return 0).
- **Automatic Driver Rollback** -- when an incompatible driver is detected, automatically rolls back to the previous version using the Win32 `DiRollbackDriver` API. Requires Administrator privileges. Disable with `"auto_rollback_driver": false` in config.
- **Rotating Log** -- logs to `runtime/logs/fan_control.log` with automatic rotation to keep file size bounded.
- **Console Output** -- live status output in the terminal alongside the log file.
- **Auto-Start at System Startup** -- PowerShell script to register a Task Scheduler startup task.

## Prerequisites

1. **Python 3.8+** installed and on PATH.
2. **`AsusFanControl.exe`** -- place this utility in `runtime/bin/AsusFanControl.exe` (recommended). PATH fallback still works if the configured path is missing.

### Optional dependencies

Install for extra features:

```bash
pip install -r requirements.txt
```

| Package | Feature |
|---------|---------|
| `pystray` + `Pillow` | System tray icon (`--tray`) |
| `winotify` | Windows toast notifications (`--notifications`) |

## Installation

```bash
git clone https://github.com/UtkarshPrajapati/ASUS-Fan-Control-using-ASUSFanControlEnhanced
cd ASUS-Fan-Control-using-ASUSFanControlEnhanced
```

Place `AsusFanControl.exe` at `runtime/bin/AsusFanControl.exe` (default configured location).

## Usage

### Basic

```bash
python main.py
```

The script validates `AsusFanControl.exe`, then runs in a loop adjusting fan speed. Press `Ctrl+C` to stop.

### With a specific profile

```bash
python main.py --profile silent
python main.py --profile performance
```

### With CLI overrides

```bash
python main.py --high-temp 55 --min-speed 20 --profile balanced
```

### With system tray icon

```bash
python main.py --tray
```

Tray is enabled by default in `config.json`. Use `--no-tray` to disable it.

Tray behaviour:

- **Left-click** the icon to toggle the live dashboard.
- **Right-click** for the menu: Show/Hide Dashboard, Profiles, Show/Hide Console, Quit.
- The console **close (X) button is disabled** to prevent killing the process -- use the tray menu instead.
- **Minimizing** the console window hides it to the tray automatically.

### With notifications

```bash
python main.py --notifications
```

### Skip startup validation

```bash
python main.py --skip-validation
```

### All CLI options

```
--config PATH       Path to config JSON file (default: <app_dir>/config.json)
--profile NAME      Fan curve profile: silent, balanced, performance
--low-temp N        Low temperature threshold (C)
--high-temp N       High temperature threshold (C)
--min-speed N       Minimum fan speed percentage
--max-speed N       Maximum fan speed percentage
--no-console        Disable console output (log to file only)
--tray              Show system tray icon
--no-tray           Disable system tray icon
--notifications     Enable Windows toast notifications
--skip-validation   Skip AsusFanControl.exe startup check
```

## Configuration

All settings are in `config.json`. The file is optional -- if missing, built-in defaults from `DEFAULT_CONFIG` in `main.py` are used. CLI arguments take the highest priority.

`DEFAULT_CONFIG` exists as a safety baseline/schema so the app still works even
if `config.json` is missing, incomplete, or invalid JSON.

```json
{
    "runtime_dir": "runtime",
    "tool_executable": "runtime/bin/AsusFanControl.exe",
    "low_temp": 20,
    "high_temp": 60,
    "min_speed": 10,
    "max_speed": 100,
    "log_file": "runtime/logs/fan_control.log",
    "log_max_bytes": 5000,
    "log_backup_count": 1,
    "subprocess_timeout": 10,
    "hysteresis_degrees": 3,
    "smoothing_window": 5,
    "spike_threshold": 15,
    "max_consecutive_failures": 10,
    "driver_check_interval_seconds": 60,
    "adaptive_sleep_error_seconds": 3,
    "adaptive_sleep_cool_seconds": 10,
    "adaptive_sleep_warm_seconds": 5,
    "adaptive_sleep_hot_seconds": 3,
    "adaptive_sleep_cool_margin": 10,
    "tray_icon_update_interval_seconds": 2,
    "dashboard_refresh_interval_ms": 1000,
    "dashboard_width": 520,
    "dashboard_height": 360,
    "dashboard_min_width": 460,
    "dashboard_min_height": 320,
    "dashboard_margin": 14,
    "dashboard_bottom_offset": 56,
    "console_visible_on_start": false,
    "console_maximized": true,
    "auto_rollback_driver": true,
    "profile": "balanced",
    "curve": null,
    "enable_tray": true,
    "enable_notifications": false
}
```

### Key reference

| Key | Default | Description |
|-----|---------|-------------|
| `console_visible_on_start` | `false` | Show the console window when the tray starts. Toggle from the tray menu anytime. |
| `console_maximized` | `true` | Maximize the console window on show (only when `console_visible_on_start` is `true`). |
| `auto_rollback_driver` | `true` | Automatically roll back the ASUS System Control Interface driver when an incompatible version is detected. |
| `dashboard_*` | various | Size, position margins, and refresh rate for the live dashboard window. |
| `adaptive_sleep_*` | various | Poll intervals (seconds) for cool, warm, hot, and error temperature ranges. |
| `driver_check_interval_seconds` | `60` | How often (seconds) to re-check the ASUS driver version when temps read 0. |

### Runtime folders

- `runtime/bin/` -- expected location for `AsusFanControl.exe`
- `runtime/logs/` -- rotating log output location

These folders are created automatically at startup if missing.

### Custom fan curve

Set `"curve"` to a list of `[temperature, speed%]` waypoints. This overrides the profile:

```json
{
    "curve": [[30, 10], [40, 25], [50, 50], [60, 80], [70, 100]]
}
```

Temperatures between waypoints are linearly interpolated. Below the first point the speed is clamped to the first value; above the last point it is clamped to the last value.

### Preset profiles

| Profile | Behaviour |
|---------|-----------|
| `silent` | Fans stay very low until temperatures climb well above 50C. Prioritises noise reduction. |
| `balanced` | Moderate ramp-up starting around 40C. Good mix of cooling and noise. |
| `performance` | Aggressive cooling. Fans ramp up early and reach 100% around 65C. |

## Auto-Start at System Startup

Use the included PowerShell script to register **two** Windows Task Scheduler tasks:

- `ASUSFanControlEnhanced` (core)
  - Trigger: **At system startup**
  - Logon mode: **Run whether user is logged on or not**
  - Security option: **Do not store password (S4U)**
  - Purpose: headless thermal safety before login
- `ASUSFanControlEnhancedTray` (UI)
  - Trigger: **At user logon**
  - Logon mode: **Interactive**
  - Purpose: tray icon and dashboard in desktop session

Note: `Get-ScheduledTask` may report compatibility as `Win8`/`Win10` because
the `ScheduledTasks` module uses legacy enum labels. This is normal on modern
Windows builds when the task is configured correctly in Task Scheduler UI.

```powershell
# Install
.\autostart.ps1 install

# Uninstall
.\autostart.ps1 uninstall
```

When started before login, no interactive desktop exists, so tray UI is unavailable.
The controller automatically falls back to headless mode and continues controlling fans.

## Running as a Windows Service (F3)

For a true background service that persists across user sessions, use [NSSM (Non-Sucking Service Manager)](https://nssm.cc/):

```bash
# Install NSSM, then:
nssm install ASUSFanControl "C:\path\to\python.exe" "C:\path\to\main.py --no-console"
nssm start ASUSFanControl
```

## Logging

Activity is logged to `runtime/logs/fan_control.log` (rotating, bounded by `log_max_bytes`). Use `fanlog.bat` to tail the log:

```bash
fanlog.bat
```

## How It Works

1. **Validate** -- on startup, confirms `AsusFanControl.exe` is present and working.
2. **Read Temperature** -- calls `AsusFanControl.exe --get-cpu-temp` and parses the output.
3. **Smooth** -- adds the reading to a rolling window and computes the average.
4. **Spike Check** -- if the temperature jumped by more than `spike_threshold` degrees since the last reading, fans go to 100% immediately.
5. **Decide Speed** -- interpolates along the active fan curve to find the target speed.
6. **Hysteresis** -- if the new target is lower than the current setting, only decrease if the temperature has dropped by at least `hysteresis_degrees`.
7. **Set Speed** -- calls `AsusFanControl.exe --set-fan-speeds=<N>` only when the target differs from the current setting.
8. **Log & Sleep** -- logs the current state and sleeps for an adaptive duration.
9. **Repeat** until stopped.

## Contributing

Contributions are welcome! Open an issue or submit a pull request.
