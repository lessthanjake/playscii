#!/usr/bin/env python3
"""Convert a .psci file to SVG from the command line, without launching the
Playscii GUI.

Loads the .psci JSON directly, resolves its charset and palette against a
search path of on-disk directories, and drives svg_export.build_svg with a
duck-typed Art shim. No OpenGL / SDL / numpy dependencies — only PIL.

Usage:
    python3 psci_to_svg.py [options] INPUT.psci OUTPUT.svg

Run with --help for options.
"""

import argparse
import json
import os
import sys

from PIL import Image

from svg_export import build_svg, parse_bg_fill


# Pixels in a charset PNG whose RGB matches this value become transparent,
# mirroring CharacterSet.transparent_color in charset.py.
_CHARSET_TRANSPARENT_RGB = (0, 0, 0)

# Upper bound on the number of unique colors pulled from a palette PNG,
# matching palette.MAX_COLORS.
_PALETTE_MAX_COLORS = 1024

_PALETTE_EXTENSIONS = ('png', 'gif', 'bmp')


class _Charset:
    """Minimum duck-type of charset.CharacterSet needed by svg_export."""

    def __init__(self, image_data, map_width, map_height):
        self.image_data = image_data
        self.map_width = map_width
        self.map_height = map_height
        image_width, image_height = image_data.size
        # integer division — Playscii's CharacterSet.set_char_dimensions
        # uses int() here, so we do the same and will fail loudly if the
        # charset map dims don't divide the PNG cleanly.
        self.char_width = image_width // map_width
        self.char_height = image_height // map_height


class _Palette:
    """Minimum duck-type of palette.Palette needed by svg_export."""

    def __init__(self, colors):
        self.colors = colors


class _ArtShim:
    """Minimum duck-type of art.Art needed by svg_export.build_svg.

    Pre-indexes tile data into a dense 4D array so the get_*_at hot path
    is just a few dict lookups, and so each tile's xform default (0) is
    applied once at load time."""

    def __init__(self, psci_data, charset, palette):
        self.width = int(psci_data['width'])
        self.height = int(psci_data['height'])
        self.charset = charset
        self.palette = palette

        frames_data = psci_data['frames']
        self.frames = len(frames_data)
        self.active_frame = int(psci_data.get('active_frame', 0))

        first_frame_layers = frames_data[0]['layers']
        self.layers = len(first_frame_layers)
        self.layers_z = [l.get('z', 0) for l in first_frame_layers]
        self.layers_visibility = [
            bool(l.get('visible', 1)) for l in first_frame_layers
        ]
        self.layer_names = [
            l.get('name', 'Layer %d' % (i + 1))
            for i, l in enumerate(first_frame_layers)
        ]

        # _tiles[frame][layer][y][x] -> (char, fg, bg, xform). Rebuilt into a
        # tuple-of-ints so every field access in build_svg is O(1).
        self._tiles = []
        for frame in frames_data:
            frame_layers = []
            for layer in frame['layers']:
                layer_rows = [[None] * self.width for _ in range(self.height)]
                tiles = layer['tiles']
                expected = self.width * self.height
                if len(tiles) != expected:
                    raise ValueError(
                        'layer %r has %d tiles, expected %d (%dx%d)'
                        % (layer.get('name', '?'), len(tiles), expected,
                           self.width, self.height)
                    )
                for i, tile in enumerate(tiles):
                    x = i % self.width
                    y = i // self.width
                    layer_rows[y][x] = (
                        int(tile.get('char', 0)),
                        int(tile.get('fg', 0)),
                        int(tile.get('bg', 0)),
                        int(tile.get('xform', 0)),
                    )
                frame_layers.append(layer_rows)
            self._tiles.append(frame_layers)

    # The four accessors build_svg calls on `art`.
    def get_char_index_at(self, frame, layer, x, y):
        return self._tiles[frame][layer][y][x][0]

    def get_fg_color_index_at(self, frame, layer, x, y):
        return self._tiles[frame][layer][y][x][1]

    def get_bg_color_index_at(self, frame, layer, x, y):
        return self._tiles[frame][layer][y][x][2]

    def get_char_transform_at(self, frame, layer, x, y):
        return self._tiles[frame][layer][y][x][3]


def _find_file(name, extensions, search_dirs):
    """Look for `name.<ext>` across the search dirs. Returns absolute path
    or None. If `name` already has an extension matching one of `extensions`,
    try that literal name first."""
    for ext in extensions:
        candidate_names = [name]
        if not name.lower().endswith('.' + ext):
            candidate_names.append('%s.%s' % (name, ext))
        for cand in candidate_names:
            for d in search_dirs:
                p = os.path.join(d, cand)
                if os.path.isfile(p):
                    return p
    return None


def load_charset(name, search_dirs):
    """Load a Playscii charset by name, returning a _Charset shim.
    Searches search_dirs (in order) for `<name>.char`, then loads the
    referenced PNG from the same dir, falling back to search_dirs."""
    char_path = _find_file(name, ['char'], search_dirs)
    if not char_path:
        raise FileNotFoundError(
            'charset %r not found in: %s' % (name, ', '.join(search_dirs))
        )
    lines = []
    with open(char_path, encoding='utf-8') as f:
        for line in f:
            if not line.startswith('//'):
                lines.append(line)
    if len(lines) < 2:
        raise ValueError('charset %s: malformed .char file' % char_path)
    png_name = lines[0].strip()
    map_w, map_h = [int(x) for x in lines[1].strip().split(',')]
    # PNG lives next to the .char file by convention; fall back to the
    # general search dirs if not.
    png_path = os.path.join(os.path.dirname(char_path), png_name)
    if not os.path.isfile(png_path):
        found = _find_file(os.path.splitext(png_name)[0], ['png'], search_dirs)
        if found:
            png_path = found
        else:
            raise FileNotFoundError(
                'charset %s references image %s that could not be located'
                % (name, png_name)
            )
    img = Image.open(png_path).convert('RGBA')
    # Make pixels matching the transparent sentinel fully transparent, the
    # same post-processing CharacterSet.load_image_data does.
    iw, ih = img.size
    px = img.load()
    tr = _CHARSET_TRANSPARENT_RGB
    for y in range(ih):
        for x in range(iw):
            c = px[x, y]
            if c[0] == tr[0] and c[1] == tr[1] and c[2] == tr[2]:
                px[x, y] = (c[0], c[1], c[2], 0)
    return _Charset(img, map_w, map_h)


def load_palette(name, search_dirs):
    """Load a Playscii palette by name, returning a _Palette shim. Walks
    the PNG left-to-right, top-to-bottom and collects unique colors, just
    like Palette.load_image."""
    path = _find_file(name, _PALETTE_EXTENSIONS, search_dirs)
    if not path:
        raise FileNotFoundError(
            'palette %r not found in: %s' % (name, ', '.join(search_dirs))
        )
    img = Image.open(path).convert('RGBA')
    w, h = img.size
    colors = [(0, 0, 0, 0)]
    seen = {colors[0]}
    for y in range(h):
        for x in range(w):
            if len(colors) >= _PALETTE_MAX_COLORS:
                break
            c = img.getpixel((x, y))
            if c not in seen:
                seen.add(c)
                colors.append(c)
    return _Palette(colors)


def _default_search_dirs(subdir):
    """Default search dirs for charsets/ or palettes/: the repo directory
    alongside this script, then the user's Playscii documents dir. This
    covers the macOS dev-run layout and the default documents layout."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    home = os.path.expanduser('~')
    docs = os.path.join(home, 'Documents', 'Playscii')
    return [
        os.path.join(script_dir, subdir),
        os.path.join(docs, subdir),
    ]


def _build_arg_parser():
    ap = argparse.ArgumentParser(
        description='Convert a .psci art file to SVG without launching '
                    'the Playscii GUI.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument('input', help='Input .psci file path')
    ap.add_argument('output', help='Output .svg file path')
    ap.add_argument('--max-width', type=int, default=0,
                    help='Max rendered width in pixels, 0=native')
    ap.add_argument('--max-height', type=int, default=0,
                    help='Max rendered height in pixels, 0=native')
    ap.add_argument('--bg-fill', default='',
                    help='Fill transparent areas with this color '
                         '(name like black/white or #rrggbb)')
    ap.add_argument('--frame', type=int, default=None,
                    help='Frame index to export (default: active_frame)')
    ap.add_argument('--no-optimize-glyphs', action='store_true',
                    help='Inline glyph paths instead of reusing <symbol>')
    ap.add_argument('--no-optimize-backgrounds', action='store_true',
                    help='Emit one background rect per tile, no merging')
    ap.add_argument('--charset-dir', action='append', default=[],
                    metavar='DIR',
                    help='Additional charset search dir (can repeat)')
    ap.add_argument('--palette-dir', action='append', default=[],
                    metavar='DIR',
                    help='Additional palette search dir (can repeat)')
    return ap


def main(argv=None):
    args = _build_arg_parser().parse_args(argv)

    bg_fill, bg_err = parse_bg_fill(args.bg_fill)
    if bg_err:
        print('error: --bg-fill: %s' % bg_err, file=sys.stderr)
        return 2

    with open(args.input, encoding='utf-8') as f:
        psci = json.load(f)

    charset_dirs = list(args.charset_dir) + _default_search_dirs('charsets')
    palette_dirs = list(args.palette_dir) + _default_search_dirs('palettes')

    charset = load_charset(psci['charset'], charset_dirs)
    palette = load_palette(psci['palette'], palette_dirs)
    art = _ArtShim(psci, charset, palette)

    frame = args.frame if args.frame is not None else art.active_frame
    if not (0 <= frame < art.frames):
        print('error: frame %d out of range [0, %d)'
              % (frame, art.frames), file=sys.stderr)
        return 2

    svg_text = build_svg(
        art,
        frame=frame,
        optimize_glyphs=not args.no_optimize_glyphs,
        optimize_backgrounds=not args.no_optimize_backgrounds,
        max_width=args.max_width,
        max_height=args.max_height,
        bg_fill=bg_fill,
    )

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(svg_text)

    native_w = charset.char_width * art.width
    native_h = charset.char_height * art.height
    print('wrote %s  (%d bytes, native %dx%d, charset %s, palette %s)'
          % (args.output, len(svg_text), native_w, native_h,
             psci['charset'], psci['palette']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
