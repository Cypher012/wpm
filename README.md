# wpm — Live Terminal Typing Speedometer

A system-wide WPM (words-per-minute) dashboard that runs in your terminal. It listens directly to your keyboard via Linux's `evdev` subsystem, so it works everywhere — terminal, browser, IDE, game, doesn't matter — completely independent of which app has focus or whether you're on X11 or Wayland.

![Demo](https://raw.githubusercontent.com/Cypher012/wpm/main/assets/demo.png)

## Features

- **🎹 System-wide monitoring** — Tracks every keystroke across all applications via raw `evdev` input
- **⚡ Auto keyboard detection** — No need to manually find `/dev/input/eventX` paths; it discovers keyboards automatically
- **📊 Live dashboard** — Rich terminal UI with real-time updates:
  - Current WPM with color-coded speed tier
  - Multi-segment gradient gauge bar (cyan → green → yellow → red → magenta)
  - WPM sparkline history (`▁▂▃▄▅▆▇█`)
  - Session stats: accuracy, peak WPM, average WPM, KPM, duration, consistency score, backspace count
- **🧮 Smart filtering** — Ignores modifier keys (Shift, Ctrl, Alt, Meta), function keys, arrows, etc. Only productive keystrokes count
- **🔄 Backspace tracking** — Accuracy metric based on backspace ratio in the rolling window
- **📦 Zero system dependencies** — Single-file script with inline dependency metadata; `uv` handles everything

## Requirements

- Linux (needs `/dev/input/event*` access)
- Python ≥ 3.10
- [uv](https://docs.astral.sh/uv/) (for running the script)
- Your user must be in the `input` group

## Setup

### 1. Add yourself to the `input` group

The script needs permission to read raw keyboard events:

```bash
sudo usermod -aG input $USER
```

**Log out and back in** for the group change to take effect. You can verify with:

```bash
groups | grep input
```

### 2. Install `uv`

If you don't have it yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Clone and run

```bash
git clone https://github.com/Cypher012/wpm.git
cd wpm
uv run wpm.py
```

Or with explicit keyboard devices:

```bash
uv run wpm.py /dev/input/event3 /dev/input/event5
```

## Install as a global command

Tired of `cd`-ing into the folder? Create a wrapper:

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/wpm << 'EOF'
#!/bin/sh
exec uv run /absolute/path/to/wpm.py "$@"
EOF
chmod +x ~/.local/bin/wpm
```

Then run it from anywhere:

```bash
wpm           # auto-detect keyboards
wpm --help    # show options
```

> Make sure `~/.local/bin` is on your `PATH`.

## How it works

### Input layer: `evdev`

Linux exposes all input devices as files under `/dev/input/event*`. The script uses the [`python-evdev`](https://python-evdev.readthedocs.io/) library to:

1. Enumerate devices and filter for keyboards (heuristic: has `EV_KEY` capability and name contains "keyboard" or "key")
2. Exclude mice and touchpads by name
3. `select()` across all matched devices for non-blocking reads
4. Capture `EV_KEY` events with `value == 1` (key-down only)

This works **below** both X11 and Wayland, so it's truly global.

### WPM calculation

The tracker maintains a rolling window (default 8 seconds) of keystroke timestamps:

```
WPM = (keystrokes_in_window / 5) / (8 / 60)
```

Where 5 is the standard "characters per word" convention. The fixed window acts as a low-pass filter — it smooths out natural micro-pauses between words so the gauge is readable rather than epileptic.

### Accuracy

Accuracy is defined as the ratio of backspaces to total keystrokes within the rolling window:

```
accuracy = 100% × (1 - backspaces / (keystrokes + backspaces))
```

This is not "text-target accuracy" (you'd need a reference string for that) but a useful proxy for how much you're correcting yourself.

### Consistency

Measures how steady your recent WPM has been using the coefficient of variation over the last ~20 samples:

```
consistency = 100% × (1 - std_dev / mean)
```

Higher = more robotic, steady typing. Lower = erratic bursts and pauses.

## Architecture

```
┌─────────────────────────────────────────┐
│           Main Thread                   │
│  ┌─────────────┐    ┌─────────────┐    │
│  │   Live UI   │◄───│   render()  │    │
│  │  (Rich)     │    │  12 Hz      │    │
│  └─────────────┘    └─────────────┘    │
│         ▲                               │
│         │ reads                         │
│  ┌──────┴──────────┐                   │
│  │   WpmTracker    │                   │
│  │  - keystrokes   │                   │
│  │  - backspaces   │                   │
│  │  - history      │                   │
│  │  - peak/avg     │                   │
│  └─────────────────┘                   │
│         ▲                               │
│         │ thread-safe writes            │
│  ┌──────┴──────────────────────────┐   │
│  │      Listener Thread             │   │
│  │  selector.select() → evdev.read │   │
│  │  filters IGNORED_KEYS           │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

- **Listener thread** (daemon): Blocks on `select()` across all keyboard devices, writes timestamps to `WpmTracker` under a `threading.Lock`
- **Main thread**: Runs the Rich `Live` display loop, reads from `WpmTracker` to render panels

## File overview

| File | Purpose |
|------|---------|
| `wpm.py` | Main application. Self-contained with PEP 723 inline metadata. |
| `find_keyboard.py` | Standalone utility to list all input devices and identify likely keyboards. Useful for debugging auto-detection. |

## Configuration

All tunables are at the top of `wpm.py`:

```python
WINDOW_SECONDS = 8      # Rolling window for WPM calculation
CHARS_PER_WORD = 5      # Standard typing convention
REFRESH_HZ = 12         # Dashboard redraw rate
SPARKLINE_SAMPLES = 45  # WPM history buffer size
```

Speed tiers (for color/emoji) are in `speed_tier()`:

| WPM | Tier | Color | Emoji |
|-----|------|-------|-------|
| < 20 | idle/slow | dim | ◷ |
| 20–40 | steady | yellow | ◎ |
| 40–65 | cruising | green | ◉ |
| 65–90 | fast | bold green | ⚡ |
| ≥ 90 | blazing | bold magenta | 🔥 |

## Known limitations

- **Linux only** — macOS and Windows do not expose raw input devices this way
- **Auto-repeat inflation** — Holding a key down counts every OS-generated repeat event. This inflates WPM for held keys vs. distinct presses
- **No text-target accuracy** — Accuracy is backspace-based, not compared against a reference text
- **Requires `input` group** — Without it, `evdev` can't open device nodes

## License

MIT
