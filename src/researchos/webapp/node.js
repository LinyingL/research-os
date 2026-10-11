// The node page, 成果 and 数据与代码 -- DESIGN.md 12.6, 12.7, 12.9, 12.11;
// task V8 phase U3. Served verbatim at /node.js by the same process and
// loaded by app.html with a plain same-origin <script src>, exactly as
// /desk.js is: zero build, zero external resources, same origin check.
//
// It defines one global, window.RONode, and calls app.html's helpers (mk,
// mkButton, apiGet, apiPost, setStatus, renderBlockingError,
// renderViewFailure, clearProjectState, ownMark, openFilePanel,
// openAttemptForm, openVariableCard, activateView, humanRecordsBlocked,
// ACT_WORDS, state, views, currentView, RODesk, RCECanvas) only at run
// time, never while loading.
//
// Every object opens the same page: what it is, its trace (来龙去脉: the
// columns 数据 | 脚本 | 中间数据 | 脚本 | 本节点 | 被引用 joined by drawn
// lines, every break a gap with its repair), its changes and notes as one
// timeline (改动与备注), the machine links that touch it (判断), and the
// canvas scoped to its neighbourhood behind 「打开关系图」 (关系图).
//
// What it reads (all GET, all origin-checked by the server):
//   /api/node?node=        -- the page (researchos.nodepage.node);
//   /api/history?node=     -- its versions, newest first;
//   /api/history/diff?node=&a=&b= -- what changed between two;
//   /api/notes?node=       -- its notes;
//   /api/me                -- who I am (my own notes can be withdrawn);
//   /api/results[?draft=]  -- 成果; /api/data-code -- 数据与代码.
// What it writes, each through the existing write path:
//   POST /api/mappings/add    -- 「补上来源」 (generates / writes) and
//                                「关联文件」 (belongs_to); in a member's
//                                working copy these travel as packets (U1);
//   POST /api/mappings/delete -- 「删除标注」 on a hand-drawn link;
//   POST /api/judgements      -- 确认 / 否决 / 撤回 on a machine link;
//   POST /api/notes           -- a note added, or my own note withdrawn.
// Every path or id sent comes from a server payload; nothing typed on the
// page ever becomes a path.
//
// 12.11: no emoji, no arrow characters; relations in words (读取, 写出,
// 生成, 引用, 属于) or drawn as lines; verdicts as text status labels; the
// researcher's own words (titles, verdict wording, file names) as written.
(function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const DIFF_SHOWN = 20;
  const DIFFS_AUTO = 5;
  const LINKS_SHOWN = 40;
  const TRACE_TONES = { complete: "olive", no_source: "clay", no_attempt: "ochre" };
  const SOURCE_WORDS = { scan: "本机扫描时记下", approval: "批准后写入", publish: "发布时记下", git: "git 提交", card: "定义卡记录" };
  const VIA_WORDS = { step_files: "步骤脚本", belongs_to: "人工关联" };
  const STATUS_WORDS = { pending: "待确认", confirmed: "已确认", rejected: "已否决", auto: "机器提取" };
  const JUDGE_WORDS = { confirmed: "确认了", rejected: "否决了", withdrawn: "撤回了对它的判断", undone: "撤销了上一次判断" };
  const FILTERS = [
    ["all", "全部"], ["orphans", "无来源输入"], ["chains", "血缘链"], ["broken", "断链"], ["duplicates", "同名拷贝"],
  ];
  // Monochrome line icons for file types (12.11: where they help recognition).
  const ICONS = {
    script: "M4.5 3.5 2 8l2.5 4.5M11.5 3.5 14 8l-2.5 4.5M9.5 2.5l-3 11",
    dataset: "M3 4c0-.9 2.2-1.5 5-1.5s5 .6 5 1.5-2.2 1.5-5 1.5S3 4.9 3 4zM3 4v8c0 .9 2.2 1.5 5 1.5s5-.6 5-1.5V4M3 8c0 .9 2.2 1.5 5 1.5S13 8.9 13 8",
    figure: "M2.5 3.5h11v9h-11zM4.5 10.5l2.5-3 2 2 1.5-1.5 1.5 2.5",
    file: "M4 2.5h5l3 3v8H4zM9 2.5v3h3",
    section: "M3 3.5h10M3 6.5h10M3 9.5h10M3 12.5h6",
    claim: "M3 3.5h10M3 6.5h10M3 9.5h6",
    attempt: "M4.5 2.5v11M4.5 7.5c0-2 1.5-3 3.5-3h4.5M4.5 10.5c1.5 0 3 .5 3 2.5",
    variable: "M5.5 2.5c-1.5 0-2 .8-2 2v1.5c0 1-.5 2-1.5 2 1 0 1.5 1 1.5 2v1.5c0 1.2.5 2 2 2M10.5 2.5c1.5 0 2 .8 2 2v1.5c0 1 .5 2 1.5 2-1 0-1.5 1-1.5 2v1.5c0 1.2-.5 2-2 2",
    reference: "M2.5 3h4a1.5 1.5 0 0 1 1.5 1.5V13a1.5 1.5 0 0 0-1.5-1.5h-4zM13.5 3h-4A1.5 1.5 0 0 0 8 4.5V13a1.5 1.5 0 0 1 1.5-1.5h4z",
  };

  // -- pure helpers (loaded into node by tests/test_v8_u3.py) ---------------------

  // The rows of a unified diff: the diff's own prefix kept, in two quiet
  // colours (added / removed); the file header lines are left out (the
  // page says which versions it compares).
  function diffRows(text) {
    const rows = [];
    String(text || "").split("\n").forEach((line, i, all) => {
      if (i === all.length - 1 && line === "") return;
      if (line.startsWith("--- ") || line.startsWith("+++ ")) return;
      let cls = "ctx";
      if (line.startsWith("@@")) cls = "hunk";
      else if (line.startsWith("+")) cls = "add";
      else if (line.startsWith("-")) cls = "del";
      else if (line.startsWith("\\")) cls = "meta";
      rows.push({ cls: cls, text: line });
    });
    return rows;
  }

  function traceTone(state) {
    return Object.prototype.hasOwnProperty.call(TRACE_TONES, state) ? TRACE_TONES[state] : "plain";
  }

  // A link said in words: 「17-定价检验.py 写出 fig_vol.png」.
  function linkSentence(link) {
    return (link.src_label || link.src) + " " + (link.relation || link.type) + " " + (link.dst_label || link.dst);
  }

  // Where a link stands, in words: who drew it and what has been decided.
  function linkStateWords(link) {
    if (link.human) return "人工标注";
    if (link.conflict) return "记录冲突，待处理";
    if (link.review) return "待复核";
    return STATUS_WORDS[link.status] || "机器提取";
  }

  // The judgment actions a machine link offers -- the canvas's own rule
  // (canvas.js linkActions): confirm unless it stands confirmed, reject,
  // and withdraw while a verdict stands.
  function judgeActions(link) {
    if (link.human) return ["delete"];
    const j = link.judgement;
    const waiting = !!(link.review || link.conflict);
    const stands = waiting ? !!((j && (j.verdict === "confirmed" || j.verdict === "rejected")) || link.conflict)
      : link.status === "confirmed" || link.status === "rejected";
    const out = [];
    if (waiting || link.status !== "confirmed") out.push("confirmed");
    if (waiting || link.status !== "rejected") out.push("rejected");
    if (stands) out.push("withdrawn");
    return out;
  }

  // A hand-drawn link as its mappings.toml entry names it: `reads` is
  // written data-first (8.5), every other type as it is stored.
  function mappingEnds(link) {
    const path = (id) => (String(id).indexOf("attempt:") === 0 ? String(id) : String(id).replace(/^[a-z_]+:/, ""));
    if (link.type === "reads") return { from: path(link.dst), to: path(link.src), type: "reads" };
    return { from: path(link.src), to: path(link.dst), type: link.type };
  }

  function noteIsMine(note, meId) {
    return meId ? note.by === meId : !note.by;
  }

  // An attempt's verdict as it moved between two recorded versions.
  function verdictMove(prev, cur) {
    const parts = [];
    const a = prev && prev.verdict_status ? prev.verdict_status.label : "";
    const b = cur && cur.verdict_status ? cur.verdict_status.label : "";
    if (!prev) parts.push("记下这条尝试，判决为「" + (b || "其它") + "」");
    else if ((prev.verdict || "") !== (cur.verdict || "")) parts.push("判决从「" + (a || "其它") + "」改为「" + (b || "其它") + "」");
    if (prev && (prev.result || "") !== (cur.result || "")) parts.push("结果改为：" + (cur.result || "（空）"));
    if (prev && !parts.length) parts.push("判决不变（" + (b || "其它") + "）");
    return parts.join("，");
  }

  // A table's first recorded version in words: 「3 行，列 month、topic」.
  function tableWords(summary) {
    if (!summary || typeof summary !== "object") return "";
    const parts = [];
    if (summary.rows != null) parts.push(summary.rows + (summary.rows_complete === false ? " 行以上" : " 行"));
    else if (summary.sheet_rows != null) parts.push(summary.sheet_rows + " 行");
    if (Array.isArray(summary.columns)) parts.push("列 " + summary.columns.join("、"));
    else if (summary.sheet_columns != null) parts.push(summary.sheet_columns + " 列");
    return parts.join("，");
  }

  // The page's timeline: versions, verdict movements, card entries, notes
  // and judgments, newest first. `history` is /api/history's body (or
  // null), `notes` /api/notes's list, `links` the page's links.
  function timeline(history, notes, links) {
    const out = [];
    const versions = (history && history.versions) || [];
    versions.forEach((v, i) => {
      const prev = versions[i + 1] || null;
      if (history.kind === "attempt") out.push({ kind: "verdict", at: v.at, v: v, words: verdictMove(prev, v) });
      else if (history.kind === "variable") out.push({ kind: "card", at: v.at, v: v });
      else out.push({ kind: "version", at: v.at, v: v, prev: prev });
    });
    (notes || []).forEach((n) => out.push({ kind: "note", at: n.at, note: n }));
    (links || []).forEach((l) => {
      const j = l.judgement;
      if (j && j.verdict && j.at && !l.human) out.push({ kind: "judgment", at: j.at, link: l, j: j });
    });
    out.sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
    return out;
  }

  // -- runtime ------------------------------------------------------------------------

  const nav = { id: null, fromView: null, trail: [], graphOpen: false, notesDraft: new Map(), shownDiffs: new Set() };
  const dc = { filter: "all", data: null };
  const res = { open: new Set(), drafts: new Map() };
  let meCache = { epoch: -1, id: null, loaded: false };

  function now() { return new Date(); }
  function when(at) { return at ? RODesk._whenWords(at, now()) || String(at) : ""; }

  function icon(type) {
    const d = ICONS[type === "ref" ? "reference" : type] || ICONS.file;
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("class", "node-icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    svg.appendChild(p);
    return svg;
  }

  function link(text, cls, onClick, title) {
    const b = mkButton(text, "node-link" + (cls ? " " + cls : ""), onClick);
    if (title) b.title = title;
    return b;
  }

  function sec(title, cls) {
    const s = mk("section", "node-sec" + (cls ? " " + cls : ""));
    s.appendChild(mk("h3", "node-sec-title", title));
    return s;
  }

  async function meId() {
    if (meCache.epoch === state.dataEpoch && meCache.loaded) return meCache.id;
    const me = await apiGet("/api/me").catch(() => null);
    meCache = { epoch: state.dataEpoch, id: me && me.me ? me.me.id : null, loaded: true };
    return meCache.id;
  }

  // -- navigation ------------------------------------------------------------------

  // Open the node page for a node id (or a project-relative path the server
  // names a node by). From another section the page remembers where it was
  // opened from; from a node page it remembers the node before.
  function openNode(id) {
    if (!id) return;
    if (typeof closePanel === "function") closePanel();
    if (currentView === "node") {
      if (nav.id && nav.id !== id) nav.trail.push(nav.id);
    } else {
      nav.fromView = currentView || "desk";
      nav.trail = [];
    }
    if (nav.id !== id) {
      nav.graphOpen = false;
      nav.shownDiffs = new Set();
    }
    nav.id = id;
    delete views.node.dataset.loaded;
    if (currentView === "node") loadNode(views.node);
    else activateView("node");
    window.scrollTo(0, 0);
  }

  function back() {
    if (nav.trail.length) {
      nav.id = nav.trail.pop();
      nav.graphOpen = false;
      loadNode(views.node);
      window.scrollTo(0, 0);
      return;
    }
    activateView(nav.fromView && nav.fromView !== "node" ? nav.fromView : "desk");
  }

  // The sidebar section the node page belongs to: where it was opened from.
  function section() {
    return nav.fromView || "desk";
  }

  // -- the node page -----------------------------------------------------------------

  async function loadNode(host, refresh) {
    const epoch = state.dataEpoch;
    const id = nav.id;
    if (!id) { activateView("desk"); return; }
    if (!refresh) host.innerHTML = '<p class="empty-note">载入中…</p>';
    let page, history, notes, me;
    try {
      page = await apiGet("/api/node?node=" + encodeURIComponent(id));
      [history, notes, me] = await Promise.all([
        apiGet("/api/history?node=" + encodeURIComponent(page.id)).catch((err) => ({ failed: err })),
        apiGet("/api/notes?node=" + encodeURIComponent(page.id)).catch((err) => ({ failed: err })),
        meId(),
      ]);
    } catch (err) {
      if (epoch !== state.dataEpoch || nav.id !== id) return;
      host.innerHTML = "";
      host.appendChild(backBar());
      const box = mk("div", "node-failed");
      renderBlockingError(box, "没能打开这个节点", err);
      host.appendChild(box);
      host.dataset.loaded = "1";
      return;
    }
    if (epoch !== state.dataEpoch || nav.id !== id || currentView !== "node") return;
    if (typeof clearProjectState === "function") clearProjectState();
    const top = window.scrollY;
    const typing = document.activeElement && host.contains(document.activeElement) && document.activeElement.classList.contains("node-note-input");
    host.innerHTML = "";
    const pageEl = mk("div", "node-page");
    pageEl.appendChild(backBar());
    renderHead(pageEl, page);
    renderTraceSection(pageEl, page);
    renderTimelineSection(pageEl, page, history, notes, me);
    renderJudgments(pageEl, page);
    renderGraphSection(pageEl, page);
    host.appendChild(pageEl);
    host.dataset.loaded = "1";
    drawAllLines(pageEl);
    if (refresh) window.scrollTo(0, top);
    if (typing) {
      const input = pageEl.querySelector(".node-note-input");
      if (input) input.focus();
    }
  }

  function backBar() {
    const bar = mk("div", "node-back");
    const prev = nav.trail.length ? "返回上一个节点" : "返回" + (SECTION_TITLES[SECTION_OF_VIEW[nav.fromView]] || "");
    bar.appendChild(link(prev, "", back));
    return bar;
  }

  function renderHead(host, page) {
    const head = mk("header", "node-head");
    const kind = mk("div", "node-kind");
    kind.append(icon(page.type), mk("span", null, page.type_label));
    head.appendChild(kind);
    head.appendChild(mk("h2", "node-name", page.name || page.id));
    if (page.path) head.appendChild(mk("div", "node-path mono", page.path));
    else if (page.file) head.appendChild(mk("div", "node-path mono", page.file));
    const marks = mk("div", "node-marks");
    const d = page.details || {};
    if (page.type === "attempt") marks.appendChild(RODesk.verdictInline(d.verdict_status));
    if (page.path && page.exists === false) marks.appendChild(mk("span", "status-label tone-clay", "文件不存在"));
    if (page.review) marks.appendChild(mk("span", "status-label tone-ochre", page.review + " 条关联待复核"));
    const own = ownMark(page);
    if (own) marks.appendChild(own);
    if (marks.childElementCount) head.appendChild(marks);
    const actions = mk("div", "node-actions");
    const status = mk("div", "notice-status");
    if (page.path && page.exists) {
      actions.appendChild(mkButton("查看内容", "btn btn-small", () => openFilePanel(page.path, page.type === "script" ? "script" : undefined)));
      actions.appendChild(mkButton("打开", "btn btn-small", () => openFile(page.path, false, status)));
      actions.appendChild(mkButton("在 Finder 中显示", "btn btn-small", () => openFile(page.path, true, status)));
    }
    if (page.type === "attempt") actions.appendChild(mkButton("编辑这一行", "btn btn-small", () => openAttemptForm("update", page.id)));
    if (page.type === "variable") actions.appendChild(mkButton("打开定义卡", "btn btn-small", () => openVariableCard(page.card || page.id.slice("variable:".length))));
    if (actions.childElementCount) head.append(actions, status);
    host.appendChild(head);
    renderDetails(host, page);
  }

  async function openFile(path, reveal, status) {
    setStatus(status, "", "处理中…");
    try {
      const r = await apiPost("/api/open", { path: path, reveal: reveal });
      setStatus(status, "ok", (reveal ? "已在 Finder 中显示：" : "已打开：") + r.opened);
    } catch (err) {
      setStatus(status, "err", reveal ? "无法在 Finder 中显示" : "无法打开", err);
    }
  }

  function detailRow(dl, label, value) {
    if (value == null || value === "") return;
    dl.append(mk("dt", null, label), typeof value === "string" ? mk("dd", null, value) : wrapDd(value));
  }

  function wrapDd(el) {
    const dd = mk("dd");
    dd.appendChild(el);
    return dd;
  }

  function renderDetails(host, page) {
    const d = page.details || {};
    const dl = mk("dl", "node-details");
    if (page.type === "attempt") {
      detailRow(dl, "日期", d.date || "");
      detailRow(dl, "途径", d.description || "");
      detailRow(dl, "变量", d.variables || "");
      detailRow(dl, "结果", d.result || "（无）");
      detailRow(dl, "判决", RODesk.verdictInline(d.verdict_status));
      if (d.source_file) {
        detailRow(dl, "出处", link(d.source_file + (d.source_line ? " 第 " + d.source_line + " 行" : ""), "mono",
          () => openFilePanel(d.source_file)));
      }
      if (d.step_files_broken && d.step_files_broken.length) detailRow(dl, "找不到的步骤脚本", d.step_files_broken.join("、"));
    } else if (page.type === "claim") {
      detailRow(dl, "原文", d.sentence || "");
      if (d.line) detailRow(dl, "位置", "第 " + d.line + " 行");
    } else if (page.type === "reference" || page.type === "ref") {
      detailRow(dl, "DOI", d.doi || "");
      detailRow(dl, "作者", Array.isArray(d.authors) ? d.authors.join("、") : (d.authors || ""));
      detailRow(dl, "年份", d.year ? String(d.year) : "");
      detailRow(dl, "刊物", d.venue || "");
    } else if (page.type === "variable") {
      if (d.in_use != null) detailRow(dl, "使用中", "第 " + d.in_use + " 版");
      if (d.card_state && d.card_state !== "ok") detailRow(dl, "状态", "定义卡的记录现在无法读取");
    }
    if (dl.childElementCount) host.appendChild(dl);
  }

  // -- 来龙去脉 (12.7) -------------------------------------------------------------------

  function renderTraceSection(host, page) {
    const s = sec("来龙去脉", "node-trace-sec");
    const t = page.trace || { columns: [], lines: [], breaks: [] };
    if (t.deeper) s.appendChild(mk("p", "node-sub", "更上游还有 " + t.deeper + " 个文件，这里只画出最近的几层。"));
    const scroller = mk("div", "trace-scroll");
    const grid = mk("div", "trace");
    t.columns.forEach((col, ci) => {
      const c = mk("div", "trace-col" + (col.key === "self" ? " is-self" : ""));
      c.dataset.col = String(ci);
      c.appendChild(mk("div", "trace-col-title", col.title));
      col.items.forEach((item) => c.appendChild(traceItem(item, ci)));
      if (col.gap) c.appendChild(gapBox(page, col.gap));
      if (!col.items.length && !col.gap) c.appendChild(mk("div", "trace-empty", "没有"));
      grid.appendChild(c);
    });
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "trace-lines");
    svg.setAttribute("aria-hidden", "true");
    grid.appendChild(svg);
    grid._lines = t.lines || [];
    // Columns share the width down to 120px each; below that the trace scrolls sideways.
    grid.style.minWidth = (t.columns.length * 120 + Math.max(0, t.columns.length - 1) * 32) + "px";
    scroller.appendChild(grid);
    s.appendChild(scroller);
    renderBelongs(s, page);
    const other = (t.breaks || []).filter((b) => ["no_generating_script", "no_attempt", "no_files", "no_backing"].indexOf(b.kind) < 0);
    other.forEach((b) => s.appendChild(mk("p", "trace-break", b.label + (b.path ? "：" + b.path : ""))));
    host.appendChild(s);
  }

  function traceItem(item, col) {
    const self = !!item.self;
    const el = mk(self ? "div" : "button", "trace-item t-" + item.type + (self ? " self" : "") +
      (item.exists === false ? " missing" : ""));
    if (!self) {
      el.type = "button";
      el.addEventListener("click", () => openNode(item.id));
      el.title = item.path || item.file || item.label;
    }
    el.dataset.node = item.id;
    el.dataset.col = String(col);
    const top = mk("span", "trace-item-head");
    top.append(icon(item.type), mk("span", "trace-item-name", item.label));
    el.appendChild(top);
    const where = item.path ? posixDir(item.path) : (item.file || "");
    if (where) el.appendChild(mk("span", "trace-item-dir mono", where));
    const flags = [];
    if (item.exists === false) flags.push(["文件不存在", "clay"]);
    if (item.unreadable) flags.push(["暂时无法读取", "clay"]);
    if (item.raw && item.type === "dataset") flags.push(["原始数据", "plain"]);
    if (item.member) flags.push([VIA_WORDS[item.member] || "", "plain"]);
    if (item.note) flags.push([item.note, "plain"]);
    if (flags.length) {
      const f = mk("span", "trace-item-flags");
      flags.forEach(([text, tone]) => f.appendChild(mk("span", "status-label tone-" + tone, text)));
      el.appendChild(f);
    }
    const own = ownMark(item);
    if (own) el.appendChild(own);
    return el;
  }

  function posixDir(path) {
    const i = String(path).lastIndexOf("/");
    return i > 0 ? String(path).slice(0, i) : "";
  }

  // A break, drawn where the missing piece would be, with its repair.
  function gapBox(page, gap) {
    const box = mk("div", "trace-gap");
    box.appendChild(mk("div", "trace-gap-label", gap.label));
    if (gap.repair) box.appendChild(repairControl(page, gap.repair.type));
    return box;
  }

  // 「补上来源」 (generates / writes from a chosen script) or 「关联文件」
  // (belongs_to: this file to a chosen attempt, or a chosen file to this
  // attempt). Choose, see the sentence it will record, then confirm.
  function repairControl(page, type) {
    const wrap = mk("div", "repair");
    const blocked = humanRecordsBlocked();
    const choices = page.choices || {};
    let options = [];
    let label = "";
    if (type === "generates" || type === "writes") {
      label = "补上来源";
      options = (choices.scripts || []).map((s) => ({ value: s.path, text: s.path }));
    } else if (page.type === "attempt") {
      label = "关联文件";
      options = (choices.files || []).map((f) => ({ value: f.path, text: f.type_label + "  " + f.path }));
    } else {
      label = "关联文件";
      options = (choices.attempts || []).map((a) => ({
        value: a.id,
        text: (a.verdict_status && a.verdict_status.label ? a.verdict_status.label + " · " : "") + "#" +
          String(a.number || "").replace(/^#/, "") + " " + (a.title || ""),
      }));
    }
    // An open chooser widens its column (the lines follow).
    const widen = (on) => {
      const col = wrap.closest(".trace-col");
      if (!col) return;
      col.classList.toggle("repairing", on);
      drawAllLines(col.closest(".node-page") || col);
    };
    const trigger = mkButton(label, "btn btn-small", () => {
      trigger.classList.add("hidden");
      form.classList.remove("hidden");
      widen(true);
      select.focus();
    });
    if (blocked) { trigger.disabled = true; trigger.title = blocked; }
    const form = mk("div", "repair-form hidden");
    const select = mk("select", "form-input repair-select");
    select.setAttribute("aria-label", type === "generates" || type === "writes" ? "生成它的脚本" : (page.type === "attempt" ? "要关联的文件" : "它属于的尝试"));
    const first = mk("option", null, type === "generates" || type === "writes" ? "选择生成它的脚本" : (page.type === "attempt" ? "选择要关联的文件" : "选择它属于的尝试"));
    first.value = "";
    select.appendChild(first);
    options.forEach((o) => {
      const opt = mk("option", null, o.text);
      opt.value = o.value;
      select.appendChild(opt);
    });
    const sentence = mk("p", "repair-sentence");
    const note = mk("input", "form-input repair-note");
    note.type = "text";
    note.placeholder = "备注（可不填）";
    const status = mk("div", "notice-status");
    const go = mkButton("确认", "btn btn-small btn-primary", () => writeRepair(page, type, select.value, note.value, status, [go, cancel]));
    const cancel = mkButton("取消", "btn btn-small", () => {
      form.classList.add("hidden");
      trigger.classList.remove("hidden");
      widen(false);
      select.value = "";
      sentence.textContent = "";
      go.disabled = true;
    });
    go.disabled = true;
    select.addEventListener("change", () => {
      const chosen = options.find((o) => o.value === select.value);
      go.disabled = !chosen;
      sentence.textContent = chosen ? repairSentence(page, type, chosen) : "";
    });
    const row = mk("div", "repair-actions");
    row.append(go, cancel);
    form.append(select, sentence, note, row, status);
    if (!options.length) {
      form.innerHTML = "";
      form.appendChild(mk("p", "node-sub", type === "generates" || type === "writes" ? "项目里还没有脚本可选。" : "没有可以选择的项目。"));
    }
    wrap.append(trigger, form);
    return wrap;
  }

  function repairSentence(page, type, chosen) {
    const me = page.path || page.name;
    if (type === "generates") return "将记下：" + chosen.value + " 生成 " + me;
    if (type === "writes") return "将记下：" + chosen.value + " 写出 " + me;
    if (page.type === "attempt") return "将记下：" + chosen.value + " 属于 " + page.name;
    return "将记下：" + me + " 属于 " + chosen.text.replace(/^[^·]*·\s*/, "");
  }

  async function writeRepair(page, type, value, note, status, buttons) {
    if (!value) return;
    let body;
    if (type === "generates" || type === "writes") body = { from: value, to: page.path, type: type };
    else if (page.type === "attempt") body = { from: value, to: page.id, type: "belongs_to" };
    else body = { from: page.path, to: value, type: "belongs_to" };
    if (note && note.trim()) body.note = note.trim();
    buttons.forEach((b) => { b.disabled = true; });
    setStatus(status, "", "正在记下…");
    let r;
    try {
      r = await apiPost("/api/mappings/add", body);
    } catch (err) {
      buttons.forEach((b) => { b.disabled = false; });
      setStatus(status, "err", type === "belongs_to" ? "没能关联文件" : "没能补上来源", err);
      return;
    }
    if (r.ingest_error) {
      setStatus(status, "err", "已经写进 mappings.toml，但图谱还没有更新；稍后会自动重试", r.ingest_error);
      return;
    }
    setStatus(status, "ok", "已记下。");
    loadNode(views.node, true);
  }

  // The attempt(s) it belongs to and the variable versions it uses, as a
  // line under the trace; no attempt is a gap with 「关联文件」.
  function renderBelongs(host, page) {
    const t = page.trace || {};
    if (page.type === "attempt") {
      // 12.7: 「关联文件」 attaches more scripts, data and figures to it.
      if (!(t.breaks || []).some((b) => b.kind === "no_files")) {
        const more = mk("div", "trace-belongs");
        more.appendChild(mk("span", "trace-belongs-word", "还有文件属于这条尝试？"));
        more.appendChild(repairControl(page, "belongs_to"));
        host.appendChild(more);
      }
      return;
    }
    if (page.type === "variable") return;
    const attempts = t.attempts || [];
    const brk = (t.breaks || []).find((b) => b.kind === "no_attempt");
    const line = mk("div", "trace-belongs");
    if (attempts.length) {
      line.appendChild(mk("span", "trace-belongs-word", "属于"));
      attempts.forEach((a) => {
        const one = mk("span", "trace-attempt");
        one.appendChild(RODesk.statusLabel(a.verdict_status));
        one.appendChild(link("#" + String(a.number || "").replace(/^#/, "") + " " + (a.title || ""), "", () => openNode(a.id)));
        const via = (a.via || []).map((v) => VIA_WORDS[v.via] || v.via).filter((x, i, all) => all.indexOf(x) === i);
        if (via.length) one.appendChild(mk("span", "node-sub-inline", "（" + via.join("、") + "）"));
        line.appendChild(one);
      });
      host.appendChild(line);
    } else if (brk && (page.type === "script" || page.type === "dataset" || page.type === "figure")) {
      const gap = mk("div", "trace-gap wide");
      gap.appendChild(mk("div", "trace-gap-label", brk.label));
      gap.appendChild(repairControl(page, "belongs_to"));
      host.appendChild(gap);
    }
    const vars = t.variables || [];
    if (vars.length) {
      const v = mk("div", "trace-belongs");
      v.appendChild(mk("span", "trace-belongs-word", "使用变量"));
      vars.forEach((x) => {
        const one = mk("span", "trace-attempt");
        const name = link(x.variable + " 第 " + x.version + " 版", "", () => openNode("variable:" + x.variable));
        if (x.reference && x.reference.label) name.title = x.reference.label;
        one.appendChild(name);
        one.appendChild(mk("span", "node-sub-inline", x.matched_by === "output" ? "（定义卡写明输出到这里）" : "（按列名 " + (x.field || "") + " 对上）"));
        v.appendChild(one);
      });
      host.appendChild(v);
    }
  }

  // The lines between columns, drawn after layout (and again when the
  // window changes size). A line joins the nearest pair of its ends that
  // sit in an earlier and a later column.
  function drawLines(grid) {
    const svg = grid.querySelector(".trace-lines");
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const box = grid.getBoundingClientRect();
    svg.setAttribute("width", String(grid.scrollWidth));
    svg.setAttribute("height", String(grid.scrollHeight));
    const els = new Map();
    grid.querySelectorAll(".trace-item").forEach((el) => {
      const list = els.get(el.dataset.node) || [];
      list.push(el);
      els.set(el.dataset.node, list);
    });
    (grid._lines || []).forEach((l) => {
      let best = null;
      (els.get(l.from) || []).forEach((a) => (els.get(l.to) || []).forEach((b) => {
        const d = Number(b.dataset.col) - Number(a.dataset.col);
        if (d > 0 && (!best || d < best.d)) best = { a: a, b: b, d: d };
      }));
      if (!best) return;
      const ra = best.a.getBoundingClientRect();
      const rb = best.b.getBoundingClientRect();
      const x1 = ra.right - box.left, y1 = ra.top + ra.height / 2 - box.top;
      const x2 = rb.left - box.left, y2 = rb.top + rb.height / 2 - box.top;
      const mid = (x1 + x2) / 2;
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", "M" + x1 + " " + y1 + " C" + mid + " " + y1 + " " + mid + " " + y2 + " " + x2 + " " + y2);
      path.setAttribute("class", "trace-line" + (l.human ? " human" : "") + (l.status === "pending" ? " pending" : "") +
        (l.review ? " review" : ""));
      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = l.relation + (l.human ? "（人工标注）" : "");
      path.appendChild(title);
      svg.appendChild(path);
      if (l.human) {
        const dot = document.createElementNS(SVG_NS, "circle");
        dot.setAttribute("cx", String(mid));
        dot.setAttribute("cy", String((y1 + y2) / 2));
        dot.setAttribute("r", "3");
        dot.setAttribute("class", "trace-dot");
        svg.appendChild(dot);
      }
    });
  }

  function drawAllLines(root) {
    const run = () => root.querySelectorAll(".trace").forEach(drawLines);
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
    else run();
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (currentView === "node" && views.node) drawAllLines(views.node);
    }, 120);
  });

  // -- 改动与备注 (12.6) ---------------------------------------------------------------

  function renderTimelineSection(host, page, history, notes, me) {
    const s = sec("改动与备注");
    const hist = history && !history.failed ? history : null;
    const noteList = notes && !notes.failed ? (notes.notes || []) : [];
    if (history && history.failed) {
      const box = mk("p", "trace-break");
      renderBlockingError(box, "这个节点的历史现在读不到", history.failed);
      s.appendChild(box);
    } else if (hist && hist.reason && !(hist.versions || []).length) {
      s.appendChild(mk("p", "node-sub", hist.reason));
    }
    if (hist && (page.type === "section" || page.type === "claim") && hist.path) {
      s.appendChild(mk("p", "node-sub", "下面的版本是它所在的文稿 " + hist.path + " 的版本。"));
    }
    if (notes && notes.failed) {
      const box = mk("p", "trace-break");
      renderBlockingError(box, "这个节点的笔记现在读不到", notes.failed);
      s.appendChild(box);
    } else if (notes && notes.stale) {
      s.appendChild(mk("p", "trace-break", (notes.message || "笔记记录文件现在无法读取") + "；下面是图谱上次读到的笔记。"));
    }
    const entries = timeline(hist, noteList, page.links);
    if (!entries.length) s.appendChild(mk("p", "node-empty", "还没有记录到改动或笔记。"));
    else {
      const ul = mk("ul", "node-timeline");
      let diffs = 0;
      entries.forEach((e) => {
        const li = mk("li", "tl-row kind-" + e.kind);
        li.appendChild(mk("span", "tl-when", when(e.at)));
        const main = mk("div", "tl-main");
        if (e.kind === "version") {
          renderVersion(main, page, hist, e, diffs < DIFFS_AUTO);
          if (e.prev) diffs += 1;
        } else if (e.kind === "verdict") {
          main.appendChild(mk("div", "tl-what", e.words));
          main.appendChild(mk("div", "tl-meta", [e.v.who_label, SOURCE_WORDS[e.v.source] || ""].filter(Boolean).join(" · ")));
          if (e.v.approved_by) main.appendChild(mk("div", "tl-meta", "经 " + e.v.approved_by + " 批准"));
          if (e.v.note) main.appendChild(mk("p", "tl-note", e.v.note));
        } else if (e.kind === "card") {
          const act = (typeof ACT_WORDS !== "undefined" && ACT_WORDS[e.v.act]) || e.v.act || "记录";
          main.appendChild(mk("div", "tl-what", act + (e.v.number ? "第 " + e.v.number + " 版" : "")));
          main.appendChild(mk("div", "tl-meta", e.v.who_label || ""));
          if (e.v.note) main.appendChild(mk("p", "tl-note", e.v.note));
        } else if (e.kind === "note") {
          renderNote(main, page, e.note, me);
        } else if (e.kind === "judgment") {
          main.appendChild(mk("div", "tl-what", (JUDGE_WORDS[e.j.verdict] || "判断了") + "这条关联：" + linkSentence(e.link)));
          if (e.j.at_label) main.appendChild(mk("div", "tl-meta", e.j.at_label));
          if (e.j.note) main.appendChild(mk("p", "tl-note", e.j.note));
        }
        li.appendChild(main);
        ul.appendChild(li);
      });
      s.appendChild(ul);
    }
    s.appendChild(noteForm(page, notes));
    host.appendChild(s);
  }

  function renderVersion(host, page, hist, e, auto) {
    const v = e.v;
    const head = mk("div", "tl-what");
    if (!e.prev) head.textContent = v.version === "worktree" ? "工作区里还没有提交的改动" : "最早记下的版本";
    else head.textContent = v.version === "worktree" ? "工作区里还没有提交的改动" : (v.kind === "data" ? "数据有改动" : "内容有改动");
    host.appendChild(head);
    const meta = [v.who_label, SOURCE_WORDS[v.source] || ""];
    if (v.approved_by) meta.push("经 " + v.approved_by + " 批准");
    host.appendChild(mk("div", "tl-meta", meta.filter(Boolean).join(" · ")));
    if (v.note && v.version !== "worktree") host.appendChild(mk("p", "tl-note", v.note));
    if (!e.prev) {
      const words = tableWords(v.summary);
      if (words) host.appendChild(mk("p", "tl-summary", words));
      return;
    }
    const slot = mk("div", "tl-diff");
    host.appendChild(slot);
    const key = String(e.prev.version) + ".." + String(v.version);
    const load = () => loadDiff(slot, page, hist, e.prev, v);
    if (auto || nav.shownDiffs.has(key)) load();
    else slot.appendChild(mkButton("查看这一版的改动", "btn btn-small", () => { nav.shownDiffs.add(key); load(); }));
  }

  async function loadDiff(slot, page, hist, a, b) {
    slot.innerHTML = "";
    slot.appendChild(mk("span", "node-sub", "载入中…"));
    let d;
    try {
      d = await apiGet("/api/history/diff?node=" + encodeURIComponent(hist.node || page.id) + "&a=" +
        encodeURIComponent(a.version) + "&b=" + encodeURIComponent(b.version));
    } catch (err) {
      slot.innerHTML = "";
      const box = mk("p", "trace-break");
      renderBlockingError(box, "没能比较这两个版本", err);
      slot.appendChild(box);
      return;
    }
    slot.innerHTML = "";
    const words = d.summary_words || "";
    const isData = b.kind === "data" || a.kind === "data";
    if (words) slot.appendChild(mk("p", "tl-summary", words));
    if (d.diff) {
      const rows = diffRows(d.diff);
      if (isData) {
        const holder = mk("div");
        const toggle = mkButton("逐行对比", "btn btn-small", () => {
          toggle.remove();
          holder.appendChild(diffBlock(rows, d.truncated));
        });
        holder.appendChild(toggle);
        slot.appendChild(holder);
      } else slot.appendChild(diffBlock(rows, d.truncated));
    } else if (!words && d.same) {
      slot.appendChild(mk("p", "tl-summary", "内容相同"));
    }
  }

  function diffBlock(rows, truncated) {
    const wrap = mk("div", "diff");
    const render = (all) => {
      wrap.innerHTML = "";
      const shown = all ? rows : rows.slice(0, DIFF_SHOWN);
      shown.forEach((r) => wrap.appendChild(mk("div", "dl-row " + r.cls, r.text || " ")));
      if (rows.length > DIFF_SHOWN) {
        wrap.appendChild(mkButton(all ? "收起" : "显示其余 " + (rows.length - DIFF_SHOWN) + " 行", "btn btn-small diff-more",
          () => render(!all)));
      }
      if (truncated) wrap.appendChild(mk("p", "node-sub", "改动太长，只比较了开头的一部分。"));
    };
    render(false);
    return wrap;
  }

  function renderNote(host, page, note, me) {
    const head = mk("div", "tl-what", "笔记");
    host.appendChild(head);
    host.appendChild(mk("div", "tl-meta", note.label || note.name || note.by || "最初的研究者"));
    const text = mk("p", "tl-note" + (note.withdrawn ? " withdrawn" : ""), note.text || "");
    host.appendChild(text);
    if (note.withdrawn) {
      host.appendChild(mk("div", "tl-meta", "已撤回" + (note.withdrawn_at ? "（" + when(note.withdrawn_at) + "）" : "")));
      return;
    }
    if (noteIsMine(note, me)) {
      const status = mk("div", "notice-status");
      const b = mkButton("撤回", "btn btn-small", async () => {
        b.disabled = true;
        setStatus(status, "", "正在撤回…");
        try {
          await apiPost("/api/notes", { action: "withdraw", id: note.id });
        } catch (err) {
          b.disabled = false;
          setStatus(status, "err", "没能撤回这条笔记", err);
          return;
        }
        loadNode(views.node, true);
      });
      const blocked = humanRecordsBlocked();
      if (blocked) { b.disabled = true; b.title = blocked; }
      const row = mk("div", "tl-actions");
      row.appendChild(b);
      host.append(row, status);
    }
  }

  function noteForm(page, notes) {
    const form = mk("div", "node-note");
    const input = mk("textarea", "form-input node-note-input");
    input.rows = 3;
    input.placeholder = "写一条笔记：为什么这样做、要注意什么";
    input.setAttribute("aria-label", "笔记");
    input.value = nav.notesDraft.get(page.id) || "";
    input.addEventListener("input", () => nav.notesDraft.set(page.id, input.value));
    const status = mk("div", "notice-status");
    const add = mkButton("添加笔记", "btn btn-small", async () => {
      const text = input.value.trim();
      if (!text) { setStatus(status, "err", "笔记是空的"); return; }
      add.disabled = true;
      setStatus(status, "", "正在保存…");
      try {
        await apiPost("/api/notes", { action: "add", node: page.id, text: text });
      } catch (err) {
        add.disabled = false;
        setStatus(status, "err", "笔记没有保存", err);
        return;
      }
      nav.notesDraft.delete(page.id);
      loadNode(views.node, true);
    });
    const blocked = humanRecordsBlocked() || (notes && notes.trusted === false ? (notes.message || "笔记记录文件现在无法写入") : null);
    if (blocked) { add.disabled = true; add.title = blocked; }
    const row = mk("div", "tl-actions");
    row.appendChild(add);
    form.append(input, row, status);
    return form;
  }

  // -- 判断 ----------------------------------------------------------------------------

  function renderJudgments(host, page) {
    const s = sec("判断");
    const links = page.links || [];
    if (!links.length) {
      s.appendChild(mk("p", "node-empty", "没有关联到这个节点的连线。"));
      host.appendChild(s);
      return;
    }
    s.appendChild(mk("p", "node-sub", "机器提取的关联由你确认或否决；人工标注的关联来自 mappings.toml。"));
    const ul = mk("ul", "node-links");
    const render = (all) => {
      ul.innerHTML = "";
      (all ? links : links.slice(0, LINKS_SHOWN)).forEach((l) => ul.appendChild(linkRow(page, l)));
      if (links.length > LINKS_SHOWN) {
        const li = mk("li", "node-links-more");
        li.appendChild(mkButton(all ? "收起" : "显示全部 " + links.length + " 条", "btn btn-small", () => render(!all)));
        ul.appendChild(li);
      }
    };
    render(false);
    s.appendChild(ul);
    host.appendChild(s);
  }

  function linkRow(page, l) {
    const li = mk("li", "link-row" + (l.status === "rejected" ? " rejected" : ""));
    const head = mk("div", "link-head");
    const other = l.other || {};
    const srcEl = l.src === page.id ? mk("span", "link-self", l.src_label) : link(l.src_label, "", () => openNode(l.src));
    const dstEl = l.dst === page.id ? mk("span", "link-self", l.dst_label) : link(l.dst_label, "", () => openNode(l.dst));
    head.append(srcEl, mk("span", "link-rel", l.relation), dstEl);
    const tone = l.human ? "clay" : (l.review || l.conflict) ? "ochre" : l.status === "confirmed" ? "olive" : l.status === "rejected" ? "gray" : "plain";
    head.appendChild(mk("span", "status-label tone-" + tone, linkStateWords(l)));
    const own = ownMark(l);
    if (own) head.appendChild(own);
    li.appendChild(head);
    const where = (l.evidence || []).map((e) => (e.file && e.file !== l.other.path && e.file !== (page.path || "") ? e.file + " " : "") +
      "第 " + e.line + " 行" + (e.callee ? "（" + e.callee + "）" : ""));
    if (where.length) li.appendChild(mk("div", "tl-meta mono", where.join("；")));
    if (l.judgement && (l.review || l.conflict) && l.judgement.label) li.appendChild(mk("div", "tl-meta", l.judgement.label));
    if (l.judgement && l.judgement.note) li.appendChild(mk("p", "tl-note", l.judgement.note));
    if (other.path && other.type !== page.type) li.title = other.path;
    const actions = mk("div", "link-actions");
    const status = mk("div", "notice-status");
    const blocked = humanRecordsBlocked();
    const buttons = [];
    judgeActions(l).forEach((a) => {
      const text = a === "delete" ? "删除标注" : a === "confirmed" ? "确认" : a === "rejected" ? "否决" : "撤回";
      const b = mkButton(text, "btn btn-small", () => judge(l, a, status, buttons));
      if (blocked) { b.disabled = true; b.title = blocked; }
      buttons.push(b);
      actions.appendChild(b);
    });
    li.append(actions, status);
    return li;
  }

  async function judge(l, verdict, status, buttons) {
    if (verdict === "delete" && !window.confirm("删除这条人工标注？\n\n" + linkSentence(l) + "\n\n它会从 .researchos/mappings.toml 里移除。")) return;
    buttons.forEach((b) => { b.disabled = true; });
    setStatus(status, "", "正在记下…");
    try {
      if (verdict === "delete") {
        const r = await apiPost("/api/mappings/delete", mappingEnds(l));
        if (r.ingest_error) {
          setStatus(status, "err", "已经从 mappings.toml 删除，但图谱还没有更新；稍后会自动重试", r.ingest_error);
          return;
        }
      } else {
        await apiPost("/api/judgements", { src: l.src, dst: l.dst, type: l.type, extractor: l.extractor, verdict: verdict });
      }
    } catch (err) {
      buttons.forEach((b) => { b.disabled = false; });
      setStatus(status, "err", verdict === "delete" ? "没能删除这条标注" : "没能记下这个判断", err);
      return;
    }
    loadNode(views.node, true);
  }

  // -- 关系图 (the canvas, scoped to this node's neighbourhood) ----------------------------

  function renderGraphSection(host, page) {
    const s = sec("关系图");
    const canvasable = ["script", "dataset", "figure"].indexOf(page.type) >= 0 && page.in_index;
    if (!canvasable) {
      s.appendChild(mk("p", "node-empty", "关系图只画脚本、数据和图表；这个节点的关联见上面的来龙去脉。"));
      host.appendChild(s);
      return;
    }
    const holder = mk("div", "node-graph" + (nav.graphOpen ? "" : " hidden"));
    const toggle = mkButton(nav.graphOpen ? "收起关系图" : "打开关系图", "btn btn-small", () => {
      nav.graphOpen = !nav.graphOpen;
      toggle.textContent = nav.graphOpen ? "收起关系图" : "打开关系图";
      holder.classList.toggle("hidden", !nav.graphOpen);
      if (nav.graphOpen) RCECanvas.load(holder, "near:" + page.id);
    });
    s.appendChild(toggle);
    s.appendChild(holder);
    host.appendChild(s);
    if (nav.graphOpen) RCECanvas.load(holder, "near:" + page.id);
  }

  // -- 成果 (12.9) ---------------------------------------------------------------------

  function stateLabel(trace) {
    const t = trace || {};
    const el = mk("span", "status-label tone-" + traceTone(t.state), t.label || "");
    if (t.breaks && t.breaks.length) el.title = t.breaks.join("；");
    return el;
  }

  function resultRow(item) {
    const li = mk("li", "res-row");
    li.appendChild(mk("span", "res-kind", item.kind || item.type_label || ""));
    const main = mk("div", "res-main");
    const name = link(item.label, "", () => openNode(item.id), "打开它的来龙去脉");
    main.appendChild(name);
    if (item.path) main.appendChild(mk("span", "res-path mono", item.path));
    if (item.exists === false) main.appendChild(mk("span", "status-label tone-clay", "文件不存在"));
    const own = ownMark(item);
    if (own) main.appendChild(own);
    li.append(main, stateLabel(item.trace));
    return li;
  }

  async function loadResults(host, refresh) {
    const epoch = state.dataEpoch;
    if (!refresh) host.innerHTML = '<p class="empty-note">载入中…</p>';
    let data;
    try {
      data = await apiGet("/api/results");
    } catch (err) {
      if (epoch !== state.dataEpoch) return;
      renderViewFailure(host, err);
      return;
    }
    if (epoch !== state.dataEpoch) return;
    if (typeof clearProjectState === "function") clearProjectState();
    const top = window.scrollY;
    host.innerHTML = "";
    const page = mk("div", "results");
    page.appendChild(mk("p", "node-sub", "文稿里的图、表和论断。每一项都能打开它的来龙去脉：完整，或者缺来源、缺尝试。"));
    const figs = mk("section", "res-block");
    figs.appendChild(mk("h2", "desk-title", "图与表"));
    if (!data.sections.length && !data.loose.length) figs.appendChild(mk("p", "node-empty", "文稿里还没有引用图或表。"));
    data.sections.forEach((g) => {
      const group = mk("div", "res-group");
      const title = mk("div", "res-group-title");
      title.append(mk("span", "res-group-file mono", g.file || ""), link(g.title || "", "", () => openNode(g.section)));
      group.appendChild(title);
      const ul = mk("ul", "res-list");
      g.items.forEach((item) => ul.appendChild(resultRow(item)));
      group.appendChild(ul);
      figs.appendChild(group);
    });
    if (data.loose.length) {
      const group = mk("div", "res-group");
      group.appendChild(mk("div", "res-group-title", "没有被文稿引用的图"));
      const ul = mk("ul", "res-list");
      data.loose.forEach((item) => ul.appendChild(resultRow(item)));
      group.appendChild(ul);
      figs.appendChild(group);
    }
    page.appendChild(figs);
    const claims = mk("section", "res-block");
    claims.appendChild(mk("h2", "desk-title", "论断"));
    if (!data.claims.length) claims.appendChild(mk("p", "node-empty", "文稿里还没有找到带数字的论断。"));
    else claims.appendChild(mk("p", "node-sub", "按文稿列出；展开一份文稿才载入它的论断。"));
    data.claims.forEach((c) => claims.appendChild(draftClaims(c)));
    page.appendChild(claims);
    host.appendChild(page);
    host.dataset.loaded = "1";
    if (refresh) window.scrollTo(0, top);
  }

  function draftClaims(c) {
    const box = mk("details", "res-draft");
    const summary = mk("summary", "res-draft-summary");
    summary.append(mk("span", "mono", c.file), mk("span", "node-sub-inline", c.count + " 条"));
    box.appendChild(summary);
    const body = mk("div", "res-draft-body");
    box.appendChild(body);
    const fill = async () => {
      body.innerHTML = '<p class="empty-note">载入中…</p>';
      let d;
      try {
        d = await apiGet("/api/results?draft=" + encodeURIComponent(c.file));
      } catch (err) {
        body.innerHTML = "";
        const p = mk("p", "trace-break");
        renderBlockingError(p, "没能载入这份文稿的论断", err);
        body.appendChild(p);
        return;
      }
      body.innerHTML = "";
      d.sections.forEach((g) => {
        body.appendChild(mk("div", "res-group-title", g.title || ""));
        const ul = mk("ul", "res-list");
        g.claims.forEach((cl) => {
          const li = mk("li", "res-row");
          const main = mk("div", "res-main");
          main.appendChild(link(cl.sentence || cl.id, "claim", () => openNode(cl.id), "打开它的来龙去脉"));
          const where = [cl.number ? "数字 " + cl.number : "", cl.line ? "第 " + cl.line + " 行" : ""].filter(Boolean).join(" · ");
          if (where) main.appendChild(mk("span", "node-sub-inline", where));
          li.append(main, stateLabel(cl.trace));
          ul.appendChild(li);
        });
        body.appendChild(ul);
      });
      if (d.total > d.shown) body.appendChild(mk("p", "node-sub", "这份文稿共有 " + d.total + " 条，只列出了前 " + d.shown + " 条。"));
    };
    if (res.open.has(c.file)) { box.open = true; fill(); }
    box.addEventListener("toggle", () => {
      if (box.open) { res.open.add(c.file); if (!body.childElementCount) fill(); } else res.open.delete(c.file);
    });
    return box;
  }

  // -- 数据与代码 (12.9) ------------------------------------------------------------------

  function inFilter(f, filter) {
    if (filter === "orphans") return !!f.orphan_input;
    if (filter === "chains") return f.type === "dataset" && (f.written_by || []).length > 0 && (f.read_by || []).length > 0;
    if (filter === "broken") return !!(f.broken && f.broken.length) || f.exists === false;
    if (filter === "duplicates") return !!(f.other_copies && f.other_copies.length);
    return true;
  }

  function fileRelations(f) {
    const lines = [];
    const few = (list) => list.slice(0, 3).map((p) => p.split("/").pop()).join("、") + (list.length > 3 ? " 等 " + list.length + " 个" : "");
    if (f.type === "script") {
      if (f.reads && f.reads.length) lines.push("读取 " + few(f.reads));
      if (f.writes && f.writes.length) lines.push("写出 " + few(f.writes));
      if (!lines.length) lines.push("没有读到读写记录。");
    } else {
      if (f.written_by && f.written_by.length) lines.push("由这些脚本写出：" + few(f.written_by));
      if (f.read_by && f.read_by.length) lines.push("被这些脚本读取：" + few(f.read_by));
      if (!lines.length) lines.push("没有脚本读取或写出它");
    }
    return lines;
  }

  function fileRow(f) {
    const li = mk("li", "dc-row");
    const kind = mk("span", "dc-kind");
    kind.append(icon(f.type), mk("span", null, f.type_label));
    const main = mk("div", "dc-main");
    const head = mk("div", "dc-head");
    head.appendChild(link(f.path, "mono", () => openNode(f.id), "打开它的来龙去脉"));
    if (f.exists === false) head.appendChild(mk("span", "status-label tone-clay", "文件不存在"));
    if (f.unreadable) head.appendChild(mk("span", "status-label tone-clay", "暂时无法读取"));
    if (f.orphan_input) head.appendChild(mk("span", "status-label tone-clay", "无来源输入"));
    if (f.review) {
      const tag = mkButton(f.review + " 条关联待复核", "review-tag", () => openReviewPanel(null));
      tag.title = "打开待复核的判断";
      head.appendChild(tag);
    }
    const own = ownMark(f);
    if (own) head.appendChild(own);
    main.appendChild(head);
    fileRelations(f).forEach((t) => main.appendChild(mk("div", "dc-rel", t)));
    (f.broken || []).forEach((b) => {
      main.appendChild(mk("div", "dc-rel bad", "断链：" + (b.script || "") + " " + b.relation + " " + (b.target || "") +
        (b.relation === "写出" ? "（写出，文件不存在）" : "（读取，文件不存在）")));
    });
    if (f.other_copies && f.other_copies.length) main.appendChild(mk("div", "dc-rel", "其它拷贝：" + f.other_copies.join("、")));
    li.append(kind, main);
    return li;
  }

  async function loadDataCode(host, refresh) {
    const epoch = state.dataEpoch;
    if (!refresh) host.innerHTML = '<p class="empty-note">载入中…</p>';
    let data;
    try {
      data = await apiGet("/api/data-code");
    } catch (err) {
      if (epoch !== state.dataEpoch) return;
      renderViewFailure(host, err);
      return;
    }
    if (epoch !== state.dataEpoch) return;
    if (typeof clearProjectState === "function") clearProjectState();
    dc.data = data;
    const top = window.scrollY;
    host.innerHTML = "";
    const bar = mk("div", "dc-filters");
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "筛选");
    const counts = Object.assign({}, data.counts || {});
    counts.chains = (data.files || []).filter((f) => inFilter(f, "chains")).length;
    const list = mk("ul", "dc-list");
    const draw = () => {
      list.innerHTML = "";
      const shown = (data.files || []).filter((f) => inFilter(f, dc.filter));
      if (!shown.length) list.appendChild(mk("li", "node-empty", dc.filter === "all" ? "还没有扫描到脚本或数据文件。" : "没有。"));
      shown.forEach((f) => list.appendChild(fileRow(f)));
      bar.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === dc.filter)));
    };
    FILTERS.forEach(([key, text]) => {
      const n = key === "all" ? counts.all : counts[key];
      const b = mkButton(text + " " + (n || 0), "dc-filter", () => { dc.filter = key; draw(); });
      b.dataset.filter = key;
      bar.appendChild(b);
    });
    host.append(bar, list);
    draw();
    host.dataset.loaded = "1";
    if (refresh) window.scrollTo(0, top);
  }

  window.RONode = Object.freeze({
    openNode, back, section, loadNode, loadResults, loadDataCode, current: () => nav.id,
    // pure, for tests/test_v8_u3.py
    _diffRows: diffRows, _traceTone: traceTone, _linkSentence: linkSentence, _linkStateWords: linkStateWords,
    _judgeActions: judgeActions, _mappingEnds: mappingEnds, _noteIsMine: noteIsMine, _verdictMove: verdictMove,
    _tableWords: tableWords, _timeline: timeline, _inFilter: inFilter, _fileRelations: fileRelations,
  });
})();
