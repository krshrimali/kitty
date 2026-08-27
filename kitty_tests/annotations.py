#!/usr/bin/env python
# License: GPL v3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

from functools import partial

from kitty.annotations import Annotation, AnnotationStore, Location, format_annotations
from kitty.fast_data_types import GLFW_MOUSE_BUTTON_LEFT, create_mock_window, mock_mouse_selection, send_mock_mouse_event_to_window
from kitty.window import cell_is_in_selection

from .base import BaseTest


def send_mouse_event(window, button=-1, modifiers=0, is_release=False, x=0.0, y=0, clear_click_queue=False):
    ix = int(x)
    in_left_half_of_cell = x - ix < 0.5
    send_mock_mouse_event_to_window(window, button, modifiers, is_release, ix, y, clear_click_queue, in_left_half_of_cell)


class TestAnnotations(BaseTest):
    def test_selection_bounds(self):
        s = self.create_screen()
        w = create_mock_window(s)
        ev = partial(send_mouse_event, w)
        s.callbacks.mouse_selection = lambda code: mock_mouse_selection(w, s.callbacks.current_mouse_button, code)
        for line in ('12345', '67890', 'abcde', 'fghij', 'klmno'):
            s.draw(line)

        self.ae(s.selection_bounds(), ())
        ev(GLFW_MOUSE_BUTTON_LEFT, x=1, y=1)
        ev(x=3, y=3)
        ev(GLFW_MOUSE_BUTTON_LEFT, x=3, y=3, is_release=True, clear_click_queue=True)
        (b,) = s.selection_bounds()
        self.ae((b['start_x'], b['start_y']), (1, 1))
        self.ae((b['end_x'], b['end_y']), (3, 3))
        self.assertFalse(b['rectangle_select'])
        self.ae(''.join(s.text_for_selection()), '7890abcdefgh')

        # the bounds track the text as it scrolls into the scrollback, so that
        # absolute line numbers (history count + y) stay stable
        def abs_lines():
            offset = s.historybuf.count + 1
            (q,) = s.selection_bounds()
            return offset + q['start_y'], offset + q['end_y']

        before = abs_lines()
        for line in ('xxxxx', 'yyyyy'):
            s.draw(line)
        self.ae(s.selection_bounds()[0]['start_y'], -1)
        self.ae(abs_lines(), before)

    def test_cell_is_in_selection(self):
        # this is what decides whether a right click annotates or extends the selection
        s = self.create_screen()
        w = create_mock_window(s)
        ev = partial(send_mouse_event, w)
        s.callbacks.mouse_selection = lambda code: mock_mouse_selection(w, s.callbacks.current_mouse_button, code)
        for line in ('12345', '67890', 'abcde', 'fghij', 'klmno'):
            s.draw(line)

        def in_sel(x, y):
            return cell_is_in_selection(s.current_selections(), s.columns, s.lines, x, y)

        # nothing is selected
        self.assertFalse(in_sel(2, 2))
        ev(GLFW_MOUSE_BUTTON_LEFT, x=1, y=1)
        ev(x=3, y=3)
        ev(GLFW_MOUSE_BUTTON_LEFT, x=3, y=3, is_release=True, clear_click_queue=True)
        self.ae(''.join(s.text_for_selection()), '7890abcdefgh')
        for x, y in ((1, 1), (4, 1), (0, 2), (4, 2), (0, 3), (2, 3)):
            self.assertTrue(in_sel(x, y), f'cell {x},{y} should be in the selection')
        for x, y in ((0, 1), (2, 0), (3, 3), (4, 3), (0, 4), (2, 4)):
            self.assertFalse(in_sel(x, y), f'cell {x},{y} should not be in the selection')
        # out of bounds cells are never in the selection
        for x, y in ((-1, 1), (99, 1), (1, -1), (1, 99)):
            self.assertFalse(in_sel(x, y))
        # an extra leading row in the mask must not shift the answers
        mask = s.current_selections()
        padded = bytes(s.columns) + mask
        self.assertTrue(cell_is_in_selection(padded, s.columns, s.lines, 1, 1))
        self.assertFalse(cell_is_in_selection(padded, s.columns, s.lines, 0, 1))

    def test_annotation_store(self):
        st = AnnotationStore()
        a = st.add(Annotation('one', 'first note', Location(tab_id=1, window_id=1)))
        b = st.add(Annotation('two', 'second note', Location(tab_id=1, window_id=2)))
        c = st.add(Annotation('three', 'third note', Location(tab_id=2, window_id=3)))
        self.ae(len(st), 3)
        self.ae([x.id for x in st.for_tab(1)], [a.id, b.id])
        self.ae([x.id for x in st.for_window(3)], [c.id])
        self.assertIs(st.get(b.id), b)
        self.assertIsNone(st.get('does-not-exist'))
        self.ae(st.remove([a.id]), 1)
        self.ae(len(st), 2)
        self.ae(st.remove_tab(1), 1)
        self.ae([x.id for x in st], [c.id])
        self.ae(st.remove_window(3), 1)
        self.ae(len(st), 0)

    def test_annotation_round_trip(self):
        a = Annotation('some text', 'a note', Location(tab_id=3, window_id=4, tab_title='t', window_title='w', start_line=7, end_line=9))
        b = Annotation.from_dict(a.as_dict())
        self.ae(a.as_dict(), b.as_dict())
        self.ae(b.location.describe(), 'tab: t • window: w • lines 7-9')
        self.ae(Location(start_line=5, end_line=5).describe(), 'line 5')

    def test_annotation_formatting(self):
        loc = Location(tab_title='mytab', window_title='mywin', start_line=2, end_line=3)
        anns = [Annotation('line one\nline two', 'looks wrong', loc), Annotation('other', '', loc)]
        md = format_annotations(anns)
        self.assertIn('### Annotation 1 — tab: mytab • window: mywin • lines 2-3', md)
        self.assertIn('> line one\n> line two', md)
        self.assertIn('looks wrong', md)
        self.assertIn('(no note)', md)
        plain = format_annotations(anns, 'plain')
        self.assertIn('--- annotation 1 ---', plain)
        self.assertIn('    line one', plain)
        self.ae(format_annotations([]), '')
