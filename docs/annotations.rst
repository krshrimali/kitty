Annotate text on screen
==========================

Annotations let you attach a note to a piece of text in a kitty window and then
collect all your notes, together with the text they refer to, and paste them
somewhere else. This is handy for reviewing the output of a long running
program, a diff or the responses of an AI coding agent without ever leaving the
terminal.

An annotation stores three things: the text you annotated, where that text came
from (tab, window and line numbers) and your note.


A quick tour
--------------

#. Select some text with the mouse, as usual.

#. Right click inside the selection (:kbd:`shift+right` in a program that grabs
   the mouse), or press :sc:`annotate_selection`
   (:kbd:`ctrl+shift+m` then :kbd:`m`). A small overlay appears showing the
   selected text with a prompt for your note. Type the note and press
   :kbd:`Enter`. If the note needs more than one line, press :kbd:`ctrl+e` to
   write it in your editor (:opt:`editor`) instead.

#. Repeat for as many pieces of text as you like, they can be in different
   windows of the same tab.

#. Press :sc:`show_annotations` (:kbd:`ctrl+shift+m` then :kbd:`l`) to open the
   annotations panel, which lists every annotation in the current tab.

#. In the panel, press :kbd:`y` to copy the annotations to the clipboard. With
   nothing ticked, all of them are copied; tick the ones you want with
   :kbd:`space` to copy only those.

#. Paste anywhere. Each annotation is pasted along with the text it refers to
   and where that text came from.


The annotations panel
------------------------

The panel is an overlay listing the annotations in scope, with the text and note
of the current entry shown below the list. The following keys are available:

=========================== ================================================================
Key                         Action
=========================== ================================================================
:kbd:`j`, :kbd:`down`       Move to the next annotation
:kbd:`k`, :kbd:`up`         Move to the previous annotation
:kbd:`g` / :kbd:`G`         Move to the first / last annotation
:kbd:`Tab`                  Switch focus between the list and preview in wide terminals
:kbd:`/`                    Search annotated text, notes and source information
:kbd:`?`                    Show the keyboard help overlay
:kbd:`space`                Tick or untick the current annotation
:kbd:`a`                    Tick all annotations, or untick them all if any are ticked
:kbd:`e`                    Edit the note of the current annotation in your editor
:kbd:`Enter`                Jump to the source window and line, when it is still available
:kbd:`d`                    Delete the current annotation
:kbd:`u`                    Undo the most recent deletion
:kbd:`y`                    Copy the ticked annotations, or all of them if none are ticked
:kbd:`Y`                    Copy only the current annotation
:kbd:`q`, :kbd:`Esc`        Close the panel, applying any edits and deletions
=========================== ================================================================

A filled diamond beside an annotation means its source window is still open;
an empty diamond means the stored text remains available but the source window
has closed. Pressing :kbd:`Enter` on a live source focuses its window and
scrolls to the annotated line.


What gets copied
-------------------

By default annotations are copied as Markdown, with the annotated text as a
block quote::

    ### Annotation 1 — tab: build • window: make • lines 1204-1206

    > gcc -c kitty/screen.c
    > kitty/screen.c:5671:31: error: unused parameter 'self'
    > cc1: all warnings being treated as errors

    this is the only warning that is actually a bug, the rest are noise

Pass ``plain`` as the second argument to :ac:`copy_annotations` or
:ac:`show_annotations` for an indented plain text layout instead::

    map f1 copy_annotations tab plain


Annotating without the mouse
-------------------------------

If you use :ref:`shell_integration`, you can annotate the output of the command
you just ran without selecting anything, with :sc:`annotate_last_cmd_output`
(:kbd:`ctrl+shift+m` then :kbd:`o`).

You can also supply the note directly in the mapping, in which case no overlay
is shown at all::

    map f2 annotate_selection needs a second look before merging


Scopes
---------

:ac:`show_annotations`, :ac:`copy_annotations` and :ac:`clear_annotations` all
take an optional scope as their first argument:

``tab``
    Annotations made in any window of the current tab. This is the default.

``window``
    Annotations made in the currently active window only.

``all``
    Every annotation in this kitty instance, across all tabs.

For example::

    map f3 show_annotations all
    map f4 copy_annotations window


Lifetime
-----------

Annotations are held in memory for as long as the tab they were made in is
alive, they are not written to disk. Closing a tab discards its annotations, and
so does :ac:`clear_annotations`. The text of an annotation is a copy taken when
you made it, so it survives even if the window it came from is closed or its
scrollback is cleared.


Configuration
----------------

The default shortcuts all start with :kbd:`ctrl+shift+m`:

=============================================== ==================================
Shortcut                                        Action
=============================================== ==================================
:sc:`annotate_selection`                        Annotate the selected text
:sc:`annotate_last_cmd_output`                  Annotate the last command output
:sc:`show_annotations`                          Show the panel for this tab
:sc:`show_annotations_all`                      Show the panel for all tabs
:sc:`copy_annotations`                          Copy this tab's annotations
:sc:`clear_annotations`                         Delete this tab's annotations
=============================================== ==================================

Using the mouse
------------------

Select some text and then right click anywhere inside the selection to annotate
it. This is the :ac:`mouse_annotate_selection` action, which is mapped to right
click by default. Right clicking outside the selection still extends it, the way
it always has, so nothing is lost.

In programs that grab the mouse, such as full screen TUIs, kitty never sees a
plain click, so hold :kbd:`shift`: :kbd:`shift` drag to select and
:kbd:`shift+right` click to annotate. Those work in ordinary windows too, so the
gesture is the same everywhere.

If you would rather right click always extended the selection, put this in
:file:`kitty.conf`::

    mouse_map right press ungrabbed mouse_selection extend
    mouse_map shift+right press ungrabbed,grabbed mouse_selection extend

You can of course map annotating to any other button, for example to middle
click while holding :kbd:`ctrl+shift`, whether or not the mouse is inside the
selection::

    mouse_map ctrl+shift+middle release ungrabbed annotate_selection
