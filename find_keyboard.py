#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["evdev"]
# ///
"""
Run this on your actual Linux machine (not here) to find which
/dev/input/eventX corresponds to your keyboard.

Usage:
    uv run find_keyboard.py

You may need to run with sudo the first time if you're not yet
in the 'input' group:
    sudo uv run find_keyboard.py
"""

import evdev

devices = [evdev.InputDevice(path) for path in evdev.list_devices()]

if not devices:
    print("No input devices found. Are you in the 'input' group?")
    print("Run: sudo usermod -aG input $USER   then log out and back in.")
else:
    print(f"{'Path':<20} {'Name':<40} Capabilities")
    print("-" * 80)
    for dev in devices:
        caps = dev.capabilities(verbose=True)
        # Keyboards will have EV_KEY capability with a large set of key codes
        has_keys = any("EV_KEY" in str(k) for k in caps.keys())
        marker = (
            "  <-- likely keyboard"
            if has_keys and "key" in dev.name.lower() or "keyboard" in dev.name.lower()
            else ""
        )
        print(f"{dev.path:<20} {dev.name:<40}{marker}")
