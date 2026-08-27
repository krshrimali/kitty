#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

import json
import os
import subprocess
import sys
import tempfile
from typing import Any

from kitty.fast_data_types import truncate_point_for_length, wcswidth
from kitty.key_encoding import SHIFT, EventType, KeyEvent
from kitty.typing_compat import BossType, MouseButton, MouseEvent

from ..tui.handler import Handler, result_handler
from ..tui.line_edit import LineEdit
from ..tui.loop import Loop
from ..tui.operations import MouseTracking, styled, write_to_clipboard

# Drawing helpers {{{
TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT = '╭', '╮', '╰', '╯'
HORIZONTAL, VERTICAL = '─', '│'
MARGIN, MAX_WIDTH = 2, 120


def dim(text: str) -> str:
    return styled(text, dim=True)


def bold(text: str) -> str:
    return styled(text, bold=True)


def accent(text: str) -> str:
    return styled(text, fg='green', bold=True)


def key_hint(key: str, action: str) -> str:
    return f'{bold(key)} {dim(action)}'


def truncate_to_width(text: str, width: int) -> str:
    if width < 1:
        return ''
    if wcswidth(text) <= width:
        return text
    x = truncate_point_for_length(text, max(0, width - 1))
    return text[:x] + '…'


def pad_to_width(text: str, width: int) -> str:
    text = truncate_to_width(text, width)
    return text + ' ' * max(0, width - wcswidth(text))


def sanitize(text: str) -> str:
    text = text.replace('\t', '    ')
    return ''.join(ch for ch in text if ch == ' ' or ch.isprintable())


def wrap_text(text: str, width: int) -> list[str]:
    ans: list[str] = []
    width = max(4, width)
    for para in text.splitlines() or ['']:
        para = sanitize(para)
        if not para:
            ans.append('')
            continue
        while wcswidth(para) > width:
            x = truncate_point_for_length(para, width)
            bp = para.rfind(' ', 0, x)
            if bp < max(1, x // 2):
                bp = x
            ans.append(para[:bp].rstrip())
            para = para[bp:].lstrip()
        ans.append(para)
    return ans


def visible_window(total: int, current: int, count: int) -> tuple[int, int]:
    'Return the start and end indexes for a viewport centered on the current item.'
    count = max(1, count)
    start = max(0, min(current - count // 2, total - count))
    return start, min(total, start + count)


class Frame:
    'A rounded box that floats with some space around it, rather than filling the window'

    def __init__(self, cols: int) -> None:
        self.margin = MARGIN if cols > 44 else 0
        self.width = max(20, min(cols - 2 * self.margin, MAX_WIDTH))
        self.inner = self.width - 4  # two border cells and a cell of padding on either side
        self.pad = ' ' * self.margin

    def top(self, title: str, tag: str = '') -> str:
        g = f' {tag} ' if tag else ''
        avail = max(0, self.width - 2 - wcswidth(g))
        t = truncate_to_width(f' {title} ', avail)
        fill = max(0, avail - wcswidth(t))
        return self.pad + dim(TOP_LEFT) + bold(t) + dim(HORIZONTAL * fill) + dim(g) + dim(TOP_RIGHT)

    def row(self, content: str = '', width_used: int = -1) -> str:
        'content is already styled, width_used is its display width when it contains escape codes'
        if width_used < 0:
            width_used = wcswidth(content)
        return self.pad + dim(VERTICAL) + ' ' + content + ' ' * max(0, self.inner - width_used) + ' ' + dim(VERTICAL)

    def bottom(self) -> str:
        return self.pad + dim(BOTTOM_LEFT + HORIZONTAL * (self.width - 2) + BOTTOM_RIGHT)

    def rule(self, label: str) -> str:
        fill = max(0, self.width - wcswidth(label) - 1)
        return self.pad + bold(label) + ' ' + dim(HORIZONTAL * fill)

    def text(self, content: str) -> str:
        return self.pad + content

    def indented(self, content: str) -> str:
        return self.pad + '  ' + content


# }}}


def key_event_for_char(ch: str) -> KeyEvent:
    'Synthesize a key event for a character received as plain text rather than as a key escape code'
    if ch.isupper() and ch.lower() != ch:
        return KeyEvent(key=ch.lower(), shifted_key=ch, text=ch, mods=SHIFT, shift=True)
    return KeyEvent(key=ch, text=ch)


def location_text(loc: dict[str, Any]) -> str:
    parts = []
    if loc.get('window_title'):
        parts.append(str(loc['window_title']))
    sl, el = int(loc.get('start_line') or 0), int(loc.get('end_line') or 0)
    if sl:
        parts.append(f'lines {sl}-{el}' if el > sl else f'line {sl}')
    if loc.get('label'):
        parts.append(str(loc['label']))
    return ' • '.join(parts)


def first_line_of(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return sanitize(line.strip())
    return '<blank line>'


def edit_note_in_editor(handler: Handler, initial: str, quoted_text: str) -> str | None:
    'Open the users editor to edit a (possibly multi-line) note. Returns None on failure.'
    from kitty.utils import get_editor

    preamble = [
        '',
        '# Write your annotation above. Lines starting with # are ignored.',
        '# The annotated text is shown below for reference.',
        '#',
    ]
    for line in quoted_text.splitlines()[:40]:
        preamble.append('# | ' + line)
    with tempfile.TemporaryDirectory() as tdir:
        path = os.path.join(tdir, 'kitty-annotation.md')
        with open(path, 'w') as f:
            f.write(initial.rstrip() + '\n')
            f.write('\n'.join(preamble) + '\n')
        cmd = get_editor() + [path]
        try:
            with handler.suspend():
                with open(os.ctermid(), 'r+b', buffering=0) as tty:
                    subprocess.run(cmd, stdin=tty, stdout=tty, stderr=subprocess.DEVNULL, close_fds=True)
        except Exception:
            return None
        try:
            with open(path) as f:
                raw = f.read()
        except OSError:
            return None
    lines = [line for line in raw.splitlines() if not line.startswith('#')]
    return '\n'.join(lines).strip()


class AddHandler(Handler):
    'Prompt the user for the note to attach to a piece of selected text'

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.text: str = payload.get('text', '')
        self.location: dict[str, Any] = payload.get('location') or {}
        self.editing_existing = bool(payload.get('id'))
        self.line_edit = LineEdit()
        initial = payload.get('note', '')
        self.multiline_note = initial if '\n' in initial else ''
        if not self.multiline_note:
            self.line_edit.add_text(initial)
        self.result: dict[str, Any] | None = None

    def initialize(self) -> None:
        self.draw_screen()

    def on_resize(self, new_size: Any) -> None:
        super().on_resize(new_size)
        self.draw_screen()

    @Handler.atomic_update
    def draw_screen(self) -> None:
        self.cmd.clear_screen()
        sz = self.screen_size
        f = Frame(sz.cols)
        title = 'Edit annotation' if self.editing_existing else 'Annotate selection'
        loc = location_text(self.location)
        lines = ['', f.top(title, 'selected text')]
        if loc:
            lines.append(f.text(dim(truncate_to_width('From  ' + loc, f.width))))
            lines.append('')
        text_lines = self.text.splitlines() or ['']
        room = max(2, sz.rows - 13)
        for line in text_lines[:room]:
            raw = truncate_to_width(sanitize(line), max(1, f.inner - 2))
            content = accent('┃') + ' ' + dim(raw)
            lines.append(f.row(content, 2 + wcswidth(raw)))
        if len(text_lines) > room:
            extra = f'Showing {room} of {len(text_lines)} lines · {len(text_lines) - room} hidden'
            lines.append(f.row(dim(extra), wcswidth(extra)))
        lines.append(f.bottom())
        lines.append('')
        lines.append(f.rule('Your note'))
        if self.multiline_note:
            for line in wrap_text(self.multiline_note, f.width - 2)[: max(1, sz.rows - len(lines) - 3)]:
                lines.append(f.indented(line))
            lines.append('')
            actions = f'{key_hint("Enter", "Save")}  {key_hint("Ctrl+E", "Edit")}  {key_hint("Esc", "Cancel")}'
            lines.append(f.text(truncate_to_width(actions, f.width)))
        else:
            lines.append(f.row('', 0))
            lines.append(f.bottom())
            actions = f'{key_hint("Enter", "Save")}  {key_hint("Ctrl+E", "Editor")}  {key_hint("Esc", "Cancel")}'
            lines.append(f.text(truncate_to_width(actions, f.width)))
        self.write('\r\n'.join(lines[: sz.rows]))
        if not self.multiline_note:
            # Move back into the empty input row drawn immediately above the lower border.
            self.write('\x1b[2A\r')
            self.line_edit.write(self.write, f.pad + dim(VERTICAL) + ' ' + accent('note> '))

    def handle_special_key(self, key_event: KeyEvent) -> bool:
        if key_event.matches('esc'):
            self.cancel()
            return True
        if key_event.matches('enter') or key_event.matches('ctrl+j'):
            self.save()
            return True
        if key_event.matches('ctrl+e') or key_event.matches('f2'):
            initial = self.multiline_note or self.line_edit.current_input
            note = edit_note_in_editor(self, initial, self.text)
            if note:
                self.multiline_note = note
                self.line_edit.clear()
            self.draw_screen()
            return True
        return False

    def on_key_event(self, key_event: KeyEvent, in_bracketed_paste: bool = False) -> None:
        if key_event.type is EventType.RELEASE:
            return
        if self.handle_special_key(key_event):
            return
        if self.multiline_note:
            return
        if key_event.text:
            self.add_input_text(key_event.text)
        elif self.line_edit.on_key(key_event):
            self.draw_screen()

    def on_key(self, key_event: KeyEvent) -> None:
        # keys the loop synthesizes from plain text, such as enter and backspace
        if self.handle_special_key(key_event) or self.multiline_note:
            return
        if self.line_edit.on_key(key_event):
            self.draw_screen()

    def on_text(self, text: str, in_bracketed_paste: bool = False) -> None:
        if self.multiline_note:
            return
        self.add_input_text(text)

    def add_input_text(self, text: str) -> None:
        'Add typed or pasted text to the note, control characters are not useful in a single line note'
        text = ''.join(' ' if ch in '\n\r\t' else ch for ch in text)
        text = ''.join(ch for ch in text if ch == ' ' or ch.isprintable())
        if text:
            self.line_edit.add_text(text)
            self.draw_screen()

    def on_interrupt(self) -> None:
        self.cancel()

    def on_eot(self) -> None:
        self.cancel()

    def cancel(self) -> None:
        self.result = None
        self.quit_loop(1)

    def save(self) -> None:
        note = (self.multiline_note or self.line_edit.current_input).strip()
        if not note:
            self.cmd.beep()
            return
        self.result = {'note': note, 'text': self.text, 'location': self.location, 'id': self.payload.get('id', '')}
        self.quit_loop(0)


class ListHandler(Handler):
    'The annotations panel'

    mouse_tracking = MouseTracking.buttons_only

    def __init__(self, payload: dict[str, Any]) -> None:
        self.annotations: list[dict[str, Any]] = list(payload.get('annotations') or [])
        self.panel_title: str = payload.get('title') or 'Annotations'
        self.scope: str = payload.get('scope') or 'tab'
        self.fmt: str = payload.get('format') or 'markdown'
        self.idx = 0
        self.ticked: set[str] = set()
        self.deleted: list[str] = []
        self.edited: dict[str, str] = {}
        self.message = ''
        self.copy_ids: list[str] = []
        self.result: dict[str, Any] = {}
        self.query = ''
        self.searching = False
        self.show_help = False
        self.focus = 'list'
        self.detail_offset = 0
        self.last_deleted: tuple[dict[str, Any], int] | None = None
        self.jump_id = ''
        self.entry_rows: dict[int, int] = {}

    # helpers {{{
    @property
    def displayed(self) -> list[dict[str, Any]]:
        if not self.query:
            return self.annotations
        q = self.query.casefold()
        return [
            a for a in self.annotations
            if q in '\n'.join((a.get('text', ''), self.note_for(a), location_text(a.get('location') or {}))).casefold()
        ]

    @property
    def current(self) -> dict[str, Any] | None:
        shown = self.displayed
        if 0 <= self.idx < len(shown):
            return shown[self.idx]
        return None

    def note_for(self, a: dict[str, Any]) -> str:
        return self.edited.get(a['id'], a.get('note', ''))

    def effective_ids(self) -> list[str]:
        'The ticked annotations, or all of them when nothing is ticked'
        if self.ticked:
            return [a['id'] for a in self.annotations if a['id'] in self.ticked]
        return [a['id'] for a in self.annotations]

    def formatted(self, ids: list[str]) -> str:
        pos = {annotation_id: i for i, annotation_id in enumerate(ids)}
        chosen = sorted((a for a in self.annotations if a['id'] in pos), key=lambda a: pos[a['id']])
        parts = []
        for i, a in enumerate(chosen, 1):
            loc = a.get('loc_desc') or location_text(a.get('location') or {})
            note = self.note_for(a).strip() or '(no note)'
            text = a.get('text', '')
            if self.fmt == 'plain':
                q = '\n'.join('    ' + line for line in text.splitlines())
                header = f'--- annotation {i} ---'
                if loc:
                    header += f'\nLocation: {loc}'
                parts.append(f'{header}\nText:\n{q}\nNote:\n{note}')
            else:
                q = '\n'.join('> ' + line for line in text.splitlines())
                header = f'### Annotation {i}'
                if loc:
                    header += f' — {loc}'
                parts.append(f'{header}\n\n{q}\n\n{note}')
        return ('\n\n'.join(parts) + '\n') if parts else ''

    # }}}

    def initialize(self) -> None:
        self.cmd.set_cursor_visible(False)
        self.draw_screen()

    def visible_annotations(self, count: int) -> tuple[int, list[dict[str, Any]]]:
        shown = self.displayed
        start, end = visible_window(len(shown), self.idx, count)
        return start, shown[start:end]

    def on_resize(self, new_size: Any) -> None:
        super().on_resize(new_size)
        self.draw_screen()

    def list_entry(self, i: int, a: dict[str, Any], width: int) -> tuple[str, int]:
        is_current = i == self.idx
        ticked = a['id'] in self.ticked
        marker = accent('▸') if is_current else ' '
        tick = styled('✓', fg='green', bold=True) if ticked else dim('·')
        source = '◆' if a.get('source_available') else '◇'
        head = truncate_to_width(f'{i + 1}. {source} {first_line_of(a.get("text", ""))}', max(1, width - 5))
        raw_width = 5 + wcswidth(head)
        head = bold(head) if is_current else (styled(head, fg='green') if ticked else head)
        return f'{marker} {tick}  {head}', raw_width

    def draw_wide_panel(self, f: Frame, rows: int, lines: list[str]) -> None:
        left_width = min(46, max(32, f.inner * 2 // 5))
        right_width = f.inner - left_width - 3
        cur = self.current
        detail: list[tuple[str, int]] = []
        if cur is not None:
            detail.append((bold('ANNOTATED TEXT'), len('ANNOTATED TEXT')))
            for line in (cur.get('text', '') or '').splitlines() or ['']:
                raw = truncate_to_width(sanitize(line), right_width)
                detail.append((dim(raw), wcswidth(raw)))
            detail.append(('', 0))
            detail.append((bold('NOTE'), len('NOTE')))
            for line in wrap_text(self.note_for(cur) or '(no note)', right_width):
                detail.append((line, wcswidth(line)))
        available = max(3, rows - 7)
        if self.focus == 'detail':
            detail = detail[self.detail_offset :]
        start, shown = self.visible_annotations(available // 2)
        list_rows: list[tuple[str, int]] = []
        for i, a in enumerate(shown, start):
            list_rows.append(self.list_entry(i, a, left_width))
            loc = location_text(a.get('location') or {})
            if not a.get('source_available'):
                loc = f'{loc} · source closed' if loc else 'source closed'
            note = self.note_for(a).replace('\n', ' ⏎ ') or '(no note)'
            raw = truncate_to_width(f'{loc} · {note}' if loc else note, max(1, left_width - 5))
            list_rows.append(('     ' + dim(raw), 5 + wcswidth(raw)))
        body_rows = min(available, max(len(list_rows), len(detail), 3))
        for n in range(body_rows):
            if n < len(list_rows):
                self.entry_rows[len(lines)] = start + n // 2
            lc, lw = list_rows[n] if n < len(list_rows) else ('', 0)
            rc, rw = detail[n] if n < len(detail) else ('', 0)
            content = lc + ' ' * max(0, left_width - lw) + dim(' │ ') + rc
            lines.append(f.row(content, left_width + 3 + rw))

    def finalize(self) -> None:
        self.cmd.set_cursor_visible(True)

    @Handler.atomic_update
    def draw_screen(self) -> None:
        self.cmd.clear_screen()
        sz = self.screen_size
        f = Frame(sz.cols)
        shown = self.displayed
        position = f'{self.idx + 1}/{len(shown)}' if shown else '0/0'
        tag = f'{len(self.ticked)} ticked · {position}' if self.ticked else position
        lines = ['', f.top(self.panel_title, tag)]
        self.entry_rows = {}
        context = f'{self.scope.capitalize()} · {self.fmt.capitalize()}'
        if self.query:
            context += f' · Filter: {self.query}'
        lines.append(f.text(dim(truncate_to_width(context, f.width))))
        lines.append('')
        if not shown:
            empty = 'No matching annotations.' if self.query else 'No annotations yet.'
            hint = 'Press / to change the search.' if self.query else 'Select some text and press the annotate shortcut.'
            for msg in ('', empty, hint, ''):
                lines.append(f.row(dim(msg), wcswidth(msg)))
        elif f.width >= 90:
            self.draw_wide_panel(f, sz.rows, lines)
        # chrome: title/context, border lines, detail pane and footer
        visible = max(1, (sz.rows - 15) // 3)
        start, shown = self.visible_annotations(visible)
        for i, a in (() if f.width >= 90 else enumerate(shown, start)):
            if i > start:
                lines.append(f.row())
            head, width_used = self.list_entry(i, a, f.inner)
            self.entry_rows[len(lines)] = i
            self.entry_rows[len(lines) + 1] = i
            lines.append(f.row(head, width_used))
            loc = location_text(a.get('location') or {})
            if not a.get('source_available'):
                loc = f'{loc} · source closed' if loc else 'source closed'
            note = self.note_for(a).replace('\n', ' ⏎ ') or '(no note)'
            sub = truncate_to_width(f'{loc} · {note}' if loc else note, f.inner - 5)
            lines.append(f.row('     ' + dim(sub), 5 + wcswidth(sub)))
        lines.append(f.bottom())

        cur = self.current
        if cur is not None and f.width < 90:
            lines.append('')
            lines.append(f.rule('Annotated text'))
            text_lines = (cur.get('text', '') or '').splitlines() or ['']
            for line in text_lines[:3]:
                lines.append(f.indented(dim(truncate_to_width(sanitize(line), f.width - 4))))
            if len(text_lines) > 3:
                lines.append(f.indented(dim(f'… {len(text_lines) - 3} more lines …')))
            lines.append(f.rule('Note'))
            for line in wrap_text(self.note_for(cur) or '(no note)', f.width - 4)[:3]:
                lines.append(f.indented(line))
        lines.append('')
        if self.show_help:
            help_lines = (
                'Navigation: j/k move · g/G first/last · Tab list/preview',
                'Find: / search · Esc clear search',
                'Selection: Space tick · a toggle all',
                'Actions: e edit · d delete · y copy · Y copy current · q quit',
                'Source: Enter jump to source · ◆ available · ◇ closed',
                'Safety: u undo the most recent deletion',
                'Press ? to close help',
            )
            lines = lines[: max(0, sz.rows - len(help_lines) - 2)]
            lines.extend(('', f.rule('Keyboard help'), *(f.text(x) for x in help_lines)))
        footer = self.message or ('search> ' + self.query if self.searching else '/ search · ? help · space tick · e edit · d delete · y copy · q quit')
        lines.append(f.text(styled(truncate_to_width(footer, f.width), fg='yellow') if self.message else dim(truncate_to_width(footer, f.width))))
        self.write('\r\n'.join(lines[: sz.rows]))

    def on_key_event(self, key_event: KeyEvent, in_bracketed_paste: bool = False) -> None:
        if key_event.type is EventType.RELEASE:
            return
        self.on_key(key_event)

    def on_mouse_event(self, mouse_event: MouseEvent) -> None:
        if mouse_event.buttons == MouseButton.WHEEL_UP:
            self.on_key(key_event_for_char('k'))
        elif mouse_event.buttons == MouseButton.WHEEL_DOWN:
            self.on_key(key_event_for_char('j'))
        else:
            super().on_mouse_event(mouse_event)

    def on_click(self, mouse_event: MouseEvent) -> None:
        if mouse_event.buttons != MouseButton.LEFT:
            return
        clicked = self.entry_rows.get(mouse_event.cell_y)
        if clicked is None or clicked >= len(self.displayed):
            return
        if clicked == self.idx:
            cur = self.current
            if cur is not None:
                self.ticked.symmetric_difference_update({cur['id']})
                self.message = 'Annotation ticked' if cur['id'] in self.ticked else 'Annotation unticked'
        else:
            self.idx = clicked
            self.detail_offset = 0
        self.draw_screen()

    def on_text(self, text: str, in_bracketed_paste: bool = False) -> None:
        # characters the loop reports as plain text rather than as key escape codes
        for ch in text:
            self.on_key(key_event_for_char(ch))

    def on_interrupt(self) -> None:
        self.finish()

    def on_eot(self) -> None:
        self.finish()

    def on_key(self, key_event: KeyEvent) -> None:
        self.message = ''
        if self.show_help:
            if key_event.matches('?') or key_event.matches('esc') or key_event.matches('q'):
                self.show_help = False
                self.draw_screen()
            return
        if self.searching:
            if key_event.matches('esc'):
                self.searching = False
                self.query = ''
            elif key_event.matches('enter'):
                self.searching = False
            elif key_event.matches('backspace'):
                self.query = self.query[:-1]
            elif key_event.text and key_event.text.isprintable():
                self.query += key_event.text
            self.idx = 0
            self.detail_offset = 0
            self.draw_screen()
            return
        if key_event.matches('q') or key_event.matches('esc'):
            if self.query:
                self.query = ''
                self.idx = 0
                self.draw_screen()
                return
            self.finish()
            return
        if key_event.matches('?'):
            self.show_help = True
        elif key_event.matches('/'):
            self.searching = True
        elif key_event.matches('tab'):
            self.focus = 'detail' if self.focus == 'list' else 'list'
            self.message = f'{self.focus.capitalize()} focused'
        elif (key_event.matches('j') or key_event.matches('down') or key_event.matches('ctrl+n')) and self.focus == 'detail':
            self.detail_offset += 1
        elif (key_event.matches('k') or key_event.matches('up') or key_event.matches('ctrl+p')) and self.focus == 'detail':
            self.detail_offset = max(0, self.detail_offset - 1)
        elif key_event.matches('j') or key_event.matches('down') or key_event.matches('ctrl+n'):
            self.idx = min(self.idx + 1, max(0, len(self.displayed) - 1))
            self.detail_offset = 0
        elif key_event.matches('k') or key_event.matches('up') or key_event.matches('ctrl+p'):
            self.idx = max(0, self.idx - 1)
            self.detail_offset = 0
        elif key_event.matches('g') or key_event.matches('home'):
            self.idx = 0
        elif key_event.matches('shift+g') or key_event.matches('end'):
            self.idx = max(0, len(self.displayed) - 1)
        elif key_event.matches('space'):
            cur = self.current
            if cur is not None:
                self.ticked.symmetric_difference_update({cur['id']})
                self.idx = min(self.idx + 1, max(0, len(self.annotations) - 1))
        elif key_event.matches('a'):
            if self.ticked:
                self.ticked.clear()
            else:
                self.ticked = {a['id'] for a in self.annotations}
        elif key_event.matches('d') or key_event.matches('delete'):
            cur = self.current
            if cur is not None:
                self.deleted.append(cur['id'])
                self.ticked.discard(cur['id'])
                original_idx = self.annotations.index(cur)
                self.last_deleted = cur, original_idx
                self.annotations.remove(cur)
                self.idx = min(self.idx, max(0, len(self.displayed) - 1))
                self.message = 'Annotation deleted · u undo'
        elif key_event.matches('u'):
            if self.last_deleted is None:
                self.message = 'Nothing to undo'
            else:
                annotation, original_idx = self.last_deleted
                self.annotations.insert(min(original_idx, len(self.annotations)), annotation)
                try:
                    self.deleted.remove(annotation['id'])
                except ValueError:
                    pass
                self.last_deleted = None
                shown = self.displayed
                if annotation in shown:
                    self.idx = shown.index(annotation)
                self.message = 'Deletion undone'
        elif key_event.matches('enter'):
            cur = self.current
            if cur is None or not cur.get('source_available'):
                self.message = 'The source window is no longer available'
            else:
                self.jump_id = cur['id']
                self.finish()
                return
        elif key_event.matches('e'):
            cur = self.current
            if cur is not None:
                note = edit_note_in_editor(self, self.note_for(cur), cur.get('text', ''))
                if note is None:
                    self.message = 'Could not run your editor'
                elif note != self.note_for(cur):
                    self.edited[cur['id']] = note
                    self.message = 'Note updated'
        elif key_event.matches('y'):
            self.copy(self.effective_ids())
        elif key_event.matches('shift+y'):
            cur = self.current
            self.copy([cur['id']] if cur is not None else [])
        else:
            return
        self.draw_screen()

    def copy(self, ids: list[str]) -> None:
        if not ids:
            self.message = 'Nothing to copy'
            return
        self.copy_ids = ids
        text = self.formatted(ids)
        self.write(write_to_clipboard(text))
        n = len(ids)
        self.message = f'Copied {n} annotation{"" if n == 1 else "s"} to the clipboard'

    def finish(self) -> None:
        self.result = {'deleted': self.deleted, 'edited': self.edited, 'copy': self.copy_ids, 'format': self.fmt, 'jump': self.jump_id}
        self.quit_loop(0)


OPTIONS = '''
--mode
choices=list,add
default=list
Whether to add a new annotation or manage the list of existing annotations.
'''.format
help_text = 'Add and manage annotations attached to text in kitty windows. Used internally by kitty, not intended to be run directly.'
usage = ''


def main(args: list[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(sys.stdin.read() or '{}')
    except Exception:
        payload = {}
    mode = payload.get('mode') or ('add' if '--mode=add' in args else 'list')
    handler: Handler = AddHandler(payload) if mode == 'add' else ListHandler(payload)
    loop = Loop()
    loop.loop(handler)
    if isinstance(handler, AddHandler):
        return handler.result
    assert isinstance(handler, ListHandler)
    return handler.result or None


@result_handler()
def handle_result(args: list[str], data: dict[str, Any] | None, target_window_id: int, boss: BossType) -> None:
    from kitty.annotations import Annotation, Location, annotation_store, format_annotations
    from kitty.clipboard import set_clipboard_string

    if not data:
        return
    store = annotation_store()
    if 'note' in data:  # add mode
        loc = data.get('location') or {}
        existing_id = data.get('id') or ''
        if existing_id:
            a = store.get(existing_id)
            if a is not None:
                a.note = data['note']
            return
        store.add(
            Annotation(
                text=data.get('text', ''),
                note=data['note'],
                location=Location(**{k: v for k, v in loc.items() if k in Location._fields}),
            )
        )
        return
    # list mode
    for annotation_id, note in (data.get('edited') or {}).items():
        a = store.get(annotation_id)
        if a is not None:
            a.note = note
    copy_ids = list(data.get('copy') or ())
    if copy_ids:
        pos = {annotation_id: i for i, annotation_id in enumerate(copy_ids)}
        anns = sorted((a for a in store if a.id in pos), key=lambda a: pos[a.id])
        text = format_annotations(anns, data.get('format') or 'markdown')
        if text:
            set_clipboard_string(text)
    store.remove(data.get('deleted') or ())
    if jump_id := data.get('jump'):
        a = store.get(jump_id)
        if a is not None and (window := boss.window_id_map.get(a.location.window_id)) is not None:
            boss.set_active_window(window, switch_os_window_if_needed=True)
            if a.location.start_line:
                total_lines = window.screen.historybuf.count + window.screen.lines
                window.screen.scroll_to_absolute(float(max(0, total_lines - a.location.start_line)))


if __name__ == '__main__':
    main(sys.argv)
elif __name__ == '__doc__':
    cd = sys.cli_docs  # type: ignore
    cd['usage'] = usage
    cd['options'] = OPTIONS
    cd['help_text'] = help_text
    cd['short_desc'] = 'Annotate text in kitty windows'
