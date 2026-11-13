// The LaTeX editor -- DESIGN.md Section 22 (22.0.1, 22.1); task V17, batch B1.
// Served verbatim at /latex.js and loaded by app.html with a plain same-origin
// <script src>, exactly as /desk.js and /node.js are. It defines one global,
// window.ROLatex, and calls app.html's helpers (mk, mkButton, apiGet, apiPost,
// setStatus, activateView, views, currentView, state) only at run time.
//
// The vendored code (tools/vendor/, served by GET /vendor/<name> from an
// allow-list) is never in a page's markup: the first call of loadEditorLibs()
// adds its <script> tag once, and every later call gets the same promise, so no
// page loads it until the editor opens (22.8 item 7). KaTeX (B3) is loaded the
// same way but apart, by loadMath(), which only the editor calls: its script, its
// stylesheet and, through the stylesheet, its fonts; a failure leaves formulas as
// written and the editor working.
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
//
// Like Word (22.1, B3b): the range reader `readLatex`, a state field of decorations (markup hidden where the selection does
// not touch it, formulas by KaTeX, cite and ref chips, list items, the folded 导言区), 「显示源码」, and Return by context.
//
// Export to PDF (22.4, B2b): 「导出 PDF」, a quiet button beside 保存 and on a .tex draft's own page (`exporter`), with the
// settings line GET /api/latex/export answers, POST /api/latex/export and /api/latex/export/stop, and the result.
(function () {
  "use strict";

  function fillVars(s, v) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return v && Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }); }
  function t(s, v) { var I = typeof window !== "undefined" && window.ROI18N; return I ? I.t(s, v) : fillVars(s, v); }

  // The vendored scripts the editor needs, in load order, and the global each one sets.
  const VENDOR_SCRIPTS = [{ src: "/vendor/codemirror.js", global: "ROCodeMirror" }];

  let loading = null;

  // onTag, if given, is handed the script tag it adds, so a caller can take it away when a sibling load fails.
  function addScript(entry, onTag) {
    return new Promise(function (resolve, reject) {
      if (window[entry.global]) { resolve(window[entry.global]); return; }
      const tag = document.createElement("script");
      if (onTag) onTag(tag);
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

  // Resolves to window.katex once /vendor/katex.js has run and /vendor/katex.css has loaded (it names the fonts,
  // /vendor/fonts/*.woff2, which the browser then fetches as a formula needs them). Like loadEditorLibs: one link and
  // one script tag however often it is called, and a failure is not kept, its tags taken away for the next try.
  let mathLoading = null;
  function loadMath() {
    if (!mathLoading) {
      const link = document.createElement("link");
      const style = new Promise(function (resolve, reject) {
        link.rel = "stylesheet";
        link.href = "/vendor/katex.css";
        link.onload = function () { resolve(); };
        link.onerror = function () { reject(new Error("/vendor/katex.css could not be loaded")); };
        document.head.appendChild(link);
      });
      let script = null;
      const code = addScript({ src: "/vendor/katex.js", global: "katex" }, function (tag) { script = tag; });
      mathLoading = Promise.all([style, code]).then(function (both) {
        return both[1];
      }, function (err) {
        // Whichever of the two failed, both tags go, so the next try adds one of each and katex.js never runs twice.
        link.remove();
        if (script) script.remove();
        mathLoading = null;
        throw err;
      });
    }
    return mathLoading;
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

  // -- like Word (22.1, B3b) -------------------------------------------------------------------------------------
  //
  // The text stays the file's LaTeX; only how it looks changes. `readLatex` is the range reader (CodeMirror has no LaTeX
  // tree): commands with their arguments, environments, math, comments, \verb and verbatim-like environments (left raw),
  // the preamble. A state field turns what it read into decorations (line-crossing ones must come from a field), hiding
  // a construct's markup only where the selection does not touch it; `returnSpec` is Return by context.

  const HEADINGS = new Map([["chapter", 1], ["section", 2], ["subsection", 3], ["subsubsection", 4], ["paragraph", 5]]);
  const STYLES = new Map([["textbf", "latex-b"], ["textit", "latex-i"], ["emph", "latex-i"], ["underline", "latex-u"]]);
  const CITE_RE = /^(?:cite(?:p|t|alp)?|Citep|Citet|parencite|textcite|autocite)$/;   // ingest/latex.py's _CITE_RE
  const REFS = new Set(["ref", "eqref", "label"]);
  const LIST_ENVS = new Set(["itemize", "enumerate", "description"]);
  // Environments of running text, where Return makes a paragraph: these, and those the file declares by \newtheorem or
  // \declaretheorem. Any other is shown as written (tabular, tikzpicture, eqnarray ...): a blank line there can break it.
  const TEXT_ENVS = new Set(["document", "abstract", "quote", "quotation", "verse", "center", "flushleft", "flushright",
    "doublespace", "onehalfspace", "singlespace", "spacing", "minipage", "multicols", "appendices", "landscape", "titlepage",
    "frame", "proof", "theorem", "lemma", "proposition", "corollary", "definition", "remark", "example", "assumption",
    "conjecture", "claim", "hypothesis", "fact", "condition", "note"]);
  const MATH_ENVS = /^(?:equation|align|gather|multline)\*?$/;
  const VERBATIM_ENVS = /^(?:verbatim|Verbatim|BVerbatim|lstlisting|minted|comment|filecontents)\*?$/;
  const SPECIAL = /[\\%$]/g;

  // A blank line starts at the "\n" at `i`: only spaces, tabs or CR up to the next "\n".
  function blankAfter(text, i) {
    for (let j = i + 1; j < text.length; j++) {
      const c = text.charCodeAt(j);
      if (c === 10) return true;
      if (c !== 32 && c !== 9 && c !== 13) return false;
    }
    return false;
  }
  function skipSpaces(text, i) { while (text[i] === " " || text[i] === "\t") i += 1; return i; }

  // The group opened by text[i] ("{" or "["): the index after its close, or -1 when unclosed. `short` arguments end at
  // a blank line, as TeX's do; comments and escaped braces do not count.
  function skipGroup(text, i, short) {
    const square = text[i] === "[";
    let depth = 0;
    for (let j = i + 1; j < text.length; j++) {
      const c = text[j];
      if (c === "\\") { j += 1; continue; }
      if (c === "%") { const e = text.indexOf("\n", j); if (e < 0) return -1; j = e - 1; continue; }
      if (c === "\n" && short && blankAfter(text, j)) return -1;
      if (c === "{") depth += 1;
      else if (c === "}") { if (depth === 0) return square ? -1 : j + 1; depth -= 1; }
      else if (c === "]" && square && depth === 0) return j + 1;
    }
    return -1;
  }

  // The end of math whose content starts at `from`, closed by `close`: the index after the close, or -1 when a blank
  // line (which TeX refuses in math) or the end comes first.
  function mathEnd(text, from, close) {
    for (let j = from; j < text.length; j++) {
      const c = text[j];
      if (c === "\\") { if (text.startsWith(close, j)) return j + close.length; j += 1; continue; }
      if (c === "%") { const e = text.indexOf("\n", j); if (e < 0) return -1; j = e - 1; continue; }
      if (c === "\n" && blankAfter(text, j)) return -1;
      if (c === "$" && text.startsWith(close, j)) return j + close.length;
    }
    return -1;
  }

  // {cmds, maths, envs, raws, preamble, end}: positions are indices into `text`. A command is {name, role (heading,
  // style, cite, ref, item or other), from, nameTo, to, args: [{from, to, opt}]}; an item has its list and number. An
  // environment {name, role (list, text or raw), from, beginTo, endFrom, to}; math {from, to, display, src}; raw text
  // (comments, \verb, verbatim-like environments) {from, to, kind}. Nothing inside math or raw text is read.
  function readLatex(text) {
    const out = { cmds: [], maths: [], envs: [], raws: [], preamble: null, end: -1 }, theorems = new Set();
    const stack = [], n = text.length;
    let i = 0;
    while (i < n) {
      SPECIAL.lastIndex = i;
      const m = SPECIAL.exec(text);
      if (!m) break;
      i = m.index;
      const c = text[i];
      if (c === "%") { let e = text.indexOf("\n", i); if (e < 0) e = n; out.raws.push({ from: i, to: e, kind: "comment" }); i = e; continue; }
      if (c === "$") {
        const display = text[i + 1] === "$", open = display ? 2 : 1, e = mathEnd(text, i + open, display ? "$$" : "$");
        if (e > 0) { out.maths.push({ from: i, to: e, display: display, src: text.slice(i + open, e - open) }); i = e; } else i += open;
        continue;
      }
      const s = i + 1;
      let e = s;
      for (let k = text.charCodeAt(e); (k >= 65 && k <= 90) || (k >= 97 && k <= 122); k = text.charCodeAt(e)) e += 1;
      if (e === s) {   // a one-character command: \( and \[ open math; \\, \$, \%, \{ ... are text
        const d = text[s];
        if (d === "(" || d === "[") {
          const end = mathEnd(text, s + 1, d === "(" ? "\\)" : "\\]");
          if (end > 0) { out.maths.push({ from: i, to: end, display: d === "[", src: text.slice(s + 1, end - 2) }); i = end; continue; }
        }
        i = s + 1;
        continue;
      }
      const name = text.slice(s, e), star = text[e] === "*" ? 1 : 0;
      if (name === "verb") {   // \verb|...|: to the same delimiter on the same line
        const d = text[e + star], close = d && d !== "\n" && d !== "\r" ? text.indexOf(d, e + star + 1) : -1, eol = text.indexOf("\n", e + star + 1);
        if (close > 0 && (eol < 0 || close < eol)) { out.raws.push({ from: i, to: close + 1, kind: "verb" }); i = close + 1; continue; }
        i = e;
        continue;
      }
      if (name === "begin" || name === "end") {
        const at = skipSpaces(text, e), g = text[at] === "{" ? skipGroup(text, at, true) : -1;
        if (g < 0) { i = e; continue; }
        const env = text.slice(at + 1, g - 1).trim();
        if (name === "end") {
          let k = stack.length - 1;
          while (k >= 0 && stack[k].name !== env) k -= 1;
          if (k >= 0) { stack[k].endFrom = i; stack[k].to = g; stack.length = k; }
          if (env === "document" && out.end < 0) out.end = g;
          i = g;
          continue;
        }
        if (env === "document" && !out.preamble && !stack.length) out.preamble = { from: 0, to: i };
        if (MATH_ENVS.test(env)) {
          const end = mathEnd(text, g, "\\end{" + env + "}");
          if (end > 0) { out.maths.push({ from: i, to: end, display: true, src: text.slice(i, end) }); i = end; continue; }
        }
        if (VERBATIM_ENVS.test(env)) {
          const close = "\\end{" + env + "}", end = text.indexOf(close, g), to = end < 0 ? n : end + close.length;
          out.raws.push({ from: i, to: to, kind: "verbatim" });
          i = to;
          continue;
        }
        const base = env.replace(/\*$/, ""), prose = TEXT_ENVS.has(base) || theorems.has(base);
        const frame = { name: env, role: LIST_ENVS.has(env) ? "list" : prose ? "text" : "raw", from: i, beginTo: g, endFrom: n, to: n, items: 0 };
        stack.push(frame);
        out.envs.push(frame);
        i = g;
        continue;
      }
      const role = HEADINGS.has(name) ? "heading" : STYLES.has(name) ? "style" : CITE_RE.test(name) ? "cite" : REFS.has(name) ? "ref" : name === "item" ? "item" : "other";
      const cmd = { name: name, role: role, from: i, nameTo: e + star, to: e + star, args: [] };
      let at = cmd.nameTo;
      if (role === "other") {   // a command it does not know: the groups right after it are its arguments
        while (cmd.args.length < 9 && (text[at] === "{" || text[at] === "[")) {
          const g = skipGroup(text, at, false);
          if (g < 0) break;
          cmd.args.push({ from: at, to: g, opt: text[at] === "[" });
          at = g;
        }
      } else {
        at = skipSpaces(text, at);
        for (let opts = role === "cite" ? 2 : role === "heading" || role === "item" ? 1 : 0; opts > 0 && text[at] === "["; opts--) {
          const g = skipGroup(text, at, true);
          if (g < 0) break;
          cmd.args.push({ from: at, to: g, opt: true });
          at = skipSpaces(text, g);
        }
        const g = role !== "item" && text[at] === "{" ? skipGroup(text, at, true) : -1;
        if (g > 0) cmd.args.push({ from: at, to: g, opt: false });
        else if (role !== "item") cmd.role = "other";   // no argument: shown as written
        if (role === "item") {
          for (let k = stack.length - 1; k >= 0; k--) if (stack[k].role === "list") { cmd.list = stack[k]; cmd.n = ++stack[k].items; break; }
          cmd.depth = stack.filter((f) => f.role === "list").length;
        }
      }
      if (cmd.args.length) cmd.to = cmd.args[cmd.args.length - 1].to;
      if (name === "newtheorem" || name === "declaretheorem") {   // \newtheorem{lemma}[theorem]{Lemma}: lemma is text
        const a = cmd.args.find((x) => !x.opt);
        if (a) theorems.add(text.slice(a.from + 1, a.to - 1).trim());
      }
      out.cmds.push(cmd);
      i = cmd.role === "cite" || cmd.role === "ref" ? cmd.to : cmd.nameTo;   // the arguments' text is read as text
    }
    return out;
  }

  // What KaTeX is given: \label (which it does not know) dropped, multline set as gather.
  function forKatex(src) { return src.replace(/\\label\s*\{[^{}]*\}/g, "").replace(/\\(begin|end)\{multline(\*?)\}/g, "\\$1{gather$2}"); }
  // A formula typeset by KaTeX (trust off: no \href or \url reaches the network); one it cannot typeset is shown as
  // written, in the mono face, uncoloured -- the PDF is the judge (22.1). Never throws. What trust off refuses (\href,
  // \url, \includegraphics ...) KaTeX draws in its error colour instead of throwing: that colour, set to one no formula
  // asks for, counts as a failure too.
  const KATEX_ERROR = "#0e0e0f";
  function mathDOM(src, display, whole) {
    const box = mk("span", display ? "latex-math latex-math-display" : "latex-math");
    try {
      const html = window.katex.renderToString(forKatex(src), { displayMode: display, throwOnError: true, trust: false, strict: "ignore", errorColor: KATEX_ERROR });
      if (html.includes(KATEX_ERROR)) throw new Error("refused");
      box.innerHTML = html;
    } catch (err) {
      box.className = "latex-math-raw";
      box.textContent = whole;
    }
    return box;
  }

  // The preamble is folded from the file's start to the end of the line before \begin{document}: 0 when nothing is.
  function preambleEnd(doc, read) {
    if (!read.preamble || !read.preamble.to) return 0;
    const line = doc.lineAt(read.preamble.to);
    return line.from > 0 && doc.sliceString(0, line.from).trim() ? line.from - 1 : 0;
  }
  // Where the body starts, the line after \begin{document}'s: the editor's first cursor, and where folding the preamble
  // moves one that was in it. 0 when nothing is folded.
  function bodyStart(doc, read) {
    if (!preambleEnd(doc, read)) return 0;
    const line = doc.lineAt(read.preamble.to);
    return line.number < doc.lines ? doc.line(line.number + 1).from : line.to;
  }
  // The selection is in the preamble when a cursor is in it, its start included, or a selection covers any of it: then
  // the preamble is open, so nothing typed or deleted lands on lines that cannot be seen (22.1).
  function inPreamble(ranges, pre) { return pre > 0 && ranges.some((r) => r.from <= pre); }

  // Constructs read before (mapped through the change) and now, compared from both ends: the ranges of those between,
  // which differ, are pushed to `spans`.
  function differ(was, now, same, parts, map, spans) {
    const n = Math.min(was.length, now.length);
    let a = 0, b = 0;
    while (a < n && same(was[a], now[a])) a += 1;
    while (b < n - a && same(was[was.length - 1 - b], now[now.length - 1 - b])) b += 1;
    for (let k = a; k < was.length - b; k++) parts(was[k], map).forEach((p) => spans.push([Math.min(p[0], p[1]), Math.max(p[0], p[1])]));
    for (let k = a; k < now.length - b; k++) parts(now[k], (x) => x).forEach((p) => spans.push(p));
  }
  function sameCmd(a, b, map) {
    if (a.role !== b.role || a.name !== b.name || a.n !== b.n || a.depth !== b.depth || (a.list && a.list.name) !== (b.list && b.list.name) ||
      map(a.from) !== b.from || map(a.to) !== b.to || map(a.nameTo) !== b.nameTo || a.args.length !== b.args.length) return false;
    return a.args.every((x, k) => map(x.from) === b.args[k].from && map(x.to) === b.args[k].to);
  }
  const envParts = (e, map) => [[map(e.from), map(e.beginTo)], [map(e.endFrom), map(e.to)]];
  const wholeParts = (x, map) => [[map(x.from), map(x.to)]];

  // Where the decorations must be drawn again after `tr` (22.1): around each change, every construct read differently
  // from before, and every one the selection now touches or leaves (none while `live` is off); widened to whole lines
  // and to every construct it reaches until none crosses an edge. Null when nothing is to be drawn again.
  function redraw(old, read, math, tr, live) {
    const doc = tr.state.doc, ch = tr.changes, map = (x) => ch.mapPos(x), sel = tr.state.selection.ranges, spans = [];
    const was = tr.docChanged ? tr.startState.selection.map(ch).ranges : tr.startState.selection.ranges;
    const pre = preambleEnd(doc, read);
    if (tr.docChanged) {
      ch.iterChangedRanges((fa, ta, fb, tb) => spans.push([fb, tb]));
      differ(old.cmds, read.cmds, (a, b) => sameCmd(a, b, map), wholeParts, map, spans);
      differ(old.envs, read.envs, (a, b) => map(a.from) === b.from && map(a.beginTo) === b.beginTo && map(a.endFrom) === b.endFrom && map(a.to) === b.to, envParts, map, spans);
      differ(old.maths, read.maths, (a, b) => map(a.from) === b.from && map(a.to) === b.to && a.display === b.display, wholeParts, map, spans);
      const preWas = map(preambleEnd(tr.startState.doc, old));
      if (preWas !== pre) spans.push([0, Math.max(pre, preWas)]);
    }
    if (inPreamble(was, pre) !== inPreamble(sel, pre)) spans.push([0, pre]);
    const touch = (s, from, to) => s.some((r) => r.from <= to && r.to >= from);
    const flip = (from, to) => { if (live && touch(was, from, to) !== touch(sel, from, to)) spans.push([from, to]); };
    for (const c of read.cmds) if (c.role !== "other" && c.from >= pre) flip(c.from, c.to);
    if (math) for (const f of read.maths) if (f.from >= pre) flip(f.from, f.to);
    if (!spans.length) return null;
    const lines = (list) => {
      const out = [];
      list.map((s) => [doc.lineAt(s[0]).from, doc.lineAt(s[1]).to]).sort((x, y) => x[0] - y[0]).forEach((s) => {
        const last = out[out.length - 1];
        if (last && s[0] <= last[1] + 1) last[1] = Math.max(last[1], s[1]); else out.push(s);
      });
      return out;
    };
    let w = lines(spans);
    for (let pass = 0; pass < 8; pass++) {
      let grew = false;
      const reach = (from, to) => {
        for (const s of w) if (from <= s[1] && to >= s[0] && (from < s[0] || to > s[1])) { s[0] = Math.min(s[0], from); s[1] = Math.max(s[1], to); grew = true; }
      };
      if (pre > 0) reach(0, pre);
      for (const c of read.cmds) reach(c.from, c.to);
      for (const e of read.envs) { reach(e.from, e.beginTo); reach(e.endFrom, e.to); }
      for (const f of read.maths) reach(f.from, f.to);
      if (!grew) return w;
      w = lines(w);
    }
    return [[0, doc.length]];
  }

  let wx = null;
  function wordField(CM) {
    if (wx && wx.CM === CM) return wx;
    // `live` (on unless the editor turns it off): whether the selection shows the source of what it touches. The editor
    // loads a file with it off and turns it on at the first focus, so the cursor CodeMirror puts at 0 (or the body's
    // start) before anyone has clicked never shows a file's first heading as source.
    const mathOn = CM.StateEffect.define(), preamble = CM.StateEffect.define(), live = CM.StateEffect.define(), D = CM.Decoration;
    class MathWidget extends CM.WidgetType {
      constructor(src, display, whole) { super(); this.src = src; this.display = display; this.whole = whole; }
      eq(other) { return other.whole === this.whole; }
      toDOM() { return mathDOM(this.src, this.display, this.whole); }
      ignoreEvent() { return false; }   // a click places the cursor there, which shows the source
    }
    class Chip extends CM.WidgetType {
      constructor(text, kind) { super(); this.text = text; this.kind = kind; }
      eq(other) { return other.text === this.text && other.kind === this.kind; }
      toDOM() { return mk("span", "latex-chip latex-chip-" + this.kind, this.text); }
      ignoreEvent() { return false; }
    }
    class Item extends CM.WidgetType {
      constructor(n) { super(); this.n = n; }
      eq(other) { return other.n === this.n; }
      toDOM() { return mk("span", this.n ? "latex-item" : "latex-item latex-bullet", this.n ? this.n + "." : ""); }
    }
    // 导言区 is a button (12.11): Tab reaches it, Return and Space press it. Folding it moves a cursor that was in the
    // preamble to the body, so the fold always closes when asked. Its state is said in words, with no chevron (22.6).
    class Preamble extends CM.WidgetType {
      constructor(open) { super(); this.open = open; }
      eq(other) { return other.open === this.open; }
      toDOM(view) {
        const b = mk("button", "latex-preamble", this.open ? t("收起导言区") : t("导言区（已折叠，点开）"));
        b.setAttribute("type", "button");
        b.setAttribute("aria-expanded", String(this.open));
        b.addEventListener("mousedown", (event) => event.preventDefault());
        b.addEventListener("click", () => {
          const value = view.state.field(field, false);
          if (!value) return;
          const spec = { effects: preamble.of(!this.open) };
          if (this.open && inPreamble(view.state.selection.ranges, preambleEnd(view.state.doc, value.read))) spec.selection = { anchor: bodyStart(view.state.doc, value.read) };
          view.dispatch(spec);
          view.focus();
        });
        return b;
      }
      ignoreEvent() { return true; }
    }
    const command = D.mark({ class: "latex-cmd" }), hide = D.replace({});
    const marks = new Map();
    const mark = (cls) => { if (!marks.has(cls)) marks.set(cls, D.mark({ class: cls })); return marks.get(cls); };
    const lineMarks = [1, 2, 3, 4].map((k) => D.line({ class: "latex-li latex-li-" + k }));

    // The decorations of the constructs that meet `within` ([from, to], closed: none crosses its edges), or of all.
    function decorate(read, math, open, state, within, on) {
      const doc = state.doc, sel = state.selection.ranges, out = [];
      const touch = (from, to) => on !== false && sel.some((r) => r.from <= to && r.to >= from);
      const meets = within ? (from, to) => from <= within[1] && to >= within[0] : () => true;
      const pre = preambleEnd(doc, read);
      if (pre > 0 && meets(0, pre)) {
        out.push(open || inPreamble(sel, pre) ? D.widget({ widget: new Preamble(true), block: true, side: -1 }).range(0)
          : D.replace({ widget: new Preamble(false), block: true }).range(0, pre));
      }
      const lines = new Set();
      for (const c of read.cmds) {
        if (c.from < pre || !meets(c.from, c.to)) continue;   // the preamble is source, folded or open
        const arg = c.args.length ? c.args[c.args.length - 1] : null;
        if (c.role === "item" && c.list) {
          const line = doc.lineAt(c.from);
          if (!lines.has(line.from)) { lines.add(line.from); out.push(lineMarks[Math.min(c.depth, 4) - 1].range(line.from)); }
        }
        if (c.role === "other" || touch(c.from, c.to)) { out.push(command.range(c.from, c.nameTo)); continue; }
        if (c.role === "heading" || c.role === "style") {
          if (arg.to - arg.from < 3) { out.push(command.range(c.from, c.nameTo)); continue; }
          out.push(hide.range(c.from, arg.from + 1),
            mark(c.role === "heading" ? "latex-h latex-h" + HEADINGS.get(c.name) : STYLES.get(c.name)).range(arg.from + 1, arg.to - 1),
            hide.range(arg.to - 1, arg.to));
        } else if (c.role === "cite" || c.role === "ref") {
          const keys = doc.sliceString(arg.from + 1, arg.to - 1).split(",").map((k) => k.trim()).filter(Boolean).join(", ");
          out.push(D.replace({ widget: new Chip(keys, c.role) }).range(c.from, c.to));
        } else if (c.list && c.args.length === 0) {
          out.push(D.replace({ widget: new Item(c.list.name === "enumerate" ? c.n : 0) }).range(c.from, c.nameTo + (doc.sliceString(c.nameTo, c.nameTo + 1) === " " ? 1 : 0)));
        } else out.push(command.range(c.from, c.nameTo));
      }
      for (const env of read.envs) {
        if (env.from < pre) continue;
        if (meets(env.from, env.beginTo)) out.push(command.range(env.from, env.beginTo));
        if (env.endFrom < env.to && meets(env.endFrom, env.to)) out.push(command.range(env.endFrom, env.to));
      }
      if (math) {
        for (const f of read.maths) {
          if (f.from >= pre && meets(f.from, f.to) && !touch(f.from, f.to)) out.push(D.replace({ widget: new MathWidget(f.src, f.display, doc.sliceString(f.from, f.to)) }).range(f.from, f.to));
        }
      }
      return out;
    }
    // On each change the text is read again whole (3 ms on a 2 MB file, the save's limit); the decorations are mapped
    // and drawn again only where `redraw` says, so a key costs the same in a long file as in a short one. A move of the
    // selection that shows and hides nothing new keeps them as they are.
    const field = CM.StateField.define({
      create: (state) => {
        const read = readLatex(state.doc.toString()), math = typeof window !== "undefined" && !!window.katex;
        return { read: read, math: math, open: false, live: true, deco: D.set(decorate(read, math, false, state), true) };
      },
      update(value, tr) {
        let math = value.math, open = value.open, on = value.live;
        for (const e of tr.effects) { if (e.is(mathOn)) math = true; if (e.is(preamble)) open = e.value; if (e.is(live)) on = e.value; }
        if (!tr.docChanged && !tr.selection && math === value.math && open === value.open && on === value.live) return value;
        const read = tr.docChanged ? readLatex(tr.state.doc.toString()) : value.read;
        if (math !== value.math || open !== value.open || on !== value.live) {
          return { read: read, math: math, open: open, live: on, deco: D.set(decorate(read, math, open, tr.state, null, on), true) };
        }
        const spans = redraw(value.read, read, math, tr, on);
        let deco = tr.docChanged ? value.deco.map(tr.changes) : value.deco;
        if (!spans && !tr.docChanged) return value;
        for (const s of spans || []) {
          deco = deco.update({ filterFrom: s[0], filterTo: s[1], filter: (from, to) => !(from <= s[1] && to >= s[0]), add: decorate(read, math, open, tr.state, s, on), sort: true });
        }
        return { read: read, math: math, open: open, live: on, deco: deco };
      },
      provide: (f) => CM.EditorView.decorations.from(f, (v) => v.deco),
    });
    wx = { CM: CM, field: field, mathOn: mathOn, preamble: preamble, live: live };
    return wx;
  }
  // Like Word's extensions, which 显示源码 takes out (a compartment): the field, and the class that sets the text face.
  function wordExtensions(CM) {
    const w = wordField(CM);
    const focus = CM.EditorView.focusChangeEffect;   // absent only in a test's stand-in view
    return [w.field, CM.EditorView.contentAttributes.of({ class: "latex-word" })]
      .concat(focus ? [focus.of((state, focusing) => (focusing && !state.field(w.field).live ? w.live.of(true) : null))] : []);
  }

  // The innermost place `pos` is in: {kind: math, raw, arg or env, x: what it is}, or null in running text at the top.
  function contextAt(read, pos) {
    let best = null;
    const consider = (from, to, kind, x) => {
      if (from <= pos && pos <= to && (!best || from > best.from || (from === best.from && to < best.to))) best = { from: from, to: to, kind: kind, x: x };
    };
    if (read.preamble) consider(0, read.preamble.to, "raw", null);
    if (read.end >= 0) consider(read.end, Infinity, "raw", null);
    read.envs.forEach((e) => consider(e.beginTo, e.endFrom, "env", e));
    read.raws.forEach((r) => consider(r.from + 1, r.to, "raw", r));
    read.maths.forEach((f) => consider(f.from + 1, f.to - 1, "math", f));
    read.cmds.forEach((c) => c.args.forEach((a) => consider(a.from + 1, a.to - 1, "arg", { cmd: c, arg: a })));
    return best;
  }

  // Return by where the cursor is (22.1): running text, a new paragraph; a heading's argument, out of the braces and a
  // new paragraph after the heading; another command's argument, a plain newline; a list, a new \item, and an empty
  // item is removed and the cursor leaves the list; math, raw text, an environment shown as written or `read` null
  // (显示源码, ⇧Return), a plain newline. Every line break is the file's own (state.lineBreak, 22.1).
  function returnSpec(state, read) {
    const sel = state.selection.main, pos = sel.from, br = state.lineBreak, doc = state.doc;
    const make = (specs, at, back) => {
      const changes = state.changes(specs);
      return { changes: changes, selection: { anchor: changes.mapPos(at, 1) - (back || 0) }, scrollIntoView: true, userEvent: "input" };
    };
    const plain = () => make({ from: sel.from, to: sel.to, insert: br }, sel.to);
    // A new, empty paragraph at the end of `at`'s line, set apart by a blank line from the one after it too.
    const after = (deletes, at) => {
      const line = doc.lineAt(at), next = line.number < doc.lines ? doc.line(line.number + 1).text.trim() : "";
      return next ? make(deletes.concat([{ from: at, insert: br + br + br }]), at, 1) : make(deletes.concat([{ from: at, insert: br + br }]), at);
    };
    if (!read) return plain();
    const ctx = contextAt(read, pos);
    const paragraph = () => /^\s*$/.test(doc.sliceString(doc.lineAt(pos).from, pos)) ? plain() : make({ from: sel.from, to: sel.to, insert: br + br }, sel.to);
    if (!ctx) return paragraph();
    if (ctx.kind === "arg") {
      const c = ctx.x.cmd;
      if (c.role !== "heading" || ctx.x.arg.opt || sel.to > c.to) return plain();
      const line = doc.lineAt(c.to), rest = doc.sliceString(c.to, line.to);
      const at = /^\s*(\\label\s*\{[^{}]*\}\s*)*$/.test(rest) ? line.to : c.to;   // after a \label that follows it
      return after([{ from: sel.from, to: sel.to }], at);
    }
    if (ctx.kind !== "env" || ctx.x.role === "raw") return plain();
    if (ctx.x.role === "text") return paragraph();
    const env = ctx.x, items = read.cmds.filter((c) => c.role === "item" && c.list === env);
    const item = items.filter((c) => c.from <= pos).pop();
    if (!item || pos < item.to) return plain();
    const next = items.find((c) => c.from > item.from), end = next ? next.from : env.endFrom;
    if (!doc.sliceString(item.to, end).trim()) {
      if (!sel.empty || env.endFrom >= doc.length) return plain();   // a list never closed: nowhere to leave to
      const line = doc.lineAt(item.from), endLine = doc.lineAt(end), whole = endLine.from > item.from;
      const delFrom = whole && !doc.sliceString(line.from, item.from).trim() ? line.from : item.from;
      return after([{ from: delFrom, to: whole ? endLine.from : end }], env.to);
    }
    const indent = /^[ \t]*/.exec(doc.lineAt(item.from).text)[0];
    return make({ from: sel.from, to: sel.to, insert: br + indent + "\\item " }, sel.to);
  }

  // Return and ⇧Return: never while an input method composes (its Return picks a candidate), never in a read-only file.
  function onReturn(CM, plain) {
    return (view) => {
      if (view.composing || view.state.readOnly) return false;
      const value = plain ? null : view.state.field(wordField(CM).field, false);
      view.dispatch(returnSpec(view.state, value ? value.read : null));
      return true;
    };
  }
  function returnKeys(CM) { return CM.keymap.of([{ key: "Enter", run: onReturn(CM, false) }, { key: "Shift-Enter", run: onReturn(CM, true) }]); }

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

  // `at`: {link} -- open the draft at that passage (以它为依据的段落's 「在编辑器里打开」); {line} -- at that line (an
  // export error's file:line, 22.4).
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
    // the last 关联/撤回关联/是这段 (18.3), named by what it takes back and set with the status that reports it, apart
    // from 撤销这次保存
    const undoLink = mkButton(t("撤销这次关联"), "ws-text-link latex-act-undo hidden", () => undoAct(ed));
    const exported = exporter(file.path, () => saveFirst(me));   // 22.4: 导出 PDF, quiet
    const source = mkButton(t("显示源码"), "ws-text-link latex-source", () => toggleSource(me));   // 22.1: like Word off for the file
    const said = mk("span", "latex-act");
    said.append(status, undoLink);
    tools.append(save, undo, exported.button, source, said);
    head.append(mk("div", "node-path mono", file.path), tools);
    const readOnly = mk("p", "node-sub latex-read-only" + (file.read_only ? "" : " hidden"), file.read_only_message || "");
    const notice = mk("div", "latex-notice hidden"), box = mk("div", "latex-editor"), side = mk("aside", "latex-side");
    const body = mk("div", "latex-body");
    body.append(box, side);
    page.append(backBar(), head, exported.block, readOnly, notice, body);
    host.replaceChildren(page);
    const me = { path: file.path, CM: CM, ui: { save: save, undo: undo, status: status, notice: notice, box: box, side: side, undoLink: undoLink, source: source },
      lastKey: 0, busy: false, lastSave: null, afterComposing: null, target: at || null, panel: null };
    me.ui.linkButton = mkButton(t("关联依据文件"), "btn btn-small latex-link-button hidden", () => openLinkPanel(me));
    me.ui.linkButton.addEventListener("mousedown", (event) => event.preventDefault());   // the selection stays
    box.appendChild(me.ui.linkButton);
    ed = me;
    load(me, file);
    mathWhenShown(me);
    if (at && at.line) { me.target = null; goToLine(me, at.line); }   // an export error's file:line (22.4)
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
    me.wordComp = new CM.Compartment();
    me.ui.source.setAttribute("aria-pressed", String(sourceShown.has(me.path)));
    me.revision = file.revision;
    me.readOnly = !!file.read_only;
    me.sep = file.line_ending === "crlf" ? "\r\n" : "\n";
    if (file.mains) me.mains = file.mains;
    if (file.links) me.links = new Map(file.links.filter((l) => l.path).map((l) => [includeKey(l.command, l.args), l]));
    const extensions = [
      CM.history(), CM.drawSelection(), CM.highlightSpecialChars(), CM.EditorView.lineWrapping,
      returnKeys(CM), CM.keymap.of([].concat(CM.defaultKeymap, CM.historyKeymap)),
      CM.StreamLanguage.define(CM.stex), CM.syntaxHighlighting(highlight(CM)), includeLinks(CM, me),
      CM.EditorView.contentAttributes.of({ "aria-label": t("LaTeX 源码"), spellcheck: "false" }),
      CM.EditorView.updateListener.of((update) => { changed(me, update); if (update.selectionSet || update.docChanged || update.geometryChanged) placeLinkButton(me); }),
      passageField(CM).field, me.wordComp.of(sourceShown.has(me.path) ? [] : wordExtensions(CM)),
      CM.EditorView.domEventHandlers({ click: (event) => {
        const hit = event.target && event.target.closest && event.target.closest("[data-passage]");
        if (hit) openPassage(me, hit.getAttribute("data-passage"));
      } }),
    ];
    // A read-only file may mix endings: CodeMirror's own splitting shows it; nothing is ever sent.
    if (me.readOnly) extensions.push(CM.EditorState.readOnly.of(true));
    else extensions.push(CM.EditorState.lineSeparator.of(me.sep), breakFilter(CM, me.sep));
    let state = CM.EditorState.create({ doc: file.text, extensions: extensions });
    // the first cursor is where the body starts: one at 0 would sit before the folded preamble, typing into it unseen
    const first = bodyStart(state.doc, readLatex(state.doc.toString()));
    if (first) state = state.update({ selection: { anchor: first } }).state;
    // like Word, nothing shows its source until the editor is clicked or reached by keyboard (a reload keeps the focus)
    const focused = !!(me.view && me.view.hasFocus);
    if (!sourceShown.has(me.path) && !focused) state = state.update({ effects: wordField(CM).live.of(false) }).state;
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

  // 显示源码 (22.1): like Word off, or on again, for this file while Research OS runs. KaTeX is loaded the first time a
  // file is shown like Word; until it is in, and if it fails, formulas stay as written (22.0.1).
  const sourceShown = new Set();
  function toggleSource(me) {
    if (ed !== me || !me.view) return;
    if (sourceShown.has(me.path)) sourceShown.delete(me.path); else sourceShown.add(me.path);
    const on = sourceShown.has(me.path);
    me.ui.source.setAttribute("aria-pressed", String(on));
    me.view.dispatch({ effects: me.wordComp.reconfigure(on ? [] : wordExtensions(me.CM)) });
    if (!on && !me.view.hasFocus) me.view.dispatch({ effects: wordField(me.CM).live.of(false) });   // as a file loads
    mathWhenShown(me);
  }
  function mathWhenShown(me) {
    if (sourceShown.has(me.path) || window.katex || typeof document === "undefined") return;
    loadMath().then(() => { if (ed === me && me.view) me.view.dispatch({ effects: wordField(me.CM).mathOn.of(true) }); }, () => {});
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

  // The save now running is kept as `me.saving`: an export asked meanwhile waits for its answer (22.4).
  function saveNow() {
    const me = ed, idle = !!me && !me.busy, done = saveOnce();
    if (idle && me.busy) me.saving = done;
    return done;
  }
  async function saveOnce() {
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
    if (lastShown && lastShown.path === me.path) lastShown = null;   // an export's answer is about the text before this save
    clearNotice(me);   // a conflict's notice and folded text are over once a save goes through
    const emptied = checkPassages(me);
    if (r.changed && r.previous) me.lastSave = { revision: r.revision, previous: r.previous };
    me.ui.undo.classList.toggle("hidden", !me.lastSave);
    // a cut passage pasted elsewhere is found there by the server; the save stays busy until its answer is in, so no
    // passage act runs on a list about to be replaced
    if (emptied) { await loadPassages(me, true); if (ed !== me) return; }
    me.busy = false; me.ui.save.disabled = false;
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
      CM.Decoration.mark({ class: "latex-passage" + (p.state === "found" && !p.pending ? "" : " latex-wait"), attributes: { "data-passage": p.key } }).range(p.from, p.to),
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
    let data;
    try {
      data = await apiGet("/api/latex/evidence?path=" + encodeURIComponent(me.path));
    } catch (err) {
      if (ed === me) { me.evidence = { failed: err }; renderSide(me); }
      return;
    }
    if (ed !== me) return;
    if (data.revision !== me.revision) { if (!retried && !dirty()) await loadPassages(me, keepLocal, true); return; }
    // read when the answer is in: text typed while the request was out has moved the local passages since
    const local = new Map();
    if (keepLocal) passagesOf(me).forEach((p) => { if (p.local) p.links.forEach((l) => local.set(l.link, { last: l.last, p: p })); });
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
    list.forEach((p) => { p.pending = p.links.every((l) => l.pending); });   // 22.5: 待批准 backs nothing
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
  // A passage whose range is now empty may have been cut and pasted elsewhere: true is returned, and the caller has
  // the server find it again in the saved text (found, 待复核 or 「段落已被删掉」 is the server's answer). Until that
  // answer applies (it may fail, or the file may have changed again) the passage reads 「段落已被删掉」, never found;
  // it is not local, so the server's answer replaces it. Nothing is written: only a click writes the ledger.
  function checkPassages(me) {
    const doc = me.view.state.doc;
    let moved = false, emptied = false;
    const list = passagesOf(me).map((p) => {
      if (p.opened !== "found" || p.from == null) return p;
      if (p.to <= p.from) { emptied = true; return Object.assign({}, p, { state: "deleted", local: false, candidates: [] }); }
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
    me.ui.undoLink.textContent = undoActWords(action, done);
    me.ui.undoLink.classList.toggle("hidden", !me.lastAct.length);
    if (failed) setStatus(me.ui.status, "err", t("关联没有写入"), failed);
    else setStatus(me.ui.status, "ok", done);
    if (wrote.length) await loadPassages(me, true);
    return !failed;
  }

  // A reanchor's undo is named after what its status said (「是这段」 or 「仍然成立」), never 「确认」, the link's word (22.6).
  function undoActWords(action, done) {
    if (action === "link") return t("撤销这次关联");
    if (action === "withdraw") return t("撤销这次撤回");
    return done === t("已记录：仍然成立") ? t("撤销“仍然成立”") : t("撤销“是这段”");
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
    const side = me.ui.side, chosen = [], picked = mk("ul", "latex-files latex-picked"), results = mk("div", "latex-results");
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
      try { data = await apiGet("/api/latex/files?q=" + encodeURIComponent(search.value || "") + "&path=" + encodeURIComponent(me.path)); } catch (err) {
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
    if (p.pending) head.append(mk("span", "status-label tone-ochre", t("待批准")));
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
    const backed = list.filter((p) => !p.pending), waiting = backed.filter((p) => p.state !== "found");
    const pending = list.filter((p) => p.pending);
    if (list.length) {
      const counts = mk("p", "latex-counts", backed.length ? t("{n} 段有依据文件", { n: backed.length }) : "");
      let first = !backed.length;   // a space only between two counts
      const add = (label) => { counts.append(...(first ? [label] : [" ", label])); first = false; };
      if (waiting.length) add(mk("span", "status-label tone-ochre", t("{n} 段待复核", { n: waiting.length })));
      if (pending.length) add(mk("span", "status-label tone-ochre", t("{n} 段待批准", { n: pending.length })));
      parts.push(counts);
      waiting.concat(pending).forEach((p) => parts.push(mkButton(Array.from(words(p.quote)).slice(0, 40).join(""), "ws-text-link latex-waiting", () => openPassage(me, p.key))));
    } else if (me.mayLink) parts.push(mk("p", "node-sub", t("选中一段文字，可以关联依据文件。")));
    side.replaceChildren(...parts);
  }

  // -- export to PDF (22.4, B2b) ------------------------------------------------------------------------------------

  const ENGINE_NAMES = { pdflatex: "pdfLaTeX", xelatex: "XeLaTeX", lualatex: "LuaLaTeX" };
  // The last export's answer, {path, r}: the draft's page drawn again (the scan after an export redraws it) shows it
  // again, until the draft is saved, another draft's export is drawn or its PDF is no longer the last one there.
  let lastShown = null;
  // The draft whose export runs, {path, using}, and the exporters drawn for each draft: a page drawn again during the run
  // shows 正在导出… with 停止, and the answer reaches the page now shown, not a block no longer on screen.
  let exporting = null;
  const drawn = {};

  // 22.4 "Before running": the editor's unsaved text is saved first; a save under way is waited for; a save refused
  // or overtaken by typing stops the export.
  async function saveFirst(me) {
    if (ed === me && me.busy && me.saving) await me.saving;
    if (ed === me && dirty() && !me.busy) await saveNow();
    return ed === me && !me.busy && !dirty();
  }

  function goToLine(me, n) {
    const doc = me.view.state.doc, line = doc.line(Math.min(Math.max(1, n), doc.lines));
    me.view.dispatch({ selection: { anchor: line.from }, scrollIntoView: true });
    if (typeof me.view.focus === "function") me.view.focus();
  }
  // An error's file:line: the open draft moves to the line; another file opens in the editor there.
  function openAt(path, n) { if (ed && ed.path === path && ed.view) goToLine(ed, n); else open(path, { line: n }); }

  // 「导出 PDF」 for the draft `path` (22.4, 22.7): `button`, the quiet button, for the editor's toolbar or a .tex page's
  // actions, and `block`: the settings line (主文件, the engine as a quiet choice, 导出的 PDF), the running state with
  // 「停止」, and the result -- 已导出 with 打开 PDF and 在 Finder 中显示, the question of the main file or of 替换 / 另存为, or
  // 导出没有成功 as an error with TeX's file:line errors (a link into the editor where the server's validator passed the path)
  // and the log under 详情; characters the fonts lack as a warning. `before` (the editor's save) runs first.
  // `list` (正文, 22.10): one of several exporters on one page, so another draft's does not drop this one's 已导出.
  function exporter(path, before, list) {
    const button = mkButton(t("导出 PDF"), "btn btn-small", () => run({}));
    const settingsLine = mk("div", "latex-export-line node-sub hidden"), result = mk("div", "latex-export-result");
    const block = mk("div", "latex-export");
    block.append(settingsLine, result);
    const engine = mk("select", "ws-input latex-engine");
    engine.setAttribute("aria-label", t("排版引擎"));
    Object.keys(ENGINE_NAMES).forEach((k) => { const o = mk("option", null, ENGINE_NAMES[k]); o.value = k; engine.appendChild(o); });
    let kept = null, chosen = null;   // kept: the engine the server would use; chosen: the person's change
    engine.addEventListener("change", () => { chosen = engine.value; });
    const row = (...kids) => { const r = mk("div", "latex-export-line"); r.append(...kids); return r; };
    const opener = (label, pdf, reveal, status) => mkButton(label, "ws-text-link", async () => {
      try { await apiPost("/api/open", { path: pdf, reveal: reveal }); setStatus(status, "", ""); } catch (err) {
        setStatus(status, "err", reveal ? t("无法在 Finder 中显示") : t("无法打开"), err);
      }
    });
    const settings = () => {
      const shown = lastShown;
      return apiGet("/api/latex/export?path=" + encodeURIComponent(path) + "&project=" + encodeURIComponent(state.currentPath)).then((seen) => {
      const parts = [];
      // a PDF exported here that is gone or no longer the last one: its 已导出 and 打开 PDF are not offered again
      if (shown && shown === lastShown && shown.path === path && shown.r.exported && !(exporting && exporting.path === path)
          && !(seen.last && seen.last.pdf === shown.r.pdf)) { lastShown = null; result.replaceChildren(); }
      if (seen.main) parts.push(mk("span", null, t("主文件")), mk("span", "mono", seen.main));
      if (seen.engine) {
        kept = seen.engine;
        if (!chosen) engine.value = seen.engine;
        parts.push(mk("span", null, t("排版引擎")), engine);
      }
      if (seen.last) {
        const status = mk("span", "notice-status");
        parts.push(mk("span", null, t("导出的 PDF")), mk("span", "mono", seen.last.pdf), opener(t("打开 PDF"), seen.last.pdf, false, status), status);
      }
      settingsLine.replaceChildren(...parts);
      settingsLine.classList.toggle("hidden", !parts.length);
    }, () => settingsLine.classList.add("hidden"));
    };

    function show(r) {
      const parts = [], status = mk("span", "notice-status");
      if (r.ask === "main") {
        parts.push(mk("p", "node-sub", r.message ? t("找不到包含这个文件的主文件（带 {command} 的 .tex）。", { command: "\\documentclass" })
          : t("有几个主文件包含这个文件。导出哪一个？")));
        parts.push(row(...(r.mains || []).map((m) => mkButton(m, "ws-text-link mono", () => run({ main: m })))));
      } else if (r.ask === "replace") {
        parts.push(mk("p", "node-sub", t("{file} 不是上次导出的文件，要替换它吗？", { file: r.file })));
        parts.push(row(mkButton(t("替换"), "btn btn-small", () => run({ main: r.main, pdf: "replace" })),
          mkButton(t("另存为 {file}", { file: r.save_as }), "btn btn-small", () => run({ main: r.main, pdf: "save_as" }))));
      } else if (r.exported) {
        parts.push(row(mk("span", null, t("已导出 {pdf}", { pdf: r.pdf })), opener(t("打开 PDF"), r.pdf, false, status),
          opener(t("在 Finder 中显示"), r.pdf, true, status), status));
      } else {
        const stopped = r.stopped === "stopped";
        parts.push(mk("p", stopped ? "node-sub" : "latex-export-failed",
          stopped ? t("已停止导出。") : r.stopped === "timeout" && r.message ? r.message : t("导出没有成功")));
        const errors = mk("ul", "latex-export-errors");
        (r.errors || []).forEach((e) => {
          const li = mk("li", null), where = e.file + ":" + e.line;
          li.append(e.path ? mkButton(where, "ws-text-link mono", () => openAt(e.path, e.line)) : mk("span", "mono", where), " ", mk("span", null, e.text));
          errors.appendChild(li);
        });
        if (errors.children.length) parts.push(errors);
      }
      if (r.missing_message) parts.push(mk("p", "latex-export-warn", r.missing_message));
      (r.notes || []).forEach((note) => parts.push(mk("p", "node-sub", note)));
      if (r.log && !r.exported && !r.ask) {
        const fold = mk("details", "dc-fold"), pre = mk("pre", "latex-held");
        pre.textContent = r.log;
        fold.append(mk("summary", null, t("详情")), pre);
        parts.push(fold);
      }
      result.replaceChildren(...parts);
    }
    function showRunning(using) {
      button.disabled = true;
      const stop = mkButton(t("停止"), "ws-text-link", () => { stop.disabled = true; apiPost("/api/latex/export/stop", {}).catch(() => { stop.disabled = false; }); });
      result.replaceChildren(row(mk("span", "notice-status", using ? t("正在用 {engine} 导出…", { engine: ENGINE_NAMES[using] }) : t("正在导出…")), stop));
    }
    // A refusal before anything ran (no TeX, no sandbox, a file only in iCloud, another export running...): the engine's
    // own sentence, already in the screen's language (22.4); the framing only for an error without one.
    function showRefused(err) {
      const s = mk("div", "notice-status");
      const own = err && typeof err.state === "string" && err.state.indexOf("workspace_") === 0 && err.engineMessage;
      setStatus(s, "err", own || t("导出没有成功"), err);
      result.replaceChildren(s);
    }
    const self = { button: button, block: block, result: result, show: show, showRunning: showRunning, showRefused: showRefused, settings: settings };
    // the exporters of this draft still on screen (a detached block is dropped; a test's stand-in elements stay)
    const each = (fn) => { (drawn[path] || []).filter((x) => x.block.isConnected !== false).forEach(fn); };

    async function run(extra) {
      if (exporting && exporting.path === path) return;
      if (exporting) { result.replaceChildren(mk("p", "notice-status err", t("正在导出另一份文稿，请等它结束。"))); return; }
      const mine = exporting = { path: path, using: null };   // a project switch (reset) drops it: its answer is not shown
      each((x) => { x.button.disabled = true; });
      try {
        if (before && !(await before())) {
          if (exporting === mine) each((x) => x.result.replaceChildren(mk("p", "notice-status err", t("文稿没有保存，所以没有导出。"))));
          return;
        }
        if (exporting !== mine) return;
        mine.using = chosen || kept;
        each((x) => x.showRunning(mine.using));
        let r;
        try {
          r = await apiPost("/api/latex/export", Object.assign({ project: state.currentPath, path: path }, chosen ? { engine: chosen } : {}, extra));
        } catch (err) {
          if (exporting === mine) each((x) => x.showRefused(err));
          return;
        }
        if (exporting !== mine) return;
        if (r.engine) chosen = null;   // the server keeps the person's engine for this main file from now on
        lastShown = { path: path, r: r };
        each((x) => { x.show(r); x.settings(); });
      } finally {
        if (exporting === mine) { exporting = null; each((x) => { x.button.disabled = false; }); }
      }
    }

    drawn[path] = (drawn[path] || []).filter((x) => x.block.isConnected !== false).concat([self]);
    if (!list && lastShown && lastShown.path !== path) lastShown = null;   // another draft's page: the person left this one
    settings();
    if (exporting && exporting.path === path) showRunning(exporting.using);
    else if (lastShown && lastShown.path === path) show(lastShown.r);
    return { button: button, block: block };
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
        const li = mk("li", "res-row"), main = mk("div", "res-main"), seen = p.pending ? [t("待批准"), "ochre"] : pageState(p.state);
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
      (data.links || []).filter((l) => !l.pending).forEach((l) => {
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

  // 正文 (22.10), the sidebar's page of the project's drafts: each main file with the files it brings in, then the rest.
  // A path opens the editor, whose 返回 comes back here; a file only in the cloud is text. One read, GET /api/latex/drafts,
  // which also counts each draft's passages; a main file's 导出 PDF is the exporter's, quiet. Writes nothing.
  async function loadDrafts(host) {
    if (!host.children.length) host.replaceChildren(mk("p", "empty-note", t("载入中…")));
    const epoch = state.dataEpoch;   // a project switch while the read is out: its answer is not shown
    let data;
    try { data = await apiGet("/api/latex/drafts"); } catch (err) {
      if (epoch !== state.dataEpoch) return;
      const status = mk("div", "notice-status");
      setStatus(status, "err", t("没能读到项目里的文稿"), err);
      host.replaceChildren(status);
      return;
    }
    if (epoch !== state.dataEpoch) return;
    host.dataset.loaded = "1";
    const when = (at) => (window.RODesk && RODesk.whenWords && RODesk.whenWords(at, new Date())) || at;
    const row = (d) => {
      const li = mk("li", "res-row"), main = mk("div", "res-main");
      main.appendChild(d.cloud ? mk("span", "mono", d.path) : mkButton(d.path, "ws-text-link mono", () => open(d.path)));
      if (d.cloud) main.appendChild(mk("span", "node-sub-inline", t("只在云端")));
      else if (d.modified) main.appendChild(mk("span", "node-sub-inline", t("改动于 {when}", { when: when(d.modified) })));
      if (d.supported) main.appendChild(mk("span", "node-sub-inline", t("{n} 段有依据文件", { n: d.supported })));
      if (d.waiting) main.appendChild(mk("span", "status-label tone-ochre", t("{n} 段待复核", { n: d.waiting })));
      li.appendChild(main);
      return li;
    };
    const list = (rows) => { const ul = mk("ul", "res-list"); rows.forEach((d) => ul.appendChild(row(d))); return ul; };
    const page = mk("div", "latex-drafts");
    page.appendChild(mk("p", "node-sub", t("项目里的 LaTeX 文稿。点一份就在编辑器里打开；改动保存回原文件。")));
    if (data.message) page.appendChild(mk("p", "notice-status err", data.message));
    (data.groups || []).forEach((g) => {
      const block = mk("section", "res-block latex-draft-group"), head = row(g.main);
      let ex = null;
      if (!g.main.cloud) {
        ex = exporter(g.main.path, null, true);
        ex.button.className = "ws-text-link";   // 22.10: a quiet text action, never a second primary
        head.children[0].appendChild(ex.button);
      }
      const top = mk("ul", "res-list");
      top.appendChild(head);
      block.appendChild(top);
      if (ex) block.appendChild(ex.block);
      if (g.files.length) block.appendChild(list(g.files));
      page.appendChild(block);
    });
    if ((data.others || []).length) {
      const block = mk("section", "res-block latex-draft-others");
      if ((data.groups || []).length) block.appendChild(mk("h2", "desk-title", t("其他 .tex 文件")));
      block.appendChild(list(data.others));
      page.appendChild(block);
    }
    if (!(data.groups || []).length && !(data.others || []).length) {
      page.append(mk("p", "node-empty", t("这个项目里还没有 .tex 文稿。")), mk("p", "node-sub", t("把 .tex 文件放进项目文件夹后，这里会列出来。")));
    }
    if (data.truncated) page.appendChild(mk("p", "node-sub", t("项目里的 .tex 文件多于 {n} 个，这里只列出前 {n} 个。", { n: data.limit })));
    if (data.generated) page.appendChild(mk("p", "node-sub", t("另有 {n} 个由脚本生成的 .tex 表格，只能查看，不在这里编辑。", { n: data.generated })));
    host.replaceChildren(page);
  }

  // The pages' refresh (app.html refreshCurrentView): evidence.toml is in the watcher's set, so a link made or withdrawn
  // elsewhere (the CLI, another window) shows in the open editor at once (22.2 "The index").
  function refresh() {
    if (ed && ed.view && !ed.busy && !ed.acting) return loadPassages(ed, true);
  }

  // A project switch or the editor's view dropped: nothing of the previous project stays open.
  function reset() { discard(); fromView = null; lastShown = null; exporting = null; sourceShown.clear(); Object.keys(drawn).forEach((k) => { delete drawn[k]; }); }

  if (typeof window.addEventListener === "function") window.addEventListener("focus", () => { checkRevision(); });

  window.ROLatex = Object.freeze({
    loadEditorLibs: loadEditorLibs, loadMath: loadMath, open: open, isDirty: dirty, mayClose: mayClose, section: section, reset: reset,
    renderSupported: renderSupported, paperCounts: paperCounts, refresh: refresh, exporter: exporter, loadDrafts: loadDrafts,
    _vendorScripts: VENDOR_SCRIPTS, _passages: () => (ed && ed.view ? passagesOf(ed) : null),
    _pure: Object.freeze({ cpLength: cpLength, serverEdits: serverEdits, breakFilter: breakFilter, includeMatches: includeMatches, positions: positions,
      readLatex: readLatex, bodyStart: bodyStart, returnSpec: returnSpec, wordField: wordField, wordExtensions: wordExtensions, onReturn: onReturn, mathDOM: mathDOM }),
  });
})();
