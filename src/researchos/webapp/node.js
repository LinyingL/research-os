// The node page, 图表与论断 and 数据与代码 -- DESIGN.md 12.6, 12.7, 12.9, 12.11;
// task V8 phase U3. Served verbatim at /node.js by the same process and
// loaded by app.html with a plain same-origin <script src>, exactly as
// /desk.js is: zero build, zero external resources, same origin check.
//
// It defines one global, window.RONode, and calls app.html's helpers (mk,
// mkButton, apiGet, apiPost, setStatus, renderBlockingError,
// renderViewFailure, clearProjectState, ownMark, openFilePanel,
// openAttemptForm, openVariableCard, activateView, humanRecordsBlocked,
// refreshCurrentView, lastGeneration,
// ACT_WORDS, state, views, currentView, RODesk, RCECanvas, ROTeam -- whether
// this person edits or proposes, 「当时的依据」, U4) only at run time, never
// while loading.
//
// Every object opens the same page: what it is, its trace (来龙去脉: the
// columns 数据 | 脚本 | 中间数据 | 脚本 | 本节点 | 被引用 joined by drawn
// lines, every break a gap with its repair), its changes and notes as one
// timeline (改动与备注), the machine links that touch it (连线确认), and the
// canvas scoped to its neighbourhood behind 「打开关系图」 (关系图).
//
// What it reads (all GET, all origin-checked by the server):
//   /api/node?node=        -- the page (researchos.nodepage.node);
//   /api/history?node=     -- its versions, newest first;
//   /api/history/diff?node=&a=&b= -- what changed between two;
//   /api/notes?node=       -- its notes;
//   /api/me                -- who I am (my own notes can be withdrawn);
//   /api/results[?draft=]  -- 图表与论断; /api/data-code -- 数据与代码.
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

  // The words for the screen (DESIGN.md 13.6): /i18n.js's table, looked up
  // at call time; without it (this file evaluated alone) the Chinese. This covers
  // the node page, Figures and claims and Data & code, including the trace the desk's
  // side panel draws. Only interface words are translated; research text stays as written.
  function fillVars(s, v) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return v && Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }); }
  function t(s, v) { var I = typeof window !== "undefined" && window.ROI18N; return I ? I.t(s, v) : fillVars(s, v); }

  const SVG_NS = "http://www.w3.org/2000/svg";
  const DIFF_SHOWN = 20;
  const DIFFS_AUTO = 5;
  const LINKS_SHOWN = 40;
  const TRACE_TONES = { complete: "olive", no_source: "clay", no_attempt: "ochre" };
  // Words for the screen are looked up when drawn, never as this file loads (13.6).
  const statusWords = () => ({ pending: t("待确认"), confirmed: t("已确认"), rejected: t("已否决"), auto: t("机器提取") });
  // One sentence per verdict (13.6): the timeline's line for a link judgment.
  const judgmentSentence = (verdict, what) => {
    const words = { link: what };
    switch (verdict) {
      case "confirmed": return t("确认了这条关联：{link}", words);
      case "rejected": return t("否决了这条关联：{link}", words);
      case "withdrawn": return t("撤回了连线确认：{link}", words);
      case "undone": return t("撤销了上一次连线确认：{link}", words);
      default: return t("连线确认：{link}", words);
    }
  };
  const filters = () => [
    ["all", t("全部")], ["orphans", t("未识别生成脚本")], ["chains", t("中间数据")], ["broken", t("断链")], ["duplicates", t("同名文件")],
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
    return (link.src_label || link.src) + " " + (t(link.relation || t("关联"))) + " " + (link.dst_label || link.dst);
  }

  // Where a link stands, in words: who drew it and what has been decided.
  function linkStateWords(link) {
    if (link.human) return t("人工标注");
    if (link.conflict) return t("记录冲突，待处理");
    if (link.review) return t("待复核");
    return statusWords()[link.status] || t("机器提取");
  }

  // The judgment actions a machine link offers -- the canvas's own rule
  // (canvas.js linkActions): confirm unless it stands confirmed, reject,
  // and withdraw while a verdict stands.
  function judgeActions(link) {
    if (link.human && link.type === "derives_from") {
      // 14.2: a 「源自」 line is removed by the PI or a single researcher; a
      // member proposes its removal; anyone else (not in the team yet, or
      // team.toml unreadable) is offered nothing here.
      const c = typeof RODesk !== "undefined" && RODesk.context ? RODesk.context() : null;
      const role = c && c.mode;
      return role === "pi" || role === "single" ? ["delete"] : role === "member" ? ["propose_remove"] : [];
    }
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

  // One panel at a time (14.3): app.html's question before a panel holding
  // half-typed text is replaced; this file evaluated alone never asks.
  function mayReplace() { return typeof mayReplacePanel !== "function" || mayReplacePanel(); }

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

  // An attempt's verdict as it moved between two recorded versions, in the
  // screen's words (13.6; the desk's panel says it too, 14.5): a default
  // verdict by its code (RODesk.verdictWord), the researcher's own as written.
  function verdictMove(prev, cur) {
    const word = (v) => {
      const s = v && v.verdict_status;
      if (!s || !s.label) return t("其它");
      const D = typeof window !== "undefined" && window.RODesk;
      return D && D.verdictWord ? D.verdictWord(s) : s.label;
    };
    if (!prev) return t("记下这条尝试，研究判断为「{verdict}」", { verdict: word(cur) });
    const moved = (prev.verdict || "") !== (cur.verdict || "");
    const result = (prev.result || "") !== (cur.result || "") ? cur.result || t("（空）") : null;
    const words = { was: word(prev), became: word(cur), result: result };
    if (moved && result !== null) return t("研究判断从「{was}」改为「{became}」，结果改为：{result}", words);
    if (moved) return t("研究判断从「{was}」改为「{became}」", words);
    return result !== null ? t("结果改为：{result}", words) : t("研究判断不变（{verdict}）", { verdict: word(cur) });
  }

  // Where a version came from, in words; one 同步 installed names the
  // publish it came from (12.13): 「随第 3 版发布同步」.
  function sourceWords(v) {
    if (v && v.source === "publish" && v.snapshot != null) return t("随第 {n} 版发布同步", { n: v.snapshot });
    switch (v && v.source) {
      case "scan": return t("扫描时记下");
      case "approval": return t("批准后写入");
      case "publish": return t("发布时记下");
      case "git": return t("git 提交");
      case "card": return t("定义卡记录");
      case "agent": return t("AI 执行");
      default: return "";
    }
  }

  // A table's first recorded version in words: 「3 行，列 month、topic」.
  function tableWords(summary) {
    if (!summary || typeof summary !== "object") return "";
    const parts = [];
    if (summary.rows != null) parts.push(summary.rows_complete === false ? t("{n} 行以上", { n: summary.rows }) : t("{n} 行", { n: summary.rows }));
    else if (summary.sheet_rows != null) parts.push(t("{n} 行", { n: summary.sheet_rows }));
    // 15.4: a Stata .dta's columns are its variables.
    if (Array.isArray(summary.columns) && summary.format === "dta") parts.push(t("变量 {columns}", { columns: summary.columns.join(t("、")) }));
    else if (Array.isArray(summary.columns)) parts.push(t("列 {columns}", { columns: summary.columns.join(t("、")) }));
    else if (summary.sheet_columns != null) parts.push(t("{n} 列", { n: summary.sheet_columns }));
    return parts.join(t("，"));
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
      // 12.13: a link drawn with 「关联文件」 / 「补上来源」, with the note written with it.
      const m = l.human ? l.mapping : null;
      if (m && (m.date || m.note)) out.push({ kind: "link", at: m.date || "", link: l, m: m });
    });
    out.sort((a, b) => timeKey(b.at) - timeKey(a.at));
    return out;
  }

  // A link's `date` in mappings.toml is a local calendar day with no time
  // (8.5): it is not midnight UTC. It sorts at the end of that day, the
  // latest it can be; any other time sorts by the instant it names.
  const DAY_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;
  function timeKey(at) {
    const m = DAY_ONLY.exec(String(at || ""));
    if (m) return new Date(+m[1], +m[2] - 1, +m[3], 23, 59, 59, 999).getTime();
    const t = Date.parse(String(at || ""));
    return isNaN(t) ? -Infinity : t;
  }

  // -- runtime ------------------------------------------------------------------------

  const nav = { id: null, fromView: null, trail: [], graphOpen: false, notesDraft: new Map(), shownDiffs: new Set() };
  const dc = { filter: "all", data: null };
  const res = { open: new Set(), drafts: new Map() };
  let meCache = { epoch: -1, id: null, loaded: false };

  function now() { return new Date(); }
  // A day with no time is said as the day (「今天」, 「10月4日」), never a made-up hour.
  function when(at) {
    if (!at) return "";
    const m = DAY_ONLY.exec(String(at));
    if (!m) return RODesk._whenWords(at, now()) || String(at);
    const today = now();
    const days = Math.round((new Date(today.getFullYear(), today.getMonth(), today.getDate()) - new Date(+m[1], +m[2] - 1, +m[3])) / 86400000);
    if (days === 0) return t("今天");
    if (days === 1) return t("昨天");
    const month = [t("1月"), t("2月"), t("3月"), t("4月"), t("5月"), t("6月"),
      t("7月"), t("8月"), t("9月"), t("10月"), t("11月"), t("12月")][+m[2] - 1];
    const words = { year: +m[1], month, day: +m[3] };
    return +m[1] === today.getFullYear() ? t("{month}{day}日", words) : t("{year}年{month}{day}日", words);
  }

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
    if (!refresh) host.replaceChildren(mk("p", "empty-note", t("载入中…")));
    let page, history, notes, me;
    try {
      page = await apiGet("/api/node?node=" + encodeURIComponent(id));
      [history, notes, me] = await Promise.all([
        apiGet("/api/history?node=" + encodeURIComponent(page.id)).catch((err) => ({ failed: err })),
        apiGet("/api/notes?node=" + encodeURIComponent(page.id)).catch((err) => ({ failed: err })),
        meId(),
        RODesk.ensureContext(), // U4: whether this person edits or proposes
      ]);
    } catch (err) {
      if (epoch !== state.dataEpoch || nav.id !== id) return;
      host.innerHTML = "";
      host.appendChild(backBar());
      const box = mk("div", "node-failed");
      renderBlockingError(box, t("没能打开这一页"), err);
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
    const prev = nav.trail.length ? t("返回上一页") : t("返回{section}", { section: t(SECTION_TITLES[SECTION_OF_VIEW[nav.fromView]] || "") });
    bar.appendChild(link(prev, "", back));
    return bar;
  }

  function renderHead(host, page) {
    const head = mk("header", "node-head");
    const kind = mk("div", "node-kind");
    kind.append(icon(page.type), mk("span", null, t(page.type_label)));
    head.appendChild(kind);
    head.appendChild(mk("h2", "node-name", page.name || page.id));
    if (page.path) head.appendChild(mk("div", "node-path mono", page.path));
    else if (page.file) head.appendChild(mk("div", "node-path mono", page.file));
    const marks = mk("div", "node-marks");
    const d = page.details || {};
    if (page.type === "attempt") marks.appendChild(RODesk.verdictInline(d.verdict_status));
    if (page.path && page.exists === false) marks.appendChild(mk("span", "status-label tone-clay", t("文件不存在")));
    if (page.review) marks.appendChild(mk("span", "status-label tone-ochre", t("{n} 条关联待复核", { n: page.review })));
    const own = ownMark(page);
    if (own) marks.appendChild(own);
    if (marks.childElementCount) head.appendChild(marks);
    const actions = mk("div", "node-actions");
    const status = mk("div", "notice-status");
    if (page.path && page.exists) {
      actions.appendChild(mkButton(t("查看内容"), "btn btn-small", () => openFilePanel(page.path, page.type === "script" ? "script" : undefined)));
      actions.appendChild(mkButton(t("打开"), "btn btn-small", () => openFile(page.path, false, status)));
      actions.appendChild(mkButton(t("在 Finder 中显示"), "btn btn-small", () => openFile(page.path, true, status)));
    }
    if (page.type === "attempt") {
      // U4: a member proposes a verdict instead of editing the official
      // table; someone not in the team yet cannot change it at all.
      const role = ROTeam.mode();
      const edit = role === "member" ? t("提议修改研究判断") : t("编辑这一行");
      if (role !== "none") actions.appendChild(mkButton(edit, "btn btn-small", () => { if (mayReplace()) openAttemptForm("update", page.id); }));
      if (d.basis) actions.appendChild(mkButton(t("当时的依据"), "btn btn-small", () => ROTeam.openEvidence(page.id, { number: d.number, title: d.description })));
    }
    if (page.type === "variable") actions.appendChild(mkButton(t("打开定义卡"), "btn btn-small", () => openVariableCard(page.card || page.id.slice("variable:".length))));
    if (page.paper) {
      // 12.13: a draft's own page marks it as the paper, or no longer.
      actions.appendChild(paperButton(page.paper.draft, page.paper.marked, status));
      marks.appendChild(mk("span", "status-label tone-" + (page.paper.marked ? "olive" : "plain"),
        page.paper.marked ? t("论文正文") : t("不是论文正文")));
      if (!head.contains(marks)) head.insertBefore(marks, actions.parentNode === head ? actions : null);
    }
    if (actions.childElementCount) head.append(actions, status);
    host.appendChild(head);
    renderDetails(host, page);
  }

  async function openFile(path, reveal, status) {
    setStatus(status, "", t("处理中…"));
    try {
      const r = await apiPost("/api/open", { path: path, reveal: reveal });
      setStatus(status, "ok", reveal ? t("已在 Finder 中显示：{path}", { path: r.opened }) : t("已打开：{path}", { path: r.opened }));
    } catch (err) {
      setStatus(status, "err", reveal ? t("无法在 Finder 中显示") : t("无法打开"), err);
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
      detailRow(dl, t("日期"), d.date || "");
      detailRow(dl, t("途径"), d.description || "");
      detailRow(dl, t("变量"), d.variables || "");
      detailRow(dl, t("结果"), d.result || t("（无）"));
      detailRow(dl, t("研究判断"), RODesk.verdictInline(d.verdict_status));
      if (d.source_file) {
        // 12.11: the mono for the path only; 「第 N 行」 is words.
        const where = mk("span", "node-where");
        where.appendChild(link(d.source_file, "mono", () => openFilePanel(d.source_file)));
        if (d.source_line) where.append(t(" 第 {n} 行", { n: d.source_line }));
        detailRow(dl, t("出处"), where);
      }
      if (d.step_files_broken && d.step_files_broken.length) detailRow(dl, t("找不到的步骤脚本"), d.step_files_broken.join(t("、")));
    } else if (page.type === "claim") {
      detailRow(dl, t("原文"), d.sentence || "");
      if (d.line) detailRow(dl, t("位置"), t("第 {n} 行", { n: d.line }));
    } else if (page.type === "reference" || page.type === "ref") {
      detailRow(dl, "DOI", d.doi || "");
      detailRow(dl, t("作者"), Array.isArray(d.authors) ? d.authors.join(t("、")) : (d.authors || ""));
      detailRow(dl, t("年份"), d.year ? String(d.year) : "");
      detailRow(dl, t("刊物"), d.venue || "");
    } else if (page.type === "variable") {
      if (d.in_use != null) detailRow(dl, t("使用中"), t("第 {n} 版", { n: d.in_use }));
      if (d.card_state && d.card_state !== "ok") detailRow(dl, t("状态"), t("定义卡的记录现在无法读取"));
    }
    if (dl.childElementCount) host.appendChild(dl);
  }

  // -- 来龙去脉 (12.7) -------------------------------------------------------------------

  // The node page draws it with its repairs; the desk's side panel (DESIGN.md
  // 14.3, `opts.panel`) draws the same columns and lines without them, a file
  // opening through `opts.open`. Its words go through t() (14.6); the
  // columns' titles come from the engine in the request's language.
  function renderTraceSection(host, page, opts) {
    const o = opts || {};
    const s = sec(t("来龙去脉"), "node-trace-sec");
    const tr = page.trace || { columns: [], lines: [], breaks: [] };
    if (tr.deeper) s.appendChild(mk("p", "node-sub", t("更上游还有 {n} 个文件，这里只画出最近的几层。", { n: tr.deeper })));
    const scroller = mk("div", "trace-scroll");
    const grid = mk("div", "trace");
    tr.columns.forEach((col, ci) => {
      const c = mk("div", "trace-col" + (col.key === "self" ? " is-self" : ""));
      c.dataset.col = String(ci);
      c.appendChild(mk("div", "trace-col-title", t(col.title)));
      col.items.forEach((item) => c.appendChild(traceItem(item, ci, o.open)));
      if (col.gap) c.appendChild(gapBox(page, col.gap, o.panel));
      if (!col.items.length && !col.gap) c.appendChild(mk("div", "trace-empty", t("没有")));
      grid.appendChild(c);
    });
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "trace-lines");
    svg.setAttribute("aria-hidden", "true");
    grid.appendChild(svg);
    grid._lines = tr.lines || [];
    // Columns share the width down to 120px each; below that the trace scrolls sideways.
    grid.style.minWidth = (tr.columns.length * 120 + Math.max(0, tr.columns.length - 1) * 32) + "px";
    scroller.appendChild(grid);
    s.appendChild(scroller);
    if (!o.panel) renderBelongs(s, page);
    const other = (tr.breaks || []).filter((b) => ["no_generating_script", "no_attempt", "no_files", "no_backing"].indexOf(b.kind) < 0);
    other.forEach((b) => s.appendChild(mk("p", "trace-break", b.path ? t("{label}：{path}", { label: t(b.label), path: b.path }) : t(b.label))));
    host.appendChild(s);
    return s;
  }

  // How a file belongs to an attempt (14.3's membership rule), in words.
  function viaWord(via) {
    switch (via) {
      case "step_files": return t("步骤脚本");
      case "belongs_to": return t("人工关联");
      default: return via ? String(via) : "";
    }
  }

  function traceItem(item, col, open) {
    const self = !!item.self;
    const el = mk(self ? "div" : "button", "trace-item t-" + item.type + (self ? " self" : "") +
      (item.exists === false ? " missing" : ""));
    if (!self) {
      el.type = "button";
      el.addEventListener("click", () => (open || openNode)(item.id));
    }
    // The full name on hover for every card, this node's own included: a
    // long name is cut short with an ellipsis in a narrow window (12.11 sweep).
    el.title = item.path || item.file || item.label;
    el.dataset.node = item.id;
    el.dataset.col = String(col);
    const top = mk("span", "trace-item-head");
    top.append(icon(item.type), mk("span", "trace-item-name", item.label));
    el.appendChild(top);
    const where = item.path ? posixDir(item.path) : (item.file || "");
    if (where) el.appendChild(mk("span", "trace-item-dir mono", where));
    const flags = [];
    if (item.exists === false) flags.push([t("文件不存在"), "clay"]);
    if (item.unreadable) flags.push([t("暂时无法读取"), "clay"]);
    if (item.raw && item.type === "dataset") flags.push([t("原始数据"), "plain"]);
    if (item.member) flags.push([viaWord(item.member), "plain"]);
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

  // A break, drawn where the missing piece would be, with its repair (not
  // in the desk's panel: `bare`).
  function gapBox(page, gap, bare) {
    const box = mk("div", "trace-gap");
    box.appendChild(mk("div", "trace-gap-label", t(gap.label)));
    if (gap.repair && !bare) box.appendChild(repairControl(page, gap.repair.type));
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
      label = t("补上来源");
      options = (choices.scripts || []).map((s) => ({ value: s.path, text: s.path }));
    } else if (page.type === "attempt") {
      label = t("关联文件");
      options = (choices.files || []).map((f) => ({ value: f.path, text: t(f.type_label) + "  " + f.path }));
    } else {
      label = t("关联文件");
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
    select.setAttribute("aria-label", type === "generates" || type === "writes" ? t("生成它的脚本") : (page.type === "attempt" ? t("要关联的文件") : t("它属于的尝试")));
    const first = mk("option", null, type === "generates" || type === "writes" ? t("选择生成它的脚本") : (page.type === "attempt" ? t("选择要关联的文件") : t("选择它属于的尝试")));
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
    note.placeholder = t("备注（可不填）");
    const status = mk("div", "notice-status");
    const go = mkButton(t("确认"), "btn btn-small btn-primary", () => writeRepair(page, type, select.value, note.value, status, [go, cancel]));
    const cancel = mkButton(t("取消"), "btn btn-small", () => {
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
    const travels = travelsWords("link");
    if (travels) form.appendChild(mk("p", "node-sub writes", travels));
    if (!options.length) {
      form.innerHTML = "";
      form.appendChild(mk("p", "node-sub", type === "generates" || type === "writes" ? t("项目里还没有脚本可选。") : t("没有可以选择的项目。")));
    }
    wrap.append(trigger, form);
    return wrap;
  }

  function repairSentence(page, type, chosen) {
    const me = page.path || page.name;
    if (type === "generates") return t("将记下：{script} 生成 {file}", { script: chosen.value, file: me });
    if (type === "writes") return t("将记下：{script} 写出 {file}", { script: chosen.value, file: me });
    if (page.type === "attempt") return t("将记下：{file} 属于 {attempt}", { file: chosen.value, attempt: page.name });
    return t("将记下：{file} 属于 {attempt}", { file: me, attempt: chosen.text.replace(/^[^·]*·\s*/, "") });
  }

  async function writeRepair(page, type, value, note, status, buttons) {
    if (!value) return;
    let body;
    if (type === "generates" || type === "writes") body = { from: value, to: page.path, type: type };
    else if (page.type === "attempt") body = { from: value, to: page.id, type: "belongs_to" };
    else body = { from: page.path, to: value, type: "belongs_to" };
    if (note && note.trim()) body.note = note.trim();
    buttons.forEach((b) => { b.disabled = true; });
    setStatus(status, "", t("正在记下…"));
    let r;
    try {
      r = await apiPost("/api/mappings/add", body);
    } catch (err) {
      buttons.forEach((b) => { b.disabled = false; });
      setStatus(status, "err", type === "belongs_to" ? t("没能关联文件") : t("没能补上来源"), err);
      return;
    }
    if (r.ingest_error) {
      setStatus(status, "err", t("已经写进 mappings.toml，但图谱还没有更新；稍后会自动重试"), r.ingest_error);
      return;
    }
    setStatus(status, "ok", t("已记下。"));
    loadNode(views.node, true);
  }

  // The attempt(s) it belongs to and the variable versions it uses, as a
  // line under the trace; no attempt is a gap with 「关联文件」.
  function renderBelongs(host, page) {
    const trace = page.trace || {};
    if (page.type === "attempt") {
      // 12.7: 「关联文件」 attaches more scripts, data and figures to it.
      if (!(trace.breaks || []).some((b) => b.kind === "no_files")) {
        const more = mk("div", "trace-belongs");
        more.appendChild(mk("span", "trace-belongs-word", t("还有文件属于这条尝试？")));
        more.appendChild(repairControl(page, "belongs_to"));
        host.appendChild(more);
      }
      return;
    }
    if (page.type === "variable") return;
    const attempts = trace.attempts || [];
    const brk = (trace.breaks || []).find((b) => b.kind === "no_attempt");
    const line = mk("div", "trace-belongs");
    if (attempts.length) {
      line.appendChild(mk("span", "trace-belongs-word", t("属于")));
      attempts.forEach((a) => {
        const one = mk("span", "trace-attempt");
        one.appendChild(RODesk.statusLabel(a.verdict_status));
        one.appendChild(link("#" + String(a.number || "").replace(/^#/, "") + " " + (a.title || ""), "", () => openNode(a.id)));
        const via = (a.via || []).map((v) => viaWord(v.via)).filter((x, i, all) => all.indexOf(x) === i);
        if (via.length) one.appendChild(mk("span", "node-sub-inline", t("（{text}）", { text: via.join(t("、")) })));
        line.appendChild(one);
      });
      host.appendChild(line);
    } else if (brk && (page.type === "script" || page.type === "dataset" || page.type === "figure")) {
      const gap = mk("div", "trace-gap wide");
      gap.appendChild(mk("div", "trace-gap-label", t(brk.label)));
      gap.appendChild(repairControl(page, "belongs_to"));
      host.appendChild(gap);
    }
    const vars = trace.variables || [];
    if (vars.length) {
      const v = mk("div", "trace-belongs");
      v.appendChild(mk("span", "trace-belongs-word", t("使用变量")));
      vars.forEach((x) => {
        const one = mk("span", "trace-attempt");
        const name = link(t("{variable} 第 {n} 版", { variable: x.variable, n: x.version }), "", () => openNode("variable:" + x.variable));
        if (x.reference && x.reference.label) name.title = x.reference.label;
        one.appendChild(name);
        one.appendChild(mk("span", "node-sub-inline", x.matched_by === "output" ? t("（定义卡写明输出到这里）") : t("（按列名 {field} 对上）", { field: x.field || "" })));
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
      // The relation in the screen's language, by its type (13.6).
      const relation = typeof RODesk !== "undefined" && RODesk.relationWord ? RODesk.relationWord(l.type) : l.relation;
      title.textContent = l.human ? t("{relation}（人工标注）", { relation: relation }) : relation;
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
    const s = sec(t("改动与备注"));
    const hist = history && !history.failed ? history : null;
    const noteList = notes && !notes.failed ? (notes.notes || []) : [];
    if (history && history.failed) {
      const box = mk("p", "trace-break");
      renderBlockingError(box, t("这里的历史现在读不到"), history.failed);
      s.appendChild(box);
    } else if (hist && hist.reason && !(hist.versions || []).length) {
      s.appendChild(mk("p", "node-sub", t(hist.reason)));
    }
    if (hist && (page.type === "section" || page.type === "claim") && hist.path) {
      s.appendChild(mk("p", "node-sub", t("下面的版本是它所在的文稿 {path} 的版本。", { path: hist.path })));
    }
    if (notes && notes.failed) {
      const box = mk("p", "trace-break");
      renderBlockingError(box, t("这里的备注现在读不到"), notes.failed);
      s.appendChild(box);
    } else if (notes && notes.stale) {
      s.appendChild(mk("p", "trace-break", t("{reason}；下面是图谱上次读到的备注。", { reason: notes.message || t("备注记录文件现在无法读取") })));
    }
    const entries = timeline(hist, noteList, page.links);
    if (!entries.length) s.appendChild(mk("p", "node-empty", t("还没有记录到改动或备注。")));
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
          main.appendChild(mk("div", "tl-meta", [e.v.who_label, sourceWords(e.v)].filter(Boolean).join(" · ")));
          if (e.v.approved_by) main.appendChild(mk("div", "tl-meta", t("经 {who} 批准", { who: e.v.approved_by_label || e.v.approved_by })));
          if (e.v.note) main.appendChild(mk("p", "tl-note", e.v.note));
        } else if (e.kind === "card") {
          const act = t((typeof ACT_WORDS !== "undefined" && ACT_WORDS[e.v.act]) || e.v.act || t("记录"));
          main.appendChild(mk("div", "tl-what", act + (e.v.number ? t("第 {n} 版", { n: e.v.number }) : "")));
          main.appendChild(mk("div", "tl-meta", e.v.who_label || ""));
          if (e.v.note) main.appendChild(mk("p", "tl-note", e.v.note));
        } else if (e.kind === "note") {
          renderNote(main, page, e.note, me);
        } else if (e.kind === "link") {
          main.appendChild(mk("div", "tl-what", t("连线：{link}", { link: linkSentence(e.link) })));
          main.appendChild(mk("div", "tl-meta", [e.m.by_label || "", t("人工标注")].filter(Boolean).join(" · ")));
          if (e.m.note) main.appendChild(mk("p", "tl-note", e.m.note));
        } else if (e.kind === "judgment") {
          main.appendChild(mk("div", "tl-what", judgmentSentence(e.j.verdict, linkSentence(e.link))));
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
    if (!e.prev) head.textContent = v.version === "worktree" ? t("工作区里还没有提交的改动") : t("最早记下的版本");
    else head.textContent = v.version === "worktree" ? t("工作区里还没有提交的改动") : (v.kind === "data" ? t("数据有改动") : t("内容有改动"));
    host.appendChild(head);
    const meta = [v.who_label, sourceWords(v)];
    if (v.approved_by) meta.push(t("经 {who} 批准", { who: v.approved_by_label || v.approved_by }));
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
    else slot.appendChild(mkButton(t("查看这一版的改动"), "btn btn-small", () => { nav.shownDiffs.add(key); load(); }));
  }

  async function loadDiff(slot, page, hist, a, b) {
    slot.innerHTML = "";
    slot.appendChild(mk("span", "node-sub", t("载入中…")));
    let d;
    try {
      d = await apiGet("/api/history/diff?node=" + encodeURIComponent(hist.node || page.id) + "&a=" +
        encodeURIComponent(a.version) + "&b=" + encodeURIComponent(b.version));
    } catch (err) {
      slot.innerHTML = "";
      const box = mk("p", "trace-break");
      renderBlockingError(box, t("没能比较这两个版本"), err);
      slot.appendChild(box);
      return;
    }
    slot.innerHTML = "";
    // 12.13: a table leads with its rows and columns; a small one shows its
    // line diff below, a large one only says it was left out.
    const words = d.summary_words || "";
    if (words) slot.appendChild(mk("p", "tl-summary", words));
    if (d.diff) slot.appendChild(diffBlock(diffRows(d.diff), d.truncated));
    else if (d.diff_left_out) slot.appendChild(mk("p", "node-sub", t("表格较大：只比较行和列，不逐行对比。")));
    else if (!words && d.same) slot.appendChild(mk("p", "tl-summary", t("内容相同")));
  }

  function diffBlock(rows, truncated) {
    const wrap = mk("div", "diff");
    const render = (all) => {
      wrap.innerHTML = "";
      const shown = all ? rows : rows.slice(0, DIFF_SHOWN);
      shown.forEach((r) => wrap.appendChild(mk("div", "dl-row " + r.cls, r.text || " ")));
      if (rows.length > DIFF_SHOWN) {
        wrap.appendChild(mkButton(all ? t("收起") : t("显示其余 {n} 行", { n: rows.length - DIFF_SHOWN }), "btn btn-small diff-more",
          () => render(!all)));
      }
      if (truncated) wrap.appendChild(mk("p", "node-sub", t("改动太长，只比较了开头的一部分。")));
    };
    render(false);
    return wrap;
  }

  function renderNote(host, page, note, me) {
    const head = mk("div", "tl-what", t("备注", { n: 1 }));
    host.appendChild(head);
    host.appendChild(mk("div", "tl-meta", note.label || note.name || note.by || t("最初的研究者")));
    const text = mk("p", "tl-note" + (note.withdrawn ? " withdrawn" : ""), note.text || "");
    host.appendChild(text);
    if (note.withdrawn) {
      host.appendChild(mk("div", "tl-meta", note.withdrawn_at ? t("已撤回（{when}）", { when: when(note.withdrawn_at) }) : t("已撤回")));
      return;
    }
    if (noteIsMine(note, me)) {
      const status = mk("div", "notice-status");
      const b = mkButton(t("撤回"), "btn btn-small", async () => {
        b.disabled = true;
        setStatus(status, "", t("正在撤回…"));
        try {
          await apiPost("/api/notes", { action: "withdraw", id: note.id });
        } catch (err) {
          b.disabled = false;
          setStatus(status, "err", t("没能撤回这条备注"), err);
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

  // 12.12: in a member's working copy a note, a link judgment or a drawn
  // link travels without approval -- said where it is made.
  function travelsWords(kind, ctx) {
    const c = ctx || (typeof RODesk !== "undefined" ? RODesk.context() : null);
    if (!c || c.mode !== "member") return "";
    const me = c.me && c.me.me ? c.me.me.id : t("你的邮箱");
    const what = { note: t("这条备注"), link: t("这条连线"), judgment: t("你的连线确认"),
      unlink: t("这次删除"), paper: t("「设为论文正文」的标记") }[kind] || t("这条记录");
    const record = kind === "link" ? t("正式的 mappings.toml") : kind === "paper" ? t("正式的 papers.toml") : t("正式记录");
    const then = kind === "unlink" ? t("PI 下次发布时从正式的 mappings.toml 里删去这条连线")
      : t("PI 下次发布时加入{record}", { record });
    const mine = c.share && c.share.mine && c.share.mine.path;
    const where = mine ? t("你自己的提议文件夹") : t("公盘的「{folder}」", { folder: "提议/" + me + "/" });
    return t("{what}先记在你的工作副本里；下次同步时写入{where}，{then}，署你的名字，不需要批准。", { what, where, then });
  }
  function noteForm(page, notes) {
    const form = mk("div", "node-note");
    const input = mk("textarea", "form-input node-note-input");
    input.rows = 3;
    input.placeholder = t("写一条备注：为什么这样做、要注意什么");
    input.setAttribute("aria-label", t("备注", { n: 1 }));
    input.value = nav.notesDraft.get(page.id) || "";
    input.addEventListener("input", () => nav.notesDraft.set(page.id, input.value));
    const status = mk("div", "notice-status");
    const add = mkButton(t("添加备注"), "btn btn-small", async () => {
      const text = input.value.trim();
      if (!text) { setStatus(status, "err", t("备注是空的")); return; }
      add.disabled = true;
      setStatus(status, "", t("正在保存…"));
      try {
        await apiPost("/api/notes", { action: "add", node: page.id, text: text });
      } catch (err) {
        add.disabled = false;
        setStatus(status, "err", t("备注没有保存"), err);
        return;
      }
      nav.notesDraft.delete(page.id);
      loadNode(views.node, true);
    });
    const blocked = humanRecordsBlocked() || (notes && notes.trusted === false ? (notes.message || t("备注记录文件现在无法写入")) : null);
    if (blocked) { add.disabled = true; add.title = blocked; }
    const row = mk("div", "tl-actions");
    row.appendChild(add);
    form.append(input, row, status);
    const travels = travelsWords("note");
    if (travels) form.appendChild(mk("p", "node-sub writes", travels));
    return form;
  }

  // -- 连线确认 ----------------------------------------------------------------------------

  function renderJudgments(host, page) {
    const s = sec(t("连线确认"));
    const links = page.links || [];
    if (!links.length) {
      s.appendChild(mk("p", "node-empty", t("还没有和它关联的连线。")));
      host.appendChild(s);
      return;
    }
    s.appendChild(mk("p", "node-sub", t("机器提取的关联由你确认或否决；人工标注的关联来自 mappings.toml。")));
    const travels = travelsWords("judgment");
    if (travels) s.appendChild(mk("p", "node-sub writes", travels));
    const ul = mk("ul", "node-links");
    const render = (all) => {
      ul.innerHTML = "";
      (all ? links : links.slice(0, LINKS_SHOWN)).forEach((l) => ul.appendChild(linkRow(page, l)));
      if (links.length > LINKS_SHOWN) {
        const li = mk("li", "node-links-more");
        li.appendChild(mkButton(all ? t("收起") : t("显示全部 {n} 条", { n: links.length }), "btn btn-small", () => render(!all)));
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
    head.append(srcEl, mk("span", "link-rel", t(l.relation)), dstEl);
    const tone = l.human ? "clay" : (l.review || l.conflict) ? "ochre" : l.status === "confirmed" ? "olive" : l.status === "rejected" ? "gray" : "plain";
    head.appendChild(mk("span", "status-label tone-" + tone, linkStateWords(l)));
    const own = ownMark(l);
    if (own) head.appendChild(own);
    li.appendChild(head);
    // 12.11: a path in the mono, 「第 N 行」 in the sans.
    const where = mk("div", "tl-meta");
    (l.evidence || []).forEach((e, i) => {
      if (i) where.append(t("；"));
      if (e.file && e.file !== l.other.path && e.file !== (page.path || "")) where.append(mk("span", "mono", e.file), " ");
      where.append(t("第 {n} 行", { n: e.line }));
      if (e.callee) where.append(t("（"), mk("span", "mono", e.callee), t("）"));
      // 15.3: a link made through a confirmed answer carries it.
      if (e.anchor) where.append(" ", t("{global} 已确认指{folder}", { global: "$" + e.anchor.global, folder: stataFolder(e.anchor.folder) }));
    });
    if ((l.evidence || []).length) li.appendChild(where);
    if (l.judgement && (l.review || l.conflict) && l.judgement.label) li.appendChild(mk("div", "tl-meta", l.judgement.label));
    if (l.judgement && l.judgement.note) li.appendChild(mk("p", "tl-note", l.judgement.note));
    if (other.path && other.type !== page.type) li.title = other.path;
    const actions = mk("div", "link-actions");
    const status = mk("div", "notice-status");
    const blocked = humanRecordsBlocked();
    const buttons = [];
    judgeActions(l).forEach((a) => {
      const text = a === "propose_remove" ? t("提议删除这条连线") : a === "delete" ? t("删除标注") : a === "confirmed" ? t("确认")
        : a === "rejected" ? t("否决") : t("撤回");
      const b = mkButton(text, "btn btn-small", () => judge(l, a, status, buttons));
      if (blocked) { b.disabled = true; b.title = blocked; }
      buttons.push(b);
      actions.appendChild(b);
    });
    li.append(actions, status);
    return li;
  }

  async function judge(l, verdict, status, buttons) {
    // A 「源自」 line never travels in a packet (14.2): the no-approval sentence is never said of it.
    const travels = l.type === "derives_from" ? "" : travelsWords("unlink");
    if (verdict === "propose_remove") { await proposeRemoval(l, status, buttons); return; }
    if (verdict === "delete" && !window.confirm(t("删除这条人工标注？\n\n{link}\n\n它会从 .researchos/mappings.toml 里移除。", { link: linkSentence(l) }) +
      (travels ? "\n\n" + travels : ""))) return;
    buttons.forEach((b) => { b.disabled = true; });
    setStatus(status, "", t("正在记下…"));
    try {
      if (verdict === "delete") {
        const r = await apiPost("/api/mappings/delete", mappingEnds(l));
        if (r.ingest_error) {
          setStatus(status, "err", t("已经从 mappings.toml 删除，但图谱还没有更新；稍后会自动重试"), r.ingest_error);
          return;
        }
      } else {
        await apiPost("/api/judgements", { src: l.src, dst: l.dst, type: l.type, extractor: l.extractor, verdict: verdict });
      }
    } catch (err) {
      buttons.forEach((b) => { b.disabled = false; });
      setStatus(status, "err", (verdict === "delete" && lineRefusal(err)) || (verdict === "delete" ? t("没能删除这条标注") : t("没能记下这条连线确认")), err);
      return;
    }
    loadNode(views.node, true);
  }

  // A refused 「源自」 line says the route map's own sentence (8.8), when the map is loaded.
  function lineRefusal(err) {
    return typeof RORouteMap !== "undefined" && RORouteMap.lineRefusal ? RORouteMap.lineRefusal(err && err.state) : null;
  }

  // A member removes a 「源自」 line by proposing it (14.2), as the desk's map and panel do.
  async function proposeRemoval(l, status, buttons) {
    if (!window.confirm(t("提议删除这条连线？PI 批准后它才会从路线图上消失。"))) return;
    buttons.forEach((b) => { b.disabled = true; });
    setStatus(status, "", t("正在写入公盘…"));
    try {
      await apiPost("/api/proposals", { kind: "route_link", op: "remove", child: l.src, parent: l.dst, write: true });
    } catch (err) {
      buttons.forEach((b) => { b.disabled = false; });
      setStatus(status, "err", lineRefusal(err) || t("没能删除这条连线"), err);
      return;
    }
    setStatus(status, "ok", t("已把删除这条连线作为提议交给 PI。"));
    if (typeof markViewsStale === "function") markViewsStale(); // the desk marks the line 提议删除
    if (typeof RODesk !== "undefined" && RODesk.refreshSidebar) RODesk.refreshSidebar();
  }

  // -- 关系图 (the canvas, scoped to this node's neighbourhood) ----------------------------

  function renderGraphSection(host, page) {
    const s = sec(t("关系图"));
    const canvasable = ["script", "dataset", "figure"].indexOf(page.type) >= 0 && page.in_index;
    if (!canvasable) {
      s.appendChild(mk("p", "node-empty", t("关系图只画脚本、数据和图表；它的关联见上面的来龙去脉。")));
      host.appendChild(s);
      return;
    }
    const holder = mk("div", "node-graph" + (nav.graphOpen ? "" : " hidden"));
    const toggle = mkButton(nav.graphOpen ? t("收起关系图") : t("打开关系图"), "btn btn-small", () => {
      nav.graphOpen = !nav.graphOpen;
      toggle.textContent = nav.graphOpen ? t("收起关系图") : t("打开关系图");
      holder.classList.toggle("hidden", !nav.graphOpen);
      if (nav.graphOpen) RCECanvas.load(holder, "near:" + page.id);
    });
    s.appendChild(toggle);
    s.appendChild(holder);
    host.appendChild(s);
    if (nav.graphOpen) RCECanvas.load(holder, "near:" + page.id);
  }

  // -- 图表与论断 (12.9) ---------------------------------------------------------------------

  function stateLabel(trace) {
    const value = trace || {};
    const el = mk("span", "status-label tone-" + traceTone(value.state), t(value.label || ""));
    if (value.breaks && value.breaks.length) el.title = value.breaks.map((text) => t(text)).join(t("；"));
    return el;
  }

  function resultRow(item) {
    const li = mk("li", "res-row");
    li.appendChild(mk("span", "res-kind", t(item.kind || item.type_label || "")));
    const main = mk("div", "res-main");
    const name = link(item.label, "", () => openNode(item.id), t("打开它的来龙去脉"));
    main.appendChild(name);
    if (item.path) main.appendChild(mk("span", "res-path mono", item.path));
    if (item.exists === false) main.appendChild(mk("span", "status-label tone-clay", t("文件不存在")));
    const own = ownMark(item);
    if (own) main.appendChild(own);
    li.append(main, stateLabel(item.trace));
    return li;
  }

  async function loadResults(host, refresh) {
    const epoch = state.dataEpoch;
    if (!refresh) host.replaceChildren(mk("p", "empty-note", t("载入中…")));
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
    if (data.outputs) renderRegisteredOutputs(page, data.outputs);
    page.appendChild(mk("p", "node-sub", t("文稿里的图、表和论断。每一项都能打开它的来龙去脉：完整，或者缺来源、缺尝试。")));
    const figs = mk("section", "res-block");
    figs.appendChild(mk("h2", "desk-title", t("图与表")));
    if (!data.sections.length && !data.loose.length) figs.appendChild(mk("p", "node-empty", t("文稿里还没有引用图或表。")));
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
      group.appendChild(mk("div", "res-group-title", t("没有被文稿引用的图")));
      const ul = mk("ul", "res-list");
      data.loose.forEach((item) => ul.appendChild(resultRow(item)));
      group.appendChild(ul);
      figs.appendChild(group);
    }
    page.appendChild(figs);
    const claims = mk("section", "res-block");
    claims.appendChild(mk("h2", "desk-title", t("论断")));
    renderPapers(claims, data);
    page.appendChild(claims);
    host.appendChild(page);
    host.dataset.loaded = "1";
    if (refresh) window.scrollTo(0, top);
  }

  // 12.13: 「设为论文正文」 / 「不再作为论文正文」 -- one entry appended to
  // .researchos/papers.toml; in a member's copy it travels with the next sync.
  // 18.4 item 1: as every other write of the page does, it keeps the generation
  // the POST returns (so the poll does not find a move this page has already
  // read) and refreshes the page's views itself -- once, not once here and
  // again when the poll sees the generation move.
  function paperButton(draft, marked, status) {
    const b = mkButton(marked ? t("不再作为论文正文") : t("设为论文正文"), "btn btn-small", async () => {
      b.disabled = true;
      setStatus(status, "", t("正在记下…"));
      let res;
      try {
        res = await apiPost("/api/papers", { draft: draft, marked: !marked });
      } catch (err) {
        b.disabled = false;
        setStatus(status, "err", t("没能记下"), err);
        return;
      }
      setStatus(status, "ok", marked ? t("已不再作为论文正文。") : t("已设为论文正文：它的论断会出现在图表与论断里。"));
      if (res && typeof res.generation === "number" && typeof lastGeneration !== "undefined") lastGeneration = res.generation;
      if (typeof refreshCurrentView === "function") await refreshCurrentView();
    });
    const blocked = humanRecordsBlocked();
    if (blocked) { b.disabled = true; b.title = blocked; }
    const travels = travelsWords("paper");
    if (travels) b.title = (b.title ? b.title + "\n" : "") + travels;
    return b;
  }

  // A quiet line chevron for disclosure (12.13), turned by CSS when open;
  // the browser's own triangle is hidden.
  function chevron() {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("class", "res-chevron");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", "M6 4l4 4-4 4");
    svg.appendChild(p);
    return svg;
  }

  // 12.13: every draft on its own line, with the mark; claims only under
  // a draft marked 「设为论文正文」.
  function renderPapers(section, data) {
    const papers = data.papers || {};
    const drafts = data.drafts || [];
    const marked = drafts.filter((d) => d.marked);
    if (papers.message) {
      const p = mk("p", "trace-break");
      renderBlockingError(p, papers.message, "");
      section.appendChild(p);
    }
    if (!drafts.length) {
      section.appendChild(mk("p", "node-empty", t("还没有读到文稿。")));
      return;
    }
    if (!marked.length) {
      section.appendChild(mk("p", "node-sub", t("图表与论断只列出论文正文里的论断：修订记录、审稿意见里的数字不算。还没有文稿设为论文正文——") +
        t("在下面的文稿旁点「设为论文正文」，或在文稿自己的页面上点它。")));
    } else {
      section.appendChild(mk("p", "node-sub", t("只列出设为论文正文的文稿里的论断；展开一份文稿才载入它的论断。")));
    }
    const status = mk("div", "notice-status");
    const list = mk("div", "res-drafts");
    drafts.forEach((d) => list.appendChild(d.marked ? draftClaims(d, status) : draftLine(d, status)));
    section.append(list, status);
    const travels = travelsWords("paper");
    if (travels) section.appendChild(mk("p", "node-sub writes", travels));
  }

  function draftLine(d, status) {
    const row = mk("div", "res-draft res-draft-plain");
    const head = mk("div", "res-draft-summary");
    head.append(link(d.file, "mono", () => openNode(d.node), t("打开这份文稿的页面")),
      mk("span", "node-sub-inline", d.claims ? t("{n} 处带数字的句子，不列出", { n: d.claims }) : t("没有带数字的句子")));
    head.appendChild(paperButton(d.file, false, status));
    row.appendChild(head);
    return row;
  }

  function draftClaims(c, status) {
    const box = mk("details", "res-draft");
    const summary = mk("summary", "res-draft-summary");
    summary.append(chevron(), mk("span", "mono", c.file), mk("span", "node-sub-inline", t("{n} 条论断", { n: c.claims != null ? c.claims : c.count })));
    if (status) {
      const off = paperButton(c.file, true, status);
      off.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); }); // not a toggle
      summary.appendChild(off);
    }
    box.appendChild(summary);
    const body = mk("div", "res-draft-body");
    box.appendChild(body);
    const fill = async () => {
      body.replaceChildren(mk("p", "empty-note", t("载入中…")));
      let d;
      try {
        d = await apiGet("/api/results?draft=" + encodeURIComponent(c.file));
      } catch (err) {
        body.innerHTML = "";
        const p = mk("p", "trace-break");
        renderBlockingError(p, t("没能载入这份文稿的论断"), err);
        body.appendChild(p);
        return;
      }
      body.innerHTML = "";
      d.sections.forEach((g) => {
        body.appendChild(mk("div", "res-group-title", g.section ? g.title || "" : t("（不在任何章节下）")));
        const ul = mk("ul", "res-list");
        g.claims.forEach((cl) => {
          const li = mk("li", "res-row");
          const main = mk("div", "res-main");
          main.appendChild(link(cl.sentence || cl.id, "claim", () => openNode(cl.id), t("打开它的来龙去脉")));
          const where = [cl.number ? t("数字 {number}", { number: cl.number }) : "", cl.line ? t("第 {n} 行", { n: cl.line }) : ""].filter(Boolean).join(" · ");
          if (where) main.appendChild(mk("span", "node-sub-inline", where));
          li.append(main, stateLabel(cl.trace));
          ul.appendChild(li);
        });
        body.appendChild(ul);
      });
      if (d.total > d.shown) body.appendChild(mk("p", "node-sub", t("这份文稿共有 {total} 条，只列出了前 {shown} 条。", { total: d.total, shown: d.shown })));
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
    const few = (list) => list.slice(0, 3).map((p) => p.split("/").pop()).join(t("、")) + (list.length > 3 ? t(" 等 {n} 个", { n: list.length }) : "");
    if (f.type === "script") {
      if (f.reads && f.reads.length) lines.push(t("读取 {files}", { files: few(f.reads) }));
      if (f.writes && f.writes.length) lines.push(t("写出 {files}", { files: few(f.writes) }));
      if (!lines.length) lines.push(t("未识别到读写关联"));
    } else {
      if (f.written_by && f.written_by.length) lines.push(t("由这些脚本写出：{files}", { files: few(f.written_by) }));
      if (f.read_by && f.read_by.length) lines.push(t("被这些脚本读取：{files}", { files: few(f.read_by) }));
      if (!lines.length) lines.push(t("未识别到读写关联"));
    }
    return lines;
  }

  function fileRow(f) {
    const li = mk("li", "dc-row");
    const kind = mk("span", "dc-kind");
    kind.append(icon(f.type), mk("span", null, t(f.type_label)));
    const main = mk("div", "dc-main");
    const head = mk("div", "dc-head");
    head.appendChild(link(f.path, "mono", () => openNode(f.id), t("打开它的来龙去脉")));
    if (f.exists === false) head.appendChild(mk("span", "status-label tone-clay", t("文件不存在")));
    if (f.unreadable) head.appendChild(mk("span", "status-label tone-clay", t("暂时无法读取")));
    if (f.orphan_input) head.appendChild(mk("span", "status-label tone-clay", t("未识别生成脚本")));
    if (f.review) {
      const tag = mkButton(t("{n} 条关联待复核", { n: f.review }), "review-tag", () => { if (mayReplace()) openReviewPanel(null); });
      tag.title = t("打开待复核的连线确认");
      head.appendChild(tag);
    }
    const own = ownMark(f);
    if (own) head.appendChild(own);
    main.appendChild(head);
    fileRelations(f).forEach((t) => main.appendChild(mk("div", "dc-rel", t)));
    (f.broken || []).forEach((b) => {
      // 8.8's glossary: the brackets read 「（写出，文件不存在）」 or 「（读取，文件不存在）」, by the code (13.6).
      const words = { script: b.script || "", relation: t(b.relation), target: b.target || "" };
      main.appendChild(mk("div", "dc-rel bad", b.type === "writes"
        ? t("断链：{script} {relation} {target}（写出，文件不存在）", words)
        : t("断链：{script} {relation} {target}（读取，文件不存在）", words)));
    });
    if (f.other_copies && f.other_copies.length) main.appendChild(mk("div", "dc-rel", t("其他同名文件：{files}", { files: f.other_copies.join(t("、")) })));
    li.append(kind, main);
    return li;
  }

  async function loadDataCode(host, refresh) {
    const epoch = state.dataEpoch;
    if (!refresh) host.replaceChildren(mk("p", "empty-note", t("载入中…")));
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
    if (data.outputs) renderRegisteredOutputs(host, data.outputs);
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", t("筛选"));
    const counts = Object.assign({}, data.counts || {});
    counts.chains = (data.files || []).filter((f) => inFilter(f, "chains")).length;
    const list = mk("ul", "dc-list");
    const draw = () => {
      list.innerHTML = "";
      const shown = (data.files || []).filter((f) => inFilter(f, dc.filter));
      if (!shown.length) list.appendChild(mk("li", "node-empty", dc.filter === "all" ? t("还没有扫描到脚本或数据文件。") : t("没有。")));
      shown.forEach((f) => list.appendChild(fileRow(f)));
      bar.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === dc.filter)));
    };
    filters().forEach(([key, text]) => {
      const n = key === "all" ? counts.all : counts[key];
      const b = mkButton(text + " " + (n || 0), "dc-filter", () => { dc.filter = key; draw(); });
      b.dataset.filter = key;
      bar.appendChild(b);
    });
    const stata = mk("div", "dc-stata-slot");
    host.append(stata, bar, list);
    draw();
    host.dataset.loaded = "1";
    if (refresh) window.scrollTo(0, top);
    loadStata(stata, host);
  }

  // -- Stata globals and the project folders they stand for (DESIGN.md 15.3) --
  // Research OS proposes, with its evidence (the engine's sentence, in the
  // page's language); the PI or a single researcher answers 「是」 or 「不是」,
  // a member sees the proposal; the paths 展开不了 are listed by reason.

  function stataFolder(folder) {
    return folder === "." ? t("项目文件夹本身") : t("项目里的文件夹 {folder}", { folder: String(folder || "") + "/" });
  }

  function stataReasonWords(code) {
    if (code === "undefined") return t("全局变量没有定义");
    if (code === "outside") return t("指向项目以外的位置");
    if (code === "reassigned") return t("被赋了不同的值");
    if (code === "not_literal") return t("不是写定的值");
    if (code === "loop") return t("在循环里拼出");
    if (code === "cd") return t("改变工作文件夹的 cd 无法跟随");
    if (code === "working_folder") return t("工作文件夹无法确定");
    if (code === "two_folders") return t("从两个工作文件夹运行");
    if (code === "cycle") return t("do 文件相互运行");
    return t("展开不了");
  }

  async function stataAnswer(host, name, folder, status, buttons) {
    buttons.forEach((b) => { b.disabled = true; });
    setStatus(status, "", t("正在记下回答…"));
    try {
      await apiPost("/api/stata/answer", { global: name, folder: folder });
    } catch (err) {
      buttons.forEach((b) => { b.disabled = false; });
      setStatus(status, "err", (err && err.engineMessage) || t("没能记下这个回答"), err);
      return;
    }
    loadDataCode(host, true);
  }

  function stataAnswerButtons(host, s, name, folder, item) {
    const status = mk("div", "notice-status");
    if (!s.may_answer) {
      if (s.refusal) item.appendChild(mk("div", "tl-meta", s.refusal));
      return;
    }
    const row = mk("div", "tl-actions");
    const buttons = [];
    if (folder != null) buttons.push(mkButton(t("是"), "btn btn-small", () => stataAnswer(host, name, folder, status, buttons)));
    buttons.push(mkButton(t("不是"), "btn btn-small", () => stataAnswer(host, name, null, status, buttons)));
    const blocked = humanRecordsBlocked();
    buttons.forEach((b) => { if (blocked) { b.disabled = true; b.title = blocked; } row.appendChild(b); });
    item.append(row, status);
  }

  function renderStata(slot, host, s) {
    slot.replaceChildren();
    if (!s || !s.has_do) return;
    const proposals = s.proposals || [], confirmed = s.confirmed || [], groups = s.unresolved || [];
    const untrusted = s.record && s.record.state !== "ok";
    if (!proposals.length && !confirmed.length && !groups.length && !untrusted) return;
    const box = mk("section", "dc-stata");
    box.appendChild(mk("h3", "dc-stata-title", t("Stata 全局变量")));
    if (untrusted) {
      const line = mk("p", "dc-rel bad");
      renderBlockingError(line, s.record.message || t("全局变量记录文件当前无法读取"), s.record.detail || "");
      box.appendChild(line);
    }
    proposals.forEach((p) => {
      const item = mk("div", "dc-stata-item");
      item.appendChild(mk("p", "dc-stata-sentence", p.sentence || ""));
      if (p.answer && p.answer.answer === "no") item.appendChild(mk("div", "tl-meta", t("上次的回答是「不是」（{who}）", { who: p.answer.by || "" })));
      stataAnswerButtons(host, s, p.global, p.folder, item);
      box.appendChild(item);
    });
    confirmed.forEach((c) => {
      const item = mk("div", "dc-stata-item");
      item.appendChild(mk("p", "dc-stata-sentence", t("{global} 已确认指{folder}", { global: "$" + c.global, folder: stataFolder(c.folder) })));
      if (c.by) item.appendChild(mk("div", "tl-meta", t("回答的人：{who}", { who: c.by })));
      stataAnswerButtons(host, s, c.global, null, item);
      box.appendChild(item);
    });
    if (groups.length) {
      const total = groups.reduce((n, g) => n + (g.items || []).length, 0);
      const details = mk("details", "dc-stata-unresolved");
      const summary = mk("summary", "res-draft-summary"); // 12.13: the quiet chevron, not the browser's triangle
      summary.append(chevron(), mk("span", null, t("展开不了（{n}）", { n: total })));
      details.appendChild(summary);
      groups.forEach((g) => {
        details.appendChild(mk("div", "dc-stata-reason", t("{reason}（{n}）", { reason: stataReasonWords(g.reason), n: (g.items || []).length })));
        const ul = mk("ul", "dc-stata-list");
        (g.items || []).forEach((i) => {
          const li = mk("li", "tl-meta");
          li.append(mk("span", "mono", String(i.file || "")), " ", t("第 {n} 行", { n: i.line }), " ",
            mk("span", "mono", (String(i.command || "") + " " + String(i.text || "")).trim()));
          ul.appendChild(li);
        });
        details.appendChild(ul);
      });
      box.appendChild(details);
    }
    slot.appendChild(box);
  }

  async function loadStata(slot, host) {
    const epoch = state.dataEpoch;
    let s;
    try {
      s = await apiGet("/api/stata");
    } catch (err) {
      if (epoch !== state.dataEpoch) return;
      const line = mk("p", "dc-rel bad");
      renderBlockingError(line, t("没能读取 Stata 全局变量"), err);
      slot.replaceChildren(line);
      return;
    }
    if (epoch !== state.dataEpoch) return;
    renderStata(slot, host, s);
  }

  window.RONode = Object.freeze({
    openNode, back, section, loadNode, loadResults, loadDataCode, current: () => nav.id, diffBlock,
    // the desk's side panel (14.3): an attempt's 来龙去脉, and its lines drawn once it shows
    renderTrace: renderTraceSection, drawTraceLines: drawAllLines,
    // and its timeline of what changed since the last visit (14.5): a row's move, where a version came from
    verdictMove, sourceWords,
    // pure, for tests/test_v8_u3.py
    _diffRows: diffRows, _traceTone: traceTone, _travelsWords: travelsWords, _linkSentence: linkSentence, _linkStateWords: linkStateWords,
    _judgeActions: judgeActions, _linkRow: linkRow, _mappingEnds: mappingEnds, _noteIsMine: noteIsMine, _verdictMove: verdictMove,
    _tableWords: tableWords, _timeline: timeline, _when: when, _timeKey: timeKey, _inFilter: inFilter, _fileRelations: fileRelations, _sourceWords: sourceWords,
    _renderStata: renderStata, _stataReasonWords: stataReasonWords, _judgmentSentence: judgmentSentence,
  });
})();
