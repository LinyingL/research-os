// The sidebar's live lines and the desk (工作台) -- DESIGN.md 12.9, 12.11 and
// Section 14 (task V10: the desk is the route map). Served verbatim at
// /desk.js by the same process and loaded by app.html with a plain
// same-origin <script src>, exactly as /canvas.js is: zero build, zero
// external resources, same origin check.
//
// It defines one global, window.RODesk, and calls app.html's helpers (mk,
// mkButton, apiGet, apiPost, renderBlockingError, renderViewFailure,
// setStatus, ownMark, openReviewPanel, RONode.openNode, ROTeam.openProposal,
// state, ...) and /routemap.js's window.RORouteMap only at run time, never
// while loading.
//
// What it reads (all GET, all origin-checked by the server):
//   /api/me         -- who I am on this machine (the sidebar's last line);
//   /api/share      -- the shared drive's state (the sidebar's status line);
//   /api/proposals  -- the quiet count beside 提议 and the shell's mode;
//   /api/desk       -- the route map's cards, lines and arrangement, and the
//                      column beside it (researchos.desk.payload).
// and the one write: POST /api/share/sync, 「同步」 a member's working copy.
//
// The desk is the route map with what waits for me beside it (14.1, 14.4):
// the PI's 等你批准, a member's 我的提议, 待复核 under the attempt it
// belongs to, a member's 官方版本有更新 with 同步, the lines with a missing
// end -- or one quiet line. What changed since I last looked is 「有改动」
// on the cards, abandoned routes are the quieter cards (14.5). No
// statistics, no charts, no counters for their own sake. Nothing typed on
// the page ever becomes a filesystem path: every path shown comes from the
// server.
//
// 12.11: no emoji, no arrow characters, verdicts as text status labels (the
// server's verdict_status, mapped from the researcher's own markers), the
// researcher's own words shown as written. 13.6: every word through t().
(function () {
  "use strict";

  // The words for the screen (DESIGN.md 13.6): /i18n.js's table, looked up
  // at call time; without it (this file evaluated alone) the Chinese.
  function fillVars(s, v) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return v && Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }); }
  function t(s, v) { var I = typeof window !== "undefined" && window.ROI18N; return I ? I.t(s, v) : fillVars(s, v); }

  // -- pure wording (loaded into node by tests/test_v8_u2.py) ------------------

  // A proposal's kind as its label, or null for a kind this page does not know.
  function kindWord(kind) {
    switch (kind) {
      case "attempt_new": return t("新尝试");
      case "verdict_change": return t("判决变更");
      case "variable_version": return t("变量定义");
      case "files": return t("文件");
      case "route_link": return t("连线");
      default: return null;
    }
  }

  // How a proposal stands, as its author reads it.
  function stateWord(state) {
    switch (state) {
      case "pending": return t("等待 PI 批准");
      case "conflict": return t("批准与退回的记录互相矛盾");
      case "approved": return t("已批准");
      case "returned": return t("已退回");
      case "superseded": return t("已被你后来的提议替代");
      default: return String(state || "");
    }
  }

  // The six default verdict words and 其它 by status code (ingest/attempts.py
  // VERDICT_STATUSES), each with its words for the screen.
  function defaultVerdict(code) {
    switch (code) {
      case "current": return ["现行", t("现行")];
      case "abandoned": return ["已放弃", t("已放弃")];
      case "pending": return ["待定", t("待定")];
      case "adverse": return ["不利证据", t("不利证据")];
      case "rejected": return ["被否", t("被否")];
      case "archived": return ["归档", t("归档")];
      case "other": return ["其它", t("其它")];
      default: return null;
    }
  }

  // A verdict's word (13.4): a default status in the screen's language, by
  // its code; a word the researcher named in [verdict_labels] as written --
  // status labelled, or an abandoned route under their own word.
  function verdictWord(status) {
    if (!status || !status.label) return t("其它");
    const d = defaultVerdict(status.status);
    return d && d[0] === status.label ? d[1] : status.label;
  }

  function english() { const I = typeof window !== "undefined" && window.ROI18N; return !!I && I.lang === "en"; }

  // A number as 13.5 writes it: 1,497 in English; in Chinese as before.
  function numberText(n) {
    const s = String(n);
    return english() && /^\d{4,}$/.test(s) ? s.replace(/\B(?=(\d{3})+$)/g, ",") : s;
  }

  // Names in a row: 「、」 in Chinese, a comma in English. (Not `list`: the
  // render functions keep a local of that name.)
  function series(xs) { return xs.join(t("、")); }

  const VERDICT_TONES = {
    current: "olive", abandoned: "gray", pending: "ochre", adverse: "clay",
    rejected: "plain", archived: "plain", labelled: "plain", other: "plain",
  };
  const DESK_DAYS = 7;

  function pad2(n) { return String(n).padStart(2, "0"); }

  // When something happened, as a person says it: 今天 14:02, 昨天 09:10,
  // 10月4日, 2025年10月4日; in English (13.5) Today 14:02, Yesterday 09:10,
  // 4 Oct, 4 Oct 2025. `now` is a Date (the tests pass one).
  function whenWords(at, now) {
    const d = new Date(String(at || ""));
    if (isNaN(d.getTime())) return "";
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const days = Math.round((today - day) / 86400000);
    const time = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    if (days === 0) return t("今天 {time}", { time: time });
    if (days === 1) return t("昨天 {time}", { time: time });
    const month = [t("1月"), t("2月"), t("3月"), t("4月"), t("5月"), t("6月"),
      t("7月"), t("8月"), t("9月"), t("10月"), t("11月"), t("12月")][d.getMonth()];
    return d.getFullYear() === now.getFullYear() ? t("{month}{day}日", { month: month, day: d.getDate() })
      : t("{year}年{month}{day}日", { year: d.getFullYear(), month: month, day: d.getDate() });
  }

  // A date inside a line starts lower case (13.5's forms start a line in
  // English; Chinese has no case).
  function whenInLine(at, now) {
    const w = whenWords(at, now);
    return w.charAt(0).toLowerCase() + w.slice(1);
  }

  function toneOf(status) {
    const s = status && status.status;
    return Object.prototype.hasOwnProperty.call(VERDICT_TONES, s) ? VERDICT_TONES[s] : "plain";
  }

  // The per-person, per-project key of "when I last looked at the desk".
  function seenKey(project, me) {
    return "ro.desk.seen:" + String(project || "") + ":" + String(me || "-");
  }

  function nameOf(id, team) {
    const m = (team || []).find((x) => x.id === id);
    return m && m.name ? m.name : String(id || "");
  }

  // The shared drive's status line (12.9), or "" for a project without one:
  // its parts joined by " · ". A date inside the line starts lower case
  // (13.5's forms start a line in English; Chinese has no case).
  function shareLineText(share, now) {
    if (!share || !share.shared) return "";
    if (!share.reachable) return t("公盘现在打不开");
    const last = share.last_snapshot;
    const unverified = share.verification && share.verification !== "verified";
    const when = (at) => whenInLine(at, now);
    if (share.role === "pi") {
      if (!last) return t("还没有发布过");
      const pending = share.pending_publish && share.pending_publish.count > 0;
      return (pending ? [t("有改动尚未发布")] : []).concat(t("已发布 {when}", { when: when(last.at) })).join(" · ");
    }
    if (share.joined) {
      if (share.refused && share.refused.length) return t("公盘上有未通过验证的版本");
      if (share.behind) return t("有新的官方版本");
      const synced = share.joined.synced;
      return (unverified ? [t("未验证")] : [])
        .concat(synced ? t("已同步 {when}", { when: when(synced) }) : t("已同步")).join(" · ");
    }
    return "";
  }

  // A proposal in one line: what approving it would do.
  function proposalWhat(p) {
    const files = Array.isArray(p.files) ? p.files : [];
    const filesPart = files.length ? t("，附带 {n} 个文件", { n: numberText(files.length) }) : "";
    if (p.kind === "attempt_new" && p.attempt) {
      const a = p.attempt;
      return t("新增尝试 #{number}：{description}", { number: String(a.number || "").replace(/^#/, ""),
        description: a.description || t("（无描述）") }) + filesPart;
    }
    if (p.kind === "verdict_change" && p.verdict) {
      const v = p.verdict;
      return t("#{number} 的判决从「{was}」改为「{became}」", { number: String(v.number || "").replace(/^#/, ""),
        was: v.old_status ? verdictWord(v.old_status) : "", became: v.new_status ? verdictWord(v.new_status) : "" }) +
        (v.abandons ? t("，放弃这条路线") : "") + filesPart;
    }
    if (p.kind === "route_link" && p.route) {
      const ends = { child: p.route.child_number || "", parent: p.route.parent_number || "" };
      return p.route.op === "remove" ? t("删去连线：#{child} 源自 #{parent}", ends) : t("加上连线：#{child} 源自 #{parent}", ends);
    }
    if (p.kind === "variable_version" && p.variable) {
      return t("变量 {card} 的第 {version} 版定义", { card: p.variable.card || "", version: p.variable.version || "" }) +
        filesPart;
    }
    if (files.length) {
      const names = series(files.slice(0, 2).map((f) => String(f.path || "").split("/").pop()));
      return files.length > 2 ? t("{n} 个文件：{names} 等", { n: numberText(files.length), names: names })
        : t("{n} 个文件：{names}", { n: numberText(files.length), names: names });
    }
    return "";
  }

  function lastOutcome(p, act) {
    const outs = (p.outcomes || []).filter((o) => o.act === act);
    return outs.length ? outs[outs.length - 1] : null;
  }

  // How a member reads the state of one of their own proposals.
  function proposalStateText(p) {
    if (p.state === "returned") {
      const o = lastOutcome(p, "returned");
      if (o && o.text) return t("已退回：{reason}", { reason: o.text });
    }
    if (p.state === "pending") {
      const talk = lastOutcome(p, "discussed") || lastOutcome(p, "discuss");
      if (talk && talk.text) return t("等待 PI 批准 · PI 说：{text}", { text: talk.text });
    }
    return stateWord(p.state);
  }

  // -- runtime --------------------------------------------------------------------

  const ctx = { me: null, share: null, proposals: null, mode: null, project: null, epoch: -1 };
  const sessionSince = new Map(); // project key -> the last-seen time this page session shows from

  function now() { return new Date(); }

  function projectKey() {
    return state.currentId || state.currentPath || "";
  }

  function meId() {
    return ctx.me && ctx.me.me ? ctx.me.me.id : null;
  }

  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (err) { return null; }
  }

  function storageSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch (err) { /* a convenience only */ }
  }

  // The time this session's desk counts changes from: what the browser
  // remembered for this person and project (read once per session, then
  // replaced by now, so the next session starts from this visit); null --
  // the engine's default of seven days -- when nothing was remembered.
  function deskSince() {
    const key = seenKey(projectKey(), meId());
    if (!sessionSince.has(key)) {
      const stored = storageGet(key);
      const ok = stored && !isNaN(new Date(stored).getTime()) ? stored : null;
      sessionSince.set(key, ok);
      storageSet(key, new Date().toISOString());
    }
    return sessionSince.get(key);
  }

  function statusLabel(status) {
    const span = mk("span", "status-label tone-" + toneOf(status), verdictWord(status));
    return span;
  }

  // A verdict as 12.11 shows it: the label, then the researcher's own words.
  function verdictInline(status) {
    const wrap = mk("span", "verdict-inline");
    wrap.appendChild(statusLabel(status));
    if (status && status.words) wrap.appendChild(mk("span", "verdict-words", status.words));
    return wrap;
  }

  async function fetchContext() {
    const epoch = state.dataEpoch;
    const [me, proposals] = await Promise.all([
      apiGet("/api/me").catch(() => null),
      apiGet("/api/proposals").catch(() => null),
    ]);
    const role = proposals && proposals.role;
    const share = role === "pi" || role === "member" || role === "none"
      ? await apiGet("/api/share").catch(() => null) : null;
    if (epoch !== state.dataEpoch) return false;
    ctx.me = me;
    ctx.proposals = proposals;
    ctx.share = share;
    ctx.mode = role === "pi" || role === "member" || role === "none" ? role : "single";
    ctx.project = projectKey();
    ctx.epoch = epoch;
    return true;
  }

  // The sidebar's live parts: the quiet count beside 提议 when something
  // waits for me, the shared drive's line, who I am.
  async function refreshSidebar() {
    if (state.noProject) {
      // Who I am is known without a project (U4: set while joining).
      ctx.me = await apiGet("/api/me").catch(() => ctx.me);
      renderSidebar();
      return;
    }
    try {
      await fetchContext();
    } catch (err) {
      /* the lines keep what they last said */
    }
    renderSidebar();
  }

  function waitingForMe() {
    const list = (ctx.proposals && ctx.proposals.proposals) || [];
    if (ctx.mode === "pi") return list.filter((p) => p.state === "pending" || p.state === "conflict").length;
    return 0; // a member's proposals wait on the PI, not on them
  }

  function renderSidebar() {
    const count = document.getElementById("side-count-proposals");
    const shareLine = document.getElementById("share-line");
    const meLine = document.getElementById("me-line");
    if (!count || !shareLine || !meLine) return;
    // The shell's 文件 menu shows 提交提议… or 发布 by role (V8 U5).
    if (typeof postShellMode === "function") postShellMode(state.noProject ? "single" : ctx.mode);
    const n = state.noProject ? 0 : waitingForMe();
    count.textContent = n > 0 ? String(n) : "";
    count.title = n > 0 ? t("{n} 条提议等你批准", { n: n }) : "";
    const line = state.noProject ? "" : shareLineText(ctx.share, now());
    shareLine.textContent = line;
    shareLine.classList.toggle("hidden", !line);
    shareLine.title = ctx.share && ctx.share.shared ? t("公盘：{folder}", { folder: ctx.share.shared }) : "";
    meLine.textContent = "";
    // U4: a member proposes a new attempt; someone not in the team yet
    // cannot propose at all.
    const add = document.getElementById("new-attempt-btn");
    if (add) {
      add.textContent = ctx.mode === "member" ? t("提议新的尝试") : t("新增尝试");
      add.dataset.role = ctx.mode || "";
    }
    const me = ctx.me && ctx.me.me;
    if (me) {
      meLine.append(mk("span", "me-name", me.name || me.id));
      // The role word (13.4): PI stays PI; 成员 as a role is the singular.
      const role = ctx.mode === "pi" ? "PI" : ctx.mode === "member" ? t("成员", { n: 1 })
        : ctx.mode === "none" ? t("尚未加入团队") : "";
      if (role && !state.noProject) meLine.append(mk("span", "me-role", role));
      meLine.title = me.id || "";
    } else {
      meLine.append(mk("span", "me-unset", t("还没有设置你是谁")));
      meLine.title = t("在「成员」一栏里设置");
    }
  }

  // -- the desk: the route map and the column beside it (DESIGN.md 14.1, 14.4, 14.5) -------
  // Both are made once and kept: a refresh hands the map the new payload and
  // redraws the column's contents in place, so the map keeps its camera, its
  // selection and a drag in progress, and the column its scroll. Without
  // /routemap.js (this file run alone) the column alone.

  function block(title, cls) {
    const sec = mk("section", "desk-block" + (cls ? " " + cls : ""));
    sec.appendChild(mk("h2", "desk-title", title));
    return sec;
  }

  function emptyLine(text) {
    return mk("p", "desk-empty", text);
  }

  function rowLink(text, cls, onClick, title) {
    const b = mkButton(text, "desk-link" + (cls ? " " + cls : ""), onClick);
    if (title) b.title = title;
    return b;
  }

  // A click on a card opens it: its node page (12.9) until the panel (14.3).
  function openAttempt(id) {
    RONode.openNode(id);
  }

  function attemptName(item) {
    const num = item.number != null && item.number !== "" ? "#" + String(item.number).replace(/^#/, "") : "";
    return [num, item.title || ""].filter(Boolean).join(" ");
  }

  function metaLine(parts) {
    return mk("div", "desk-meta", parts.filter(Boolean).join(" · "));
  }

  let routeMap = null;
  let layoutEl = null;
  let columnEl = null;
  let drawnEpoch = -1;          // the project (state.dataEpoch) the desk last showed
  let picked = null;            // the column's row picked: { key, ids }
  const drawnKeys = new Set();  // the rows the column shows now

  // A row of the column picked (14.4): the attempts it touches are marked on
  // the map and brought into view; picked again, unmarked.
  function pick(key, ids) {
    picked = picked && picked.key === key ? null : { key: key, ids: ids };
    columnEl.querySelectorAll("[data-pick]").forEach((r) => r.classList.toggle("picked", !!picked && r.dataset.pick === picked.key));
    if (routeMap) routeMap.highlight(picked ? picked.ids : []);
  }

  function pickLink(row, text, key, ids) {
    row.dataset.pick = key;
    drawnKeys.add(key);
    if (picked && picked.key === key) row.classList.add("picked");
    return rowLink(text, "strong", () => pick(key, ids), ids.length ? t("在路线图上标出它涉及的尝试") : "");
  }

  function listBlock(host, title, rows) {
    if (!rows.length) return false;
    const sec = block(title);
    const ul = mk("ul", "desk-list");
    rows.forEach((r) => ul.appendChild(r));
    sec.appendChild(ul);
    host.appendChild(sec);
    return true;
  }

  // How a member's own proposal stands (14.4): 提议中 (with what the PI
  // said), 已退回 with the reason, 已批准，待同步.
  function mineState(p) {
    if (p.state === "pending") {
      const talk = lastOutcome(p, "discussed") || lastOutcome(p, "discuss");
      return talk && talk.text ? t("提议中 · PI 说：{text}", { text: talk.text }) : t("提议中");
    }
    if (p.state === "returned") return p.reason ? t("已退回：{reason}", { reason: p.reason }) : t("已退回");
    return p.state === "approved" ? t("已批准，待同步") : stateWord(p.state);
  }

  // What waits in 我的提议: a proposal pending or returned (its reason
  // stays), or approved while something it carries is not synced yet.
  function waits(p) {
    return p.state === "pending" || p.state === "conflict" || p.state === "returned" || !!p.awaiting_sync;
  }

  // A proposal in the column (14.4): what approving it would do, who and
  // when -- or, for its author, how it stands. Its title marks the attempts
  // it touches; 「查看」 opens its page (in the panel once there is one, 14.3).
  function proposalRow(p, forPi) {
    const row = mk("li", "desk-row");
    const main = mk("div", "desk-main");
    const head = mk("div", "desk-head");
    const what = proposalWhat(p);
    head.appendChild(mk("span", "status-label tone-plain", kindWord(p.kind) || t("提议", { n: 1 })));
    head.appendChild(pickLink(row, p.sentence || what || p.id, "p:" + p.id, p.touches || []));
    main.appendChild(head);
    if (what && p.sentence) main.appendChild(mk("p", "desk-note plain", what));
    const state = forPi ? (p.state === "conflict" ? stateWord(p.state) : "") : mineState(p);
    main.appendChild(metaLine([forPi ? p.by_label || p.by : "", state, whenWords(p.at, now())]));
    const actions = mk("div", "desk-actions");
    actions.appendChild(mkButton(t("查看"), "btn btn-small", () => ROTeam.openProposal(p.id, p.by)));
    main.appendChild(actions);
    row.appendChild(main);
    return row;
  }

  // A link's relation in words (12.11), never its type.
  function relationWord(type) {
    switch (type) {
      case "reads": return t("读取");
      case "writes": return t("写出");
      case "generates": return t("生成");
      case "includes": case "cites": return t("引用");
      case "belongs_to": return t("属于");
      case "backed_by": return t("依据");
      case "produces": return t("产出");
      case "implements": return t("实现");
      case "uses": return t("使用");
      case "supports": return t("支持");
      default: return t("关联");
    }
  }

  function reviewItems(items, cards) {
    const ul = mk("ul", "desk-items");
    items.forEach((i) => {
      const li = mk("li");
      if (i.label) li.appendChild(mk("span", "desk-reason", i.label));
      li.append([i.src_label || i.src, relationWord(i.type), i.dst_label || i.dst].join(" "));
      ul.appendChild(li);
    });
    cards.forEach((g) => {
      const li = mk("li");
      if (g.script) li.append(mk("span", "mono", g.script), " ");
      li.append(g.words || series(g.cards || []));
      ul.appendChild(li);
    });
    return ul;
  }

  // 待复核 (14.4): the judgments whose basis changed, under the attempt each
  // belongs to (14.3's membership rule), else under 「不属于任何尝试」; one
  // two attempts hold is under both and counted once (the engine's count).
  function renderReview(host, review, byId) {
    if (!review || !review.count) return false;
    const sec = block(t("待复核"));
    const sub = mk("p", "desk-sub", t("{n} 条判断的依据变了，等你复核。", { n: numberText(review.count) }));
    sub.appendChild(rowLink(t("复核"), "", () => openReviewPanel(null)));
    sec.appendChild(sub);
    const ul = mk("ul", "desk-list");
    (review.groups || []).forEach((g) => {
      const row = mk("li", "desk-row");
      const main = mk("div", "desk-main");
      const head = mk("div", "desk-head");
      head.appendChild(pickLink(row, attemptName(byId.get(g.id) || {}) || g.id, "r:" + g.id, [g.id]));
      const own = ownMark(g);
      if (own) head.appendChild(own);
      if (g.items.length) head.appendChild(rowLink(t("复核"), "", () => openReviewPanel(g.items[0])));
      main.append(head, reviewItems(g.items, []));
      row.appendChild(main);
      ul.appendChild(row);
    });
    const none = review.none || {};
    if ((none.items || []).length || (none.cards || []).length) {
      const row = mk("li", "desk-row");
      const main = mk("div", "desk-main");
      main.append(mk("div", "desk-head", t("不属于任何尝试")), reviewItems(none.items || [], none.cards || []));
      row.appendChild(main);
      ul.appendChild(row);
    }
    sec.appendChild(ul);
    host.appendChild(sec);
    return true;
  }

  // 「连线的一端找不到了」 (14.2): a line whose end the table no longer has
  // (a renumbered row) waits in the record; it is said here, never dropped.
  function renderMissing(host, missing) {
    return listBlock(host, t("连线的一端找不到了"), (missing || []).map((l) => {
      const row = mk("li", "desk-row");
      const main = mk("div", "desk-main");
      const head = mk("div", "desk-head");
      head.appendChild(mk("span", "desk-what", t("#{child} 源自 #{parent}", { child: l.child_number, parent: l.parent_number })));
      const own = ownMark(l);
      if (own) head.appendChild(own);
      main.appendChild(head);
      const gone = (l.missing || []).map((end) => t("年表里没有 #{n}", { n: end === "parent" ? l.parent_number : l.child_number }));
      main.appendChild(metaLine(gone.concat(l.by_label || "")));
      if (l.note) main.appendChild(mk("p", "desk-note", l.note));
      row.appendChild(main);
      return row;
    }));
  }

  // What the last 同步 from the desk did: kept on the desk until it is
  // opened afresh, because the sync itself moves the generation and the
  // desk is drawn again at once.
  let syncShown = null;

  function renderSyncShown(sec) {
    if (!syncShown) return;
    const done = mk("div", "desk-sync-done team-flash");
    syncShown.forEach((f) => {
      done.appendChild(mk("p", "team-done", f.text));
      if (f.diff || f.summary) done.appendChild(ROTeam.differenceBlock(f, true));
    });
    sec.appendChild(done);
  }

  // 官方版本有更新 and 同步 (14.4, a member), as the desk had them: what 同步
  // will write is said first (the engine's sync-preview), and the button
  // waits for it; after a sync, what it did. A shared folder not set or not
  // open is the column's notice instead.
  function renderOfficial(host, sync) {
    const last = sync && sync.last_snapshot;
    const behind = !!(sync && sync.shared && sync.reachable && sync.behind && last);
    if (!behind && !syncShown) return false;
    const sec = block(behind ? t("官方版本有更新") : t("官方版本"));
    if (behind) {
      const when = whenInLine(last.at, now());
      sec.appendChild(mk("p", "desk-sub", last.note
        ? t("PI 于 {when} 发布了第 {seq} 版：{note}", { when: when, seq: numberText(last.seq), note: last.note })
        : t("PI 于 {when} 发布了第 {seq} 版。", { when: when, seq: numberText(last.seq) })));
      const writes = mk("p", "team-line writes", t("正在查看同步会写入什么…"));
      const status = mk("div", "notice-status");
      const btn = mkButton(t("同步"), "btn", () => syncNow(btn, status));
      btn.disabled = true;
      const actions = mk("div", "desk-actions");
      actions.append(btn);
      sec.append(writes, actions, status);
      apiGet("/api/share/sync-preview").then((p) => {
        writes.textContent = ROTeam.syncWrites(p);
        btn.disabled = !!p.untrusted;
      }).catch((err) => {
        setStatus(writes, "err", t("没能查看同步会写入什么"), err);
        btn.disabled = false;
      });
    } else if (last) {
      sec.appendChild(emptyLine(t("你的工作副本已经是最新的官方版本（第 {seq} 版）。", { seq: numberText(last.seq) })));
    }
    renderSyncShown(sec);
    host.appendChild(sec);
    return true;
  }

  async function syncNow(btn, status) {
    btn.disabled = true;
    setStatus(status, "", t("正在同步…"));
    let res;
    try {
      res = await apiPost("/api/share/sync", {});
    } catch (err) {
      btn.disabled = false;
      setStatus(status, "err", t("没能同步，工作副本没有改动"), err);
      return;
    }
    setStatus(status, "", "");
    // The sentence counts what the engine reports (team.js syncDone), each
    // .official copy with its difference; the desk is drawn again with it.
    syncShown = ROTeam.syncFlash(res);
    if (typeof markViewsStale === "function") markViewsStale();
    await loadDesk(views.desk, true);
    const shown = views.desk.querySelector(".desk-sync-done");
    if (shown && typeof shown.scrollIntoView === "function") shown.scrollIntoView({ block: "center" });
  }

  // The column (14.4), newest first in each part: who I am not yet (or a
  // team file that cannot be read) and a shared folder that cannot be read,
  // said first; the PI's 等你批准 or a member's 我的提议; 待复核; a member's
  // 官方版本有更新; the lines with a missing end -- or one quiet line.
  function renderColumn(col, data) {
    const c = data.column || {};
    const byId = new Map((data.attempts || []).map((a) => [a.id, a]));
    const top = col.scrollTop;
    col.textContent = "";
    drawnKeys.clear();
    col.appendChild(mk("h2", "desk-column-title", t("待办")));
    const notices = [c.role === "none" ? t("你还不是这个项目的成员：请 PI 把你加入团队，然后同步") : c.notice,
      c.shared && c.shared.message].filter(Boolean);
    notices.forEach((n) => col.appendChild(mk("p", "desk-alone", n)));
    const shown = [
      listBlock(col, t("等你批准"), c.role === "pi" ? (c.waiting || []).map((p) => proposalRow(p, true)) : []),
      listBlock(col, t("我的提议"), c.role === "member" ? (c.mine || []).filter(waits).map((p) => proposalRow(p, false)) : []),
      renderReview(col, c.review, byId),
      c.role === "member" && renderOfficial(col, c.sync),
      renderMissing(col, data.lines_missing),
    ].some(Boolean);
    if (!shown && !notices.length) col.appendChild(emptyLine(t("现在没有要处理的事。")));
    if (picked && !drawnKeys.has(picked.key)) { // its row is gone: nothing stays marked
      picked = null;
      if (routeMap) routeMap.highlight([]);
    }
    col.scrollTop = top;
  }

  // The desk's two parts, made once, in `host`.
  function deskColumn(host) {
    if (!layoutEl) {
      layoutEl = mk("div", "desk-layout");
      if (window.RORouteMap) {
        const map = mk("div", "desk-map");
        routeMap = window.RORouteMap.create({ onOpen: openAttempt, onChanged: () => loadDesk(views.desk, true) });
        routeMap.mount(map);
        layoutEl.appendChild(map);
      }
      columnEl = mk("aside", "desk-column");
      layoutEl.appendChild(columnEl);
    }
    if (layoutEl.parentNode !== host) {
      host.textContent = "";
      host.appendChild(layoutEl);
    }
    return columnEl;
  }

  // 「有改动」 (14.5): the attempt's row or one of its files changed since the
  // last visit to the desk (deskSince, kept as before); with none remembered,
  // in the engine's default days.
  function changedSince(since) {
    const from = since ? new Date(since).getTime() : now().getTime() - DESK_DAYS * 86400000;
    return (a) => !!a.changed_at && new Date(a.changed_at).getTime() > from;
  }

  // The shell's 放大 / 缩小 / 实际大小 on the desk move the map's camera.
  function mapZoom(step) {
    if (!routeMap || !routeMap.visible()) return false;
    if (step > 0) routeMap.zoomIn();
    else if (step < 0) routeMap.zoomOut();
    else routeMap.zoomReset();
    return true;
  }

  // `refresh`: the project's generation moved. Gone to again (not a
  // refresh), the desk drawn for this project stays until the new reading
  // arrives; only a first visit, or another project's, says it is loading.
  async function loadDesk(host, refresh) {
    const epoch = state.dataEpoch;
    const drawn = !!layoutEl && layoutEl.parentNode === host && drawnEpoch === epoch;
    if (!refresh) {
      if (!drawn) host.innerHTML = '<p class="empty-note">' + t("载入中…") + "</p>";
      syncShown = null;
    }
    let data, since;
    try {
      // Who I am first (it keys "last seen"), then the rest side by side.
      if (!ctx.me || ctx.project !== projectKey()) ctx.me = await apiGet("/api/me").catch(() => null);
      since = deskSince();
      [, data] = await Promise.all([fetchContext(), apiGet("/api/desk")]);
    } catch (err) {
      if (epoch !== state.dataEpoch) return;
      renderViewFailure(drawn ? columnEl : host, err); // a desk drawn keeps its map
      return;
    }
    if (epoch !== state.dataEpoch) return;
    if (drawnEpoch !== epoch) picked = null;
    renderSidebar();
    renderColumn(deskColumn(host), data);
    if (routeMap) routeMap.update(data, { changed: changedSince(since) });
    drawnEpoch = epoch;
    host.dataset.loaded = "1";
  }

  // The context's freshness: fetched again once the project (epoch) moved.
  async function ensureContext() {
    if (ctx.epoch !== state.dataEpoch || ctx.project !== projectKey() || ctx.mode === null) {
      try { await fetchContext(); } catch (err) { /* the callers say what they cannot read */ }
    }
    return ctx;
  }

  function context() { return ctx; }

  window.RODesk = Object.freeze({
    loadDesk, refreshSidebar, renderSidebar, verdictInline, statusLabel, ensureContext, context, mapZoom,
    kindWord, stateWord, verdictWord, proposalWhat, proposalStateText, nameOf, whenWords,
    // pure, for tests/test_v8_u2.py
    _whenWords: whenWords, _shareLineText: shareLineText, _proposalWhat: proposalWhat,
    _proposalStateText: proposalStateText, _seenKey: seenKey, _toneOf: toneOf,
  });
})();
