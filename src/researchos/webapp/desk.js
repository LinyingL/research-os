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

  // -- pure wording (loaded into node by tests/test_v8_u2.py) ------------------

  const KIND_WORDS = { attempt_new: "新尝试", verdict_change: "判决变更", variable_version: "变量定义", files: "文件" };
  const STATE_WORDS = {
    pending: "等待 PI 批准", conflict: "批准与退回的记录互相矛盾", approved: "已批准",
    returned: "已退回", superseded: "已被你后来的提议替代",
  };
  const FILE_ACTION_WORDS = { added: "新增", changed: "改动", removed: "删除" };
  const VERDICT_TONES = {
    current: "olive", abandoned: "gray", pending: "ochre", adverse: "clay",
    rejected: "plain", archived: "plain", labelled: "plain", other: "plain",
  };
  const DESK_DAYS = 7;

  function pad2(n) { return String(n).padStart(2, "0"); }

  // When something happened, as a person says it: 今天 14:02, 昨天 09:10,
  // 10月4日, 2025年10月4日. `now` is a Date (the tests pass one).
  function whenWords(at, now) {
    const d = new Date(String(at || ""));
    if (isNaN(d.getTime())) return "";
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const days = Math.round((today - day) / 86400000);
    const hm = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    if (days === 0) return "今天 " + hm;
    if (days === 1) return "昨天 " + hm;
    const md = (d.getMonth() + 1) + "月" + d.getDate() + "日";
    return d.getFullYear() === now.getFullYear() ? md : d.getFullYear() + "年" + md;
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

  // The shared drive's status line (12.9), or "" for a project without one.
  function shareLineText(share, now) {
    if (!share || !share.shared) return "";
    if (!share.reachable) return "公盘现在打不开";
    const last = share.last_snapshot;
    const unverified = share.verification && share.verification !== "verified";
    if (share.role === "pi") {
      if (!last) return "还没有发布过";
      const pending = share.pending_publish && share.pending_publish.count > 0;
      return (pending ? "有改动尚未发布 · " : "") + "已发布 " + whenWords(last.at, now);
    }
    if (share.joined) {
      if (share.refused && share.refused.length) return "公盘上有未通过验证的版本";
      if (share.behind) return "有新的官方版本";
      const synced = share.joined.synced;
      return (unverified ? "未验证 · " : "") + (synced ? "已同步 " + whenWords(synced, now) : "已同步");
    }
    return "";
  }

  // A proposal in one line: what approving it would do.
  function proposalWhat(p) {
    const files = Array.isArray(p.files) ? p.files : [];
    const filesPart = files.length ? "，附带 " + files.length + " 个文件" : "";
    if (p.kind === "attempt_new" && p.attempt) {
      const a = p.attempt;
      return "新增尝试 #" + String(a.number || "").replace(/^#/, "") + "：" + (a.description || "（无描述）") + filesPart;
    }
    if (p.kind === "verdict_change" && p.verdict) {
      const v = p.verdict;
      const was = v.old_status ? v.old_status.label : "";
      const became = v.new_status ? v.new_status.label : "";
      return "#" + String(v.number || "").replace(/^#/, "") + " 的判决从「" + was + "」改为「" + became + "」" +
        (v.abandons ? "，放弃这条路线" : "") + filesPart;
    }
    if (p.kind === "variable_version" && p.variable) {
      return "变量 " + (p.variable.card || "") + " 的第 " + (p.variable.version || "") + " 版定义" + filesPart;
    }
    if (files.length) {
      const names = files.slice(0, 2).map((f) => String(f.path || "").split("/").pop()).join("、");
      return files.length + " 个文件：" + names + (files.length > 2 ? " 等" : "");
    }
    return "";
  }

  function lastOutcome(p, act) {
    const outs = (p.outcomes || []).filter((o) => o.act === act);
    return outs.length ? outs[outs.length - 1] : null;
  }

  // How a member reads the state of one of their own proposals.
  function proposalStateText(p) {
    const base = Object.prototype.hasOwnProperty.call(STATE_WORDS, p.state) ? STATE_WORDS[p.state] : String(p.state || "");
    if (p.state === "returned") {
      const o = lastOutcome(p, "returned");
      return base + (o && o.text ? "：" + o.text : "");
    }
    if (p.state === "pending") {
      const talk = lastOutcome(p, "discussed") || lastOutcome(p, "discuss");
      return base + (talk && talk.text ? " · PI 说：" + talk.text : "");
    }
    return base;
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
    const span = mk("span", "status-label tone-" + toneOf(status), status && status.label ? status.label : "其它");
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
    count.title = n > 0 ? n + " 条提议等你批准" : "";
    const line = state.noProject ? "" : shareLineText(ctx.share, now());
    shareLine.textContent = line;
    shareLine.classList.toggle("hidden", !line);
    shareLine.title = ctx.share && ctx.share.shared ? "公盘：" + ctx.share.shared : "";
    meLine.textContent = "";
    // U4: a member proposes a new attempt; someone not in the team yet
    // cannot propose at all.
    const add = document.getElementById("new-attempt-btn");
    if (add) {
      add.textContent = ctx.mode === "member" ? "提议新的尝试" : "新增尝试";
      add.dataset.role = ctx.mode || "";
    }
    const me = ctx.me && ctx.me.me;
    if (me) {
      meLine.append(mk("span", "me-name", me.name || me.id));
      const role = ctx.mode === "pi" ? "PI" : ctx.mode === "member" ? "成员" : ctx.mode === "none" ? "尚未加入团队" : "";
      if (role && !state.noProject) meLine.append(mk("span", "me-role", role));
      meLine.title = me.id || "";
    } else {
      meLine.append(mk("span", "me-unset", "还没有设置你是谁"));
      meLine.title = "在「成员」一栏里设置";
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
    const who = (item.who || []).join("、");
    if (item.kind === "file") {
      head.appendChild(rowLink(item.path, "mono", () => RONode.openNode(item.node || item.path), "打开这个文件"));
      head.appendChild(mk("span", "desk-what", item.what + (item.uncommitted ? "（还没有提交）" : "")));
      if (item.exists === false) head.appendChild(mk("span", "desk-flag", "文件不存在"));
    } else if (item.kind === "attempt") {
      head.appendChild(rowLink(attemptName(item) || item.node, "", () => openAttempt(item.node), "打开这条尝试"));
      if (item.what === "判决改了" && item.old_status) {
        const change = mk("span", "desk-what");
        change.append("判决 ", verdictInline(item.old_status), " 改为 ", verdictInline(item.new_status));
        head.appendChild(change);
      } else if (item.what === "新增尝试") {
        const change = mk("span", "desk-what");
        change.append("新增的尝试 ", verdictInline(item.new_status));
        head.appendChild(change);
      } else {
        head.appendChild(mk("span", "desk-what", "结果改为：" + (item.result || "（空）")));
      }
    } else if (item.kind === "decision") {
      const what = item.act === "abandoned" ? "放弃了这条路线" : "批准了" + (KIND_WORDS[item.proposal_kind] ? "一条" + KIND_WORDS[item.proposal_kind] + "提议" : "一条提议");
      if (item.node) head.appendChild(rowLink(attemptName(item) || item.node, "", () => openAttempt(item.node), "打开这条尝试"));
      head.appendChild(mk("span", "desk-what", what));
      if (item.verdict_status) head.appendChild(verdictInline(item.verdict_status));
    } else if (item.kind === "note") {
      const target = item.path || item.node || "";
      head.appendChild(mk("span", "desk-what", "笔记，写在"));
      head.appendChild(rowLink(target, item.path ? "mono" : "", () => RONode.openNode(item.node || item.path)));
    }
    main.appendChild(head);
    const meta = [];
    if (item.kind === "decision") {
      meta.push("提议人 " + who);
      if (item.approved_by && item.approved_by.length) meta.push("批准人 " + item.approved_by.join("、"));
    } else if (who) {
      meta.push(who);
      if (item.approved_by && item.approved_by.length) meta.push("经 " + item.approved_by.join("、") + " 批准");
    }
    if (item.versions > 1) meta.push("这段时间里改了 " + item.versions + " 次");
    main.appendChild(metaLine(meta));
    const quotes = item.kind === "note" ? [item.text] : (item.notes || []);
    if (item.kind === "decision" && item.reason) quotes.unshift("理由：" + item.reason);
    quotes.filter(Boolean).slice(0, 3).forEach((q) => main.appendChild(mk("p", "desk-note", q)));
    row.append(at, main);
    return row;
  }

  function renderChanges(host, data) {
    const sec = block("你上次查看以来的改动");
    const since = data.since_default
      ? "还没有记录你上次查看的时间，这里列出过去 " + (data.default_days || DESK_DAYS) + " 天的改动。"
      : "上次查看：" + whenWords(data.since, now());
    sec.appendChild(mk("p", "desk-sub", since));
    const changes = data.changes || [];
    if (!changes.length) {
      sec.appendChild(emptyLine("这段时间里没有改动。"));
    } else {
      const ul = mk("ul", "desk-list");
      const team = (ctx.share && ctx.share.team) || [];
      changes.forEach((c) => ul.appendChild(renderChange(c, team)));
      sec.appendChild(ul);
      if (data.more_changes > 0) sec.appendChild(mk("p", "desk-sub", "更早的改动没有列出，可以在各个文件的历史里查看。"));
    }
    host.appendChild(sec);
  }

  function renderResults(host, data) {
    const sec = block("依据变了的成果");
    const results = data.results || [];
    const review = typeof data.review === "number" ? data.review : 0;
    if (!results.length && !review) {
      sec.appendChild(emptyLine("所有成果的来源都还在。"));
      host.appendChild(sec);
      return;
    }
    if (results.length) {
      const ul = mk("ul", "desk-list");
      results.forEach((r) => {
        const row = mk("li", "desk-row");
        const main = mk("div", "desk-main");
        const head = mk("div", "desk-head");
        head.appendChild(rowLink(r.path || r.node, "mono", () => RONode.openNode(r.node || r.path), "打开这个成果"));
        if (r.exists === false) head.appendChild(mk("span", "desk-flag", "文件不存在"));
        main.appendChild(head);
        (r.breaks || []).forEach((b) => {
          const line = mk("p", "desk-break", b.label + (b.path && b.path !== r.path ? "：" + b.path : ""));
          if (b.repair) line.appendChild(mk("span", "desk-repair", "可以" + b.repair));
          main.appendChild(line);
        });
        if (r.review) {
          const line = mk("p", "desk-break", "它的来源里有 " + r.review + " 条关联的判断待复核");
          line.appendChild(rowLink("复核", "", () => openReviewPanel(null)));
          main.appendChild(line);
        }
        if (r.drafts && r.drafts.length) main.appendChild(metaLine(["文稿 " + r.drafts.join("、") + " 引用了它"]));
        row.appendChild(main);
        ul.appendChild(row);
      });
      sec.appendChild(ul);
    }
    if (review) {
      const line = mk("p", "desk-sub");
      line.append(review + " 条判断的依据变了，等你复核。");
      line.appendChild(rowLink("查看", "", () => openReviewPanel(null)));
      sec.appendChild(line);
    }
    host.appendChild(sec);
  }

  function renderAbandoned(host, data) {
    const sec = block("已放弃的路线");
    const list = data.abandoned || [];
    if (!list.length) {
      sec.appendChild(emptyLine("还没有放弃过的路线。"));
    } else {
      const line = mk("p", "desk-sub", list.length + " 条路线已经放弃，每条都留着当时的结果和理由。");
      line.appendChild(rowLink("在研究路线中查看", "", () => showAbandonedRoutes()));
      sec.appendChild(line);
    }
    host.appendChild(sec);
  }

  function renderProposalRow(p, team, forPi) {
    const row = mk("li", "desk-row");
    const main = mk("div", "desk-main");
    const head = mk("div", "desk-head");
    head.appendChild(mk("span", "status-label tone-plain", KIND_WORDS[p.kind] || "提议"));
    head.appendChild(mk("span", "desk-what strong", p.sentence || proposalWhat(p) || p.id));
    main.appendChild(head);
    const what = proposalWhat(p);
    if (what && p.sentence) main.appendChild(mk("p", "desk-note plain", what));
    const meta = forPi ? [nameOf(p.by, team)] : [proposalStateText(p)];
    main.appendChild(metaLine(meta));
    const actions = mk("div", "desk-actions");
    actions.appendChild(mkButton("查看", "btn btn-small", () => ROTeam.openProposal(p.id, p.by)));
    main.appendChild(actions);
    row.append(mk("span", "desk-when", whenWords(p.at, now())), main);
    return row;
  }

  function renderWaiting(host) {
    const sec = block("等你批准");
    const team = (ctx.share && ctx.share.team) || [];
    const list = ((ctx.proposals && ctx.proposals.proposals) || []).filter((p) => p.state === "pending" || p.state === "conflict");
    if (!list.length) sec.appendChild(emptyLine("没有等你批准的提议。"));
    else {
      const ul = mk("ul", "desk-list");
      list.forEach((p) => ul.appendChild(renderProposalRow(p, team, true)));
      sec.appendChild(ul);
    }
    host.appendChild(sec);
  }

  function renderMine(host) {
    const sec = block("我的提议");
    const team = (ctx.share && ctx.share.team) || [];
    const list = (ctx.proposals && ctx.proposals.proposals) || [];
    if (!list.length) sec.appendChild(emptyLine("你还没有提交过提议。"));
    else {
      const ul = mk("ul", "desk-list");
      list.forEach((p) => ul.appendChild(renderProposalRow(p, team, false)));
      sec.appendChild(ul);
    }
    host.appendChild(sec);
  }

  function renderOfficial(host) {
    const sec = block("官方版本有更新");
    const share = ctx.share || {};
    const last = share.last_snapshot;
    if (!share.shared) {
      sec.appendChild(emptyLine("这个工作副本还没有连上公盘。"));
    } else if (!share.reachable) {
      sec.appendChild(emptyLine("公盘现在打不开，等它同步好再来看。"));
    } else if (share.behind && last) {
      sec.querySelector(".desk-title").textContent = "官方版本有更新";
      const line = mk("p", "desk-sub", "PI 于 " + whenWords(last.at, now()) + " 发布了第 " + last.seq + " 版" +
        (last.note ? "：" + last.note : "。"));
      sec.appendChild(line);
      const status = mk("div", "notice-status");
      const btn = mkButton("同步", "btn", () => syncNow(btn, status));
      const actions = mk("div", "desk-actions");
      actions.append(btn);
      sec.append(actions, status);
      sec.appendChild(mk("p", "desk-sub", "你改过的文件不会被覆盖：官方的那一份会放在它旁边。"));
    } else {
      sec.querySelector(".desk-title").textContent = "官方版本";
      sec.appendChild(emptyLine(last ? "你的工作副本已经是最新的官方版本（第 " + last.seq + " 版）。" : "PI 还没有发布过官方版本。"));
    }
    host.appendChild(sec);
  }

  async function syncNow(btn, status) {
    btn.disabled = true;
    setStatus(status, "", "正在同步…");
    let res;
    try {
      res = await apiPost("/api/share/sync", {});
    } catch (err) {
      btn.disabled = false;
      setStatus(status, "err", "没能同步", err);
      return;
    }
    const kept = Array.isArray(res.kept_yours) ? res.kept_yours.length : 0;
    setStatus(status, "ok", "已同步到第 " + (res.seq != null ? res.seq : "") + " 版" +
      (kept ? "；你改过的 " + kept + " 个文件保留了你的版本，官方的那一份放在旁边" : "") + "。");
    refreshSidebar();
  }

  async function loadDesk(host, refresh) {
    const epoch = state.dataEpoch;
    if (!refresh) host.innerHTML = '<p class="empty-note">载入中…</p>';
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
      desk.appendChild(mk("p", "desk-alone", "你还不是这个项目的成员：请 PI 把你加入团队，然后同步"));
    }
    if (ctx.mode === "pi") {
      renderWaiting(desk);
      renderChanges(desk, data);
      renderResults(desk, data);
      renderAbandoned(desk, data);
    } else if (ctx.mode === "member") {
      renderMine(desk);
      renderOfficial(desk);
      renderChanges(desk, data);
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
    KIND_WORDS, STATE_WORDS, FILE_ACTION_WORDS, proposalWhat, proposalStateText, nameOf, whenWords,
    // pure, for tests/test_v8_u2.py
    _whenWords: whenWords, _shareLineText: shareLineText, _proposalWhat: proposalWhat,
    _proposalStateText: proposalStateText, _seenKey: seenKey, _toneOf: toneOf,
  });
})();
