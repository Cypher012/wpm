#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["evdev", "rich"]
# ///
"""
Live system-wide WPM speedometer.

Reads raw keystrokes from keyboard devices via evdev (works regardless
of which app has focus, and regardless of X11 vs Wayland), computes a
rolling WPM, and renders a live dashboard in the terminal.

Usage:
    uv run wpm.py                    # auto-detect keyboards
    uv run wpm.py /dev/input/eventX  # explicit device(s)

Requires your user to be in the 'input' group:
    sudo usermod -aG input $USER
    (then log out and back in)
"""

import argparse
import sys
import threading
import time
from collections import deque
from datetime import timedelta

import evdev
from evdev import ecodes
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# --- Config ---
WINDOW_SECONDS = 8  # rolling window for WPM calculation
CHARS_PER_WORD = 5  # standard typing convention
REFRESH_HZ = 12  # how often we redraw the dashboard
SPARKLINE_SAMPLES = 45  # number of historical WPM readings to keep

# Keys that should NOT count as "characters typed" for WPM purposes.
# We only want to count keys that actually produce visible characters
# (letters, numbers, punctuation, space) — not modifiers, arrows, etc.
IGNORED_KEYS = {
    "KEY_LEFTSHIFT",
    "KEY_RIGHTSHIFT",
    "KEY_LEFTCTRL",
    "KEY_RIGHTCTRL",
    "KEY_LEFTALT",
    "KEY_RIGHTALT",
    "KEY_LEFTMETA",
    "KEY_RIGHTMETA",
    "KEY_CAPSLOCK",
    "KEY_TAB",
    "KEY_ESC",
    "KEY_UP",
    "KEY_DOWN",
    "KEY_LEFT",
    "KEY_RIGHT",
    "KEY_HOME",
    "KEY_END",
    "KEY_PAGEUP",
    "KEY_PAGEDOWN",
    "KEY_INSERT",
    "KEY_DELETE",
    "KEY_F1",
    "KEY_F2",
    "KEY_F3",
    "KEY_F4",
    "KEY_F5",
    "KEY_F6",
    "KEY_F7",
    "KEY_F8",
    "KEY_F9",
    "KEY_F10",
    "KEY_F11",
    "KEY_F12",
    "KEY_NUMLOCK",
    "KEY_SCROLLLOCK",
    "KEY_PAUSE",
    "KEY_PRINT",
    "KEY_MENU",
}

BACKSPACE_KEY = "KEY_BACKSPACE"


class WpmTracker:
    def __init__(self, window_seconds=WINDOW_SECONDS):
        self.window_seconds = window_seconds
        self.keystrokes = deque()  # timestamps of counted keystrokes
        self.backspaces = deque()  # timestamps of backspace presses
        self.total_keystrokes = 0
        self.total_backspaces = 0
        self.peak_wpm = 0.0
        self.session_start = time.time()
        self.wpm_history = deque(maxlen=SPARKLINE_SAMPLES)
        self.lock = threading.Lock()

    def record_key(self, keycode):
        now = time.time()
        with self.lock:
            if keycode == BACKSPACE_KEY:
                self.backspaces.append(now)
                self.total_backspaces += 1
            elif keycode not in IGNORED_KEYS:
                self.keystrokes.append(now)
                self.total_keystrokes += 1
            self._trim(now)

    def _trim(self, now):
        cutoff = now - self.window_seconds
        while self.keystrokes and self.keystrokes[0] < cutoff:
            self.keystrokes.popleft()
        while self.backspaces and self.backspaces[0] < cutoff:
            self.backspaces.popleft()

    def current_wpm(self):
        now = time.time()
        with self.lock:
            self._trim(now)
            n = len(self.keystrokes)
        wpm = (n / CHARS_PER_WORD) / (self.window_seconds / 60)
        if wpm > self.peak_wpm:
            self.peak_wpm = wpm
        return wpm

    def current_accuracy(self):
        now = time.time()
        with self.lock:
            self._trim(now)
            keys = len(self.keystrokes)
            back = len(self.backspaces)
        total = keys + back
        if total == 0:
            return 100.0
        return max(0.0, 100.0 * (1 - back / total))

    def average_wpm(self):
        elapsed = time.time() - self.session_start
        if elapsed < 1:
            return 0.0
        with self.lock:
            total_chars = self.total_keystrokes + self.total_backspaces
        return (total_chars / CHARS_PER_WORD) / (elapsed / 60)

    def current_kpm(self):
        wpm = self.current_wpm()
        return wpm * CHARS_PER_WORD

    def session_duration(self):
        return time.time() - self.session_start

    def push_history(self, wpm):
        self.wpm_history.append(wpm)

    def consistency(self):
        """Return a 0-100 score of how steady the recent WPM has been."""
        if len(self.wpm_history) < 5:
            return 100.0
        recent = list(self.wpm_history)[-20:]
        if not recent or max(recent) == 0:
            return 100.0
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        std = variance**0.5
        # Lower std relative to mean = higher consistency
        coeff_var = std / mean if mean > 0 else 0
        return max(0.0, min(100.0, 100.0 * (1 - coeff_var)))


def speed_tier(wpm):
    if wpm < 20:
        return "dim", "idle/slow", "◷"
    elif wpm < 40:
        return "yellow", "steady", "◎"
    elif wpm < 65:
        return "green", "cruising", "◉"
    elif wpm < 90:
        return "bold green", "fast", "⚡"
    else:
        return "bold magenta", "blazing", "🔥"


SPARK_CHARS = "▁▂▃▄▅▆▇█"


def make_sparkline(values, width=30):
    if not values:
        return " " * width
    recent = list(values)[-width:]
    lo, hi = min(recent), max(recent)
    if hi == lo:
        return SPARK_CHARS[0] * len(recent)
    scaled = [int((v - lo) / (hi - lo) * (len(SPARK_CHARS) - 1)) for v in recent]
    return "".join(SPARK_CHARS[s] for s in scaled)


def colored_bar(wpm, width=40):
    """Build a multi-toned progress bar. Returns a list of (char, style) tuples."""
    filled = min(width, int((wpm / 120) * width))
    segments = []
    colors = ["cyan", "green", "yellow", "red", "magenta"]
    for i in range(width):
        if i < filled:
            color_idx = min(len(colors) - 1, int((i / width) * len(colors)))
            segments.append(("█", colors[color_idx]))
        else:
            segments.append(("░", "dim"))
    return segments


def render(tracker: WpmTracker) -> Panel:
    wpm = tracker.current_wpm()
    acc = tracker.current_accuracy()
    avg = tracker.average_wpm()
    kpm = tracker.current_kpm()
    consistency = tracker.consistency()
    duration = tracker.session_duration()
    color, label, emoji = speed_tier(wpm)

    # --- Top: Big WPM readout ---
    header = Text()
    header.append(f"{wpm:5.1f}", style=f"{color} bold")
    header.append(" WPM  ", style="dim")
    header.append(f"{emoji} {label}\n", style=color)

    # --- Gauge bar ---
    bar_width = 42
    bar_segments = colored_bar(wpm, bar_width)
    bar_text = Text()
    for ch, st in bar_segments:
        bar_text.append(ch, style=st)
    bar_text.append(f"  {wpm:.0f}", style=color)

    # --- Sparkline ---
    spark = make_sparkline(tracker.wpm_history, width=bar_width)
    spark_text = Text()
    spark_text.append("history  ", style="dim")
    spark_text.append(spark, style="bright_cyan")

    # --- Stats grid ---
    stats = Table(show_header=False, box=None, padding=(0, 2))
    stats.add_column(style="dim", justify="right")
    stats.add_column(style="default", justify="left")
    stats.add_column(style="dim", justify="right")
    stats.add_column(style="default", justify="left")

    stats.add_row(
        "accuracy",
        f"{acc:.1f}%",
        "peak",
        f"{tracker.peak_wpm:.1f}",
    )
    stats.add_row(
        "average",
        f"{avg:.1f} WPM",
        "total keys",
        f"{tracker.total_keystrokes:,}",
    )
    stats.add_row(
        "kpm",
        f"{kpm:.0f}",
        "duration",
        str(timedelta(seconds=int(duration))),
    )
    stats.add_row(
        "consistency",
        f"{consistency:.0f}%",
        "backspaces",
        f"{tracker.total_backspaces:,}",
    )

    # --- Assemble body ---
    body = Group(
        Align.center(header),
        Text(),
        Align.center(bar_text),
        Text(),
        Align.center(spark_text),
        Text(),
        Align.center(stats),
    )

    return Panel(
        Align.center(body),
        title="[bold]⌨  Typing Speed Monitor[/bold]",
        border_style=color,
        padding=(1, 2),
    )


def find_keyboards():
    """Auto-discover likely keyboard devices."""
    devices = [evdev.InputDevice(p) for p in evdev.list_devices()]
    keyboards = []
    for dev in devices:
        caps = dev.capabilities(verbose=True)
        has_keys = any("EV_KEY" in str(k) for k in caps.keys())
        name_lower = dev.name.lower()
        is_likely_kb = has_keys and ("keyboard" in name_lower or "key" in name_lower)
        # Exclude mice / touchpads explicitly
        is_mouse = (
            "mouse" in name_lower
            or "touchpad" in name_lower
            or "trackpoint" in name_lower
        )
        if is_likely_kb and not is_mouse:
            keyboards.append(dev.path)
    return keyboards


def listen_devices(device_paths, tracker: WpmTracker, stop_event: threading.Event):
    devices = [evdev.InputDevice(p) for p in device_paths]
    for d in devices:
        print(f"[+] Listening on: {d.path} ({d.name})")

    from selectors import EVENT_READ, DefaultSelector

    selector = DefaultSelector()
    for d in devices:
        selector.register(d, EVENT_READ)

    while not stop_event.is_set():
        for key, _ in selector.select(timeout=0.5):
            device = key.fileobj
            try:
                for event in device.read():
                    if (
                        event.type == ecodes.EV_KEY and event.value == 1
                    ):  # key down only
                        keycode = ecodes.KEY.get(event.code)
                        if isinstance(keycode, list):
                            keycode = keycode[0]
                        if keycode:
                            tracker.record_key(keycode)
            except (OSError, BlockingIOError):
                continue


def main():
    parser = argparse.ArgumentParser(
        description="Live system-wide WPM speedometer",
        epilog="Requires membership in the 'input' group.",
    )
    parser.add_argument(
        "devices",
        nargs="*",
        help="Keyboard device paths (e.g. /dev/input/event3). If omitted, auto-detects keyboards.",
    )
    args = parser.parse_args()

    if args.devices:
        device_paths = args.devices
    else:
        device_paths = find_keyboards()
        if not device_paths:
            print("No keyboards found. Are you in the 'input' group?")
            print("  sudo usermod -aG input $USER   # then log out and back in")
            sys.exit(1)

    tracker = WpmTracker()
    stop_event = threading.Event()

    listener_thread = threading.Thread(
        target=listen_devices, args=(device_paths, tracker, stop_event), daemon=True
    )
    listener_thread.start()

    console = Console(force_terminal=True)

    try:
        with Live(
            render(tracker),
            refresh_per_second=REFRESH_HZ,
            screen=True,
            console=console,
        ) as live:
            while True:
                time.sleep(1 / REFRESH_HZ)
                wpm = tracker.current_wpm()
                tracker.push_history(wpm)
                live.update(render(tracker))
    except KeyboardInterrupt:
        stop_event.set()
        print("\n[+] Stopped.")


if __name__ == "__main__":
    main()
