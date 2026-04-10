"""Pure-data SVG export for Playscii Art.

No OpenGL / SDL / numpy dependencies — this module only needs PIL (via the
charset.image_data a caller supplies). It can be driven from the in-app
exporter (formats/out_svg.py) or from a standalone CLI script
(psci_to_svg.py) without launching the GUI.

Any object with the attributes listed on `build_svg` can be passed as `art`;
this is duck-typed deliberately so CLI tools don't have to bring up the
full Application/Art/Charset stack.
"""

import xml.sax.saxutils as sax


DEFAULT_MAX_WIDTH = 0   # 0 = no constraint, keep native size
DEFAULT_MAX_HEIGHT = 0
DEFAULT_OPTIMIZE_GLYPHS = True
DEFAULT_OPTIMIZE_BACKGROUNDS = True
DEFAULT_BG_FILL = ''    # empty = leave transparent areas transparent


# Minimal named-color table for the transparency-fill option. Only a few
# common names — anything else must be supplied as #hex.
_BG_FILL_NAMES = {
    'black':  '#000000',
    'white':  '#ffffff',
    'red':    '#ff0000',
    'green':  '#00ff00',
    'blue':   '#0000ff',
    'gray':   '#808080',
    'grey':   '#808080',
    'none':   None,
    '':       None,
}


def parse_bg_fill(text):
    """Parse the user's background-fill text into a valid SVG fill color
    ('#rrggbb' / '#rgb') or None for transparent. Returns (color, error).
    Invalid input yields (None, error_message)."""
    if text is None:
        return None, None
    text = text.strip().lower()
    if text in _BG_FILL_NAMES:
        return _BG_FILL_NAMES[text], None
    if text.startswith('#') and len(text) in (4, 7):
        hex_part = text[1:]
        try:
            int(hex_part, 16)
        except ValueError:
            return None, 'Invalid hex color'
        return text, None
    return None, 'Use a name (black/white/...) or #hex'


# UV corner table — mirrors art.uv_types. Kept local to make the xform math
# self-contained. For each xform value:
#   (TL_u, TL_v, TR_u, TR_v, BL_u, BL_v, BR_u, BR_v)
_UV_TABLE = {
    0: (0, 0, 1, 0, 0, 1, 1, 1),   # UV_NORMAL
    1: (0, 1, 0, 0, 1, 1, 1, 0),   # UV_ROTATE90
    2: (1, 1, 0, 1, 1, 0, 0, 0),   # UV_ROTATE180
    3: (1, 0, 1, 1, 0, 0, 0, 1),   # UV_ROTATE270
    4: (1, 0, 0, 0, 1, 1, 0, 1),   # UV_FLIPX
    5: (0, 1, 1, 1, 0, 0, 1, 0),   # UV_FLIPY
    6: (0, 0, 0, 1, 1, 0, 1, 1),   # UV_FLIP90
    7: (1, 1, 1, 0, 0, 1, 0, 0),   # UV_FLIP270
}


def _sample_glyph_pixel(cw, ch, ox, oy, xform):
    """Map output pixel (ox, oy) in a cw*ch tile back to source glyph pixel
    (gx, gy) via UV bilinear interpolation with nearest-pixel rounding. This
    matches GL's nearest-filtered texture sampling of the charset sheet."""
    u = (ox + 0.5) / cw
    v = (oy + 0.5) / ch
    TLu, TLv, TRu, TRv, BLu, BLv, BRu, BRv = _UV_TABLE[xform]
    one_u, one_v = 1.0 - u, 1.0 - v
    us = TLu*one_u*one_v + TRu*u*one_v + BLu*one_u*v + BRu*u*v
    vs = TLv*one_u*one_v + TRv*u*one_v + BLv*one_u*v + BRv*u*v
    gx = int(us * cw)
    gy = int(vs * ch)
    if gx < 0: gx = 0
    elif gx >= cw: gx = cw - 1
    if gy < 0: gy = 0
    elif gy >= ch: gy = ch - 1
    return gx, gy


def _build_glyph_mask(charset, char_index, xform):
    """Return a list-of-lists [ch][cw] of bools marking opaque output pixels
    for (char_index, xform), or None if the resulting glyph is fully blank."""
    cw, ch = charset.char_width, charset.char_height
    img = charset.image_data
    base_x = (char_index % charset.map_width) * cw
    base_y = (char_index // charset.map_width) * ch
    mask = [[False] * cw for _ in range(ch)]
    any_on = False
    if xform == 0:
        # identity — fast path, also exact pixel match with the source sheet
        for gy in range(ch):
            row = mask[gy]
            for gx in range(cw):
                if img.getpixel((base_x + gx, base_y + gy))[3] > 0:
                    row[gx] = True
                    any_on = True
    else:
        for oy in range(ch):
            row = mask[oy]
            for ox in range(cw):
                gx, gy = _sample_glyph_pixel(cw, ch, ox, oy, xform)
                if img.getpixel((base_x + gx, base_y + gy))[3] > 0:
                    row[ox] = True
                    any_on = True
    return mask if any_on else None


def _mask_to_path_d(mask):
    """Row-run encode a boolean glyph mask into an SVG path 'd' attribute.
    Each maximal horizontal run of opaque pixels becomes one unit-high
    rectangular subpath. A single <path> can therefore describe the whole
    glyph with one fill op."""
    parts = []
    for y, row in enumerate(mask):
        cw = len(row)
        x = 0
        while x < cw:
            if row[x]:
                x_start = x
                while x < cw and row[x]:
                    x += 1
                run = x - x_start
                parts.append('M%d %dh%dv1h-%dz' % (x_start, y, run, run))
            else:
                x += 1
    return ''.join(parts)


def _rgb_hex(color):
    return '#%02x%02x%02x' % (color[0], color[1], color[2])


def _is_transparent_color(palette, color_index):
    """Palette index 0 is always the transparent sentinel in .psci. Any color
    with alpha < 255 is also treated as transparent for export purposes."""
    if color_index == 0:
        return True
    try:
        return palette.colors[color_index][3] < 255
    except IndexError:
        return True


def _merge_bg_rects(bg_grid, w, h):
    """Greedy rectangle cover of a 2D grid of color indices (None = skip).
    Returns a list of (tile_x, tile_y, tile_w, tile_h, color_index). Scans
    row-major; for each untaken cell, extends right as far as the color
    matches, then extends down as long as every cell in the candidate row
    matches that same run. O(W*H) in practice for typical art.

    Not provably optimal, but near-optimal on the kinds of flat color
    regions .psci files tend to produce."""
    used = [[False] * w for _ in range(h)]
    rects = []
    for y in range(h):
        x = 0
        while x < w:
            if used[y][x] or bg_grid[y][x] is None:
                x += 1
                continue
            color = bg_grid[y][x]
            x_end = x
            while x_end < w and not used[y][x_end] and bg_grid[y][x_end] == color:
                x_end += 1
            run_w = x_end - x
            y_end = y + 1
            while y_end < h:
                can_extend = True
                for xi in range(x, x_end):
                    if used[y_end][xi] or bg_grid[y_end][xi] != color:
                        can_extend = False
                        break
                if not can_extend:
                    break
                y_end += 1
            run_h = y_end - y
            for yi in range(y, y_end):
                used_row = used[yi]
                for xi in range(x, x_end):
                    used_row[xi] = True
            rects.append((x, y, run_w, run_h, color))
            x = x_end
    return rects


def compute_fit_size(native_w, native_h, max_w, max_h):
    """Return (width, height) attribute values to place on <svg>, honoring
    the max_w/max_h constraints while preserving aspect ratio. Never upscales
    past native — SVG is vector, so upscaling is free to the viewer anyway."""
    if max_w <= 0 and max_h <= 0:
        return native_w, native_h
    sx = (max_w / native_w) if max_w > 0 else float('inf')
    sy = (max_h / native_h) if max_h > 0 else float('inf')
    s = min(sx, sy, 1.0)
    fit_w = max(1, int(round(native_w * s)))
    fit_h = max(1, int(round(native_h * s)))
    return fit_w, fit_h


def build_svg(art, frame, optimize_glyphs, optimize_backgrounds,
              max_width, max_height, bg_fill=None):
    """Convert an Art-like object for a single frame into an SVG text string.

    The `art` argument is duck-typed and only needs:
      - width, height                          (tile grid dims)
      - layers, layers_z, layers_visibility, layer_names
      - charset: with char_width, char_height, map_width, image_data (PIL)
      - palette: with colors (list of RGBA tuples; index 0 is transparent)
      - get_char_index_at(frame, layer, x, y)
      - get_fg_color_index_at(frame, layer, x, y)
      - get_bg_color_index_at(frame, layer, x, y)
      - get_char_transform_at(frame, layer, x, y)

    No OpenGL context or numpy is required. This makes the function usable
    from tests and command-line converters without booting Playscii.
    """
    charset = art.charset
    palette = art.palette
    cw, ch = charset.char_width, charset.char_height
    tw, th = art.width, art.height
    pw, ph = tw * cw, th * ch  # native pixel size

    fit_w, fit_h = compute_fit_size(pw, ph, max_width, max_height)

    # Layer draw order: ascending z is back-to-front, matching the GL path
    # at renderable.py:421. Hidden layers are skipped (same as export render).
    layer_order = sorted(range(art.layers), key=lambda i: art.layers_z[i])
    visible_layers = [i for i in layer_order if art.layers_visibility[i]]

    mask_cache = {}
    def get_mask(char_index, xform):
        key = (char_index, xform)
        if key not in mask_cache:
            mask_cache[key] = _build_glyph_mask(charset, char_index, xform)
        return mask_cache[key]

    # Walk every visible tile in every visible layer, splitting into two
    # buckets per layer: background fills and glyph placements. The bg grid
    # is kept dense (None where transparent) so the greedy merger has a
    # uniform input.
    layer_bg_rects = []
    layer_glyph_uses = []
    for layer in visible_layers:
        bg_grid = [[None] * tw for _ in range(th)]
        glyph_uses = []
        for y in range(th):
            for x in range(tw):
                bg = art.get_bg_color_index_at(frame, layer, x, y)
                if not _is_transparent_color(palette, bg):
                    bg_grid[y][x] = int(bg)
                char_index = int(art.get_char_index_at(frame, layer, x, y))
                fg = art.get_fg_color_index_at(frame, layer, x, y)
                xform = int(art.get_char_transform_at(frame, layer, x, y))
                if char_index == 0:
                    continue
                if _is_transparent_color(palette, fg):
                    continue
                if get_mask(char_index, xform) is None:
                    continue
                glyph_uses.append((x, y, char_index, xform, int(fg)))
        if optimize_backgrounds:
            rects = _merge_bg_rects(bg_grid, tw, th)
        else:
            rects = []
            for y in range(th):
                for x in range(tw):
                    if bg_grid[y][x] is not None:
                        rects.append((x, y, 1, 1, bg_grid[y][x]))
        layer_bg_rects.append(rects)
        layer_glyph_uses.append(glyph_uses)

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'viewBox="0 0 %d %d" width="%d" height="%d" '
        'shape-rendering="crispEdges">\n'
        % (pw, ph, fit_w, fit_h)
    )

    # Optional opaque background that fills transparent areas of the art
    # (including palette-index-0 backgrounds and any tiles we skipped). Sits
    # behind every layer, so lower-layer alpha also composites onto it.
    if bg_fill:
        out.append(
            '<rect x="0" y="0" width="%d" height="%d" fill="%s"/>\n'
            % (pw, ph, bg_fill)
        )

    # Glyph dedup via <defs><symbol>. Emit in sorted key order so repeated
    # exports of the same art produce byte-identical SVGs.
    if optimize_glyphs:
        out.append('<defs>\n')
        for key in sorted(mask_cache.keys()):
            mask = mask_cache[key]
            if mask is None:
                continue
            char_index, xform = key
            d = _mask_to_path_d(mask)
            out.append(
                '<symbol id="g_%d_%d" overflow="visible">'
                '<path d="%s" fill="currentColor"/></symbol>\n'
                % (char_index, xform, d)
            )
        out.append('</defs>\n')

    for li, layer in enumerate(visible_layers):
        layer_name_attr = sax.quoteattr(art.layer_names[layer])
        out.append('<g data-layer=%s>\n' % layer_name_attr)
        for (tx, ty, rw, rh, color_index) in layer_bg_rects[li]:
            out.append(
                '<rect x="%d" y="%d" width="%d" height="%d" fill="%s"/>\n'
                % (tx * cw, ty * ch, rw * cw, rh * ch,
                   _rgb_hex(palette.colors[color_index]))
            )
        for (tx, ty, char_index, xform, fg) in layer_glyph_uses[li]:
            fg_hex = _rgb_hex(palette.colors[fg])
            if optimize_glyphs:
                out.append(
                    '<use href="#g_%d_%d" x="%d" y="%d" color="%s"/>\n'
                    % (char_index, xform, tx * cw, ty * ch, fg_hex)
                )
            else:
                mask = get_mask(char_index, xform)
                d = _mask_to_path_d(mask)
                out.append(
                    '<path transform="translate(%d %d)" d="%s" fill="%s"/>\n'
                    % (tx * cw, ty * ch, d, fg_hex)
                )
        out.append('</g>\n')

    out.append('</svg>\n')
    return ''.join(out)
