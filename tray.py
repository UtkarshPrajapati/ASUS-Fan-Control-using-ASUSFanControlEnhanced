"""System tray icon for ASUSFanControlEnhanced (F1).

Provides a Windows system tray icon that displays the current CPU temperature,
allows profile switching, and supports graceful shutdown.

Requires: pip install pystray Pillow
"""

import signal
import threading
import time
import ctypes
from ctypes import wintypes
import logging
import sys
from typing import TYPE_CHECKING, Optional

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]

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
    try:
        if visible:
            ctypes.windll.user32.ShowWindow(hwnd, 5)       # SW_SHOW
            # If the window was minimized before it was hidden, SW_SHOW
            # restores it in the minimized state and the minimize-watcher
            # would immediately hide it again.  SW_RESTORE clears that.
            if ctypes.windll.user32.IsIconic(hwnd):
                ctypes.windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        else:
            ctypes.windll.user32.ShowWindow(hwnd, 0)       # SW_HIDE
    except Exception:
        pass


def _disable_console_close_button() -> None:
    """Grey-out the console window's X button so it cannot kill the process.

    Removing SC_CLOSE from the system menu disables the close button and
    the "Close" entry in the title-bar context menu.  The user should use
    the tray icon's "Hide Console" / "Quit" options instead.
    """
    hwnd = _get_console_window()
    if not hwnd:
        return
    try:
        SC_CLOSE = 0xF060
        MF_BYCOMMAND = 0x00000000
        hmenu = ctypes.windll.user32.GetSystemMenu(hwnd, False)
        if hmenu:
            ctypes.windll.user32.DeleteMenu(hmenu, SC_CLOSE, MF_BYCOMMAND)
    except Exception:
        pass


# Prevent garbage-collection of the ctypes callback.
_console_ctrl_handler_ref = None


def _install_console_close_handler() -> None:
    """Backup handler: hide console instead of dying on CTRL_CLOSE_EVENT.

    If something still triggers CTRL_CLOSE_EVENT (e.g. Task Manager
    End-Task, or the close button was re-enabled), we hide the console
    and return TRUE to tell Windows we handled the event.
    """
    global _console_ctrl_handler_ref
    if sys.platform != "win32":
        return

    CTRL_CLOSE_EVENT = 2
    HANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    @HANDLER_ROUTINE
    def _handler(event: int) -> bool:
        if event == CTRL_CLOSE_EVENT:
            _set_console_visible(False)
            return True          # handled – do NOT terminate the process
        return False              # let the default / next handler deal with it

    _console_ctrl_handler_ref = _handler          # prevent GC
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler, True)


def _start_console_minimize_watcher(on_hide: "Optional[callable]" = None) -> None:
    """Treat the console minimize button as 'hide console'.

    A lightweight daemon thread polls ``IsIconic`` every 150 ms.  When the
    user clicks minimize, the console is hidden instead of sitting in the
    taskbar -- the tray menu "Show Console" can bring it back.

    *on_hide* is an optional callback invoked after the console is hidden
    (e.g. ``icon.update_menu`` to refresh the tray label).
    """
    def _watch() -> None:
        is_iconic = ctypes.windll.user32.IsIconic
        while True:
            if not _is_console_visible():
                time.sleep(1)           # nothing to watch while hidden
                continue
            hwnd = _get_console_window()
            if hwnd:
                try:
                    if is_iconic(hwnd):
                        _set_console_visible(False)
                        if on_hide:
                            try:
                                on_hide()
                            except Exception:
                                pass
                except Exception:
                    pass
            time.sleep(0.15)

    threading.Thread(target=_watch, daemon=True, name="console-min-watcher").start()


def _enable_high_dpi_mode() -> None:
    """Enable high-DPI awareness so the dashboard does not look blurry."""
    if sys.platform != "win32":
        return

    # Best to fallback order:
    # 1) Per-monitor v2 DPI awareness (Win10+)
    # 2) Per-monitor DPI awareness (shcore)
    # 3) System DPI aware (legacy)
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _get_work_area_bounds() -> Optional[tuple[int, int, int, int]]:
    """Return usable desktop bounds (excluding taskbar) on Windows."""
    if sys.platform != "win32":
        return None
    rect = wintypes.RECT()
    # SPI_GETWORKAREA = 0x0030
    ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    if not ok:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _compute_dashboard_position(
    root: "tk.Tk",
    width: int,
    height: int,
    margin: int = 14,
    bottom_safe_margin: int = 56,
) -> tuple[int, int]:
    """Compute a reliable bottom-right position in Tk coordinate space."""
    screen_w = int(root.winfo_screenwidth())
    screen_h = int(root.winfo_screenheight())

    # Default to full screen bounds in Tk coordinates.
    left = 0
    top = 0
    right = screen_w
    bottom = screen_h

    # If work-area exists, map it into Tk coordinates to account for DPI scaling
    # differences between Win32 pixels and Tk units.
    work = _get_work_area_bounds()
    if work and sys.platform == "win32":
        left_w, top_w, right_w, bottom_w = work
        try:
            full_w = int(ctypes.windll.user32.GetSystemMetrics(0))
            full_h = int(ctypes.windll.user32.GetSystemMetrics(1))
        except Exception:
            full_w, full_h = screen_w, screen_h

        if full_w > 0 and full_h > 0:
            scale_x = screen_w / full_w
            scale_y = screen_h / full_h
            left = int(left_w * scale_x)
            top = int(top_w * scale_y)
            right = int(right_w * scale_x)
            bottom = int(bottom_w * scale_y)

            # Guard against invalid conversions.
            if right <= left or bottom <= top:
                left, top, right, bottom = 0, 0, screen_w, screen_h

    pos_x = max(left + margin, right - width - margin)
    pos_y = max(top + margin, bottom - height - margin - bottom_safe_margin)
    return pos_x, pos_y


def _config_int(config: dict, key: str, default: int, min_value: int = 0) -> int:
    """Return integer config value with fallback and minimum clamp."""
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(min_value, value)


def _config_float(config: dict, key: str, default: float, min_value: float = 0.0) -> float:
    """Return float config value with fallback and minimum clamp."""
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(min_value, value)


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

        # Minimize then hide the freshly allocated console so even the
        # brief flash between AllocConsole and hide is just a taskbar blip
        # rather than a full window.  The caller will call
        # _set_console_visible(True) if the console should be shown.
        hwnd = _get_console_window()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE
        _set_console_visible(False)                     # SW_HIDE

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


def _temp_hex_colour(temp: Optional[int]) -> str:
    """Return a dashboard text color for the given temperature."""
    if temp is None or temp <= 0:
        return "#9CA3AF"  # Unknown
    if temp < 45:
        return "#22C55E"  # Cool
    if temp < 65:
        return "#FACC15"  # Warm
    return "#EF4444"      # Hot


class DashboardWindow:
    """Small live dashboard window opened from the tray icon."""

    def __init__(self, controller: "FanController") -> None:
        self.controller = controller
        self._thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()
        self._is_open = False
        self._close_requested = False

    @property
    def is_supported(self) -> bool:
        return tk is not None and ttk is not None

    @property
    def is_open(self) -> bool:
        with self._state_lock:
            return self._is_open

    def _set_open(self, value: bool) -> None:
        with self._state_lock:
            self._is_open = value

    def show(self) -> None:
        if not self.is_supported:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._close_requested = False
        self._set_open(True)              # reflect state immediately for menu label
        try:
            self._thread = threading.Thread(
                target=self._run_window,
                daemon=True,
                name="tray-dashboard",
            )
            self._thread.start()
        except Exception:
            self._set_open(False)

    def request_close(self) -> None:
        self._close_requested = True
        self._set_open(False)             # reflect state immediately for menu label

    def toggle(self) -> None:
        if self.is_open:
            self.request_close()
        else:
            self.show()

    def _run_window(self) -> None:
        if not self.is_supported:
            return

        root: Optional["tk.Tk"] = None
        try:
            cfg = self.controller.config
            dashboard_width = _config_int(cfg, "dashboard_width", 520, min_value=320)
            dashboard_height = _config_int(cfg, "dashboard_height", 360, min_value=240)
            dashboard_min_width = _config_int(cfg, "dashboard_min_width", 460, min_value=320)
            dashboard_min_height = _config_int(cfg, "dashboard_min_height", 320, min_value=240)
            dashboard_margin = _config_int(cfg, "dashboard_margin", 14, min_value=0)
            dashboard_bottom_offset = _config_int(cfg, "dashboard_bottom_offset", 56, min_value=0)
            dashboard_refresh_ms = _config_int(cfg, "dashboard_refresh_interval_ms", 1000, min_value=200)

            root = tk.Tk()
            root.withdraw()
            root.title("ASUS Fan Control Dashboard")
            root.geometry(f"{dashboard_width}x{dashboard_height}")
            root.minsize(dashboard_min_width, dashboard_min_height)
            root.configure(bg="#111827")

            container = tk.Frame(root, bg="#111827", padx=14, pady=14)
            container.pack(fill="both", expand=True)

            tk.Label(
                container,
                text="ASUS Fan Control",
                bg="#111827",
                fg="#E5E7EB",
                font=("Segoe UI", 16, "bold"),
            ).pack(anchor="w")
            tk.Label(
                container,
                text="Live cooling status",
                bg="#111827",
                fg="#9CA3AF",
                font=("Segoe UI", 10),
            ).pack(anchor="w", pady=(0, 10))

            # Summary cards
            cards = tk.Frame(container, bg="#111827")
            cards.pack(fill="x", pady=(0, 10))

            temp_card = tk.Frame(cards, bg="#1F2937", padx=12, pady=10)
            temp_card.pack(side="left", fill="both", expand=True, padx=(0, 6))
            fan_card = tk.Frame(cards, bg="#1F2937", padx=12, pady=10)
            fan_card.pack(side="left", fill="both", expand=True, padx=(6, 0))

            temp_value_var = tk.StringVar(value="Unknown")
            smoothed_var = tk.StringVar(value="-")
            target_var = tk.StringVar(value="--%")
            rpm_var = tk.StringVar(value="Unknown")
            profile_var = tk.StringVar(value="Unknown")
            failures_var = tk.StringVar(value="0")
            driver_var = tk.StringVar(value="Unknown")
            status_var = tk.StringVar(value="Starting...")
            updated_var = tk.StringVar(value="--:--:--")

            tk.Label(
                temp_card,
                text="CPU Temperature",
                bg="#1F2937",
                fg="#D1D5DB",
                font=("Segoe UI", 10),
            ).pack(anchor="w")
            temp_value_label = tk.Label(
                temp_card,
                textvariable=temp_value_var,
                bg="#1F2937",
                fg="#22C55E",
                font=("Segoe UI", 24, "bold"),
            )
            temp_value_label.pack(anchor="w", pady=(2, 0))
            tk.Label(
                temp_card,
                textvariable=smoothed_var,
                bg="#1F2937",
                fg="#9CA3AF",
                font=("Segoe UI", 10),
            ).pack(anchor="w")

            tk.Label(
                fan_card,
                text="Target Fan Speed",
                bg="#1F2937",
                fg="#D1D5DB",
                font=("Segoe UI", 10),
            ).pack(anchor="w")
            tk.Label(
                fan_card,
                textvariable=target_var,
                bg="#1F2937",
                fg="#60A5FA",
                font=("Segoe UI", 24, "bold"),
            ).pack(anchor="w", pady=(2, 0))
            tk.Label(
                fan_card,
                textvariable=rpm_var,
                bg="#1F2937",
                fg="#9CA3AF",
                font=("Segoe UI", 10),
            ).pack(anchor="w")

            # Progress bars
            bars = tk.Frame(container, bg="#111827")
            bars.pack(fill="x", pady=(0, 10))
            tk.Label(bars, text="Temp", bg="#111827", fg="#9CA3AF", font=("Segoe UI", 9)).grid(
                row=0, column=0, sticky="w", padx=(0, 8)
            )
            temp_bar = ttk.Progressbar(bars, orient="horizontal", mode="determinate", maximum=100)
            temp_bar.grid(row=0, column=1, sticky="ew")

            tk.Label(bars, text="Fan", bg="#111827", fg="#9CA3AF", font=("Segoe UI", 9)).grid(
                row=1, column=0, sticky="w", padx=(0, 8), pady=(6, 0)
            )
            fan_bar = ttk.Progressbar(bars, orient="horizontal", mode="determinate", maximum=100)
            fan_bar.grid(row=1, column=1, sticky="ew", pady=(6, 0))
            bars.grid_columnconfigure(1, weight=1)

            # Detail rows
            details = tk.Frame(container, bg="#111827")
            details.pack(fill="x")

            tk.Label(details, text="Profile:", bg="#111827", fg="#9CA3AF", font=("Segoe UI", 10)).grid(
                row=0, column=0, sticky="w"
            )
            tk.Label(details, textvariable=profile_var, bg="#111827", fg="#E5E7EB", font=("Segoe UI", 10, "bold")).grid(
                row=0, column=1, sticky="w", padx=(6, 18)
            )

            tk.Label(details, text="Failures:", bg="#111827", fg="#9CA3AF", font=("Segoe UI", 10)).grid(
                row=0, column=2, sticky="w"
            )
            tk.Label(details, textvariable=failures_var, bg="#111827", fg="#E5E7EB", font=("Segoe UI", 10, "bold")).grid(
                row=0, column=3, sticky="w", padx=(6, 0)
            )

            tk.Label(details, text="Driver:", bg="#111827", fg="#9CA3AF", font=("Segoe UI", 10)).grid(
                row=1, column=0, sticky="w", pady=(4, 0)
            )
            tk.Label(details, textvariable=driver_var, bg="#111827", fg="#E5E7EB", font=("Segoe UI", 10)).grid(
                row=1, column=1, columnspan=3, sticky="w", padx=(6, 0), pady=(4, 0)
            )

            # Status footer
            tk.Label(
                container,
                textvariable=status_var,
                bg="#111827",
                fg="#E5E7EB",
                wraplength=max(280, dashboard_width - 30),
                justify="left",
                font=("Segoe UI", 10),
            ).pack(anchor="w", pady=(10, 2))
            tk.Label(
                container,
                textvariable=updated_var,
                bg="#111827",
                fg="#6B7280",
                font=("Segoe UI", 9),
            ).pack(anchor="w")

            # Place near tray area (bottom-right) and bring to front.
            root.update_idletasks()
            width = max(int(root.winfo_width()), int(root.winfo_reqwidth()), dashboard_width)
            height = max(int(root.winfo_height()), int(root.winfo_reqheight()), dashboard_height)
            pos_x, pos_y = _compute_dashboard_position(
                root,
                width,
                height,
                margin=dashboard_margin,
                bottom_safe_margin=dashboard_bottom_offset,
            )
            root.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
            root.deiconify()
            root.lift()
            root.focus_force()
            root.attributes("-topmost", True)
            # Keep topmost for 1 s so it reliably clears the taskbar z-order,
            # then drop it so the user can interact with other windows behind.
            root.after(1000, lambda: root.attributes("-topmost", False))

            def refresh_ui() -> None:
                if self._close_requested:
                    root.destroy()
                    return

                snapshot = self.controller.get_status_snapshot()
                raw_temp = snapshot.get("raw_temp")
                smoothed_temp = snapshot.get("smoothed_temp")
                target_speed = snapshot.get("target_fan_speed")
                current_set = snapshot.get("current_set_fan_percentage")
                fan_speeds = snapshot.get("fan_speeds")
                profile = snapshot.get("profile")
                failures = snapshot.get("consecutive_failures", 0)
                driver_version = snapshot.get("driver_version")
                driver_incompatible = snapshot.get("driver_incompatible")
                status_message = snapshot.get("status_message", "Running")
                updated_ts = snapshot.get("updated_ts", time.time())

                temp_value_var.set("Unknown" if raw_temp is None else f"{raw_temp} C")
                temp_value_label.config(fg=_temp_hex_colour(raw_temp))
                smoothed_var.set(
                    "Smoothed: -" if smoothed_temp is None else f"Smoothed: {smoothed_temp} C"
                )

                display_target = target_speed if target_speed is not None else current_set
                target_var.set("--%" if display_target is None else f"{display_target}%")
                rpm_var.set(f"RPM: {fan_speeds or 'Unknown'}")
                profile_var.set(str(profile).capitalize() if profile else "Unknown")
                failures_var.set(str(failures))

                if driver_version:
                    suffix = " (incompatible)" if driver_incompatible else ""
                    driver_var.set(f"{driver_version}{suffix}")
                else:
                    driver_var.set("Unknown")

                status_var.set(str(status_message))
                if isinstance(updated_ts, (int, float)):
                    updated_var.set(f"Last update: {time.strftime('%H:%M:%S', time.localtime(updated_ts))}")
                else:
                    updated_var.set("Last update: --:--:--")

                temp_bar["value"] = max(0, min(100, raw_temp if isinstance(raw_temp, int) else 0))
                fan_bar["value"] = max(
                    0,
                    min(100, display_target if isinstance(display_target, int) else 0),
                )

                root.after(dashboard_refresh_ms, refresh_ui)

            def on_close() -> None:
                self.request_close()
                root.destroy()

            root.protocol("WM_DELETE_WINDOW", on_close)
            refresh_ui()
            root.mainloop()
        except Exception as e:
            self.controller.logger.error(f"Failed to open dashboard window: {e}")
        finally:
            self._set_open(False)
            self._close_requested = False


def run_with_tray(controller: "FanController") -> None:
    """Run the fan controller with a system tray icon on the main thread."""

    _enable_high_dpi_mode()

    # Console behaviour driven by config.
    console_visible = controller.config.get("console_visible_on_start", True)
    console_maximized = controller.config.get("console_maximized", True)

    # Ensure a console window exists and is wired to logging, even if we
    # plan to hide it (the user can still toggle it from the tray menu).
    if _ensure_console_window(controller.logger):
        _set_console_visible(bool(console_visible))
        if console_visible and console_maximized:
            hwnd = _get_console_window()
            if hwnd:
                SW_MAXIMIZE = 3
                try:
                    ctypes.windll.user32.ShowWindow(hwnd, SW_MAXIMIZE)
                except Exception:
                    pass

    # Prevent the console X button from killing the entire tray process.
    # The user should use the tray icon's "Hide Console" or "Quit" instead.
    _disable_console_close_button()
    _install_console_close_handler()

    dashboard = DashboardWindow(controller)
    dashboard_warning_emitted = False

    # -- Menu callbacks ------------------------------------------------------

    def on_quit(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        dashboard.request_close()
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

    def _dashboard_label(_item: pystray.MenuItem) -> str:
        return "Hide Dashboard" if dashboard.is_open else "Show Dashboard"

    def _toggle_dashboard(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        nonlocal dashboard_warning_emitted
        if not dashboard.is_supported:
            if not dashboard_warning_emitted:
                controller.logger.warning(
                    "Dashboard UI is unavailable (tkinter is missing in this Python build)."
                )
                dashboard_warning_emitted = True
            return
        dashboard.toggle()
        _icon.update_menu()

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
        # Left-click action: pystray triggers the default menu item.
        pystray.MenuItem("Toggle Dashboard", _toggle_dashboard, default=True, visible=False),
        pystray.MenuItem(
            _dashboard_label,
            _toggle_dashboard,
            enabled=lambda _item: dashboard.is_supported,
        ),
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

    # Minimize-to-hide watcher needs the icon so it can refresh the menu label.
    _start_console_minimize_watcher(on_hide=icon.update_menu)

    # -- Background threads --------------------------------------------------

    def fan_loop() -> None:
        """Run the fan controller in a daemon thread."""
        controller.run()
        # When the controller stops, also stop the tray icon
        dashboard.request_close()
        icon.stop()

    def icon_updater() -> None:
        """Periodically update the tray icon image and tooltip."""
        update_interval = _config_float(
            controller.config,
            "tray_icon_update_interval_seconds",
            2.0,
            min_value=0.5,
        )
        while controller.running:
            snapshot = controller.get_status_snapshot()
            raw_temp = snapshot.get("raw_temp")
            temp = raw_temp if isinstance(raw_temp, int) and raw_temp > 0 else 0
            target_speed = snapshot.get("target_fan_speed")
            current_set_speed = snapshot.get("current_set_fan_percentage")
            speed = (
                target_speed
                if isinstance(target_speed, int)
                else current_set_speed if isinstance(current_set_speed, int) else 0
            )
            profile = str(snapshot.get("profile", "?")).capitalize()
            icon.icon = _create_icon_image(temp)
            icon.title = f"CPU: {temp}\u00b0C | Fan: {speed}% | {profile}"
            time.sleep(update_interval)

    # -- Graceful Ctrl+C handling --------------------------------------------

    def _shutdown(signum: int = 0, frame: object = None) -> None:
        """Handle SIGINT (Ctrl+C) without the ctypes callback traceback."""
        dashboard.request_close()
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
