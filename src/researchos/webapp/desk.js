// The sidebar's live lines and the desk (工作台) -- DESIGN.md 12.0, 12.9,
// 12.11; task V8 phase U2. Served verbatim at /desk.js by the same process
// and loaded by app.html with a plain same-origin <script src>, exactly as
// /canvas.js is: zero build, zero external resources, same origin check.
//
// It defines one global, window.RODesk, and calls app.html's helpers (mk,
// mkButton, apiGet, apiPost, renderBlockingError, renderErrorBox, setStatus,
// RONode.openNode (every object opens its node page, U3), ROTeam.openProposal
// (a proposal opens its page in 提议, U4), openReviewPanel,
// showAbandonedRoutes, state, ...) only at run time, never while loading.
//
// What it reads (all GET, all origin-checked by the server):
//   /api/me         -- who I am on this machine (the sidebar's last line);
//   /api/share      -- the shared drive's state of a collaborative project
//                      (the status line; a member's 官方版本有更新);
//   /api/proposals  -- the PI's inbox or a member's own proposals (the quiet
//                      count beside 提议; 等你批准 / 我的提议 on the desk);
//   /api/desk?since -- what changed since I last looked, results that lost
//                      their evidence, abandoned routes (researchos.desk).
// and the one write: POST /api/share/sync, 「同步」 a member's working copy.
//
// The desk is what 12.0 says and nothing else: what waits for my decision,
// what changed since I last looked and exactly where, which results lost
// their evidence, which routes were abandoned. No statistics, no charts, no
// counters for their own sake. Nothing typed on the page ever becomes a
// filesystem path: every path shown comes from the server.
//
// 12.11: no emoji, no arrow characters, verdicts as text status labels (the
// server's verdict_status, mapped from the researcher's own markers), the
// researcher's own words shown as written.
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

  // What an approval did, one sentence per kind (13.6: English word order).
  function approvedWords(kind) {
    switch (kind) {
      case "attempt_new": return t("批准了一条新尝试提议");
      case "verdict_change": return t("批准了一条判决变更提议");
      case "variable_version": return t("批准了一条变量定义提议");
      case "files": return t("批准了一条文件提议");
      default: return t("批准了一条提议");
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

  // People (and drafts) named in a sentence: in English the last two joined
  // by "and" ("Li, Wang and Original researcher"); in Chinese as series.
  function andSeries(xs) {
    return english() && xs.length > 1 ? series(xs.slice(0, -1)) + " and " + xs[xs.length - 1] : series(xs);
  }

  // A sentence whose fields are elements (a verdict's label): the template's
  // text around them, in the template's own word order (13.6).
  function fillNodes(parent, template, nodes) {
    String(template).split(/(\{\w+\})/).forEach((part) => {
      const key = part.slice(1, -1);
      if (/^\{\w+\}$/.test(part) && Object.prototype.hasOwnProperty.call(nodes, key)) parent.append(nodes[key]);
      else if (part) parent.append(part);
    });
    return parent;
  }

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

  // -- the desk ----------------------------------------------------------------------

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

  // 12.9: every object the desk names opens its node page.
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

  function renderChange(item, team) {
    const row = mk("li", "desk-row");
    const at = mk("span", "desk-when", whenWords(item.at, now()));
    const main = mk("div", "desk-main");
    const head = mk("div", "desk-head");
    const who = andSeries(item.who || []);
    if (item.kind === "file") {
      head.appendChild(rowLink(item.path, "mono", () => RONode.openNode(item.node || item.path), t("打开这个文件")));
      head.appendChild(mk("span", "desk-what", item.uncommitted ? t("{what}（还没有提交）", { what: item.what })
        : String(item.what)));
      if (item.exists === false) head.appendChild(mk("span", "desk-flag", t("文件不存在")));
    } else if (item.kind === "attempt") {
      head.appendChild(rowLink(attemptName(item) || item.node, "", () => openAttempt(item.node), t("打开这条尝试")));
      // 13.6: the engine's code decides the form, never the words it sends.
      if (item.what_code === "verdict_changed" && item.old_status) {
        head.appendChild(fillNodes(mk("span", "desk-what"), t("判决 {was} 改为 {became}"),
          { was: verdictInline(item.old_status), became: verdictInline(item.new_status) }));
      } else if (item.what_code === "new_attempt") {
        head.appendChild(fillNodes(mk("span", "desk-what"), t("新增的尝试 {verdict}"), { verdict: verdictInline(item.new_status) }));
      } else {
        head.appendChild(mk("span", "desk-what", t("结果改为：{result}", { result: item.result || t("（空）") })));
      }
    } else if (item.kind === "decision") {
      const what = item.act === "abandoned" ? t("放弃了这条路线") : approvedWords(item.proposal_kind);
      if (item.node) head.appendChild(rowLink(attemptName(item) || item.node, "", () => openAttempt(item.node), t("打开这条尝试")));
      head.appendChild(mk("span", "desk-what", what));
      if (item.verdict_status) head.appendChild(verdictInline(item.verdict_status));
    } else if (item.kind === "note") {
      // 8.8: the file's path, or what the note is on as a reader names it.
      const target = item.path || item.label || t("一处记录");
      head.appendChild(mk("span", "desk-what", t("笔记，写在")));
      head.appendChild(rowLink(target, item.path ? "mono" : "", () => RONode.openNode(item.node || item.path)));
    }
    const own = item.kind === "file" || item.kind === "attempt" ? ownMark(item) : null;
    if (own) head.appendChild(own); // 12.12: a member's own work is never shown as official
    main.appendChild(head);
    const meta = [];
    if (item.kind === "decision") {
      meta.push(t("提议人 {who}", { who: who }));
      if (item.approved_by && item.approved_by.length) meta.push(t("批准人 {who}", { who: andSeries(item.approved_by) }));
    } else if (who) {
      meta.push(who);
      if (item.approved_by && item.approved_by.length) meta.push(t("经 {who} 批准", { who: andSeries(item.approved_by) }));
    }
    if (item.versions > 1) meta.push(t("这段时间里改了 {n} 次", { n: numberText(item.versions) }));
    main.appendChild(metaLine(meta));
    const quotes = item.kind === "note" ? [item.text] : (item.notes || []);
    if (item.kind === "decision" && item.reason) quotes.unshift(t("理由：{reason}", { reason: item.reason }));
    quotes.filter(Boolean).slice(0, 3).forEach((q) => main.appendChild(mk("p", "desk-note", q)));
    row.append(at, main);
    return row;
  }

  function renderChanges(host, data) {
    const sec = block(t("你上次查看以来的改动"));
    const since = data.since_default
      ? t("还没有记录你上次查看的时间，这里列出过去 {n} 天的改动。", { n: numberText(data.default_days || DESK_DAYS) })
      : t("上次查看：{when}", { when: whenInLine(data.since, now()) });
    sec.appendChild(mk("p", "desk-sub", since));
    const changes = data.changes || [];
    if (!changes.length) {
      sec.appendChild(emptyLine(t("这段时间里没有改动。")));
    } else {
      const ul = mk("ul", "desk-list");
      const team = (ctx.share && ctx.share.team) || [];
      changes.forEach((c) => ul.appendChild(renderChange(c, team)));
      sec.appendChild(ul);
      if (data.more_changes > 0) sec.appendChild(mk("p", "desk-sub", t("更早的改动没有列出，可以在各个文件的历史里查看。")));
    }
    host.appendChild(sec);
  }

  function renderResults(host, data) {
    const sec = block(t("依据变了的成果"));
    const results = data.results || [];
    const review = typeof data.review === "number" ? data.review : 0;
    if (!results.length && !review) {
      sec.appendChild(emptyLine(t("所有成果的来源都还在。")));
      host.appendChild(sec);
      return;
    }
    if (results.length) {
      const ul = mk("ul", "desk-list");
      results.forEach((r) => {
        const row = mk("li", "desk-row");
        const main = mk("div", "desk-main");
        const head = mk("div", "desk-head");
        head.appendChild(rowLink(r.path || r.node, "mono", () => RONode.openNode(r.node || r.path), t("打开这个成果")));
        if (r.exists === false) head.appendChild(mk("span", "desk-flag", t("文件不存在")));
        const own = ownMark(r);
        if (own) head.appendChild(own);
        main.appendChild(head);
        (r.breaks || []).forEach((b) => {
          const line = mk("p", "desk-break", b.path && b.path !== r.path ? t("{label}：{path}", { label: b.label, path: b.path })
            : String(b.label));
          if (b.repair) line.appendChild(mk("span", "desk-repair", t("可以{repair}", { repair: b.repair })));
          main.appendChild(line);
        });
        if (r.review) {
          const line = mk("p", "desk-break", t("它的来源里有 {n} 条关联的判断待复核", { n: numberText(r.review) }));
          line.appendChild(rowLink(t("复核"), "", () => openReviewPanel(null)));
          main.appendChild(line);
        }
        if (r.drafts && r.drafts.length) main.appendChild(metaLine([t("文稿 {drafts} 引用了它", { drafts: andSeries(r.drafts) })]));
        row.appendChild(main);
        ul.appendChild(row);
      });
      sec.appendChild(ul);
    }
    if (review) {
      const line = mk("p", "desk-sub");
      line.append(t("{n} 条判断的依据变了，等你复核。", { n: numberText(review) }));
      line.appendChild(rowLink(t("查看"), "", () => openReviewPanel(null)));
      sec.appendChild(line);
    }
    host.appendChild(sec);
  }

  // 12.0: which routes were abandoned and why -- each route by its number
  // and title, its result and its reason, opening its page.
  function renderAbandoned(host, data) {
    const sec = block(t("已放弃的路线"));
    const list = data.abandoned || [];
    if (!list.length) {
      sec.appendChild(emptyLine(t("还没有放弃过的路线。")));
    } else {
      const ul = mk("ul", "desk-list");
      list.forEach((a) => {
        const row = mk("li", "desk-row");
        const main = mk("div", "desk-main");
        const head = mk("div", "desk-head");
        head.appendChild(rowLink(attemptName(a) || t("一条尝试"), "", () => openAttempt(a.id), t("打开这条路线")));
        if (a.verdict_status) head.appendChild(verdictInline(a.verdict_status));
        const own = ownMark(a);
        if (own) head.appendChild(own);
        main.appendChild(head);
        if (a.result) main.appendChild(mk("p", "desk-note plain", t("结果：{result}", { result: a.result })));
        // The verdict's own words beside the label often are the reason;
        // a reason recorded apart (an approval, 「放弃理由：…」) is said too.
        if (a.reason) main.appendChild(mk("p", "desk-note", t("理由：{reason}", { reason: a.reason })));
        row.appendChild(main);
        ul.appendChild(row);
      });
      sec.appendChild(ul);
      const line = mk("p", "desk-sub");
      line.appendChild(rowLink(t("在研究路线中查看它们当时的依据"), "", () => showAbandonedRoutes()));
      sec.appendChild(line);
    }
    host.appendChild(sec);
  }

  function renderProposalRow(p, team, forPi) {
    const row = mk("li", "desk-row");
    const main = mk("div", "desk-main");
    const head = mk("div", "desk-head");
    head.appendChild(mk("span", "status-label tone-plain", kindWord(p.kind) || t("提议", { n: 1 })));
    head.appendChild(mk("span", "desk-what strong", p.sentence || proposalWhat(p) || p.id));
    main.appendChild(head);
    const what = proposalWhat(p);
    if (what && p.sentence) main.appendChild(mk("p", "desk-note plain", what));
    const meta = forPi ? [nameOf(p.by, team)] : [proposalStateText(p)];
    main.appendChild(metaLine(meta));
    const actions = mk("div", "desk-actions");
    actions.appendChild(mkButton(t("查看"), "btn btn-small", () => ROTeam.openProposal(p.id, p.by)));
    main.appendChild(actions);
    row.append(mk("span", "desk-when", whenWords(p.at, now())), main);
    return row;
  }

  function renderWaiting(host) {
    const sec = block(t("等你批准"));
    const team = (ctx.share && ctx.share.team) || [];
    const list = ((ctx.proposals && ctx.proposals.proposals) || []).filter((p) => p.state === "pending" || p.state === "conflict");
    if (!list.length) sec.appendChild(emptyLine(t("没有等你批准的提议。")));
    else {
      const ul = mk("ul", "desk-list");
      list.forEach((p) => ul.appendChild(renderProposalRow(p, team, true)));
      sec.appendChild(ul);
    }
    host.appendChild(sec);
  }

  function renderMine(host) {
    const sec = block(t("我的提议"));
    const team = (ctx.share && ctx.share.team) || [];
    const list = (ctx.proposals && ctx.proposals.proposals) || [];
    if (!list.length) sec.appendChild(emptyLine(t("你还没有提交过提议。")));
    else {
      const ul = mk("ul", "desk-list");
      list.forEach((p) => ul.appendChild(renderProposalRow(p, team, false)));
      sec.appendChild(ul);
    }
    host.appendChild(sec);
  }

  // What the last 同步 from the desk did: kept on the desk until it is
  // opened afresh, because the sync itself moves the generation and the
  // desk is rebuilt at once.
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

  function renderOfficial(host) {
    const sec = block(t("官方版本有更新"));
    const share = ctx.share || {};
    const last = share.last_snapshot;
    if (!share.shared) {
      sec.appendChild(emptyLine(t("这个工作副本还没有连上公盘。")));
    } else if (!share.reachable) {
      sec.appendChild(emptyLine(t("公盘现在打不开，等它同步好再来看。")));
    } else if (share.behind && last) {
      sec.querySelector(".desk-title").textContent = t("官方版本有更新");
      const when = whenInLine(last.at, now());
      const line = mk("p", "desk-sub", last.note
        ? t("PI 于 {when} 发布了第 {seq} 版：{note}", { when: when, seq: numberText(last.seq), note: last.note })
        : t("PI 于 {when} 发布了第 {seq} 版。", { when: when, seq: numberText(last.seq) }));
      sec.appendChild(line);
      // What 同步 will write is said first (the engine's sync-preview),
      // exactly as on 成员; the button waits for it.
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
    } else {
      sec.querySelector(".desk-title").textContent = t("官方版本");
      sec.appendChild(emptyLine(last ? t("你的工作副本已经是最新的官方版本（第 {seq} 版）。", { seq: numberText(last.seq) })
        : t("PI 还没有发布过官方版本。")));
    }
    renderSyncShown(sec);
    host.appendChild(sec);
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

  async function loadDesk(host, refresh) {
    const epoch = state.dataEpoch;
    if (!refresh) {
      host.innerHTML = '<p class="empty-note">' + t("载入中…") + "</p>";
      syncShown = null;
    }
    let data;
    try {
      // Who I am first (it keys "last seen"), then the rest side by side.
      if (!ctx.me || ctx.project !== projectKey()) ctx.me = await apiGet("/api/me").catch(() => null);
      const since = deskSince();
      [, data] = await Promise.all([
        fetchContext(),
        apiGet("/api/desk" + (since ? "?since=" + encodeURIComponent(since) : "")),
      ]);
    } catch (err) {
      if (epoch !== state.dataEpoch) return;
      renderViewFailure(host, err);
      return;
    }
    if (epoch !== state.dataEpoch) return;
    renderSidebar();
    host.innerHTML = "";
    const desk = mk("div", "desk");
    if (ctx.mode === "none") {
      desk.appendChild(mk("p", "desk-alone", t("你还不是这个项目的成员：请 PI 把你加入团队，然后同步")));
    }
    if (ctx.mode === "pi") {
      renderWaiting(desk);
      renderChanges(desk, data);
      renderResults(desk, data);
      renderAbandoned(desk, data);
    } else if (ctx.mode === "member") {
      // 12.0 names no role exception: a member's desk also says which
      // results lost their evidence and which routes were abandoned.
      renderMine(desk);
      renderOfficial(desk);
      renderChanges(desk, data);
      renderResults(desk, data);
      renderAbandoned(desk, data);
    } else {
      renderChanges(desk, data);
      renderResults(desk, data);
      renderAbandoned(desk, data);
    }
    host.appendChild(desk);
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
    loadDesk, refreshSidebar, renderSidebar, verdictInline, statusLabel, ensureContext, context,
    kindWord, stateWord, verdictWord, proposalWhat, proposalStateText, nameOf, whenWords,
    // pure, for tests/test_v8_u2.py
    _whenWords: whenWords, _shareLineText: shareLineText, _proposalWhat: proposalWhat,
    _proposalStateText: proposalStateText, _seenKey: seenKey, _toneOf: toneOf,
  });
})();
