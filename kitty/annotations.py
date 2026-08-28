#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Storage and formatting for text annotations.

An annotation is a note attached to a piece of text that was selected in a
kitty window. Annotations live for as long as the tab that contains them, they
are not persisted to disk.
"""

import time
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, NamedTuple

from .short_uuid import uuid4

MAX_QUOTE_LINES = 512


class Location(NamedTuple):
    tab_id: int = 0
    window_id: int = 0
    tab_title: str = ''
    window_title: str = ''
    start_line: int = 0  # 1-based line number counted from the top of the scrollback, 0 means unknown
    end_line: int = 0
    start_x: int = 0
    end_x: int = 0
    cwd: str = ''
    label: str = ''  # description of the text when it did not come from a selection

    def describe(self) -> str:
        parts = []
        if self.tab_title:
            parts.append(f'tab: {self.tab_title}')
        if self.window_title:
            parts.append(f'window: {self.window_title}')
        if self.start_line:
            if self.end_line > self.start_line:
                parts.append(f'lines {self.start_line}-{self.end_line}')
            else:
                parts.append(f'line {self.start_line}')
        if self.label:
            parts.append(self.label)
        return ' • '.join(parts)


class Annotation:
    __slots__ = ('created_at', 'id', 'location', 'note', 'text')

    def __init__(
        self, text: str, note: str = '', location: Location | None = None, id: str = '', created_at: float = 0.0
    ) -> None:
        self.id = id or uuid4()
        self.text = text
        self.note = note
        self.location = location or Location()
        self.created_at = created_at or time.time()

    def __repr__(self) -> str:
        return f'Annotation(id={self.id!r}, note={self.note!r}, text={self.text[:32]!r})'

    @property
    def title(self) -> str:
        'A one line summary of the annotated text'
        for line in self.text.splitlines():
            line = line.strip()
            if line:
                return line
        return self.text.strip() or '<blank>'

    def as_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'text': self.text,
            'note': self.note,
            'created_at': self.created_at,
            'location': self.location._asdict(),
        }

    @classmethod
    def from_dict(cls, x: dict[str, Any]) -> 'Annotation':
        loc = x.get('location') or {}
        return cls(
            text=x.get('text', ''),
            note=x.get('note', ''),
            id=x.get('id', ''),
            created_at=float(x.get('created_at') or 0.0),
            location=Location(**{k: v for k, v in loc.items() if k in Location._fields}),
        )


class AnnotationStore:
    def __init__(self) -> None:
        self.items: list[Annotation] = []

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[Annotation]:
        return iter(self.items)

    def add(self, a: Annotation) -> Annotation:
        self.items.append(a)
        return a

    def get(self, annotation_id: str) -> Annotation | None:
        for a in self.items:
            if a.id == annotation_id:
                return a
        return None

    def for_tab(self, tab_id: int) -> list[Annotation]:
        return [a for a in self.items if a.location.tab_id == tab_id]

    def for_window(self, window_id: int) -> list[Annotation]:
        return [a for a in self.items if a.location.window_id == window_id]

    def remove(self, annotation_ids: Iterable[str]) -> int:
        ids = frozenset(annotation_ids)
        if not ids:
            return 0
        before = len(self.items)
        self.items = [a for a in self.items if a.id not in ids]
        return before - len(self.items)

    def remove_tab(self, tab_id: int) -> int:
        before = len(self.items)
        self.items = [a for a in self.items if a.location.tab_id != tab_id]
        return before - len(self.items)

    def remove_window(self, window_id: int) -> int:
        before = len(self.items)
        self.items = [a for a in self.items if a.location.window_id != window_id]
        return before - len(self.items)

    def clear(self) -> None:
        self.items = []


def quote_text(text: str, prefix: str = '> ') -> str:
    lines = text.splitlines() or ['']
    if len(lines) > MAX_QUOTE_LINES:
        extra = len(lines) - MAX_QUOTE_LINES
        lines = lines[:MAX_QUOTE_LINES] + [f'… {extra} more lines elided …']
    return '\n'.join(prefix + line for line in lines)


def format_annotation(a: Annotation, num: int = 0, fmt: str = 'markdown') -> str:
    loc = a.location.describe()
    when = time.strftime('%Y-%m-%d %H:%M', time.localtime(a.created_at))
    note = a.note.strip() or '(no note)'
    if fmt == 'plain':
        header = f'--- annotation {num} ---' if num else '--- annotation ---'
        ans = [header]
        if loc:
            ans.append(f'Location: {loc}')
        ans.append(f'Time: {when}')
        ans.append('Text:')
        ans.append(quote_text(a.text, '    '))
        ans.append('Note:')
        ans.append(note)
        return '\n'.join(ans)
    title = f'### Annotation {num}' if num else '### Annotation'
    if loc:
        title += f' — {loc}'
    return f'{title}\n\n{quote_text(a.text)}\n\n{note}'


def format_annotations(annotations: Sequence[Annotation], fmt: str = 'markdown') -> str:
    if not annotations:
        return ''
    parts = [format_annotation(a, i + 1, fmt) for i, a in enumerate(annotations)]
    return '\n\n'.join(parts) + '\n'


_store: AnnotationStore | None = None


def annotation_store() -> AnnotationStore:
    'The global, in-memory store of annotations for this kitty instance'
    global _store
    if _store is None:
        _store = AnnotationStore()
    return _store


def refresh_annotation_markers(boss: Any) -> None:
    'Refresh the independent marker used to keep annotated source text visible.'
    from .fast_data_types import set_uint_at_address

    by_window: dict[int, list[Annotation]] = {}
    for annotation in annotation_store():
        if annotation.location.window_id:
            by_window.setdefault(annotation.location.window_id, []).append(annotation)
    for window_id, window in boss.window_id_map.items():
        annotations = by_window.get(window_id)
        if annotations:
            ranges: dict[int, list[tuple[str, int]]] = {}
            for annotation in annotations:
                loc = annotation.location
                if not loc.start_line:
                    continue
                pieces = annotation.text.splitlines() or ['']
                for offset, piece in enumerate(pieces):
                    line_number = loc.start_line + offset
                    expected_x = loc.start_x if offset == 0 else 0
                    ranges.setdefault(line_number, []).append((piece, expected_x))

            def marker(text: str, left_address: int, right_address: int, color_address: int, line_number: int) -> Any:
                set_uint_at_address(color_address, 3)
                for piece, expected_x in ranges.get(line_number, ()):
                    if not piece:
                        continue
                    starts: list[int] = []
                    pos = text.find(piece)
                    while pos > -1:
                        starts.append(pos)
                        pos = text.find(piece, pos + 1)
                    if starts:
                        left = min(starts, key=lambda x: abs(x - expected_x))
                        set_uint_at_address(left_address, left)
                        set_uint_at_address(right_address, left + len(piece) - 1)
                        yield

            window.screen.set_annotation_marker(marker)
        else:
            window.screen.set_annotation_marker()
