# kitty with terminal annotations

This repository is a fork of [kitty](https://github.com/kovidgoyal/kitty), the fast, GPU-based terminal emulator. It keeps kitty's existing features and adds a terminal annotation workflow for reviewing command output, diffs, logs, and AI coding-agent responses without leaving the terminal.

Annotations attach your note to selected terminal text. You can review, edit, search, select, export, and optionally preserve those notes across restarts.

## Build and install

### Dependencies

Install kitty's normal build dependencies, plus:

- Python and a C compiler (`gcc` or `clang`)
- Go
- `pkg-config`
- `shader-slang` (`slangc`)
- `tic`, normally provided by an ncurses development package
- The Python packages in `docs/requirements.txt` when building from this Git checkout

On Arch Linux or CachyOS, the additional shader dependency is:

```bash
sudo pacman -S shader-slang
```

### Development build

From the repository root:

```bash
python3 setup.py build
./kitty/launcher/kitty
```

The development launcher must remain inside the repository. Do not copy only `kitty/launcher/kitty` to `/usr/bin`, because it loads the rest of kitty using paths relative to the repository.

### Installable Linux package

Create a virtual environment and install the documentation dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r docs/requirements.txt
```

Build and test the staged package:

```bash
python setup.py linux-package
./linux-package/bin/kitty --version
./linux-package/bin/kitty
```

Install the complete package layout:

```bash
sudo cp -a linux-package/. /usr/
```

Close all existing kitty processes before testing the newly installed build. Running processes continue using the old native libraries.

## Using annotations

1. Select text in a kitty window.
2. Right-click inside the selection, or press `Ctrl+Shift+M`, then `M`.
3. Type a note and press `Enter`.
4. Open the annotation panel with `Ctrl+Shift+M`, then `L`.
5. Tick individual annotations with `Space`, or leave everything unticked to operate on all annotations in the current scope.
6. Press `y` to copy the ticked annotations (or all annotations when none are ticked). Press `Y` to copy only the current annotation.

Use `Ctrl+E` in the annotation editor when you want to write a multi-line note in your configured editor.

## Added features

| Feature | What it does | Example use case |
| --- | --- | --- |
| Annotate selected text | Attaches a note to text selected in the terminal. | Explain why one compiler warning matters. |
| Annotate the last command | Annotates the output of the most recent shell command without selecting it manually. | Review a long test or build result. |
| Persistent source highlight | Keeps only the selected occurrence highlighted, including in scrollback. | Find an annotated error again without highlighting every copy of the same word. |
| Stable scrollback tracking | Keeps highlights attached when new output arrives or old scrollback is discarded. | Continue reviewing a long-running log. |
| Unicode and wrapped-line ranges | Tracks selections containing emoji, wide characters, tabs, or wrapped text more accurately. | Annotate international text or narrow-terminal output. |
| Responsive annotation panel | Uses a list and preview side by side in wide windows, and a stacked layout in narrow windows. | Review notes comfortably at different terminal sizes. |
| Keyboard navigation | Supports `j`/`k`, arrows, first/last navigation, ticking, editing, copying, and deletion. | Review many annotations without touching the mouse. |
| Mouse controls | Supports wheel navigation, entry selection, ticking, and clickable action labels. | Use the annotation panel without learning every shortcut. |
| Search | Searches annotated text, notes, and source information with `/`. | Find the note that mentioned a particular test. |
| Sorting | Cycles between creation, window, and source-line order with `o`. | Group review notes by their terminal window. |
| List and preview scrollbars | Shows where you are in long lists and long annotation previews. | Navigate a large code-review session. |
| Edit in `$EDITOR` | Opens the current note in your configured editor. | Write a detailed multi-line explanation in Neovim. |
| Safe deletion | Supports `u` to undo the latest panel deletion and confirms bulk clearing. | Recover a note deleted by mistake. |
| Copy and export | Copies selected or current annotations as Markdown or plain text. | Paste structured feedback into an issue or chat. |
| Save to a file | Saves exported annotations to a new file from the panel. | Keep review notes beside a project report. |
| Jump to source | Focuses a live source window and scrolls to the annotated line. | Return directly to the failing command output. |
| Closed-source indicator | Shows whether an annotation's original window is still open. | Understand why an old saved note cannot jump back to its source. |
| Tab badges | Adds an annotation count such as `[3]` to a tab title. | See which tabs still contain review notes. |
| Configurable highlight color | Uses kitty mark color 1, 2, or 3 for annotation highlights. | Choose a highlight that is readable with your color theme. |
| Optional JSON persistence | Saves annotations across kitty restarts when explicitly enabled. | Continue a review session after restarting the terminal. |
| Markdown and plain-text formats | Switches export format inside the panel with `f`. | Use Markdown for GitHub and plain text for email. |
| Long-input border fix | Horizontally scrolls long single-line notes instead of breaking the editor border. | Type a detailed note in a narrow terminal. |
| Help overlay | Shows panel shortcuts with `?`. | Discover commands without opening documentation. |

## Configuration

The default shortcuts use the `Ctrl+Shift+M` prefix. See [the complete annotation documentation](docs/annotations.rst) for scopes, actions, mouse mappings, and export formats.

Choose one of kitty's three configurable mark colors for highlights:

```conf
annotation_highlight 2
mark2_foreground black
mark2_background #f2dcd3
```

Annotations are kept only in memory by default. To preserve them across restarts:

```conf
annotation_storage ~/.local/state/kitty/annotations.json
```

Persistence is opt-in because terminal output can contain passwords, access tokens, private source code, and other sensitive information. Persisted annotations whose original windows no longer exist remain available under the `all` scope.

## Claude Code

Claude Code's fullscreen renderer can capture the mouse and use the alternate screen, preventing normal terminal selection. Launch it with both behaviors disabled:

```bash
CLAUDE_CODE_DISABLE_MOUSE=1 CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1 claude
```

## Upstream kitty

General kitty documentation, configuration, and support remain available from the [official kitty website](https://sw.kovidgoyal.net/kitty/) and the [upstream repository](https://github.com/kovidgoyal/kitty).
