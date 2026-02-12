"""System tray icon for ASUSFanControlEnhanced (F1).

Provides a Windows system tray icon that displays the current CPU temperature,
allows profile switching, and supports graceful shutdown.

Requires: pip install pystray Pillow
"""

import signal
import threading
import time
import ctypes
import logging
import sys
from typing import TYPE_CHECKING

try:
    import pystray  # type: ignore[import-untyped]
    from PIL import Image, ImageDraw, ImageFont  # type: ignore[import-untyped]
except ImportError as e:
    raise ImportError(
        "pystray and Pillow are required for tray mode. "
        "Install with: pip install pystray Pillow"
    ) from e

if TYPE_CHECKING:
    from main import FanController

# Available profiles (must match PROFILES in main.py)
_PROFILE_NAMES = ("silent", "balanced", "performance")


def _get_console_window() -> int:
    """Return console HWND on Windows, else 0."""
    try:
        return int(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return 0


def _is_console_visible() -> bool:
    """Return True if a console window exists and is visible."""
    hwnd = _get_console_window()
    if hwnd == 0:
        return False
    try:
        return bool(ctypes.windll.user32.IsWindowVisible(hwnd))
    except Exception:
        return False


def _set_console_visible(visible: bool) -> None:
    """Show or hide the console window if it exists."""
    hwnd = _get_console_window()
    if hwnd == 0:
        return
    sw_show = 5
    sw_hide = 0
    try:
        ctypes.windll.user32.ShowWindow(hwnd, sw_show if visible else sw_hide)
    except Exception:
        pass


def _has_tray_console_handler(logger: logging.Logger) -> bool:
    """Return True if a tray-created console stream handler already exists."""
    for handler in logger.handlers:
        if getattr(handler, "_from_tray_console", False):
            return True
    return False


def _ensure_console_window(logger: logging.Logger) -> bool:
    """Ensure a console window exists and is wired to logging."""
    if _get_console_window() != 0:
        return True

    try:
        allocated = bool(ctypes.windll.kernel32.AllocConsole())
        if not allocated:
            return False

        # Rebind std streams to the newly allocated console.
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stdin = open("CONIN$", "r", encoding="utf-8")

        if not _has_tray_console_handler(logger):
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            setattr(handler, "_from_tray_console", True)
            logger.addHandler(handler)

        return True
    except Exception:
        return False


def _create_icon_image(temp: int = 0) -> "Image.Image":
    """Create a 64x64 tray icon image with the temperature displayed."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle colour based on temperature
    if temp <= 0:
        bg_colour = (100, 100, 100)  # Grey (unknown)
    elif temp < 50:
        bg_colour = (60, 160, 60)    # Green (cool)
    elif temp < 70:
        bg_colour = (220, 180, 30)   # Yellow (warm)
    else:
        bg_colour = (210, 50, 50)    # Red (hot)

    draw.ellipse([2, 2, 62, 62], fill=bg_colour)

    # Temperature text
    text = str(temp) if temp > 0 else "?"
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except (OSError, IOError):
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(((64 - tw) // 2, (64 - th) // 2 - 2), text, fill="white", font=font)

    return img


def run_with_tray(controller: "FanController") -> None:
    """Run the fan controller with a system tray icon on the main thread."""

    # In tray mode, keep console visible by default. If the process started
    # without a console (e.g. launched hidden), allocate one and attach logs.
    if _ensure_console_window(controller.logger):
        _set_console_visible(True)

    # -- Menu callbacks ------------------------------------------------------

    def on_quit(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        controller.stop()
        _icon.stop()

    def _set_profile(name: str):
        def handler(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            controller.set_profile(name)
        return handler

    def _is_profile(name: str):
        def checker(_item: pystray.MenuItem) -> bool:
            return controller.config.get("profile") == name
        return checker

    def _console_label(_item: pystray.MenuItem) -> str:
        if _is_console_visible():
            return "Hide Console"
        return "Show Console"

    def _toggle_console(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        if not _ensure_console_window(controller.logger):
            return
        _set_console_visible(not _is_console_visible())
        _icon.update_menu()

    # -- Build menu ----------------------------------------------------------

    profile_items = [
        pystray.MenuItem(
            name.capitalize(),
            _set_profile(name),
            checked=_is_profile(name),
        )
        for name in _PROFILE_NAMES
    ]

    menu = pystray.Menu(
        pystray.MenuItem("Profiles", pystray.Menu(*profile_items)),
        pystray.MenuItem(
            _console_label,
            _toggle_console,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon(
        "ASUSFanControl",
        _create_icon_image(),
        "ASUS Fan Control Enhanced",
        menu,
    )

    # -- Background threads --------------------------------------------------

    def fan_loop() -> None:
        """Run the fan controller in a daemon thread."""
        controller.run()
        # When the controller stops, also stop the tray icon
        icon.stop()

    def icon_updater() -> None:
        """Periodically update the tray icon image and tooltip."""
        while controller.running:
            temp = controller.previous_temp or 0
            speed = controller.current_set_fan_percentage or 0
            profile = controller.config.get("profile", "?")
            icon.icon = _create_icon_image(temp)
            icon.title = f"CPU: {temp}\u00b0C | Fan: {speed}% | {profile.capitalize()}"
            time.sleep(5)

    # -- Graceful Ctrl+C handling --------------------------------------------

    def _shutdown(signum: int = 0, frame: object = None) -> None:
        """Handle SIGINT (Ctrl+C) without the ctypes callback traceback."""
        controller.stop()
        try:
            icon.stop()
        except Exception:
            pass

    # Replace the default SIGINT handler so KeyboardInterrupt never reaches
    # pystray's win32 ctypes dispatcher (which can't propagate it cleanly).
    signal.signal(signal.SIGINT, _shutdown)

    # -- Start threads -------------------------------------------------------

    fan_thread = threading.Thread(target=fan_loop, daemon=True, name="fan-control")
    fan_thread.start()

    updater_thread = threading.Thread(target=icon_updater, daemon=True, name="tray-updater")
    updater_thread.start()

    # pystray runs its event loop on the main thread (required on Windows)
    icon.run()
