# CLAUDE.md

Context for Claude Code working in this repo. Keep this file accurate — update it when the facts below change.

## What this project is

Playscii is an ASCII art, animation, and game creation tool by JP LeBreton. Python 3 + SDL2 + OpenGL 2.1. Current version: `9.16.3` (see `version` file).

Two runtime modes share one app:
- **Art Mode** — interactive editor for `.psci` art files (tile-based, character + fg/bg color per cell, layered, animatable).
- **Game Mode** — runs Python-scripted games built from GameObjects on top of the same art/rendering stack. Example games live in `games/` (wildflowers, shmup, maze, flood, platso, cronotest, fireplace).

This is **`lessthanjake/playscii`**, a personal fork of `JPLeBreton/playscii` carrying macOS fixes and small personal tweaks. It is not kept in sync with upstream. The fork relationship is tracked on GitHub — don't try to rebase or merge upstream without asking.

## Running it (macOS, local dev)

See `NOTES.txt` for the canonical install steps. Summary:

```sh
# one-time system deps
/opt/homebrew/bin/brew install sdl2 sdl2_mixer numpy libjpeg libtiff libxcb

# symlinks pysdl2 expects in the project root
ln -s /opt/homebrew/Cellar/sdl2/<ver>/lib/libSDL2-2.0.0.dylib libSDL2.dylib
ln -s /opt/homebrew/Cellar/sdl2_mixer/<ver>/lib/libSDL2_mixer-2.0.0.dylib libSDL2_mixer.dylib

# Python env
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Pillow needs to be rebuilt from source against brew libs on Apple silicon
export LDFLAGS="-L/opt/homebrew/lib"
export CPPFLAGS="-I/opt/homebrew/include"
pip install Pillow --no-binary :all: --force-reinstall

# run
PYSDL2_DLL_PATH=. python playscii.py
```

The symlinks (`libSDL2.dylib`, `libSDL2_mixer.dylib`) are in `.gitignore` — they are per-machine.

Mac app bundle build: `build_mac.sh` (uses `pyinstaller` with `playscii_mac.spec`).

## Running/launching flags

`python playscii.py [art_file.psci]` — open a specific art file in Art Mode.
`python playscii.py <game_dir>` — launch into Game Mode with that game loaded. See the top of `playscii.py` (`if __name__ == "__main__"`) for the full CLI.

A file named `autoplay_this_game` at the repo root forces Game Mode on startup.

## Tests / lint

There is no test suite and no linter configured. Verification is manual:
1. Launch the app (`PYSDL2_DLL_PATH=. python playscii.py`) and check the feature interactively.
2. For game-mode changes, launch with the relevant `games/<name>` dir.
3. Watch `console.log` (written to the user data dir — path depends on OS) for errors and `show_dev_log` messages.

Do **not** claim a change works without actually running it — type-checking isn't enough to catch the render/input path.

## Code conventions to respect

- **"Lord" manager pattern.** Subsystem managers are suffixed `Lord`: `AudioLord`, `ShaderLord`, `CharacterSetLord`, `PaletteLord`, `InputLord`. Each owns loading, hot-reload, and lookup for its resource type. Keep this naming when adding siblings.
- **Everything hangs off `Application`** (`playscii.py`). Most subsystems get a back-reference to the app as `self.app`. The `Application` instance is the implicit service locator.
- **Class defaults are config.** `playscii.cfg` is executed as Python and mutates class attributes directly (e.g. `Art.DEFAULT_CHARSET = 'c64_petscii'`). When adding a tunable, expose it as a class attribute with a sensible default, then users can override it in `playscii.cfg`. See `playscii.cfg.default` for examples.
- **No type hints** in existing code; don't add them piecemeal. Match the surrounding style.
- **Big files are normal.** `game_world.py`, `game_object.py`, `art.py`, `ui.py`, `input_handler.py` are all 20k–55k lines of code. Prefer editing in place rather than splitting files unless the user asks.
- **Hot reload exists** for shaders, charsets, palettes (`*.check_hot_reload()` in `main_loop`). If you touch how these load, preserve the hot-reload path.

## Architecture cheat sheet

See `ARCHITECTURE.md` for a fuller map. Quick version:

- **Entry / loop:** `playscii.py` → `Application.main_loop` → `update()` → `render()`.
- **Art model:** `art.py` (`Art`, `ArtFromDisk`), `charset.py`, `palette.py`. A `.psci` file is JSON describing frames × layers × tiles with (char, fg, bg, xform) per tile.
- **Rendering:** `renderable.py` (tile renderables), `renderable_line.py`, `renderable_sprite.py`, `framebuffer.py`, `shader.py`, GLSL in `shaders/`. The CRT post-process is a framebuffer shader toggle.
- **Editor:** `cursor.py`, `grid.py`, `selection.py`, `edit_command.py` (undo stack), `ui_tool.py` (brush/fill/etc.), `ui_edit_panel.py`.
- **UI toolkit:** home-grown, all `ui_*.py` + `ui.py`. Dialogs, menus, pulldowns, status bar, popup, console. Not a third-party framework.
- **Game mode:** `game_world.py` owns a room/object graph; `game_object.py` is the base class; `game_util_objects.py` has common subclasses; `collision.py` handles collision; `game_room.py` groups objects; `game_hud.py`. Games live in `games/<name>/` and are loaded dynamically.
- **I/O formats:** `formats/in_*.py` / `formats/out_*.py` — ANS, EDscii, ENDOOM, bitmap, txt, PNG (single + set), GIF. `image_convert.py` + `image_export.py` bridge to Pillow.
- **Content dirs:** `charsets/` (bitmap font + metadata), `palettes/` (PNG palettes), `art/` (bundled `.psci`), `artscripts/` (scriptable art effects, `.arsc`), `games/`, `shaders/`, `ui/` (UI images/icons).

## Things to watch out for

- **Pinned, very old deps** in `requirements.txt` (numpy 1.20.1, Pillow 8.1.2, PySDL2 0.9.7, PyOpenGL 3.1.5). Don't bump them casually — the fork's most recent commit (`1232dba`) was specifically about updating the code to work with newer Pillow. If something depends on new numpy/Pillow behavior, check `art.py`, `palette.py`, `image_convert.py`, `image_export.py` first.
- **Two docs trees:** `docs/html/` is the user-facing how-to (shipped with the app, rendered in-app). `docs/` also contains `todo.txt`, `bugs.txt`, `design`, etc. — author notes, not authoritative.
- **`__pycache__/` is committed** at the repo root. Don't touch it; don't try to "clean it up" — that's outside the scope of any normal task.
- **No CI.** Whatever you break, nothing else will catch it.

## Related files

- `ARCHITECTURE.md` — deeper module map.
- `NOTES.txt` — macOS install notes (the authoritative source; this file paraphrases).
- `README.md` — upstream readme, slightly edited to note fork status.
- `playscii.cfg.default` — reference for all user-overridable config knobs.
- `docs/html/howto_main.html` — user-facing documentation entry point.
