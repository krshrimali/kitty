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
from kitty.typing_compat import BossType

from ..tui.handler import Handler, result_handler
from ..tui.line_edit import LineEdit
from ..tui.loop import Loop
from ..tui.operations import styled, write_to_clipboard

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

    @Handler.atomic_update
    def draw_screen(self) -> None:
        self.cmd.clear_screen()
        sz = self.screen_size
        f = Frame(sz.cols)
        title = 'Edit annotation' if self.editing_existing else 'Annotate selection'
        lines = ['', f.top(title, location_text(self.location))]
        text_lines = self.text.splitlines() or ['']
        room = max(2, sz.rows - 10)
        for line in text_lines[:room]:
            lines.append(f.row(dim(truncate_to_width(sanitize(line), f.inner)), min(f.inner, wcswidth(sanitize(line)))))
        if len(text_lines) > room:
            extra = f'… {len(text_lines) - room} more lines …'
            lines.append(f.row(dim(extra), wcswidth(extra)))
        lines.append(f.bottom())
        lines.append('')
        if self.multiline_note:
            lines.append(f.rule('Note'))
            for line in wrap_text(self.multiline_note, f.width - 2)[: max(1, sz.rows - len(lines) - 3)]:
                lines.append(f.indented(line))
            lines.append('')
            lines.append(f.text(dim(truncate_to_width('enter save · ctrl+e re-edit in $EDITOR · esc cancel', f.width))))
        else:
            lines.append(f.text(dim(truncate_to_width('enter save · ctrl+e write a multi-line note in $EDITOR · esc cancel', f.width))))
            lines.append('')
        self.write('\r\n'.join(lines[: sz.rows]))
        if not self.multiline_note:
            self.write('\r\n')
            self.line_edit.write(self.write, f.pad + accent('note> '))

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

    def __init__(self, payload: dict[str, Any]) -> None:
        self.annotations: list[dict[str, Any]] = list(payload.get('annotations') or [])
        self.panel_title: str = payload.get('title') or 'Annotations'
        self.fmt: str = payload.get('format') or 'markdown'
        self.idx = 0
        self.ticked: set[str] = set()
        self.deleted: list[str] = []
        self.edited: dict[str, str] = {}
        self.message = ''
        self.copy_ids: list[str] = []
        self.result: dict[str, Any] = {}

    # helpers {{{
    @property
    def current(self) -> dict[str, Any] | None:
        if 0 <= self.idx < len(self.annotations):
            return self.annotations[self.idx]
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

    def finalize(self) -> None:
        self.cmd.set_cursor_visible(True)

    @Handler.atomic_update
    def draw_screen(self) -> None:
        self.cmd.clear_screen()
        sz = self.screen_size
        f = Frame(sz.cols)
        tag = f'{len(self.ticked)} ticked of {len(self.annotations)}' if self.ticked else f'{len(self.annotations)}'
        lines = ['', f.top(self.panel_title, tag)]
        if not self.annotations:
            for msg in ('', 'No annotations yet.', 'Select some text and press the annotate shortcut.', ''):
                lines.append(f.row(dim(msg), wcswidth(msg)))
        # chrome: one blank line, two border lines, the detail pane and the footer
        visible = max(1, (sz.rows - 13) // 3)
        start = max(0, min(self.idx - visible // 2, len(self.annotations) - visible))
        for i, a in enumerate(self.annotations[start : start + visible], start):
            if i > start:
                lines.append(f.row())
            is_current = i == self.idx
            ticked = a['id'] in self.ticked
            marker = accent('▸') if is_current else ' '
            tick = styled('✓', fg='green', bold=True) if ticked else dim('·')
            head = f'{i + 1}. {first_line_of(a.get("text", ""))}'
            head = truncate_to_width(head, f.inner - 5)
            width_used = 5 + wcswidth(head)
            head = bold(head) if is_current else (styled(head, fg='green') if ticked else head)
            lines.append(f.row(f'{marker} {tick}  {head}', width_used))
            loc = location_text(a.get('location') or {})
            note = self.note_for(a).replace('\n', ' ⏎ ') or '(no note)'
            sub = truncate_to_width(f'{loc} · {note}' if loc else note, f.inner - 5)
            lines.append(f.row('     ' + dim(sub), 5 + wcswidth(sub)))
        lines.append(f.bottom())

        cur = self.current
        if cur is not None:
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
        footer = self.message or 'space tick · a all · e edit · d delete · y copy · Y copy current · q quit'
        lines.append(f.text(styled(truncate_to_width(footer, f.width), fg='yellow') if self.message else dim(truncate_to_width(footer, f.width))))
        self.write('\r\n'.join(lines[: sz.rows]))

    def on_key_event(self, key_event: KeyEvent, in_bracketed_paste: bool = False) -> None:
        if key_event.type is EventType.RELEASE:
            return
        self.on_key(key_event)

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
        if key_event.matches('q') or key_event.matches('esc'):
            self.finish()
            return
        if key_event.matches('j') or key_event.matches('down') or key_event.matches('ctrl+n'):
            self.idx = min(self.idx + 1, max(0, len(self.annotations) - 1))
        elif key_event.matches('k') or key_event.matches('up') or key_event.matches('ctrl+p'):
            self.idx = max(0, self.idx - 1)
        elif key_event.matches('g') or key_event.matches('home'):
            self.idx = 0
        elif key_event.matches('shift+g') or key_event.matches('end'):
            self.idx = max(0, len(self.annotations) - 1)
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
                self.edited.pop(cur['id'], None)
                del self.annotations[self.idx]
                self.idx = min(self.idx, max(0, len(self.annotations) - 1))
                self.message = 'Annotation deleted'
        elif key_event.matches('e') or key_event.matches('enter'):
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
        self.result = {'deleted': self.deleted, 'edited': self.edited, 'copy': self.copy_ids, 'format': self.fmt}
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


if __name__ == '__main__':
    main(sys.argv)
elif __name__ == '__doc__':
    cd = sys.cli_docs  # type: ignore
    cd['usage'] = usage
    cd['options'] = OPTIONS
    cd['help_text'] = help_text
    cd['short_desc'] = 'Annotate text in kitty windows'
