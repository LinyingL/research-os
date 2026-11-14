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
// The edit view (22.1; B1 of 22.12, the researcher, 2026-11-14): the range reader `readLatex`, a state field of decorations
// that show no LaTeX at all (markup hidden, formulas by KaTeX, chips, list items, the 导言区 one quiet line), the input
// filter that keeps typing literal, Return like Word, and 「显示代码」 in the right column for the code.
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
  const STYLES = new Map([["textbf", "latex-b"], ["textit", "latex-i"], ["emph", "latex-i"], ["underline", "latex-u"],
    ["textsf", "latex-sf"], ["texttt", "latex-tt"], ["textrm", "latex-rm"]]);
  // 22.12 B: the font and size groups of 格式与插入 ({\heiti …}, {\large …}), drawn in their face or size, the switch hidden.
  const GROUP_CLASSES = new Map([["songti", "latex-f-song"], ["fangsong", "latex-f-song"], ["heiti", "latex-f-hei"], ["kaishu", "latex-f-kai"],
    ["small", "latex-s-small"], ["large", "latex-s-large"], ["Large", "latex-s-larger"]]);
  const PREAMBLE_META = new Set(["documentclass", "usepackage", "title", "author", "date"]);
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
  // B1 (22.1): braces, ties and one-character commands are read too, for the edit view that shows no code
  const SPECIAL = /[\\%$~{}]/g;

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

  // {cmds, maths, envs, raws, preamble, end, ...}: positions are indices into `text`. A command is {name, role (heading,
  // style, cite, ref, item or other), from, nameTo, to, args: [{from, to, opt}]}; an item has its list and number. An
  // environment {name, role (list, text or raw), from, beginTo, endFrom, to}; math {from, to, display, src}; raw text
  // (comments, \verb, verbatim-like environments) {from, to, kind}. Nothing inside math or raw text is read.
  // For the edit view (22.1, B1): `braces`, every pair of braces read as text {from, to} (a command's argument among
  // them: `argStarts` holds where those open); `symbols`, each one-character command {from, to, ch} (\%, \\, \' …);
  // `tildes`; and `bad`, what TeX would stop at or the reader cannot follow {at, why: brace, env or math}.
  function readLatex(text) {
    const out = { cmds: [], maths: [], envs: [], raws: [], groups: [], preamble: null, end: -1, meta: {}, braces: [], symbols: [], tildes: [], bad: [] }, theorems = new Set();
    const stack = [], n = text.length, argStarts = new Set(), meta = { packages: 0 }, open = [];
    out.argStarts = argStarts;
    let i = 0;
    while (i < n) {
      SPECIAL.lastIndex = i;
      const m = SPECIAL.exec(text);
      if (!m) break;
      i = m.index;
      const c = text[i];
      if (c === "%") { let e = text.indexOf("\n", i); if (e < 0) e = n; out.raws.push({ from: i, to: e, kind: "comment" }); i = e; continue; }
      if (c === "~") { out.tildes.push(i); i += 1; continue; }
      if (c === "{") { open.push(i); i += 1; continue; }
      if (c === "}") { if (open.length) out.braces.push({ from: open.pop(), to: i + 1 }); else out.bad.push({ at: i, why: "brace" }); i += 1; continue; }
      if (c === "$") {
        const display = text[i + 1] === "$", wide = display ? 2 : 1, e = mathEnd(text, i + wide, display ? "$$" : "$");
        if (e > 0) { out.maths.push({ from: i, to: e, display: display, src: text.slice(i + wide, e - wide) }); i = e; } else { out.bad.push({ at: i, why: "math" }); i += wide; }
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
          out.bad.push({ at: i, why: "math" });
        } else if (d !== undefined) out.symbols.push({ from: i, to: s + 1, ch: d });
        i = s + 1;
        continue;
      }
      const name = text.slice(s, e), star = text[e] === "*" ? 1 : 0;
      if (name === "verb") {   // \verb|...|: to the same delimiter on the same line
        const d = text[e + star], close = d && d !== "\n" && d !== "\r" ? text.indexOf(d, e + star + 1) : -1, eol = text.indexOf("\n", e + star + 1);
        if (close > 0 && (eol < 0 || close < eol)) { out.raws.push({ from: i, to: close + 1, kind: "verb" }); i = close + 1; continue; }
        out.bad.push({ at: i, why: "env" });
        i = e;
        continue;
      }
      if (name === "begin" || name === "end") {
        const at = skipSpaces(text, e), g = text[at] === "{" ? skipGroup(text, at, true) : -1;
        if (g < 0) { out.bad.push({ at: i, why: "env" }); i = e; continue; }
        const env = text.slice(at + 1, g - 1).trim();
        if (name === "end") {
          let k = stack.length - 1;
          while (k >= 0 && stack[k].name !== env) k -= 1;
          // an \end that closes nothing, closes an outer environment over an open inner one, or crosses a brace
          if (k !== stack.length - 1 || k < 0 || stack[k].depth !== open.length) out.bad.push({ at: i, why: "env" });
          if (k >= 0) { stack[k].endFrom = i; stack[k].to = g; stack.length = k; }
          if (env === "document" && out.end < 0) out.end = g;
          i = g;
          continue;
        }
        if (env === "document" && !out.preamble && !stack.length) out.preamble = { from: 0, to: i };
        if (MATH_ENVS.test(env)) {
          const end = mathEnd(text, g, "\\end{" + env + "}");
          if (end > 0) { out.maths.push({ from: i, to: end, display: true, src: text.slice(i, end) }); i = end; continue; }
          out.bad.push({ at: i, why: "math" });
        }
        if (VERBATIM_ENVS.test(env)) {
          const close = "\\end{" + env + "}", end = text.indexOf(close, g), to = end < 0 ? n : end + close.length;
          if (end < 0) out.bad.push({ at: i, why: "env" });
          out.raws.push({ from: i, to: to, kind: "verbatim" });
          i = to;
          continue;
        }
        const base = env.replace(/\*$/, ""), prose = TEXT_ENVS.has(base) || theorems.has(base);
        const frame = { name: env, role: LIST_ENVS.has(env) ? "list" : prose ? "text" : "raw", from: i, beginTo: g, endFrom: n, to: n, items: 0, depth: open.length, itemCmds: [] };
        stack.push(frame);
        out.envs.push(frame);
        i = g;
        continue;
      }
      const role = HEADINGS.has(name) ? "heading" : STYLES.has(name) ? "style" : CITE_RE.test(name) ? "cite" : REFS.has(name) ? "ref" : name === "item" ? "item" : "other";
      const cmd = { name: name, role: role, from: i, nameTo: e + star, to: e + star, args: [] };
      if (GROUP_CLASSES.has(name) && !star) {   // {\heiti …}: a group of its own, not a command's argument (as declAt reads it)
        let o = i - 1;
        while (o >= 0 && (text[o] === " " || text[o] === "\t")) o -= 1;
        let run = 0;
        while (o - run - 1 >= 0 && text[o - run - 1] === "\\") run += 1;
        const close = o >= 0 && text[o] === "{" && run % 2 === 0 && !argStarts.has(o) ? skipGroup(text, o, false) : -1;
        if (close > 0) {
          const g = { name: name, from: o, nameTo: e + (text[e] === " " || text[e] === "\t" ? 1 : 0), to: close };
          out.groups.push(g);
          cmd.group = g;
        }
      }
      let at = cmd.nameTo;
      if (role === "other") {   // a command it does not know: the groups right after it are its arguments
        while (cmd.args.length < 9 && (text[at] === "{" || text[at] === "[")) {
          const g = skipGroup(text, at, false);
          if (g < 0) break;
          cmd.args.push({ from: at, to: g, opt: text[at] === "[" });
          argStarts.add(at);
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
        if (g > 0) { cmd.args.push({ from: at, to: g, opt: false }); argStarts.add(at); }
        else if (role !== "item") cmd.role = "other";   // no argument: shown as written
        if (role === "item") {
          for (let k = stack.length - 1; k >= 0; k--) if (stack[k].role === "list") { cmd.list = stack[k]; cmd.n = ++stack[k].items; stack[k].itemCmds.push(cmd); break; }
          cmd.depth = stack.filter((f) => f.role === "list").length;
        }
      }
      if (cmd.args.length) cmd.to = cmd.args[cmd.args.length - 1].to;
      if (name === "newtheorem" || name === "declaretheorem") {   // \newtheorem{lemma}[theorem]{Lemma}: lemma is text
        const a = cmd.args.find((x) => !x.opt);
        if (a) theorems.add(text.slice(a.from + 1, a.to - 1).trim());
      }
      if (PREAMBLE_META.has(name) && !out.preamble && !stack.length) {   // what the 导言区 line and \maketitle show
        const a = cmd.args.filter((x) => !x.opt).pop(), arg = a ? text.slice(a.from + 1, a.to - 1) : null;
        if (name === "usepackage") meta.packages += arg ? arg.split(",").filter((x) => x.trim()).length : 0;
        else if (arg != null) meta[name === "documentclass" ? "cls" : name] = name === "documentclass" ? arg.trim() : arg;
      }
      out.cmds.push(cmd);
      i = cmd.role === "cite" || cmd.role === "ref" ? cmd.to : cmd.nameTo;   // the arguments' text is read as text
    }
    if (out.preamble) out.meta = meta;
    open.forEach((at) => out.bad.push({ at: at, why: "brace" }));
    stack.forEach((f) => out.bad.push({ at: f.from, why: "env" }));
    if (out.braces.length > 1) out.braces.sort((a, b) => a.from - b.from);
    return out;
  }

  // What KaTeX is given: \label (which it does not know) dropped, multline set as gather.
  function forKatex(src) { return src.replace(/\\label\s*\{[^{}]*\}/g, "").replace(/\\(begin|end)\{multline(\*?)\}/g, "\\$1{gather$2}"); }
  // A formula typeset by KaTeX (trust off: no \href or \url reaches the network); one it cannot typeset is shown as
  // written, in the mono face, uncoloured -- the PDF is the judge (22.1). Never throws. What trust off refuses (\href,
  // \url, \includegraphics ...) KaTeX draws in its error colour instead of throwing: that colour, set to one no formula
  // asks for, counts as a failure too.
  const KATEX_ERROR = "#0e0e0f";
  // HTML only (B1): KaTeX's MathML would carry the formula's TeX into the page's text; the box is named 公式 for a
  // screen reader instead.
  function mathDOM(src, display, whole) {
    const box = mk("span", display ? "latex-math latex-math-display" : "latex-math");
    if (typeof box.setAttribute === "function") { box.setAttribute("role", "img"); box.setAttribute("aria-label", t("公式")); }
    try {
      const html = window.katex.renderToString(forKatex(src), { displayMode: display, throwOnError: true, trust: false, strict: "ignore", errorColor: KATEX_ERROR, output: "html" });
      if (html.includes(KATEX_ERROR)) throw new Error("refused");
      box.innerHTML = html;
    } catch (err) {
      // never the code (22.1, B1): a quiet word where KaTeX cannot typeset it -- the PDF is the judge
      box.className = "latex-math-tag";
      box.textContent = t("公式");
    }
    return box;
  }

  // -- the page's look (22.12 B) -------------------------------------------------------------------------------------
  //
  // The page's blocks, drawn by the edit view (below): \maketitle as the title block read from the preamble, a table (a
  // tabular, or a table float holding one) as a read-only grid with its caption, a figure as its files and caption.
  // A table the grid reader doubts (unbalanced braces, a nested environment, more cells than columns, a partial rule)
  // is a quiet chip with its caption's words instead -- never its code.

  const TABLE_FLOATS = new Set(["table", "table*"]), FIGURE_FLOATS = new Set(["figure", "figure*"]);
  const TABULARS = new Map([["tabular", 0], ["tabular*", 1], ["tabularx", 1]]);   // arguments before the column spec
  const FLOAT_EXTRAS = /\\(?:centering|small|footnotesize|scriptsize|normalsize)(?![A-Za-z])/g;
  const RASTER = /\.(?:png|jpe?g|gif|webp)$/i;
  const TABLE_RULE = /^\s*\\(toprule|midrule|bottomrule|hline|addlinespace)(?![A-Za-z])\s*(?:\[[^\]]*\])?/;
  const TABLE_LIMIT = { columns: 20, rows: 200 };

  function linesOf(doc, from, to) { return [doc.lineAt(from).from, doc.lineAt(to).to]; }
  // Whether [from, to] stands alone on its lines: nothing but spaces before it on its first line or after it on its last.
  function aloneOn(doc, from, to) {
    const a = doc.lineAt(from), b = doc.lineAt(to);
    return !/\S/.test(doc.sliceString(a.from, from)) && !/\S/.test(doc.sliceString(to, b.to));
  }
  // Environments whose whole range is drawn as one (a table, a figure, a tabular, an abstract's indented lines).
  function wholeEnv(e) { return e.endFrom < e.to && (TABLE_FLOATS.has(e.name) || FIGURE_FLOATS.has(e.name) || TABULARS.has(e.name) || e.name === "abstract"); }

  // The columns of a tabular's spec, as alignments ("l", "c", "r"): null when it holds what is not read here.
  function columnsOf(spec) {
    const cols = [];
    let i = 0;
    const group = () => {
      while (spec[i] === " " || spec[i] === "\n" || spec[i] === "\t") i += 1;
      if (spec[i] !== "{") return null;
      const g = skipGroup(spec, i, false);
      if (g < 0) return null;
      const s = spec.slice(i + 1, g - 1);
      i = g;
      return s;
    };
    while (i < spec.length) {
      const c = spec[i++];
      if (c === " " || c === "\n" || c === "\t" || c === "|") continue;
      if (c === "l" || c === "c" || c === "r") cols.push(c);
      else if (c === "X") cols.push("l");
      else if (c === "p" || c === "m" || c === "b") { if (group() == null) return null; cols.push("l"); }
      else if (c === "@" || c === "!" || c === ">" || c === "<") { if (group() == null) return null; }
      else if (c === "*") {
        const k = group(), sub = k == null ? null : group(), inner = sub == null ? null : columnsOf(sub);
        if (!inner || !/^\s*\d{1,2}\s*$/.test(k)) return null;
        for (let r = Number(k); r > 0 && cols.length <= TABLE_LIMIT.columns; r--) cols.push(...inner);
      } else return null;
      if (cols.length > TABLE_LIMIT.columns) return null;
    }
    return cols.length ? cols : null;
  }

  // A tabular's grid from what follows \begin{tabular} up to its \end: {cols, rows: [{cells, above, below}]}, a rule
  // "heavy" (\toprule, \bottomrule) or "light" (\midrule, \hline). Null on any doubt.
  function tabularGrid(src, before) {
    const text = src.split("\n").map(codePart).join("\n");
    let i = 0;
    const ws = () => { while (i < text.length && /\s/.test(text[i])) i += 1; };
    for (let k = 0; k < before; k++) {
      ws();
      const g = text[i] === "{" ? skipGroup(text, i, false) : -1;
      if (g < 0) return null;
      i = g;
    }
    ws();
    if (text[i] === "[") { const g = skipGroup(text, i, false); if (g < 0) return null; i = g; ws(); }
    const g = text[i] === "{" ? skipGroup(text, i, false) : -1;
    const cols = g > 0 ? columnsOf(text.slice(i + 1, g - 1)) : null;
    if (!cols) return null;
    const body = text.slice(g);
    if (/\\(?:begin|end|cline|cmidrule)(?![A-Za-z])/.test(body)) return null;   // a nested environment, a partial rule
    const chunks = [];
    let cells = [], cell = "", depth = 0;
    for (let j = 0; j < body.length; j++) {
      const c = body[j];
      if (c === "\\") {
        if (body[j + 1] === "\\" && depth === 0) {   // \\ ends a row; \\* and \\[2pt] too
          cells.push(cell); chunks.push(cells); cells = []; cell = "";
          let k = j + 2;
          if (body[k] === "*") k += 1;
          let s = k;
          while (body[s] === " " || body[s] === "\t") s += 1;
          if (body[s] === "[") { const close = body.indexOf("]", s); if (close < 0) return null; k = close + 1; }
          j = k - 1;
          continue;
        }
        cell += c + (body[j + 1] || "");
        j += 1;
        continue;
      }
      if (c === "{") depth += 1;
      else if (c === "}" && --depth < 0) return null;
      if (c === "&" && depth === 0) { cells.push(cell); cell = ""; continue; }
      cell += c;
    }
    if (depth !== 0) return null;
    cells.push(cell); chunks.push(cells);
    const rows = [], weigh = (was, rule) => (rule === "addlinespace" ? was : was === "heavy" || rule === "toprule" || rule === "bottomrule" ? "heavy" : "light");
    let above = null;
    for (let r = 0; r < chunks.length; r++) {
      const cs = chunks[r];
      let m;
      while ((m = TABLE_RULE.exec(cs[0]))) { above = weigh(above, m[1]); cs[0] = cs[0].slice(m[0].length); }
      if (r === chunks.length - 1 && cs.length === 1 && !/\S/.test(cs[0])) {   // after the last \\: rules only
        if (above && rows.length) rows[rows.length - 1].below = above;
        break;
      }
      if (cs.length > cols.length || rows.length >= TABLE_LIMIT.rows) return null;
      rows.push({ cells: cs.map((x) => x.trim()), above: above, below: null });
      above = null;
    }
    return rows.length ? { cols: cols, rows: rows } : null;
  }

  // What a float holds besides `skip` (its tabular or its graphics): only \centering, size switches, comments, one
  // \caption and \label. {caption} (the caption's source, or null), or null when it holds anything else.
  function floatRest(doc, read, e, skip) {
    let caption = null, captions = 0;
    const cut = skip.map((r) => [r.from, r.to]);
    for (const c of read.cmds) {
      if (c.from <= e.beginTo || c.from >= e.endFrom || cut.some((r) => c.from >= r[0] && c.from < r[1])) continue;
      if (c.name === "caption") {
        const a = c.args.filter((x) => !x.opt).pop();
        if (!a || ++captions > 1) return null;
        caption = doc.sliceString(a.from + 1, a.to - 1);
        cut.push([c.from, c.to]);
      } else if (c.name === "label") cut.push([c.from, c.to]);
    }
    cut.sort((a, b) => a[0] - b[0]);
    let rest = "", at = e.beginTo;
    for (const [a, b] of cut) { if (a > at) rest += doc.sliceString(at, a); at = Math.max(at, b); }
    rest += doc.sliceString(at, e.endFrom);
    rest = rest.split("\n").map(codePart).join("\n").replace(/^\s*\[[^\]\n]*\]/, "").replace(FLOAT_EXTRAS, "");
    return /\S/.test(rest) ? null : { caption: caption };
  }
  function tableModel(doc, read, e) {
    const inner = read.envs.filter((x) => x !== e && x.from > e.from && x.to <= e.endFrom);
    let tab = e, caption = null;
    if (TABLE_FLOATS.has(e.name)) {
      if (inner.length !== 1 || !TABULARS.has(inner[0].name) || !(inner[0].endFrom < inner[0].to)) return null;
      tab = inner[0];
      const rest = floatRest(doc, read, e, [tab]);
      if (!rest) return null;
      caption = rest.caption;
    } else if (inner.length) return null;
    const grid = tabularGrid(doc.sliceString(tab.beginTo, tab.endFrom), TABULARS.get(tab.name));
    return grid ? { caption: caption, grid: grid } : null;
  }
  function figureModel(doc, read, e) {
    if (read.envs.some((x) => x !== e && x.from > e.from && x.to <= e.endFrom)) return null;   // subfigures, minipages
    const pics = read.cmds.filter((c) => c.name === "includegraphics" && c.from > e.beginTo && c.to <= e.endFrom);
    if (!pics.length || pics.length > 4) return null;
    const paths = pics.map((c) => { const a = c.args.filter((x) => !x.opt); return a.length === 1 ? doc.sliceString(a[0].from + 1, a[0].to - 1).trim() : ""; });
    if (paths.some((p) => !p)) return null;
    const rest = floatRest(doc, read, e, pics);
    return rest ? { caption: rest.caption, paths: paths } : null;
  }

  // A piece of LaTeX (a title, a caption, a cell) drawn into `host`: the styles, fonts, sizes, formulas and chips the
  // editor knows drawn as in the text; \\ a new line; \and a gap; escapes and accents as their characters; every other
  // command a quiet chip with its arguments' plain words, or 「LaTeX 内容」 -- never its code (22.1, B1), never guessed.
  function inlineInto(host, src, depth) {
    let i = 0, text = "";
    const n = src.length;
    const flush = () => { if (text) { host.appendChild(mk("span", null, text)); text = ""; } };
    const put = (el) => { flush(); host.appendChild(el); };
    if (depth > 8) { host.appendChild(mk("span", "latex-chip latex-chip-unit", t("LaTeX 内容"))); return; }
    while (i < n) {
      const c = src[i];
      if (c === "%") { const e = src.indexOf("\n", i); i = e < 0 ? n : e + 1; continue; }
      if (c === "~") { text += " "; i += 1; continue; }
      if (c === "$") {
        const display = src[i + 1] === "$", open = display ? 2 : 1, e = mathEnd(src, i + open, display ? "$$" : "$");
        if (e < 0) { text += c; i += 1; continue; }
        put(window.katex ? mathDOM(src.slice(i + open, e - open), false, src.slice(i, e)) : mk("span", "latex-math-tag", t("公式")));
        i = e;
        continue;
      }
      if (c === "{") {
        const g = skipGroup(src, i, false);
        if (g < 0) { text += c; i += 1; continue; }
        const m = /^\{[ \t]*\\([A-Za-z]+)(?![A-Za-z])[ \t]?/.exec(src.slice(i, i + 40));
        flush();
        if (m && GROUP_CLASSES.has(m[1])) { const span = mk("span", GROUP_CLASSES.get(m[1])); inlineInto(span, src.slice(i + m[0].length, g - 1), depth + 1); host.appendChild(span); }
        else inlineInto(host, src.slice(i + 1, g - 1), depth + 1);
        i = g;
        continue;
      }
      if (c !== "\\") { text += c; i += 1; continue; }
      const d = src[i + 1];
      if (d === "\\") { put(mk("br")); i += 2; continue; }
      if (d && /[%&$#_{} ]/.test(d)) { text += d; i += 2; continue; }
      let e = i + 1;
      while (e < n && /[A-Za-z]/.test(src[e])) e += 1;
      if (e === i + 1) {   // a one-character command: an accent, a space, or nothing to see
        const m = d && ACCENTS[d] ? /^(?:\{\s*([A-Za-z])\s*\}|([A-Za-z]))/.exec(src.slice(i + 2, i + 6)) : null;
        if (m) { text += accented(ACCENTS[d], m[1] || m[2]); i += 2 + m[0].length; continue; }
        if (d && Object.prototype.hasOwnProperty.call(SYMBOL_TEXT, d)) text += SYMBOL_TEXT[d];
        else if (d && !SYMBOL_SILENT.has(d)) put(mk("span", "latex-chip latex-chip-unit", t("LaTeX 内容")));
        i += 2;
        continue;
      }
      const name = src.slice(i + 1, e);
      let at = e;
      while (src[at] === " ") at += 1;
      if (STYLES.has(name) && src[at] === "{" && skipGroup(src, at, false) > 0) {
        const g = skipGroup(src, at, false), span = mk("span", STYLES.get(name));
        inlineInto(span, src.slice(at + 1, g - 1), depth + 1);
        put(span);
        i = g;
        continue;
      }
      if (CITE_RE.test(name) || REFS.has(name)) {
        let k = at;
        for (let opts = 0; opts < 2 && src[k] === "["; opts++) { const g = skipGroup(src, k, false); if (g < 0) break; k = g; }
        const g = src[k] === "{" ? skipGroup(src, k, false) : -1;
        if (g > 0) {
          const keys = src.slice(k + 1, g - 1).split(",").map((x) => x.trim()).filter(Boolean).join(", ");
          put(mk("span", "latex-chip latex-chip-" + (CITE_RE.test(name) ? "cite" : "ref"), keys));
          i = g;
          continue;
        }
      }
      if (name === "and") { put(mk("span", "latex-title-and")); i = at; continue; }   // TeX drops the spaces after it
      if (GROUP_CLASSES.has(name)) {   // a switch in the middle: the rest of its group
        const span = mk("span", GROUP_CLASSES.get(name));
        inlineInto(span, src.slice(src[e] === " " ? e + 1 : e), depth + 1);
        put(span);
        i = n;
        continue;
      }
      let to = e;
      const args = [];
      while (to < n && (src[to] === "{" || src[to] === "[")) { const g = skipGroup(src, to, false); if (g < 0) break; args.push(src.slice(to + 1, g - 1)); to = g; }
      if (TEXT_MACROS.has(name) && args.every((a) => !a)) text += TEXT_MACROS.get(name);
      else if (SILENT.has(name) || SILENT_ARGS.has(name) || HIDDEN_CMDS.has(name)) { /* nothing to see */ }
      else if ((name === "multicolumn" || name === "multirow" || WRAPPERS.has(name)) && args.length) { flush(); inlineInto(host, args[args.length - 1], depth + 1); }
      else {
        const words = args.map((a) => plainWords(a)), said = words.length && words.every((w) => w != null) ? words.filter(Boolean).join(" ") : "";
        put(mk("span", "latex-chip latex-chip-unit", said || t("LaTeX 内容")));
      }
      i = to;
    }
    flush();
  }

  // The project path of an image \includegraphics names, as the export finds it from the main file's folder (22.4), for
  // a thumbnail through the asset route; null for a kind the route does not draw or a path that leaves the project.
  function assetOf(path) {
    if (!RASTER.test(path) || typeof ed === "undefined" || !ed || !ed.path) return null;
    const f = ed.format || {}, folder = typeof f.folder === "string" ? f.folder : ed.path.slice(0, Math.max(ed.path.lastIndexOf("/"), 0));
    const p = normPath((folder ? folder + "/" : "") + path);
    return p && !p.startsWith("..") ? "/api/workspace/asset?path=" + encodeURIComponent(p) : null;
  }
  function graphicDOM(path, tag) {
    const line = mk(tag, "latex-graphic"), src = assetOf(path);
    if (src) {
      const img = mk("img", "latex-graphic-thumb");
      img.alt = ""; img.loading = "lazy"; img.src = src;
      img.addEventListener("error", () => { if (img.remove) img.remove(); });
      line.appendChild(img);
    }
    line.appendChild(mk("span", "latex-graphic-name", path));
    return line;
  }
  function captionDOM(word, src) {
    const p = mk("p", "latex-caption");
    p.appendChild(mk("span", "latex-caption-word", word));
    inlineInto(p, src, 0);
    return p;
  }
  // `word`: what stands before the caption, 「表 N：」 where the number is known (22.1), else 「表：」.
  function tableDOM(model, word) {
    const box = mk("div", "latex-table-preview"), table = mk("table", "latex-table"), body = mk("tbody");
    if (model.caption != null) box.appendChild(captionDOM(word || t("表："), model.caption));
    const cols = model.grid.cols;
    model.grid.rows.forEach((r) => {
      const tr = mk("tr", [r.above ? "latex-rule-" + r.above : "", r.below ? "latex-rule-below-" + r.below : ""].filter(Boolean).join(" ") || null);
      cols.forEach((align, k) => {
        const td = mk("td", align === "l" ? null : "latex-col-" + align);
        if (k < r.cells.length) inlineInto(td, r.cells[k], 0);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    box.appendChild(table);
    return box;
  }
  function figureDOM(model, word) {
    const box = mk("div", "latex-figure-preview");
    model.paths.forEach((p) => box.appendChild(graphicDOM(p, "div")));
    if (model.caption != null) box.appendChild(captionDOM(word || t("图："), model.caption));
    return box;
  }
  // \maketitle: the title large in the serif face, the author and the date under it. An empty \author{} is left out; a
  // \date{\today}, or no \date (LaTeX prints today's then too), is 「日期：导出当天」; an empty \date{} is left out.
  function titleDOM(meta) {
    const box = mk("div", "latex-titleblock");
    const part = (cls, src) => { const el = mk("div", cls); inlineInto(el, src, 0); box.appendChild(el); };
    if (meta.title != null && /\S/.test(meta.title)) part("latex-title", meta.title.trim());
    else box.appendChild(mk("div", "latex-title-none", t("（没有设置标题）")));
    if (meta.author != null && /\S/.test(meta.author)) part("latex-title-author", meta.author.trim());
    const date = meta.date == null ? (meta.own ? "\\today" : "") : meta.date.trim();   // a chapter has no preamble: no date guessed
    if (date === "\\today") box.appendChild(mk("div", "latex-title-date", t("日期：导出当天")));
    else if (date) part("latex-title-date", date);
    return box;
  }
  // 导言区's one quiet line in the edit view: the class and how many packages, in words (22.12 B), never opened there
  // (版式 edits it; 显示代码 shows it).
  function preambleWords(meta) {
    if (!meta || !meta.cls) return t("导言区");
    return t("导言区：文档类 {cls}，{n} 个宏包", { cls: meta.cls, n: meta.packages || 0 });
  }

  // The body's words for the status line: each CJK character one, each run of Latin letters or digits one; commands'
  // names, comments, formulas, verbatim text, the preamble and what is not prose (keys, labels, paths, environment
  // names, options, a tabular's columns) left out.
  const CJK = /[぀-ヿ㐀-䶿一-鿿豈-﫿]|[\ud840-\ud87f][\udc00-\udfff]/g;
  const LATIN_WORD = /[A-Za-z0-9À-ɏ]+(?:['’.-][A-Za-z0-9À-ɏ]+)*/g;
  function bodyWords(text, read) {
    const docEnv = read.envs.find((e) => e.name === "document");
    const start = docEnv ? docEnv.beginTo : 0, stop = docEnv && docEnv.endFrom < docEnv.to ? docEnv.endFrom : text.length;
    const cut = [];
    read.maths.forEach((f) => cut.push([f.from, f.to]));
    read.raws.forEach((r) => cut.push([r.from, r.to]));
    for (const c of read.cmds) {
      cut.push([c.from, c.nameTo]);
      if (c.role === "cite" || c.role === "ref" || NAME_ARGS.has(c.name) || c.name === "documentclass" || c.name === "usepackage") cut.push([c.from, c.to]);
      else c.args.forEach((a) => { if (a.opt) cut.push([a.from, a.to]); });
    }
    for (const e of read.envs) {
      cut.push([e.from, e.beginTo]);
      if (e.endFrom < e.to) cut.push([e.endFrom, e.to]);
      if (e.role === "raw") {   // a tabular's {ll}, a figure's [htbp]
        let at = e.beginTo;
        while (text[at] === "[" || text[at] === "{") { const g = skipGroup(text, at, false); if (g < 0) break; cut.push([at, g]); at = g; if (text[at - 1] === "}") break; }
      }
    }
    cut.sort((a, b) => a[0] - b[0]);
    let n = 0, at = start;
    const count = (s) => {
      const cjk = s.match(CJK);
      n += (cjk ? cjk.length : 0);
      const latin = s.replace(CJK, " ").match(LATIN_WORD);
      n += latin ? latin.length : 0;
    };
    for (const [a, b] of cut) {
      if (b <= at) continue;
      if (a >= stop) break;
      if (a > at) count(text.slice(at, a));
      at = Math.max(at, b);
    }
    if (at < stop) count(text.slice(at, stop));
    return n;
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
  // -- the edit view (22.1; the researcher, 2026-11-14: "我的想法是，编辑的时候是看不到latex代码的，右侧的选项打开才能看到代码") --
  //
  // In the edit view no LaTeX is ever visible, wherever the cursor or the selection is; 「显示代码」 in the right column
  // shows the whole file as code. `viewPieces` turns what `readLatex` read into pieces: atoms (a formula, a table, a
  // chip, a comment, the preamble …: one replace decoration each, never entered), the hidden markup of what keeps its
  // words editable (a heading's or a style's command and braces, an environment's \begin and \end, a list's \item, a
  // caption's figure around it), marks (the words' style) and line classes. Every replace is an atomic range, so the
  // cursor never stops inside one; `userEdit` keeps what is typed, pasted and deleted literal and the source balanced;
  // Return, ⇧Return and Backspace follow Word (`enterSpec`, `backSpec`).

  const TEXT_MACROS = new Map([["LaTeX", "LaTeX"], ["TeX", "TeX"], ["LaTeXe", "LaTeX2e"], ["ldots", "…"], ["dots", "…"], ["textellipsis", "…"],
    ["textendash", "–"], ["textemdash", "—"], ["S", "§"], ["P", "¶"], ["copyright", "©"], ["textcopyright", "©"], ["textregistered", "®"],
    ["texttrademark", "™"], ["textdegree", "°"], ["textbackslash", "\\"], ["textasciitilde", "~"], ["textasciicircum", "^"],
    ["textunderscore", "_"], ["textless", "<"], ["textgreater", ">"], ["textbar", "|"], ["slash", "/"], ["quad", " "],
    ["qquad", "  "], ["ss", "ß"], ["o", "ø"], ["O", "Ø"], ["ae", "æ"], ["AE", "Æ"], ["oe", "œ"], ["OE", "Œ"], ["aa", "å"], ["AA", "Å"],
    ["l", "ł"], ["L", "Ł"], ["i", "ı"], ["textquotedblleft", "“"], ["textquotedblright", "”"], ["textquoteleft", "‘"], ["textquoteright", "’"],
    ["euro", "€"], ["pounds", "£"], ["textyen", "¥"], ["textperthousand", "‰"]]);
  // accents, as combining marks: \'e, \'{e}, \c{c}
  const ACCENTS = { "'": "́", "`": "̀", "^": "̂", "\"": "̈", "~": "̃", "=": "̄", ".": "̇",
    c: "̧", v: "̌", u: "̆", H: "̋", r: "̊", k: "̨", d: "̣", b: "̱" };
  const SYMBOL_TEXT = { "%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "{": "{", "}": "}", " ": " ", ",": " ", ";": " ", ":": " ", ">": " " };
  const SYMBOL_SILENT = new Set(["!", "-", "/", "@", "*"]);
  // commands with nothing to read: hidden (their groups, if any, are plain groups)
  const SILENT = new Set(["noindent", "indent", "centering", "raggedright", "raggedleft", "par", "smallskip", "medskip", "bigskip", "vfill",
    "hfill", "null", "relax", "protect", "normalsize", "footnotesize", "scriptsize", "tiny", "small", "large", "Large", "LARGE", "huge", "Huge",
    "bf", "it", "em", "sc", "sf", "tt", "rm", "sl", "bfseries", "itshape", "scshape", "mdseries", "upshape", "slshape", "normalfont",
    "rmfamily", "sffamily", "ttfamily", "songti", "heiti", "kaishu", "fangsong", "nobreak", "nolinebreak", "allowbreak", "frenchspacing",
    "sloppy", "fussy", "xspace", "unskip", "leavevmode", "hline", "toprule", "midrule", "bottomrule", "maketitle"]);
  // commands whose arguments are lengths, colours or styles, never words: hidden with them
  const SILENT_ARGS = new Set(["vspace", "hspace", "addvspace", "vskip", "hskip", "color", "pagecolor", "thispagestyle", "pagenumbering",
    "linespread", "fontsize", "selectfont", "phantom", "hphantom", "vphantom", "enlargethispage", "setstretch"]);
  const BREAKS = new Set(["newline", "linebreak"]);
  // definitions in the body: one chip, their whole extent read by shape (n: \cs or {…}; c: \cs; o: an optional […]; b:
  // {…}; p: a parameter text up to "{"; e: an optional "=" and a \cs or a character)
  const DEFINITIONS = new Map([["newcommand", "noob"], ["renewcommand", "noob"], ["providecommand", "noob"], ["DeclareRobustCommand", "noob"],
    ["def", "cpb"], ["gdef", "cpb"], ["edef", "cpb"], ["xdef", "cpb"], ["let", "ce"], ["newenvironment", "boobb"], ["renewenvironment", "boobb"],
    ["DeclareMathOperator", "nb"], ["newtheorem", "bobo"], ["setlength", "nb"], ["addtolength", "nb"], ["setcounter", "bb"],
    ["addtocounter", "bb"], ["newlength", "n"], ["newcounter", "bo"], ["newif", "c"], ["usepackage", "ob"], ["hypersetup", "b"],
    ["captionsetup", "ob"], ["ctexset", "b"], ["geometry", "b"], ["graphicspath", "b"]]);
  // commands whose last argument is words, kept editable (the others hidden): [class, mandatory arguments]
  const WRAPPERS = new Map([["textsc", ["latex-sc", 1]], ["textsuperscript", ["latex-sup", 1]], ["textsubscript", ["latex-sub", 1]],
    ["textsl", ["latex-i", 1]], ["textup", ["", 1]], ["textmd", ["", 1]], ["textnormal", ["", 1]], ["mbox", ["", 1]], ["hbox", ["", 1]],
    ["text", ["", 1]], ["makebox", ["", 1]], ["fbox", ["latex-boxed", 1]], ["framebox", ["latex-boxed", 1]], ["textcolor", ["", 2]],
    ["colorbox", ["", 2]], ["uline", ["latex-u", 1]], ["sout", ["latex-strike", 1]], ["hl", ["", 1]], ["enquote", ["", 1]],
    ["foreignlanguage", ["", 2]]]);
  // a line of its own in the edit view, said in words
  const LINE_WORDS = { tableofcontents: () => t("目录（导出时生成）"), listoffigures: () => t("图目录（导出时生成）"),
    listoftables: () => t("表目录（导出时生成）"), newpage: () => t("分页"), clearpage: () => t("分页"), cleardoublepage: () => t("分页"),
    pagebreak: () => t("分页"), printbibliography: () => t("参考文献（导出时生成）"), bibliography: () => t("参考文献（导出时生成）"),
    appendix: () => t("附录") };
  const HIDDEN_CMDS = new Set(["label", "bibliographystyle", "nocite", "addbibresource"]);
  const REF_LIKE = /^(?:ref|eqref|autoref|Autoref|cref|Cref|pageref|nameref|vref)$/;
  const INCLUDE_CMDS = new Set(["input", "include", "subfile", "import", "subimport"]);
  // the arguments an environment takes after \begin{…}, hidden with it (a theorem's [title] is shown)
  const ENV_ARGS = { minipage: "ooom", multicols: "mo", "multicols*": "mo", spacing: "m", itemize: "o", enumerate: "o", description: "o" };
  const ENV_LINES = { center: "latex-align-c", flushright: "latex-align-r", flushleft: "latex-align-l", quote: "latex-quote-line",
    quotation: "latex-quote-line", verse: "latex-quote-line", abstract: "latex-abstract-line" };
  // conditionals and catcode tricks in the body: the reader cannot say what is text, so the file opens as code
  const UNSURE_CMDS = /^(?:if(?!thenelse$|def$|undef$|strequal$|boolexpr$|toggle$)[A-Za-z@]*|fi|else|or|makeatletter|makeatother|catcode|csname|endcsname|expandafter|begingroup|endgroup|loop|repeat)$/;
  const TYPE_ESCAPES = { "%": "\\%", "$": "\\$", "&": "\\&", "#": "\\#", "_": "\\_", "{": "\\{", "}": "\\}",
    "~": "\\textasciitilde{}", "^": "\\textasciicircum{}", "\\": "\\textbackslash{}" };

  // Why the edit view cannot show `text` safely (a reason, said above the code), or null: what TeX would stop at
  // (unbalanced braces, an environment or a formula not closed), a list with no \item, a conditional in the body.
  function unsureWhy(text, read) {
    const bad = read.bad[0];
    if (bad) return bad.why === "brace" ? t("这个文件里有没配对的括号，只能显示代码。") : t("这个文件里有没结束的环境或公式，只能显示代码。");
    const body = read.preamble ? read.preamble.to : 0;
    const odd = read.cmds.find((c) => c.from >= body && UNSURE_CMDS.test(c.name));
    if (odd) return t("这个文件的正文里有条件命令（{command}），只能显示代码。", { command: "\\" + odd.name });
    if (read.envs.some((e) => e.role === "list" && e.from >= body && !e.items && e.name !== "description")) return t("这个文件里有没有条目的列表，只能显示代码。");
    return null;
  }

  // The words of a piece of LaTeX that is only words (escapes allowed, braces dropped), or null when it holds markup.
  function plainWords(src) {
    const s = String(src).replace(/\\([%$&#_{} ])/g, "$1").replace(/~/g, " ");
    if (/[\\$&#^_%]/.test(s)) return null;
    return s.replace(/[{}]/g, "").replace(/\s+/g, " ").trim();
  }
  // The end of a definition in the body (DEFINITIONS' shapes), from its name on.
  function definitionEnd(text, c) {
    let at = c.nameTo;
    const ws = () => { while (text[at] === " " || text[at] === "\t" || text[at] === "\n" || text[at] === "\r") at += 1; };
    const cs = () => {
      if (text[at] !== "\\") return false;
      let e = at + 1;
      while (/[A-Za-z@]/.test(text[e] || "")) e += 1;
      at = e === at + 1 ? at + 2 : e;
      return true;
    };
    const group = (o) => { if (text[at] !== o) return false; const g = skipGroup(text, at, false); if (g < 0) return false; at = g; return true; };
    for (const s of DEFINITIONS.get(c.name) || "") {
      const keep = at;
      ws();
      if (s === "o") { if (!group("[")) at = keep; continue; }
      if (s === "p") { while (at < text.length && text[at] !== "{" && text[at] !== "\n") at += 1; continue; }
      if (s === "e") { if (text[at] === "=") { at += 1; ws(); } if (!cs()) at += 1; continue; }
      const ok = s === "c" ? cs() : s === "n" ? cs() || group("{") : group("{");
      if (!ok) { at = keep; break; }
    }
    return Math.max(at, c.to);
  }

  // The numbers the PDF will print, where they can be known cheaply: a main file's figures and tables (continuous, by
  // chapter or as 版式 writes them by section), sections, footnotes, and what each \label names. A chapter file, a form
  // not read here, anything after an \input or \appendix: unknown (null), said in words instead.
  function numbersOf(text, read) {
    const out = { fig: new Map(), tab: new Map(), notes: new Map(), labels: new Map(), sig: "" };
    if (!read.preamble) return out;
    const pre = masked(text.slice(0, read.preamble.to));
    const cm = /\\documentclass\s*(?:\[[^\]]*\])?\s*\{\s*([^{}\s]+)\s*\}/.exec(pre), chapters = !!cm && CHAPTER_CLASSES.has(cm[1]);
    const mode = (w) => {
      if (new RegExp("\\\\(?:numberwithin|@addtoreset)\\s*(?:\\[[^\\]]*\\])?\\s*\\{" + w + "\\}").test(pre)) return null;
      const within = new RegExp("\\\\counterwithin\\s*\\{" + w + "\\}\\s*\\{(\\w+)\\}").exec(pre), without = new RegExp("\\\\counterwithout\\s*\\{" + w + "\\}").test(pre);
      const the = new RegExp("\\\\renewcommand\\s*\\{?\\\\the" + w + "\\}?\\s*\\{(.*)\\}\\s*$", "m").exec(pre);
      if (the) {
        if (within && within[1] === "section" && the[1].replace(/\s/g, "") === "\\arabic{section}-\\arabic{" + w + "}") return "section-";
        if (chapters && the[1].replace(/\s/g, "") === "\\thechapter-\\arabic{" + w + "}") return "chapter-";
        return null;
      }
      if (within) return null;
      return chapters && !without ? "chapter." : "";
    };
    const modes = { figure: mode("figure"), table: mode("table") };
    const secOk = !/\\(?:setcounter\s*\{\s*secnumdepth|renewcommand\s*\{?\\the(?:chapter|section|subsection|subsubsection)\b|ctexset|titleformat|usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*titlesec)/.test(pre);
    const depth = chapters ? 2 : 3, levels = chapters ? ["chapter", "section", "subsection"] : ["section", "subsection", "subsubsection"];
    const counts = { chapter: 0, section: 0, subsection: 0, subsubsection: 0, figure: 0, table: 0, note: 0 };
    const floats = read.envs.filter((e) => e.endFrom < e.to && (FIGURE_FLOATS.has(e.name) || TABLE_FLOATS.has(e.name)));
    let lost = false, target = null;
    const secNo = (lvl) => { const k = levels.indexOf(lvl); return k < 0 ? null : levels.slice(0, k + 1).map((l) => counts[l]).join("."); };
    for (const c of read.cmds) {
      if (c.from < read.preamble.to) continue;
      if (INCLUDE_CMDS.has(c.name) || c.name === "appendix") { lost = true; target = null; continue; }
      if (c.role === "heading") {
        const star = c.nameTo > c.from + 1 + c.name.length;
        if (star) { target = null; continue; }
        if (c.name === "chapter") { counts.chapter += 1; counts.section = counts.subsection = counts.subsubsection = 0; if (chapters) { counts.figure = modes.figure === "chapter." || modes.figure === "chapter-" ? 0 : counts.figure; counts.table = modes.table === "chapter." || modes.table === "chapter-" ? 0 : counts.table; counts.note = 0; } }
        else if (c.name === "section") { counts.section += 1; counts.subsection = counts.subsubsection = 0; if (modes.figure === "section-") counts.figure = 0; if (modes.table === "section-") counts.table = 0; }
        else if (c.name === "subsection") { counts.subsection += 1; counts.subsubsection = 0; }
        else if (c.name === "subsubsection") counts.subsubsection += 1;
        const k = levels.indexOf(c.name);
        target = !lost && secOk && k >= 0 && k < depth + (chapters ? 0 : 0) + 1 && k <= depth ? { kind: c.name === "chapter" ? "chapter" : "section", n: secNo(c.name) } : null;
        continue;
      }
      if (c.name === "caption" && c.args.some((a) => !a.opt) && c.nameTo === c.from + 8) {
        const f = floats.filter((e) => e.beginTo <= c.from && c.to <= e.endFrom).pop();
        if (!f) continue;
        const kind = FIGURE_FLOATS.has(f.name) ? "figure" : "table";
        counts[kind] += 1;
        const m = modes[kind], n = lost || m == null ? null : m === "" ? String(counts[kind]) : m === "chapter." ? counts.chapter + "." + counts[kind]
          : m === "chapter-" ? counts.chapter + "-" + counts[kind] : counts.section + "-" + counts[kind];
        const map = kind === "figure" ? out.fig : out.tab;
        if (!map.has(f.from)) map.set(f.from, n);
        target = n == null ? null : { kind: kind, n: n };
        continue;
      }
      if (c.name === "footnote" && c.args.some((a) => !a.opt)) { counts.note += 1; out.notes.set(c.from, lost ? null : String(counts.note)); continue; }
      if (c.role === "item" && c.list && c.list.name === "enumerate") { target = null; continue; }
      if (c.name === "label" && c.args.length) {
        const key = text.slice(c.args[0].from + 1, c.args[0].to - 1).trim();
        if (key && !out.labels.has(key)) out.labels.set(key, target);
      }
    }
    out.sig = JSON.stringify([Array.from(out.fig.values()), Array.from(out.tab.values()), Array.from(out.notes.values()), Array.from(out.labels)]);
    return out;
  }

  // What a \cite chip says: author and year from the project's .bib (`cites`, from each key to its words), else the key.
  function citeWords(keys, cites) { return keys.map((k) => (cites && cites.get(k)) || k).join(t("；")); }
  function refWords(name, keys, num) {
    return keys.map((k) => {
      const x = name === "pageref" ? null : num.labels.get(k);
      if (!x) return t("引用：{key}", { key: k });
      return x.kind === "figure" ? t("图 {n}", { n: x.n }) : x.kind === "table" ? t("表 {n}", { n: x.n }) : x.kind === "chapter" ? t("第 {n} 章", { n: x.n }) : t("第 {n} 节", { n: x.n });
    }).join(t("、"));
  }
  // The visible character of an accent: the letter with its combining mark, composed.
  function accented(mark, letter) { return (letter.replace(/^\\i$/, "ı") + mark).normalize("NFC"); }
  const CJK_ONE = /[぀-ヿ㐀-䶿一-鿿豈-﫿　-〿＀-￯]/;

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
  const samePair = (map) => (a, b) => map(a.from) === b.from && map(a.to) === b.to;

  // Where the edit view must be drawn again after `tr`: around each change and every construct read differently from
  // before, widened to whole lines and to every construct they reach, so none crosses an edge. Null when nothing is.
  function redraw(old, read, tr, extents) {
    if (!tr.docChanged) return null;
    const doc = tr.state.doc, ch = tr.changes, map = (x) => ch.mapPos(x), spans = [];
    ch.iterChangedRanges((fa, ta, fb, tb) => spans.push([fb, tb]));
    differ(old.cmds, read.cmds, (a, b) => sameCmd(a, b, map), wholeParts, map, spans);
    differ(old.envs, read.envs, (a, b) => map(a.from) === b.from && map(a.beginTo) === b.beginTo && map(a.endFrom) === b.endFrom && map(a.to) === b.to,
      // an environment whose lines take a class (a list, a quote …) is drawn again whole when it changes
      (e, m) => (wholeEnv(e) || ((e.role === "list" || e.role === "text") && e.name !== "document") ? wholeParts(e, m) : envParts(e, m)), map, spans);
    differ(old.maths, read.maths, (a, b) => map(a.from) === b.from && map(a.to) === b.to && a.display === b.display, wholeParts, map, spans);
    differ(old.groups, read.groups, (a, b) => a.name === b.name && map(a.from) === b.from && map(a.nameTo) === b.nameTo && map(a.to) === b.to, wholeParts, map, spans);
    differ(old.raws, read.raws, samePair(map), wholeParts, map, spans);
    differ(old.braces, read.braces, samePair(map), (x, m) => [[m(x.from), m(x.from + 1)], [m(x.to - 1), m(x.to)]], map, spans);
    differ(old.symbols, read.symbols, samePair(map), wholeParts, map, spans);
    if (JSON.stringify(old.meta) !== JSON.stringify(read.meta)) for (const c of read.cmds) if (c.name === "maketitle") spans.push([c.from, c.nameTo]);
    const preWas = old.preamble ? map(old.preamble.to) : -1, preNow = read.preamble ? read.preamble.to : -1;
    if (preWas !== preNow) spans.push([0, Math.max(preWas, preNow, 0)]);
    const lines = (list) => {
      const out = [];
      list.map((s) => [doc.lineAt(Math.min(s[0], doc.length)).from, doc.lineAt(Math.min(s[1], doc.length)).to]).sort((x, y) => x[0] - y[0]).forEach((s) => {
        const last = out[out.length - 1];
        if (last && s[0] <= last[1] + 1) last[1] = Math.max(last[1], s[1]); else out.push(s);
      });
      return out;
    };
    let w = lines(spans);
    for (let pass = 0; pass < 12; pass++) {
      let grew = false;
      const reach = (from, to) => {
        for (const s of w) if (from <= s[1] && to >= s[0] && (from < s[0] || to > s[1])) { s[0] = Math.min(s[0], from); s[1] = Math.max(s[1], to); grew = true; }
      };
      if (read.preamble) reach(0, read.preamble.to);
      for (const c of read.cmds) reach(c.from, c.to);
      for (const e of read.envs) { if (wholeEnv(e)) reach(e.from, e.to); else { reach(e.from, e.beginTo); reach(e.endFrom, e.to); } }
      for (const f of read.maths) reach(f.from, f.to);
      for (const g of read.groups) reach(g.from, g.to);
      for (const r of read.raws) reach(r.from, r.to);
      for (const x of extents) reach(x[0], x[1]);
      // a pair of braces drawn again at one end is drawn again at the other: what it is (an argument, a group) is one
      const ends = [];
      for (const b of read.braces) {
        const a = w.some((s) => b.from <= s[1] && b.from + 1 >= s[0]), z = w.some((s) => b.to - 1 <= s[1] && b.to >= s[0]);
        if (a !== z) ends.push(a ? [b.to - 1, b.to] : [b.from, b.from + 1]);
      }
      if (ends.length) { w = lines(w.concat(ends)); continue; }
      if (!grew) return w;
      w = lines(w);
    }
    return [[0, doc.length]];
  }

  let wx = null;
  function wordField(CM) {
    if (wx && wx.CM === CM) return wx;
    // `mathOn`: KaTeX has arrived (formulas are typeset); `refresh`: what a chip says changed (the .bib was read).
    // `preamble` and `live` are kept for callers of the earlier rule and change nothing.
    const mathOn = CM.StateEffect.define(), refresh = CM.StateEffect.define(), preamble = CM.StateEffect.define(), live = CM.StateEffect.define();
    const D = CM.Decoration;
    // A click on an atom selects it whole (the right column then says where it is changed).
    const selectOnClick = (el, view, block) => {
      if (!el || typeof el.addEventListener !== "function") return el;
      el.addEventListener("mousedown", (event) => {
        if (event.button || !view || typeof view.posAtDOM !== "function") return;
        event.preventDefault();
        const at = view.posAtDOM(el), value = view.state.field(field, false);
        let range = null;
        if (value) value.deco.between(at, at + 1, (from, to, v) => { if (!range && from === at && v.spec.atom) range = [from, to]; });
        if (range) view.dispatch({ selection: block ? { anchor: range[0], head: range[1] } : { anchor: range[0], head: range[1] }, userEvent: "select.atom" });
        if (typeof view.focus === "function") view.focus();
      });
      return el;
    };
    class TextW extends CM.WidgetType {
      constructor(text, cls, plain) { super(); this.text = text; this.cls = cls || ""; this.plain = plain == null ? text : plain; }
      eq(o) { return o.text === this.text && o.cls === this.cls; }
      toDOM() { return mk("span", this.cls || "latex-text", this.text); }
    }
    class ChipW extends CM.WidgetType {
      constructor(text, kind, block) { super(); this.text = text; this.kind = kind; this.block = !!block; this.plain = text; }
      eq(o) { return o.text === this.text && o.kind === this.kind && o.block === this.block; }
      toDOM(view) { return selectOnClick(mk(this.block ? "div" : "span", "latex-chip latex-chip-" + this.kind + (this.block ? " latex-chip-block" : ""), this.text), view); }
      ignoreEvent() { return true; }
    }
    class MathW extends CM.WidgetType {
      constructor(src, display, whole, k) { super(); this.src = src; this.display = display; this.whole = whole; this.k = k; this.plain = src; }
      eq(o) { return o.whole === this.whole && o.k === this.k; }
      toDOM(view) {
        // never the code: until KaTeX is in, and for a formula it cannot typeset (mathDOM's own), a quiet word
        const el = this.k ? mathDOM(this.src, this.display, this.whole) : mk(this.display ? "div" : "span", "latex-math-tag", t("公式"));
        return selectOnClick(el, view);
      }
      ignoreEvent() { return true; }
    }
    class NoteW extends CM.WidgetType {
      constructor(n) { super(); this.n = n; this.plain = ""; }
      eq(o) { return o.n === this.n; }
      toDOM(view) { return selectOnClick(mk("sup", "latex-note-mark", this.n || t("注")), view); }
      ignoreEvent() { return true; }
    }
    class BulletW extends CM.WidgetType {
      constructor(n, term) { super(); this.n = n; this.term = term; this.plain = term ? term + " " : ""; }
      eq(o) { return o.n === this.n && o.term === this.term; }
      toDOM() {
        if (this.term != null) return mk("span", "latex-item latex-term", this.term);
        return mk("span", this.n ? "latex-item" : "latex-item latex-bullet", this.n ? this.n + "." : "");
      }
    }
    class VerbW extends CM.WidgetType {
      constructor(text, block) { super(); this.text = text; this.block = block; this.plain = text; }
      eq(o) { return o.text === this.text && o.block === this.block; }
      toDOM(view) { return selectOnClick(mk(this.block ? "pre" : "span", "latex-verbatim", this.text), view); }
      ignoreEvent() { return true; }
    }
    class GraphicW extends CM.WidgetType {
      constructor(path) { super(); this.path = path; this.plain = ""; }
      eq(o) { return o.path === this.path; }
      toDOM(view) { return selectOnClick(graphicDOM(this.path, "span"), view); }
      ignoreEvent() { return true; }
    }
    // a figure's images and the word before its caption (「图 N：」), the caption itself editable after it
    class FigW extends CM.WidgetType {
      constructor(paths, word) { super(); this.paths = paths; this.word = word; this.plain = ""; }
      eq(o) { return o.word === this.word && o.paths.join("\n") === this.paths.join("\n"); }
      toDOM(view) {
        const box = mk("span", "latex-figure-part");
        this.paths.forEach((p) => box.appendChild(graphicDOM(p, "span")));
        if (this.word) box.appendChild(mk("span", "latex-caption-word", this.word));
        return selectOnClick(box, view);
      }
      ignoreEvent() { return true; }
    }
    // a block of the page drawn from `make`, compared by `key`; `onClick` (the title block: 文档信息) or selected whole
    class BlockW extends CM.WidgetType {
      constructor(key, make, plain, onClick) { super(); this.key = key; this.make = make; this.plain = plain || ""; this.onClick = onClick || null; }
      eq(o) { return o.key === this.key; }
      toDOM(view) {
        const el = this.make();
        if (this.onClick && typeof el.addEventListener === "function") {
          el.addEventListener("mousedown", (event) => { if (!event.button) { event.preventDefault(); this.onClick(view); } });
          return el;
        }
        return selectOnClick(el, view, true);
      }
      ignoreEvent() { return true; }
    }
    const hooks = { title: null, preamble: null };   // set by the editor: what a click on the title block and the 导言区 line does
    const marks = new Map();
    const mark = (cls) => { if (!marks.has(cls)) marks.set(cls, D.mark({ class: cls })); return marks.get(cls); };
    const lines = new Map();
    const lineDeco = (cls) => { if (!lines.has(cls)) lines.set(cls, D.line({ class: cls })); return lines.get(cls); };

    // The pieces of the edit view that meet `within` ([from, to], whole lines; all when null).
    function viewPieces(doc, read, info, within) {
      const text = info.text, len = text.length, num = info.num;
      const W0 = within ? Math.max(0, within[0] - 1) : 0, W1 = within ? Math.min(len, within[1] + 1) : len;
      const near = (from, to) => from <= W1 && to >= W0;
      const cand = [];
      const atom = (from, to, widget, extra) => { if (to > from) cand.push(Object.assign({ from: from, to: to, widget: widget || null, atom: true }, extra || {})); };
      const lineOf = (pos) => doc.lineAt(Math.min(Math.max(pos, 0), len));
      const blank = (l) => !l.text.trim();
      const commentOnly = (l) => { const at = codePart(l.text); return at.length < l.text.length && !at.trim(); };
      const alone = (from, to) => aloneOn(doc, from, to) && !codePart(text.slice(to, lineOf(to).to)).trim();
      const word = (s) => (s == null ? null : plainWords(s));

      // the preamble and \begin{document}: one quiet line (版式 edits it; 显示代码 shows it); \end{document} and after
      const docEnv = read.preamble ? read.envs.find((e) => e.name === "document" && e.from === read.preamble.to) : null;
      const bodyFrom = docEnv ? docEnv.beginTo : read.preamble ? read.preamble.to : 0, bodyTo = docEnv && docEnv.endFrom < docEnv.to ? docEnv.endFrom : len;
      if (docEnv) {
        const bl = lineOf(docEnv.beginTo), whole = !codePart(text.slice(docEnv.beginTo, bl.to)).trim();
        const words = preambleWords(read.meta);
        atom(0, whole ? bl.to : docEnv.beginTo, new BlockW("pre" + words, () => {
          const box = mk("div", "latex-preamble-block"), b = mk("button", "latex-preamble", words);
          b.setAttribute("type", "button");
          box.append(b, mk("div", "latex-doc-mark", t("正文开始")));
          return box;
        }, "", () => { if (hooks.preamble) hooks.preamble(); }), { block: whole, lead: true, kind: "preamble" });
        if (docEnv.endFrom < docEnv.to) {
          const el = lineOf(docEnv.endFrom), own = !text.slice(el.from, docEnv.endFrom).trim();
          atom(own ? el.from : docEnv.endFrom, len, new BlockW("de", () => mk("div", "latex-doc-mark", t("正文结束"))), { block: own, trail: true, kind: "end" });
        }
      }
      // comments: hidden; a line that is only a comment leaves no trace (nor does a paragraph of them)
      const claimed = new Set();
      const runs = new Set();
      for (const r of read.raws) {
        if (r.kind !== "comment" || !near(r.from - 1, r.to + 1)) continue;
        const l = lineOf(r.from);
        if (!commentOnly(l)) { atom(r.from, r.to, null, { kind: "comment" }); continue; }
        let a = l.number, b = l.number;
        while (a > 1 && commentOnly(doc.line(a - 1))) a -= 1;
        while (b < doc.lines && commentOnly(doc.line(b + 1))) b += 1;
        if (runs.has(a)) continue;
        runs.add(a);
        const first = doc.line(a), last = doc.line(b), next = b < doc.lines;
        const before = a > 1 && blank(doc.line(a - 1)) && next;
        const from = before ? first.from - 1 : first.from, to = next ? last.to + 1 : last.to;
        if (before) claimed.add(first.from - 1);
        if (next) claimed.add(last.to);
        atom(from, to, null, { kind: "comment" });
      }
      // verbatim text, shown as it is (it is the researcher's, not code); the comment environment hidden
      for (const r of read.raws) {
        if (r.kind === "comment" || !near(r.from, r.to)) continue;
        if (r.kind === "verb") {
          const star = text[r.from + 5] === "*" ? 1 : 0;
          atom(r.from, r.to, new VerbW(text.slice(r.from + 6 + star, r.to - 1), false), { kind: "verb" });
          continue;
        }
        const first = lineOf(r.from), last = lineOf(r.to), own = alone(r.from, r.to);
        if (/^\\begin\s*\{comment\}/.test(text.slice(r.from, r.from + 20))) { atom(own ? first.from : r.from, own && last.number < doc.lines ? last.to + 1 : r.to, null, { kind: "comment" }); continue; }
        const inner = first.number < last.number ? doc.sliceString(doc.line(first.number + 1).from, Math.max(doc.line(first.number + 1).from, last.from - 1)) : "";
        atom(own ? first.from : r.from, own ? last.to : r.to, new VerbW(inner, own), { block: own, kind: "verbatim" });
      }
      // formulas, typeset (or a quiet 公式 until KaTeX is in or where it cannot typeset): changed only in 显示代码
      for (const f of read.maths) if (near(f.from, f.to)) atom(f.from, f.to, new MathW(f.src, f.display, text.slice(f.from, f.to), info.math), { kind: "math" });
      // floats and other environments the edit view does not open
      const floatEnds = [];   // the floats open before an environment, by their end (environments come in order)
      for (const e of read.envs) {
        while (floatEnds.length && floatEnds[floatEnds.length - 1] <= e.from) floatEnds.pop();
        const inFloat = floatEnds.length > 0;
        if (e.endFrom < e.to && (TABLE_FLOATS.has(e.name) || FIGURE_FLOATS.has(e.name))) floatEnds.push(e.to);
        if (!(e.endFrom < e.to) || !near(e.from, e.to) || e.role === "text" || e.role === "list") continue;
        const own = alone(e.from, e.to), from = own ? lineOf(e.from).from : e.from, to = own ? lineOf(e.to).to : e.to;
        if (TABLE_FLOATS.has(e.name) || (TABULARS.has(e.name) && !inFloat)) {
          const model = tableModel(doc, read, e), n = num.tab.get(e.from), wordOf = n ? t("表 {n}：", { n: n }) : t("表：");
          const widget = model ? new BlockW(info.typeset + "t" + wordOf + text.slice(e.from, e.to), () => tableDOM(model, wordOf), "")
            : new ChipW(TABLE_FLOATS.has(e.name) ? floatWords(read, e, t("表格"), text) : t("表格"), "unit", own);
          atom(from, to, widget, { block: own, kind: "table" });
          continue;
        }
        if (FIGURE_FLOATS.has(e.name)) {
          const model = figureModel(doc, read, e);
          const caps = read.cmds.filter((c) => c.name === "caption" && c.from > e.beginTo && c.to <= e.endFrom);
          const cap = caps.length === 1 && caps[0].nameTo === caps[0].from + 8 ? caps[0].args.filter((a) => !a.opt) : [];
          const n = num.fig.get(e.from), wordOf = n ? t("图 {n}：", { n: n }) : t("图：");
          if (model && cap.length === 1) {
            const a = cap[0], pics = read.cmds.filter((c) => c.name === "includegraphics" && c.from > e.beginTo && c.to <= e.endFrom);
            const pathOf = (c) => { const x = c.args.filter((y) => !y.opt); return text.slice(x[0].from + 1, x[0].to - 1).trim(); };
            const before = pics.filter((c) => c.from < a.from).map(pathOf), after = pics.filter((c) => c.from > a.to).map(pathOf);
            atom(from, a.from + 1, new FigW(before, wordOf), { kind: "figure", slotTo: true, frame: { from: from, to: to, openTo: a.from + 1, closeFrom: a.to - 1, kind: "figure" } });
            atom(a.to - 1, to, after.length ? new FigW(after, null) : null, { kind: "figure", slotFrom: true });
            continue;
          }
          atom(from, to, model ? new BlockW(info.typeset + "f" + wordOf + text.slice(e.from, e.to), () => figureDOM(model, wordOf), "") : new ChipW(floatWords(read, e, t("图"), text), "unit", own), { block: own, kind: "figure" });
          continue;
        }
        if (e.role === "raw" && !TABULARS.has(e.name)) atom(from, to, new ChipW(t("LaTeX 内容"), "unit", own), { block: own, kind: "unit" });
      }
      // commands
      const bareArgs = new Set();
      for (const c of read.cmds) {
        if (!near(c.from, Math.max(c.to, c.nameTo + 1)) && !DEFINITIONS.has(c.name)) continue;
        const n = c.name, args = c.args.filter((a) => !a.opt), arg = (k) => text.slice(args[k].from + 1, args[k].to - 1);
        if (c.role === "heading" || c.role === "style") continue;   // frames, below
        if (c.group) continue;
        if (DEFINITIONS.has(n)) { const end = definitionEnd(text, c); if (near(c.from, end)) atom(c.from, end, new ChipW(t("LaTeX 内容"), "unit", false), { kind: "unit" }); continue; }
        if (c.role === "cite") {
          const keys = arg(0).split(",").map((k) => k.trim()).filter(Boolean);
          atom(c.from, c.to, new ChipW(citeWords(keys, info.cites), "cite"), { kind: "cite" });
          continue;
        }
        if (HIDDEN_CMDS.has(n) && args.length) { hideLine(c.from, c.to); continue; }
        if ((c.role === "ref" || REF_LIKE.test(n)) && args.length) { atom(c.from, c.to, new ChipW(refWords(n, arg(0).split(",").map((k) => k.trim()).filter(Boolean), num), "ref"), { kind: "ref" }); continue; }
        if (c.role === "item") {
          if (!c.list) atom(c.from, c.nameTo, new ChipW(t("LaTeX 内容"), "unit"), { kind: "unit" });
          continue;   // list items: below
        }
        if (n === "footnote" && args.length === 1) { atom(c.from, c.to, new NoteW(num.notes.get(c.from) || null), { kind: "footnote" }); continue; }
        if (n === "url" && args.length === 1) { atom(c.from, c.to, new ChipW(arg(0).trim(), "url"), { kind: "url" }); continue; }
        if (n === "href" && args.length === 2) { atom(c.from, c.to, new ChipW(word(arg(1)) || t("链接"), "url"), { kind: "url" }); continue; }
        if (n === "includegraphics" && args.length === 1) { atom(c.from, c.to, new GraphicW(arg(0).trim()), { kind: "graphic" }); continue; }
        if (INCLUDE_CMDS.has(n) && args.length) { atom(c.from, c.to, new ChipW(t("引入 {file}", { file: args.map((a, k) => arg(k).trim()).join("/") }), "unit"), { kind: "unit" }); continue; }
        if (n === "maketitle" && !c.args.length) {
          const meta = { title: read.meta.title, author: read.meta.author, date: read.meta.date, own: !!read.preamble };
          if (alone(c.from, c.nameTo)) {
            const l = lineOf(c.from);
            atom(l.from, l.to, new BlockW(info.typeset + "t" + JSON.stringify(meta), () => titleDOM(meta), "", (view) => { if (hooks.title) hooks.title(view); }), { block: true, kind: "title" });
          } else atom(c.from, c.nameTo, new ChipW(t("标题"), "unit"), { kind: "unit" });
          continue;
        }
        if (Object.prototype.hasOwnProperty.call(LINE_WORDS, n)) {
          const words = LINE_WORDS[n]();
          if (alone(c.from, c.to)) { const l = lineOf(c.from); atom(l.from, l.to, new BlockW("l" + words, () => mk("div", "latex-line-mark", words)), { block: true, kind: "line" }); }
          else atom(c.from, c.to, new ChipW(words, "unit"), { kind: "unit" });
          continue;
        }
        if (n === "today") { atom(c.from, c.to, new ChipW(t("导出当天的日期"), "unit"), { kind: "unit" }); continue; }
        if (TEXT_MACROS.has(n)) {   // \LaTeX{}: its "{}" with it; any group after that is a group of its own, not an argument
          const empty = !!c.args[0] && !c.args[0].opt && c.args[0].to - c.args[0].from === 2;
          atom(c.from, empty ? c.args[0].to : c.nameTo, new TextW(TEXT_MACROS.get(n)), { kind: "text" });
          c.args.forEach((a, k) => { if (!(empty && k === 0)) bareArgs.add(a.from); });
          continue;
        }
        if (ACCENTS[n] && args.length === 1 && /^\s*(?:\\i|[A-Za-z])\s*$/.test(arg(0))) { atom(c.from, c.to, new TextW(accented(ACCENTS[n], arg(0).trim())), { kind: "text" }); continue; }
        if (BREAKS.has(n)) { atom(c.from, c.to, new TextW("", "latex-br", "\n"), { kind: "break" }); continue; }
        if (SILENT.has(n) || GROUP_CLASSES.has(n)) {
          c.args.forEach((a) => bareArgs.add(a.from));
          if (alone(c.from, c.nameTo) && !c.args.length) hideLine(c.from, c.nameTo);
          else atom(c.from, c.nameTo + (text[c.nameTo] === " " && !c.args.length ? 1 : 0), null, { kind: "hidden" });
          continue;
        }
        if (SILENT_ARGS.has(n)) { if (alone(c.from, c.to)) hideLine(c.from, c.to); else atom(c.from, c.to, null, { kind: "hidden" }); continue; }
        const wrap = WRAPPERS.get(n);
        if (wrap && args.length === wrap[1] && c.args[c.args.length - 1] === args[args.length - 1]) continue;   // frames, below
        if (n === "caption" && args.length === 1) { atom(c.from, c.to, new ChipW(word(arg(0)) || t("LaTeX 内容"), "unit"), { kind: "unit" }); continue; }
        // anything else: one quiet chip, its plain words (never the command's name), or 「LaTeX 内容」
        const words = c.args.length ? c.args.map((a) => word(text.slice(a.from + 1, a.to - 1))) : [];
        const said = words.length && words.every((w) => w != null) ? words.filter(Boolean).join(" ") : "";
        atom(c.from, c.to, new ChipW(said || t("LaTeX 内容"), "unit"), { kind: "unit" });
      }
      // a line that only holds what is hidden (a \label, \noindent …) leaves no trace, its line break with it
      function hideLine(from, to) {
        const l = lineOf(from);
        if (alone(from, to) && !text.slice(to, l.to).trim() && l.number < doc.lines && !claimed.has(l.to)) { claimed.add(l.to); atom(l.from, l.to + 1, null, { kind: "hidden" }); }
        else atom(from, to, null, { kind: "hidden" });
      }
      // one-character commands and ties
      for (const s of read.symbols) {
        if (!near(s.from, s.to + 2)) continue;
        const ch = s.ch;
        if (ch === "\\") {
          let to = s.to;
          if (text[to] === "*") to += 1;
          const sp = skipSpaces(text, to);
          if (text[sp] === "[") { const g = skipGroup(text, sp, true); if (g > 0) to = g; }
          const eol = !text.slice(to, lineOf(to).to).trim();
          atom(s.from, eol ? lineOf(to).to : to, eol ? null : new TextW("", "latex-br", "\n"), { kind: "break" });
          continue;
        }
        if (Object.prototype.hasOwnProperty.call(SYMBOL_TEXT, ch)) { atom(s.from, s.to, new TextW(SYMBOL_TEXT[ch]), { kind: "text" }); continue; }
        if (SYMBOL_SILENT.has(ch)) { atom(s.from, s.to, null, { kind: "hidden" }); continue; }
        if (ACCENTS[ch]) {
          const m = /^(?:\{\s*(\\i|[A-Za-z])\s*\}|(\\i(?![A-Za-z])|[A-Za-z]))/.exec(text.slice(s.to, s.to + 8));
          if (m) { atom(s.from, s.to + m[0].length, new TextW(accented(ACCENTS[ch], m[1] || m[2])), { kind: "text" }); continue; }
        }
        atom(s.from, s.to, new ChipW(t("LaTeX 内容"), "unit"), { kind: "unit" });
      }
      for (const p of read.tildes) if (near(p, p + 1)) atom(p, p + 1, new TextW(" "), { kind: "text" });

      // the atoms kept: outermost first, none inside another
      cand.sort((a, b) => a.from - b.from || b.to - a.to);
      const kept = [];
      let reach = -1;
      for (const p of cand) { if (p.from < reach) continue; kept.push(p); reach = p.to; }
      const inside = (pos) => {
        let lo = 0, hi = kept.length - 1;
        while (lo <= hi) { const mid = (lo + hi) >> 1; if (kept[mid].to <= pos) lo = mid + 1; else if (kept[mid].from > pos) hi = mid - 1; else return kept[mid]; }
        return null;
      };
      const blockLine = (l) => { const k = inside(l.from); return !!k && k.block && k.from <= l.from && k.to >= l.to; };
      // [from, to) cut short at the first atom kept in it (a comment after a \begin on its line)
      const upTo = (from, to) => {
        let lo = 0, hi = kept.length;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (kept[mid].from < from) lo = mid + 1; else hi = mid; }
        return lo < kept.length && kept[lo].from < to ? kept[lo].from : to;
      };

      // frames: what keeps its words editable, its markup hidden
      const out = kept.slice(), marksOut = [], linesOut = [];
      const hide = (from, to, extra) => { if (to > from) out.push(Object.assign({ from: from, to: to, widget: null, atom: true }, extra || {})); };
      const markupRanges = [];
      for (const c of read.cmds) {
        if (!near(c.from, c.to) || inside(c.from)) continue;
        const wrap = WRAPPERS.get(c.name), args = c.args.filter((a) => !a.opt);
        let cls = null;
        if ((c.role === "heading" || c.role === "style") && args.length && c.args[c.args.length - 1] === args[args.length - 1]) cls = c.role === "heading" ? "latex-h latex-h" + HEADINGS.get(c.name) : STYLES.get(c.name);
        else if (wrap && !c.group && args.length === wrap[1] && c.args[c.args.length - 1] === args[args.length - 1] && !SILENT.has(c.name)) cls = wrap[0];
        else continue;
        const a = c.args[c.args.length - 1], l = lineOf(c.from), lead = c.role === "heading" && !text.slice(l.from, c.from).trim();
        const rest = text.slice(c.to, lineOf(c.to).to).replace(/\\label\s*\{[^{}]*\}/g, "");
        const short = c.role === "heading" && c.args.length > 1 && c.args[0].opt ? word(text.slice(c.args[0].from + 1, c.args[0].to - 1)) : null;
        hide(lead ? l.from : c.from, a.from + 1, { kind: "open", lead: lead, slotTo: true });
        hide(a.to - 1, c.to, { kind: "close", slotFrom: true, trail: c.role === "heading" && !codePart(rest).trim(),
          widget: short ? new TextW(t("（目录中：{words}）", { words: short }), "latex-short", "") : null });
        if (a.to - 1 > a.from + 1 && cls) marksOut.push([a.from + 1, a.to - 1, cls]);
        if (c.role === "heading") linesOut.push([l.from, "latex-hline"]);
        markupRanges.push([c.from, a.from + 1], [a.to - 1, c.to]);
      }
      for (const g of read.groups) {
        if (!near(g.from, g.to) || inside(g.from)) continue;
        if (groupShown(read, g)) {
          hide(g.from, g.nameTo, { kind: "open", slotTo: true });
          hide(g.to - 1, g.to, { kind: "close", slotFrom: true });
          if (g.to - 1 > g.nameTo) marksOut.push([g.nameTo, g.to - 1, GROUP_CLASSES.get(g.name)]);
        } else { hide(g.from, g.nameTo); hide(g.to - 1, g.to); }
        markupRanges.push([g.from, g.nameTo], [g.to - 1, g.to]);
      }
      // lists and environments of running text: \begin and \end hidden (a line of its own hides its line break before
      // it, so the lines around keep their places); items a bullet, a number or a description's term
      const swallow = (l) => l.number > 1 && !claimed.has(l.from - 1) && !blockLine(doc.line(l.number - 1)) && !inside(l.from - 1);
      for (const e of read.envs) {
        if ((e.role !== "list" && e.role !== "text") || e.name === "document" || !(e.endFrom < e.to) || !near(e.from, e.to) || inside(e.from)) continue;
        let argsEnd = e.beginTo;
        for (const s of ENV_ARGS[e.name] || (e.role === "text" && e.name !== "abstract" ? "o" : "")) {
          if ((s === "o" && text[argsEnd] === "[") || (s === "m" && text[argsEnd] === "{")) { const g = skipGroup(text, argsEnd, true); if (g > 0) argsEnd = g; }
        }
        const items = e.role === "list" ? e.itemCmds : [];
        const bl = lineOf(e.from), bOwn = alone(e.from, argsEnd), el = lineOf(e.endFrom), eOwn = alone(e.endFrom, e.to);
        const titleOf = e.role === "text" && text[e.beginTo] === "[" ? word(text.slice(e.beginTo + 1, argsEnd - 1)) : null;
        const label = e.name === "abstract" ? t("摘要") : e.name === "proof" ? t("证明") : titleOf ? titleOf : null;
        const head = label ? new TextW(label, e.name === "abstract" ? "latex-abstract-head" : "latex-env-head", label + "\n") : null;
        // typed at the end of \begin, words go to the first item, or the first line inside
        const fwd = items.length ? itemEnd(items[0]) : bOwn && bl.to + 1 < e.endFrom ? bl.to + 1 : null;
        if (bOwn) {
          const sw = swallow(bl);
          if (sw) claimed.add(bl.from - 1);
          hide(sw ? bl.from - 1 : bl.from, upTo(bl.from, bl.to), { kind: "begin", widget: head, fwd: fwd });
        } else hide(e.from, argsEnd, { kind: "begin", widget: head, fwd: fwd, lead: !text.slice(bl.from, e.from).trim() });
        if (eOwn) {
          const sw = swallow(el) && el.from - 1 > (bOwn ? bl.to : argsEnd);
          if (sw) claimed.add(el.from - 1);
          hide(sw ? el.from - 1 : el.from, upTo(el.from, el.to), { kind: "end", trail: true });
        } else hide(e.endFrom, e.to, { kind: "end", trail: true });
        markupRanges.push([e.from, argsEnd], [e.endFrom, e.to]);
        for (const m of items) {
          if (inside(m.from)) continue;
          const ml = lineOf(m.from), lead = !text.slice(ml.from, m.from).trim();
          const opt = m.args.find((a) => a.opt), term = opt ? word(text.slice(opt.from + 1, opt.to - 1)) : null;
          hide(lead ? ml.from : m.from, itemEnd(m), { kind: "item", lead: lead, slotTo: true,
            widget: new BulletW(e.name === "enumerate" ? m.n : 0, opt ? (term == null ? t("LaTeX 内容") : term) : null) });
        }
      }
      function itemEnd(m) { const opt = m.args.find((a) => a.opt), at = opt ? opt.to : m.nameTo; return text[at] === " " ? at + 1 : at; }
      // a group of braces that is no command's argument: its braces hidden, its words kept
      const markIn = (pos) => markupRanges.some((r) => pos >= r[0] && pos < r[1]);
      for (const b of read.braces) {
        if (!near(b.from, b.to) || inside(b.from) || (read.argStarts.has(b.from) && !bareArgs.has(b.from)) || markIn(b.from)) continue;
        if (read.groups.some((g) => g.from === b.from)) continue;
        hide(b.from, b.from + 1, { kind: "brace" });
        hide(b.to - 1, b.to, { kind: "brace" });
      }
      // a paragraph's single line breaks are spaces (none between two CJK characters, as ctex sets them)
      out.sort((a, b) => a.from - b.from || a.to - b.to);
      const covered = (pos) => {
        let lo = 0, hi = out.length - 1;
        while (lo <= hi) { const mid = (lo + hi) >> 1; if (out[mid].to <= pos) lo = mid + 1; else if (out[mid].from > pos) hi = mid - 1; else return true; }
        // `out` may overlap after the frames: look at the neighbours too
        for (let k = Math.max(0, lo - 3); k < Math.min(out.length, lo + 3); k++) if (out[k].from <= pos && pos < out[k].to) return true;
        return false;
      };
      const kindOf = (l) => {
        if (blank(l)) return "blank";
        if (commentOnly(l)) return "comment";
        if (/^\s*\\item(?![A-Za-z])/.test(l.text)) return "item";
        return STRUCTURAL.test(l.text) ? "hard" : "text";
      };
      const first = lineOf(W0).number, last = lineOf(W1).number, spaces = [];
      for (let k = first; k <= last && k < doc.lines; k++) {
        const L = doc.line(k);
        if (covered(L.to)) continue;
        const kl = kindOf(L);
        if (kl !== "text" && kl !== "item") continue;
        if (/(?:^|[^\\])(?:\\\\)*\\\\\s*(?:\[[^\]]*\])?\s*$/.test(codePart(L.text))) continue;
        let j = k + 1;
        while (j <= doc.lines && commentOnly(doc.line(j))) j += 1;
        if (j > doc.lines || kindOf(doc.line(j)) !== "text") continue;
        const next = doc.line(j).text.trim()[0] || "", prev = codePart(L.text).trimEnd().slice(-1);
        spaces.push({ from: L.to, to: L.to + 1, widget: new TextW(CJK_ONE.test(prev) && CJK_ONE.test(next) ? "" : " ", "latex-text", " "), atom: true, kind: "space" });
      }
      out.push(...spaces);
      // the line classes: where a visual line starts (its line break before is not hidden), by what it is in
      out.sort((a, b) => a.from - b.from || a.to - b.to);
      const lineEnvs = read.envs.filter((e) => e.endFrom < e.to && (e.role === "list" || ENV_LINES[e.name] || (e.role === "text" && e.name !== "document")) && near(e.from, e.to));
      const lf = within ? lineOf(within[0]).number : 1, ll = within ? lineOf(within[1]).number : doc.lines;
      for (let k = lf; k <= ll; k++) {
        const L = doc.line(k);
        if (k > 1 && covered(L.from - 1)) continue;
        if (inside(L.from) && inside(L.from).from < L.from) continue;
        let depth = 0, cls = null;
        for (const e of lineEnvs) {
          if (!(e.beginTo <= L.from && L.from < e.endFrom)) continue;
          if (e.role === "list") depth += 1; else if (ENV_LINES[e.name]) cls = ENV_LINES[e.name]; else cls = cls || "latex-env-line";
        }
        if (depth) linesOut.push([L.from, "latex-li latex-li-" + Math.min(depth, 4)]);
        if (cls) linesOut.push([L.from, cls]);
        else if (!depth) { const p = paraStart(L); if (p) linesOut.push([L.from, p]); }
      }
      // where a paragraph of running text starts, for 首行缩进 (22.12 B2): its first words' line, and the line before
      // the quiet lines above it
      function paraStart(L) {
        if (L.from < bodyFrom || L.from >= bodyTo) return null;
        let C = L, gap = false;
        while (C.number < doc.lines && (quietLine(C.text) || blank(C)) && covered(C.to)) { if (blank(C)) gap = true; C = doc.line(C.number + 1); }
        if (C.from >= bodyTo || kindOf(C) !== "text" || /^\s*\\noindent(?![A-Za-z])/.test(C.text)) return null;
        // a heading's first paragraph stays so across a blank line, as TeX's \@afterheading does
        let k = L.number - 1;
        while (k >= 1 && (quietLine(doc.line(k).text) || blank(doc.line(k)))) { if (blank(doc.line(k))) gap = true; k -= 1; }
        if (k < 1 || doc.line(k).to <= bodyFrom) return "latex-para";
        if (HEADING_LINE.test(doc.line(k).text)) return "latex-para-first";
        return gap || PARA_AFTER.test(doc.line(k).text) ? "latex-para" : null;
      }
      // a figure's caption sits under its images, centred
      for (const p of out) if (p.frame && p.frame.kind === "figure") linesOut.push([lineOf(p.from).from, "latex-figure-line"]);
      // where two atoms touch and nothing may be typed between, a bridge over the place keeps the cursor from stopping
      const bridges = [];
      for (let k = 1; k < out.length; k++) {
        const a = out[k - 1], b = out[k];
        if (a.to === b.from && !a.slotTo && !b.slotFrom) bridges.push([a.to - 1, a.to + 1]);
      }
      const meets = within ? (from, to) => from <= within[1] && to >= within[0] : () => true;
      return {
        bridges: bridges.filter((x) => meets(x[0], x[1])),
        pieces: out.filter((p) => meets(p.from, p.to)),
        marks: marksOut.filter((m) => meets(m[0], m[1])),
        lines: linesOut.filter((x) => !within || (x[0] >= within[0] && x[0] <= within[1])),
      };
    }
    const bridge = D.mark({ bridge: true });
    // The decorations and the atomic ranges (each replace, and the bridges) of the pieces that meet `within`.
    function decorations(doc, read, info, within) {
      const got = viewPieces(doc, read, info, within), deco = [], atoms = [];
      const seenLines = new Set();
      for (const p of got.pieces) {
        // `fwd`: words typed at its end go further on (worked out when typed, from what is read then)
        const spec = { atom: true, kind: p.kind, lead: !!p.lead, trail: !!p.trail, fwd: p.fwd != null, slotFrom: !!p.slotFrom, slotTo: !!p.slotTo };
        if (p.widget) spec.widget = p.widget;
        if (p.block) spec.block = true;
        const r = D.replace(spec).range(p.from, p.to);
        deco.push(r);
        atoms.push(r);
      }
      for (const m of got.marks) deco.push(mark(m[2]).range(m[0], m[1]));
      for (const [pos, cls] of got.lines) { const k = pos + "\u0000" + cls; if (seenLines.has(k)) continue; seenLines.add(k); deco.push(lineDeco(cls).range(pos)); }
      for (const x of got.bridges) atoms.push(bridge.range(x[0], x[1]));
      return { deco: deco, atoms: atoms, from: got.pieces.reduce((m, p) => Math.min(m, p.from), Infinity), to: got.pieces.reduce((m, p) => Math.max(m, p.to), -1) };
    }
    // On each change the text is read again whole (3 ms on a 2 MB file, the save's limit); the decorations are mapped
    // and drawn again only where `redraw` says -- widened over every piece that meets the place, before and after, so
    // none is left half drawn -- and a key costs the same in a long file as in a short one. Nothing depends on the
    // selection: a move of the cursor changes nothing drawn.
    const infoOf = (text, read, math, cites) => ({ text: text, num: numbersOf(text, read), math: math, cites: cites, typeset: math ? "K" : "-" });
    const whole = (state, read, info, math) => {
      const d = decorations(state.doc, read, info, null);
      return { read: read, info: info, math: math, open: false, live: false, deco: D.set(d.deco, true), atoms: D.set(d.atoms, true) };
    };
    const field = CM.StateField.define({
      create: (state) => {
        const text = state.doc.toString(), read = readLatex(text), math = typeof window !== "undefined" && !!window.katex;
        return whole(state, read, infoOf(text, read, math, hooks.cites || null), math);
      },
      update(value, tr) {
        let math = value.math, again = false;
        for (const e of tr.effects) { if (e.is(mathOn)) math = true; if (e.is(refresh)) again = true; }
        if (!tr.docChanged && math === value.math && !again) return value;
        const text = tr.docChanged ? tr.state.doc.toString() : value.info.text, read = tr.docChanged ? readLatex(text) : value.read;
        const info = infoOf(text, read, math, hooks.cites || null);
        // a text the reader is unsure of (TeX would stop in it) is drawn whole: its pairs are not to be trusted far away
        if (math !== value.math || again || info.num.sig !== value.info.num.sig || read.bad.length || value.read.bad.length) return whole(tr.state, read, info, math);
        const doc = tr.state.doc, extents = read.cmds.filter((c) => DEFINITIONS.has(c.name)).map((c) => [c.from, definitionEnd(text, c)]);
        const spans = redraw(value.read, read, tr, extents);
        let deco = value.deco.map(tr.changes), atoms = value.atoms.map(tr.changes);
        const wide = (x) => [doc.lineAt(Math.max(0, Math.min(x[0], doc.length))).from, doc.lineAt(Math.max(0, Math.min(x[1], doc.length))).to];
        for (let s of spans || []) {
          let d = null;
          s = paraReach(doc, s);
          for (let n = 0; n < 6; n++) {
            // the pieces already drawn that meet the place, and those to be drawn, may reach past it: widen it to them
            let lo = s[0], hi = s[1];
            atoms.between(lo, hi, (f, t2) => { if (f < lo - 1) lo = f; if (t2 > hi + 1) hi = t2; });
            if (lo < s[0] - 1 || hi > s[1] + 1) { s = paraReach(doc, wide([Math.min(lo, s[0]), Math.max(hi, s[1])])); continue; }
            d = decorations(doc, read, info, s);
            if (d.from < s[0] - 1 || d.to > s[1] + 1) { s = paraReach(doc, wide([Math.min(d.from, s[0]), Math.max(d.to, s[1])])); d = null; continue; }
            break;
          }
          if (!d) return whole(tr.state, read, info, math);
          const keep = (from, to) => !(from <= s[1] && to >= s[0]);
          deco = deco.update({ filterFrom: s[0] - 1, filterTo: s[1] + 1, filter: keep, add: d.deco, sort: true });
          atoms = atoms.update({ filterFrom: s[0] - 1, filterTo: s[1] + 1, filter: keep, add: d.atoms, sort: true });
        }
        return { read: read, info: info, math: math, open: false, live: false, deco: deco, atoms: atoms };
      },
      provide: (f) => CM.EditorView.decorations.from(f, (v) => v.deco),
    });

    // The atoms are the replaces; those that touch are one (unless the place between is where words go: an empty
    // heading, a style's edge). Each piece says what typing at its edges means: `lead` (typed at its start, the words
    // go after it, or to `fwd`), `fwd` (typed at its end, they go there), `trail` (typed at its end, they go before it).
    function touching(value, pos) {
      const got = [];
      value.atoms.between(pos, pos, (f, t2, v) => { if (v.spec.atom) got.push({ from: f, to: t2, spec: v.spec }); });
      return got;
    }
    // The merged atom with `pos` strictly inside ("in"), starting at it ("from") or ending at it ("to"), or null:
    // {from, to, firstSpec, lastSpec}.
    function atomAt(value, pos, how) {
      const ps = touching(value, pos), left = ps.find((x) => x.to === pos && x.from < pos), right = ps.find((x) => x.from === pos && x.to > pos);
      const joined = !!left && !!right && !left.spec.slotTo && !right.spec.slotFrom;
      let p = null;
      if (how === "in") p = ps.find((x) => x.from < pos && pos < x.to) || (joined ? left : null);
      else if (how === "from") p = joined ? null : right;
      else p = joined ? null : left;
      if (!p) return null;
      let from = p.from, to = p.to, firstSpec = p.spec, lastSpec = p.spec, first = p, last = p;
      for (let n = 0; n < 400; n++) {
        const l = touching(value, from).find((x) => x.to === from && x.from < from);
        if (!l || l.spec.slotTo || firstSpec.slotFrom) break;
        from = l.from; firstSpec = l.spec; first = l;
      }
      for (let n = 0; n < 400; n++) {
        const r = touching(value, to).find((x) => x.from === to && x.to > to);
        if (!r || r.spec.slotFrom || lastSpec.slotTo) break;
        to = r.to; lastSpec = r.spec; last = r;
      }
      return { from: from, to: to, firstSpec: firstSpec, lastSpec: lastSpec, firstFwd: fwdOf(value, first), lastFwd: fwdOf(value, last) };
    }
    // Where words typed at the end of an environment's \begin go: its first item's words, or its first line.
    function fwdOf(value, p) {
      if (!p.spec.fwd) return null;
      const read = value.read, text = value.info.text, e = read.envs.find((x) => x.from >= p.from && x.from < p.to);
      if (!e) return null;
      if (e.role === "list") {
        const m = e.itemCmds[0];
        if (!m) return null;
        const opt = m.args.find((a) => a.opt), at = opt ? opt.to : m.nameTo;
        return text[at] === " " ? at + 1 : at;
      }
      const nl = text.indexOf("\n", e.beginTo);
      return nl >= 0 && nl + 1 < e.endFrom ? nl + 1 : null;
    }
    // Where typed words go when typed at `pos` (22.1: where the cursor shows them), or null where none may go.
    function typedAt(state, value, pos) {
      for (let n = 0; n < 4; n++) {
        const inner = atomAt(value, pos, "in");
        if (inner) { pos = inner.to; continue; }
        const starts = atomAt(value, pos, "from"), ends = atomAt(value, pos, "to");
        if (starts && starts.firstSpec.lead) { pos = starts.firstFwd != null && starts.firstFwd > pos ? starts.firstFwd : starts.to; continue; }
        if (ends && ends.lastFwd != null && ends.lastFwd > pos) { pos = ends.lastFwd; continue; }
        if (ends && ends.lastSpec.trail && ends.from < pos) { pos = ends.from; continue; }
        break;
      }
      const ctx = contextAt(value.read, pos);
      if (ctx && ctx.kind === "raw" && ctx.x && ctx.x.kind !== "comment" && pos === ctx.x.to) return pos;   // just after \verb|…|
      if (ctx && (ctx.kind === "math" || ctx.kind === "raw")) return null;
      if (ctx && ctx.kind === "env" && ctx.x.role === "raw" && !FIGURE_FLOATS.has(ctx.x.name)) return null;
      if (ctx && ctx.kind === "env" && ctx.x.role === "list") {
        const first = ctx.x.itemCmds[0];
        if (!first || pos < first.nameTo) return null;
      }
      return pos;
    }
    // The frames around words: {from, to, openTo, closeFrom, kind, inline} -- a style, a group, a heading, a figure's
    // caption, an environment, a list (its first \item with its \begin), braces; and the document (always kept).
    const framesCache = new WeakMap();
    function framesOf(value) {
      if (framesCache.has(value)) return framesCache.get(value);
      const read = value.read, text = value.info.text, out = [];
      for (const c of read.cmds) {
        const args = c.args.filter((a) => !a.opt), a = c.args[c.args.length - 1];
        if (!a || a.opt || !args.length) continue;
        const wrap = WRAPPERS.get(c.name);
        if (c.role === "heading" || c.role === "style" || (wrap && args.length === wrap[1] && !c.group)) out.push({ from: c.from, to: c.to, openTo: a.from + 1, closeFrom: a.to - 1, kind: c.role === "heading" ? "heading" : "inline" });
        else if (c.name === "caption") out.push({ from: c.from, to: c.to, openTo: a.from + 1, closeFrom: a.to - 1, kind: "caption" });
      }
      for (const g of read.groups) out.push({ from: g.from, to: g.to, openTo: g.nameTo, closeFrom: g.to - 1, kind: "inline" });
      const args = read.argStarts, groups = new Set(read.groups.map((g) => g.from)), loose = new Set();
      // the groups after a command that takes none (\noindent{…}, \LaTeX{}{…}) are groups of their own
      for (const c of read.cmds) if (c.args.length && (SILENT.has(c.name) || TEXT_MACROS.has(c.name) || GROUP_CLASSES.has(c.name))) c.args.forEach((a) => loose.add(a.from));
      for (const b of read.braces) if ((!args.has(b.from) || loose.has(b.from)) && !groups.has(b.from) && b.to - b.from > 2) out.push({ from: b.from, to: b.to, openTo: b.from + 1, closeFrom: b.to - 1, kind: "inline" });
      for (const e of read.envs) {
        if (!(e.endFrom < e.to)) continue;
        if (e.name === "document") { out.push({ from: e.from, to: e.to, openTo: e.beginTo, closeFrom: e.endFrom, kind: "document", keep: true }); continue; }
        if (FIGURE_FLOATS.has(e.name)) { out.push({ from: e.from, to: e.to, openTo: e.beginTo, closeFrom: e.endFrom, kind: "figure", keep: true }); continue; }
        if (e.role === "list") {
          const first = e.itemCmds[0];
          out.push({ from: e.from, to: e.to, openTo: first ? first.nameTo : e.beginTo, closeFrom: e.endFrom, kind: "list" });
        } else if (e.role === "text") out.push({ from: e.from, to: e.to, openTo: e.beginTo, closeFrom: e.endFrom, kind: "env" });
      }
      if (read.preamble) out.push({ from: 0, to: read.preamble.to, openTo: read.preamble.to, closeFrom: read.preamble.to, kind: "preamble", keep: true });
      if (read.end >= 0) out.push({ from: read.end, to: text.length, openTo: text.length, closeFrom: text.length, kind: "after", keep: true });
      out.sort((a, b) => a.from - b.from || b.to - a.to);
      framesCache.set(value, out);
      return out;
    }

    // A deletion of [from, to) made whole (22.1, B1 3.): an atom it cuts is taken whole; a construct it covers whole is
    // deleted whole; one it covers in part keeps its markup round what remains, or loses it with its last word (`pure`:
    // nothing typed in its place); the document's own markup always stays. Answers the ranges to delete.
    function protect(value, from, to, pure) {
      // markup is hidden inside pieces (with the line break or the indent they hide): kept or taken as those pieces whole
      const piece = (x) => touching(value, x).find((p) => p.from < x && x < p.to) || null;
      const outer = (x, y) => { const p = piece(x), q = piece(y); return [p ? p.from : x, q ? q.to : y]; };
      [from, to] = outer(from, to);
      let D = [[from, to]];
      const kept = [];
      const covers = (x, y) => y <= x || D.some((d) => d[0] <= x && y <= d[1]);
      const meets = (x, y) => D.some((d) => d[0] < y && x < d[1]);
      const minus = (x, y) => {
        const next = [];
        D.forEach((d) => { if (d[1] <= x || d[0] >= y) next.push(d); else { if (d[0] < x) next.push([d[0], x]); if (d[1] > y) next.push([y, d[1]]); } });
        D = next;
      };
      const cut = (x0, y0) => { if (y0 <= x0) return; const [x, y] = outer(x0, y0); kept.push([x, y]); minus(x, y); };
      const add = (x0, y0) => {
        const [x, y] = outer(x0, y0);
        D.push([x, y]);
        D.sort((p, q) => p[0] - q[0]);
        const next = [];
        D.forEach((d) => { const last = next[next.length - 1]; if (last && d[0] <= last[1]) last[1] = Math.max(last[1], d[1]); else next.push(d.slice()); });
        D = next;
        kept.forEach((k) => minus(k[0], k[1]));   // what another construct keeps stays kept
      };
      const frames = framesOf(value).filter((f) => f.from < to && from < f.to);
      for (let pass = 0; pass < 6; pass++) {
        const before = JSON.stringify(D);
        for (const f of frames) {
          if (!meets(f.from, f.to) || (covers(f.from, f.to) && !f.keep)) continue;
          if (!f.keep && pure && f.kind !== "caption" && covers(f.openTo, f.closeFrom)) { add(f.from, f.to); continue; }
          cut(f.from, f.openTo);
          cut(f.closeFrom, f.to);
        }
        if (JSON.stringify(D) === before) break;
      }
      return D.filter((d) => d[1] > d[0]);
    }

    // Typed or pasted words as LaTeX that prints them: the special characters escaped; line breaks a space inside a
    // heading, a style or a caption, a new \item in a list, else a new paragraph.
    function literal(text, ctx, br, indent) {
      let s = String(text).replace(/\r\n?/g, "\n").replace(/[%$&#_{}~^\\]/g, (c) => TYPE_ESCAPES[c]);
      if (s.indexOf("\n") < 0) return s;
      if (ctx === "inline") return s.replace(/[ \t]*\n\s*/g, " ");
      if (ctx === "list") return s.split(/\n+/).map((x) => x.trim()).filter(Boolean).join(br + indent + "\\item ");
      return s.replace(/[ \t]*\n\s*/g, br + br);
    }
    function placeOf(value, pos) {
      const text = value.info.text, inline = framesOf(value).some((f) => (f.kind === "inline" || f.kind === "heading" || f.kind === "caption") && f.openTo <= pos && pos <= f.closeFrom);
      if (inline) return { ctx: "inline", indent: "" };
      const ctx = contextAt(value.read, pos);
      if (ctx && ctx.kind === "env" && ctx.x.role === "list") {
        const item = ctx.x.itemCmds.filter((c) => c.from < pos).pop();
        const l = item ? text.slice(text.lastIndexOf("\n", item.from - 1) + 1, item.from) : "";
        return { ctx: "list", indent: /^[ \t]*/.exec(l)[0] };
      }
      return { ctx: "text", indent: "" };
    }
    // A control word must not run into what now follows it (\noindent and a letter, \\ and "["): "{}" between.
    function joints(ch, newDoc) {
      const extra = [];
      ch.iterChangedRanges((fa, ta, fb, tb) => {
        [fb, tb].forEach((p) => {
          if (p <= 0 || p >= newDoc.length) return;
          const before = newDoc.sliceString(Math.max(0, p - 40), p), after = newDoc.sliceString(p, p + 1);
          const word = /(?:^|[^\\])(?:\\\\)*\\[A-Za-z]+\*?$/.test(before), bs = /(?:^|[^\\])(?:\\\\)*\\\\$/.test(before);
          if ((word && /[A-Za-z*[]/.test(after)) || (bs && /[*[]/.test(after))) if (!extra.some((x) => x.from === p)) extra.push({ from: p, insert: "{}" });
        });
      });
      return extra;
    }
    // A user's edit in the edit view, made safe: {changes, selection} for the same transaction, `tr` itself when
    // nothing needs changing, or {refused: a sentence}. Typed and pasted words are escaped and go where the cursor shows
    // them; a deletion is made whole (`protect`); a control word never runs into what follows it; and an edit that
    // touched any markup is checked: one that would leave the source unbalanced is refused.
    function userEdit(state, value, tr, ev) {
      const br = state.lineBreak, compose = /compose/.test(ev), parts = [];
      tr.changes.iterChanges((fa, ta, fb, tb, ins) => parts.push({ from: fa, to: ta, text: ins.sliceString(0, ins.length, "\n") }));
      const specs = [];
      let caret = null, risky = false;
      for (const p of parts) {
        let at = p.from;
        if (p.to > p.from) {
          const D = protect(value, p.from, p.to, !p.text);
          if (D.length !== 1 || D[0][0] !== p.from || D[0][1] !== p.to) risky = true;
          value.atoms.between(p.from, p.to, (f, t2, v) => { if (v.spec.atom && f < p.to && t2 > p.from) risky = true; });
          D.forEach((d) => specs.push({ from: d[0], to: d[1], insert: "" }));
          const inner = atomAt(value, at, "in");
          if (inner) at = inner.from;
          const hit = D.find((d) => d[0] < at && at < d[1]);
          if (hit) at = hit[0];
          if (p.text) {
            const there = typedAt(state, value, at);
            if (there == null) return { refused: t("这里不能输入文字。") };
            if (there !== at && D.some((d) => d[0] < there && there < d[1])) return { refused: t("这里不能输入文字。") };
            at = there;
          }
        } else if (p.text) {
          const there = compose ? p.from : typedAt(state, value, p.from);
          if (there == null) return { refused: t("这里不能输入文字。") };
          if (there !== p.from) risky = true;
          at = there;
        }
        if (p.text) {
          const place = placeOf(value, at), s2 = compose ? p.text : literal(p.text, place.ctx, br, place.indent);
          if (s2 !== p.text || /\n/.test(p.text)) risky = true;
          const same = specs.find((x) => x.to != null && x.from === at && x.insert === "");
          if (same) same.insert = s2; else specs.push({ from: at, insert: s2 });
          caret = { at: at, len: s2.split(br).join("\n").length };   // a line break is one position in the editor
        } else if (caret == null) caret = { at: specs.length ? specs[0].from : p.from, len: 0 };
      }
      specs.sort((x, y) => x.from - y.from || (x.to == null ? x.from : x.to) - (y.to == null ? y.from : y.to));
      let ch;
      try { ch = state.changes(specs); } catch (err) { return { refused: t("选中的范围不能这样改。") }; }
      let nd = ch.apply(state.doc);
      const more = joints(ch, nd);
      if (!risky && !more.length) return tr;
      let anchor = caret ? ch.mapPos(caret.at, -1) + caret.len : ch.mapPos(state.selection.main.head, -1);
      if (more.length) { const c2 = CM.ChangeSet.of(more, nd.length); anchor = c2.mapPos(anchor, 1); ch = ch.compose(c2); nd = c2.apply(nd); }
      if (risky) {
        const text2 = nd.toString();
        if (unsureWhy(text2, readLatex(text2))) return { refused: t("这样改会让 LaTeX 的结构不完整，没有改。") };
      }
      return { changes: ch, selection: { anchor: Math.min(anchor, nd.length) }, userEvent: ev, scrollIntoView: true };
    }

    wx = { CM: CM, field: field, mathOn: mathOn, refresh: refresh, preamble: preamble, live: live, hooks: hooks, atomAt: atomAt,
      typedAt: typedAt, framesOf: framesOf, protect: protect, userEdit: userEdit, literal: literal, placeOf: placeOf, viewPieces: viewPieces };
    return wx;
  }
  // A group is drawn in its face or size when it holds text and its closing brace is not inside a formula (where the
  // brace belongs to the formula, not to the group).
  function groupShown(read, g) { return g.to - 1 > g.nameTo && !read.maths.some((f) => f.from < g.to - 1 && g.to - 1 < f.to); }
  // The words a float the edit view cannot draw says: its kind and its caption's plain words.
  function floatWords(read, e, kind, text) {
    const cap = read.cmds.find((c) => c.name === "caption" && c.from > e.beginTo && c.to <= e.endFrom);
    const a = cap ? cap.args.filter((x) => !x.opt).pop() : null, words = a ? plainWords(text.slice(a.from + 1, a.to - 1)) : null;
    return words ? t("{kind}：{words}", { kind: kind, words: words }) : kind;
  }

  // The edit view's extensions, which 「显示代码」 takes out (a compartment): the field, the atomic ranges, the filter
  // that keeps typing literal and deletions whole, Return, ⇧Return and Backspace like Word, copying as plain text.
  // `ui.say(sentence)`: a refusal, said quietly in the right column.
  function wordExtensions(CM, ui) {
    const w = wordField(CM), say = (s) => { if (ui && ui.say) ui.say(s); };
    const filter = CM.EditorState.transactionFilter.of((tr) => {
      const value = tr.startState.field(w.field, false);
      if (!value) return tr;
      const ev = tr.annotation(CM.Transaction.userEvent) || "";
      if (!tr.docChanged) {
        if (!tr.selection) return tr;
        // a selection set by a program (a link, a search) that lands inside what is hidden moves to its edge
        const sel = tr.selection, fix = (p) => { const a = w.atomAt(value, p, "in"); return a ? a.to : p; };
        let moved = false;
        const ranges = sel.ranges.map((r) => { const a = fix(r.anchor), h = fix(r.head); if (a !== r.anchor || h !== r.head) moved = true; return CM.EditorSelection.range(a, h); });
        if (!moved) return tr;
        return { selection: CM.EditorSelection.create(ranges, sel.mainIndex), effects: tr.effects, scrollIntoView: tr.scrollIntoView, userEvent: ev || undefined };
      }
      // moving lines (⌥↑) or commenting them out (⌘/) would move or hide what the edit view does not show: code only
      if (/^(?:move\.line|comment)/.test(ev)) return {};
      if (!/^(?:input|delete)/.test(ev) || /^input\.(?:format|enter|footnote|title)/.test(ev) || /\.made$/.test(ev)) return tr;
      const r = w.userEdit(tr.startState, value, tr, ev);
      if (r === tr) return tr;
      if (r.refused) { say(r.refused); return {}; }
      return r;
    });
    // (a test's stand-in view has neither the atomic ranges nor the clipboard's filter)
    const atoms = CM.EditorView.atomicRanges ? [CM.EditorView.atomicRanges.of((view) => { const v = view.state.field(w.field, false); return v ? v.atoms : CM.RangeSet.empty; })] : [];
    const keys = CM.keymap.of([
      { key: "Enter", run: (view) => runSpec(w, view, (st, v) => enterSpec(CM, st, v, false), say) },
      { key: "Shift-Enter", run: (view) => runSpec(w, view, (st, v) => enterSpec(CM, st, v, true), say) },
      { key: "Backspace", run: (view) => runSpec(w, view, (st, v) => backSpec(CM, st, v, -1), say, true) },
      { key: "Delete", run: (view) => runSpec(w, view, (st, v) => backSpec(CM, st, v, 1), say, true) },
    ]);
    const copy = CM.EditorView.clipboardOutputFilter ? [CM.EditorView.clipboardOutputFilter.of((text, state) => {
      const v = state.field(w.field, false);
      return v ? state.selection.ranges.filter((r) => !r.empty).map((r) => plainOf(v, r.from, r.to)).join("\n") : text;
    })] : [];
    const noDrag = CM.EditorView.domEventHandlers({ dragstart: (event) => { event.preventDefault(); return true; } });
    return [w.field, filter, keys, noDrag, CM.EditorView.contentAttributes.of({ class: "latex-word" })].concat(atoms, copy);
  }
  // Runs a key's spec: null lets CodeMirror's own key run (`passOn`), {refused} says why, else one dispatch.
  function runSpec(w, view, make, say, passOn) {
    if (view.composing || view.state.readOnly) return false;
    const value = view.state.field(w.field, false);
    if (!value) return false;
    const r = make(view.state, value);
    if (!r) return !passOn;
    if (r.refused) { say(r.refused); return true; }
    view.dispatch(r);
    return true;
  }
  // The plain text of [from, to) as the edit view shows it: hidden markup left out, a chip or a formula its words or
  // its source, a paragraph's line break a space.
  function plainOf(value, from, to) {
    const text = value.info.text, atoms = [];
    // a range set yields overlapping ranges layer by layer: put the replaces in order first
    value.deco.between(from, to, (f, t2, v) => { if (v.spec.atom) atoms.push([f, t2, v.spec.widget]); });
    atoms.sort((a, b) => a[0] - b[0]);
    let out = "", at = from;
    atoms.forEach(([f, t2, w]) => {
      if (t2 <= at) return;
      if (f > at) out += text.slice(at, Math.min(f, to));
      if (w && f >= from && t2 <= to) out += w.plain || "";
      at = Math.max(at, t2);
    });
    if (at < to) out += text.slice(at, to);
    return out;
  }

  // Return in the edit view (22.1): a new paragraph (a blank line); in a list a new \item, and an empty item leaves the
  // list; in a heading the heading ends there and the rest starts a paragraph; inside a style the style is closed before
  // the paragraph and opened again after it. ⇧Return: a line break \\, only inside a paragraph that has words before it.
  // A selection is deleted first (as a deletion is), in the same step.
  function enterSpec(CM, state, value, shift) {
    const w = wordField(CM), br = state.lineBreak;
    let st = state, val = value, first = null;
    if (!state.selection.main.empty) {
      const sel = state.selection.main, D = w.protect(value, sel.from, sel.to, true);
      first = state.changes(D.map((d) => ({ from: d[0], to: d[1] })));
      st = state.update({ changes: first, selection: { anchor: first.mapPos(sel.from, -1) } }).state;
      val = st.field(w.field);
    }
    const r = enterAt(CM, st, val, shift, br);
    if (!r || r.refused) return r;
    const changes = first ? first.compose(r.changes) : r.changes;
    return { changes: changes, selection: r.selection, userEvent: "input.enter", scrollIntoView: true };
  }
  function enterAt(CM, st, val, shift, br) {
    const w = wordField(CM), text = val.info.text, read = val.read;
    let pos = st.selection.main.head;
    const there = w.typedAt(st, val, pos);
    if (there == null) return { refused: t("这里不能分段。") };
    pos = there;
    const frames = w.framesOf(val);
    // a style the cursor stands at the edge of is left: Return there is outside it
    for (let moved = true, n = 0; moved && n < 8; n++) {
      moved = false;
      for (const f of frames) if (f.kind === "inline" && f.openTo < f.closeFrom) {
        if (pos === f.openTo) { pos = f.from; moved = true; } else if (pos === f.closeFrom) { pos = f.to; moved = true; }
      }
    }
    const around = frames.filter((f) => f.openTo <= pos && pos <= f.closeFrom && f.from < pos && pos < f.to);
    const inline = around.filter((f) => f.kind === "inline");
    const heading = around.find((f) => f.kind === "heading"), caption = around.find((f) => f.kind === "caption" || f.kind === "figure");
    const make = (specs, anchor) => { const ch = st.changes(specs); return { changes: ch, selection: { anchor: anchor(ch) } }; };
    if (caption) return { refused: shift ? t("图注里不能换行。") : t("图注里不能分段。") };
    if (shift) {
      if (heading) return { refused: t("标题里不能换行。") };
      const ls = text.lastIndexOf("\n", pos - 1) + 1, before = text.slice(ls, pos);
      const lineBefore = before.replace(/\\[A-Za-z]+\*?|[{}\s]/g, "");
      if (!lineBefore && (ls === 0 || /^[ \t]*$/.test(text.slice(text.lastIndexOf("\n", ls - 2) + 1, ls - 1)) || STRUCTURAL.test(text.slice(text.lastIndexOf("\n", ls - 2) + 1, ls - 1)))) return { refused: t("段落开头不能换行。") };
      const after = text[pos] === "[" || text[pos] === "*" ? "{}" : "";
      return make([{ from: pos, insert: "\\\\" + after + br }], (ch) => ch.mapPos(pos, 1));
    }
    if (heading) {
      const c = read.cmds.find((x) => x.from === heading.from && x.role === "heading"), line = st.doc.lineAt(heading.to);
      const rest = text.slice(heading.to, line.to), endAt = /^\s*(\\label\s*\{[^{}]*\}\s*)*$/.test(rest) ? line.to : heading.to;
      if (pos === heading.openTo && heading.openTo < heading.closeFrom) {   // at its start: an empty paragraph above it
        const hl = st.doc.lineAt(heading.from), above = hl.number > 1 && st.doc.line(hl.number - 1).text.trim();
        return make([{ from: hl.from, insert: above ? br + br : br }], (ch) => ch.mapPos(pos, 1));
      }
      // the words after the cursor leave the heading for a new paragraph after it (and its \label), set apart from
      // what follows by a blank line
      const after = line.number < st.doc.lines && st.doc.line(line.number + 1).text.trim() ? br : "";
      const moved = text.slice(pos, heading.closeFrom), words = moved.replace(/^\s+/, "");
      if (!words || crosses(text, read, pos, heading.closeFrom) || !c) {
        return make([{ from: endAt, insert: br + br + after }], (ch) => ch.mapPos(endAt, -1) + 2);
      }
      return make([{ from: pos, to: heading.closeFrom }, { from: endAt, insert: br + br + words + after }], (ch) => ch.mapPos(endAt, -1) + 2);
    }
    // in a list: a new \item, or out of the list from an empty one
    const ctx = contextAt(read, pos);
    let list = ctx && ctx.kind === "env" && ctx.x.role === "list" ? ctx.x : null;
    if (!list && inline.length) { const c2 = contextAt(read, inline[0].from); if (c2 && c2.kind === "env" && c2.x.role === "list") list = c2.x; }
    let para = br + br;
    if (list) {
      const items = list.itemCmds, item = items.filter((c) => c.from < pos).pop();
      if (!item) return { refused: t("这里不能分段。") };
      const next = items.find((c) => c.from > item.from), end = next ? next.from : list.endFrom;
      if (!inline.length && !text.slice(item.to, end).replace(/%[^\n]*/g, "").trim()) {
        const spec = returnSpec(st.update({ selection: { anchor: pos } }).state, read);
        return { changes: spec.changes, selection: spec.selection };
      }
      para = br + /^[ \t]*/.exec(st.doc.lineAt(item.from).text)[0] + "\\item ";
    } else if (!inline.length) {
      // nothing before the cursor on its line: one line break makes the paragraph (or an empty one)
      const ls = text.lastIndexOf("\n", pos - 1) + 1;
      if (!text.slice(ls, pos).trim()) para = br;
    }
    const outer = inline.slice().sort((a, b) => a.from - b.from);
    const closes = outer.map(() => "}").join(""), opens = outer.map((f) => text.slice(f.from, f.openTo)).join("");
    const ins = closes + para + opens;
    return make([{ from: pos, insert: ins }], (ch) => ch.mapPos(pos, -1) + ins.split(br).join("\n").length);
  }
  // Backspace (dir -1) and Delete (dir 1) where Word does more than CodeMirror: at the start of a heading's words it
  // becomes body text, joined with the paragraph before when that is running text; at the edge of a style the
  // character outside it is deleted; an empty style goes whole. Elsewhere null (CodeMirror's own, then the filter).
  function backSpec(CM, state, value, dir) {
    const w = wordField(CM), sel = state.selection.main, text = value.info.text;
    if (!sel.empty) return null;
    const pos = sel.head, frames = w.framesOf(value);
    const f = frames.filter((x) => (x.kind === "inline" || x.kind === "heading") && (dir < 0 ? x.openTo === pos : x.closeFrom === pos)).pop();
    if (!f) return null;
    if (f.openTo === f.closeFrom) return { changes: state.changes({ from: f.from, to: f.to }), selection: { anchor: f.from }, userEvent: "delete.backward.made" };
    if (f.kind === "heading") {
      if (dir > 0) return null;
      // joined with the paragraph before it when that is running text: the blank lines between become one line break
      let open = { from: f.from, to: f.openTo, insert: "" };
      const line = state.doc.lineAt(f.from);
      if (!text.slice(line.from, f.from).trim() && line.number > 2) {
        let k = line.number - 1;
        while (k > 1 && !state.doc.line(k).text.trim()) k -= 1;
        const p = state.doc.line(k);
        if (k < line.number - 1 && p.text.trim() && !STRUCTURAL.test(p.text) && !/\\\\\s*$/.test(codePart(p.text))) open = { from: p.to, to: f.openTo, insert: state.lineBreak };
      }
      const ch = state.changes([open, { from: f.closeFrom, to: f.to }]), nd = ch.apply(state.doc).toString();
      if (unsureWhy(nd, readLatex(nd))) return { refused: t("这个标题不能这样变成正文。") };
      return { changes: ch, selection: { anchor: ch.mapPos(f.openTo, 1) }, userEvent: "delete.backward.made" };
    }
    // the character before the style (or after it): one code point, or the atom there whole
    const q = dir < 0 ? f.from : f.to;
    if (dir < 0 ? q <= 0 : q >= text.length) return { refused: "" };
    const a = dir < 0 ? w.atomAt(value, q, "to") : w.atomAt(value, q, "from");
    let from = dir < 0 ? q - 1 : q, to = dir < 0 ? q : q + 1;
    if (a) { from = a.from; to = a.to; }
    else if (dir < 0 && /[\udc00-\udfff]/.test(text[q - 1] || "") && q > 1) from = q - 2;
    else if (dir > 0 && /[\ud800-\udbff]/.test(text[q] || "")) to = q + 2;
    const r = w.userEdit(state, value, state.update({ changes: { from: from, to: to }, userEvent: dir < 0 ? "delete.backward" : "delete.forward" }), dir < 0 ? "delete.backward" : "delete.forward");
    if (r.refused) return r;
    const made = dir < 0 ? "delete.backward.made" : "delete.forward.made";
    if (r.changes) return Object.assign({}, r, { userEvent: made });
    return { changes: state.changes({ from: from, to: to }), selection: { anchor: from }, userEvent: made };
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
  // (显示代码, ⇧Return in it), a plain newline. Every line break is the file's own (state.lineBreak, 22.1).
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
    const env = ctx.x, items = env.itemCmds;
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

  // -- 格式与插入: the tools (22.12) ----------------------------------------------------------------------------------
  //
  // Each tool is exactly the same as typing its command: given an EditorState (and what the page knows of the main
  // file), it answers {changes, selection} for the editor's own dispatch -- one transaction, so ⌘Z, the save, the
  // passages' mapping and the line-break filter all apply --, {refused: a sentence}, or null when there is nothing to
  // do. Nothing here writes anything else. Positions are the editor's (a line break is one); a block's line breaks are
  // the file's own (state.lineBreak).

  const CHAPTER_CLASSES = new Set(["book", "report", "ctexbook", "ctexrep", "memoir", "scrbook", "scrreprt"]);
  const GRAPHICX_PACKAGES = new Set(["graphicx", "tikz", "pgf", "pgfplots", "adjustbox"]);   // each loads graphicx
  const GRAPHICX_CLASSES = new Set(["beamer", "ctexbeamer"]);
  const TEXT_STYLE_NAMES = ["textbf", "textit", "underline"];
  const FONT_COMMANDS = ["textsf", "texttt", "textrm"], FONT_DECLS = ["songti", "heiti", "kaishu", "fangsong"];
  const SIZE_DECLS = ["small", "large", "Large"];
  const HEADING_NAMES = ["chapter", "section", "subsection", "subsubsection"];
  // The arguments no tool writes into: a key, a label, a file name, a package.
  const NAME_ARGS = new Set(["label", "includegraphics", "input", "include", "subfile", "import", "subimport", "url",
    "usepackage", "documentclass", "bibliography", "bibliographystyle", "addbibresource", "graphicspath", "href"]);
  // A line that is a command or an environment of its own, never part of a paragraph a heading or a list is made of.
  const STRUCTURAL = /^\s*(?:\\(?:begin|end|item|part|chapter|section|subsection|subsubsection|paragraph|caption|label|centering|includegraphics|maketitle|tableofcontents|bibliography|bibliographystyle|printbibliography|appendix|input|include|subfile|newpage|clearpage)\b|%|\\\[|\$\$)/;
  // 首行缩进 on the page (22.12 B2): a line the edit view hides whole (a comment, a \label, \noindent, \vspace{…} …) is
  // passed over when it is asked where a paragraph starts; a paragraph starts after a blank line, at the body's start,
  // after \maketitle, a contents line or a page break ("latex-para"), or as the first after a heading, blank lines
  // between or not ("latex-para-first", indented only where the class does: ctex, indentfirst). What follows a list or
  // a float without a blank line goes on the paragraph before it, as TeX sets it, and \noindent is never indented.
  function quietLine(line) {
    if (!line.trim()) return false;
    let s = codePart(line).trim();
    for (let n = 0; n < 40 && s; n++) {
      const m = /^\\([A-Za-z@]+)\*?\s*/.exec(s);
      if (!m || m[1] === "maketitle" || !(SILENT.has(m[1]) || SILENT_ARGS.has(m[1]) || m[1] === "label")) return false;
      s = s.slice(m[0].length);
      for (let g = /^(?:\{[^{}]*\}|\[[^\]]*\])\s*/.exec(s); g; g = /^(?:\{[^{}]*\}|\[[^\]]*\])\s*/.exec(s)) s = s.slice(g[0].length);
    }
    return !s;
  }
  const PARA_AFTER = /^\s*\\(?:begin\s*\{document\}|maketitle|tableofcontents|listoffigures|listoftables|newpage|clearpage|cleardoublepage|pagebreak)(?![A-Za-z])/;
  const HEADING_LINE = /^\s*\\(?:part|chapter|section|subsection|subsubsection)\*?\s*[[{]/;
  // A change on a line can start or end the paragraph of the next line with words, and a quiet or blank line's place is
  // drawn from what follows it: the place drawn again reaches back over the quiet and blank lines before it and on, over
  // those after it, to the next line with words.
  function paraReach(doc, s) {
    let a = doc.lineAt(Math.max(0, Math.min(s[0], doc.length))).number, b = doc.lineAt(Math.max(0, Math.min(s[1], doc.length))).number;
    const still = (n) => { const t2 = doc.line(n).text; return !t2.trim() || quietLine(t2); };
    while (a > 1 && still(a - 1)) a -= 1;
    if (b < doc.lines) { b += 1; while (b < doc.lines && still(b)) b += 1; }
    return [Math.min(s[0], doc.line(a).from), Math.max(s[1], doc.line(b).to)];
  }
  const IMAGE_UNSAFE = /[%#{}\\~$^&]/;   // a file name \includegraphics cannot take
  const CARET = "\u0001";   // where the cursor goes in a block

  // What a preamble says: the class, whether it has \chapter, loads ctex, loads graphicx (itself, or by a package or
  // class that does), loads booktabs, and its \graphicspath folders. Commented lines do not count.
  function preambleFacts(text) {
    const code = String(text || "").split("\n").map(codePart).join("\n");
    const m = /\\documentclass\s*(?:\[[^\]]*\])?\s*\{\s*([^{}\s]+)\s*\}/.exec(code), cls = m ? m[1] : null;
    const packages = new Set(), re = /\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}/g;
    for (let p = re.exec(code); p; p = re.exec(code)) p[1].split(",").forEach((x) => { if (x.trim()) packages.add(x.trim()); });
    const gp = /\\graphicspath\s*\{((?:\s*\{[^{}]*\})*)\s*\}/.exec(code);
    return {
      cls: cls, chapters: !!cls && CHAPTER_CLASSES.has(cls), ctex: (!!cls && /^ctex/.test(cls)) || packages.has("ctex"),
      graphicx: (!!cls && GRAPHICX_CLASSES.has(cls)) || Array.from(packages).some((x) => GRAPHICX_PACKAGES.has(x)),
      booktabs: packages.has("booktabs"),
      amsmath: (!!cls && /^ams/.test(cls)) || packages.has("amsmath") || packages.has("mathtools"),
      url: ["url", "xurl", "hyperref"].some((x) => packages.has(x)),
      graphicspath: gp ? Array.from(gp[1].matchAll(/\{([^{}]*)\}/g), (x) => x[1].trim()).filter(Boolean) : [],
    };
  }

  // Project-relative posix paths: `normPath` folds "." and "..", `relPath` is `to` as seen from the folder `from`.
  function normPath(p) {
    const out = [];
    String(p).split("/").forEach((s) => {
      if (!s || s === ".") return;
      if (s === ".." && out.length && out[out.length - 1] !== "..") out.pop(); else out.push(s);
    });
    return out.join("/");
  }
  function relPath(from, to) {
    const a = normPath(from || "").split("/").filter(Boolean), b = normPath(to).split("/");
    let k = 0;
    while (k < a.length && k < b.length - 1 && a[k] === b[k]) k += 1;
    return a.slice(k).map(() => "..").concat(b.slice(k)).join("/");
  }
  // The path \includegraphics is given for the project file `file`, as the export compiles it (22.4: TeX runs in the
  // main file's folder): relative to a \graphicspath folder the file is under, else to the main file's folder.
  function figurePath(file, folder, graphicspath) {
    for (const g of graphicspath || []) {
      const base = normPath((folder ? folder + "/" : "") + g);
      if (base && !base.startsWith("..") && file.startsWith(base + "/")) return file.slice(base.length + 1);
    }
    return relPath(folder, file);
  }

  const TEX_ESCAPES = { "\\": "\\textbackslash{}", "{": "\\{", "}": "\\}", "$": "\\$", "&": "\\&", "#": "\\#", "_": "\\_",
    "%": "\\%", "^": "\\^{}", "~": "\\~{}" };
  // A caption as typed, set as text: one line, every character TeX would read as markup escaped (latex_edit._TEX_SPECIAL).
  function escapeTex(s) { return String(s || "").replace(/\s+/g, " ").trim().replace(/[\\{}$&#_%~^]/g, (c) => TEX_ESCAPES[c]); }
  // A label for `prefix` from `words` (a file's name, a caption): letters and digits joined by "-", made unique in `text`.
  function newLabel(text, prefix, words, fallback) {
    const base = String(words || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || fallback;
    let name = prefix + ":" + base;
    for (let n = 2; text.indexOf("\\label{" + name + "}") >= 0; n++) name = prefix + ":" + base + "-" + n;
    return name;
  }

  // Why a tool of `kind` (inline: a style, a font, a formula, a footnote, a citation; block: a figure, a table, a
  // displayed formula, a list; heading) cannot work at `pos`, or null.
  function refusal(read, text, pos, kind) {
    if (read.preamble && pos <= read.preamble.to) return t("光标在导言区里。请把光标放到正文里。");
    if (read.end >= 0 && pos >= read.end) return t("光标在文稿结尾（{command}）之后。请把光标放到正文里。", { command: "\\end{document}" });
    const name = read.cmds.find((c) => c.from < pos && pos < c.nameTo);
    if (name) return t("光标在命令 {command} 的名字里。请把光标移开。", { command: "\\" + name.name });
    const ctx = contextAt(read, pos);
    if (!ctx) return null;
    if (ctx.kind === "math") return t("光标在公式里。请把光标移到公式外面。");
    if (ctx.kind === "raw") return t("光标在注释或原样照排的文字里，这里不能用格式与插入。");
    if (ctx.kind === "arg") {
      const c = ctx.x.cmd;
      if (c.role === "cite" || c.role === "ref" || NAME_ARGS.has(c.name) || ctx.x.arg.opt || kind === "block" || (kind === "heading" && c.role !== "heading")) {
        return t("光标在 {command} 的参数里，这里不能用这个工具。", { command: "\\" + c.name });
      }
    }
    if (ctx.kind === "env" && ctx.x.role === "raw") return t("光标在 {env} 环境里，这里不能用这个工具。", { env: ctx.x.name });
    if (ctx.kind === "env" && ctx.x.role === "list" && kind === "heading") return t("光标在列表里，不能把这一段变成标题。");
    return null;
  }
  function refusalOf(read, text, sel, kind) { return refusal(read, text, sel.from, kind) || (sel.to > sel.from ? refusal(read, text, sel.to, kind) : null); }

  // Whether wrapping [from, to] would break it: a brace opened or closed outside it, a backslash cut from what it
  // escapes, a command's name or two of its arguments, an environment's \begin or \end, a formula's edge, a comment.
  function crosses(text, read, from, to) {
    if (from >= to) return false;
    const odd = (at) => { let run = 0; while (at - run - 1 >= 0 && text[at - run - 1] === "\\") run += 1; return run % 2 === 1; };
    if (odd(from) || odd(to)) return true;
    let depth = 0;
    for (let j = from; j < to; j++) {
      const c = text[j];
      if (c === "\\") { j += 1; continue; }
      if (c === "%") { const e = text.indexOf("\n", j); if (e < 0 || e >= to) return true; j = e; continue; }
      if (c === "{") depth += 1;
      else if (c === "}" && --depth < 0) return true;
    }
    if (depth) return true;
    const split = (slot) => slot(from) !== slot(to);
    for (const c of read.cmds) {
      if (to <= c.from || from >= c.to) continue;
      if (split((p) => { if (p <= c.from || p >= c.to) return "out"; const k = c.args.findIndex((a) => p > a.from && p < a.to); return k < 0 ? "x" + p : "a" + k; })) return true;
    }
    for (const e of read.envs) if (split((p) => p <= e.from || p >= e.to ? "out" : p >= e.beginTo && p <= e.endFrom ? "body" : "x" + p)) return true;
    for (const f of read.maths) if (split((p) => p > f.from && p < f.to)) return true;
    for (const r of read.raws) if (split((p) => p > r.from && (r.kind === "comment" ? p <= r.to : p < r.to))) return true;
    return false;
  }
  function blankIn(text, from, to) { return /\n[ \t\r]*\n/.test(text.slice(from, to)); }
  function wrapRefusal(text, read, from, to) {
    if (blankIn(text, from, to)) return t("选中的文字跨了段落，不能整段加这个格式。");
    return crosses(text, read, from, to) ? t("选中的文字跨过了括号或环境的边界，不能整段加格式。请重新选择。") : null;
  }

  // A construct a tool toggles: a command \name{…} or a group {\name …} (`decl`), as {from, innerFrom, innerTo, to,
  // name, form}.
  function commandConstruct(c) {
    const a = c.args.find((x) => !x.opt);
    return a ? { from: c.from, innerFrom: a.from + 1, innerTo: a.to - 1, to: a.to, name: c.name, form: "command" } : null;
  }
  function declAt(text, read, decls, o) {
    if (!decls.length || text[o] !== "{") return null;
    const m = new RegExp("^\\{[ \\t]*\\\\(" + decls.join("|") + ")(?![A-Za-z])[ \\t]?").exec(text.slice(o, o + 48));
    if (!m || read.cmds.some((c) => c.args.some((a) => a.from === o))) return null;   // a command's argument is not a group of its own
    const close = skipGroup(text, o, false);
    return close > 0 ? { from: o, innerFrom: o + m[0].length, innerTo: close - 1, to: close, name: m[1], form: "decl" } : null;
  }
  // The innermost group {\decl …} around [from, to]: the braces before `from` read backwards, line by line (at most
  // 400 lines), comments and escaped braces left out.
  function declAround(text, read, decls, from, to) {
    let depth = 0;
    for (let end = from, n = 0; n < 400; n++) {
      const ls = text.lastIndexOf("\n", end - 1) + 1, code = codePart(text.slice(ls, end));
      for (let k = code.length - 1; k >= 0; k--) {
        const ch = code[k];
        if (ch !== "{" && ch !== "}") continue;
        let run = 0;
        while (k - run - 1 >= 0 && code[k - run - 1] === "\\") run += 1;
        if (run % 2) continue;
        if (ch === "}") { depth += 1; continue; }
        if (depth > 0) { depth -= 1; continue; }
        const g = declAt(text, read, decls, ls + k);
        if (g && g.innerTo >= to) return g;
      }
      if (ls === 0) break;
      end = ls - 1;
    }
    return null;
  }
  // The innermost construct of `names` (commands) or `decls` (groups) around [from, to].
  function around(text, read, names, decls, from, to) {
    let best = null;
    for (const c of read.cmds) {
      if (!names.includes(c.name)) continue;
      const k = commandConstruct(c);
      if (k && k.innerFrom <= from && to <= k.innerTo && (!best || k.innerFrom > best.innerFrom)) best = k;
    }
    const g = decls && decls.length ? declAround(text, read, decls, from, to) : null;
    return g && (!best || g.innerFrom > best.innerFrom) ? g : best;
  }
  // The construct of `names` or `decls` whose text or whole is exactly [from, to].
  function exactly(text, read, names, decls, from, to) {
    const same = (k) => k && ((k.innerFrom === from && k.innerTo === to) || (k.from === from && k.to === to));
    for (const c of read.cmds) if (names.includes(c.name) && same(commandConstruct(c))) return commandConstruct(c);
    const g = declAt(text, read, decls || [], from) || (decls && decls.length ? declAround(text, read, decls, from, to) : null);
    return same(g) ? g : null;
  }
  function openingOf(form, name) { return form === "decl" ? "{\\" + name + " " : "\\" + name + "{"; }

  // A transaction of `specs`, the selection carried through it (or `selection`, given in the new text).
  function edited(state, specs, selection) {
    const ch = state.changes(specs), sel = state.selection.main;
    if (selection) return { changes: ch, selection: selection };
    const lo = Math.min(sel.anchor, sel.head), map = (p) => ch.mapPos(p, p === lo && !sel.empty ? -1 : 1);
    return { changes: ch, selection: { anchor: map(sel.anchor), head: map(sel.head) } };
  }
  function unwrapped(state, k) { return edited(state, [{ from: k.from, to: k.innerFrom }, { from: k.innerTo, to: k.to }]); }
  // The selection wrapped in `open` and `close` ("}" by default), the same text selected inside.
  function wrapped(state, open, close) {
    const sel = state.selection.main, n = open.length;
    return edited(state, [{ from: sel.from, insert: open }, { from: sel.to, insert: close == null ? "}" : close }],
      { anchor: sel.anchor + n, head: sel.head + n });
  }
  function inserted(state, pos, before, after) {
    return { changes: state.changes({ from: pos, insert: before + after }), selection: { anchor: pos + before.length } };
  }
  function prepare(state, kind) {
    const text = state.doc.toString(), read = readLatex(text), sel = state.selection.main;
    return { text: text, read: read, sel: sel, no: refusalOf(read, text, sel, kind) };
  }

  // 加粗, 斜体, 下划线 (\textbf, \textit, \underline), toggled like Word: a selection is wrapped, or unwrapped when it
  // is exactly that command's text (or the whole command); with no selection the command the cursor is in is
  // unwrapped, else an empty one is typed with the cursor in its braces.
  function styleSpec(state, name) {
    const p = prepare(state, "inline"), sel = p.sel;
    if (p.no) return { refused: p.no };
    if (!sel.empty) {
      const k = exactly(p.text, p.read, [name], null, sel.from, sel.to);
      if (k) return unwrapped(state, k);
      const bad = wrapRefusal(p.text, p.read, sel.from, sel.to);
      return bad ? { refused: bad } : wrapped(state, openingOf("command", name));
    }
    const k = around(p.text, p.read, [name], null, sel.from, sel.to);
    return k ? unwrapped(state, k) : inserted(state, sel.from, openingOf("command", name), "}");
  }

  // 字体 (\textsf, \texttt, \textrm; ctex's {\songti …} …) and 字号 ({\small …} …): `choice` "" removes the one the
  // selection is or the cursor is in; another changes it to `choice`; with none, a selection is wrapped or an empty one
  // is typed.
  function groupSpec(state, choice, names, decls) {
    const p = prepare(state, "inline"), sel = p.sel;
    if (p.no) return { refused: p.no };
    const k = sel.empty ? around(p.text, p.read, names, decls, sel.from, sel.to) : exactly(p.text, p.read, names, decls, sel.from, sel.to);
    const form = decls.includes(choice) ? "decl" : "command";
    if (k) {
      if (!choice) return unwrapped(state, k);
      if (k.name === choice) return null;
      const open = openingOf(form, choice), delta = open.length - (k.innerFrom - k.from);
      const map = (x) => (x <= k.from ? x : x >= k.innerFrom ? x + delta : k.from + open.length);
      return edited(state, [{ from: k.from, to: k.innerFrom, insert: open }], { anchor: map(sel.anchor), head: map(sel.head) });
    }
    if (!choice) return null;
    if (sel.empty) return inserted(state, sel.from, openingOf(form, choice), "}");
    const bad = wrapRefusal(p.text, p.read, sel.from, sel.to);
    return bad ? { refused: bad } : wrapped(state, openingOf(form, choice));
  }
  function fontSpec(state, choice) { return groupSpec(state, choice, FONT_COMMANDS, FONT_DECLS); }
  function sizeSpec(state, choice) { return groupSpec(state, choice, [], SIZE_DECLS); }

  // The paragraph [from, to] is in: a heading (its lines), the run of lines between blank lines and lines of their
  // own (STRUCTURAL), `empty` on a blank line, `structural` on such a line, `blank` when [from, to] spans a blank line.
  function paragraphAt(state, read, from, to) {
    const doc = state.doc;
    for (const c of read.cmds) {
      if (c.role !== "heading") continue;
      const a = doc.lineAt(c.from).from, b = doc.lineAt(c.to).to;
      if (a <= from && to <= b) return { heading: c, from: a, to: b };
    }
    const first = doc.lineAt(from).number, last = doc.lineAt(to).number;
    const blank = (n) => !doc.line(n).text.trim(), hard = (n) => STRUCTURAL.test(doc.line(n).text);
    for (let n = first; n <= last; n++) {
      if (blank(n) && first < last) return { blank: true };
      if (hard(n)) return { structural: true };
    }
    if (first === last && blank(first)) return { empty: true };
    let top = first, bottom = last;
    while (top > 1 && !blank(top - 1) && !hard(top - 1)) top -= 1;
    while (bottom < doc.lines && !blank(bottom + 1) && !hard(bottom + 1)) bottom += 1;
    return { from: doc.line(top).from, to: doc.line(bottom).to };
  }

  // 标题: the paragraph the cursor is in becomes \section{…} (its text kept), an existing heading changes its level,
  // and "" (正文) unwraps it. 章 only where the class has \chapter (`chapters`).
  function headingSpec(state, level, chapters) {
    const p = prepare(state, "heading"), sel = p.sel;
    if (p.no) return { refused: p.no };
    if (level === "chapter" && !chapters) return { refused: t("这份文稿的文档类没有「章」。") };
    const at = paragraphAt(state, p.read, sel.from, sel.to);
    if (at.blank) return { refused: t("标题里不能有空行：选中的文字跨了段落。") };
    if (at.heading) {
      const c = at.heading, k = commandConstruct(c);
      if (!level) return edited(state, [{ from: c.from, to: k.innerFrom }, { from: k.innerTo, to: k.to }]);
      return c.name === level ? null : edited(state, [{ from: c.from + 1, to: c.from + 1 + c.name.length, insert: level }]);
    }
    if (at.structural) return { refused: t("这一行是命令或环境，不能变成标题。") };
    if (!level) return null;
    if (at.empty) return inserted(state, sel.from, "\\" + level + "{", "}");
    const lastLine = state.doc.lineAt(at.to);
    const start = at.from + /^\s*/.exec(p.text.slice(at.from, at.to))[0].length;
    const end = lastLine.from + codePart(lastLine.text).replace(/\s+$/, "").length;
    if (end <= start) return inserted(state, sel.from, "\\" + level + "{", "}");
    if (crosses(p.text, p.read, start, end)) return { refused: t("这一段跨过了括号或环境的边界，不能整段变成标题。") };
    const ch = state.changes([{ from: start, insert: "\\" + level + "{" }, { from: end, insert: "}" }]);
    const map = (x) => ch.mapPos(x, x >= end ? -1 : 1);
    return { changes: ch, selection: { anchor: map(sel.anchor), head: map(sel.head) } };
  }
  // The level of the paragraph the cursor is in: a heading's command, "" for running text, null where none applies.
  function headingLevel(state, read) {
    const sel = state.selection.main, at = paragraphAt(state, read, sel.from, sel.to);
    return at.heading ? at.heading.name : at.structural || at.blank ? null : "";
  }

  // `block` (lines joined by "\n", CARET where the cursor goes) set on lines of its own in place of [from, to]: after
  // the text before it on its line, before the text after it, in the file's own line break; no blank line is added, so
  // no paragraph is split or joined. With no CARET the cursor goes where the text goes on: the text after it, else the
  // start of the next line, else a new line at the end. `more`: other changes of the same transaction.
  function blockSpec(state, from, to, block, more) {
    const doc = state.doc, line = doc.lineAt(to), before = doc.sliceString(doc.lineAt(from).from, from), after = doc.sliceString(to, line.to);
    const caretIn = block.indexOf(CARET) >= 0, tail = after.trim() || (!caretIn && line.number === doc.lines) ? "\n" : "";
    const whole = (before.trim() ? "\n" : "") + block + tail;
    const at = whole.indexOf(CARET), plain = whole.replace(CARET, "");
    const ch = state.changes([{ from: from, to: to, insert: plain.split("\n").join(state.lineBreak) }].concat(more || []));
    const caret = at >= 0 ? at : tail ? plain.length : plain.length + after.length + 1;
    return { changes: ch, selection: { anchor: ch.mapPos(from, -1) + caret } };
  }
  // \usepackage{name} on the line after the \documentclass line of this file, or null when it has none.
  function packageLine(state, read, name) {
    const c = read.cmds.find((x) => x.name === "documentclass" && (!read.preamble || x.from < read.preamble.to));
    return c ? { from: state.doc.lineAt(c.to).to, insert: state.lineBreak + "\\usepackage{" + name + "}" } : null;
  }
  // A package the main file needs: added to this file in the same transaction when it is the main file (`isMain`),
  // else the sentence that says which line the main file needs (`note`).
  function needs(state, read, opts, name, have) {
    if (have) return { more: [] };
    const line = "\\usepackage{" + name + "}";
    const add = opts.isMain ? packageLine(state, read, name) : null;
    if (add) return { more: [add] };
    return { more: [], note: opts.main && !opts.isMain ? t("主文件 {main} 的导言区需要加一行：{line}", { main: opts.main, line: line })
      : t("主文件的导言区需要加一行：{line}", { line: line }) };
  }

  // 图片: \begin{figure}[htbp] … \end{figure} after the cursor, `opts` {path (as \includegraphics takes it), file,
  // caption, half, graphicx, isMain, main}.
  function figureSpec(state, opts) {
    const p = prepare(state, "block");
    if (p.no) return { refused: p.no };
    const stem = String(opts.file || opts.path).split("/").pop().replace(/\.[^.]*$/, "");
    const block = ["\\begin{figure}[htbp]", "\\centering",
      "\\includegraphics[width=" + (opts.half ? "0.5\\linewidth" : "\\linewidth") + "]{" + opts.path + "}",
      "\\caption{" + escapeTex(opts.caption) + "}", "\\label{" + newLabel(p.text, "fig", stem, "figure") + "}", "\\end{figure}"].join("\n");
    const need = needs(state, p.read, opts, "graphicx", opts.graphicx);
    return Object.assign(blockSpec(state, p.sel.to, p.sel.to, block, need.more), need.note ? { note: need.note } : {});
  }

  // 表格: a table of `rows` (1-20) by `cols` (1-10), the first row a header when `header`, booktabs rules when the main
  // file loads booktabs (or this file is the main file, which then loads it), else \hline; the cursor in the first cell.
  function tableSpec(state, opts) {
    const p = prepare(state, "block");
    if (p.no) return { refused: p.no };
    const rows = Math.min(20, Math.max(1, Math.round(Number(opts.rows) || 1))), cols = Math.min(10, Math.max(1, Math.round(Number(opts.cols) || 1)));
    const add = !opts.booktabs && opts.isMain ? packageLine(state, p.read, "booktabs") : null, rules = opts.booktabs || !!add;
    const rule = (k) => (rules ? "\\" + k + "rule" : "\\hline");
    const cells = (cols > 1 ? new Array(cols - 1).fill("&").join(" ") + " " : "") + "\\\\";
    const lines = ["\\begin{table}[htbp]", "\\centering", "\\caption{" + escapeTex(opts.caption) + "}",
      "\\label{" + newLabel(p.text, "tab", opts.caption, "table") + "}", "\\begin{tabular}{" + "l".repeat(cols) + "}", rule("top")];
    for (let r = 0; r < rows; r++) {
      lines.push((r === 0 ? CARET : "") + cells);
      if (r === 0 && opts.header && rows > 1) lines.push(rule("mid"));
    }
    lines.push(rule("bottom"), "\\end{tabular}", "\\end{table}");
    return blockSpec(state, p.sel.to, p.sel.to, lines.join("\n"), add ? [add] : []);
  }

  // 公式: 行内 $…$ (a selection wrapped, else the cursor between), 独立 an equation environment (a selection moved
  // into it, else the cursor in it).
  function mathSpec(state, display) {
    const p = prepare(state, display ? "block" : "inline"), sel = p.sel;
    if (p.no) return { refused: p.no };
    const bad = sel.empty ? null : wrapRefusal(p.text, p.read, sel.from, sel.to);
    if (bad) return { refused: bad };
    if (!display) return sel.empty ? inserted(state, sel.from, "$", "$") : wrapped(state, "$", "$");
    return blockSpec(state, sel.from, sel.to, "\\begin{equation}\n" + p.text.slice(sel.from, sel.to) + CARET + "\n\\end{equation}");
  }

  // 列表: 项目符号 (itemize) or 编号 (enumerate). The lines a selection touches become its items (each non-empty line
  // one \item); with none, an empty list with one \item and the cursor after it.
  function listSpec(state, kind) {
    const p = prepare(state, "block"), sel = p.sel, doc = state.doc;
    if (p.no) return { refused: p.no };
    const env = kind === "enumerate" ? "enumerate" : "itemize";
    if (sel.empty) return blockSpec(state, sel.from, sel.to, "\\begin{" + env + "}\n  \\item " + CARET + "\n\\end{" + env + "}");
    const first = doc.lineAt(sel.from);
    let last = doc.lineAt(sel.to);
    if (sel.to === last.from && last.number > first.number) last = doc.line(last.number - 1);
    const lines = doc.sliceString(first.from, last.to).split("\n");
    if (lines.some((l) => STRUCTURAL.test(l))) return { refused: t("选中的几行里有命令或环境，不能变成列表。") };
    if (crosses(p.text, p.read, first.from, last.to)) return { refused: t("选中的文字跨过了括号或环境的边界，不能变成列表。") };
    const items = lines.filter((l) => l.trim()).map((l) => "  \\item " + l.trim());
    if (!items.length) items.push("  \\item ");
    items[items.length - 1] += CARET;
    return blockSpec(state, first.from, last.to, ["\\begin{" + env + "}"].concat(items, ["\\end{" + env + "}"]).join("\n"));
  }

  // 脚注: \footnote{} after the selection (its text is new text), the cursor in its braces.
  function footnoteSpec(state) {
    const p = prepare(state, "inline");
    return p.no ? { refused: p.no } : inserted(state, p.sel.to, "\\footnote{", "}");
  }
  // 引用文献: \cite{key,key} after the selection, the cursor after it.
  function citeSpec(state, keys) {
    const p = prepare(state, "inline");
    if (p.no) return { refused: p.no };
    return keys && keys.length ? inserted(state, p.sel.to, "\\cite{" + keys.join(",") + "}", "") : null;
  }

  // What the panel shows of where the cursor is: each style pressed or not, the font, the size and the heading level.
  function formatState(state, read) {
    const text = state.doc.toString(), sel = state.selection.main, pressed = {};
    TEXT_STYLE_NAMES.forEach((name) => { pressed[name] = !!around(text, read, [name], null, sel.from, sel.to); });
    const font = around(text, read, FONT_COMMANDS, FONT_DECLS, sel.from, sel.to), size = around(text, read, [], SIZE_DECLS, sel.from, sel.to);
    return { pressed: pressed, font: font ? font.name : "", size: size ? size.name : "", heading: headingLevel(state, read) };
  }

  // -- 版式: the document's layout settings (22.12, A2) ---------------------------------------------------------------
  //
  // The settings live in the main file's preamble. `readPreamble` reads it -- the class and its options, each
  // \usepackage, \geometry and the setting lines -- with comments masked (positions kept), each command's brace depth and
  // how many \if…\fi it sits in; `layoutOf` turns that into each setting's value, or `mine` ({line, file}) where the
  // setting is written in a form 版式 does not take (inside a macro or a condition, a project .sty/.cls, a class it does
  // not know, several lines that disagree): such a setting is shown, never guessed or written twice. `layoutSpec` writes
  // one setting as the editor's own transaction: a setting already there is changed in place (only its option or line),
  // a new one goes on a line of its own after the last \usepackage (hyperref last of all), removing one removes only
  // what the tool would have written.

  const LAYOUT_CLASSES = new Set(["article", "report", "book", "amsart", "amsbook", "ctexart", "ctexrep", "ctexbook"]);
  const SLIDE_CLASSES = /^(?:ctex)?beamer$/;
  // a paper size among geometry's keys: never a margin (版式 has no 纸张: the researcher, 2026-11-14; new papers are A4, 22.11)
  const PAPER_ITEM = /^(?:[abc]\d|b\dj|letter|legal|executive)paper$/;
  const MARGIN_KEYS = new Set(["margin", "top", "bottom", "left", "right"]);
  // geometry's other keys that place the text block: with one of them the margins are the person's own
  const LAYOUT_KEYS = /^(?:[hv]?margin|[lrtb]margin|inner|outer|hmarginratio|vmarginratio|marginratio|ratio|centering|[hv]centering|textwidth|textheight|width|height|total|body|text|scale|[hv]scale|includehead|includefoot|includeheadfoot|includeall|includemp|bindingoffset|twoside|landscape|portrait|layout\w*|hdivide|vdivide|divide|left|right|top|bottom|margin)$/;
  const MARGIN_PRESETS = ["2", "2.5", "3"];
  const SPACINGS = ["1", "1.25", "1.5", "2"];
  // setspace's own commands, as the stretch they set at \@ptsize 0, 1, 2 (10, 11, 12 pt; setspace.sty)
  const SETSPACE_NAMED = { singlespacing: [1, 1, 1], onehalfspacing: [1.25, 1.213, 1.241], doublespacing: [1.667, 1.618, 1.655] };
  const named = (name) => (Object.prototype.hasOwnProperty.call(SETSPACE_NAMED, name) ? SETSPACE_NAMED[name] : null);
  const PAGE_SIZES = new Set(["10pt", "11pt", "12pt"]), ZIHAO = new Set(["5", "-4"]);
  const MARGIN_PACKAGES = new Set(["fullpage", "a4wide", "a4", "vmargin", "anysize", "typearea", "savetrees"]);
  const HYPERREF_AFTER = new Set(["cleveref", "hypcap", "bookmark", "glossaries", "glossaries-extra"]);
  const NEEDS_HYPERREF = /\\(?:href|hypersetup|autoref|nameref|hyperref|hypertarget|hyperlink|phantomsection|texorpdfstring|pdfbookmark)(?![A-Za-z@])/;
  const DEFINERS = new Set(["renewcommand", "newcommand", "providecommand", "DeclareRobustCommand", "def", "gdef", "edef", "xdef", "let"]);
  const LENGTH_SETTERS = new Set(["setlength", "addtolength"]);
  // What a project .sty or .cls says of each setting: any of it makes the setting the file's own.
  const LOCAL_MARKERS = {
    margin: /\\(?:textwidth|textheight|oddsidemargin|evensidemargin|topmargin|hoffset|voffset|newgeometry)\b|\bgeometry\b/,
    size: /\bzihao\b|\b1[012]pt\b|\\normalsize\b/,
    spacing: /\\(?:setstretch|linespread|singlespacing|onehalfspacing|doublespacing|baselinestretch|SetSinglespace)\b|\bsetspace\b|\blinespread\b/,
    indent: /\\parindent\b|\bparskip\b/,
    parskip: /\\parskip\b|\bparskip\b/,
    pagestyle: /\\(?:pagestyle|thispagestyle)\b|\\ps@|\bnopageno\b/,
    columns: /\b(?:two|one)column\b/,
    hyperref: /\bhyperref\b/,
    secnum: /\bsecnumdepth\b|\\(?:(?:(?:re)?new|provide)command|[gex]?def|let)\s*\*?\s*\{?\s*\\(?:the(?:sub)*section|@seccntformat)\b|\\titleformat\b|\\titlelabel\b|\btitlesec\b|\b(?:sub)*section\s*\/\s*(?:name|number|aftername|numbering)\b/,
    capname: /\\(?:figurename|tablename)\b|\b(?:figurename|tablename)\s*=/,
    capnum: /\\(?:(?:(?:re)?new|provide)command|[gex]?def|let)\s*\*?\s*\{?\s*\\theH?(?:figure|table)\b|\\(?:counterwithin|counterwithout|numberwithin|@addtoreset)\s*\*?\s*(?:\[[^\]]*\]\s*)?\{\s*(?:figure|table)\s*\}/,
  };
  const LAYOUT_KEYS_ORDER = ["margin", "columns", "pagestyle", "hyperref", "size", "spacing", "indent", "parskip", "secnum", "capname", "capnum"];
  // 章节编号, 图表名称, 图表编号 (22.12, A3): the classes 版式 knows that have chapters, the commands whose definitions it
  // reads, and the lines and \ctexset keys it writes.
  const LAYOUT_CHAPTER_CLASSES = new Set(["report", "book", "amsbook", "ctexrep", "ctexbook"]);
  const DEFINED = new Map([["thesection", "secnum"], ["thesubsection", "secnum"], ["thesubsubsection", "secnum"], ["@seccntformat", "secnum"],
    ["figurename", "capname"], ["tablename", "capname"], ["thefigure", "capnum"], ["thetable", "capnum"], ["theHfigure", "capnum"], ["theHtable", "capnum"]]);
  const ZH_NUMBER_DEFS = { thesection: "\\chinese{section}", thesubsection: "（\\chinese{subsection}）", thesubsubsection: "\\arabic{subsubsection}" };
  const ZH_NUMBER_KEYS = { "section/name": ",、", "section/aftername": "", "subsection/aftername": "", "subsubsection/name": ",." };
  const CAPTION_NAMES = { zh: { figurename: "图", tablename: "表" }, en: { figurename: "Figure", tablename: "Table" } };
  const squash = (x) => String(x == null ? "" : x).replace(/\s+/g, "");
  // what a \ctexset key sets, as 版式 counts it: the section headings' numbers, the figure and table names, or neither
  function ctexKey(key) {
    const k = squash(key);
    if (/^(?:sub)*section(?:\/(?:name|number|aftername|numbering))?$/.test(k) || k === "secnumdepth") return "secnum";
    if (k === "figurename" || k === "tablename") return "capname";
    return null;
  }
  // The lines 图表编号 writes: by section in a class without chapters (the counters reset there, the number printed with
  // a hyphen, hyperref's anchors kept apart by section: it loads later and would name them figure.1 in every section);
  // by chapter, a chapter class already resets them and only the hyphen is written; 连续编号 in a chapter class stops
  // the reset. Figures and tables only: equations keep the class's numbering.
  function capnumLines(chapters, value) {
    const both = (f) => [f("figure"), f("table")];
    if (value === "within" && chapters) return both((x) => "\\renewcommand{\\the" + x + "}{\\thechapter-\\arabic{" + x + "}}");
    if (value === "within") return both((x) => "\\counterwithin{" + x + "}{section}").concat(both((x) => "\\renewcommand{\\the" + x + "}{\\arabic{section}-\\arabic{" + x + "}}"),
      both((x) => "\\providecommand{\\theH" + x + "}{\\theHsection.\\arabic{" + x + "}}"));
    if (value === "continuous" && chapters) return both((x) => "\\counterwithout{" + x + "}{chapter}");
    return [];
  }
  // 章节编号's Chinese style: 一、 （一） 1. -- \the… redefined so a \ref prints the number the heading shows (ctex's own
  // `number` key changes the heading only), the punctuation after it from ctex's `name`, no space after 、 or ）.
  const ZH_NUMBER_LINES = Object.keys(ZH_NUMBER_DEFS).map((k) => "\\renewcommand{\\" + k + "}{" + ZH_NUMBER_DEFS[k] + "}")
    .concat(["\\ctexset{" + Object.keys(ZH_NUMBER_KEYS).map((k) => k + "={" + ZH_NUMBER_KEYS[k] + "}").join(",") + "}"]);

  // `text` with every comment turned into spaces, so positions and line breaks stay.
  function masked(text) { return text.split("\n").map((l) => { const c = codePart(l); return c + " ".repeat(l.length - c.length); }).join("\n"); }
  function skipBlank(code, i) { while (i < code.length && (code[i] === " " || code[i] === "\t" || code[i] === "\n" || code[i] === "\r")) i += 1; return i; }

  // Each command of the masked `code`: {name, from, nameTo, depth (braces it sits in), cond (\if…\fi it sits in),
  // lineStart (only spaces before it on its line), prev (the command before)}.
  function scanCommands(code) {
    const out = [], n = code.length;
    let depth = 0, cond = 0;
    for (let i = 0; i < n; i++) {
      const c = code[i];
      if (c === "{") { depth += 1; continue; }
      if (c === "}") { depth = Math.max(0, depth - 1); continue; }
      if (c !== "\\") continue;
      let e = i + 1;
      while (e < n && /[A-Za-z@]/.test(code[e])) e += 1;
      if (e === i + 1) { i += 1; continue; }   // \\, \{, \%: one character, never a command we read
      const name = code.slice(i + 1, e), prev = out[out.length - 1] || null;
      const ls = code.lastIndexOf("\n", i - 1) + 1;
      out.push({ name: name, from: i, nameTo: e, depth: depth, cond: cond, lineStart: !code.slice(ls, i).trim(), prev: prev });
      if (name === "fi") cond = Math.max(0, cond - 1);
      // a primitive or \newif conditional runs to its \fi; etoolbox's \ifdef{…} and \ifthenelse{…} take braces
      else if (/^if/.test(name) && name !== "ifthenelse" && code[skipBlank(code, e)] !== "{" && !(prev && prev.name === "newif")) cond += 1;
      i = e - 1;
    }
    return out;
  }

  // The groups after a command, by `shape` ("o" an optional [..], "m" a mandatory {..}): {args: [{from, to, inner}], to}.
  function readArgs(code, at, shape) {
    const args = [];
    for (const s of shape) {
      const j = skipBlank(code, at);
      if (code[j] !== (s === "o" ? "[" : "{")) { if (s === "o") continue; return null; }
      const g = skipGroup(code, j, false);
      if (g < 0) return null;
      args.push({ from: j, to: g, opt: s === "o", inner: [j + 1, g - 1] });
      at = g;
    }
    return { args: args, to: at };
  }

  // A key=value list in [..] or {..}: its items {from, to, text, key, value}, split at commas outside braces.
  function optionList(code, arg) {
    const a = arg.inner[0], b = arg.inner[1], items = [];
    let depth = 0, start = a;
    const push = (end) => {
      const raw = code.slice(start, end), text = raw.trim();
      if (!text) return;
      const from = start + raw.length - raw.trimStart().length, eq = text.indexOf("=");
      items.push({ from: from, to: from + text.length, text: text, key: (eq < 0 ? text : text.slice(0, eq)).trim(),
        value: eq < 0 ? null : text.slice(eq + 1).trim().replace(/^\{([\s\S]*)\}$/, "$1").trim() });
    };
    for (let j = a; j < b; j++) {
      const c = code[j];
      if (c === "\\") { j += 1; continue; }
      if (c === "{") depth += 1;
      else if (c === "}") depth -= 1;
      else if (c === "," && depth === 0) { push(j); start = j + 1; }
    }
    push(b);
    return { open: arg.from, close: arg.to, from: a, to: b, items: items, square: code[arg.from] === "[" };
  }

  // A length in cm from "2cm", "25mm", "1in" (null for anything else).
  function cmOf(value) {
    const m = /^(\d+(?:\.\d+)?|\.\d+)\s*(cm|mm|in)$/.exec(String(value || "").trim());
    if (!m) return null;
    const n = Number(m[1]) * (m[2] === "mm" ? 0.1 : m[2] === "in" ? 2.54 : 1);
    return Math.round(n * 1000) / 1000;
  }
  const num = (x) => String(Math.round(Number(x) * 1000) / 1000);
  const cm = (x) => num(x) + "cm";

  // The preamble `pre` read: the class, the packages, the geometry commands, the setting lines, and `marks`: where a
  // setting is written in a form 版式 does not take, {key: [{line, why?}]}.
  function readPreamble(pre) {
    const code = masked(pre), cmds = scanCommands(code), starts = [0];
    for (let i = 0; i < pre.length; i++) if (pre[i] === "\n") starts.push(i + 1);
    const lineOf = (pos) => { let lo = 0, hi = starts.length - 1; while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] <= pos) lo = mid; else hi = mid - 1; } return lo + 1; };
    const lineEnd = (pos) => { const e = code.indexOf("\n", pos); return e < 0 ? code.length : e; };
    // a line of its own: the command first on its line, nothing after it, and a line break after (the preamble ends
    // where \begin{document} starts, which no setting line may share)
    const alone = (c, to) => c.lineStart && !code.slice(to, lineEnd(to)).trim() && lineEnd(to) < code.length;
    const top = (c) => c.depth === 0 && c.cond === 0;
    const r = { pre: pre, code: code, lineOf: lineOf, lineEnd: lineEnd, dc: null, packages: [], geometry: [], lines: [], ctexsets: [], marks: {} };
    const mark = (key, pos, why) => { (r.marks[key] = r.marks[key] || []).push({ line: lineOf(pos), why: why || null }); };
    for (const c of cmds) {
      const n = c.name;
      if (n === "documentclass") {
        const a = readArgs(code, c.nameTo, "om");
        if (!a || r.dc) continue;
        const opt = a.args.find((x) => x.opt), cls = a.args.find((x) => !x.opt);
        r.dc = { c: c, top: top(c), cls: code.slice(cls.inner[0], cls.inner[1]).trim(), opts: opt ? optionList(code, opt) : null, to: a.to, line: lineOf(c.from) };
        continue;
      }
      if (n === "usepackage" || n === "RequirePackage") {
        const a = readArgs(code, c.nameTo, "om");
        if (!a) continue;
        const opt = a.args.find((x) => x.opt), names = optionList(code, a.args.find((x) => !x.opt));
        r.packages.push({ c: c, names: names.items.map((x) => x.text), nameList: names, opts: opt ? optionList(code, opt) : null, from: c.from, to: a.to,
          line: lineOf(c.from), top: top(c), own: top(c) && alone(c, a.to) && names.items.length === 1 });
        continue;
      }
      if (n === "geometry") {
        const a = readArgs(code, c.nameTo, "m");
        if (a) r.geometry.push({ c: c, list: optionList(code, a.args[0]), from: c.from, to: a.to, line: lineOf(c.from), top: top(c), own: top(c) && alone(c, a.to) });
        continue;
      }
      const recognised = (kind, shape, value) => {
        const a = readArgs(code, c.nameTo, shape);
        if (a && top(c) && alone(c, a.to)) { r.lines.push({ kind: kind, c: c, args: a.args, value: value(a.args), from: code.lastIndexOf("\n", c.from) + 1, to: lineEnd(a.to), line: lineOf(c.from) }); return true; }
        return false;
      };
      const arg = (args, k) => code.slice(args[k].inner[0], args[k].inner[1]).trim();
      if (n === "setstretch" || n === "linespread") { if (!recognised(n, "m", (x) => arg(x, 0))) mark("spacing", c.from); continue; }
      if (named(n)) { if (!recognised(n, "", () => n)) mark("spacing", c.from); continue; }
      if (n === "baselinestretch" || n === "SetSinglespace" || /^setspace@/.test(n)) { mark("spacing", c.from); continue; }
      if (n === "pagestyle") { if (!recognised("pagestyle", "m", (x) => arg(x, 0))) mark("pagestyle", c.from); continue; }
      if (n === "thispagestyle" || /^ps@/.test(n)) { mark("pagestyle", c.from); continue; }
      if (n === "twocolumn" || n === "onecolumn") { mark("columns", c.from); continue; }
      if (n === "newgeometry") { mark("margin", c.from); continue; }
      if (n === "normalsize" && c.prev && DEFINERS.has(c.prev.name)) { mark("size", c.from); continue; }
      // 章节编号, 图表名称, 图表编号 (A3): \renewcommand{\thesection}{…} and their like, read where 版式 writes them --
      // \renewcommand (\providecommand for hyperref's \theH…) of one command, at the top, alone on its line; any other
      // definition of these commands is the person's own
      if (DEFINERS.has(n)) {
        let j = skipBlank(code, c.nameTo);
        const star = code[j] === "*";
        if (star) j = skipBlank(code, j + 1);
        const braced = code[j] === "{";
        if (braced) j = skipBlank(code, j + 1);
        const m = /^\\([A-Za-z@]+)/.exec(code.slice(j, j + 40)), key = m ? DEFINED.get(m[1]) : null;
        if (!key) continue;
        let after = skipBlank(code, j + m[0].length);
        if (braced) { if (code[after] !== "}") { mark(key, c.from); continue; } after += 1; }
        const a = !star && (n === "renewcommand" || n === "providecommand") ? readArgs(code, after, "m") : null;
        if (a && top(c) && alone(c, a.to)) r.lines.push({ kind: "def", name: m[1], definer: n, c: c, args: a.args, value: code.slice(a.args[0].inner[0], a.args[0].inner[1]).trim(), from: code.lastIndexOf("\n", c.from) + 1, to: lineEnd(a.to), line: lineOf(c.from) });
        else mark(key, c.from);
        continue;
      }
      if (n === "setcounter" || n === "addtocounter") {
        const a = readArgs(code, c.nameTo, "mm");
        if (!a) { if (/^\s*\{\s*secnumdepth\s*\}/.test(code.slice(c.nameTo, c.nameTo + 40))) mark("secnum", c.from); continue; }
        if (code.slice(a.args[0].inner[0], a.args[0].inner[1]).trim() !== "secnumdepth") continue;
        if (n === "setcounter" && top(c) && alone(c, a.to)) r.lines.push({ kind: "secnumdepth", c: c, args: a.args, value: code.slice(a.args[1].inner[0], a.args[1].inner[1]).trim(), from: code.lastIndexOf("\n", c.from) + 1, to: lineEnd(a.to), line: lineOf(c.from) });
        else mark("secnum", c.from);
        continue;
      }
      if (n === "counterwithin" || n === "counterwithout" || n === "numberwithin" || n === "@addtoreset") {
        const star = code[c.nameTo] === "*", a = readArgs(code, c.nameTo + (star ? 1 : 0), n === "numberwithin" ? "omm" : "mm");
        const args = a ? a.args.filter((x) => !x.opt) : null, what = args && args.length === 2 ? code.slice(args[0].inner[0], args[0].inner[1]).trim() : "";
        if (what !== "figure" && what !== "table") { if (!a && /^\*?\s*(?:\[[^\]]*\]\s*)?\{\s*(?:figure|table)\s*\}/.test(code.slice(c.nameTo, c.nameTo + 60))) mark("capnum", c.from); continue; }
        if (!star && (n === "counterwithin" || n === "counterwithout") && top(c) && alone(c, a.to)) r.lines.push({ kind: n, c: c, args: args, value: what, within: code.slice(args[1].inner[0], args[1].inner[1]).trim(), from: code.lastIndexOf("\n", c.from) + 1, to: lineEnd(a.to), line: lineOf(c.from) });
        else mark("capnum", c.from);
        continue;
      }
      // \ctexset{…}: a line of its own is read key by key (the keys 版式 writes are its own, others left alone); one in a
      // macro or a condition, or sharing its line, makes what it sets the person's own
      if (n === "ctexset") {
        const a = readArgs(code, c.nameTo, "m");
        if (!a) continue;
        const list = optionList(code, a.args[0]);
        if (top(c) && alone(c, a.to)) r.ctexsets.push({ c: c, list: list, from: code.lastIndexOf("\n", c.from) + 1, to: lineEnd(a.to), line: lineOf(c.from) });
        else list.items.forEach((it) => { const k = ctexKey(it.key); if (k) mark(k, c.from); });
        continue;
      }
      // the caption package's names: the person's own
      if (n === "captionsetup") {
        const a = readArgs(code, c.nameTo, "om"), arg = a && a.args.find((x) => !x.opt);
        if (arg && optionList(code, arg).items.some((it) => /^(?:name|figurename|tablename)$/.test(it.key))) mark("capname", c.from);
        continue;
      }
      if (LENGTH_SETTERS.has(n)) {
        // \setlength{\parindent}{2em} or \setlength\parindent{2em}: what it sets is its first argument
        let j = skipBlank(code, c.nameTo), braced = code[j] === "{";
        if (braced) j = skipBlank(code, j + 1);
        const m = /^\\([A-Za-z@]+)/.exec(code.slice(j, j + 40));
        if (!m) continue;
        const target = m[1], after = skipBlank(code, j + m[0].length) + (braced ? 1 : 0);
        const key = target === "parindent" ? "indent" : target === "parskip" ? "parskip"
          : /^(?:textwidth|textheight|oddsidemargin|evensidemargin|topmargin|hoffset|voffset)$/.test(target) ? "margin" : null;
        if (!key) continue;
        const a = n === "setlength" && (!braced || code[after - 1] === "}") ? readArgs(code, after, "m") : null;
        if (a && top(c) && alone(c, a.to) && (key === "indent" || key === "parskip")) {
          r.lines.push({ kind: key, c: c, args: a.args, value: code.slice(a.args[0].inner[0], a.args[0].inner[1]).trim(), from: code.lastIndexOf("\n", c.from) + 1, to: lineEnd(a.to), line: lineOf(c.from) });
        } else mark(key, c.from);
        continue;
      }
      // a length set as TeX does it, \parindent=0pt or \parindent 0pt (never one only read, as in \hspace{\parindent})
      if (/^(?:parindent|parskip|textwidth|textheight|oddsidemargin|evensidemargin|topmargin)$/.test(n) && !(c.prev && LENGTH_SETTERS.has(c.prev.name) && c.prev.nameTo >= c.from - 2)) {
        const next = code[skipBlank(code, c.nameTo)];
        if (next === "=" || /[\d.-]/.test(next || "")) mark(n === "parindent" ? "indent" : n === "parskip" ? "parskip" : "margin", c.from);
      }
    }
    return r;
  }

  // Each setting of the preamble `pre` (with `local`, the project's .sty/.cls it loads: [{path, text}]): {value, mine,
  // occ (what a write changes), ...}, and the document's facts: the class, whether 版式 knows it, ctex, the stretch of
  // its single line (`s0`).
  function layoutOf(pre, local) {
    const r = readPreamble(pre), out = { r: r, settings: {} };
    const dc = r.dc;
    if (!dc) { out.why = t("主文件里没有找到 {command}。", { command: "\\documentclass" }); return out; }
    out.cls = dc.cls;
    if (SLIDE_CLASSES.test(dc.cls)) { out.why = t("幻灯片（beamer）不用这些版式设置。"); return out; }
    const known = LAYOUT_CLASSES.has(dc.cls) && dc.top, ctexClass = /^ctex/.test(dc.cls);
    const pk = r.packages.filter((p) => p.top), has = (name) => pk.some((p) => p.names.includes(name));
    const ctexPkg = pk.find((p) => p.own && p.names[0] === "ctex");
    out.known = known; out.ctexClass = ctexClass; out.ctex = ctexClass || has("ctex");
    out.s0 = out.ctex ? 1.3 : 1;   // ctex's own \linespread{1.3} (ctex.sty), measured 22.12
    out.ctexPkg = ctexPkg || null;
    const S = {};
    LAYOUT_KEYS_ORDER.forEach((k) => { S[k] = { value: "", occ: [], mine: null }; });
    out.settings = S;
    const mine = (key, line, extra) => { if (!S[key].mine) S[key].mine = Object.assign({ line: line }, extra || {}); };
    Object.keys(r.marks).forEach((k) => mine(k, r.marks[k][0].line));
    const items = (list) => (list ? list.items : []);
    const dcItems = items(dc.opts);
    const classOwn = known ? null : { line: dc.line, why: t("文档类 {cls} 的选项这里不认识，请在 {command} 那一行自己改。", { cls: dc.cls, command: "\\documentclass" }) };
    // the packages a hidden line (a condition, a macro) or a project file loads are their own
    r.packages.filter((p) => !p.top).forEach((p) => p.names.forEach((nm) => {
      if (nm === "geometry") mine("margin", p.line);
      if (nm === "setspace" || nm === "doublespace") mine("spacing", p.line);
      if (nm === "parskip") { mine("parskip", p.line); mine("indent", p.line); }
      if (nm === "nopageno") mine("pagestyle", p.line);
      if (nm === "hyperref") mine("hyperref", p.line);
      if (nm === "ctex") { mine("size", p.line); mine("secnum", p.line); }
      if (nm === "babel" || nm === "polyglossia") mine("capname", p.line);
    }));
    pk.forEach((p) => p.names.forEach((nm) => {
      if (MARGIN_PACKAGES.has(nm)) mine("margin", p.line);
      if (nm === "titlesec") mine("secnum", p.line);   // it sets the headings and their numbers its own way
      if (nm === "doublespace") mine("spacing", p.line);
    }));
    r.geometry.filter((g) => !g.top).forEach((g) => mine("margin", g.line));

    // 纸张 is not a setting (A3): the class's or geometry's paper is left as written, never read or changed
    const geoLists = pk.filter((p) => p.names.includes("geometry") && p.opts).map((p) => ({ list: p.opts, line: p.line, pkg: p }))
      .concat(r.geometry.filter((g) => g.top).map((g) => ({ list: g.list, line: g.line, geo: g })));
    // 页边距: geometry's margin, top, bottom, left and right in cm, mm or in; any other key that places the text is the person's
    const margin = {}, marginLines = [];
    geoLists.forEach((g) => g.list.items.forEach((it) => {
      if (PAPER_ITEM.test(it.key) || /^paper/.test(it.key)) return;
      if (MARGIN_KEYS.has(it.key) && it.value != null && cmOf(it.value) != null) {
        if (it.key in margin && margin[it.key] !== cmOf(it.value)) mine("margin", g.line);
        margin[it.key] = cmOf(it.value); marginLines.push(g.line); S.margin.occ.push({ list: g.list, item: it }); return;
      }
      if (LAYOUT_KEYS.test(it.key) && !/^(?:landscape|portrait|twoside)$/.test(it.key)) mine("margin", g.line);
    }));
    // 正文字号: a class option (10pt, 11pt, 12pt; zihao=5 or -4 for a ctex class), or the ctex package's zihao
    const sizeSeen = [];
    dcItems.forEach((it) => {
      if (/^\d+(?:\.\d+)?pt$/.test(it.key) && it.value == null) {
        if (known && PAGE_SIZES.has(it.key)) { sizeSeen.push({ v: it.key, line: dc.line }); S.size.occ.push({ list: dc.opts, item: it }); } else mine("size", dc.line, known ? null : classOwn);
      } else if (it.key === "zihao" || it.key === "fontsize") {
        if (known && ctexClass && it.key === "zihao" && ZIHAO.has(it.value)) { sizeSeen.push({ v: "zihao=" + it.value, line: dc.line }); S.size.occ.push({ list: dc.opts, item: it }); } else mine("size", dc.line, known ? null : classOwn);
      }
    });
    pk.filter((p) => p.names.includes("ctex")).forEach((p) => items(p.opts).forEach((it) => {
      if (it.key !== "zihao") return;
      if (p.own && ZIHAO.has(it.value)) { sizeSeen.push({ v: "zihao=" + it.value, line: p.line }); S.size.occ.push({ list: p.opts, item: it }); } else mine("size", p.line);
    }));
    if (new Set(sizeSeen.map((x) => (/^zihao/.test(x.v) ? "zihao" : "pt"))).size > 1) mine("size", sizeSeen[sizeSeen.length - 1].line);   // never both
    // 分栏: twocolumn or onecolumn among the class options
    const colSeen = [];
    dcItems.forEach((it) => { if ((it.key === "twocolumn" || it.key === "onecolumn") && it.value == null) { if (known) { colSeen.push({ v: it.key === "twocolumn" ? "twocolumn" : "", line: dc.line }); S.columns.occ.push({ list: dc.opts, item: it }); } else mine("columns", dc.line, classOwn); } });
    // 行距: \setstretch, \linespread, setspace's own commands and options, ctex's linespread option
    const size = sizeSeen.length ? sizeSeen[0].v : "", ptsize = size === "11pt" ? 1 : size === "12pt" || size === "zihao=-4" ? 2 : 0;
    const spaceSeen = [];
    r.lines.filter((l) => l.kind === "setstretch" || l.kind === "linespread" || named(l.kind)).forEach((l) => {
      const s = named(l.kind) ? named(l.kind)[ptsize] : Number(l.value);
      if (!(s > 0) || (l.kind === "setstretch" && !has("setspace"))) { mine("spacing", l.line); return; }
      spaceSeen.push({ s: s, line: l.line }); S.spacing.occ.push({ line: l });
    });
    pk.filter((p) => p.names.includes("setspace")).forEach((p) => items(p.opts).forEach((it) => {
      if (!named(it.key)) return;
      if (p.own) { spaceSeen.push({ s: named(it.key)[ptsize], line: p.line }); S.spacing.occ.push({ list: p.opts, item: it, setspace: true }); } else mine("spacing", p.line);
    }));
    [[dc.opts, dc.line, ctexClass && known], [ctexPkg && ctexPkg.opts, ctexPkg && ctexPkg.line, !!ctexPkg]].forEach(([list, line, ok]) => items(list).forEach((it) => {
      if (it.key !== "linespread") return;
      if (ok && Number(it.value) > 0) { spaceSeen.push({ s: Number(it.value), line: line }); S.spacing.occ.push({ list: list, item: it, ctex: true }); } else mine("spacing", line);
    }));
    // 首行缩进 and 段间距: \setlength lines; the parskip package (skip=, and indent to keep the class's indent)
    const indentSeen = [], parSeen = [];
    r.lines.filter((l) => l.kind === "indent").forEach((l) => {
      const v = /^0(?:\.0*)?(?:pt|em|cm|mm)?$/.test(l.value) ? "0pt" : /^2(?:\.0*)?(?:em|\\ccwd)$/.test(l.value) ? "2em" : null;
      if (v) { indentSeen.push({ v: v, line: l.line }); S.indent.occ.push({ line: l }); } else mine("indent", l.line);
    });
    const parskipValue = (x) => (/^0(?:\.0*)?(?:pt|em)?$/.test(x) ? "" : /^0?\.5\s*\\baselineskip$/.test(x) ? "half" : /^1?\s*\\baselineskip$/.test(x) ? "one" : null);
    r.lines.filter((l) => l.kind === "parskip").forEach((l) => {
      const v = parskipValue(l.value);
      if (v != null) { parSeen.push({ v: v, line: l.line }); S.parskip.occ.push({ line: l }); } else mine("parskip", l.line);
    });
    pk.filter((p) => p.names.includes("parskip")).forEach((p) => {
      const its = items(p.opts), skip = its.find((it) => it.key === "skip"), keeps = its.some((it) => it.key === "indent" && it.value == null);
      const v = skip ? parskipValue(skip.value || "") : "half";   // parskip's default: .5\baselineskip
      if (!p.own || v == null || its.some((it) => it.key !== "skip" && it.key !== "indent") || its.some((it) => it.key === "indent" && it.value != null)) { mine("parskip", p.line); mine("indent", p.line); return; }
      parSeen.push({ v: v, line: p.line }); S.parskip.occ.push({ pkg: p, skip: skip || null });
      if (!keeps) mine("indent", p.line);   // parskip without indent sets the indent to 0
    });
    // 页码: \pagestyle{plain} or {empty}, the nopageno package
    const styleSeen = [];
    r.lines.filter((l) => l.kind === "pagestyle").forEach((l) => {
      if (l.value === "plain" || l.value === "empty") { styleSeen.push({ v: l.value, line: l.line }); S.pagestyle.occ.push({ line: l }); } else mine("pagestyle", l.line);
    });
    pk.filter((p) => p.names.includes("nopageno")).forEach((p) => {
      if (p.own && !p.opts) { styleSeen.push({ v: "empty", line: p.line, nopageno: true }); S.pagestyle.occ.push({ pkg: p }); } else mine("pagestyle", p.line);
    });
    // 超链接: hyperref loaded
    const hyper = pk.filter((p) => p.names.includes("hyperref"));
    S.hyperref.value = hyper.length ? "on" : "off";
    S.hyperref.occ = hyper.map((p) => ({ pkg: p }));

    // 章节编号, 图表名称, 图表编号 (A3)
    const chapters = known && LAYOUT_CHAPTER_CLASSES.has(dc.cls);
    out.chapters = chapters;
    const heading = ctexPkg && items(ctexPkg.opts).some((it) => it.key === "heading" && (it.value == null || it.value === "true"));
    // Chinese numbers need ctex's headings: a ctex class, or the ctex package with heading=true (without it ctex has no
    // section keys: "The key 'ctex/section/name' is unknown", measured); Chinese names need a font with them
    out.zhNumbers = (ctexClass && known) || !!heading;
    out.zhNumbersWhy = out.zhNumbers ? null : out.ctex ? t("中文编号需要 ctex 宏包的 {option} 选项", { option: "heading=true" }) : t("中文编号需要中文文档类");
    out.cjk = out.ctex || has("xeCJK");
    out.capDefault = out.ctex ? "zh" : "en";
    const defs = (names) => r.lines.filter((l) => l.kind === "def" && names.includes(l.name));
    const classNumbers = known ? null : { line: dc.line, why: t("文档类 {cls} 的编号这里不认识，请自己设置。", { cls: dc.cls }) };
    // 章节编号: 一、（一）1. as the three \renewcommand lines and ctex's four keys, all of them; 不编号 as secnumdepth
    // 0 (-1 with chapters: a chapter's level is 0)
    const zhLines = [], zhItems = [];
    defs(["thesection", "thesubsection", "thesubsubsection", "@seccntformat"]).forEach((l) => {
      const want = Object.prototype.hasOwnProperty.call(ZH_NUMBER_DEFS, l.name) ? ZH_NUMBER_DEFS[l.name] : null;
      if (l.definer === "renewcommand" && want != null && squash(l.value) === squash(want) && !zhLines.some((x) => x.name === l.name)) zhLines.push(l); else mine("secnum", l.line);
    });
    const nameSeen = [];
    r.ctexsets.forEach((set) => set.list.items.forEach((it) => {
      const k = squash(it.key), kind = ctexKey(k);
      if (kind === "secnum") {
        const want = Object.prototype.hasOwnProperty.call(ZH_NUMBER_KEYS, k) ? ZH_NUMBER_KEYS[k] : null;
        if (want != null && it.value != null && squash(it.value) === want && !zhItems.some((x) => x.k === k)) zhItems.push({ set: set, item: it, k: k }); else mine("secnum", set.line);
      } else if (kind === "capname") {
        if (it.value == null) mine("capname", set.line); else nameSeen.push({ name: k, v: squash(it.value), line: set.line, occ: { set: set, item: it, name: k } });
      }
    }));
    const zh = zhLines.length || zhItems.length;
    if (zh && (zhLines.length < 3 || zhItems.length < 4 || !out.zhNumbers)) mine("secnum", (zhLines[0] || zhItems[0].set).line);
    const depth = r.lines.filter((l) => l.kind === "secnumdepth"), none = [];
    depth.forEach((l) => { if (/^-?\d+$/.test(l.value) && Number(l.value) <= (chapters ? -1 : 0)) none.push(l); else mine("secnum", l.line); });
    if (zh && none.length) mine("secnum", none[0].line);
    S.secnum.value = zh ? "zh" : none.length ? "none" : "";
    S.secnum.occ = zhLines.map((l) => ({ line: l })).concat(zhItems, none.map((l) => ({ line: l })));
    // 图表名称: \renewcommand{\figurename}{…} and {\tablename}, or ctex's figurename= and tablename=; the class's own
    // names when none is set. babel and polyglossia set the names again at \begin{document}: theirs.
    defs(["figurename", "tablename"]).forEach((l) => { if (l.definer === "renewcommand") nameSeen.push({ name: l.name, v: squash(l.value), line: l.line, occ: { line: l, name: l.name } }); else mine("capname", l.line); });
    const lang = pk.find((p) => p.names.some((nm) => nm === "babel" || nm === "polyglossia"));
    if (lang) mine("capname", lang.line, { why: t("文稿用 {pkg} 设置语言，图表名称由它决定。", { pkg: lang.names.find((nm) => nm === "babel" || nm === "polyglossia") }) });
    pk.filter((p) => p.names.includes("caption")).forEach((p) => { if (items(p.opts).some((it) => /^(?:name|figurename|tablename)$/.test(it.key))) mine("capname", p.line); });
    S.capname.value = out.capDefault;
    if (nameSeen.length) {
      const one = (name) => Array.from(new Set(nameSeen.filter((x) => x.name === name).map((x) => x.v)));
      const fig = one("figurename"), tab = one("tablename");
      const v = fig.length === 1 && tab.length === 1 ? Object.keys(CAPTION_NAMES).find((x) => CAPTION_NAMES[x].figurename === fig[0] && CAPTION_NAMES[x].tablename === tab[0]) : null;
      if (v && (v === "en" || out.cjk)) { S.capname.value = v; S.capname.occ = nameSeen.map((x) => x.occ); } else mine("capname", nameSeen[0].line);
    }
    // 图表编号: exactly the lines the tool writes (capnumLines), with or without the \theH… lines; anything else theirs
    const numLines = r.lines.filter((l) => (l.kind === "def" && /^theH?(?:figure|table)$/.test(l.name)) || l.kind === "counterwithin" || l.kind === "counterwithout");
    if (numLines.length) {
      const sig = (l) => (l.kind === "def" ? "\\" + l.definer + "{\\" + l.name + "}{" + squash(l.value) + "}" : "\\" + l.kind + "{" + l.value + "}{" + l.within + "}");
      const sigs = numLines.map(sig);
      const form = ["within", "continuous"].find((v) => {
        const want = capnumLines(chapters, v).map(squash), need = want.filter((x) => !/\\theH/.test(x));
        return want.length && need.every((x) => sigs.filter((y) => y === x).length === 1) && sigs.every((x) => want.filter((y) => y === x).length === 1);
      });
      if (form) { S.capnum.value = form; S.capnum.occ = numLines.map((l) => ({ line: l })); } else mine("capnum", numLines[0].line);
    }
    if (!known) { mine("secnum", dc.line, classNumbers); mine("capnum", dc.line, classNumbers); }

    // the values; settings that disagree are the person's own
    const agree = (key, seen, v) => {
      if (!seen.length) return;
      const vals = new Set(seen.map(v));
      if (vals.size > 1) { mine(key, seen[seen.length - 1].line); return; }
      S[key].value = v(seen[0]);
    };
    agree("size", sizeSeen, (x) => x.v);
    agree("columns", colSeen, (x) => x.v);
    agree("indent", indentSeen, (x) => x.v);
    agree("parskip", parSeen, (x) => x.v);
    if (styleSeen.some((x) => x.nopageno)) S.pagestyle.value = "empty"; else agree("pagestyle", styleSeen, (x) => x.v);
    if (spaceSeen.length) {
      if (new Set(spaceSeen.map((x) => x.s)).size > 1) mine("spacing", spaceSeen[spaceSeen.length - 1].line);
      const n = Math.round((spaceSeen[0].s / out.s0) * 100) / 100;
      S.spacing.n = n;
      S.spacing.value = SPACINGS.find((p) => Math.abs(Number(p) - n) < 0.006) || "custom";
    }
    const sides = ["top", "bottom", "left", "right"];
    if (S.margin.occ.length) {
      const each = {};
      sides.forEach((k) => { each[k] = k in margin ? margin[k] : "margin" in margin ? margin.margin : null; });
      if (sides.some((k) => each[k] == null)) mine("margin", marginLines[0]);
      else {
        Object.assign(S.margin, each);
        const one = Object.keys(margin).length === 1 && "margin" in margin ? num(margin.margin) : null;
        S.margin.value = one && MARGIN_PRESETS.includes(one) ? one : "custom";
      }
    }
    if (!known) ["size", "columns"].forEach((k) => mine(k, dc.line, classOwn));
    // a project .sty or .cls that touches a setting makes it its own
    (local || []).forEach((f) => {
      const code = masked(String(f.text || "")), lines = code.split("\n");
      LAYOUT_KEYS_ORDER.forEach((k) => {
        const at = lines.findIndex((l) => LOCAL_MARKERS[k].test(l));
        if (at >= 0) mine(k, at + 1, { file: f.path });
      });
    });
    return out;
  }

  // Edits of an option list, merged so that touching ranges never overlap: `remove` (items), `replace` (Map item, text),
  // `add` (texts appended). `empty`: what to do when no item is left -- "drop" the brackets, else leave them empty.
  function listChanges(r, list, ops, empty) {
    const its = list.items, gone = new Set(ops.remove || []), rep = ops.replace || new Map(), add = ops.add || [];
    const kept = its.filter((it) => !gone.has(it));
    if (!kept.length) {
      if (add.length) return [{ from: list.from, to: list.to, insert: add.join(",") }];
      if (!its.length) return [];
      return empty === "drop" ? [{ from: list.open, to: list.close, insert: "" }] : [{ from: list.from, to: list.to, insert: "" }];
    }
    const out = [];
    const lastKept = its.lastIndexOf(kept[kept.length - 1]);
    its.forEach((it, k) => {
      if (gone.has(it)) out.push(k < lastKept ? { from: it.from, to: its[k + 1].from, insert: "" } : { from: its[k - 1].to, to: it.to, insert: "" });
      else if (rep.has(it)) out.push({ from: it.from, to: it.to, insert: rep.get(it) });
    });
    if (add.length) {
      const sep = its.length > 1 && /,\s/.test(r.pre.slice(its[0].to, its[1].from)) ? ", " : ",";
      out.push(kept.length ? { from: kept[kept.length - 1].to, to: kept[kept.length - 1].to, insert: add.map((x) => sep + x).join("") }
        : { from: list.from, to: list.from, insert: add.join(sep) });
    }
    return out;
  }
  // Specs sorted, those that touch merged into one (an insert at the end of a deletion, two deletions end to end).
  function merged(specs) {
    const s = specs.slice().sort((a, b) => a.from - b.from || a.to - b.to), out = [];
    s.forEach((x) => {
      const last = out[out.length - 1];
      if (last && x.from <= last.to) { last.insert += x.insert || ""; last.to = Math.max(last.to, x.to); } else out.push({ from: x.from, to: x.to, insert: x.insert || "" });
    });
    return out;
  }

  // The specs of a batch of option edits [{list, remove?: item, replace?: [item, text], add?: text}], by list.
  function optionChanges(r, edits, empty) {
    const byList = new Map();
    edits.forEach((e) => {
      const o = byList.get(e.list) || { remove: [], replace: new Map(), add: [] };
      if (e.remove) o.remove.push(e.remove);
      if (e.replace) o.replace.set(e.replace[0], e.replace[1]);
      if (e.add) o.add.push(e.add);
      byList.set(e.list, o);
    });
    const out = [];
    byList.forEach((o, list) => out.push(...listChanges(r, list, o, empty)));
    return out;
  }
  // `text` added to the class's options: in its [..], or a new [..] after \documentclass.
  function classAdd(r, text) {
    return r.dc.opts ? optionChanges(r, [{ list: r.dc.opts, add: text }]) : [{ from: r.dc.c.nameTo, to: r.dc.c.nameTo, insert: "[" + text + "]" }];
  }
  function packageAdd(r, p, text) {
    return p.opts ? optionChanges(r, [{ list: p.opts, add: text }]) : [{ from: p.c.nameTo, to: p.c.nameTo, insert: "[" + text + "]" }];
  }
  // A whole line removed, its line break with it.
  function lineGone(r, from, to) { const s = r.code.lastIndexOf("\n", from - 1) + 1; return { from: s, to: r.lineEnd(to == null ? from : to) + 1, insert: "" }; }
  // New lines, each of its own, after the last \usepackage (hyperref: before a package that must follow it, else last).
  function newLines(r, lines, hyper) {
    const pk = r.packages.filter((p) => p.top);
    if (hyper) {
      const after = pk.find((p) => p.names.some((n) => HYPERREF_AFTER.has(n)));
      if (after) { const s = r.code.lastIndexOf("\n", after.from - 1) + 1; return { from: s, to: s, insert: lines.join("\n") + "\n" }; }
    }
    const last = pk.filter((p) => hyper || !p.names.some((n) => n === "hyperref" || HYPERREF_AFTER.has(n))).pop();
    const at = r.lineEnd(last ? last.to : r.dc.to);
    return { from: at, to: at, insert: "\n" + lines.join("\n") };
  }

  // One setting written: {changes, selection} for the editor's dispatch, {refused}, or null when it already is so.
  // `opts`: {local, text (the whole main file, for hyperref's users)}.
  function layoutSpec(state, key, value, opts) {
    opts = opts || {};
    const text = state.doc.toString(), read = readLatex(text);
    if (!read.preamble) return { refused: t("主文件里没有 {command}，版式还不能设置。", { command: "\\begin{document}" }) };
    const L = layoutOf(text.slice(0, read.preamble.to), opts.local), r = L.r;
    if (L.why) return { refused: L.why };
    const S = L.settings[key];
    if (!S) return null;
    if (S.mine) return { refused: S.mine.why || t("导言区第 {n} 行已有自己写的设置", { n: S.mine.line }) };
    let specs = [];
    const gone = (o) => (o.line ? lineGone(r, o.line.c.from, o.line.to) : lineGone(r, o.pkg.from, o.pkg.to));
    const argText = (l, k, s) => ({ from: l.args[k].inner[0], to: l.args[k].inner[1], insert: s });
    // what 章节编号 and 图表名称 found taken away: each line whole; \ctexset keys one by one, the line gone when none is left
    const removed = (occ) => {
      const out = [], bySet = new Map();
      occ.forEach((o) => { if (o.line) out.push(gone(o)); else bySet.set(o.set, (bySet.get(o.set) || []).concat([o.item])); });
      bySet.forEach((its, set) => out.push(...(its.length === set.list.items.length ? [lineGone(r, set.c.from, set.to)] : listChanges(r, set.list, { remove: its }))));
      return out;
    };
    if (key === "margin") {
      const v = value || {};
      const items = !v.preset ? [] : v.preset === "custom" ? ["top", "bottom", "left", "right"].map((k) => k + "=" + cm(v[k])) : ["margin=" + cm(v.preset)];
      if (v.preset === "custom" && ["top", "bottom", "left", "right"].some((k) => !(Number(v[k]) >= 0 && Number(v[k]) <= 10))) return { refused: t("页边距要在 0 到 10 厘米之间。") };
      if (v.preset === S.value && (v.preset !== "custom" || ["top", "bottom", "left", "right"].every((k) => Number(v[k]) === S[k]))) return null;
      const edits = S.occ.map((o) => ({ list: o.list, remove: o.item }));
      const pk = r.packages.filter((p) => p.top && p.names.includes("geometry"));
      const ownPkg = pk.find((p) => p.own), ownGeo = r.geometry.find((g) => g.own);
      const target = S.occ.length ? S.occ[0].list : ownPkg && ownPkg.opts ? ownPkg.opts : ownGeo ? ownGeo.list : null;
      if (items.length && target) items.forEach((x) => edits.push({ list: target, add: x }));
      else if (items.length && ownPkg) specs.push(...packageAdd(r, ownPkg, items.join(",")));
      else if (items.length) specs.push(newLines(r, [pk.length ? "\\geometry{" + items.join(",") + "}" : "\\usepackage[" + items.join(",") + "]{geometry}"]));
      // back to 默认: a geometry line the margins leave empty goes, unless something else still needs geometry
      const emptied = (list) => list.items.every((it) => edits.some((e) => e.list === list && e.remove === it)) && !edits.some((e) => e.list === list && e.add);
      const others = /\\(?:geometry|newgeometry|restoregeometry|savegeometry|loadgeometry)\b/.test(masked(text).slice(read.preamble.to));
      const lines = [];
      r.geometry.filter((g) => g.own && emptied(g.list)).forEach((g) => lines.push(g));
      const geoLeft = r.geometry.filter((g) => !lines.includes(g));
      pk.filter((p) => p.own && p.opts && emptied(p.opts) && !geoLeft.length && !others).forEach((p) => lines.push(p));
      specs.push(...optionChanges(r, edits.filter((e) => !lines.some((l) => (l.list || l.opts) === e.list)), "drop"), ...lines.map((l) => lineGone(r, l.from, l.to)));
    } else if (key === "size") {
      if (value === S.value) return null;
      const zihao = /^zihao=/.test(value);
      if (zihao && !(L.ctexClass || L.ctexPkg)) return { refused: t("五号、小四这些字号要用 ctex。") };
      const edits = [];
      const same = S.occ.filter((o) => (o.item.key === "zihao") === zihao);
      S.occ.forEach((o) => { if (!value || o !== same[0]) edits.push({ list: o.list, remove: o.item }); });
      if (value && same.length) edits.push({ list: same[0].list, replace: [same[0].item, value] });
      const home = zihao && !L.ctexClass ? L.ctexPkg : null, homeOpts = home ? home.opts : r.dc.opts;
      if (value && !same.length && homeOpts) edits.push({ list: homeOpts, add: value });
      specs = optionChanges(r, edits, "drop");
      if (value && !same.length && !homeOpts) specs.push({ from: (home ? home.c : r.dc.c).nameTo, to: (home ? home.c : r.dc.c).nameTo, insert: "[" + value + "]" });
    } else if (key === "columns") {
      if (value === S.value) return null;
      if (value && S.occ.length) specs = optionChanges(r, S.occ.map((o, k) => (k === 0 ? { list: o.list, replace: [o.item, value] } : { list: o.list, remove: o.item })), "drop");
      else if (value) specs = classAdd(r, value);
      else specs = optionChanges(r, S.occ.map((o) => ({ list: o.list, remove: o.item })), "drop");
    } else if (key === "spacing") {
      const n = value && value !== "custom" ? Number(value) : value === "custom" ? Number(opts.n) : 0;
      if (value && !(n >= 0.5 && n <= 4)) return { refused: t("行距要在 0.5 到 4 倍之间。") };
      if (!value && !S.occ.length) return null;
      if (value && S.occ.length && Math.abs((S.n || 0) - n) < 0.006) return null;
      const s = num(n * L.s0), edits = [];
      S.occ.forEach((o) => {
        if (o.list) {
          if (o.ctex && value) edits.push({ list: o.list, replace: [o.item, "linespread=" + s] });
          else edits.push({ list: o.list, remove: o.item });
          if (o.setspace && value && !specs.some((x) => x.setspace)) specs.push(Object.assign(newLines(r, ["\\setstretch{" + s + "}"]), { setspace: true }));
        } else if (!value) specs.push(gone(o));
        else if (named(o.line.kind)) specs.push({ from: o.line.c.from, to: o.line.c.nameTo, insert: "\\setstretch{" + s + "}" });
        else specs.push(argText(o.line, 0, s));
      });
      specs.push(...optionChanges(r, edits, "drop"));
      if (value && !S.occ.length) {
        const setspace = r.packages.some((p) => p.top && p.names.includes("setspace"));
        specs.push(newLines(r, L.ctex ? ["\\linespread{" + s + "}"] : setspace ? ["\\setstretch{" + s + "}"] : ["\\usepackage{setspace}", "\\setstretch{" + s + "}"]));
      }
    } else if (key === "indent") {
      if (value === S.value) return null;
      if (!value) specs = S.occ.map(gone);
      else if (S.occ.length) specs = S.occ.map((o) => argText(o.line, 0, value));
      else specs = [newLines(r, ["\\setlength{\\parindent}{" + value + "}"])];
    } else if (key === "parskip") {
      if (value === S.value) return null;
      const len = value === "half" ? "0.5\\baselineskip" : "\\baselineskip";
      if (!value) specs = S.occ.map(gone);
      else if (S.occ.length) {
        S.occ.forEach((o) => {
          if (o.line) specs.push(argText(o.line, 0, len));
          else if (o.skip) specs.push(...optionChanges(r, [{ list: o.pkg.opts, replace: [o.skip, "skip=" + len] }]));
          else specs.push(...packageAdd(r, o.pkg, "skip=" + len));
        });
      } else specs = [newLines(r, ["\\usepackage[skip=" + len + ",indent]{parskip}"])];
    } else if (key === "pagestyle") {
      if (value === S.value) return null;
      const lines = S.occ.filter((o) => o.line), nopageno = S.occ.filter((o) => o.pkg);
      if (!value) specs = S.occ.map(gone);
      else if (value === "plain") {
        // nopageno's line becomes \pagestyle{plain} in place, or the \pagestyle lines change
        specs = lines.map((o) => argText(o.line, 0, "plain")).concat(nopageno.map((o, k) => (k || lines.length ? gone(o) : { from: o.pkg.from, to: o.pkg.to, insert: "\\pagestyle{plain}" })));
        if (!lines.length && !nopageno.length) specs.push(newLines(r, ["\\pagestyle{plain}"]));
      } else {
        specs = lines.filter((o) => o.line.value === "plain").map(gone);
        specs.push(newLines(r, ["\\usepackage{nopageno}"]));
      }
    } else if (key === "hyperref") {
      if (value === S.value) return null;
      if (value === "on") specs = [newLines(r, ["\\usepackage[hidelinks]{hyperref}"], true)];
      else {
        const body = masked(text), pk = r.packages.filter((p) => p.top);
        const user = NEEDS_HYPERREF.exec(body) || (/\\url(?![A-Za-z@])/.test(body) && !pk.some((p) => p.names.some((n) => n === "url" || n === "xurl")) ? ["\\url"] : null);
        const dependent = pk.find((p) => p.names.some((n) => n === "bookmark" || n === "hypcap"));
        if (user || dependent) return { refused: t("文稿用到了 {command}，它需要 hyperref，不能关掉超链接。", { command: user ? user[0] : dependent.names.find((n) => n === "bookmark" || n === "hypcap") }) };
        S.occ.forEach((o) => {
          if (o.pkg.own) specs.push(gone(o));
          else specs.push(...listChanges(r, o.pkg.nameList, { remove: o.pkg.nameList.items.filter((x) => x.text === "hyperref") }));
        });
      }
    } else if (key === "secnum") {
      // 1 / 1.1 / 1.1.1 (the class's), 一、（一）1. (ctex), 不编号: what was there goes, the new style's lines come
      if (!["", "zh", "none"].includes(value) || value === S.value) return null;
      if (value === "zh" && !L.zhNumbers) return { refused: L.zhNumbersWhy };
      if (value === "none" && L.settings.capnum.value === "within" && !L.settings.capnum.mine) return { refused: t("图表按章或节编号时，章节不能不编号：没有章节号，图表会印成 0-1。") };
      specs = removed(S.occ);
      if (value === "zh") specs.push(newLines(r, ZH_NUMBER_LINES));
      if (value === "none") specs.push(newLines(r, ["\\setcounter{secnumdepth}{" + (L.chapters ? "-1" : "0") + "}"]));
    } else if (key === "capname") {
      // 图 / 表 or Figure / Table: the class's own names remove the setting; a setting there changes in place
      if (!CAPTION_NAMES[value] || value === S.value) return null;
      if (value === "zh" && !L.cjk) return { refused: t("中文图表名需要中文文档类") };
      const names = CAPTION_NAMES[value];
      if (value === L.capDefault) specs = removed(S.occ);
      else if (S.occ.length) specs = S.occ.map((o) => (o.line ? argText(o.line, 0, names[o.name]) : { from: o.item.from, to: o.item.to, insert: o.item.text.slice(0, o.item.text.indexOf("=") + 1) + names[o.name] }));
      else specs = [newLines(r, ["\\renewcommand{\\figurename}{" + names.figurename + "}", "\\renewcommand{\\tablename}{" + names.tablename + "}"])];
    } else if (key === "capnum") {
      // 连续编号（图 1） / 按章或节编号（图 2-3） (and, with chapters, the class's 图 2.3): the old lines go, the new come
      if (!(L.chapters ? ["", "continuous", "within"] : ["", "within"]).includes(value) || value === S.value) return null;
      if (value === "within" && L.settings.secnum.value === "none" && !L.settings.secnum.mine) return { refused: t("章节不编号时，图表不能按章或节编号：没有章节号，图表会印成 0-1。") };
      specs = S.occ.map(gone);
      const lines = capnumLines(L.chapters, value);
      if (lines.length) specs.push(newLines(r, lines));
    }
    if (!specs.length) return null;
    return edited(state, merged(specs).map((x) => ({ from: x.from, to: x.to, insert: x.insert.split("\n").join(state.lineBreak) })));
  }

  // -- 段落 and the further insertions (22.12, A2) --------------------------------------------------------------------

  const ALIGN_ENVS = ["flushleft", "center", "flushright"];
  // The innermost environment of `names` the selection [from, to] is inside the body of.
  function envAround(read, names, from, to) {
    let best = null;
    read.envs.forEach((e) => { if (names.includes(e.name) && e.beginTo <= from && to <= e.endFrom && (!best || e.beginTo > best.beginTo)) best = e; });
    return best;
  }
  // An environment's \begin and \end taken away: each whole line when it is alone on it, else the command alone.
  function envRemoved(state, e) {
    const doc = state.doc, out = [];
    [[e.from, e.beginTo], [e.endFrom, e.to]].forEach(([a, b]) => {
      const la = doc.lineAt(a), lb = doc.lineAt(b);
      const alone = !doc.sliceString(la.from, a).trim() && !doc.sliceString(b, lb.to).trim();
      out.push(alone ? { from: la.from, to: Math.min(lb.to + 1, doc.length) } : { from: a, to: b });
    });
    return out;
  }
  // The lines of the paragraphs [from, to] touches (blank lines between them kept), or why there are none.
  function paragraphsOf(state, read, from, to) {
    const a = paragraphAt(state, read, from, from), b = paragraphAt(state, read, to, to);
    if (a.heading || b.heading) return { refused: t("这一段是标题，不能这样设置。") };
    if (a.structural || b.structural) return { refused: t("这一行是命令或环境，不能这样设置。") };
    if (a.empty && b.empty) return { empty: true };
    const doc = state.doc, first = a.empty ? doc.lineAt(from).number + 1 : doc.lineAt(a.from).number, last = b.empty ? doc.lineAt(to).number - 1 : doc.lineAt(b.to).number;
    for (let n = first; n <= last; n++) if (STRUCTURAL.test(doc.line(n).text)) return { refused: t("选中的几段里有命令或环境，不能这样设置。") };
    return { from: doc.line(first).from, to: doc.line(last).to };
  }
  // The paragraphs wrapped in `env`, its \begin and \end on lines of their own.
  function envWrapped(state, at, env) {
    if (at.empty) return blockSpec(state, state.selection.main.from, state.selection.main.to, "\\begin{" + env + "}\n" + CARET + "\n\\end{" + env + "}");
    const br = state.lineBreak;
    return edited(state, [{ from: at.from, insert: "\\begin{" + env + "}" + br }, { from: at.to, insert: br + "\\end{" + env + "}" }]);
  }

  // 对齐: 两端对齐 (the default: an alignment environment the paragraph is in is taken away), 左对齐 (flushleft), 居中
  // (center), 右对齐 (flushright) for the paragraph(s) the selection touches; another alignment's environment is renamed.
  function alignSpec(state, kind) {
    if (kind && !ALIGN_ENVS.includes(kind)) return null;
    const p = prepare(state, "block"), sel = p.sel;
    if (p.no) return { refused: p.no };
    const e = envAround(p.read, ALIGN_ENVS, sel.from, sel.to);
    if (e) {
      if (e.name === kind) return null;
      if (!kind) return edited(state, envRemoved(state, e));
      const name = (x) => { const at = p.text.indexOf("{", x) + 1; return { from: at, to: p.text.indexOf("}", at), insert: kind }; };
      return edited(state, [name(e.from), name(e.endFrom)]);
    }
    if (!kind) return null;
    const at = paragraphsOf(state, p.read, sel.from, sel.to);
    return at.refused ? at : envWrapped(state, at, kind);
  }
  // 引文块: the paragraph(s) in a quote environment, or out of the one they are in.
  function quoteSpec(state) {
    const p = prepare(state, "block"), sel = p.sel;
    if (p.no) return { refused: p.no };
    const e = envAround(p.read, ["quote", "quotation"], sel.from, sel.to);
    if (e) return edited(state, envRemoved(state, e));
    const at = paragraphsOf(state, p.read, sel.from, sel.to);
    return at.refused ? at : envWrapped(state, at, "quote");
  }
  // What the cursor's paragraph is: its alignment ("" when justified) and whether it is quoted.
  function paragraphState(state, read) {
    const sel = state.selection.main, e = envAround(read, ALIGN_ENVS, sel.from, sel.to);
    return { align: e ? e.name : "", quote: !!envAround(read, ["quote", "quotation"], sel.from, sel.to) };
  }

  // 分页: \clearpage on a line of its own after the selection (22.12: the floats before it are set first, as a page break
  // in Word keeps what is before it before it).
  function pageBreakSpec(state) {
    const p = prepare(state, "block");
    return p.no ? { refused: p.no } : blockSpec(state, p.sel.to, p.sel.to, "\\clearpage");
  }
  // 目录: \tableofcontents after \maketitle when the file has one, else after the selection; never a second one.
  function tocSpec(state) {
    const p = prepare(state, "block"), doc = state.doc;
    const at = (name) => { const c = p.read.cmds.find((x) => x.name === name && (!p.read.preamble || x.from > p.read.preamble.to)); return c || null; };
    const toc = at("tableofcontents");
    if (toc) return { refused: t("文稿里已经有目录（第 {n} 行）。", { n: doc.lineAt(toc.from).number }) };
    const title = at("maketitle");
    if (title) {
      const line = doc.lineAt(title.to), ins = state.lineBreak + "\\tableofcontents";
      return { changes: state.changes({ from: line.to, insert: ins }), selection: { anchor: line.to + 1 + "\\tableofcontents".length } };
    }
    return p.no ? { refused: p.no } : blockSpec(state, p.sel.to, p.sel.to, "\\tableofcontents");
  }
  // 交叉引用: \ref{key} (\eqref for a formula when amsmath is loaded) after the selection.
  function refSpec(state, key, eq) {
    const p = prepare(state, "inline");
    if (p.no) return { refused: p.no };
    return key ? inserted(state, p.sel.to, "\\" + (eq ? "eqref" : "ref") + "{" + key + "}", "") : null;
  }
  // 网址: \url{…} for the address typed (in place of the selection when it is that address); `opts` as figureSpec's
  // {url: whether url, xurl or hyperref is loaded, isMain, main}: the open main file gets \usepackage{url}.
  function urlSpec(state, address, opts) {
    const p = prepare(state, "inline"), sel = p.sel;
    if (p.no) return { refused: p.no };
    const url = String(address || "").trim();
    if (!url) return null;
    if (/[\s{}\\]|[\u0000-\u001f]/.test(url)) return { refused: t("网址里不能有空格、花括号或反斜杠。") };
    const ctx = contextAt(p.read, sel.from);
    if (/[%#]/.test(url) && ctx && ctx.kind === "arg") return { refused: t("网址里有 % 或 #，放在命令的参数里（比如脚注）会出错。请把它放在正文里。") };
    const need = needs(state, p.read, opts || {}, "url", opts && opts.url);
    const whole = state.doc.sliceString(sel.from, sel.to) === url, cmd = "\\url{" + url + "}";
    const at = whole ? sel.from : sel.to, ch = state.changes([{ from: at, to: whole ? sel.to : at, insert: cmd }].concat(need.more));
    return Object.assign({ changes: ch, selection: { anchor: ch.mapPos(at, -1) + cmd.length } }, need.note ? { note: need.note } : {});
  }

  // The \label keys of the open file, each with its kind, as latex_edit.labels_in reads another file's: the prefix
  // (fig:, tab:, eq:, sec: and their like), else the environment it is in, else a heading just before it, else other.
  const LABEL_PREFIXES = [["figure", /^(?:fig|figure)[:_-]/i], ["table", /^(?:tab|table|tbl)[:_-]/i], ["equation", /^(?:eq|eqn|equation)[:_-]/i],
    ["section", /^(?:sec|ssec|subsec|chap|ch|chapter|section|part|app)[:_-]/i]];
  const LABEL_ENVS = { figure: "figure", "figure*": "figure", wrapfigure: "figure", subfigure: "figure", table: "table", "table*": "table", longtable: "table", sidewaystable: "table" };
  function labelsOf(text, read) {
    const out = [], re = /\\label\s*\{([^{}]+)\}/g;
    for (let m = re.exec(text); m; m = re.exec(text)) {
      const at = m.index, key = m[1].trim();
      if (!key || read.raws.some((x) => x.from <= at && at < x.to)) continue;
      let kind = (LABEL_PREFIXES.find(([, rx]) => rx.test(key)) || [null])[0];
      if (!kind && read.maths.some((f) => f.display && f.from < at && at < f.to)) kind = "equation";
      if (!kind) {
        const env = read.envs.filter((e) => e.beginTo <= at && at < e.endFrom && Object.prototype.hasOwnProperty.call(LABEL_ENVS, e.name)).pop();
        if (env) kind = LABEL_ENVS[env.name];
      }
      if (!kind) {
        const h = read.cmds.filter((c) => c.role === "heading" && c.to <= at).pop();
        kind = h && !text.slice(h.to, at).trim() ? "section" : "other";
      }
      out.push({ key: key, kind: kind });
    }
    return out;
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
    const said = mk("span", "latex-act");
    said.append(status, undoLink);
    tools.append(save, undo, exported.button, said);
    const where = mk("div", "node-path mono", file.path);
    where.title = file.path;   // 22.12 B: a long path is cut short in the header, said whole on hover
    head.append(where, tools);
    const readOnly = mk("p", "node-sub latex-read-only" + (file.read_only ? "" : " hidden"), file.read_only_message || "");
    const notice = mk("div", "latex-notice hidden"), box = mk("div", "latex-editor"), side = mk("aside", "latex-side");
    // 22.12 B: under the text, quietly, how many words the body has and whether it is saved
    const bar = mk("div", "latex-statusbar"), count = mk("span", "latex-words"), saved = mk("span", "latex-save-state");
    bar.append(count, saved);
    const body = mk("div", "latex-body"), main = mk("div", "latex-main");
    const note = mk("p", "node-sub latex-new-note" + (at && at.note ? "" : " hidden"), (at && at.note) || "");   // 22.11: \input not added
    // the header, the notices and the status line in the text's column; the right column beside it, from the top
    main.append(head, exported.block, readOnly, note, notice, box, bar);
    body.append(main, side);
    page.append(backBar(), body);
    host.replaceChildren(page);
    const me = { path: file.path, CM: CM, ui: { save: save, undo: undo, status: status, notice: notice, box: box, side: side, undoLink: undoLink,
      page: page, words: count, saveState: saved },
      lastKey: 0, busy: false, lastSave: null, afterComposing: null, target: at || null, panel: null };
    me.ui.format = formatPanel(me);   // 22.12: 格式与插入, at the top of the right column
    side.appendChild(me.ui.format.box);
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
    // B1 (22.1): a file the edit view cannot show safely opens as code, the switch on and disabled, saying why
    me.forced = file.read_only ? t("这个文件只能查看，只显示代码。") : unsureWhy(file.text, readLatex(file.text));
    me.codeNote = null;
    const code = codeShown(me);
    me.ui.page.classList.toggle("is-source", code);   // 22.12 B: the page's column, or the full width
    me.revision = file.revision;
    me.readOnly = !!file.read_only;
    me.sep = file.line_ending === "crlf" ? "\r\n" : "\n";
    if (file.mains) me.mains = file.mains;
    if (file.links) me.links = new Map(file.links.filter((l) => l.path).map((l) => [includeKey(l.command, l.args), l]));
    if (file.links) me.format = file.format || null;   // 22.12: the main file's facts, for a writable file
    hookEditView(me);
    const extensions = [
      me.wordComp.of(code ? [] : wordExtensions(CM, { say: (s) => sayEdit(me, s) })),   // first, so its keys come first
      CM.history(), CM.drawSelection(), CM.highlightSpecialChars(), CM.EditorView.lineWrapping,
      returnKeys(CM), formatKeys(CM, me), CM.keymap.of([].concat(CM.defaultKeymap, CM.historyKeymap)),
      CM.StreamLanguage.define(CM.stex), CM.syntaxHighlighting(highlight(CM)), includeLinks(CM, me),
      CM.EditorView.contentAttributes.of({ "aria-label": t("LaTeX 源码"), spellcheck: "false" }),
      CM.EditorView.updateListener.of((update) => {
        changed(me, update);
        if (update.docChanged) { scheduleWords(me); drawSaveState(me); }
        if (update.selectionSet || update.docChanged || update.geometryChanged) placeLinkButton(me);
        syncFormat(me);
      }),
      passageField(CM).field,
      CM.EditorView.domEventHandlers({ click: (event) => {
        const hit = event.target && event.target.closest && event.target.closest("[data-passage]");
        if (hit) openPassage(me, hit.getAttribute("data-passage"));
      } }),
    ];
    // A read-only file may mix endings: CodeMirror's own splitting shows it; nothing is ever sent.
    if (me.readOnly) extensions.push(CM.EditorState.readOnly.of(true));
    else extensions.push(CM.EditorState.lineSeparator.of(me.sep), breakFilter(CM, me.sep));
    let state = CM.EditorState.create({ doc: file.text, extensions: extensions });
    // the first cursor is where the body starts (in the edit view, at the edge of what is hidden there)
    const first = bodyStart(state.doc, readLatex(state.doc.toString()));
    if (first) state = state.update({ selection: { anchor: first } }).state;
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
    if (me.picker) closePicker(me);   // a picker was for the text before the reload
    if (me.ui.format) me.ui.format.optionsFor = null;
    me.layoutCustom = {};
    me.note = null;
    drawSwitch(me);
    formatAvailability(me);
    loadCites(me);
    drawWords(me);
    drawSaveState(me);
    loadPassages(me, false);
  }

  // 显示代码 (22.1, B1; the researcher, 2026-11-14): the whole file as its LaTeX code, typed and pasted as it is, or the
  // edit view, which shows no code. Per file while Research OS runs; switching keeps the cursor's place and is no step
  // of ⌘Z. A file the edit view cannot show safely (`me.forced`: read-only, or one the reader is unsure of) stays code.
  // KaTeX is loaded the first time a file is shown in the edit view (22.0.1).
  const sourceShown = new Set();
  function codeShown(me) { return !!me.forced || sourceShown.has(me.path); }
  function toggleSource(me, on) {
    if (ed !== me || !me.view || me.forced) return;
    const want = on == null ? !sourceShown.has(me.path) : !!on;
    me.codeNote = null;
    if (!want) {   // back to the edit view: only when the text can be shown safely
      const text = me.view.state.doc.toString(), why = unsureWhy(text, readLatex(text));
      if (why) { me.codeNote = why; drawSwitch(me); return; }
    }
    if (want) sourceShown.add(me.path); else sourceShown.delete(me.path);
    me.ui.page.classList.toggle("is-source", want);
    me.view.dispatch({ effects: me.wordComp.reconfigure(want ? [] : wordExtensions(me.CM, { say: (s) => sayEdit(me, s) })) });
    if (!want) me.view.dispatch({ selection: me.view.state.selection });   // the edit view's filter moves it out of what is hidden
    me.note = null;
    drawSwitch(me);
    formatAvailability(me);   // 22.12: 格式 is for the edit view; 版式 works in both
    mathWhenShown(me);
  }
  // 显示代码 on, the cursor at `pos` (a table's, a formula's or a construct's start): where it is changed.
  function codeAt(me, pos) {
    if (!me.view) return;
    if (!codeShown(me)) toggleSource(me, true);
    me.view.dispatch({ selection: { anchor: Math.min(pos, me.view.state.doc.length) }, scrollIntoView: true });
    if (typeof me.view.focus === "function") me.view.focus();
  }
  function mathWhenShown(me) {
    if (codeShown(me) || window.katex || typeof document === "undefined") return;
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
    me.inSave = true;
    setStatus(me.ui.status, "", t("正在保存…"));
    drawSaveState(me);
    let r;
    try {
      r = await apiPost("/api/latex/save", body);
    } catch (err) {
      if (ed !== me) return;
      me.busy = false; me.ui.save.disabled = false; me.sinceSent = null;
      me.inSave = false; drawSaveState(me);
      if (err.state === "workspace_conflict") conflict(me);
      else setStatus(me.ui.status, "err", t("没能保存，当前内容已保留。"), err);
      return;
    }
    if (ed !== me) return;
    me.savedDoc = doc; me.revision = r.revision; me.pending = me.sinceSent; me.sinceSent = null;
    me.inSave = false; drawSaveState(me);
    if (lastShown && lastShown.path === me.path) lastShown = null;   // an export's answer is about the text before this save
    clearNotice(me);   // a conflict's notice and folded text are over once a save goes through
    const emptied = checkPassages(me);
    if (r.changed && r.previous) me.lastSave = { revision: r.revision, previous: r.previous };
    me.ui.undo.classList.toggle("hidden", !me.lastSave);
    // a cut passage pasted elsewhere is found there by the server; the save stays busy until its answer is in, so no
    // passage act runs on a list about to be replaced
    if (emptied) { await loadPassages(me, true); if (ed !== me) return; }
    me.busy = false; me.ui.save.disabled = false;
    me.inSave = false; drawSaveState(me);
    setStatus(me.ui.status, "", dirty() ? t("尚未保存") : t("已保存"));
  }

  // 22.12 B: the status line under the text. The word count is drawn when a file loads and a moment after typing
  // stops, so a key never waits for it; the save state follows the save (正在保存 while one is under way).
  function drawWords(me) {
    if (ed !== me || !me.view || !me.ui.words) return;
    const st = me.view.state, value = st.field(wordField(me.CM).field, false), text = st.doc.toString();
    me.ui.words.textContent = t("{n} 字", { n: bodyWords(text, value ? value.read : readLatex(text)) });
  }
  function scheduleWords(me) {
    clearTimeout(me.wordsTimer);
    me.wordsTimer = setTimeout(() => { me.wordsTimer = null; drawWords(me); }, 400);
  }
  function drawSaveState(me) {
    const el = me.ui.saveState;
    if (!el || ed !== me) return;
    el.textContent = me.readOnly ? "" : me.inSave ? t("正在保存") : dirty() ? t("有未保存的改动") : t("已保存");
    el.classList.toggle("is-unsaved", !me.readOnly && !me.inSave && dirty());
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
    const chosen = [], picked = mk("ul", "latex-files latex-picked"), results = mk("div", "latex-results");
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
    closePicker(me);   // one primary at a time in the column
    fillSide(me, [mk("h3", "latex-side-title", t("关联依据文件")), quoted(me.view.state.sliceDoc(sel.from, sel.to)),
      search, results, picked, note, go, closeLink(me)]);
    find();
  }


  // The passage's panel: its files (each a link to its page), the note, who linked it and when, its state, the
  // candidates when 待复核, 撤回关联. No primary button (22.7).
  function openPassage(me, key) {
    const p = passagesOf(me).find((x) => x.key === key);
    if (!p) return;
    me.panel = key;
    const head = mk("div", "latex-side-head");
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
    fillSide(me, parts);
  }

  // The side of the editor with no panel open: how many passages have supporting files, those 待复核 listed.
  function renderSide(me) {
    if (me.panel === "link") return;
    if (me.panel) { openPassage(me, me.panel); return; }
    const ev = me.evidence || {}, list = me.view ? passagesOf(me) : [];
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
    fillSide(me, parts);
  }

  // -- 格式与插入: the panel (22.12) ----------------------------------------------------------------------------------
  //
  // At the top of the right column, above the passages: two tabs, 「格式」 (this panel) and 「版式」 (22.12's next step).
  // 「格式」 has three quiet groups -- 文字, 标题, 插入 -- whose controls run the tools above through `applyFormat`, the
  // editor's own dispatch. Disabled, with its reason, in a read-only file and while 显示代码 shows the code. A picker
  // (图片, 表格, 引用文献) opens inside the panel, one at a time, with 「插入」 its one primary button and 「取消」 a link.

  // What the panel knows of the main file: the open file's own preamble when it is the main file (or has its own
  // \documentclass and no main file is known), else what GET /api/latex/file said of the main file (`format`).
  function panelFacts(me) {
    const f = me.format || {}, state = me.view.state, value = state.field(wordField(me.CM).field, false);
    const read = value ? value.read : readLatex(state.doc.toString());
    const own = read.preamble ? state.doc.sliceString(0, read.preamble.to) : "";
    const isMain = f.main ? f.main === me.path : !!preambleFacts(own).cls;
    const facts = preambleFacts(isMain ? own : f.preamble || "");
    const folder = typeof f.folder === "string" ? f.folder : me.path.slice(0, Math.max(me.path.lastIndexOf("/"), 0));
    return Object.assign(facts, { isMain: isMain, main: f.main || (isMain ? me.path : null), folder: folder,
      engine: f.engine || (facts.ctex ? "xelatex" : "pdflatex") });
  }

  // 「插入」, the one primary button of an open picker (22.7).
  function insertButton(run) { return mkButton(t("插入"), "btn btn-primary", run); }

  function formatPanel(me) {
    const box = mk("section", "latex-format"), strip = mk("div", "latex-tabs"), tabs = {};
    const panes = { format: mk("div", "latex-tab-pane"), layout: mk("div", "latex-tab-pane hidden") };
    strip.setAttribute("role", "tablist");
    const pick = (key) => {
      Object.keys(tabs).forEach((k) => {
        tabs[k].setAttribute("aria-selected", String(k === key));
        tabs[k].classList.toggle("is-on", k === key);
        panes[k].classList.toggle("hidden", k !== key);
      });
      if (key === "layout" && ui.layout) drawLayout(me, true);   // 版式's rows are drawn while it is shown
    };
    [["format", t("格式")], ["layout", t("版式")]].forEach(([key, label]) => {
      const b = mkButton(label, "latex-tab", () => pick(key));
      b.setAttribute("role", "tab");
      tabs[key] = b;
      strip.appendChild(b);
      panes[key].setAttribute("role", "tabpanel");
    });
    pick("format");

    const ui = { box: box, tabs: tabs, panes: panes, pick: pick, controls: [], styles: {} };
    const keep = (el) => { el.addEventListener("mousedown", (event) => event.preventDefault()); return el; };   // the selection stays
    const tool = (label, run, key, aria) => {
      const b = keep(mkButton(label, "btn btn-small latex-tool", run));
      if (key) { b.append(" ", mk("span", "latex-key", key)); b.setAttribute("aria-keyshortcuts", aria); }
      ui.controls.push(b);
      return b;
    };
    const row = (...kids) => { const r = mk("div", "latex-format-row"); r.append(...kids); return r; };
    const group = (title, ...rows) => { const g = mk("div", "latex-format-group"); g.append(mk("h4", "latex-format-title", title), ...rows); return g; };
    const choice = (label, onChange) => {
      const field = mk("label", "latex-format-field"), select = mk("select", "ws-input latex-format-select");
      field.append(mk("span", "latex-format-label", label), select);
      select.addEventListener("change", () => onChange(select.value));
      ui.controls.push(select);
      return { field: field, select: select };
    };
    const run = (make) => () => applyFormat(me, make);

    ui.reason = mk("p", "node-sub latex-format-reason hidden");
    ui.note = mk("p", "node-sub latex-format-note hidden");
    ui.note.setAttribute("role", "status");
    ui.styles.textbf = tool(t("加粗"), run((st) => styleSpec(st, "textbf")), "⌘B", "Meta+B");
    ui.styles.textit = tool(t("斜体"), run((st) => styleSpec(st, "textit")), "⌘I", "Meta+I");
    ui.styles.underline = tool(t("下划线"), run((st) => styleSpec(st, "underline")), "⌘U", "Meta+U");
    ui.font = choice(t("字体"), (v) => applyFormat(me, (st) => fontSpec(st, v)));
    ui.size = choice(t("字号"), (v) => applyFormat(me, (st) => sizeSpec(st, v)));
    ui.heading = choice(t("级别"), (v) => applyFormat(me, (st) => headingSpec(st, v, panelFacts(me).chapters)));
    ui.align = choice(t("对齐"), (v) => applyFormat(me, (st) => alignSpec(st, v)));
    ui.align.select.replaceChildren(...[["", t("两端对齐")], ["flushleft", t("左对齐")], ["center", t("居中")], ["flushright", t("右对齐")]]
      .map(([value, label]) => { const o = mk("option", null, label); o.value = value; return o; }));
    ui.quote = tool(t("引文块"), run(quoteSpec));
    ui.openers = {
      image: tool(t("图片"), () => openPicker(me, "image")),
      table: tool(t("表格"), () => openPicker(me, "table")),
      cite: tool(t("文献引用"), () => openPicker(me, "cite")),
      ref: tool(t("交叉引用"), () => openPicker(me, "ref")),
      url: tool(t("网址"), () => openPicker(me, "url")),
    };
    Object.keys(ui.openers).forEach((k) => ui.openers[k].setAttribute("aria-expanded", "false"));
    ui.picker = mk("div", "latex-picker hidden");
    // 22.1 (B1): 「显示代码」 heads the column, above the tabs; under it what the edit view says about where the cursor is
    ui.code = mkButton(t("显示代码"), "latex-switch", () => toggleSource(me));
    ui.code.setAttribute("aria-pressed", "false");
    ui.code.addEventListener("mousedown", (event) => event.preventDefault());
    ui.codeWhy = mk("p", "node-sub latex-code-why hidden");
    ui.context = mk("div", "latex-context hidden");
    ui.context.setAttribute("role", "status");
    const head = mk("div", "latex-switch-row");
    head.append(ui.code);
    panes.format.append(ui.reason, ui.note,
      group(t("文字"), row(ui.styles.textbf, ui.styles.textit, ui.styles.underline), row(ui.font.field, ui.size.field)),
      group(t("章节标题"), row(ui.heading.field)),
      group(t("段落"), row(ui.align.field, ui.quote)),
      group(t("插入"), row(ui.openers.image, ui.openers.table, ui.openers.cite, tool(t("脚注"), () => { if (applyFormat(me, footnoteSpec)) focusNote(me); })),
        row(ui.openers.ref, ui.openers.url, tool(t("分页"), run(pageBreakSpec)), tool(t("目录"), run(tocSpec))),
        row(mk("span", "latex-format-label", t("公式")), tool(t("行内"), () => formulaTool(me, false)), tool(t("独立"), () => formulaTool(me, true))),
        row(mk("span", "latex-format-label", t("列表")), tool(t("项目符号"), run((st) => listSpec(st, "itemize"))), tool(t("数字编号"), run((st) => listSpec(st, "enumerate"))))),
      ui.picker);
    box.append(head, ui.codeWhy, ui.context, strip, panes.format, panes.layout);
    ui.layout = layoutPane(me, ui);   // 版式 (A2)
    return ui;
  }

  // The choices of 字体, 字号 and 标题 for this document: 宋体, 黑体, 楷体 and 仿宋 where ctex is loaded (its commands),
  // 衬线 for pdfLaTeX, 章 where the class has \chapter. Filled again only when what they depend on changed: the main
  // file's facts, or the open file's preamble (typed into, a package added by a tool).
  function fillFormatOptions(me) {
    const ui = me.ui.format;
    if (!ui || !me.view) return;
    const value = me.view.state.field(wordField(me.CM).field, false), read = value ? value.read : null;
    const key = JSON.stringify(me.format || null) + "\u0000" + (read && read.preamble ? me.view.state.doc.sliceString(0, read.preamble.to) : "");
    if (key === ui.optionsFor && read) return;
    ui.optionsFor = key;
    const facts = panelFacts(me);
    const fill = (select, list) => {
      const was = select.value;
      select.replaceChildren(...list.map(([value, label, off]) => { const o = mk("option", null, label); o.value = value; if (off) o.disabled = true; return o; }));
      select.value = was;
    };
    fill(ui.font.select, [["", t("默认")]].concat(facts.ctex ? [["songti", t("宋体")], ["heiti", t("黑体")], ["kaishu", t("楷体")], ["fangsong", t("仿宋")]] : [],
      [["textsf", t("无衬线")], ["texttt", t("等宽")]], facts.engine === "pdflatex" ? [["textrm", t("衬线")]] : []));
    fill(ui.size.select, [["small", t("小")], ["", t("正常")], ["large", t("大")], ["Large", t("更大")]]);
    fill(ui.heading.select, [["", t("正文段落")]].concat(facts.chapters ? [["chapter", t("章")]] : [],
      [["section", t("节")], ["subsection", t("小节")], ["subsubsection", t("小小节")], ["other", t("其他标题"), true]]));
  }

  // Disabled, saying why, where the tools would not make sense: a read-only file, and 显示代码 (the code as written).
  function formatOff(me) {
    if (me.readOnly) return t("这个文件只能查看，不能用格式与插入。");
    return codeShown(me) ? t("正在显示代码。关掉「显示代码」后可以用格式工具。") : "";
  }
  // 「显示代码」: pressed while the code shows; disabled and pressed, with why, for a file the edit view cannot show.
  function drawSwitch(me) {
    const ui = me.ui.format;
    if (!ui || !ui.code) return;
    const on = codeShown(me), why = me.forced || me.codeNote || "";
    ui.code.setAttribute("aria-pressed", String(on));
    ui.code.classList.toggle("is-on", on);
    ui.code.disabled = !!me.forced;
    ui.codeWhy.textContent = why;
    ui.codeWhy.classList.toggle("hidden", !why);
  }
  // A sentence of the edit view's (a refusal of what was typed or deleted, where a tool put the cursor): quiet, under
  // the switch, until the cursor moves on.
  function sayEdit(me, text) {
    me.said = text ? { text: text, at: me.view ? me.view.state.selection.main.head : 0 } : null;
    drawContext(me);
  }
  // What the edit view's blocks do when clicked: the title block opens 版式's 文档信息, the 导言区 line 版式.
  function hookEditView(me) {
    const hooks = wordField(me.CM).hooks;
    hooks.title = () => { if (ed !== me || !me.ui.format) return; me.ui.format.pick("layout"); const f = me.ui.format.layout.info && me.ui.format.layout.info.title; if (f && typeof f.focus === "function") f.focus(); };
    hooks.preamble = () => { if (ed !== me || !me.ui.format) return; me.ui.format.pick("layout"); };
    hooks.cites = me.cites || null;
  }
  // \cite chips say author and year: the project's .bib entries are read once a file with a citation is open (a moment
  // after it loads, so opening never waits), then the chips are drawn again.
  function loadCites(me) {
    if (me.readOnly || me.citesAsked || typeof setTimeout !== "function") return;
    const text = me.view.state.doc.toString();
    if (!/\\(?:cite|citep|citet|parencite|textcite|autocite)\b/.test(text)) return;
    me.citesAsked = true;
    setTimeout(async () => {
      let data = null;
      try { data = await apiGet("/api/latex/insert?what=bib&path=" + encodeURIComponent(me.path)); } catch (err) { return; }
      if (ed !== me || !data || !Array.isArray(data.entries)) return;
      const map = new Map();
      data.entries.forEach((e) => {
        if (!e || !e.key) return;
        const authors = String(e.author || "").split(/\s+and\s+/i).map((a) => a.trim()).filter(Boolean);
        const first = authors.length ? (authors[0].indexOf(",") >= 0 ? authors[0].split(",")[0] : authors[0].split(/\s+/).pop()).replace(/[{}]/g, "") : "";
        const second = authors.length === 2 ? (authors[1].indexOf(",") >= 0 ? authors[1].split(",")[0] : authors[1].split(/\s+/).pop()).replace(/[{}]/g, "") : "";
        const who = first ? (authors.length > 2 ? t("{author} 等", { author: first }) : second ? t("{a} 和 {b}", { a: first, b: second }) : first) : "";
        const said = [who, e.year].filter(Boolean).join(" ");
        if (said) map.set(e.key, said);
      });
      me.cites = map;
      hookEditView(me);
      if (!codeShown(me)) me.view.dispatch({ effects: wordField(me.CM).refresh.of(true) });
    }, 0);
  }
  // Under the switch, in the edit view: where what the cursor is at (or has selected) is changed -- a table, a
  // formula, anything shown as a chip: 「…要在「显示代码」里改」 with a link that turns the code on there; a footnote:
  // its words, changed here; and a sentence the edit view said, until the cursor moves.
  const CHANGED_IN_CODE = { table: () => t("表格内容要在「显示代码」里改。"), math: () => t("公式要在「显示代码」里改。"),
    unit: () => t("这部分要在「显示代码」里改。"), verbatim: () => t("这部分要在「显示代码」里改。"), verb: () => t("这部分要在「显示代码」里改。"),
    url: () => t("这部分要在「显示代码」里改。") };
  function unitAt(me) {
    const st = me.view.state, v = st.field(wordField(me.CM).field, false);
    if (!v) return null;
    const sel = st.selection.main, got = [];
    v.atoms.between(sel.from, sel.to, (from, to, d) => {
      const k = d.spec.kind;
      if (!d.spec.atom || !(k === "footnote" || CHANGED_IN_CODE[k])) return;
      if (sel.empty ? from === sel.head || to === sel.head : from <= sel.from && sel.to <= to || (sel.from <= from && to <= sel.to)) got.push({ from: from, to: to, kind: k });
    });
    return got.find((x) => x.to === sel.head) || got[0] || null;
  }
  function drawContext(me) {
    const ui = me.ui.format;
    if (!ui || !ui.context || !me.view) return;
    const parts = [];
    if (me.said && me.said.at !== me.view.state.selection.main.head) me.said = null;
    if (me.said && me.said.text) parts.push(mk("p", "node-sub", me.said.text));
    const u = codeShown(me) ? null : unitAt(me);
    if (u && u.kind === "footnote") {
      const kept = me.note && me.note.from === u.from && me.note.box &&
        (typeof ui.context.contains === "function" ? ui.context.contains(me.note.box) : (ui.context.children || []).includes(me.note.box));
      if (kept && !parts.length) return;   // being typed in: the same field stays
      parts.push(noteEditor(me, u));
    } else {
      me.note = null;
      if (u) {
        const go = mkButton(t("在「显示代码」里改"), "ws-text-link", () => codeAt(me, u.from));
        go.addEventListener("mousedown", (event) => event.preventDefault());
        const p = mk("p", "node-sub latex-context-line");
        p.append(mk("span", null, CHANGED_IN_CODE[u.kind]()), " ", go);
        parts.push(p);
      }
    }
    ui.context.replaceChildren(...parts);
    ui.context.classList.toggle("hidden", !parts.length);
  }
  // A footnote's words, read and changed here when they are plain words (escaped as typing is; each change one step of
  // ⌘Z with the others typed after it); a footnote holding LaTeX is changed in 显示代码.
  function noteEditor(me, u) {
    const value = me.view.state.field(wordField(me.CM).field), text = value.info.text;
    const c = value.read.cmds.find((x) => x.from === u.from && x.name === "footnote"), a = c ? c.args.filter((x) => !x.opt).pop() : null;
    const box = mk("div", "latex-note");
    box.appendChild(mk("h4", "latex-format-title", t("脚注")));
    const inner = a ? text.slice(a.from + 1, a.to - 1) : "", plain = a ? unescapeTex(inner) : null;
    if (plain == null) {
      const go = mkButton(t("在「显示代码」里改"), "ws-text-link", () => codeAt(me, u.from));
      box.append(mk("p", "node-sub", t("这个脚注里有 LaTeX 命令，要在「显示代码」里改。")), go);
      me.note = null;
      return box;
    }
    const area = mk("textarea", "ws-input latex-note-text");
    area.value = plain;
    area.setAttribute("aria-label", t("脚注"));
    area.addEventListener("input", () => {
      const v2 = me.view.state.field(wordField(me.CM).field), c2 = v2.read.cmds.find((x) => x.from === u.from && x.name === "footnote");
      const a2 = c2 ? c2.args.filter((x) => !x.opt).pop() : null;
      if (!a2 || readOnlyNow(me)) return;
      const ins = escapeTyped(area.value);
      // the cursor stays beside the footnote, so this panel stays
      me.view.dispatch({ changes: { from: a2.from + 1, to: a2.to - 1, insert: ins }, selection: { anchor: c2.to + ins.length - (a2.to - a2.from - 2) }, userEvent: "input.type.made" });
    });
    box.appendChild(area);
    me.note = { from: u.from, box: box, area: area };
    return box;
  }
  function readOnlyNow(me) { return !!me.readOnly || codeShown(me); }
  // After 脚注 inserts one, its words are typed in the right column.
  function focusNote(me) {
    drawContext(me);
    if (me.note && me.note.area && typeof me.note.area.focus === "function") me.note.area.focus();
  }
  // Words typed in a field of the right column, as LaTeX that prints them (one line).
  function escapeTyped(s) { return String(s || "").replace(/\r\n?|\n/g, " ").replace(/[%$&#_{}~^\\]/g, (c) => TYPE_ESCAPES[c]); }
  // The words of LaTeX that holds only words and escapes, unescaped; null when it holds any other command or markup.
  function unescapeTex(src) {
    let s = String(src);
    Object.keys(TYPE_ESCAPES).forEach((k) => { s = s.split(TYPE_ESCAPES[k]).join("\u0000" + k.charCodeAt(0) + "\u0000"); });
    s = s.replace(/\\\^\{\}/g, "\u0000" + 94 + "\u0000").replace(/\\~\{\}/g, "\u0000" + 126 + "\u0000").replace(/~/g, "\u00a0");
    if (/[\\{}$&#^_%]/.test(s)) return null;
    return s.replace(/\u0000(\d+)\u0000/g, (m, n) => String.fromCharCode(Number(n))).replace(/\u00a0/g, " ");
  }

  // 版式 edits the preamble, so it works in 显示代码 too: off only in a file that cannot be changed.
  function layoutOff(me) { return me.readOnly ? t("这个文件只能查看，不能用格式与插入。") : ""; }
  function formatAvailability(me) {
    const ui = me.ui.format;
    if (!ui) return;
    const why = formatOff(me);
    ui.controls.forEach((c) => { c.disabled = !!why; });
    ui.reason.textContent = why;
    ui.reason.classList.toggle("hidden", !why);
    if (why) { closePicker(me); sayFormat(me, ""); }
    drawFormatState(me);
  }

  // A sentence of the panel's: a refusal (quiet) or a line the main file needs (a warning, in ochre).
  function sayFormat(me, text, warn) {
    const note = me.ui.format && me.ui.format.note;
    if (!note) return;
    note.textContent = text || "";
    note.className = "node-sub latex-format-note" + (warn ? " latex-export-warn" : "") + (text ? "" : " hidden");
  }

  // Runs a tool: its transaction goes through the editor's dispatch (so it is undone by ⌘Z, saved, mapped through the
  // passages and filtered for line breaks like typing); a refusal is said in the panel. The editor regains focus.
  // `layout`: a 版式 setting, which edits the preamble and works in 显示代码 too.
  function applyFormat(me, make, layout) {
    if (ed !== me || !me.view || (layout ? layoutOff(me) : formatOff(me)) || me.view.composing) return false;
    const r = make(me.view.state);
    const focus = () => { if (typeof me.view.focus === "function") me.view.focus(); };
    if (!r) { drawFormatState(me); focus(); return false; }
    if (r.refused) { sayFormat(me, r.refused); drawFormatState(me); return false; }
    me.view.dispatch({ changes: r.changes, selection: r.selection, scrollIntoView: true, userEvent: "input.format" });
    // in the edit view a tool's cursor that lands in what is hidden (a footnote's braces) moves to its edge
    if (!codeShown(me)) me.view.dispatch({ selection: me.view.state.selection });
    sayFormat(me, r.note || "", !!r.note);
    focus();
    return true;
  }
  // 公式 (22.12) in the edit view: a formula is written in 显示代码 (22.1, B1), so the tool puts it there, the cursor
  // in it; with words selected it asks for none (they would become its code).
  function formulaTool(me, display) {
    if (formatOff(me) || !me.view) return;
    if (!me.view.state.selection.main.empty) { sayFormat(me, t("公式在「显示代码」里写：先不要选文字，再点「行内」或「独立」。")); return; }
    if (!applyFormat(me, (st) => mathSpec(st, display))) return;
    codeAt(me, me.view.state.selection.main.head);
    sayEdit(me, t("公式在「显示代码」里写；写好后关掉「显示代码」。"));
  }

  // ⌘B, ⌘I, ⌘U in the editor: the same as the buttons (they come before CodeMirror's own Mod-i and Mod-u).
  function formatKeys(CM, me) {
    const key = (name) => (view) => {
      if (formatOff(me) || view.composing) return false;
      applyFormat(me, (st) => styleSpec(st, name));
      return true;
    };
    return CM.keymap.of([{ key: "Mod-b", run: key("textbf") }, { key: "Mod-i", run: key("textit") }, { key: "Mod-u", run: key("underline") }]);
  }

  // The buttons' state where the cursor is (aria-pressed), the font, the size and the paragraph's level: drawn at the
  // next frame after a change, so a key never waits for it.
  function syncFormat(me) {
    if (me.formatQueued) return;
    me.formatQueued = true;
    const later = () => { me.formatQueued = false; if (ed === me && me.view) drawFormatState(me); };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(later); else later();
  }
  function drawFormatState(me) {
    const ui = me.ui.format;
    if (!ui || !me.view) return;
    fillFormatOptions(me);
    const off = !!formatOff(me), value = me.view.state.field(wordField(me.CM).field, false);
    const read = off ? null : value ? value.read : readLatex(me.view.state.doc.toString());
    const st = off ? { pressed: {}, font: "", size: "", heading: "" } : formatState(me.view.state, read);
    const para = off ? { align: "", quote: false } : paragraphState(me.view.state, read);
    ui.align.select.value = para.align;
    ui.quote.setAttribute("aria-pressed", String(para.quote));
    ui.quote.classList.toggle("is-on", para.quote);
    TEXT_STYLE_NAMES.forEach((name) => {
      ui.styles[name].setAttribute("aria-pressed", String(!!st.pressed[name]));
      ui.styles[name].classList.toggle("is-on", !!st.pressed[name]);
    });
    const offered = (select, value) => Array.from(select.options || select.children || []).some((o) => o.value === value && !o.disabled);
    ui.font.select.value = offered(ui.font.select, st.font) ? st.font : "";
    ui.size.select.value = offered(ui.size.select, st.size) ? st.size : "";
    ui.heading.select.value = offered(ui.heading.select, st.heading || "") ? st.heading || "" : "other";
    drawContext(me);
    drawLayout(me);
  }

  function closePicker(me) {
    const ui = me.ui.format;
    if (!ui) return;
    me.picker = null;
    me.pickerSeq = (me.pickerSeq || 0) + 1;
    ui.picker.replaceChildren();
    ui.picker.classList.add("hidden");
    Object.keys(ui.openers).forEach((k) => ui.openers[k].setAttribute("aria-expanded", "false"));
  }
  // 图片, 表格 or 引用文献 opens its picker in the panel (pressed again, it closes). The link panel closes: one primary at a time.
  function openPicker(me, kind) {
    const ui = me.ui.format;
    if (!ui || formatOff(me)) return;
    if (me.picker === kind) { closePicker(me); return; }
    closePicker(me);
    if (me.panel === "link") { me.panel = null; me.linkRange = null; renderSide(me); }
    me.picker = kind;
    ui.openers[kind].setAttribute("aria-expanded", "true");
    ui.picker.classList.remove("hidden");
    sayFormat(me, "");
    const seq = me.pickerSeq;
    ({ image: imagePicker, table: tablePicker, cite: citePicker, ref: refPicker, url: urlPicker })[kind](me, ui.picker, () => ed === me && me.picker === kind && me.pickerSeq === seq);
  }
  const fieldOf = (label, input) => { const f = mk("label", "latex-format-field"); f.append(mk("span", "latex-format-label", label), input); return f; };
  function cancelLink(me) { return mkButton(t("取消"), "ws-text-link", () => { closePicker(me); if (me.view && typeof me.view.focus === "function") me.view.focus(); }); }
  function textInput(label) { const i = mk("input", "ws-input"); i.type = "text"; i.setAttribute("aria-label", label); return i; }
  function pickerActions(go, me) { const r = mk("div", "latex-format-row"); r.append(go, cancelLink(me)); return r; }
  const folded = (s) => String(s || "").toLowerCase();

  // 图片: the project's images (GET /api/latex/insert), a search, thumbnails where the asset route serves them, 图注
  // and 宽度; the path is written as the export compiles it (`figurePath`), and graphicx is seen to (`needs`).
  async function imagePicker(me, host, current) {
    const search = textInput(t("搜索图片")), list = mk("div", "latex-images"), caption = textInput(t("图注")), said = mk("p", "node-sub hidden");
    search.setAttribute("placeholder", t("搜索图片"));
    const width = mk("select", "ws-input latex-format-select");
    [["full", t("整栏")], ["half", t("半栏")]].forEach(([value, label]) => { const o = mk("option", null, label); o.value = value; width.appendChild(o); });
    let chosen = null, images = [];
    const go = insertButton(() => {
      if (!chosen) return;
      const facts = panelFacts(me);
      if (applyFormat(me, (st) => figureSpec(st, { path: figurePath(chosen, facts.folder, facts.graphicspath), file: chosen, caption: caption.value,
        half: width.value === "half", graphicx: facts.graphicx, isMain: facts.isMain, main: facts.main }))) closePicker(me);
    });
    go.disabled = true;
    const form = [fieldOf(t("图注"), caption), fieldOf(t("宽度"), width)];
    host.replaceChildren(mk("h4", "latex-format-title", t("插入图片")), search, list, said, ...form, pickerActions(go, me));
    const show = () => {
      const q = folded(search.value), shown = images.filter((x) => folded(x.path).indexOf(q) >= 0).slice(0, 60);
      list.replaceChildren(...shown.map((x) => {
        const b = mk("button", "latex-image");
        b.type = "button";
        b.setAttribute("aria-pressed", String(chosen === x.path));
        if (x.thumb) {
          const img = mk("img", "latex-thumb");
          img.alt = ""; img.loading = "lazy";
          img.src = "/api/workspace/asset?path=" + encodeURIComponent(x.path);
          img.addEventListener("error", () => { if (img.remove) img.remove(); });
          b.appendChild(img);
        }
        b.appendChild(mk("span", "mono", x.path));
        if (IMAGE_UNSAFE.test(x.path)) { b.disabled = true; b.appendChild(mk("span", "node-sub-inline", t("文件名里有 LaTeX 不能用的字符"))); }
        b.addEventListener("click", () => {
          chosen = x.path;
          go.disabled = false;
          said.textContent = /\.eps$/i.test(x.path) ? t(".eps 图片导出时可能排不出来，换成 .pdf 或 .png 更稳妥。") : "";
          said.classList.toggle("hidden", !said.textContent);
          show();
        });
        return b;
      }));
      if (!shown.length) list.appendChild(mk("p", "node-sub", t("没有找到这样的图片")));
    };
    search.addEventListener("input", show);
    list.replaceChildren(mk("p", "node-sub", t("载入中…")));
    let data;
    try { data = await apiGet("/api/latex/insert?what=images&path=" + encodeURIComponent(me.path)); } catch (err) {
      if (current()) list.replaceChildren(mk("p", "notice-status err", t("没能列出项目里的图片")));
      return;
    }
    if (!current()) return;
    images = data.images || [];
    if (!images.length) {
      host.replaceChildren(mk("h4", "latex-format-title", t("插入图片")), mk("p", "node-sub", t("项目里还没有图片。把图片放进项目文件夹后，这里就能选。")), cancelLink(me));
      return;
    }
    show();
  }

  // 表格: 行 and 列 (number fields, the keyboard's way) and a grid up to 8 by 8 that sets them, 第一行是表头, 表注.
  function tablePicker(me, host) {
    const number = (label, max) => { const i = mk("input", "ws-input latex-number"); i.type = "number"; i.min = "1"; i.max = String(max); i.value = "3"; i.setAttribute("aria-label", label); return i; };
    const rows = number(t("行"), 20), cols = number(t("列"), 10), caption = textInput(t("表注"));
    const header = mk("input");
    header.type = "checkbox"; header.checked = true;
    const headerField = mk("label", "latex-format-check");
    headerField.append(header, " " + t("第一行是表头"));
    const grid = mk("div", "latex-grid"), cells = [];
    grid.setAttribute("aria-hidden", "true");
    const light = (r, c) => cells.forEach((x) => x.el.classList.toggle("is-on", x.r <= r && x.c <= c));
    for (let r = 1; r <= 8; r++) for (let c = 1; c <= 8; c++) {
      const cell = mk("button", "latex-grid-cell");
      cell.type = "button";
      cell.setAttribute("tabindex", "-1");
      cell.addEventListener("mouseenter", () => light(r, c));
      cell.addEventListener("click", () => { rows.value = String(r); cols.value = String(c); light(r, c); });
      cells.push({ el: cell, r: r, c: c });
      grid.appendChild(cell);
    }
    grid.addEventListener("mouseleave", () => light(Number(rows.value) || 0, Number(cols.value) || 0));
    [rows, cols].forEach((i) => i.addEventListener("input", () => light(Number(rows.value) || 0, Number(cols.value) || 0)));
    light(3, 3);
    const go = insertButton(() => {
      const facts = panelFacts(me);
      if (applyFormat(me, (st) => tableSpec(st, { rows: rows.value, cols: cols.value, header: header.checked, caption: caption.value,
        booktabs: facts.booktabs, isMain: facts.isMain }))) closePicker(me);   // in the edit view the cursor is beside it, and the column says where its cells are filled in
    });
    const size = mk("div", "latex-format-row");
    size.append(fieldOf(t("行"), rows), fieldOf(t("列"), cols));
    host.replaceChildren(mk("h4", "latex-format-title", t("插入表格")), size, grid, headerField, fieldOf(t("表注"), caption), pickerActions(go, me));
  }

  // 引用文献 (22.1's promise): a search over the project's .bib entries; the keys picked go in one \cite{…}.
  async function citePicker(me, host, current) {
    const search = textInput(t("搜索文献")), list = mk("div", "latex-bibs"), picked = mk("p", "node-sub hidden"), keys = [];
    search.setAttribute("placeholder", t("搜索文献"));
    let entries = [];
    const go = insertButton(() => { if (keys.length && applyFormat(me, (st) => citeSpec(st, keys))) closePicker(me); });
    go.disabled = true;
    host.replaceChildren(mk("h4", "latex-format-title", t("插入文献引用")), search, list, picked, pickerActions(go, me));
    const show = () => {
      const q = folded(search.value);
      const shown = entries.filter((e) => [e.key, e.author, e.year, e.title].some((x) => folded(x).indexOf(q) >= 0)).slice(0, 60);
      list.replaceChildren(...shown.map((e) => {
        const b = mk("button", "latex-bib");
        b.type = "button";
        b.setAttribute("aria-pressed", String(keys.includes(e.key)));
        b.append(mk("span", "mono", e.key), mk("span", "node-sub-inline", [e.author, e.year].filter(Boolean).join(" ")));
        if (e.title) b.appendChild(mk("span", "latex-bib-title", e.title));
        b.addEventListener("click", () => {
          const at = keys.indexOf(e.key);
          if (at >= 0) keys.splice(at, 1); else keys.push(e.key);
          go.disabled = !keys.length;
          picked.textContent = keys.length ? t("已选：{keys}", { keys: keys.join(", ") }) : "";
          picked.classList.toggle("hidden", !keys.length);
          show();
        });
        return b;
      }));
      if (!shown.length) list.appendChild(mk("p", "node-sub", t("没有找到这样的文献")));
    };
    search.addEventListener("input", show);
    list.replaceChildren(mk("p", "node-sub", t("载入中…")));
    let data;
    try { data = await apiGet("/api/latex/insert?what=bib&path=" + encodeURIComponent(me.path)); } catch (err) {
      if (current()) list.replaceChildren(mk("p", "notice-status err", t("没能读到项目里的文献")));
      return;
    }
    if (!current()) return;
    entries = data.entries || [];
    if (!entries.length) {
      const words = (data.files || []).length ? t("项目的 .bib 文件里没有读到文献条目。") : t("项目里没有 .bib 文件，没有可以引用的文献。");
      host.replaceChildren(mk("h4", "latex-format-title", t("插入文献引用")), mk("p", "node-sub", words), cancelLink(me));
      return;
    }
    show();
  }

  // 文档信息 (22.12, B1): the main file's \title, \author and \date, read before written as every 版式 setting is: one
  // of them written in a form this does not take (twice, in a macro or a condition, holding a command such as \thanks
  // or \and) is the person's own, shown with its line and never written.
  const INFO_KEYS = ["title", "author", "date"];
  function docInfoOf(pre) {
    const code = masked(pre), out = {}, found = { title: [], author: [], date: [] };
    const lineOf = (pos) => pre.slice(0, pos).split("\n").length;
    for (const c of scanCommands(code)) {
      if (!Object.prototype.hasOwnProperty.call(found, c.name)) continue;
      found[c.name].push({ c: c, a: readArgs(code, c.nameTo, "m"), line: lineOf(c.from), top: c.depth === 0 && c.cond === 0 });
    }
    INFO_KEYS.forEach((k) => {
      const f = found[k], x = f[0];
      if (!f.length) { out[k] = { value: k === "date" ? "today" : "", absent: true }; return; }
      if (f.length > 1 || !x.a || !x.top || code[skipBlank(code, x.c.nameTo)] === "[") { out[k] = { mine: { line: x.line } }; return; }
      const arg = x.a.args[0], inner = pre.slice(arg.inner[0], arg.inner[1]).trim();
      if (k === "date" && /^\\today$/.test(inner)) { out[k] = { value: "today", arg: arg }; return; }
      if (k === "date" && !inner) { out[k] = { value: "none", arg: arg }; return; }
      const w = unescapeTex(inner);
      out[k] = w == null ? { mine: { line: x.line } } : k === "date" ? { value: "custom", text: w, arg: arg } : { value: w, arg: arg };
    });
    return out;
  }
  // One of them written: {changes} for the editor's dispatch, null when it already is so, or {refused}. `value`: the
  // words of the title or the author; for the date {kind: today | none | custom, text}.
  function docInfoSpec(state, key, value) {
    const text = state.doc.toString(), read = readLatex(text);
    if (!read.preamble) return { refused: t("主文件里没有 {command}，版式还不能设置。", { command: "\\begin{document}" }) };
    const pre = text.slice(0, read.preamble.to), all = docInfoOf(pre), info = all[key];
    if (info.mine) return { refused: t("导言区第 {n} 行已有自己写的设置", { n: info.mine.line }) };
    const arg = key === "date" ? (value.kind === "today" ? "\\today" : value.kind === "none" ? "" : escapeTyped(value.text)) : escapeTyped(value);
    if (info.arg) {
      if (pre.slice(info.arg.inner[0], info.arg.inner[1]) === arg) return null;
      return { changes: state.changes({ from: info.arg.inner[0], to: info.arg.inner[1], insert: arg }) };
    }
    if (key === "date" && value.kind === "today") return null;   // no \date: LaTeX prints the day of the export
    const r = readPreamble(pre);
    if (!r.dc) return { refused: t("主文件里没有找到 {command}。", { command: "\\documentclass" }) };
    const line = "\\" + key + "{" + arg + "}", there = INFO_KEYS.map((k) => all[k].arg).filter(Boolean).sort((a, b) => b.to - a.to)[0];
    const spec = there ? { from: r.lineEnd(there.to), insert: "\n" + line } : newLines(r, [line], false);
    return { changes: state.changes({ from: spec.from, to: spec.to == null ? spec.from : spec.to, insert: spec.insert.split("\n").join(state.lineBreak) }) };
  }

  // 版式 (22.12, A2): the document's settings, each a select showing what the main file's preamble says. In a chapter
  // file they are shown and cannot be changed, with a link that opens the main file; a setting written in a form 版式
  // does not take shows 「自定义」, cannot be changed, and says on which line (with a link that shows it).
  function layoutChoices(key, layout) {
    const ctex = !!(layout && (layout.ctexClass || layout.ctexPkg)), set = layout && !layout.why ? layout.settings : null;
    switch (key) {
      case "margin": return [["", t("默认")], ["2", t("窄（2 厘米）")], ["2.5", t("适中（2.5 厘米）")], ["3", t("宽（3 厘米）")], ["custom", t("自定义")]];
      case "columns": return [["", t("单栏")], ["twocolumn", t("双栏")]];
      case "pagestyle": return [["", t("默认")], ["plain", t("页脚居中")], ["empty", t("不显示")]];
      case "hyperref": return [["off", t("关")], ["on", t("开")]];
      case "size": return [["", t("默认")], ["10pt", "10pt"], ["11pt", "11pt"], ["12pt", "12pt"]].concat(ctex ? [["zihao=5", t("五号")], ["zihao=-4", t("小四")]] : []);
      case "spacing": return [["", t("默认")], ["1", t("单倍")], ["1.25", t("1.25 倍")], ["1.5", t("1.5 倍")], ["2", t("双倍")], ["custom", t("自定义")]];
      case "indent": return [["", t("默认")], ["0pt", t("无")], ["2em", t("2 字")]];
      case "parskip": return [["", t("无")], ["half", t("半行")], ["one", t("一行")]];
      // A3: a choice the document cannot take is shown, disabled, with why
      case "secnum": return [["", "1 / 1.1 / 1.1.1"], unable("zh", t("一、（一）1."), set ? layout.zhNumbersWhy : null),
        unable("none", t("不编号"), set && set.capnum.value === "within" && !set.capnum.mine && set.secnum.value !== "none" ? t("图表按章或节编号时不能选") : null)];
      case "capname": return [unable("zh", t("图 / 表"), set && !layout.cjk ? t("中文图表名需要中文文档类") : null), ["en", "Figure / Table"]];
      case "capnum": {
        const within = unable("within", t("按章或节编号（图 2-3）"), set && set.secnum.value === "none" && !set.secnum.mine && set.capnum.value !== "within" ? t("章节不编号时不能选") : null);
        return layout && layout.chapters ? [["continuous", t("连续编号（图 1）")], ["", t("按章编号（图 2.3）")], within] : [["", t("连续编号（图 1）")], within];
      }
      default: return [];
    }
  }
  function unable(value, label, why) { return why ? [value, t("{choice}：{why}", { choice: label, why: why }), true] : [value, label]; }
  function layoutLabel(key) {
    return { secnum: t("章节编号"), capname: t("图表名称"), capnum: t("图表编号"), margin: t("页边距"), columns: t("分栏"), pagestyle: t("页码"), hyperref: t("超链接"), size: t("正文字号"),
      spacing: t("行距"), indent: t("首行缩进"), parskip: t("段间距") }[key];
  }

  function layoutPane(me, ui) {
    const L = { rows: {}, head: mk("div", "latex-layout-head hidden"), note: mk("p", "node-sub latex-format-note hidden") };
    L.note.setAttribute("role", "status");
    const number = (label, step, min, max) => { const i = mk("input", "ws-input latex-number"); i.type = "number"; i.step = step; i.min = min; i.max = max; i.setAttribute("aria-label", label); return i; };
    const control = (key) => {
      const field = mk("label", "latex-format-field"), select = mk("select", "ws-input latex-format-select");
      field.append(mk("span", "latex-format-label", layoutLabel(key)), select);
      select.addEventListener("change", () => chooseLayout(me, key, select.value));
      const row = mk("div", "latex-layout-item"), extra = mk("div", "latex-format-row hidden"), note = mk("p", "node-sub latex-layout-note hidden");
      row.append(field, extra, note);
      L.rows[key] = { select: select, extra: extra, note: note, row: row, shape: "" };
      return row;
    };
    const rows = {};
    LAYOUT_KEYS_ORDER.forEach((k) => { rows[k] = control(k); });
    // 自定义 页边距: 上, 下, 左, 右 in cm; 自定义 行距: the number of lines
    L.margins = {};
    [["top", t("上")], ["bottom", t("下")], ["left", t("左")], ["right", t("右")]].forEach(([k, label]) => {
      const i = number(label, "0.1", "0", "10");
      i.addEventListener("change", () => applyLayout(me, "margin", Object.assign({ preset: "custom" }, marginInputs(L))));
      L.margins[k] = i;
      L.rows.margin.extra.append(fieldOf(label, i));
    });
    L.rows.margin.extra.append(mk("span", "latex-format-label", t("厘米")));
    L.stretch = number(t("行距"), "0.05", "0.5", "4");
    L.stretch.addEventListener("change", () => applyLayout(me, "spacing", "custom", { n: Number(L.stretch.value) }));
    L.rows.spacing.extra.append(L.stretch, mk("span", "latex-format-label", t("倍")));
    const group = (title, keys) => { const g = mk("div", "latex-format-group"); g.append(mk("h4", "latex-format-title", title), ...keys.map((k) => rows[k])); return g; };
    // 文档信息 (B1): 标题, 作者 and 日期, each written when changed (one step of ⌘Z)
    const info = { title: textInput(t("标题")), author: textInput(t("作者")), date: mk("select", "ws-input latex-format-select"), dateText: textInput(t("日期")),
      note: mk("p", "node-sub latex-layout-note hidden") };
    info.date.setAttribute("aria-label", t("日期"));
    info.date.replaceChildren(...[["today", t("今天")], ["none", t("不显示")], ["custom", t("自定义")]].map(([v, label]) => { const o = mk("option", null, label); o.value = v; return o; }));
    info.title.addEventListener("change", () => applyInfo(me, "title", info.title.value));
    info.author.addEventListener("change", () => applyInfo(me, "author", info.author.value));
    info.date.addEventListener("change", () => {
      if (info.date.value === "custom") { info.dateText.classList.remove("hidden"); if (info.dateText.value.trim()) applyInfo(me, "date", { kind: "custom", text: info.dateText.value }); return; }
      applyInfo(me, "date", { kind: info.date.value });
    });
    info.dateText.addEventListener("change", () => applyInfo(me, "date", { kind: "custom", text: info.dateText.value }));
    const infoGroup = mk("div", "latex-format-group");
    infoGroup.append(mk("h4", "latex-format-title", t("文档信息")), fieldOf(t("标题"), info.title), fieldOf(t("作者"), info.author), fieldOf(t("日期"), info.date), info.dateText, info.note);
    L.info = info;
    ui.panes.layout.replaceChildren(L.head, L.note, infoGroup, group(t("页面"), ["margin", "columns", "pagestyle", "hyperref"]),
      group(t("文字与段落"), ["size", "spacing", "indent", "parskip"]), group(t("章节与图表"), ["secnum", "capname", "capnum"]));
    return L;
  }
  function marginInputs(L) { const v = {}; Object.keys(L.margins).forEach((k) => { v[k] = Number(L.margins[k].value); }); return v; }

  // 文档信息 written as the editor's own transaction; a refusal said in the tab.
  function applyInfo(me, key, value) {
    const L = me.ui.format && me.ui.format.layout;
    if (!L || !layoutState(me).writable) return;
    let refused = null;
    const done = applyFormat(me, (st) => { const r = docInfoSpec(st, key, value); if (r && r.refused) { refused = r.refused; return null; } return r; }, true);
    L.note.textContent = refused || "";
    L.note.classList.toggle("hidden", !refused);
    if (!done) drawLayout(me, true);
  }
  // A select's choice: 自定义 shows its fields (written when one of them changes); any other is written at once.
  function chooseLayout(me, key, v) {
    me.layoutCustom = me.layoutCustom || {};
    if ((key === "margin" || key === "spacing") && v === "custom") { me.layoutCustom[key] = true; drawLayout(me, true); return; }
    if (key === "margin" || key === "spacing") me.layoutCustom[key] = false;
    applyLayout(me, key, key === "margin" ? (v ? { preset: v } : null) : v);
  }
  // A setting written as the editor's own transaction (22.12's one rule), a refusal said in the tab.
  function applyLayout(me, key, value, extra) {
    const L = me.ui.format && me.ui.format.layout;
    if (!L || !layoutState(me).writable) return;
    let refused = null;
    const done = applyFormat(me, (st) => {
      const r = layoutSpec(st, key, value, Object.assign({ local: (me.format && me.format.local) || [] }, extra || {}));
      if (r && r.refused) { refused = r.refused; return null; }
      return r;
    }, true);
    L.note.textContent = refused || "";
    L.note.classList.toggle("hidden", !refused);
    if (!done) drawLayout(me, true);
  }

  // The page's first-line indent (22.12 B2), as the PDF has it: 版式's 首行缩进 (无 none, 2 字 2em), else the class's own
  // (ctex 2em, the others 1.5em); the parskip package without its indent option sets none. After a heading only ctex
  // and the indentfirst package indent. With no main file read, 1.5em and none after a heading.
  function pageIndent(layout, pre) {
    if (!layout) return { all: "1.5em", first: "0" };
    const S = layout.settings && layout.settings.indent, code = (pre || "").split("\n").map(codePart).join("\n");
    let all = layout.ctex ? "2em" : "1.5em";
    if (S && !S.mine && S.value === "0pt") all = "0";
    else if (S && !S.mine && S.value === "2em") all = "2em";
    else if (S && S.mine) {
      // the person's own: a plain length in em (or ctex's \ccwd) read as it is; the parskip package without indent none
      const own = [...code.matchAll(/\\setlength\s*\{?\s*\\parindent\s*\}?\s*\{\s*(\d*\.?\d+)\s*(em|\\ccwd)\s*\}/g)].pop();
      const pk = /\\usepackage\s*(?:\[([^\]]*)\])?\s*\{\s*parskip\s*\}/.exec(code);
      if (pk && !/(?:^|,)\s*indent\s*(?:,|$)/.test(pk[1] || "")) all = "0";
      else if (own) all = Number(own[1]) ? Math.min(Number(own[1]), 6) + "em" : "0";
    }
    const first = layout.ctex || /\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*\bindentfirst\b[^}]*\}/.test(code) ? all : "0";
    return { all: all, first: first };
  }

  // Where the settings come from: the open file's preamble when it is the main file, else the main file's (`format`).
  function layoutState(me) {
    const facts = panelFacts(me), off = layoutOff(me);
    let pre = null, why = off || "";
    if (why) return { facts: facts, pre: null, why: why, writable: false };
    if (facts.isMain) {
      const value = me.view.state.field(wordField(me.CM).field, false), read = value ? value.read : readLatex(me.view.state.doc.toString());
      if (read.preamble) pre = me.view.state.doc.sliceString(0, read.preamble.to);
      else if (!why) why = t("主文件里没有 {command}，版式还不能设置。", { command: "\\begin{document}" });
    } else if (me.format && typeof me.format.preamble === "string") pre = me.format.preamble;
    else if (!why) why = t("没有找到主文件。版式在主文件的导言区里设置。");
    return { facts: facts, pre: pre, why: why, writable: !why && facts.isMain && pre != null };
  }

  // Drawn with the panel's state (every change of the text or the cursor, at the next frame): the preamble is read
  // again only when it or the main file's facts changed; the rows only while 版式 is shown.
  function drawLayout(me, force) {
    const L = me.ui.format && me.ui.format.layout;
    if (!L || !me.view) return;
    const st = layoutState(me), local = (me.format && me.format.local) || [];
    if (L.formatRef !== me.format || !("cacheKey" in L)) { L.formatRef = me.format; L.cacheKey = undefined; }
    const key = st.pre == null ? null : st.pre;
    if (key !== L.cacheKey) { L.cacheKey = key; L.layout = key == null ? null : layoutOf(st.pre, local); force = true; }
    const layout = L.layout, why = st.why || (layout && layout.why) || "";
    // like Word (22.1, A2's C): the editor's lines follow 行距, roughly
    const sp = layout && !layout.why ? layout.settings.spacing : null, n = sp && !sp.mine && sp.n ? sp.n : 1;
    if (me.ui.box.style && typeof me.ui.box.style.setProperty === "function") {
      me.ui.box.style.setProperty("--latex-stretch", String(Math.min(Math.max(n, 0.8), 2.5)));
      // and its paragraphs' first lines follow 首行缩进 (22.12 B2)
      const ind = pageIndent(layout && !layout.why ? layout : null, st.pre);
      me.ui.box.style.setProperty("--latex-indent", ind.all);
      me.ui.box.style.setProperty("--latex-indent-first", ind.first);
    }
    if (me.ui.format.panes.layout.classList.contains("hidden") && !force) return;
    const main = st.facts.main;
    drawInfo(me, L, st, st.why);
    // the head: why nothing can be set, or that a chapter's layout is its main file's
    if (why) L.head.replaceChildren(mk("p", "node-sub", why));
    else if (!st.facts.isMain) L.head.replaceChildren(mk("p", "node-sub", t("版式在主文件 {main} 里设置。", { main: main })),
      mkButton(t("打开 {file}", { file: main }), "ws-text-link", () => open(main)));
    L.head.classList.toggle("hidden", !why && st.facts.isMain);
    LAYOUT_KEYS_ORDER.forEach((k) => {
      const row = L.rows[k], S = layout && !layout.why ? layout.settings[k] : null, custom = me.layoutCustom && me.layoutCustom[k];
      const choices = layoutChoices(k, layout).concat(S && S.mine ? [["mine", t("自定义"), true]] : []);
      const shape = JSON.stringify(choices);
      if (shape !== row.shape) {
        row.shape = shape;
        row.select.replaceChildren(...choices.map(([value, label, off]) => { const o = mk("option", null, label); o.value = value; if (off) o.disabled = true; return o; }));
      }
      row.select.value = !S ? choices[0][0] : S.mine ? "mine" : custom ? "custom" : S.value;
      row.select.disabled = !S || !!why || !st.writable || !!S.mine;
      // 自定义's fields: the margins or the stretch the file has, filled when they are first shown or the file changed
      const open = !!S && !S.mine && (S.value === "custom" || !!custom);
      if (open && (force || row.extra.classList.contains("hidden"))) {
        if (k === "margin") Object.keys(L.margins).forEach((x) => { L.margins[x].value = S.value ? String(S[x]) : "2.5"; });
        if (k === "spacing") L.stretch.value = String(S.n || 1);
      }
      row.extra.classList.toggle("hidden", !open);
      (k === "margin" ? Object.values(L.margins) : k === "spacing" ? [L.stretch] : []).forEach((i) => { i.disabled = row.select.disabled; });
      // a setting the person wrote: where, with a link that shows the line; 不编号's one consequence (A3)
      const hint = k === "secnum" && S && !S.mine && S.value === "none" ? t("不编号时，{command} 引用章节印不出编号。", { command: "\\ref" }) : "";
      if (S && S.mine) {
        const said = S.mine.why || (S.mine.file ? t("{file} 第 {n} 行已有自己写的设置", { file: S.mine.file, n: S.mine.line }) : t("导言区第 {n} 行已有自己写的设置", { n: S.mine.line }));
        const parts = [mk("span", null, said)];
        if (!S.mine.file && S.mine.line) parts.push(" ", mkButton(t("看这一行"), "ws-text-link", () => (st.facts.isMain ? goToLine(me, S.mine.line) : openAt(main, S.mine.line))));
        row.note.replaceChildren(...parts);
      } else if (hint) row.note.replaceChildren(mk("span", null, hint));
      else row.note.replaceChildren();
      row.note.classList.toggle("hidden", !(S && S.mine) && !hint);
    });
  }

  // 文档信息's fields from the preamble: never over what is being typed in one of them.
  function drawInfo(me, L, st, why) {
    const I = L.info;
    if (!I) return;
    const got = st.pre == null ? null : docInfoOf(st.pre), off = !!why || !st.writable;
    const typing = (el) => typeof document !== "undefined" && document.activeElement === el;
    const mine = [];
    [["title", I.title], ["author", I.author]].forEach(([k, input]) => {
      const x = got ? got[k] : null;
      if (!typing(input)) input.value = x && !x.mine ? x.value : "";
      input.disabled = off || !x || !!x.mine;
      if (x && x.mine) mine.push(x.mine.line);
    });
    const d = got ? got.date : null;
    I.date.value = d && !d.mine ? d.value : "today";
    I.date.disabled = off || !d || !!d.mine;
    if (d && d.mine) mine.push(d.mine.line);
    if (!typing(I.dateText) && d && d.value === "custom") I.dateText.value = d.text;
    I.dateText.classList.toggle("hidden", !(d && !d.mine && (d.value === "custom" || I.date.value === "custom")));
    I.dateText.disabled = I.date.disabled;
    if (mine.length) {
      const n = Math.min(...mine), parts = [mk("span", null, t("导言区第 {n} 行已有自己写的设置", { n: n }))];
      if (st.facts.isMain) parts.push(" ", mkButton(t("看这一行"), "ws-text-link", () => goToLine(me, n)));
      I.note.replaceChildren(...parts);
    } else I.note.replaceChildren();
    I.note.classList.toggle("hidden", !mine.length);
  }

  // 交叉引用 (A2): the \label keys of the open file and of the other files of its paper (GET /api/latex/insert, what=labels),
  // grouped as 图, 表, 公式, 节 and 其他; the one picked goes in as \ref{…} (\eqref for a formula where amsmath is loaded).
  const labelGroups = () => [["figure", t("图")], ["table", t("表")], ["equation", t("公式")], ["section", t("节")], ["other", t("其他")]];
  async function refPicker(me, host, current) {
    const search = textInput(t("搜索标签")), list = mk("div", "latex-bibs"), said = mk("p", "node-sub hidden");
    search.setAttribute("placeholder", t("搜索标签"));
    const own = labelsOf(me.view.state.doc.toString(), readLatex(me.view.state.doc.toString())).map((x) => Object.assign({ file: me.path }, x));
    let labels = own, chosen = null;
    const go = insertButton(() => {
      if (!chosen) return;
      if (applyFormat(me, (st) => refSpec(st, chosen.key, chosen.kind === "equation" && panelFacts(me).amsmath))) closePicker(me);
    });
    go.disabled = true;
    host.replaceChildren(mk("h4", "latex-format-title", t("插入交叉引用")), search, list, said, pickerActions(go, me));
    const show = () => {
      const q = folded(search.value), shown = labels.filter((x) => folded(x.key).indexOf(q) >= 0 || folded(x.file).indexOf(q) >= 0);
      const parts = [];
      labelGroups().forEach(([kind, word]) => {
        const of = shown.filter((x) => x.kind === kind).slice(0, 60);
        if (!of.length) return;
        parts.push(mk("p", "latex-group", word));
        of.forEach((x) => {
          const b = mk("button", "latex-bib");
          b.type = "button";
          b.setAttribute("aria-pressed", String(chosen === x));
          b.append(mk("span", "mono", x.key));
          if (x.file !== me.path) b.append(mk("span", "node-sub-inline", t("在 {file}", { file: x.file })));
          b.addEventListener("click", () => { chosen = x; go.disabled = false; show(); });
          parts.push(b);
        });
      });
      list.replaceChildren(...(parts.length ? parts : [mk("p", "node-sub", labels.length ? t("没有找到这样的标签") : t("还没有可以引用的标签（{command}）。", { command: "\\label" }))]));
    };
    search.addEventListener("input", show);
    show();
    let data = null;
    try { data = await apiGet("/api/latex/insert?what=labels&path=" + encodeURIComponent(me.path)); } catch (err) {
      if (!current()) return;
      said.textContent = t("没能读到主文件和其他章节里的标签");
      said.classList.remove("hidden");
      return;
    }
    if (!current()) return;
    const seen = new Set(own.map((x) => x.key));
    labels = own.concat((data.labels || []).filter((x) => x && x.key && !seen.has(x.key)));
    show();
  }

  // 网址 (A2): an address typed (the selection, when it is one, filled in) goes in as \url{…}.
  function urlPicker(me, host) {
    const input = textInput(t("网址")), sel = me.view.state.selection.main, picked = me.view.state.doc.sliceString(sel.from, sel.to);
    input.setAttribute("placeholder", "https://");
    if (picked && picked.length <= 300 && !/\s/.test(picked)) input.value = picked;
    const go = insertButton(() => {
      const facts = panelFacts(me);
      if (applyFormat(me, (st) => urlSpec(st, input.value, { url: facts.url, isMain: facts.isMain, main: facts.main }))) closePicker(me);
    });
    host.replaceChildren(mk("h4", "latex-format-title", t("插入网址")), fieldOf(t("网址"), input), pickerActions(go, me));
  }

  // The right column: 格式与插入 at the top, then what the column shows (the passages, a link or a passage's panel).
  function fillSide(me, parts) { me.ui.side.replaceChildren(...(me.ui.format ? [me.ui.format.box] : []), ...parts); }

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

  // A line of the file (an export error's, 版式's 看这一行): lines are the code's, so 显示代码 is turned on first.
  function goToLine(me, n) {
    const doc = me.view.state.doc, line = doc.line(Math.min(Math.max(1, n), doc.lines));
    codeAt(me, line.from);
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

  // 新建文稿 (22.11): a form in 正文's page -- 类型, 文件夹, 文件名, 标题 and, for a chapter, 加入主文件 --, kept through the page's
  // redraws (`newForm`, its box moved into the new page) so nothing typed is lost; 创建 is its one primary while the page's
  // 新建文稿 is hidden. POST /api/latex/new; the editor then opens on the new file, saying when a chapter's \input was not added.
  let newForm = null;
  const KINDS = ["zh_paper", "en_paper", "chapter"];
  function newDraftForm(data) {
    const listed = [].concat(...(data.groups || []).map((g) => [g.main].concat(g.files)), data.others || []).map((d) => d.path);
    const taken = new Set(listed.map((p) => p.toLowerCase()));
    const field = (label, input) => { const f = mk("label", "ws-field"); f.append(mk("span", "ws-field-label", label), input); return f; };
    const input = (value) => { const i = mk("input", "form-input"); i.type = "text"; i.value = value || ""; return i; };
    const box = mk("div", "ws-task-form latex-new"), kinds = mk("div", "latex-new-kinds"), radios = {};
    kinds.appendChild(mk("span", "ws-field-label", t("类型")));
    [[KINDS[0], t("中文论文")], [KINDS[1], t("英文论文")], [KINDS[2], t("章节文件")]].forEach(([value, label]) => {
      const choice = mk("label", "latex-new-kind"), radio = mk("input");
      radio.type = "radio"; radio.name = "latex-new-kind"; radio.value = value; radio.checked = value === "zh_paper";
      radio.addEventListener("change", () => fill());
      radios[value] = radio; choice.append(radio, " " + label); kinds.appendChild(choice);
    });
    const folder = input(data.new_folder), name = input(""), title = input(""), main = mk("select", "form-input");
    const folders = mk("datalist"), mains = (data.groups || []).map((g) => g.main.path);
    folders.id = "latex-new-folders"; folder.setAttribute("list", folders.id);
    [...new Set([data.new_folder].concat(listed.map((p) => p.slice(0, Math.max(p.lastIndexOf("/"), 0)))))]
      .filter(Boolean).forEach((f) => { const o = mk("option"); o.value = f; folders.appendChild(o); });
    [["", t("不加入")]].concat(mains.map((m) => [m, m])).forEach(([value, label]) => { const o = mk("option", "", label); o.value = value; main.appendChild(o); });
    main.value = mains.length === 1 ? mains[0] : "";
    const mainField = field(t("加入主文件"), main), status = mk("span", "notice-status");
    let named = false;
    name.addEventListener("input", () => { named = true; });
    folder.addEventListener("input", () => fill());
    const kind = () => KINDS.find((k) => radios[k].checked) || KINDS[0];
    // 文件名 follows 类型 and 文件夹 until the person types one: main.tex for a paper, intro.tex for a chapter, -2, -3 when taken
    function fill() {
      mainField.classList.toggle("hidden", kind() !== "chapter");
      if (named) return;
      const stem = kind() === "chapter" ? "intro" : "main", dir = folder.value.trim().replace(/^\/+|\/+$/g, "");
      let n = 1;
      while (taken.has(((dir ? dir + "/" : "") + stem + (n > 1 ? "-" + n : "") + ".tex").toLowerCase())) n += 1;
      name.value = stem + (n > 1 ? "-" + n : "") + ".tex";
    }
    const close = () => { const f = newForm; newForm = null; if (f) { f.slot.replaceChildren(); f.opener.classList.remove("hidden"); } };
    const go = mkButton(t("创建"), "btn btn-primary", async () => {
      go.disabled = true;
      setStatus(status, "", t("正在新建…"));
      const body = { project: state.currentPath, kind: kind(), folder: folder.value, name: name.value, title: title.value };
      if (body.kind === "chapter" && main.value) body.main = main.value;
      let r;
      try { r = await apiPost("/api/latex/new", body); } catch (err) {
        go.disabled = false;
        setStatus(status, "err", t("没能新建文稿"), err);
        return;
      }
      newForm = null;
      open(r.path, r.input && !r.input.added ? { note: r.input.message } : null);
    });
    const actions = mk("div", "latex-export-line");
    actions.append(go, mkButton(t("取消"), "ws-text-link", close), status);
    box.append(kinds, field(t("文件夹"), folder), folders, field(t("文件名"), name), field(t("标题（可选）"), title), mainField, actions);
    fill();
    return { box: box, slot: null, opener: null };
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
    const page = mk("div", "latex-drafts"), bar = mk("div", "latex-export-line"), slot = mk("div", "latex-new-slot");
    const place = () => { newForm.slot = slot; newForm.opener = opener; opener.classList.add("hidden"); slot.replaceChildren(newForm.box); };
    const opener = mkButton(t("新建文稿"), "btn btn-primary", () => { newForm = newDraftForm(data); place(); });
    bar.appendChild(opener);
    page.append(mk("p", "node-sub", t("项目里的 LaTeX 文稿。点一份就在编辑器里打开；改动保存回原文件。")), bar, slot);
    // where 创建 could not write (a project opened read-only, a member outside their working copy) it is not offered,
    // as 新建笔记 and 写报告 are not
    if (data.can_write === false) { opener.disabled = true; newForm = null; }
    if (newForm) place();   // a redraw keeps the open form and what is typed in it
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
      if (g.files.length) { const files = list(g.files); files.classList.add("latex-draft-files"); block.appendChild(files); }   // under its main file, set in
      page.appendChild(block);
    });
    if ((data.others || []).length) {
      const block = mk("section", "res-block latex-draft-others");
      if ((data.groups || []).length) block.appendChild(mk("h2", "desk-title", t("其他 .tex 文件")));
      block.appendChild(list(data.others));
      page.appendChild(block);
    }
    if (!(data.groups || []).length && !(data.others || []).length) {
      page.appendChild(mk("p", "node-empty", t("这个项目里还没有 .tex 文稿。点「新建文稿」开始写，或把 .tex 文件放进项目文件夹。")));
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
  function reset() { discard(); newForm = null; fromView = null; lastShown = null; exporting = null; sourceShown.clear(); Object.keys(drawn).forEach((k) => { delete drawn[k]; }); }

  if (typeof window.addEventListener === "function") window.addEventListener("focus", () => { checkRevision(); });

  window.ROLatex = Object.freeze({
    loadEditorLibs: loadEditorLibs, loadMath: loadMath, open: open, isDirty: dirty, mayClose: mayClose, section: section, reset: reset,
    renderSupported: renderSupported, paperCounts: paperCounts, refresh: refresh, exporter: exporter, loadDrafts: loadDrafts,
    _vendorScripts: VENDOR_SCRIPTS, _passages: () => (ed && ed.view ? passagesOf(ed) : null),
    _pure: Object.freeze({ cpLength: cpLength, serverEdits: serverEdits, breakFilter: breakFilter, includeMatches: includeMatches, positions: positions,
      readLatex: readLatex, bodyStart: bodyStart, returnSpec: returnSpec, wordField: wordField, wordExtensions: wordExtensions, onReturn: onReturn, mathDOM: mathDOM,
      preambleFacts: preambleFacts, figurePath: figurePath, escapeTex: escapeTex, crosses: crosses, styleSpec: styleSpec, fontSpec: fontSpec,
      sizeSpec: sizeSpec, headingSpec: headingSpec, headingLevel: headingLevel, figureSpec: figureSpec, tableSpec: tableSpec, mathSpec: mathSpec,
      listSpec: listSpec, footnoteSpec: footnoteSpec, citeSpec: citeSpec, formatState: formatState,
      readPreamble: readPreamble, layoutOf: layoutOf, layoutSpec: layoutSpec, alignSpec: alignSpec, quoteSpec: quoteSpec,
      paragraphState: paragraphState, pageBreakSpec: pageBreakSpec, tocSpec: tocSpec, refSpec: refSpec, urlSpec: urlSpec, labelsOf: labelsOf,
      bodyWords: bodyWords, tabularGrid: tabularGrid, columnsOf: columnsOf, inlineInto: inlineInto, preambleWords: preambleWords,
      unsureWhy: unsureWhy, enterSpec: enterSpec, backSpec: backSpec, plainOf: plainOf, numbersOf: numbersOf, plainWords: plainWords,
      docInfoOf: docInfoOf, docInfoSpec: docInfoSpec, unescapeTex: unescapeTex, escapeTyped: escapeTyped,
      quietLine: quietLine, paraReach: paraReach, pageIndent: pageIndent }),
  });
})();
