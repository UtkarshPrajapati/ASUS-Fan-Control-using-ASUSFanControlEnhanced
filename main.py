"""ASUSFanControlEnhanced - Dynamic fan control for ASUS laptops."""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Default configuration & preset profiles
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "low_temp": 20,
    "high_temp": 60,
    "min_speed": 10,
    "max_speed": 100,
    "log_max_bytes": 5000,
    "log_backup_count": 1,
    "subprocess_timeout": 10,
    "hysteresis_degrees": 3,
    "smoothing_window": 5,
    "spike_threshold": 15,
    "max_consecutive_failures": 10,
    "profile": "balanced",
    "curve": None,
    "enable_tray": True,
    "enable_notifications": False,
}

# Preset fan-curve profiles (E4).
# Each profile is a list of (temperature_C, fan_speed_%) waypoints.
PROFILES: Dict[str, List[Tuple[int, int]]] = {
    "silent": [
        (0, 0), (35, 5), (45, 15), (55, 35), (65, 55), (75, 80), (85, 100),
    ],
    "balanced": [
        (0, 10), (30, 10), (40, 25), (50, 50), (60, 75), (70, 100),
    ],
    "performance": [
        (0, 20), (25, 30), (35, 50), (45, 70), (55, 90), (65, 100),
    ],
}

# ASUS System Control Interface driver.  Versions beyond 3.1.38.0 break the
# AsusFanControl.exe CLI (temperature reads return 0).
COMPATIBLE_DRIVER_VERSION: Tuple[int, ...] = (3, 1, 38, 0)
DRIVER_DEVICE_ID_PATTERN = "*ASUS2018*"


def _parse_version(version_str: str) -> Tuple[int, ...]:
    """Parse a dotted version string into a comparable tuple of ints."""
    try:
        return tuple(int(x) for x in version_str.strip().split("."))
    except (ValueError, AttributeError):
        return (0,)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    """Load configuration from a JSON file, falling back to defaults.

    Priority: config file values override DEFAULT_CONFIG values.
    """
    config = DEFAULT_CONFIG.copy()
    path = Path(config_path)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            config.update(user_config)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not load config from {config_path}: {e}. Using defaults.")
    return config


def _is_interactive_user_session() -> bool:
    """Return True when a desktop/user session is available for tray UI.

    Some scheduled-task logon launches have an empty SESSIONNAME even though
    they are interactive. To avoid false negatives, fall back to Windows
    session-id detection: session 0 is non-interactive service context.
    """
    if sys.platform != "win32":
        return True

    # In startup tasks running before login this is commonly "Services".
    session_name = os.environ.get("SESSIONNAME", "").strip().lower()
    if session_name == "services":
        return False
    if session_name.startswith("console") or session_name.startswith("rdp-"):
        return True

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        process_id = kernel32.GetCurrentProcessId()
        session_id = ctypes.c_uint()
        ok = kernel32.ProcessIdToSessionId(process_id, ctypes.byref(session_id))
        if ok:
            return session_id.value != 0
    except Exception:
        pass

    # If we cannot determine reliably, prefer allowing tray startup.
    return True


def _enable_ansi_colors() -> bool:
    """Enable ANSI escape-code processing on the Windows console."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except Exception:
        return False


def _temp_to_ansi(temp: int) -> str:
    """Map a temperature to an ANSI RGB foreground escape sequence.

    Produces a smooth green -> yellow -> orange -> red gradient:
        < 35 C  bright green
        35-50 C  green to yellow
        50-65 C  yellow to orange-red
        > 65 C  bright red
    """
    temp = max(30, min(80, temp))
    if temp <= 50:
        # Green (70,210,70) -> Yellow (255,210,0)
        t = (temp - 30) / 20.0
        r, g, b = int(70 + 185 * t), int(210), int(70 * (1 - t))
    else:
        # Yellow (255,210,0) -> Red (255,55,55)
        t = (temp - 50) / 30.0
        r, g, b = 255, int(210 - 155 * t), int(55 * t)
    return f"\033[38;2;{r};{g};{b}m"


_ANSI_RESET = "\033[0m"
_ANSI_YELLOW = "\033[93m"
_ANSI_RED = "\033[91m"
_ANSI_BOLD_RED = "\033[1;91m"

# Pre-compiled pattern to find "CPU Temp: NNC" in log messages
_TEMP_RE = re.compile(r"CPU Temp: (\d+)C")


class ColoredFormatter(logging.Formatter):
    """Logging formatter that colour-codes console output.

    * WARNING  -> yellow
    * ERROR    -> red
    * CRITICAL -> bold red
    * INFO lines containing a temperature -> green/yellow/red gradient
    """

    _LEVEL_COLOURS = {
        logging.WARNING: _ANSI_YELLOW,
        logging.ERROR: _ANSI_RED,
        logging.CRITICAL: _ANSI_BOLD_RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)

        # Level-based colouring takes precedence
        colour = self._LEVEL_COLOURS.get(record.levelno)
        if colour:
            return f"{colour}{msg}{_ANSI_RESET}"

        # For INFO, try to colour by temperature
        if record.levelno == logging.INFO:
            m = _TEMP_RE.search(record.getMessage())
            if m:
                temp = int(m.group(1))
                return f"{_temp_to_ansi(temp)}{msg}{_ANSI_RESET}"

        return msg


def setup_logger(
    log_file: str = "fan_control.log",
    max_bytes: int = 5000,
    backup_count: int = 1,
    console_output: bool = True,
) -> logging.Logger:
    """Create a named logger with rotating file handler and optional console output (C4, C5)."""
    logger = logging.getLogger("fan_control")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt_str = "%(asctime)s - %(levelname)s - %(message)s"

    # Rotating file handler (keeps log size bounded) -- no colours in log file
    file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
    file_handler.setFormatter(logging.Formatter(fmt_str))
    logger.addHandler(file_handler)

    # Console handler with colour-coded output (C5)
    if console_output:
        ansi_ok = _enable_ansi_colors()
        console_handler = logging.StreamHandler(sys.stdout)
        if ansi_ok:
            console_handler.setFormatter(ColoredFormatter(fmt_str))
        else:
            console_handler.setFormatter(logging.Formatter(fmt_str))
        logger.addHandler(console_handler)

    return logger


def send_notification(title: str, message: str) -> None:
    """Send a Windows toast notification if winotify is installed (F2)."""
    try:
        from winotify import Notification  # type: ignore[import-untyped]

        toast = Notification(
            app_id="ASUS Fan Control Enhanced",
            title=title,
            msg=message,
        )
        toast.show()
    except ImportError:
        pass  # Notifications not available
    except Exception:
        pass  # Silently ignore notification failures


# ---------------------------------------------------------------------------
# FanController
# ---------------------------------------------------------------------------


class FanController:
    """Controls ASUS laptop fan speeds based on CPU temperature (C1)."""

    def __init__(self, config: Dict[str, Any], logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

        # State
        self.current_set_fan_percentage: Optional[int] = None
        self.previous_temp: Optional[int] = None
        self.consecutive_failures: int = 0
        self.temp_history: deque[int] = deque(maxlen=config.get("smoothing_window", 5))
        self.running: bool = False

        # Driver check state (throttled to avoid spamming PowerShell)
        self._last_driver_check: float = 0.0
        self._driver_incompatible: Optional[bool] = None  # None = not yet checked
        self._cached_driver_version: Optional[str] = None

        # Resolve the active fan curve
        self._fan_curve: List[Tuple[int, int]] = self._resolve_fan_curve()

    # -- Fan curve resolution ------------------------------------------------

    def _resolve_fan_curve(self) -> List[Tuple[int, int]]:
        """Resolve the fan curve: custom curve > profile > linear fallback (E3, E4)."""
        # 1. Explicit custom curve in config
        custom_curve = self.config.get("curve")
        if custom_curve:
            return sorted([tuple(p) for p in custom_curve], key=lambda x: x[0])

        # 2. Named preset profile
        profile_name = self.config.get("profile", "balanced")
        if profile_name in PROFILES:
            return PROFILES[profile_name]

        # 3. Fallback: build a simple linear ramp from legacy thresholds
        self.logger.warning(f"Unknown profile '{profile_name}', building linear curve from thresholds.")
        lo_t = self.config.get("low_temp", 20)
        hi_t = self.config.get("high_temp", 60)
        lo_s = self.config.get("min_speed", 10)
        hi_s = self.config.get("max_speed", 100)
        return [(lo_t, lo_s), (hi_t, hi_s)]

    def set_profile(self, profile_name: str) -> bool:
        """Switch to a named profile at runtime."""
        if profile_name not in PROFILES:
            self.logger.error(f"Unknown profile: {profile_name}")
            return False
        self.config["profile"] = profile_name
        self.config["curve"] = None
        self._fan_curve = PROFILES[profile_name]
        self.current_set_fan_percentage = None  # Force re-set on next cycle
        self.logger.info(f"Switched to profile: {profile_name}")
        return True

    # -- Startup validation --------------------------------------------------

    def validate_exe(self) -> bool:
        """Check that AsusFanControl.exe exists and responds (B1)."""
        exe_path = shutil.which("AsusFanControl.exe")
        if exe_path is None:
            local_exe = Path("AsusFanControl.exe")
            if not local_exe.exists():
                msg = (
                    "AsusFanControl.exe not found in PATH or current directory. "
                    "Please install it before running this script."
                )
                self.logger.error(msg)
                send_notification("Startup Error", msg)
                return False

        try:
            result = subprocess.run(
                ["AsusFanControl.exe", "--get-cpu-temp"],
                capture_output=True, text=True, check=True,
                timeout=self.config.get("subprocess_timeout", 10),
            )
            self.logger.info("AsusFanControl.exe validated successfully.")

            # Check if the exe returned temp 0 (driver issue indicator)
            output = result.stdout.strip()
            try:
                temp_val = int(output.split(": ")[1])
            except (IndexError, ValueError):
                temp_val = -1

            if temp_val == 0:
                self.logger.warning(
                    "AsusFanControl.exe returned temperature 0 -- possible driver issue."
                )
                self._check_driver_if_needed()
            else:
                # Still check driver version proactively on startup
                self._check_driver_if_needed()

            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            msg = f"AsusFanControl.exe validation failed: {e}"
            self.logger.error(msg)
            send_notification("Startup Error", msg)
            return False

    # -- Driver version detection --------------------------------------------

    def check_driver_version(self) -> Optional[str]:
        """Query the ASUS System Control Interface v3 driver version via WMI.

        Returns the version string (e.g. '3.1.38.0') or None if the driver
        is not found or the query fails.
        """
        try:
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_PnPSignedDriver "
                    "| Where-Object {$_.DeviceID -like '"
                    + DRIVER_DEVICE_ID_PATTERN
                    + "'} "
                    "| Select-Object -ExpandProperty DriverVersion",
                ],
                capture_output=True, text=True,
                timeout=15,
            )
            version = result.stdout.strip()
            if version:
                return version
        except Exception as e:
            self.logger.debug(f"Driver version query failed: {e}")
        return None

    def _check_driver_if_needed(self) -> None:
        """Check the ASUS driver version (throttled to once per 60 s).

        When the exe returns temp == 0 this is the most likely cause.
        Sets ``self._driver_incompatible`` and logs an actionable warning.
        """
        now = time.time()
        if now - self._last_driver_check < 60:
            return  # Already checked recently
        self._last_driver_check = now

        version_str = self.check_driver_version()
        self._cached_driver_version = version_str

        if version_str is None:
            self.logger.warning(
                "Could not determine ASUS System Control Interface driver version."
            )
            self._driver_incompatible = None
            return

        current = _parse_version(version_str)
        if current > COMPATIBLE_DRIVER_VERSION:
            self._driver_incompatible = True
            compatible_str = ".".join(str(x) for x in COMPATIBLE_DRIVER_VERSION)
            self.logger.critical(
                f"ASUS System Control Interface driver v{version_str} is INCOMPATIBLE "
                f"(temp reads return 0). Roll back to v{compatible_str} or earlier.  "
                f"Device Manager > System devices > ASUS System Control Interface v3 "
                f"> Driver tab > Roll Back Driver."
            )
            send_notification(
                "Driver Incompatible",
                f"ASUS driver v{version_str} breaks fan control. "
                f"Roll back to v{compatible_str}.",
            )
        else:
            self._driver_incompatible = False
            self.logger.info(
                f"ASUS driver version {version_str} is compatible."
            )

    # -- Subprocess helper (DRY) ---------------------------------------------

    def _run_command(self, args: List[str]) -> Optional[str]:
        """Run a subprocess command with timeout and unified error handling (A3, C2)."""
        timeout = self.config.get("subprocess_timeout", 10)
        try:
            result = subprocess.run(
                args,
                capture_output=True, text=True, check=True,
                timeout=timeout,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Command failed [{' '.join(args)}]: {e}")
        except subprocess.TimeoutExpired:
            self.logger.error(f"Command timed out after {timeout}s: {' '.join(args)}")
        except FileNotFoundError:
            self.logger.error("AsusFanControl.exe not found. Ensure it is in the system PATH.")
        return None

    # -- Temperature & fan speed I/O -----------------------------------------

    def get_cpu_temp(self) -> Optional[int]:
        """Get the current CPU temperature (C3: type-hinted)."""
        output = self._run_command(["AsusFanControl.exe", "--get-cpu-temp"])
        if output is None:
            return None
        try:
            temp_str = output.split(": ")[1]
            return int(temp_str)
        except (IndexError, ValueError):
            self.logger.error(f"Error parsing CPU temperature from output: '{output}'")
            return None

    def get_current_fan_speeds(self) -> Optional[str]:
        """Get the current fan speeds reported by the hardware."""
        output = self._run_command(["AsusFanControl.exe", "--get-fan-speeds"])
        if output is None:
            return None
        try:
            return output.split(": ")[1]
        except IndexError:
            self.logger.error(f"Error parsing fan speeds from output: '{output}'")
            return None

    def set_fan_speed(self, percentage: int) -> bool:
        """Set the fan speed. Only issues the command if the value changed (B4)."""
        if self.current_set_fan_percentage == percentage:
            self.logger.info(f"Fan speed already at {percentage}%, no change needed.")
            return True

        output = self._run_command(
            ["AsusFanControl.exe", f"--set-fan-speeds={percentage}"]
        )
        if output is not None:
            self.logger.info(output)
            self.current_set_fan_percentage = percentage
            return True

        self.logger.error(f"Failed to set fan speed to {percentage}%.")
        return False

    # -- Fan speed decision logic --------------------------------------------

    def decide_fan_speed(self, temp: int) -> int:
        """Decide fan speed using the active curve with hysteresis (E1, E3)."""
        curve = self._fan_curve

        # Clamp to curve boundaries
        if temp <= curve[0][0]:
            target = curve[0][1]
        elif temp >= curve[-1][0]:
            target = curve[-1][1]
        else:
            # Linear interpolation between surrounding waypoints
            target = curve[-1][1]
            for i in range(len(curve) - 1):
                t_low, s_low = curve[i]
                t_high, s_high = curve[i + 1]
                if t_low <= temp <= t_high:
                    if t_high == t_low:
                        target = s_high
                    else:
                        fraction = (temp - t_low) / (t_high - t_low)
                        target = round(s_low + fraction * (s_high - s_low))
                    break

        # Hysteresis: resist *decreasing* fan speed unless temp dropped enough (E1)
        hysteresis = self.config.get("hysteresis_degrees", 3)
        if (
            self.current_set_fan_percentage is not None
            and target < self.current_set_fan_percentage
            and self.previous_temp is not None
        ):
            temp_drop = self.previous_temp - temp
            if temp_drop < hysteresis:
                target = self.current_set_fan_percentage

        return max(0, min(100, target))

    # -- Temperature smoothing & spike detection -----------------------------

    def get_smoothed_temp(self, raw_temp: int) -> int:
        """Return a rolling-average smoothed temperature (E2)."""
        self.temp_history.append(raw_temp)
        return round(sum(self.temp_history) / len(self.temp_history))

    def detect_spike(self, current_temp: int) -> bool:
        """Detect a sudden temperature spike (B2)."""
        threshold = self.config.get("spike_threshold", 15)
        if self.previous_temp is not None:
            delta = current_temp - self.previous_temp
            if delta >= threshold:
                self.logger.warning(
                    f"Temperature spike: {self.previous_temp}C -> {current_temp}C "
                    f"(+{delta}C >= {threshold}C threshold)"
                )
                return True
        return False

    # -- Adaptive sleep ------------------------------------------------------

    def adaptive_sleep(self, temp: Optional[int]) -> int:
        """Return sleep duration in seconds based on current CPU temperature."""
        high = self.config.get("high_temp", 60)
        if temp is None or temp <= 0:
            return 3   # Short sleep on error
        elif temp < high - 10:
            return 10  # Cool: long sleep
        elif temp < high:
            return 5   # Warm: medium sleep
        else:
            return 3   # Hot: short sleep

    # -- Main control loop ---------------------------------------------------

    def run(self) -> None:
        """Main control loop."""
        max_failures = self.config.get("max_consecutive_failures", 10)
        notify_enabled = self.config.get("enable_notifications", False)
        self.running = True

        self.logger.info("Fan control loop started.")

        while self.running:
            try:
                raw_temp = self.get_cpu_temp()

                if raw_temp is not None and raw_temp > 0:
                    self.consecutive_failures = 0

                    # Spike detection (B2)
                    if self.detect_spike(raw_temp):
                        fan_speed = 100
                        self.logger.warning(f"Spike response: forcing fan to {fan_speed}%.")
                        if notify_enabled:
                            send_notification(
                                "Temperature Spike",
                                f"CPU jumped to {raw_temp}C - fan set to 100%.",
                            )
                    else:
                        smoothed_temp = self.get_smoothed_temp(raw_temp)
                        fan_speed = self.decide_fan_speed(smoothed_temp)

                    self.previous_temp = raw_temp

                elif raw_temp == 0:
                    # Temp == 0 is a strong signal that the ASUS System Control
                    # Interface driver has been updated to an incompatible version.
                    self.consecutive_failures += 1
                    fan_speed = 100

                    self._check_driver_if_needed()

                    if self._driver_incompatible:
                        self.logger.critical(
                            f"Temp reads 0 (driver incompatible). Fan locked at 100%. "
                            f"Roll back the ASUS System Control Interface driver."
                        )
                    else:
                        self.logger.warning(
                            f"Temp reads 0 ({self.consecutive_failures}/{max_failures}). "
                            f"Safety: fan set to 100%."
                        )

                    if self.consecutive_failures >= max_failures and notify_enabled:
                        send_notification(
                            "Fan Control Error",
                            "Temp stuck at 0. Likely an ASUS driver update. Fan at 100%.",
                        )

                else:
                    # raw_temp is None or negative -- exe failure
                    self.consecutive_failures += 1
                    fan_speed = 100  # Safety: max fan if temp unknown

                    if self.consecutive_failures >= max_failures:
                        self.logger.critical(
                            f"Temperature reading failed {self.consecutive_failures} times "
                            f"consecutively. Fan locked at 100%. Check AsusFanControl.exe."
                        )
                        if notify_enabled:
                            send_notification(
                                "Fan Control Error",
                                f"Temp reading failed {self.consecutive_failures}x. Fan at 100%.",
                            )
                    else:
                        self.logger.warning(
                            f"Temp read failure ({self.consecutive_failures}/{max_failures}). "
                            f"Safety: fan set to 100%."
                        )

                # Set fan speed and check result (B4)
                success = self.set_fan_speed(fan_speed)
                if not success:
                    self.logger.error("Failed to apply fan speed setting.")

                # Log status
                current_fan_speeds = self.get_current_fan_speeds()
                temp_display = f"{raw_temp}C" if raw_temp is not None else "Unknown"
                fan_display = current_fan_speeds if current_fan_speeds else "Unknown"
                self.logger.info(
                    f"CPU Temp: {temp_display}, Fan Speed: {fan_display} (Target: {fan_speed}%)"
                )

                sleep_time = self.adaptive_sleep(raw_temp)
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                self.logger.info("Script terminated by user.")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error in control loop: {e}")
                time.sleep(3)

        self.running = False
        self.logger.info("Fan control loop stopped.")

    def stop(self) -> None:
        """Signal the control loop to stop gracefully."""
        self.running = False


# ---------------------------------------------------------------------------
# CLI argument parsing (D2)
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments. CLI values override config file values."""
    parser = argparse.ArgumentParser(
        description="ASUSFanControlEnhanced - Dynamic fan control for ASUS laptops.",
    )
    parser.add_argument(
        "--config", type=str, default="config.json",
        help="Path to configuration JSON file (default: config.json)",
    )
    parser.add_argument(
        "--profile", type=str, choices=list(PROFILES.keys()),
        help="Fan curve profile: silent, balanced, performance",
    )
    parser.add_argument("--low-temp", type=int, help="Low temperature threshold (C)")
    parser.add_argument("--high-temp", type=int, help="High temperature threshold (C)")
    parser.add_argument("--min-speed", type=int, help="Minimum fan speed %%")
    parser.add_argument("--max-speed", type=int, help="Maximum fan speed %%")
    parser.add_argument(
        "--no-console", action="store_true",
        help="Disable console output (log to file only)",
    )
    parser.add_argument("--tray", action="store_true", help="Show system tray icon")
    parser.add_argument(
        "--no-tray", action="store_true",
        help="Disable system tray icon even if enabled in config",
    )
    parser.add_argument(
        "--skip-validation", action="store_true",
        help="Skip AsusFanControl.exe startup validation",
    )
    parser.add_argument(
        "--notifications", action="store_true",
        help="Enable Windows toast notifications for critical events",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point: load config, parse args, validate, and run."""
    args = parse_args()

    # Load config from file (D1)
    config = load_config(args.config)

    # CLI args override config file values (D2)
    cli_overrides: Dict[str, Any] = {
        "profile": args.profile,
        "low_temp": args.low_temp,
        "high_temp": args.high_temp,
        "min_speed": args.min_speed,
        "max_speed": args.max_speed,
    }
    for key, value in cli_overrides.items():
        if value is not None:
            config[key] = value
    if args.tray:
        config["enable_tray"] = True
    if args.no_tray:
        config["enable_tray"] = False
    if args.notifications:
        config["enable_notifications"] = True

    console_output = not args.no_console

    # Set up logger (C4: named logger, C5: console output)
    logger = setup_logger(
        max_bytes=config.get("log_max_bytes", 5000),
        backup_count=config.get("log_backup_count", 1),
        console_output=console_output,
    )

    curve_source = "custom" if config.get("curve") else config.get("profile", "balanced")
    logger.info(f"Configuration loaded (curve source: {curve_source})")

    # Create controller
    controller = FanController(config, logger)

    # Startup validation (B1)
    if not args.skip_validation:
        if not controller.validate_exe():
            logger.critical("Startup validation failed. Exiting.")
            sys.exit(1)

    # System tray mode (F1) only works with an interactive desktop.
    tray_requested = bool(config.get("enable_tray"))
    if tray_requested and not _is_interactive_user_session():
        logger.warning(
            "Tray mode requested but no interactive desktop session is available "
            "(startup task before login). Running headless."
        )
        tray_requested = False

    if tray_requested:
        try:
            from tray import run_with_tray  # type: ignore[import-untyped]

            run_with_tray(controller)
        except ImportError:
            logger.warning(
                "System tray dependencies not installed (pystray, Pillow). "
                "Install with: pip install pystray Pillow. Running without tray."
            )
            controller.run()
    else:
        controller.run()


if __name__ == "__main__":
    main()
