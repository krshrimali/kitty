#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Storage and formatting for text annotations.

An annotation is a note attached to a piece of text that was selected in a
kitty window. Annotations live for as long as the tab that contains them, they
are not persisted to disk.
"""

import fcntl
import json
import os
import shutil
import tempfile
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
    start_line_id: int = 0
    end_line_id: int = 0
    ranges: tuple[tuple[int, int, int, int, bool], ...] = ()  # stable start/end line ids, start/end columns, rectangular
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
        if loc.get('ranges'):
            loc = dict(loc, ranges=tuple(tuple(item) for item in loc['ranges']))
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
_store_loaded = False
_known_persisted_ids: set[str] = set()


def read_persisted_annotations(path: str) -> list[dict[str, Any]]:
    from .utils import log_error

    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise TypeError('the top-level JSON value is not a list')
        return [item for item in data if isinstance(item, dict)]
    except FileNotFoundError:
        return []
    except OSError as e:
        log_error(f'Could not read annotation storage {path}: {e}')
        return []
    except (ValueError, TypeError) as e:
        backup = f'{path}.corrupt-{time.strftime("%Y%m%d-%H%M%S")}'
        try:
            shutil.copy2(path, backup)
            log_error(f'Invalid annotation storage {path}: {e}. Preserved a copy at {backup}')
        except OSError as backup_error:
            log_error(f'Invalid annotation storage {path}: {e}. Could not preserve a copy: {backup_error}')
        return []


def annotation_store() -> AnnotationStore:
    'The global, in-memory store of annotations for this kitty instance'
    global _store, _store_loaded, _known_persisted_ids
    if _store is None:
        _store = AnnotationStore()
    if not _store_loaded:
        _store_loaded = True
        path = annotation_storage_path()
        if path:
            for item in read_persisted_annotations(path):
                annotation = Annotation.from_dict(item)
                annotation.location = annotation.location._replace(tab_id=0, window_id=0)
                _store.add(annotation)
                _known_persisted_ids.add(annotation.id)
    return _store


def annotation_storage_path() -> str:
    from .fast_data_types import get_options

    path = get_options().annotation_storage
    return '' if not path or path.lower() == 'none' else os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def save_annotations() -> None:
    global _known_persisted_ids
    path = annotation_storage_path()
    if not path or _store is None:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.lock', 'a+') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        disk_items = read_persisted_annotations(path)
        disk_by_id = {item.get('id'): item for item in disk_items if isinstance(item, dict) and item.get('id')}
        current_by_id = {a.id: a for a in _store}
        merged: dict[str, dict[str, Any]] = {}
        for annotation_id, item in disk_by_id.items():
            if annotation_id not in _known_persisted_ids or annotation_id in current_by_id:
                merged[annotation_id] = item
        for annotation_id, annotation in current_by_id.items():
            if annotation_id not in _known_persisted_ids or annotation_id in disk_by_id:
                merged[annotation_id] = annotation.as_dict()
        fd, temporary = tempfile.mkstemp(prefix='.kitty-annotations-', dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(list(merged.values()), f, ensure_ascii=False, indent=2)
                f.write('\n')
            os.replace(temporary, path)
            _known_persisted_ids = set(merged)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def marker_for_ranges(ranges: dict[int, list[tuple[int, int, str]]], highlight_mark: int) -> Any:
    from .fast_data_types import set_uint_at_address, truncate_point_for_length

    def marker(text: str, left_address: int, right_address: int, color_address: int, line_number: int) -> Any:
        set_uint_at_address(color_address, highlight_mark)
        for start_x, end_x, fallback_text in ranges.get(line_number, ()):
            left = truncate_point_for_length(text, start_x) if start_x else 0
            right = len(text) - 1 if end_x < 0 else truncate_point_for_length(text, end_x) - 1
            if right < left and fallback_text:
                pos = text.find(fallback_text)
                if pos > -1:
                    left, right = pos, pos + len(fallback_text) - 1
            if right >= left:
                set_uint_at_address(left_address, left)
                set_uint_at_address(right_address, right)
                yield

    return marker


def highlight_ranges_for_location(loc: Location, fallback_text: str = '') -> dict[int, list[tuple[int, int, str]]]:
    ans: dict[int, list[tuple[int, int, str]]] = {}
    first = loc.start_line_id or loc.start_line
    last = loc.end_line_id or first + max(0, loc.end_line - loc.start_line)
    selections = loc.ranges or ((first, last, loc.start_x, loc.end_x, False),)
    for first, last, start_x, end_x, rectangle in selections:
        if not first:
            continue
        if last < first:
            first, last, start_x, end_x = last, first, end_x, start_x
        if rectangle:
            left, right = sorted((start_x, end_x))
            for line_number in range(first, last + 1):
                ans.setdefault(line_number, []).append((left, right, fallback_text if first == last else ''))
        elif first == last:
            ans.setdefault(first, []).append((start_x, end_x, fallback_text))
        else:
            ans.setdefault(first, []).append((start_x, -1, ''))
            for line_number in range(first + 1, last):
                ans.setdefault(line_number, []).append((0, -1, ''))
            ans.setdefault(last, []).append((0, end_x, ''))
    return ans


def reanchor_location_after_reflow(loc: Location, selected_text: str, physical_lines: list[tuple[int, str, bool]]) -> Location:
    'Find the same selected text after terminal lines have been rewrapped.'
    from .fast_data_types import wcswidth

    if not selected_text or len(loc.ranges) > 1 or (loc.ranges and loc.ranges[0][4]):
        return loc
    chunks: list[str] = []
    positions: list[tuple[int, int] | None] = []
    for i, (line_id, text, continued) in enumerate(physical_lines):
        if i and not continued:
            chunks.append('\n')
            positions.append(None)
        chunks.append(text)
        positions.extend((line_id, col) for col in range(len(text)))
    haystack = ''.join(chunks)
    candidates: list[int] = []
    pos = haystack.find(selected_text)
    while pos > -1:
        if pos < len(positions) and positions[pos] is not None and pos + len(selected_text) - 1 < len(positions):
            candidates.append(pos)
        pos = haystack.find(selected_text, pos + 1)
    if not candidates:
        return loc
    expected_line = loc.start_line_id or loc.start_line
    expected_x = loc.start_x

    def distance(offset: int) -> tuple[int, int]:
        anchor = positions[offset]
        assert anchor is not None
        return abs(anchor[0] - expected_line), abs(anchor[1] - expected_x)

    start_offset = min(candidates, key=distance)
    end_offset = start_offset + len(selected_text) - 1
    while end_offset > start_offset and positions[end_offset] is None:
        end_offset -= 1
    start = positions[start_offset]
    end = positions[end_offset]
    if start is None or end is None:
        return loc
    first_id, first_col = start
    last_id, last_col = end
    line_text = {line_id: text for line_id, text, continued in physical_lines}
    first_x = wcswidth(line_text[first_id][:first_col])
    last_x = wcswidth(line_text[last_id][: last_col + 1])
    new_range = (first_id, last_id, first_x, last_x, False)
    return loc._replace(start_line_id=first_id, end_line_id=last_id, start_x=first_x, end_x=last_x, ranges=(new_range,))


def reanchor_annotations_for_window(window: Any) -> bool:
    if _store is None:
        return False
    physical_lines = window.screen.physical_lines()
    changed = False
    for annotation in _store.for_window(window.id):
        location = reanchor_location_after_reflow(annotation.location, annotation.text, physical_lines)
        if location != annotation.location:
            annotation.location = location
            changed = True
    return changed


def refresh_annotation_markers(boss: Any) -> None:
    'Refresh the independent marker used to keep annotated source text visible.'
    from .fast_data_types import get_options

    highlight_mark = max(1, min(3, get_options().annotation_highlight))
    save_annotations()

    by_window: dict[int, list[Annotation]] = {}
    for annotation in annotation_store():
        if annotation.location.window_id:
            by_window.setdefault(annotation.location.window_id, []).append(annotation)
    for window_id, window in boss.window_id_map.items():
        annotations = by_window.get(window_id)
        if annotations:
            ranges: dict[int, list[tuple[int, int, str]]] = {}
            for annotation in annotations:
                loc = annotation.location
                for line_number, line_ranges in highlight_ranges_for_location(loc, annotation.text).items():
                    ranges.setdefault(line_number, []).extend(line_ranges)

            window.screen.set_annotation_marker(marker_for_ranges(ranges, highlight_mark))
        else:
            window.screen.set_annotation_marker()
    for tab_manager in boss.os_window_map.values():
        tab_manager.update_tab_bar_data()
