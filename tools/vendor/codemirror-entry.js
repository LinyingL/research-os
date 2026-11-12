// The entry of the vendored CodeMirror 6 bundle (DESIGN.md 22.0.1, task V17 B1a).
//
// `build-codemirror.mjs` bundles this file, with the exact package versions of
// `codemirror.lock.json`, into `src/researchos/webapp/vendor/codemirror.js`, an
// IIFE that sets `window.ROCodeMirror` to the names exported here. Only the
// editor page loads it, through `ROLatex.loadEditorLibs()` (webapp/latex.js).
//
// A name added or removed here is a rebuild: update the lock's "exports" list,
// run the build, and record the new sha256 in the vendor README.

export {
  EditorState, EditorSelection, Compartment, StateField, StateEffect, Transaction,
  ChangeSet, MapMode, RangeSet, RangeSetBuilder, Text,
} from "@codemirror/state";

export {
  EditorView, keymap, Decoration, ViewPlugin, WidgetType, drawSelection,
  highlightSpecialChars, placeholder,
} from "@codemirror/view";

export {
  history, defaultKeymap, historyKeymap, undo, redo, undoDepth, redoDepth,
} from "@codemirror/commands";

export {
  StreamLanguage, syntaxHighlighting, HighlightStyle,
} from "@codemirror/language";

export { tags } from "@lezer/highlight";

export { stex } from "@codemirror/legacy-modes/mode/stex";

export { search, searchKeymap } from "@codemirror/search";
