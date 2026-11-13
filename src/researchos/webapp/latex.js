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
// CodeMirror counts a line break as one and uses UTF-16, so `serverEdits` and
// `positions` convert. Its unsaved text joins ROWorkspace.isDirty() (workspace.js).
//
// Passages and their supporting files (22.2, B1e): GET /api/latex/evidence finds
// the draft's links again; the editor carries each found passage through every
// edit (a state field, mapped), and at a save a found passage whose words changed
// becomes 待复核 with 「仍然成立」 beside it -- a save never writes the ledger; only
// a click does, through POST /api/latex/evidence/<link|reanchor|withdraw>. Also
// 以它为依据的段落 on a file's page and the counts on 论文正文's rows (node.js).
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

  // `at`: {link} -- open the draft at that passage (以它为依据的段落's 「在编辑器里打开」).
  async function open(path, at) {
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
    build(host, libs.CodeMirror, file, at);
  }

  function backBar() {
    const bar = mk("div", "node-back");
    bar.appendChild(mkButton(t("返回"), "node-link", back));
    return bar;
  }

  function build(host, CM, file, at) {
    const page = mk("div", "latex-page");
    const head = mk("header", "latex-head"), tools = mk("div", "latex-tools");
    const save = mkButton(t("保存"), "btn btn-primary", () => saveNow());
    const undo = mkButton(t("撤销这次保存"), "ws-text-link hidden", () => undoSave());
    const status = mk("span", "notice-status");
    status.setAttribute("role", "status");
    const undoLink = mkButton(t("撤销"), "ws-text-link hidden", () => undoAct(ed));   // the last 关联/撤回关联/是这段 (18.3)
    tools.append(save, undo, status, undoLink);
    head.append(mk("div", "node-path mono", file.path), tools);
    const readOnly = mk("p", "node-sub latex-read-only" + (file.read_only ? "" : " hidden"), file.read_only_message || "");
    const notice = mk("div", "latex-notice hidden"), box = mk("div", "latex-editor"), side = mk("aside", "latex-side");
    const body = mk("div", "latex-body");
    body.append(box, side);
    page.append(backBar(), head, readOnly, notice, body);
    host.replaceChildren(page);
    const me = { path: file.path, CM: CM, ui: { save: save, undo: undo, status: status, notice: notice, box: box, side: side, undoLink: undoLink },
      lastKey: 0, busy: false, lastSave: null, afterComposing: null, target: at || null, panel: null };
    me.ui.linkButton = mkButton(t("关联依据文件"), "btn btn-small latex-link-button hidden", () => openLinkPanel(me));
    me.ui.linkButton.addEventListener("mousedown", (event) => event.preventDefault());   // the selection stays
    box.appendChild(me.ui.linkButton);
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
      CM.EditorView.updateListener.of((update) => { changed(me, update); if (update.selectionSet || update.docChanged || update.geometryChanged) placeLinkButton(me); }),
      passageField(CM).field,
      CM.EditorView.domEventHandlers({ click: (event) => {
        const hit = event.target && event.target.closest && event.target.closest("[data-passage]");
        if (hit) openPassage(me, hit.getAttribute("data-passage"));
      } }),
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
    // a passage can be tied to a file in any draft but one that cannot be read as the file it is (22.1's read-only reasons)
    me.mayLink = !file.read_only || file.read_only_reason === "generated";
    me.lastAct = null; me.ui.undoLink.classList.add("hidden");   // an entry's 撤销 is offered until the file is reloaded
    if (me.panel === "link") keepLinkPanel(me, state.doc);   // a passage's panel stays; loadPassages drops it if gone
    placeLinkButton(me);
    loadPassages(me, false);
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
    if (me.linkRange) me.linkRange = { from: update.changes.mapPos(me.linkRange.from, 1), to: update.changes.mapPos(me.linkRange.to, -1) };
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
    const emptied = checkPassages(me);
    me.busy = false; me.ui.save.disabled = false;
    if (r.changed && r.previous) me.lastSave = { revision: r.revision, previous: r.previous };
    me.ui.undo.classList.toggle("hidden", !me.lastSave);
    setStatus(me.ui.status, "", dirty() ? t("尚未保存") : t("已保存"));
    if (emptied) await loadPassages(me, true);   // a cut passage pasted elsewhere is found there by the server
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

  // -- passages and their supporting files (22.2) ----------------------------------------------------------------

  // Converts between the editor's positions (UTF-16, a line break as one) and the server's (code points, a line
  // break as the file's ending) in `doc`; the reverse of what `serverEdits` does for edits.
  function positions(doc, sep) {
    const base = [];
    let b = 0;
    for (let i = 1; i <= doc.lines; i++) { base.push(b); b += cpLength(doc.line(i).text) + sep.length; }
    return {
      toServer(pos) { const line = doc.lineAt(pos); return base[line.number - 1] + cpLength(line.text.slice(0, pos - line.from)); },
      toEditor(k) {
        let lo = 0, hi = base.length - 1;
        while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (base[mid] <= k) lo = mid; else hi = mid - 1; }
        const line = doc.line(lo + 1);
        let rest = k - base[lo], u = 0;
        while (rest > 0 && u < line.text.length) {
          const c = line.text.charCodeAt(u);
          u += c >= 0xD800 && c <= 0xDBFF && u + 1 < line.text.length ? 2 : 1;
          rest -= 1;
        }
        return line.from + u;
      },
    };
  }

  // Whitespace collapsed: "the passage's own words" (22.2); markup counts.
  function words(s) { return s.split(/\s+/).filter(Boolean).join(" "); }

  // The passages of the open draft live in a state field, so every edit maps them: `from` and `to` take text typed
  // right at their edge (a 「不」 before 「显著」 changes the passage). Each is {key, links, quote, state, opened, from,
  // to, candidates, local}: `opened` the state the server found it in, `local` a state this editor gave it at a save.
  let fx = null;
  function passageField(CM) {
    if (fx && fx.CM === CM) return fx;
    const set = CM.StateEffect.define();
    class Count extends CM.WidgetType {
      constructor(p) { super(); this.n = p.links.length; this.key = p.key; }
      eq(other) { return other.n === this.n && other.key === this.key; }
      toDOM() {
        const s = mk("span", "latex-count", String(this.n));
        s.setAttribute("data-passage", this.key);
        s.title = t("{n} 个依据文件", { n: this.n });
        return s;
      }
    }
    const mapped = (changes, r) => r.from == null ? r : Object.assign({}, r, { from: changes.mapPos(r.from, -1), to: Math.max(changes.mapPos(r.from, -1), changes.mapPos(r.to, 1)) });
    const decorate = (list) => CM.Decoration.set([].concat.apply([], list.filter((p) => p.from != null && p.to > p.from).map((p) => [
      CM.Decoration.mark({ class: "latex-passage" + (p.state === "found" ? "" : " latex-wait"), attributes: { "data-passage": p.key } }).range(p.from, p.to),
      CM.Decoration.widget({ widget: new Count(p), side: -1 }).range(p.from),
    ])), true);
    const field = CM.StateField.define({
      create: () => ({ list: [], deco: CM.Decoration.none }),
      update(value, tr) {
        let list = null;
        for (const e of tr.effects) if (e.is(set)) list = e.value;
        if (!list && tr.docChanged) list = value.list.map((p) => Object.assign(mapped(tr.changes, p), { candidates: p.candidates.map((c) => mapped(tr.changes, c)) }));
        return list ? { list: list, deco: decorate(list) } : value;
      },
      provide: (f) => CM.EditorView.decorations.from(f, (v) => v.deco),
    });
    fx = { CM: CM, set: set, field: field };
    return fx;
  }
  function passagesOf(me) { return me.view.state.field(passageField(me.CM).field).list; }
  function setPassages(me, list) { me.view.dispatch({ effects: passageField(me.CM).set.of(list) }); renderSide(me); }

  // The draft's links as the server finds them again in the saved text, mapped to what the editor holds now. With
  // `keepLocal` (after a click wrote an entry) a passage this editor put under 待复核 at a save stays so, unless its
  // link has a new entry since (`last`).
  async function loadPassages(me, keepLocal, retried) {
    const local = new Map();
    if (keepLocal) passagesOf(me).forEach((p) => { if (p.local) p.links.forEach((l) => local.set(l.link, { last: l.last, p: p })); });
    let data;
    try {
      data = await apiGet("/api/latex/evidence?path=" + encodeURIComponent(me.path));
    } catch (err) {
      if (ed === me) { me.evidence = { failed: err }; renderSide(me); }
      return;
    }
    if (ed !== me) return;
    if (data.revision !== me.revision) { if (!retried && !dirty()) await loadPassages(me, keepLocal, true); return; }
    const conv = positions(me.savedDoc, me.sep), pending = me.pending;
    const here = (k, assoc) => k == null ? null : pending.mapPos(conv.toEditor(k), assoc);
    const groups = new Map(), list = [];
    (data.links || []).forEach((item) => {
      const kept = local.get(item.link);
      const p = kept && kept.last === item.last
        ? { state: kept.p.state, opened: "found", local: true, from: kept.p.from, to: kept.p.to, candidates: kept.p.candidates }
        : { state: item.state, opened: item.state, local: false, from: here(item.from, -1), to: here(item.to, 1),
          candidates: (item.candidates || []).map((c) => ({ from: here(c.from, -1), to: here(c.to, 1), text: c.text })) };
      // links of one place are one passage; a passage is named by its first link, so a reload or an edit keeps its panel
      const at = p.from != null && (p.state === "found" || p.local) ? p.state + "@" + p.from + ":" + p.to : item.link;
      if (!groups.has(at)) { groups.set(at, Object.assign(p, { key: item.link, quote: item.quote, links: [] })); list.push(groups.get(at)); }
      groups.get(at).links.push(item);
    });
    me.evidence = { trusted: data.trusted, message: data.message };
    if (me.panel && me.panel !== "link") {
      const kept = list.find((p) => p.links.some((l) => l.link === me.panel));
      me.panel = kept ? kept.key : null;
    }
    setPassages(me, list);
    const target = me.target && list.find((p) => p.links.some((l) => l.link === me.target.link));
    me.target = null;
    if (target) {
      if (target.from != null) me.view.dispatch({ selection: { anchor: target.from, head: target.to }, scrollIntoView: true });
      openPassage(me, target.key);
    }
  }

  // At a save (22.2 "While editing"): a passage found when the editor opened whose words changed is 待复核
  // 「依据已变化」, its new words the first candidate with 「仍然成立」. A link 待复核 at opening is never touched.
  // A passage whose range is now empty may have been cut and pasted elsewhere: it is not called deleted here; true is
  // returned, and the caller has the server find it again in the saved text (found, 待复核 or 「段落已被删掉」 is
  // the server's answer). Nothing is written: only a click writes the ledger.
  function checkPassages(me) {
    const doc = me.view.state.doc;
    let moved = false, emptied = false;
    const list = passagesOf(me).map((p) => {
      if (p.opened !== "found" || p.from == null) return p;
      if (p.to <= p.from) { emptied = true; return p.local ? Object.assign({}, p, { local: false }) : p; }
      const now = doc.sliceString(p.from, p.to, "\n");
      const state = words(now) === words(p.quote) ? "found" : "changed";
      if (state === p.state && state !== "changed") return p;
      moved = true;
      return Object.assign({}, p, { state: state, local: state !== "found",
        candidates: state === "changed" ? [{ from: p.from, to: p.to, text: now, still: true }] : [] });
    });
    if (moved || emptied) setPassages(me, list);
    return emptied;
  }

  // Entries in the ledger, one POST each. A link or a reanchor names a passage of the saved file, so with unsaved text
  // it is refused, never saved for the person (保存 is the editor's own act, 22.1, 22.7); a withdraw names only the
  // link. `each(body)` runs after each POST that went through; what was written is then offered for 撤销 (18.3).
  async function act(me, action, bodyOf, done, each) {
    if (me.acting) return false;
    if (me.busy || (action !== "withdraw" && dirty())) { setStatus(me.ui.status, "err", t("先保存，再改依据文件")); return false; }
    const conv = positions(me.view.state.doc, me.sep), wrote = [];
    me.acting = true;
    let failed = null;
    try {
      for (const body of bodyOf(conv)) {
        const r = await apiPost("/api/latex/evidence/" + action, Object.assign({ project: state.currentPath, path: me.path, revision: me.revision }, body));
        wrote.push({ link: r && r.link, entry: r && r.entry });
        if (each && ed === me) each(body);
      }
    } catch (err) { failed = err; }
    if (ed !== me) return false;
    me.acting = false;
    me.lastAct = wrote.filter((w) => w.link && w.entry);
    me.ui.undoLink.classList.toggle("hidden", !me.lastAct.length);
    if (failed) setStatus(me.ui.status, "err", t("关联没有写入"), failed);
    else setStatus(me.ui.status, "ok", done);
    if (wrote.length) await loadPassages(me, true);
    return !failed;
  }

  // 撤销: the entries the last act wrote taken back, newest first (the engine's `undone`).
  async function undoAct(me) {
    if (!me || me.acting || !me.lastAct || !me.lastAct.length) return;
    const last = me.lastAct;
    me.acting = true; me.ui.undoLink.disabled = true;
    let failed = null;
    while (last.length && !failed) {
      const w = last[last.length - 1];
      try {
        await apiPost("/api/latex/evidence/undo", { project: state.currentPath, path: me.path, link: w.link, undoes: w.entry });
        last.pop();
      } catch (err) { failed = err; }
    }
    if (ed !== me) return;
    me.acting = false; me.ui.undoLink.disabled = false;
    me.ui.undoLink.classList.toggle("hidden", !last.length);
    if (failed) setStatus(me.ui.status, "err", t("没能撤销"), failed);
    else setStatus(me.ui.status, "ok", t("已撤销"));
    await loadPassages(me, true);
  }

  // A reload (载入新版本, a version saved elsewhere) keeps an open link panel, its files and note, when the selected
  // words are still in the file, at the place nearest where they were; else it closes, saying so.
  function keepLinkPanel(me, doc) {
    const text = doc.toString(), quote = me.linkQuote || "", was = me.linkRange ? me.linkRange.from : 0;
    let best = -1;
    for (let at = quote ? text.indexOf(quote) : -1; at >= 0; at = text.indexOf(quote, at + 1)) {
      if (best < 0 || Math.abs(at - was) < Math.abs(best - was)) best = at;
    }
    if (best >= 0 && me.mayLink) { me.linkRange = { from: best, to: best + quote.length }; return; }
    me.panel = null; me.linkRange = null;
    setStatus(me.ui.status, "err", t("选中的文字在新版本里找不到了，请重新选择"));
  }

  // 「关联依据文件」 beside a selection of words.
  function placeLinkButton(me) {
    const b = me.ui.linkButton, view = me.view;
    if (!b || !view) return;
    const sel = view.state.selection.main;
    const show = me.mayLink && !sel.empty && !!view.state.sliceDoc(sel.from, sel.to).trim();
    b.classList.toggle("hidden", !show);
    if (!show || typeof view.requestMeasure !== "function") return;
    view.requestMeasure({
      read: () => ({ at: view.coordsAtPos(sel.to), box: me.ui.box.getBoundingClientRect() }),
      write: (m) => {
        if (!m.at) { b.classList.add("hidden"); return; }
        b.style.top = Math.round(m.at.bottom - m.box.top + 4) + "px";
        b.style.left = Math.round(Math.max(4, Math.min(m.at.left - m.box.left, me.ui.box.clientWidth - 160))) + "px";
      },
    });
  }

  function quoted(text) {
    const chars = Array.from(text);
    return mk("blockquote", "latex-quote", chars.length > 200 ? chars.slice(0, 200).join("") + t("…（共 {n} 个字符）", { n: chars.length }) : text);
  }
  function closeLink(me) { return mkButton(t("关闭"), "ws-text-link", () => { me.panel = null; me.linkRange = null; renderSide(me); }); }

  // The link panel: the passage quoted, a search over the project's files (figures and tables first), a note, and
  // 「确认关联」, its one primary button. Several files may be chosen.
  function openLinkPanel(me) {
    const sel = me.view.state.selection.main;
    if (sel.empty) return;
    me.linkRange = { from: sel.from, to: sel.to };
    me.linkQuote = me.view.state.doc.sliceString(sel.from, sel.to);
    me.panel = "link";
    const side = me.ui.side, chosen = [], picked = mk("ul", "latex-files"), results = mk("div", "latex-results");
    const search = mk("input", "ws-input");
    search.setAttribute("placeholder", t("搜索项目里的文件"));
    search.setAttribute("aria-label", t("搜索项目里的文件"));
    const note = mk("textarea", "ws-input latex-note");
    note.setAttribute("placeholder", t("备注（可选）"));
    let sending = false;
    const go = mkButton(t("确认关联"), "btn btn-primary", async () => {
      if (!chosen.length || !me.linkRange || sending) return;
      sending = true; go.disabled = true;
      const range = me.linkRange, text = (note.value || "").trim();
      // each file leaves the chosen list once its link is written, so pressing again after a failure writes the rest only
      const ok = await act(me, "link", (conv) => chosen.map((file) => ({ file: file, note: text || null,
        from: conv.toServer(range.from), to: conv.toServer(range.to) })), t("已关联依据文件"),
      (body) => { chosen.splice(chosen.indexOf(body.file), 1); showPicked(); });
      sending = false;
      if (ed !== me) return;
      if (ok) { me.panel = null; me.linkRange = null; renderSide(me); } else go.disabled = !chosen.length;
    });
    go.disabled = true;
    const showPicked = () => {
      picked.replaceChildren(...chosen.map((file) => {
        const li = mk("li", null);
        li.append(mk("span", "mono", file), mkButton(t("移除"), "ws-text-link", () => { chosen.splice(chosen.indexOf(file), 1); showPicked(); }));
        return li;
      }));
      go.disabled = sending || !chosen.length;
    };
    let asked = 0;
    const find = async () => {
      const mine = ++asked;
      let data;
      try { data = await apiGet("/api/latex/files?q=" + encodeURIComponent(search.value || "")); } catch (err) {
        if (mine === asked) results.replaceChildren(mk("p", "notice-status err", t("没能列出项目里的文件")));
        return;
      }
      if (mine !== asked || ed !== me || me.panel !== "link") return;
      const out = [];
      ["figure", "data", "script", "document"].forEach((group) => {
        const files = (data.files || []).filter((f) => f.group === group);
        if (!files.length) return;
        out.push(mk("div", "latex-group", { figure: t("图和表"), data: t("数据"), script: t("脚本"), document: t("文档") }[group]));
        files.forEach((f) => out.push(mkButton(f.path, "latex-result mono", () => { if (!chosen.includes(f.path)) { chosen.push(f.path); showPicked(); } })));
      });
      if (!out.length) out.push(mk("p", "node-sub", t("没有找到这样的文件")));
      results.replaceChildren(...out);
    };
    search.addEventListener("input", find);
    side.replaceChildren(mk("h3", "latex-side-title", t("关联依据文件")), quoted(me.view.state.sliceDoc(sel.from, sel.to)),
      search, results, picked, note, go, closeLink(me));
    find();
  }


  // The passage's panel: its files (each a link to its page), the note, who linked it and when, its state, the
  // candidates when 待复核, 撤回关联. No primary button (22.7).
  function openPassage(me, key) {
    const p = passagesOf(me).find((x) => x.key === key);
    if (!p) return;
    me.panel = key;
    const side = me.ui.side, head = mk("div", "latex-side-head");
    head.appendChild(mk("h3", "latex-side-title", t("依据文件")));
    if (p.state !== "found") head.append(mk("span", "status-label tone-ochre", t("待复核")), mk("span", "node-sub-inline", p.state === "deleted" ? t("段落已被删掉") : t("依据已变化")));
    const files = mk("ul", "latex-files");
    p.links.forEach((l) => {
      const li = mk("li", null), line = mk("div", null);
      if (l.file_missing) line.append(mk("span", "mono", l.file.replace(/^[a-z]+:/, "")), mk("span", "status-label tone-ochre", t("这个文件现在不在项目里")));
      else line.appendChild(mkButton(l.file.replace(/^[a-z]+:/, ""), "ws-text-link mono", () => { if (window.RONode) RONode.openNode(l.file); }));
      li.appendChild(line);
      if (l.note) li.appendChild(mk("p", "latex-note-text", l.note));
      const when = window.RODesk && RODesk.whenWords ? RODesk.whenWords(l.at, new Date()) : "";
      li.appendChild(mk("p", "node-sub", [l.label, when].filter(Boolean).join(t("，"))));
      const withdraw = mkButton(t("撤回关联"), "ws-text-link", async () => {
        if (withdraw.disabled || !window.confirm(t("撤回这个依据文件的关联吗？"))) return;
        withdraw.disabled = true;   // one click, one entry
        if (!await act(me, "withdraw", () => [{ link: l.link }], t("已撤回关联"))) withdraw.disabled = false;
      });
      li.appendChild(withdraw);
      files.appendChild(li);
    });
    const parts = [head, quoted(p.from != null && p.state !== "deleted" ? me.view.state.sliceDoc(p.from, p.to) : p.quote), files];
    if (p.state === "changed" && p.candidates.length) {
      parts.push(mk("p", "node-sub", p.candidates[0].still ? t("这段的文字改过了。依据文件还支持它吗？") : t("原来的文字找不到了。是下面哪一段？")));
      p.candidates.slice(0, 3).forEach((c) => {
        const row = mk("div", "latex-candidate");
        const here = mkButton(c.still ? t("仍然成立") : t("是这段"), "btn btn-small", async () => {
          if (here.disabled) return;
          here.disabled = true;   // one click, one entry
          if (!await act(me, "reanchor", (conv) => p.links.map((l) => ({ link: l.link, from: conv.toServer(c.from), to: conv.toServer(c.to) })),
            c.still ? t("已记录：仍然成立") : t("已记录：是这段"))) here.disabled = false;
        });
        row.append(quoted(c.text), here);
        parts.push(row);
      });
    }
    parts.push(closeLink(me));
    side.replaceChildren(...parts);
  }

  // The side of the editor with no panel open: how many passages have supporting files, those 待复核 listed.
  function renderSide(me) {
    if (me.panel === "link") return;
    if (me.panel) { openPassage(me, me.panel); return; }
    const side = me.ui.side, ev = me.evidence || {}, list = me.view ? passagesOf(me) : [];
    const parts = [];
    if (ev.failed) parts.push(mk("p", "notice-status err", t("没能读到这份文稿的依据文件")));
    if (ev.message) parts.push(mk("p", "notice-status err", ev.message));
    const waiting = list.filter((p) => p.state !== "found");
    if (list.length) {
      const counts = mk("p", "latex-counts", t("{n} 段有依据文件", { n: list.length }));
      if (waiting.length) counts.append(" ", mk("span", "status-label tone-ochre", t("{n} 段待复核", { n: waiting.length })));
      parts.push(counts);
      waiting.forEach((p) => parts.push(mkButton(Array.from(words(p.quote)).slice(0, 40).join(""), "ws-text-link latex-waiting", () => openPassage(me, p.key))));
    } else if (me.mayLink) parts.push(mk("p", "node-sub", t("选中一段文字，可以关联依据文件。")));
    side.replaceChildren(...parts);
  }

  // -- the pages outside the editor (22.2 "What is shown") ------------------------------------------------------

  function pageState(state) {
    return { found: [t("已找到"), "plain"], deleted: [t("段落已被删掉"), "ochre"], draft_missing: [t("文稿现在打不开"), "plain"] }[state] || [t("依据已变化"), "ochre"];
  }

  // 以它为依据的段落 on a file's own page: each passage's draft, section, first 80 characters and state, with
  // 「在编辑器里打开」. Shown only when there is one; a block is placed now and filled when the answer comes.
  function renderSupported(host, page) {
    const block = mk("section", "res-block latex-supports hidden");
    host.appendChild(block);
    apiGet("/api/latex/supports?file=" + encodeURIComponent(page.id)).then((data) => {
      const passages = data.passages || [];
      if (!passages.length && !data.message) return;
      block.appendChild(mk("h2", "desk-title", t("以它为依据的段落")));
      if (data.message) block.appendChild(mk("p", "notice-status err", data.message));
      const ul = mk("ul", "res-list");
      passages.forEach((p) => {
        const li = mk("li", "res-row"), main = mk("div", "res-main"), seen = pageState(p.state);
        main.append(mk("span", "mono", p.draft));
        if (p.section) main.append(mk("span", "node-sub-inline", p.section));
        main.append(mk("span", "status-label tone-" + seen[1], seen[0]), mk("div", "latex-excerpt", p.excerpt));
        li.appendChild(main);
        if (p.state !== "draft_missing") li.appendChild(mkButton(t("在编辑器里打开"), "ws-text-link", () => open(p.draft, { link: p.link })));
        ul.appendChild(li);
      });
      block.appendChild(ul);
      block.classList.remove("hidden");
    }, () => {});
  }

  // 图表与论断, per draft under 论文正文: 「{n} 段有依据文件」 and, in ochre, 「{n} 段待复核」 when there are some. `once`, shared by
  // the rows of one list, says the ledger's own message (it cannot be trusted now) or a failed read on one row only.
  function paperCounts(host, path, once) {
    const line = mk("span", "node-sub-inline hidden");
    host.appendChild(line);
    const say = (text) => {
      if (!once || once.said) return;
      once.said = true;
      line.replaceChildren(mk("span", "notice-status err", text));
      line.classList.remove("hidden");
    };
    apiGet("/api/latex/evidence?path=" + encodeURIComponent(path)).then((data) => {
      if (data.message) { say(data.message); return; }
      const all = new Set(), waiting = new Set();
      (data.links || []).forEach((l) => {
        const key = l.state === "found" ? l.from + ":" + l.to : l.link;
        all.add(key);
        if (l.state !== "found") waiting.add(key);
      });
      if (!all.size) return;
      line.append(t("{n} 段有依据文件", { n: all.size }));
      if (waiting.size) line.append(" ", mk("span", "status-label tone-ochre", t("{n} 段待复核", { n: waiting.size })));
      line.classList.remove("hidden");
    }, () => say(t("没能读到这份文稿的依据文件")));
  }

  // The pages' refresh (app.html refreshCurrentView): evidence.toml is in the watcher's set, so a link made or withdrawn
  // elsewhere (the CLI, another window) shows in the open editor at once (22.2 "The index").
  function refresh() {
    if (ed && ed.view && !ed.busy && !ed.acting) return loadPassages(ed, true);
  }

  // A project switch or the editor's view dropped: nothing of the previous project stays open.
  function reset() { discard(); fromView = null; }

  if (typeof window.addEventListener === "function") window.addEventListener("focus", () => { checkRevision(); });

  window.ROLatex = Object.freeze({
    loadEditorLibs: loadEditorLibs, open: open, isDirty: dirty, mayClose: mayClose, section: section, reset: reset,
    renderSupported: renderSupported, paperCounts: paperCounts, refresh: refresh,
    _vendorScripts: VENDOR_SCRIPTS, _passages: () => (ed && ed.view ? passagesOf(ed) : null),
    _pure: Object.freeze({ cpLength: cpLength, serverEdits: serverEdits, breakFilter: breakFilter, includeMatches: includeMatches, positions: positions }),
  });
})();
