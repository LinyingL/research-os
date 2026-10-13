// The sidebar's live lines and the desk (工作台) -- DESIGN.md 12.9, 12.11 and
// Section 14 (task V10: the desk is the route map). Served verbatim at
// /desk.js by the same process and loaded by app.html with a plain
// same-origin <script src>, exactly as /canvas.js is: zero build, zero
// external resources, same origin check.
//
// It defines one global, window.RODesk, and calls app.html's helpers (mk,
// mkButton, apiGet, apiPost, renderBlockingError, renderViewFailure,
// setStatus, ownMark, openReviewPanel, renderReviewItem, keyId,
// undoFromReview, RONode.openNode, ROTeam.renderProposalInto, state, ...)
// and /routemap.js's
// window.RORouteMap only at run time, never while loading.
//
// What it reads (all GET, all origin-checked by the server):
//   /api/me         -- who I am on this machine (the sidebar's last line);
//   /api/share      -- the shared drive's state (the sidebar's status line);
//   /api/proposals  -- the quiet count beside 提议 and the shell's mode;
//   /api/desk       -- the route map's cards, lines and arrangement, and the
//                      column beside it (researchos.desk.payload);
//   /api/review     -- an attempt's 待复核, judged in its panel (14.3);
//   /api/history    -- how its row moved, for the panel's timeline (14.5).
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

  // A day with no time -- a 「源自」 line's date, as mappings.toml writes it
  // (2026-10-13) -- in the same forms (13.5): 今天, 昨天, 10月13日,
  // 2025年10月13日; Today, Yesterday, 13 Oct, 13 Oct 2025. Read as that day
  // where the researcher is: never new Date(iso), which reads UTC midnight
  // and moves the day west of Greenwich; no hour is invented. Anything else
  // comes back as it is.
  function dayWords(at, now) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(at || ""));
    if (!m) return at == null ? "" : String(at);
    const day = new Date(+m[1], +m[2] - 1, +m[3]);
    const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - day) / 86400000);
    if (days === 0) return t("今天");
    if (days === 1) return t("昨天");
    const month = [t("1月"), t("2月"), t("3月"), t("4月"), t("5月"), t("6月"),
      t("7月"), t("8月"), t("9月"), t("10月"), t("11月"), t("12月")][day.getMonth()];
    return day.getFullYear() === now.getFullYear() ? t("{month}{day}日", { month: month, day: day.getDate() })
      : t("{year}年{month}{day}日", { year: day.getFullYear(), month: month, day: day.getDate() });
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

  // ... and of the returned proposals a member said 「知道了」 to (ruled
  // 2026-10-13), kept in this browser like the last visit.
  function gotItKey(project, me) {
    return "ro.desk.gotit:" + String(project || "") + ":" + String(me || "-");
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
  const gotItNow = new Map();     // gotItKey -> the returned proposals said 「知道了」 to (storage may be off)
  const GOT_IT_KEPT = 200;

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
  // the map and brought into view; picked again, unmarked. `on` true: marked
  // whatever it was (its proposal opened in the panel).
  function pick(key, ids, on) {
    picked = on || !(picked && picked.key === key) ? { key: key, ids: ids } : null;
    columnEl.querySelectorAll("[data-pick]").forEach((r) => r.classList.toggle("picked", !!picked && r.dataset.pick === picked.key));
    if (routeMap) routeMap.highlight(picked ? picked.ids : []);
  }

  // `title`: what marking it does, when the row is not a proposal's.
  function pickLink(row, text, key, ids, title) {
    row.dataset.pick = key;
    drawnKeys.add(key);
    if (picked && picked.key === key) row.classList.add("picked");
    return rowLink(text, "strong", () => pick(key, ids), ids.length ? title || t("在路线图上标出它涉及的尝试") : "");
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

  // What waits in 我的提议: a proposal pending, returned (its reason stays
  // until its author says 「知道了」), or approved while something it carries
  // is not synced yet.
  function waits(p) {
    if (p.state === "returned") return !gotIt().has(String(p.id));
    return p.state === "pending" || p.state === "conflict" || !!p.awaiting_sync;
  }

  function gotIt() {
    const key = gotItKey(projectKey(), meId());
    if (!gotItNow.has(key)) {
      let kept = [];
      try { kept = JSON.parse(storageGet(key) || "[]"); } catch (err) { kept = []; }
      gotItNow.set(key, new Set(Array.isArray(kept) ? kept.map(String) : []));
    }
    return gotItNow.get(key);
  }

  // 「知道了」 on a returned proposal: it leaves 我的提议 and the panel's 待办.
  function sayGotIt(p) {
    const ids = gotIt();
    ids.add(String(p.id));
    storageSet(gotItKey(projectKey(), meId()), JSON.stringify(Array.from(ids).slice(-GOT_IT_KEPT)));
    renderColumn(columnEl, lastData);
    const s = state.panelSubject;
    return s && s.kind === "desk-attempt" && panelShown() ? drawPanel(s) : null;
  }

  // A proposal in the column (14.4): what approving it would do, who and
  // when -- or, for its author, how it stands. Its title marks the attempts
  // it touches; 「查看」 opens it in the panel (14.3). In an attempt's panel
  // (`subject`), 「查看」 opens it in place, under its row.
  function proposalRow(p, forPi, subject) {
    const row = mk("li", "desk-row");
    const main = mk("div", "desk-main");
    const head = mk("div", "desk-head");
    const what = proposalWhat(p);
    const name = p.sentence || what || p.id;
    head.appendChild(mk("span", "status-label tone-plain", kindWord(p.kind) || t("提议", { n: 1 })));
    head.appendChild(subject ? mk("span", "desk-what", name) : pickLink(row, name, "p:" + p.id, p.touches || []));
    main.appendChild(head);
    if (what && p.sentence) main.appendChild(mk("p", "desk-note plain", what));
    const state = forPi ? (p.state === "conflict" ? stateWord(p.state) : "") : mineState(p);
    main.appendChild(metaLine([forPi ? p.by_label || p.by : "", state, whenWords(p.at, now())]));
    const actions = mk("div", "desk-actions");
    main.appendChild(actions);
    if (!subject) {
      actions.appendChild(mkButton(t("查看"), "btn btn-small", () => openProposal(p)));
      if (!forPi && p.state === "returned") actions.appendChild(mkButton(t("知道了"), "btn btn-small", () => sayGotIt(p)));
    } else {
      const key = (p.by || "") + "/" + p.id;
      const inner = mk("div", "dp-in-place");
      main.appendChild(inner);
      const open = () => { subject.open.add(key); actions.textContent = ""; openInPlace(subject, p, inner); };
      if (subject.open.has(key)) open();
      else actions.appendChild(mkButton(t("查看"), "btn btn-small", open));
    }
    row.appendChild(main);
    return row;
  }

  // A link's relation in words (12.11), never its type: nodepage.RELATION_WORDS'
  // in the screen's language (node.js's trace lines say it too).
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
      case "authored_by": return t("作者是");
      case "derives_from": return t("源自");
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
    sub.appendChild(rowLink(t("复核"), "", () => { if (mayLeavePanel()) openReviewPanel(null); }));
    sec.appendChild(sub);
    const ul = mk("ul", "desk-list");
    (review.groups || []).forEach((g) => {
      const row = mk("li", "desk-row");
      const main = mk("div", "desk-main");
      const head = mk("div", "desk-head");
      head.appendChild(pickLink(row, attemptName(byId.get(g.id) || {}) || g.id, "r:" + g.id, [g.id], t("在路线图上标出这条尝试")));
      const own = ownMark(g);
      if (own) head.appendChild(own);
      if (g.items.length) head.appendChild(rowLink(t("复核"), "", () => openAttempt(g.id, true))); // judged in its panel (14.3)
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
  function sinceTime(since) {
    return since ? new Date(since).getTime() : now().getTime() - DESK_DAYS * 86400000;
  }

  function changedSince(since) {
    const from = sinceTime(since);
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
    lastData = data;
    lastChanged = changedSince(since);
    lastFrom = sinceTime(since);
    renderColumn(deskColumn(host), data);
    if (routeMap) routeMap.update(data, { changed: lastChanged });
    drawnEpoch = epoch;
    host.dataset.loaded = "1";
  }

  // -- the side panel (DESIGN.md 14.3) ----------------------------------------------------
  // A card opens its attempt in app.html's side panel, on the desk, the map
  // live beside it (no backdrop): the attempt -- its verdict and result
  // changed through the map file's preview-then-confirm write, dirty cells
  // only; a member's change becomes a verdict-change proposal (12.4); turning
  // a route abandoned asks its reason --, 写备注, 来龙去脉 (the node page's
  // trace), 源自 and 由它而来 with remove, 待办 (the proposals touching it,
  // each opened in place and answered there), its 待复核 judged in place,
  // what changed since the last visit (14.5), 打开详情页. The column's 查看
  // opens a proposal there as its own subject (team.js draws it, reading
  // /api/proposals/<id>?from=). One panel at a time: what is half typed in
  // it is never replaced unasked.

  const NOTES_SHOWN = 5;
  let lastData = null;     // the desk payload last drawn: attempts, lines, the column
  let lastChanged = null;  // its 「有改动」 rule
  let lastFrom = Infinity; // ... from this time
  let panelSaid = null;    // { id, text }: what the last write in the panel did, said until another opens

  function byIdOf(id) { return document.getElementById(id); }

  function panelShown() {
    const p = byIdOf("panel");
    return !!p && !p.classList.contains("hidden");
  }

  // A field typed in (app.html marks it data-typed) whose words are not written.
  function halfTyped() {
    const body = byIdOf("panel-body");
    if (!body || !panelShown()) return false;
    return Array.from(body.querySelectorAll("textarea, input")).some((f) =>
      f.dataset.typed === "1" && f.value.trim() !== String(f.dataset.initial || "").trim());
  }

  function mayLeavePanel() {
    return !halfTyped() || window.confirm(t("这里有还没写入的内容，离开它就不保存了。继续吗？"));
  }

  function attemptOf(id) {
    return ((lastData && lastData.attempts) || []).find((a) => a.id === id) || null;
  }

  function deskRole() {
    const c = (lastData && lastData.column) || {};
    return c.role || (lastData && lastData.role) || "single";
  }

  // 「打开详情页」, or a file of its trace: the node page, asked first.
  function leaveTo(nodeId) {
    if (mayLeavePanel()) RONode.openNode(nodeId);
  }

  // A card clicked, or an attempt named in the panel: opened in the panel;
  // the one open already stays as it is, typing and all. `toReview` (the
  // column's 复核): brought to its 待复核.
  function openAttempt(id, toReview) {
    const s = state.panelSubject;
    if (s && s.kind === "desk-attempt" && s.id === id && panelShown()) {
      if (toReview === true) showReviewPart();
      return null;
    }
    if (!mayLeavePanel()) return null;
    const subject = { kind: "desk-attempt", id: id, hold: false, open: new Set(), judged: new Set(), toReview: toReview === true };
    const a = attemptOf(id) || {};
    showPanel(subject, attemptName(a) || id, a.date || "");
    return drawPanel(subject);
  }

  // The panel opened on the desk for `subject`, the map live beside it (no
  // backdrop); what an earlier write said is not carried over.
  function showPanel(subject, title, subtitle) {
    state.panelSubject = subject;
    panelSaid = null;
    const head = byIdOf("panel-title");
    head.textContent = title;
    head.title = "";
    head.classList.add("plain-title");
    byIdOf("panel-subtitle").textContent = subtitle;
    byIdOf("panel-body").innerHTML = '<p class="empty-note">' + t("载入中…") + "</p>";
    openPanel();
    const backdrop = byIdOf("panel-backdrop");
    if (backdrop) backdrop.classList.add("hidden");
  }

  // A proposal from the column (14.4) opened in the panel as its own
  // subject -- also one touching no attempt (files, a variable's version) --,
  // and the cards it touches marked on the map. The one open already stays.
  function openProposal(p) {
    const s = state.panelSubject;
    const mark = () => pick("p:" + p.id, p.touches || [], true);
    if (s && s.kind === "desk-proposal" && s.id === p.id && s.by === (p.by || null) && panelShown()) { mark(); return null; }
    if (!mayLeavePanel()) return null;
    const subject = { kind: "desk-proposal", id: p.id, by: p.by || null, hold: false };
    showPanel(subject, p.sentence || proposalWhat(p) || p.id,
      [kindWord(p.kind) || t("提议", { n: 1 }), p.by_label || p.by].filter(Boolean).join(" · "));
    mark();
    return drawProposal(subject);
  }

  // The proposal drawn by team.js (renderProposalInto): what approving does,
  // 批准并加入 / 退回并说明 / 讨论; read again on a refresh unless something
  // in it is typed.
  async function drawProposal(subject) {
    if (subject.hold || halfTyped()) return;
    const host = mk("div", "dp-proposal");
    await ROTeam.renderProposalInto(host, subject.id, subject.by, (said) => answered(subject, "p:" + subject.id, said),
      { panel: true, open: leaveTo });
    if (state.panelSubject !== subject || subject.hold || halfTyped()) return;
    const body = byIdOf("panel-body");
    const top = body.scrollTop;
    body.textContent = "";
    if (panelSaid && panelSaid.id === "p:" + subject.id) body.appendChild(mk("div", "notice-status ok", panelSaid.text));
    body.appendChild(host);
    body.scrollTop = top;
  }

  // An answer given in the panel (approve, return, discuss; team.js empties
  // its fields): what it did is said at the panel's top, and the map, the
  // column and the panel are drawn again in place.
  async function answered(subject, key, said) {
    panelSaid = { id: key, text: said.text };
    await loadDesk(views.desk, true);
    if (state.panelSubject !== subject) return;
    await (subject.kind === "desk-proposal" ? drawProposal(subject) : drawPanel(subject));
  }

  // One proposal opened in place under its row in an attempt's panel.
  async function openInPlace(subject, p, inner) {
    const host = mk("div", "dp-proposal");
    inner.textContent = "";
    inner.appendChild(mk("p", "empty-note", t("载入中…")));
    await ROTeam.renderProposalInto(host, p.id, p.by, (said) => answered(subject, subject.id, said), { panel: true, open: leaveTo });
    inner.textContent = "";
    inner.appendChild(host);
  }

  // 待办 (14.3: its urgent work): the proposals touching this attempt -- the
  // PI's waiting ones, a member's own still waiting (14.4) --, each opened in
  // place with the full 「批准以后会怎样」 and its answers.
  function urgentPart(subject, id) {
    const c = (lastData && lastData.column) || {};
    const role = deskRole();
    const list = role === "pi" ? c.waiting || [] : role === "member" ? (c.mine || []).filter(waits) : [];
    const touching = list.filter((p) => (p.touches || []).indexOf(id) >= 0);
    if (!touching.length) return null;
    const s = part(t("待办"));
    const ul = mk("ul", "desk-list");
    touching.forEach((p) => ul.appendChild(proposalRow(p, role === "pi", subject)));
    s.appendChild(ul);
    return s;
  }

  // Drawn again after a write or a refresh (app.html revalidatePanel) --
  // unless something is typed in it, or a preview or a proposal's draft stands.
  async function drawPanel(subject) {
    const id = subject.id;
    const keys = reviewKeys(id);
    const rowMoved = newChanges(attemptOf(id)).some((c) => c.node === id);
    let page, notes, review, hist;
    try {
      [page, notes, , review, hist] = await Promise.all([
        apiGet("/api/node?node=" + encodeURIComponent(id)),
        apiGet("/api/notes?node=" + encodeURIComponent(id)).catch((err) => ({ failed: err })),
        typeof fetchAttemptRecords === "function" ? fetchAttemptRecords().catch(() => false) : false,
        keys.length || offerHere(subject) ? apiGet("/api/review").catch((err) => ({ failed: err })) : null,
        rowMoved ? apiGet("/api/history?node=" + encodeURIComponent(id)).catch((err) => ({ failed: err })) : null,
      ]);
    } catch (err) {
      if (state.panelSubject !== subject || subject.hold || halfTyped()) return;
      const body = byIdOf("panel-body");
      body.textContent = "";
      const box = mk("div", "notice-status");
      setStatus(box, "err", t("没能打开这条尝试"), err);
      body.appendChild(box);
      return;
    }
    if (state.panelSubject !== subject || subject.hold || halfTyped()) return;
    const d = page.details || {};
    const a = attemptOf(id) || { id: id, number: d.number, title: d.description, date: d.date, route_status: d.verdict_status };
    byIdOf("panel-title").textContent = attemptName(a) || page.name || id;
    byIdOf("panel-subtitle").textContent = a.date || "";
    const body = byIdOf("panel-body");
    const top = body.scrollTop;
    body.textContent = "";
    if (panelSaid && panelSaid.id === id) body.appendChild(mk("div", "notice-status ok", panelSaid.text));
    body.appendChild(panelHead(a, id));
    body.appendChild(editPart(subject, a, page));
    const changes = changesPart(a, id, hist);
    if (changes) body.appendChild(changes);
    body.appendChild(notesPart(subject, page, notes));
    if (window.RONode && RONode.renderTrace) RONode.renderTrace(body, page, { panel: true, open: leaveTo });
    body.appendChild(linesPart(subject, id, "parent"));
    body.appendChild(linesPart(subject, id, "child"));
    const urgent = urgentPart(subject, id);
    if (urgent) body.appendChild(urgent);
    const judged = reviewPart(subject, keys, review);
    if (judged) body.appendChild(judged);
    body.scrollTop = top;
    if (subject.toReview) { subject.toReview = false; showReviewPart(); }
    const offered = subject.offered;
    subject.offered = null;
    if (offered && typeof offered.scrollIntoView === "function") offered.scrollIntoView({ block: "nearest" });
    if (window.RONode && RONode.drawTraceLines) RONode.drawTraceLines(body);
  }

  // 「有改动」 said in place (14.5): what changed on this attempt since the
  // last visit, newest first -- its row as the node page's timeline says it
  // (how the verdict and the result moved, who, how it came), reduced to the
  // versions since then; each of its files that changed, opened on its own
  // page. Nothing changed since: no part.
  function newChanges(a) {
    return ((a && a.changed) || []).filter((c) => new Date(c.at).getTime() > lastFrom);
  }

  function changesPart(a, id, hist) {
    const news = newChanges(a);
    if (!news.length) return null;
    const s = part(t("你上次查看以来的改动"));
    const versions = hist && !hist.failed && window.RONode && RONode.verdictMove ? hist.versions || [] : [];
    const moved = versions.map((v, i) => ({ at: v.at, v: v, prev: versions[i + 1] || null }))
      .filter((e) => new Date(e.at).getTime() > lastFrom);
    const row = news.find((c) => c.node === id);
    const entries = news.filter((c) => c.node !== id).map((c) => ({ at: c.at, file: c }))
      .concat(row ? (moved.length ? moved : [{ at: row.at, row: true }]) : []);
    entries.sort((x, y) => new Date(y.at).getTime() - new Date(x.at).getTime());
    const ul = mk("ul", "node-timeline");
    entries.forEach((e) => {
      const li = mk("li", "tl-row");
      li.appendChild(mk("span", "tl-when", whenWords(e.at, now())));
      const main = mk("div", "tl-main");
      if (e.v) {
        main.appendChild(mk("div", "tl-what", RONode.verdictMove(e.prev, e.v)));
        main.appendChild(mk("div", "tl-meta", [e.v.who_label, RONode.sourceWords(e.v)].filter(Boolean).join(" · ")));
        if (e.v.approved_by) main.appendChild(mk("div", "tl-meta", t("经 {who} 批准", { who: e.v.approved_by_label || e.v.approved_by })));
        if (e.v.note) main.appendChild(mk("p", "tl-note", e.v.note));
      } else if (e.row) {
        main.appendChild(mk("div", "tl-what", t("这一行有改动")));
      } else {
        const what = mk("div", "tl-what");
        what.appendChild(rowLink(e.file.path || e.file.node, "mono", () => leaveTo(e.file.node), t("打开它的详情页")));
        main.append(what, mk("div", "tl-meta", t("文件有改动")));
      }
      li.appendChild(main);
      ul.appendChild(li);
    });
    s.appendChild(ul);
    if (a.changed_more && news.length === a.changed.length) {
      s.appendChild(mk("p", "desk-empty", t("更早改动的 {n} 个文件没有列在这里。", { n: numberText(a.changed_more) })));
    }
    return s;
  }

  // Its 待复核 (14.3): the judgments the column lists under this attempt,
  // each drawn by the review panel's own item (app.html renderReviewItem),
  // confirmed again, changed or withdrawn in place. An act made here offers
  // its 「撤销」 here -- in its item while it is listed, else at the top of
  // this part -- for as long as the review panel would (state.reviewOffer).
  function reviewKeys(id) {
    const groups = ((((lastData && lastData.column) || {}).review) || {}).groups || [];
    const g = groups.find((x) => x.id === id);
    return g && typeof keyId === "function" ? (g.items || []).map(keyId) : [];
  }

  function offerHere(subject) {
    const o = state.reviewOffer;
    return o && o.until > Date.now() && subject.judged && subject.judged.has(o.origin) ? o : null;
  }

  function showReviewPart() {
    const sec = byIdOf("panel-body").querySelector(".dp-review");
    if (sec && typeof sec.scrollIntoView === "function") sec.scrollIntoView({ block: "start" });
  }

  function reviewPart(subject, keys, review) {
    const offer = offerHere(subject);
    if (!keys.length && !offer) return null;
    const s = part(t("待复核"), "dp-review");
    const top = mk("div");
    s.appendChild(top);
    if (review && review.failed) {
      const box = mk("div", "notice-status");
      setStatus(box, "err", t("没能读到待复核的判断"), review.failed);
      s.appendChild(box);
    } else if (review && review.ledger && review.ledger.state && review.ledger.state !== "ok") {
      s.appendChild(mk("p", "desk-alone", t("判断记录文件当前无法读取，请先恢复它")));
    }
    const wanted = new Set(keys);
    const boxes = (review && !review.failed ? review.review || [] : []).filter((i) => wanted.has(keyId(i))).map((i) => {
      subject.judged.add(keyId(i));
      return s.appendChild(renderReviewItem(i, { focus: null }));
    });
    if (offer) {
      const said = mk("div", "panel-status ok");
      said.append(mk("span", null, offer.text), mkButton(t("撤销"), "err-detail-toggle", () => undoFromReview(offer.key, said)));
      (boxes.find((b) => b.dataset.key === offer.origin) || top).appendChild(said);
      setTimeout(() => { said.textContent = ""; said.classList.add("hidden"); }, Math.max(0, offer.until - Date.now()) + 50);
      if (!offer.shown) { offer.shown = true; subject.offered = said; }
    }
    return s;
  }

  function part(title, cls) {
    const s = mk("section", "node-sec dp-part" + (cls ? " " + cls : ""));
    s.appendChild(mk("h3", "node-sec-title", title));
    return s;
  }

  // Who a decision names (12.4): 「由 X 放弃」 for a route abandoned directly
  // (no proposal, 14.3), else who proposed it and who approved it.
  function decisionWho(d) {
    const by = d.by && d.by.label;
    if (d.act === "abandoned" && !d.proposal) return by ? t("由 {who} 放弃", { who: by }) : "";
    return [by ? t("提议人 {who}", { who: by }) : "", d.approved_by ? t("批准人 {who}", { who: d.approved_by.label }) : ""]
      .filter(Boolean).join(" · ");
  }

  // Its verdict in its tone, its marks as quiet words (14.1), 打开详情页.
  function panelHead(a, id) {
    const head = mk("div", "dp-head");
    const marks = mk("div", "node-marks");
    marks.appendChild(verdictInline(a.route_status));
    const own = ownMark(a);
    if (own) marks.appendChild(own);
    const m = a.marks || {};
    const said = new Set(own ? [own.textContent] : []); // each said once (14.1), as on its card
    (m.proposals || []).map((p) => p.label).concat(m.review ? m.review.label : [], lastChanged && lastChanged(a) ? t("有改动") : [])
      .filter(Boolean).forEach((w) => {
        if (said.has(String(w))) return;
        said.add(String(w));
        marks.appendChild(mk("span", "dp-mark", w));
      });
    head.appendChild(marks);
    if (a.abandoned && a.reason) head.appendChild(mk("p", "desk-note", t("放弃的理由：{reason}", { reason: a.reason })));
    if (a.abandoned && a.decision) head.appendChild(metaLine([decisionWho(a.decision), whenWords(a.decision.at, now())]));
    const actions = mk("div", "desk-actions");
    actions.appendChild(mkButton(t("打开详情页"), "btn btn-small", () => leaveTo(id)));
    head.appendChild(actions);
    return head;
  }

  function typedField(tag, value, placeholder) {
    const f = mk(tag, tag === "textarea" ? "form-textarea" : "form-input");
    if (tag === "textarea") f.rows = 2;
    f.value = value || "";
    f.dataset.initial = f.value;
    if (placeholder) f.placeholder = placeholder;
    return f;
  }

  // The verdicts this table already holds, as 12.11 shows them; a click puts
  // the cell's own text, its marker included, into the field.
  function verdictChips(field) {
    const records = state.attemptsById ? Array.from(state.attemptsById.values()) : [];
    const values = typeof distinctVerdicts === "function" ? distinctVerdicts(records) : [];
    if (!values.length) return null;
    const statusOf = new Map(records.map((r) => [(r.verdict || "").trim(), r.verdict_status]));
    const chips = mk("div", "verdict-chips");
    values.forEach((v) => {
      const vs = statusOf.get(v);
      chips.appendChild(mkButton(vs ? [verdictWord(vs), vs.words].filter(Boolean).join(" ") : v, "chip", () => {
        field.value = v;
        field.dataset.typed = "1";
      }));
    });
    return chips;
  }

  // The attempt's verdict and result (14.3): preview, then confirm; only the
  // cells changed are sent. Description, date and variables are 研究路线's.
  function editPart(subject, a, page) {
    const d = page.details || {};
    const role = deskRole();
    const s = part(t("判决与结果"));
    if (role !== "single" && role !== "pi" && role !== "member") {
      if (d.result) s.appendChild(mk("p", "desk-note", d.result));
      const c = (lastData && lastData.column) || {};
      s.appendChild(mk("p", "desk-alone", role === "none" ? t("你还不是这个项目的成员：请 PI 把你加入团队，然后同步")
        : c.notice || t("现在说不清你在这个项目里的角色，判决先不能改。")));
      return s;
    }
    const proposing = role === "member";
    // A member's own row the official version does not have yet (the engine's
    // new_row): proposed whole, as a new attempt -- replacing their pending
    // one when it is proposed already; once approved, it waits for the sync.
    const own = (proposing && a.officialness) || {};
    const newRow = !!own.new_row;
    if (newRow && own.state === "approved") {
      if (d.result) s.appendChild(mk("p", "desk-note", d.result));
      s.appendChild(mk("p", "desk-alone", t("这一行已经批准，正式版本里有了：先同步，再改它。")));
      return s;
    }
    const number = String(d.number || a.number || ""); // the row's identity, as the table writes it
    const verdict = typedField("input", d.verdict || "");
    const result = typedField("textarea", d.result || "");
    const verdictField = formFieldEl(t("判决"), verdict);
    const chips = verdictChips(verdict);
    if (chips) verdictField.appendChild(chips);
    s.append(verdictField, formFieldEl(t("结果"), result));
    let sentence = null;
    if (proposing) {
      sentence = typedField("textarea", "", t("用你自己的话说明这份提议：PI 会先看到这句话"));
      s.appendChild(formFieldEl(t("一句话说明（必填）"), sentence));
    }
    const reason = typedField("textarea", "", t("为什么放弃这条路线"));
    const reasonField = formFieldEl(proposing ? t("理由（放弃一条路线时必填）") : t("放弃的理由（必填）"), reason);
    reasonField.appendChild(mk("div", "team-help", t("理由会写进结果一栏，这条路线当时的依据也会冻结下来。")));
    reasonField.classList.add("hidden"); // until the preview says this write abandons the route
    s.appendChild(reasonField);
    const status = mk("div", "notice-status");
    const shown = mk("div", "dp-preview");
    const go = mkButton(proposing ? t("预览这一行") : t("预览改动"), "btn btn-primary", () => preview());
    const row = mk("div", "form-actions");
    row.appendChild(go);
    s.append(row, status, shown);
    const fields = [verdict, result, reason].concat(sentence ? [sentence] : []);
    const freeze = (on) => { fields.forEach((f) => { f.disabled = on; }); go.disabled = on; subject.hold = on; };

    function body() {
      const changed = {};
      if (verdict.value !== verdict.dataset.initial) changed.verdict = toCellText(verdict.value);
      if (result.value !== result.dataset.initial) changed.result = toCellText(result.value);
      if (!Object.keys(changed).length) return null;
      const b = { op: "update", number: number, fields: changed };
      if (!proposing && !reasonField.classList.contains("hidden") && reason.value.trim()) b.reason = reason.value.trim();
      return b;
    }

    async function preview(again) {
      if (proposing && !sentence.value.trim()) { setStatus(status, "err", t("请用一句话说明这份提议")); sentence.focus(); return; }
      const b = body();
      if (!b) { setStatus(status, "", t("没有需要写入的改动")); return; }
      setStatus(status, "", t("正在生成预览…"));
      let p;
      try {
        p = await apiPost("/api/attempts/preview", b);
      } catch (err) {
        setStatus(status, "err", t("无法生成预览"), err);
        return;
      }
      // A2b: the PI and a single researcher give the reason when this write
      // turns the route abandoned; a member's proposal whenever it abandons.
      const asks = proposing ? !!p.abandons : !!p.reason_required;
      reasonField.classList.toggle("hidden", !asks);
      if (asks && !reason.value.trim()) { setStatus(status, "err", t("放弃一条路线时必须写明理由")); reason.focus(); return; }
      if (asks && !b.reason && !proposing && !again) return preview(true); // its field shows now: the row with the reason
      if (proposing && p.abandons && !result.value.trim()) {
        setStatus(status, "err", t("放弃一条路线时，结果和理由都必须填写"));
        result.focus();
        return;
      }
      setStatus(status, "", "");
      showPreview(p, b);
    }

    function showPreview(p, b) {
      freeze(true);
      shown.textContent = "";
      if (p.old_row != null) shown.append(mk("div", "diff-label", t("原行")), mk("div", "diff-row old", p.old_row));
      shown.append(mk("div", "diff-label", t("新行")), mk("div", "diff-row new", p.new_row));
      shown.appendChild(mk("div", "confirm-note", proposing
        ? t("将写入你的工作副本里的 {file}（已自动备份）。这是你自己的改动，尚未提交；下一步给你看提议的内容，确认后才发送给 PI。", { file: p.file })
        : t("将写入 {file}（已自动备份）", { file: p.file })));
      if (newRow) {
        shown.appendChild(mk("div", "confirm-note", own.state === "proposed" && own.proposal
          ? t("这一行还不在正式版本里：它会整行作为新的尝试再提议一次，替换你还在等待的那份提议。")
          : t("这一行还不在正式版本里：它会整行作为一条新的尝试提议。")));
      }
      if (!proposing && p.reason_required) shown.appendChild(mk("div", "confirm-note", t("这一行会放弃这条路线：理由写进结果一栏，它当时的依据冻结下来。")));
      const actions = mk("div", "form-actions");
      const ok = mkButton(proposing ? t("写入工作副本，查看提议") : t("确认写入"), "btn btn-primary", () => write(b, ok, back));
      const back = mkButton(t("返回修改"), "btn", () => { shown.textContent = ""; freeze(false); setStatus(status, "", ""); });
      actions.append(ok, back);
      shown.appendChild(actions);
    }

    async function write(b, ok, back) {
      ok.disabled = true;
      back.disabled = true;
      setStatus(status, "", t("正在写入…"));
      let res;
      try {
        res = await apiPost("/api/attempts/write", b); // the confirmed body, verbatim
      } catch (err) {
        setStatus(status, "err", t("写入失败，文件没有被修改"), err);
        back.disabled = false;
        return;
      }
      if (typeof pollGeneration === "function") pollGeneration();
      if (proposing) {
        // The row is the member's own change now; the proposal is the
        // engine's dry run of it, sent only on 发送提议 (team.js).
        shown.textContent = "";
        setStatus(status, "", t("这一行已写进你的工作副本。"));
        const draft = { kind: newRow ? "attempt_new" : "verdict_change", sentence: sentence.value.trim(), number: number };
        if (newRow && own.state === "proposed" && own.proposal) draft.supersedes = own.proposal;
        if (reason.value.trim()) draft.reason = reason.value.trim();
        const composeStatus = mk("div", "notice-status");
        const composeHost = mk("div", "compose-result");
        shown.append(composeStatus, composeHost);
        if (!(await ROTeam.composeInto(composeHost, draft, composeStatus))) {
          composeStatus.appendChild(mk("div", "team-help", t("这一行已经在你的工作副本里，可以稍后在「提议」一栏里提交。")));
        }
        return; // the draft holds the panel until it is closed
      }
      if (res.ingest_error) {
        setStatus(status, "err", t("已写入并备份，但重扫失败"), res.ingest_error);
        back.disabled = false;
        return;
      }
      // Written: these fields hold the row as it is now; the panel is drawn
      // again unless something else in it is half typed.
      reason.value = "";
      reasonField.classList.add("hidden");
      fields.forEach((f) => { f.dataset.initial = f.value; delete f.dataset.typed; });
      shown.textContent = "";
      freeze(false);
      panelSaid = { id: subject.id, text: t("已写入 {file}。", { file: res.file }) };
      setStatus(status, "ok", panelSaid.text);
      await drawPanel(subject);
    }
    return s;
  }

  // 写备注 (12.6): the notes ledger, newest first, and a note added.
  function notesPart(subject, page, notes) {
    const s = part(t("写备注"));
    if (notes && notes.failed) {
      const failed = mk("div", "notice-status");
      setStatus(failed, "err", t("没能读到这条尝试的备注"), notes.failed);
      s.appendChild(failed);
    }
    const list = notes && !notes.failed ? (notes.notes || []).filter((n) => !n.withdrawn) : [];
    list.slice(-NOTES_SHOWN).reverse().forEach((n) => {
      s.appendChild(mk("p", "tl-note", n.text || ""));
      s.appendChild(metaLine([n.label || n.name || n.by || t("最初的研究者"), whenWords(n.at, now())]));
    });
    if (list.length > NOTES_SHOWN) {
      s.appendChild(mk("p", "desk-empty", t("更早的 {n} 条备注在详情页。", { n: numberText(list.length - NOTES_SHOWN) })));
    }
    const input = typedField("textarea", "", t("写一条备注：为什么这样做、要注意什么"));
    input.rows = 3;
    input.setAttribute("aria-label", t("备注"));
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
      input.value = "";
      delete input.dataset.typed;
      add.disabled = false;
      panelSaid = { id: subject.id, text: t("备注已保存。") };
      setStatus(status, "ok", panelSaid.text);
      await drawPanel(subject);
    });
    const blocked = humanRecordsBlocked() || (notes && notes.trusted === false ? t("备注记录文件现在无法写入") : null);
    if (blocked) { add.disabled = true; add.title = blocked; }
    const row = mk("div", "form-actions");
    row.appendChild(add);
    s.append(input, row, status);
    if (deskRole() === "member") {
      s.appendChild(mk("p", "node-sub writes", t("备注先记在你的工作副本里；下次同步时交到公盘，PI 下次发布时加入正式记录，署你的名字，不需要批准。")));
    }
    return s;
  }

  // 源自 (its parents) or 由它而来 (what grew out of it, `side` "child"),
  // each with its note and who drew it; removed by the PI or a single
  // researcher, a member's removal proposed (14.2).
  function linesPart(subject, id, side) {
    const parents = side === "parent";
    const s = part(parents ? t("源自") : t("由它而来"));
    const lines = ((lastData && lastData.lines) || []).filter((l) => (parents ? l.child : l.parent) === id);
    if (!lines.length) {
      s.appendChild(emptyLine(parents ? t("还没标来处") : t("还没有尝试源自它")));
      return s;
    }
    const role = deskRole();
    const ul = mk("ul", "desk-list");
    lines.forEach((l) => {
      const other = parents ? l.parent : l.child;
      const a = attemptOf(other);
      const li = mk("li", "desk-row");
      const main = mk("div", "desk-main");
      const head = mk("div", "desk-head");
      head.appendChild(rowLink(a ? attemptName(a) : "#" + String(other).split("#").pop(), "", () => openAttempt(other)));
      if (a) head.appendChild(statusLabel(a.route_status));
      const mark = l.officialness && l.officialness.state !== "official" && l.officialness.label ? l.officialness.label
        : l.pending ? (l.pending.op === "remove" ? t("提议删除") : t("提议中")) : null;
      if (mark) head.appendChild(mk("span", "own-mark", mark));
      main.appendChild(head);
      if (l.note) main.appendChild(mk("p", "desk-note", l.note));
      main.appendChild(metaLine([l.by_label, dayWords(l.date, now())]));
      const status = mk("div", "notice-status");
      const proposing = role === "member";
      if ((role === "single" || role === "pi" || proposing) && !(l.pending && (l.pending.op === "add" || proposing))) {
        const actions = mk("div", "desk-actions");
        const del = mkButton(proposing ? t("提议删除这条连线") : t("删除这条连线"), "btn btn-small", () => removeLine(subject, l, proposing, del, status));
        actions.appendChild(del);
        main.appendChild(actions);
      }
      main.appendChild(status);
      li.appendChild(main);
      ul.appendChild(li);
    });
    s.appendChild(ul);
    return s;
  }

  async function removeLine(subject, l, proposing, btn, status) {
    if (!window.confirm(proposing ? t("提议删除这条连线？PI 批准后它才会从路线图上消失。") : t("删除这条连线？它会从项目的记录里删去。"))) return;
    btn.disabled = true;
    setStatus(status, "", t("正在写入…"));
    try {
      if (proposing) await apiPost("/api/proposals", { kind: "route_link", op: "remove", child: l.child, parent: l.parent, write: true });
      else await apiPost("/api/mappings/delete", { from: l.child, to: l.parent, type: "derives_from" });
    } catch (err) {
      btn.disabled = false;
      const refusal = window.RORouteMap && RORouteMap.lineRefusal ? RORouteMap.lineRefusal(err && err.state) : null;
      setStatus(status, "err", refusal || t("没能删除这条连线"), err);
      return;
    }
    panelSaid = { id: subject.id, text: proposing ? t("已把删除这条连线作为提议交给 PI。") : t("这条连线已删去。") };
    setStatus(status, "ok", panelSaid.text);
    await loadDesk(views.desk, true); // the map without it (or marked), then the panel
    await drawPanel(subject);
  }

  // Going to another view (app.html activateView): the desk's panel goes
  // with the desk, after asking when something in it is half typed. False
  // when the researcher stays.
  function leaveDesk() {
    const s = state.panelSubject;
    if (!s || (s.kind !== "desk-attempt" && s.kind !== "desk-proposal") || !panelShown()) return true;
    if (!mayLeavePanel()) return false;
    closePanel();
    return true;
  }

  // app.html's revalidatePanel, after the desk read itself again.
  function revalidatePanel(subject) {
    if (typeof currentView !== "undefined" && currentView !== "desk") return null; // drawn from the desk's payload only
    if (subject && subject.kind === "desk-attempt") return drawPanel(subject);
    return subject && subject.kind === "desk-proposal" ? drawProposal(subject) : null;
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
    kindWord, stateWord, verdictWord, proposalWhat, proposalStateText, nameOf, whenWords, dayWords, relationWord,
    openAttempt, openProposal, revalidatePanel, mayLeavePanel, leaveDesk,
    // pure, for tests/test_v8_u2.py
    _whenWords: whenWords, _shareLineText: shareLineText, _proposalWhat: proposalWhat,
    _proposalStateText: proposalStateText, _seenKey: seenKey, _toneOf: toneOf,
  });
})();
