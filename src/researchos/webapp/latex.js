// The LaTeX editor -- DESIGN.md Section 22 (22.0.1, 22.1); task V17, batch B1.
// Served verbatim at /latex.js and loaded by app.html with a plain same-origin
// <script src>, exactly as /desk.js and /node.js are. It defines one global,
// window.ROLatex, and calls app.html's helpers (mk, mkButton, apiGet, apiPost,
// setStatus, activateView, views, currentView, state) only at run time.
//
// The vendored code (tools/vendor/, served by GET /vendor/<name> from an
// allow-list) is never in a page's markup: the first call of loadEditorLibs()
// adds its <script> tag once, and every later call gets the same promise, so no
// page loads it until the editor opens (22.8 item 7). KaTeX (B3) will be added
// to VENDOR_SCRIPTS when it is vendored.
//
// The editor (B1c) is a page of its own, the view "latex": one .tex at a time,
// its source as written with the stex mode's colouring, 保存 (and Cmd-S), 撤销这次
// 保存, the 409 of a file changed elsewhere with what the editor holds folded,
// read-only files with their sentence, the quiet reload when the window regains
// focus, and \input / \include / \subfile / \import links the server resolved.
// What it reads and writes: GET /api/latex/file, POST /api/latex/save and
// POST /api/latex/undo (server.py). Positions it sends are code points of the
// file's text with a line break counted as the file's ending (two for CRLF);
// CodeMirror counts a line break as one and uses UTF-16, so `serverEdits`
// converts. Its unsaved text joins ROWorkspace.isDirty() (workspace.js).
(function () {
  "use strict";

  function fillVars(s, v) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return v && Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }); }
  function t(s, v) { var I = typeof window !== "undefined" && window.ROI18N; return I ? I.t(s, v) : fillVars(s, v); }

  // The vendored scripts the editor needs, in load order, and the global each one sets.
  const VENDOR_SCRIPTS = [{ src: "/vendor/codemirror.js", global: "ROCodeMirror" }];

  let loading = null;

  function addScript(entry) {
    return new Promise(function (resolve, reject) {
      if (window[entry.global]) { resolve(window[entry.global]); return; }
      const tag = document.createElement("script");
      tag.src = entry.src;
      tag.async = false;
      tag.onload = function () {
        if (window[entry.global]) resolve(window[entry.global]);
        else reject(new Error(entry.src + " did not define " + entry.global));
      };
      tag.onerror = function () { tag.remove(); reject(new Error(entry.src + " could not be loaded")); };
      document.head.appendChild(tag);
    });
  }

  // Resolves to {CodeMirror} once every vendored script has run. A failure is not
  // kept: the next call (opening the editor again) tries afresh.
  function loadEditorLibs() {
    if (!loading) {
      loading = VENDOR_SCRIPTS.reduce(function (chain, entry) {
        return chain.then(function (libs) {
          return addScript(entry).then(function (lib) { libs[entry.global] = lib; return libs; });
        });
      }, Promise.resolve({})).then(function (libs) {
        return { CodeMirror: libs.ROCodeMirror };
      }, function (err) {
        loading = null;
        throw err;
      });
    }
    return loading;
  }

  // -- coordinates (22.1) ------------------------------------------------------------------------------------------

  // Code points in a UTF-16 string: a surrogate pair is one.
  function cpLength(s) {
    let n = s.length;
    for (let i = 0; i + 1 < s.length; i++) {
      const c = s.charCodeAt(i), d = s.charCodeAt(i + 1);
      if (c >= 0xD800 && c <= 0xDBFF && d >= 0xDC00 && d <= 0xDFFF) { n -= 1; i += 1; }
    }
    return n;
  }

  // The edits that turn `doc` (a CodeMirror Text, as last opened or saved) into what `changes` make of it, in the
  // server's coordinates: {from, to, insert}, sorted, `from`/`to` code points with each line break counted as the
  // file's ending `sep`, and the inserted text joined with `sep`. More than `limit` of them (the server's 2,000) become one
  // edit from the first change's start to the last one's end: every byte outside that span is still untouched.
  function serverEdits(doc, changes, sep, limit) {
    let lineNo = 1, line = doc.line(1), base = 0;
    const at = function (pos) {   // positions come in ascending order, so the lines are walked once
      while (pos > line.to) { base += cpLength(line.text) + sep.length; lineNo += 1; line = doc.line(lineNo); }
      return base + cpLength(line.text.slice(0, pos - line.from));
    };
    const edits = [], span = [];
    changes.iterChanges(function (fromA, toA, fromB, toB, inserted) {
      edits.push({ from: at(fromA), to: at(toA), insert: inserted.sliceString(0, inserted.length, sep) });
      if (!span.length) span.push(fromB);
      span[1] = toB;
    });
    if (!limit || edits.length <= limit) return edits;
    return [{ from: edits[0].from, to: edits[edits.length - 1].to, insert: changes.apply(doc).sliceString(span[0], span[1], sep) }];
  }

  // Every line break typed, pasted or dropped becomes a line break of the file's ending: CodeMirror splits inserted
  // text only on `sep` (its lineSeparator), so a "\n" pasted into a CRLF file, or a "\r" into an LF one, is replaced.
  function breakFilter(CM, sep) {
    return CM.EditorState.transactionFilter.of(function (tr) {
      if (!tr.docChanged) return tr;
      const specs = [];
      let odd = false;
      tr.changes.iterChanges(function (fromA, toA, fromB, toB, inserted) {
        const text = inserted.sliceString(0, inserted.length, sep), fixed = text.replace(/\r\n|\r|\n/g, sep);
        if (fixed !== text) odd = true;
        specs.push({ from: fromA, to: toA, insert: fixed });
      });
      if (!odd) return tr;
      const changes = tr.startState.changes(specs);
      return { changes: changes, selection: tr.startState.selection.map(changes, 1), scrollIntoView: true };
    });
  }

  // -- includes (22.1 "One file at a time") ------------------------------------------------------------------------

  // The same commands the server reads (latex_edit._INCLUDE_RE), matched line by line outside comments.
  const INCLUDE_RE = /\\(input|include|subfile)\s*\{([^{}]*)\}|\\(input)\s+([^\s{}\\%]+)|\\(import|subimport)\*?\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g;
  function codePart(text) {
    for (let at = text.indexOf("%"); at >= 0; at = text.indexOf("%", at + 1)) {
      let run = 0;
      while (at - run - 1 >= 0 && text[at - run - 1] === "\\") run += 1;
      if (run % 2 === 0) return text.slice(0, at);
    }
    return text;
  }
  function includeKey(command, args) { return command + "\u0000" + args.join("\u0000"); }
  function includeMatches(text) {
    const found = [], code = codePart(text);
    INCLUDE_RE.lastIndex = 0;
    let m;
    while ((m = INCLUDE_RE.exec(code))) {
      const key = m[1] ? includeKey(m[1], [m[2]]) : m[3] ? includeKey("input", [m[4]]) : includeKey(m[5], [m[6], m[7]]);
      found.push({ end: m.index + m[0].length, key: key });
    }
    return found;
  }

  // -- the editor page -------------------------------------------------------------------------------------------

  const EDIT_LIMIT = 2000;  // the most edits one save may carry (latex_edit.EDIT_LIMIT)
  let ed = null;            // the open file: {path, view, savedDoc, pending, sinceSent, revision, sep, readOnly, ...}
  let opening = 0;          // the open() in progress; a later one wins
  let fromView = null;      // the page 返回 goes back to

  function dirty() { return !!ed && !ed.readOnly && !ed.pending.empty && !ed.view.state.doc.eq(ed.savedDoc); }
  function discard() {
    if (ed) ed.view.destroy();
    ed = null;
    opening += 1;
  }
  // Leaving the editor (another page, another file, another project): asks when something is unsaved, then closes it.
  function mayClose() {
    if (dirty() && !window.confirm(t("有未保存的内容，仍要离开吗？"))) return false;
    discard();
    return true;
  }
  function section() { return fromView && fromView !== "latex" ? fromView : "workspace"; }
  function back() { activateView(section()); }

  async function open(path) {
    if (!path || !mayClose()) return;
    if (currentView !== "latex") fromView = currentView || "workspace";
    const mine = ++opening, host = views.latex;
    host.replaceChildren(mk("p", "empty-note", t("载入中…")));
    activateView("latex");
    let libs, file;
    try {
      [libs, file] = await Promise.all([loadEditorLibs(), apiGet("/api/latex/file?path=" + encodeURIComponent(path))]);
    } catch (err) {
      if (mine !== opening) return;
      const box = mk("div", "latex-page"), status = mk("div", "notice-status");
      box.append(backBar(), status);
      setStatus(status, "err", t("没能打开这个文件"), err);
      host.replaceChildren(box);
      return;
    }
    if (mine !== opening || currentView !== "latex") return;
    build(host, libs.CodeMirror, file);
  }

  function backBar() {
    const bar = mk("div", "node-back");
    bar.appendChild(mkButton(t("返回"), "node-link", back));
    return bar;
  }

  function build(host, CM, file) {
    const page = mk("div", "latex-page");
    const head = mk("header", "latex-head"), tools = mk("div", "latex-tools");
    const save = mkButton(t("保存"), "btn btn-primary", () => saveNow());
    const undo = mkButton(t("撤销这次保存"), "ws-text-link hidden", () => undoSave());
    const status = mk("span", "notice-status");
    status.setAttribute("role", "status");
    tools.append(save, undo, status);
    head.append(mk("div", "node-path mono", file.path), tools);
    const readOnly = mk("p", "node-sub latex-read-only" + (file.read_only ? "" : " hidden"), file.read_only_message || "");
    const notice = mk("div", "latex-notice hidden"), box = mk("div", "latex-editor");
    page.append(backBar(), head, readOnly, notice, box);
    host.replaceChildren(page);
    const me = { path: file.path, CM: CM, ui: { save: save, undo: undo, status: status, notice: notice, box: box },
      lastKey: 0, busy: false, lastSave: null, afterComposing: null };
    ed = me;
    load(me, file);
    box.addEventListener("keydown", () => { me.lastKey = Date.now(); }, true);
    box.addEventListener("compositionend", () => {
      const later = me.afterComposing; me.afterComposing = null;
      if (later) setTimeout(later, 0);   // after the input method's last input event has been taken
    });
    page.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "s") { event.preventDefault(); saveNow(); }
    });
  }

  // (Re)loads `file` into the editor: a fresh state, so CodeMirror's own undo history starts again.
  function load(me, file) {
    const CM = me.CM;
    me.revision = file.revision;
    me.readOnly = !!file.read_only;
    me.sep = file.line_ending === "crlf" ? "\r\n" : "\n";
    if (file.mains) me.mains = file.mains;
    if (file.links) me.links = new Map(file.links.filter((l) => l.path).map((l) => [includeKey(l.command, l.args), l]));
    const extensions = [
      CM.history(), CM.drawSelection(), CM.highlightSpecialChars(), CM.EditorView.lineWrapping,
      CM.keymap.of([].concat(CM.defaultKeymap, CM.historyKeymap)),
      CM.StreamLanguage.define(CM.stex), CM.syntaxHighlighting(highlight(CM)), includeLinks(CM, me),
      CM.EditorView.contentAttributes.of({ "aria-label": t("LaTeX 源码"), spellcheck: "false" }),
      CM.EditorView.updateListener.of((update) => changed(me, update)),
    ];
    // A read-only file may mix endings: CodeMirror's own splitting shows it; nothing is ever sent.
    if (me.readOnly) extensions.push(CM.EditorState.readOnly.of(true));
    else extensions.push(CM.EditorState.lineSeparator.of(me.sep), breakFilter(CM, me.sep));
    const state = CM.EditorState.create({ doc: file.text, extensions: extensions });
    const top = me.view ? me.view.scrollDOM.scrollTop : 0;
    if (me.view) me.view.setState(state);
    else me.view = new CM.EditorView({ state: state, parent: me.ui.box });
    me.view.scrollDOM.scrollTop = top;
    me.savedDoc = state.doc;
    me.pending = CM.ChangeSet.empty(state.doc.length);
    me.sinceSent = null;
    me.ui.save.disabled = me.readOnly;
    me.ui.save.classList.toggle("hidden", me.readOnly);
  }

  // Colours from the palette's own tokens (8): commands olive, comments the quiet gray, the rest ink.
  function highlight(CM) {
    const tags = CM.tags;
    return CM.HighlightStyle.define([
      { tag: tags.tagName, color: "var(--olive)" },
      { tag: [tags.keyword, tags.atom, tags.string], color: "var(--ink-soft)" },
      { tag: [tags.special(tags.variableName), tags.number], color: "var(--ink-soft)" },
      { tag: tags.comment, color: "var(--gray)", fontStyle: "italic" },
      { tag: tags.bracket, color: "var(--gray)" },
    ]);
  }

  // A text link after each include whose file the server resolved and its validator passed; any other is text.
  function includeLinks(CM, me) {
    class IncludeLink extends CM.WidgetType {
      constructor(link) { super(); this.link = link; }
      eq(other) { return other.link === this.link; }
      toDOM() {
        const several = me.mains && me.mains.length > 1;
        const words = !this.link.own_folder ? t("打开 {file}", { file: this.link.path })
          : several ? t("打开 {file}（有几个主文件，按这个文件所在的文件夹找）", { file: this.link.path })
            : t("打开 {file}（没有找到主文件，按这个文件所在的文件夹找）", { file: this.link.path });
        const b = mkButton(words, "ws-text-link latex-include", () => open(this.link.path));
        b.addEventListener("mousedown", (event) => event.preventDefault());
        return b;
      }
      ignoreEvent() { return true; }
    }
    const build = (view) => {
      const builder = new CM.RangeSetBuilder();
      view.visibleRanges.forEach((range) => {
        for (let pos = range.from; pos <= range.to;) {
          const line = view.state.doc.lineAt(pos);
          includeMatches(line.text).forEach((m) => {
            const link = me.links && me.links.get(m.key);
            if (link) builder.add(line.from + m.end, line.from + m.end, CM.Decoration.widget({ widget: new IncludeLink(link), side: 1 }));
          });
          pos = line.to + 1;
        }
      });
      return builder.finish();
    };
    return CM.ViewPlugin.define((view) => ({
      decorations: build(view),
      update(update) { if (update.docChanged || update.viewportChanged) this.decorations = build(update.view); },
    }), { decorations: (plugin) => plugin.decorations });
  }

  function changed(me, update) {
    if (!update.docChanged || ed !== me) return;
    me.pending = me.pending.compose(update.changes);
    if (me.sinceSent) me.sinceSent = me.sinceSent.compose(update.changes);
    if (!me.busy) setStatus(me.ui.status, "", dirty() ? t("尚未保存") : "");
  }

  async function saveNow() {
    const me = ed;
    if (!me || me.readOnly || me.busy) return;
    if (me.view.composing) { me.afterComposing = saveNow; return; }
    const doc = me.view.state.doc;
    if (!dirty()) return;
    const body = { project: state.currentPath, path: me.path, revision: me.revision, edits: serverEdits(me.savedDoc, me.pending, me.sep, EDIT_LIMIT) };
    if (new TextEncoder().encode(JSON.stringify(body)).length > 4 * 1024 * 1024) {   // the server would close the connection unread
      setStatus(me.ui.status, "err", t("这次保存的内容超过 4 MB，没有保存"));
      return;
    }
    me.busy = true; me.ui.save.disabled = true; me.sinceSent = me.CM.ChangeSet.empty(doc.length);
    setStatus(me.ui.status, "", t("正在保存…"));
    let r;
    try {
      r = await apiPost("/api/latex/save", body);
    } catch (err) {
      if (ed !== me) return;
      me.busy = false; me.ui.save.disabled = false; me.sinceSent = null;
      if (err.state === "workspace_conflict") conflict(me);
      else setStatus(me.ui.status, "err", t("没能保存，当前内容已保留。"), err);
      return;
    }
    if (ed !== me) return;
    me.savedDoc = doc; me.revision = r.revision; me.pending = me.sinceSent; me.sinceSent = null;
    clearNotice(me);   // a conflict's notice and folded text are over once a save goes through
    me.busy = false; me.ui.save.disabled = false;
    if (r.changed && r.previous) me.lastSave = { revision: r.revision, previous: r.previous };
    me.ui.undo.classList.toggle("hidden", !me.lastSave);
    setStatus(me.ui.status, "", dirty() ? t("尚未保存") : t("已保存"));
  }

  // 409: the file changed elsewhere. What the editor holds stays in it and, folded, under the notice; 载入新版本 reads the
  // file again, and the folded text stays for copying back.
  function conflict(me) {
    const notice = me.ui.notice, line = mk("p", "notice-status err", t("这个文件在别处被改过。"));
    const held = mk("details", "dc-fold"), pre = mk("pre", "latex-held");
    pre.textContent = me.view.state.doc.sliceString(0, me.view.state.doc.length, me.sep);
    held.append(mk("summary", null, t("编辑器里还没保存的内容")), pre);
    const reload = mkButton(t("载入新版本"), "ws-text-link", async () => {
      reload.disabled = true;
      try {
        const file = await apiGet("/api/latex/file?path=" + encodeURIComponent(me.path));
        if (ed !== me) return;
        reloadWith(me, file, true);
        line.className = "notice-status";
        line.textContent = t("已载入新版本。你原来的内容在下面，可以复制回去。");
        reload.remove();
        setStatus(me.ui.status, "", "");
      } catch (err) {
        reload.disabled = false;
        setStatus(me.ui.status, "err", t("没能打开这个文件"), err);
      }
    });
    notice.replaceChildren(line, reload, held);
    notice.classList.remove("hidden");
    setStatus(me.ui.status, "", t("尚未保存"));
  }

  function clearNotice(me) {
    me.ui.notice.replaceChildren();
    me.ui.notice.classList.add("hidden");
  }

  // `keepNotice`: the conflict's own reload, whose notice keeps the folded text for copying back.
  function reloadWith(me, file, keepNotice) {
    load(me, file);
    if (!keepNotice) clearNotice(me);
    me.lastSave = null;   // 撤销这次保存 is offered until the file is reloaded (22.1)
    me.ui.undo.classList.add("hidden");
  }

  const UNDO_REFUSED = ["workspace_conflict", "workspace_gone", "workspace_not_found", "workspace_read_only_file"];
  async function undoSave() {
    const me = ed;
    if (!me || !me.lastSave || me.busy) return;
    if (dirty() && !window.confirm(t("撤销这次保存会丢掉还没保存的改动，仍要撤销吗？"))) return;
    me.busy = true; me.ui.save.disabled = true;
    try {
      const r = await apiPost("/api/latex/undo", { project: state.currentPath, path: me.path, revision: me.lastSave.revision, previous: me.lastSave.previous });
      if (ed !== me) return;
      me.busy = false;
      reloadWith(me, r);
      setStatus(me.ui.status, "ok", t("已撤销这次保存"));
    } catch (err) {
      if (ed !== me) return;
      me.busy = false; me.ui.save.disabled = false;
      // Withdrawn only when the server says this save can no longer be undone; a dropped connection may be tried again.
      if (UNDO_REFUSED.includes(err && err.state)) { me.lastSave = null; me.ui.undo.classList.add("hidden"); }
      setStatus(me.ui.status, "err", t("没能撤销这次保存"), err);
    }
  }

  // 22.1: when the window regains focus, a file saved elsewhere is loaded, if nothing is unsaved -- not while an input
  // method composes or within two seconds of a keystroke (it looks again then). With unsaved text, the next save's 409 says so.
  async function checkRevision() {
    const me = ed;
    if (!me || me.busy || typeof currentView === "undefined" || currentView !== "latex") return;
    const wait = me.lastKey + 2000 - Date.now();
    if (me.view.composing || wait > 0) { clearTimeout(me.recheck); me.recheck = setTimeout(checkRevision, Math.max(wait, 500)); return; }
    if (dirty()) return;
    let file;
    const url = "/api/latex/file?path=" + encodeURIComponent(me.path);
    try {
      const seen = await apiGet(url + "&links=0");   // the revision alone: no walk of the project on every focus
      if (ed !== me || me.busy || seen.revision === me.revision) return;
      file = await apiGet(url);
    } catch (err) { return; }
    if (ed !== me || me.busy || file.revision === me.revision) return;
    if (dirty() || me.view.composing || Date.now() - me.lastKey < 2000) return;
    reloadWith(me, file);
    setStatus(me.ui.status, "", t("已载入在别处保存的版本"));
  }

  // A project switch or the editor's view dropped: nothing of the previous project stays open.
  function reset() { discard(); fromView = null; }

  if (typeof window.addEventListener === "function") window.addEventListener("focus", () => { checkRevision(); });

  window.ROLatex = Object.freeze({
    loadEditorLibs: loadEditorLibs, open: open, isDirty: dirty, mayClose: mayClose, section: section, reset: reset,
    _vendorScripts: VENDOR_SCRIPTS, _pure: Object.freeze({ cpLength: cpLength, serverEdits: serverEdits, breakFilter: breakFilter, includeMatches: includeMatches }),
  });
})();
