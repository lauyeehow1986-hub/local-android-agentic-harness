# Termux:Widget shortcuts

Home-screen buttons for local-agent — a one-tap **killswitch** and one-tap voice
capture. Uses the **Termux:Widget** app (install from F-Droid, same source as Termux).

## Scripts

| Script | What tapping it does |
|---|---|
| `stop-agent` | 🔴 **Killswitch** — creates `~/.local_agent/STOP`; any screen automation halts before its next tap/type/step. |
| `resume-agent` | 🟢 Clears the killswitch so automation can run again. |
| `dictate-type` | Wispr-style — speak, and it **types into the focused app field** (needs ADB). |
| `dictate-note` | Speak, and it **appends to today's daily note** in the vault. |

## Install

```bash
mkdir -p ~/.shortcuts
cp ~/local-android-agentic-harness/scripts/termux-widgets/* ~/.shortcuts/
chmod +x ~/.shortcuts/*
rm ~/.shortcuts/README.md      # not a shortcut
```

Then long-press your home screen → **Widgets** → **Termux:Widget** → place it, and pick
a script. (Add several widgets, one per script.)

## Notes

- The scripts assume the repo is at `~/local-android-agentic-harness` and that your env
  (whisper model, ADB serial, etc.) is set in `~/.bashrc` — Termux:Widget runs a login
  shell, so those are picked up.
- `dictate-type` needs ADB reachable (Wireless Debugging) and a whisper backend; see the
  README's *Screen automation & type-anywhere* section.
- Tune the recording length by editing `--seconds` in each script.
- The killswitch is just a file (`AGENT_KILLSWITCH`, default `~/.local_agent/STOP`); you can
  also `touch`/`rm` it from any shell.
