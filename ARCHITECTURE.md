# Architecture

A module-level map of Playscii, oriented around "where do I go to change X?"

## Lifecycle

```
playscii.py  __main__
    └── Application.__init__              # wires every subsystem
         ├── sdl2.ext.init, window, GL ctx
         ├── ShaderLord        (shader.py)        — GLSL compile + hot reload
         ├── CharacterSetLord  (charset.py)       — bitmap fonts in charsets/
         ├── PaletteLord       (palette.py)       — palette PNGs in palettes/
         ├── AudioLord         (audio.py)         — SDL_mixer wrapper
         ├── Camera            (camera.py)        — view/zoom
         ├── UI                (ui.py + ui_*.py)  — menus, dialogs, status bar
         ├── Cursor            (cursor.py)        — edit cursor in Art Mode
         ├── ArtGrid           (grid.py)          — visible tile grid overlay
         ├── InputLord         (input_handler.py) — keybinds, SDL event pump
         ├── GameWorld         (game_world.py)    — Game Mode root
         └── loads initial Art / game dir

Application.main_loop:
    while not should_quit:
        update()         # input → game world update (fixed timestep) → frame update
        render()         # clear → renderables → UI → framebuffer post → swap
        *.check_hot_reload()
```

The fixed-timestep `update_rate` (default 30) drives game logic; `framerate` (default 30, -1 = uncapped) drives render cadence.

## Art model

- `art.py`
  - `Art` — in-memory model. An Art has N frames, each frame has M layers, each layer is a 2D grid of tiles, each tile is `(char_index, fg_color_index, bg_color_index, xform)`. Animations are frame sequences with per-frame delays.
  - `ArtFromDisk` — loads `.psci` (JSON) from disk.
  - Constants: `DEFAULT_CHARSET`, `DEFAULT_PALETTE`, `DEFAULT_WIDTH/HEIGHT`, `ART_DIR`, `ART_FILE_EXTENSION`, `ART_SCRIPT_DIR`.
- `charset.py` — `CharacterSet` (bitmap font + metadata from `charsets/*.char` + `.png`) and `CharacterSetLord`.
- `palette.py` — `Palette` (loaded from palette PNG) and `PaletteLord`. Helpers like `get_closest_color_index` for nearest-color matching (used by bitmap import + wildflowers).
- `lab_color.py` — CIE Lab color distance helpers for the above.

## Rendering

- `renderable.py` — `TileRenderable` (draws an Art), `OnionTileRenderable` (onion-skin frames for animation).
- `renderable_line.py` — debug lines, grid lines.
- `renderable_sprite.py` — sprite/UI background textures.
- `framebuffer.py` — `Framebuffer` target, CRT shader toggle (`start_crt_enabled`, `disable_crt`).
- `shader.py` — `Shader` + `ShaderLord`. Hot reload watches `shaders/*.glsl`.
- `shaders/*.glsl` — vertex/fragment pairs for renderables, lines, sprites, cursor, framebuffer (plain + CRT).
- `texture.py` — GL texture wrapper.

## Editor (Art Mode)

- `cursor.py` — cursor state, click + drag + keyboard editing.
- `grid.py` — `ArtGrid` toggle/overlay.
- `selection.py` — rectangular selection.
- `edit_command.py` — undo/redo command pattern.
- `ui_tool.py` — brush, erase, fill (char/fg/bg), grab, text — the paint tools.
- `ui_edit_panel.py` — the char/color/xform picker panel.
- `ui_object_panel.py` — object properties panel (also used in Game Mode edit).
- `ui_status_bar.py`, `ui_menu_bar.py`, `ui_menu_pulldown*.py`, `ui_popup.py`, `ui_console.py` — the main editor chrome.

## UI toolkit (home-grown)

Everything `ui_*.py` plus `ui.py`. Dialogs derive from `ui_dialog.py` / `ui_chooser_dialog.py` / `ui_file_chooser_dialog.py`. Swatch rendering (chars + palette) is `ui_swatch.py`. Images live in `ui/`. There is no external UI library — extend this toolkit rather than adding one.

## Game Mode

- `game_world.py` — `GameWorld`. Holds the loaded game dir, active room, object list, camera target, serialization (game state load/save), frame lifecycle (`frame_begin`, `pre_update`, `update`, `post_update`).
- `game_object.py` — `GameObject` base class. Transform, art + animation, physics, collision, input hooks, lifecycle. This file is large and central — most game behavior is subclassing `GameObject`.
- `game_util_objects.py` — common `GameObject` subclasses (tile triggers, location markers, player bases, etc.).
- `collision.py` — collision shapes + resolution.
- `game_room.py` — room container; rooms scope which objects are "live."
- `game_hud.py` — HUD base.
- `ui_game_dialog.py`, `ui_game_menu_pulldown_item.py` — Game Mode-specific UI.
- `games/<name>/` — each game dir contains its own `.py` module defining objects, rooms, and entry points, plus content files.

## I/O formats

`formats/in_*.py` and `formats/out_*.py`. Each is a small module with a class that registers with the importer/exporter system.

- In: `in_ans.py`, `in_edscii.py`, `in_endoom.py`, `in_bitmap.py`, `in_bitmap_sequence.py`, `in_txt.py`.
- Out: `out_ans.py`, `out_endoom.py`, `out_png.py`, `out_png_set.py`, `out_gif.py`, `out_txt.py`.
- `art_import.py` / `art_export.py` — higher-level ArtImporter/ArtExporter orchestration.
- `image_convert.py` — "convert a bitmap into Playscii tiles" (the bitmap→charset quantizer).
- `image_export.py` — render Art frames to PIL Images for PNG/GIF output.
- `bulk_import.py` — batch importer script (added in this fork, `2ed8b03`).

## Scripts, config, assets

- `artscripts/*.arsc` — Python snippets operating on an `Art` (conway, dissolve, evap, fade, etc.). Run from the art console.
- `playscii.cfg.default` → copied to `playscii.cfg` on first run. Executed as Python; mutates class attributes. This is the "user config."
- `binds.cfg.default` → keybinds, same pattern.
- `charsets/`, `palettes/`, `art/`, `shaders/`, `ui/` — asset dirs.
- `games/` — bundled example games.
- `docs/html/` — in-app user documentation.

## Build / packaging

- `build_mac.sh` + `playscii_mac.spec` — pyinstaller-based `.app` build, wraps it in a DMG.
- `playscii_linux.sh` + `playscii_linux.spec` — Linux pyinstaller.
- `build_windows.bat` + `zip_build.bat` + `win_*` — Windows build artifacts.
- `zip_src.sh` + `zip_src_include` — source zip for distribution.

## Where things *aren't*

- No tests, no CI, no linter config.
- No dependency injection framework — `self.app` is the service locator.
- No plugin system — game code lives in `games/` and is imported by path.
- No async — everything is synchronous around the SDL event loop.
