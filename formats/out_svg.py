from art_export import ArtExporter
from ui_dialog import UIDialog, Field
from ui_art_dialog import ExportOptionsDialog

from svg_export import (
    build_svg,
    parse_bg_fill,
    compute_fit_size,
    DEFAULT_MAX_WIDTH,
    DEFAULT_MAX_HEIGHT,
    DEFAULT_OPTIMIZE_GLYPHS,
    DEFAULT_OPTIMIZE_BACKGROUNDS,
    DEFAULT_BG_FILL,
)


# UI-only: default state for the dimension chain-link toggle. Lives in this
# module (not svg_export) because it's purely dialog ergonomics — the
# backend already honors aspect ratio implicitly via compute_fit_size.
DEFAULT_LINK_RATIO = True

# Dialog field indices — keep these in one place so the handle_input override
# and the confirm_pressed wiring stay consistent.
_F_MAX_W = 0
_F_MAX_H = 1
_F_LINK  = 2
_F_BG    = 3
_F_OPT_G = 4
_F_OPT_B = 5


class SVGExportOptionsDialog(ExportOptionsDialog):
    title = 'SVG image export options'
    field0_label = 'Max width in px, 0=native (%s)'
    field1_label = 'Max height in px, 0=native (%s)'
    field2_label = 'Link aspect ratio'
    field3_label = 'Fill transparency (name/#hex, blank=none)'
    field4_label = 'Optimize glyphs (reuse shapes)'
    field5_label = 'Optimize backgrounds (merge rects)'
    fields = [
        Field(label=field0_label, type=int,  width=8,
              oneline=False),
        Field(label=field1_label, type=int,  width=8,
              oneline=False),
        Field(label=field2_label, type=bool, width=0, oneline=True),
        Field(label=field3_label, type=str,
              width=UIDialog.default_field_width, oneline=False),
        Field(label=field4_label, type=bool, width=0, oneline=True),
        Field(label=field5_label, type=bool, width=0, oneline=True),
    ]
    always_redraw_labels = True
    invalid_dim_error = 'Max dimensions must be integers >= 0'

    def get_initial_field_text(self, field_number):
        if field_number == _F_MAX_W:
            return str(DEFAULT_MAX_WIDTH)
        elif field_number == _F_MAX_H:
            return str(DEFAULT_MAX_HEIGHT)
        elif field_number == _F_LINK:
            return [' ', UIDialog.true_field_text][DEFAULT_LINK_RATIO]
        elif field_number == _F_BG:
            return DEFAULT_BG_FILL
        elif field_number == _F_OPT_G:
            return [' ', UIDialog.true_field_text][DEFAULT_OPTIMIZE_GLYPHS]
        elif field_number == _F_OPT_B:
            return [' ', UIDialog.true_field_text][DEFAULT_OPTIMIZE_BACKGROUNDS]

    def get_field_label(self, field_index):
        label = self.fields[field_index].label
        if field_index in (_F_MAX_W, _F_MAX_H):
            valid, _ = self.is_input_valid()
            if not valid:
                return label % '???'
            art = self.ui.active_art
            native_w = art.charset.char_width * art.width
            native_h = art.charset.char_height * art.height
            try:
                mw = int(self.field_texts[_F_MAX_W])
                mh = int(self.field_texts[_F_MAX_H])
            except ValueError:
                return label % '???'
            fit_w, fit_h = compute_fit_size(native_w, native_h, mw, mh)
            label = label % ('%s x %s' % (fit_w, fit_h))
        return label

    def is_input_valid(self):
        try:
            mw = int(self.field_texts[_F_MAX_W])
            mh = int(self.field_texts[_F_MAX_H])
        except ValueError:
            return False, self.invalid_dim_error
        if mw < 0 or mh < 0:
            return False, self.invalid_dim_error
        _, bg_err = parse_bg_fill(self.field_texts[_F_BG])
        if bg_err:
            return False, bg_err
        return True, None

    # -- aspect-ratio chain link ------------------------------------------
    # When the link field is toggled on, edits to Max Width automatically
    # recompute Max Height (and vice versa) so the two stay proportional
    # to the art's native pixel dimensions. The backend already preserves
    # aspect ratio through compute_fit_size, so this is a UI-only affordance
    # that lets the user see the paired value while typing.

    def _link_is_on(self):
        return self.field_texts[_F_LINK] == UIDialog.true_field_text

    def _native_ratio(self):
        art = self.ui.active_art
        native_w = art.charset.char_width * art.width
        native_h = art.charset.char_height * art.height
        if native_w <= 0 or native_h <= 0:
            return None
        return native_w, native_h

    def _assign_linked_value(self, target_index, value):
        """Write an integer to a max-dimension field, respecting the same
        width limit that UIDialog.handle_input uses when the user types."""
        text = str(value)
        if len(text) < self.fields[target_index].width:
            self.field_texts[target_index] = text

    def _apply_link(self, edited_index, prev_texts, link_just_turned_on):
        ratio = self._native_ratio()
        if ratio is None:
            return
        native_w, native_h = ratio

        # If the link toggle was just switched on, snap height to width.
        if link_just_turned_on:
            try:
                w = int(self.field_texts[_F_MAX_W])
            except ValueError:
                return
            if w > 0:
                self._assign_linked_value(
                    _F_MAX_H, max(1, int(round(w * native_h / native_w)))
                )
            else:
                self._assign_linked_value(_F_MAX_H, 0)
            return

        # Otherwise, only react if a max-dimension field actually changed.
        if edited_index == _F_MAX_W and \
                self.field_texts[_F_MAX_W] != prev_texts[_F_MAX_W]:
            try:
                w = int(self.field_texts[_F_MAX_W])
            except ValueError:
                return
            if w <= 0:
                self._assign_linked_value(_F_MAX_H, 0)
            else:
                self._assign_linked_value(
                    _F_MAX_H, max(1, int(round(w * native_h / native_w)))
                )
        elif edited_index == _F_MAX_H and \
                self.field_texts[_F_MAX_H] != prev_texts[_F_MAX_H]:
            try:
                h = int(self.field_texts[_F_MAX_H])
            except ValueError:
                return
            if h <= 0:
                self._assign_linked_value(_F_MAX_W, 0)
            else:
                self._assign_linked_value(
                    _F_MAX_W, max(1, int(round(h * native_w / native_h)))
                )

    def handle_input(self, key, shift_pressed, alt_pressed, ctrl_pressed):
        # Snapshot the state the user's keystroke is about to mutate, so the
        # chain-link logic below can tell which field changed (active_field
        # can shift under us on Tab) and whether the link itself was toggled.
        edited_index = self.active_field
        prev_texts = list(self.field_texts)
        prev_link_on = self._link_is_on()

        UIDialog.handle_input(
            self, key, shift_pressed, alt_pressed, ctrl_pressed
        )

        link_on = self._link_is_on()
        if not link_on:
            return

        link_just_turned_on = (not prev_link_on) and link_on
        if not link_just_turned_on and edited_index not in (_F_MAX_W, _F_MAX_H):
            return

        self._apply_link(edited_index, prev_texts, link_just_turned_on)
        # Redraw so the auto-updated field is visible immediately rather
        # than on the next keystroke. always_redraw_labels already covers
        # label text, but field values need draw_fields to re-render.
        self.draw_fields(self.always_redraw_labels)

    def confirm_pressed(self):
        valid, reason = self.is_input_valid()
        if not valid:
            return
        self.dismiss()
        bg_fill, _ = parse_bg_fill(self.field_texts[_F_BG])
        options = {
            'max_width': int(self.field_texts[_F_MAX_W]),
            'max_height': int(self.field_texts[_F_MAX_H]),
            'bg_fill': bg_fill,
            'optimize_glyphs': bool(self.field_texts[_F_OPT_G].strip()),
            'optimize_backgrounds': bool(self.field_texts[_F_OPT_B].strip()),
        }
        ExportOptionsDialog.do_export(self.ui.app, self.filename, options)


class SVGExporter(ArtExporter):
    format_name = 'SVG image'
    format_description = """
SVG (vector) still image of the current frame. Character glyphs are emitted
as rectilinear paths derived from the bitmap character set, so the result is
pixel-exact at native resolution and scales cleanly to any size. Repeated
glyphs are deduplicated via <defs>/<use>, and adjacent background tiles of
the same color are merged into single rects for a compact output file.

Animation, CRT filter, and multi-frame export are not supported.
    """
    file_extension = 'svg'
    options_dialog_class = SVGExportOptionsDialog

    def run_export(self, out_filename, options):
        art = self.app.ui.active_art
        svg_text = build_svg(
            art,
            frame=art.active_frame,
            optimize_glyphs=options.get('optimize_glyphs',
                                        DEFAULT_OPTIMIZE_GLYPHS),
            optimize_backgrounds=options.get('optimize_backgrounds',
                                             DEFAULT_OPTIMIZE_BACKGROUNDS),
            max_width=options.get('max_width', DEFAULT_MAX_WIDTH),
            max_height=options.get('max_height', DEFAULT_MAX_HEIGHT),
            bg_fill=options.get('bg_fill'),
        )
        with open(out_filename, 'w', encoding='utf-8') as f:
            f.write(svg_text)
        return True
