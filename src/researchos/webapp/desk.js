// The sidebar's live lines and the page 研究路线 -- DESIGN.md 12.9, 12.11,
// Section 14 (task V10: the route map and the column beside it), 18.3
// (one page: the map or the grouped list, with the same column; the
// old 工作台 is retired, this module keeps the name "desk") and 21.4 (the
// column is 等你处理, the one list 项目首页 shows too). Served verbatim at
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
//                      column beside it (researchos.desk.payload), and what
//                      each attempt holds: its tasks and its latest handback
//                      (DESIGN.md 21.1); its `needs` are the rows of 等你处理;
//   /api/workspace/now?only=needs -- the same rows, for the sidebar's count
//                      when the page on screen is neither the desk nor 项目首页;
//   /api/review     -- an attempt's 待复核, judged in its panel (14.3);
//   /api/history    -- how its row moved, for the panel's timeline (14.5).
// and two writes: POST /api/share/sync, 「同步」 a member's working copy, and
// POST /api/workspace/run/aside, 「知道了」 on a failed run.
//
// 研究路线 is the route map with what waits for me beside it (14.1, 14.4)
// or, by the switch 路线图 / 列表 (18.3), the grouped list that team.js
// draws, with the same column beside it:
// 等你处理 (21.4): my unopened handbacks, the runs I started that wait,
// failed or went out of bounds, the PI's proposals to decide, a member's own,
// 待复核 under the attempt it belongs to, a member's 正式版本有更新 with
// 同步, the lines with a missing end -- folded to a strip when there is
// nothing. What changed since I last looked is 「有改动」
// on the cards, abandoned routes are the quieter cards (14.5). No
// statistics, no charts, no counters for their own sake (the one count is
// the sidebar's, beside 项目首页, 21.4). Nothing typed on
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
      case "verdict_change": return t("研究判断变更");
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

  // An ISO time as a Date, or null. A log writes the T as a space and a clock may write more fractional digits than a Date reads
  // (microseconds): both are read. A time written with no zone is this Mac's own (the engine writes `+02:00` or `Z` on every
  // time it keeps; the stamps of a person's own files carry none).
  function momentOf(at) {
    const text = at == null ? "" : String(at).trim();
    if (!text) return null;
    const d = new Date(text.replace(/^(\d{4}-\d{2}-\d{2}) (?=\d{2}:\d{2})/, "$1T").replace(/(\.\d{3})\d+/, "$1"));
    return isNaN(d.getTime()) ? null : d;
  }
  const ISO_TIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;

  // When something happened, as a person says it: 今天 14:02, 昨天 09:10,
  // 10月4日, 2025年10月4日; in English (13.5) Today 14:02, Yesterday 09:10,
  // 4 Oct, 4 Oct 2025. `now` is a Date (the tests pass one).
  // Every time the app shows is the viewer's own local time (DESIGN.md 21.8), never UTC and never the time of the machine that
  // wrote it, and this is the one function that turns a stored time into words, in two forms. The third argument names the
  // other: "full", 2026-10-30 16:33, for a history line, a run's head and the like where the minute is the point (24-hour as
  // 13.5 writes the time, in both languages), and "day", 2026-10-30. A time with hours and minutes is read for them; anything
  // else -- a day, a label, nothing -- gives "" in those forms and the caller keeps what was written.
  function whenWords(at, now, form) {
    const d = momentOf(at);
    if (!d) return "";
    if (form === "full" || form === "day") {
      if (!ISO_TIME.test(String(at).trim())) return "";
      const date = d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
      return form === "day" ? date : date + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    }
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

  // A long number as it is shown (DESIGN.md 21.8): a value with more than six significant digits to four (9.509441061106294e-05
  // is 9.509e-05, 0.008063173780026744 is 0.008063); an integer, a value of six digits or fewer, and anything that is
  // not a plain decimal -- with a unit, a percent sign, a word, a separator -- as written. The significant digits are
  // the digits as written from the first that is not zero, trailing zeros after a decimal point among them; rounding
  // is on those digits (half up), not on a float. A value written with an exponent keeps its form (e-05, two digits at
  // least); a plain one stays plain unless four digits cannot hold its integer part. The caller shows the value as
  // written on hover (`title`) and keeps it as written in the record. Anywhere a table or a list shows a long number
  // uses this one function (batch 5 reuses it).
  const NUMBER_KEEP = 6, NUMBER_SHOW = 4;
  const PLAIN_NUMBER = /^([+-]?)(\d*)(?:\.(\d*))?(?:([eE])([+-]?)(\d+))?$/;
  function formatNumber(value) {
    const text = value == null ? "" : String(value);
    const m = PLAIN_NUMBER.exec(text);
    if (!m || (m[3] === undefined && m[4] === undefined)) return text;      // not a plain decimal, or an integer
    const whole = m[2], all = whole + (m[3] || "");
    const first = all.search(/[1-9]/);
    if (first < 0) return text;
    const digits = all.slice(first);
    if (digits.length <= NUMBER_KEEP) return text;
    let kept = digits.slice(0, NUMBER_SHOW), power = whole.length - 1 - first + (m[4] ? parseInt(m[5] + m[6], 10) : 0);
    if (digits.charAt(NUMBER_SHOW) >= "5") {
      kept = String(Number(kept) + 1);
      if (kept.length > NUMBER_SHOW) { kept = kept.slice(0, NUMBER_SHOW); power += 1; }
    }
    if (m[4] !== undefined || power >= NUMBER_SHOW) {
      const exponent = String(Math.abs(power)).padStart(2, "0");
      return m[1] + kept.charAt(0) + "." + kept.slice(1) + (m[4] || "e") + (power < 0 ? "-" : "+") + exponent;
    }
    if (power < 0) return m[1] + "0." + "0".repeat(-power - 1) + kept;
    return m[1] + kept.slice(0, power + 1) + (kept.length > power + 1 ? "." + kept.slice(power + 1) : "");
  }

  // A number in a table or a list the app draws from a data file (21.8): the node shows formatNumber's text and, whenever
  // that is shorter than the value, the value as written is on hover (`title`). The text of a note or a report, which a
  // person or an agent wrote, never comes here: it is shown as written. Returns the node.
  function showNumber(node, value) {
    const written = value == null ? "" : String(value), shown = formatNumber(written);
    node.textContent = shown;
    if (shown !== written) node.title = written;
    return node;
  }

  function toneOf(status) {
    const s = status && status.status;
    return Object.prototype.hasOwnProperty.call(VERDICT_TONES, s) ? VERDICT_TONES[s] : "plain";
  }

  // The first time a project's map is opened in this browser (21.8: the map then fits all its cards): remembered per project, as the
  // way the page is looked at is (18.3), a per-viewer convenience -- with storage off, every opening is the first.
  function fittedKey(project) {
    return "ro.routes.fitted:" + String(project || "");
  }
  function firstMapOpen(project) {
    if (!project) return false;
    const key = fittedKey(project), seen = storageGet(key) === "1";
    if (!seen) storageSet(key, "1");
    return !seen;
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

  // ... and of the way 研究路线 is looked at, 路线图 or 列表 (18.3): per
  // project, in this browser, a per-viewer convenience.
  function modeKey(project) {
    return "ro.routes.mode:" + String(project || "");
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
      if (share.behind) return t("有新的正式版本");
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
      return t("#{number} 的研究判断从「{was}」改为「{became}」", { number: String(v.number || "").replace(/^#/, ""),
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

  const ctx = { me: null, share: null, proposals: null, mode: null, single: false, project: null, epoch: -1 };
  let needsState = { project: null, rows: [] }; // the rows of 等你处理 (21.4): the sidebar counts them, two lists draw them
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
    // A project of one person (18.3) is the engine's own "single" with no team file listing anyone: a PI
    // alone in the file is "single" to the engine (12.1) and has a team to set up, so for the sidebar the
    // team file counts. A read that failed, or a team file that cannot be read ("unknown"), also leave
    // `mode` at "single" for the pages that always did, and are never taken for one person.
    ctx.single = role === "single" && !(proposals && proposals.team === true);
    ctx.project = projectKey();
    ctx.epoch = epoch;
    return true;
  }

  // The context, read once per project at a time (14.4): /api/proposals and
  // /api/share read the shared folder and are not bounded in time, so a
  // folder that hangs holds one pair of requests, never one per load --
  // the desk's, the sidebar's and the panel's wait share it.
  let contextLoad = null;
  function loadContext() {
    if (!contextLoad || contextLoad.epoch !== state.dataEpoch) {
      const p = fetchContext().finally(() => { if (contextLoad === p) contextLoad = null; });
      p.epoch = state.dataEpoch;
      contextLoad = p;
    }
    return contextLoad;
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
      await loadContext();
    } catch (err) {
      /* the lines keep what they last said */
    }
    renderSidebar();
    readNeeds();
  }

  // A project of one person (12.1, 18.3): the engine said "single" for the open project and no team file
  // lists anyone (ctx.single). Not known yet, not read (the request failed, a team file that cannot be
  // read) is not that: the sidebar and the menus then show everything, as for a team.
  function onePerson() {
    return !state.noProject && ctx.single === true && ctx.project === projectKey() && ctx.epoch === state.dataEpoch;
  }

  // What the Mac's menus are told (the shell takes exactly these four, V8 U5): "single" hides 提议, 成员,
  // 提交提议 and 发布 there; a project the page could not read the role of is "none" -- 提议 and 成员 show,
  // the two that need a role do not.
  function shellMode() {
    if (state.noProject || onePerson()) return "single";
    return ctx.mode === "pi" || ctx.mode === "member" ? ctx.mode : "none";
  }

  // 等你处理 (21.4) is one list the engine works out (researchos.now_doing.needs_you): the person's unopened
  // handbacks, the runs waiting for an answer or failed, the PI's proposals to decide, a member's own, a newer official
  // version, the judgments to review, the lines with a missing end. A returned proposal its author said 「知道了」 to
  // (kept in this browser, 18.3) has left it. The sidebar counts its rows beside 项目首页, in a project of one too.
  function waitingRows(rows) {
    return (rows || []).filter((r) => !(r.kind === "mine" && r.proposal && r.proposal.state === "returned" && gotIt().has(String(r.proposal.id))));
  }

  function needsCount() {
    return needsState.project === projectKey() ? waitingRows(needsState.rows).length : 0;
  }

  function setNeeds(needs) {
    needsState = { project: projectKey(), rows: (needs && needs.rows) || [] };
  }

  // What reads the list takes it in here: the page's own reading sets the count (the desk's, 项目首页's); from any
  // other page it is read here, `only=needs`, one request at a time -- a refresh that came meanwhile reads once more.
  function noteNeeds(needs) {
    setNeeds(needs);
    renderSidebar();
  }

  function onHome() { return typeof currentView !== "undefined" && currentView === "workspace"; }

  let needsBusy = false;
  let needsAgain = false;
  async function readNeeds() {
    if (onDesk() || onHome() || state.noProject) return;
    if (needsBusy) { needsAgain = true; return; }
    needsBusy = true;
    const epoch = state.dataEpoch;
    try {
      const data = await apiGet("/api/workspace/now?only=needs");
      if (epoch === state.dataEpoch && !onDesk() && !onHome()) noteNeeds(data.needs);
    } catch (err) {
      /* the count keeps what it last said */
    } finally {
      needsBusy = false;
      if (needsAgain) { needsAgain = false; readNeeds(); }
    }
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
    // The shell's menus show 提交提议… or 发布 by role (V8 U5), and 提议 and 成员 unless the project is one
    // person's (18.3).
    if (typeof postShellMode === "function") postShellMode(shellMode());
    // 18.3: the sidebar of a project of one person has no 提议 and no 成员 (app.html's stylesheet reads this
    // mark). With no project there is no sidebar to speak of, and the mark stays as the page starts, so a
    // project added next does not show the two for a moment.
    const nav = document.getElementById("side-nav");
    if (nav && nav.dataset) nav.dataset.team = state.noProject || onePerson() ? "single" : "team";
    const n = state.noProject ? 0 : waitingForMe();
    count.textContent = n > 0 ? String(n) : "";
    count.title = n > 0 ? t("{n} 条提议等你批准", { n: n }) : "";
    // 21.4: the count of 等你处理 beside 项目首页 (one count, in a project of one person too).
    const home = document.getElementById("side-count-home");
    if (home) {
      const waiting = state.noProject ? 0 : needsCount();
      home.textContent = waiting > 0 ? String(waiting) : "";
      home.title = waiting > 0 ? t("{n} 项等你处理", { n: waiting }) : "";
    }
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
    } else if (!onePerson()) {
      meLine.append(mk("span", "me-unset", t("还没有设置你是谁")));
      meLine.title = t("在「成员」一栏里设置");
    } else {
      meLine.title = ""; // 18.3: a project of one person does not ask who you are; 开始协作… does
    }
  }

  // -- the desk: the route map and the column beside it (DESIGN.md 14.1, 14.4, 14.5) -------
  // Both are made once and kept: a refresh hands the map the new payload and
  // redraws the column's contents in place, so the map keeps its camera, its
  // selection and a drag in progress, and the column its scroll. Without
  // /routemap.js (this file run alone) the column alone.

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

  // A task's owner as the engine labels it. The researcher with no name set is stored as the text 「我」 whatever the screen's language
  // (21.7), and the engine gives that text back as it is; the screen reads it as 项目首页 does (workspace.js `ownerLabel`): "Me" in a
  // project of one person, the project's first researcher in a team's, where it is a task of the solo period and not the viewer's.
  function ownerWords(item) {
    if (item.owner !== "我") return item.owner_label;
    return deskRole() === "single" ? t("我") : t("最初的研究者");
  }

  let routeMap = null;
  let layoutEl = null;
  let mapEl = null;             // the map's element, hidden while the page is a list
  let listEl = null;            // the list's pane (team.js loadRoutes), made when first wanted
  let columnEl = null;
  let drawnEpoch = -1;          // the project (state.dataEpoch) the desk last showed
  let listDrawn = -1;           // ... and the one the list last showed
  let listZoomed = "";          // the text zoom the list is shown at ("" for none)
  let picked = null;            // the column's row picked: { key, ids }
  const drawnKeys = new Set();  // the rows the column shows now

  // -- 路线图 or 列表 (DESIGN.md 18.3) ----------------------------------------------------
  // One page, one 等你处理 column; beside it the route map or the grouped list
  // (team.js loadRoutes, in a pane of its own). The choice is kept per project
  // in this browser -- the map until the researcher picks the list -- and the
  // switch in the page's header (app.html #routes-switch) shows it.

  const MODES = ["map", "list"];
  const modeSeen = new Map(); // project key -> the mode this page session shows (storage may be off)

  function routesMode() {
    const project = projectKey();
    if (!modeSeen.has(project)) {
      const stored = storageGet(modeKey(project));
      modeSeen.set(project, MODES.indexOf(stored) >= 0 ? stored : "map");
    }
    return modeSeen.get(project);
  }

  function listShown() { return routesMode() === "list"; }

  function onDesk() { return typeof currentView !== "undefined" && currentView === "desk"; }

  // Another page is on screen (a page that knows no views is taken for the desk, as the desk's own tests run it).
  function offDesk() { return typeof currentView !== "undefined" && currentView !== "desk"; }

  // The switch's buttons marked as the other switches mark theirs.
  function syncSwitch() {
    const bar = typeof document !== "undefined" && document.getElementById ? document.getElementById("routes-switch") : null;
    if (!bar || typeof bar.querySelectorAll !== "function") return;
    const mode = routesMode();
    Array.from(bar.querySelectorAll(".subnav-item")).forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
  }

  // The list's pane, between the map and the column.
  function listPane() {
    if (!listEl) {
      listEl = mk("div", "routes-pane hidden");
      if (listZoomed) listEl.style.zoom = listZoomed;
      layoutEl.insertBefore(listEl, columnEl);
    }
    return listEl;
  }

  // The map's element and the list's shown or hidden to match the mode.
  function showMode() {
    syncSwitch();
    if (!layoutEl) return;
    const list = listShown();
    if (mapEl) mapEl.classList.toggle("hidden", list);
    if (list) listPane().classList.remove("hidden");
    else if (listEl) listEl.classList.add("hidden");
  }

  // The list drawn (list mode only): in place when it was drawn already for
  // this project and `refresh`, else with 载入中… first. What the column has
  // picked stays marked in it.
  function loadList(refresh, acted) {
    if (!listShown() || !layoutEl || !window.ROTeam || !ROTeam.loadRoutes) return null;
    const epoch = state.dataEpoch;
    const pane = listPane();
    return Promise.resolve(ROTeam.loadRoutes(pane, !!refresh && listDrawn === epoch, acted)).then(() => {
      if (epoch !== state.dataEpoch) return;
      listDrawn = epoch;
      markListRows(picked ? picked.ids : [], false);
    }).catch((err) => { if (typeof console !== "undefined") console.error(err); });
  }

  // The page looks the way it is kept: shown, the column's hints worded for
  // it, the picked row marked in what is on screen. `reload`: the list read
  // again even if drawn already (the researcher just switched to it).
  function applyMode(reload) {
    showMode();
    if (lastData && columnEl) renderColumn(columnEl, lastData);
    if (listShown()) return reload || listDrawn !== state.dataEpoch ? loadList(true) : null;
    if (routeMap) routeMap.highlight(picked ? picked.ids : []);
    return null;
  }

  // The switch (app.html), or a caller that means one of the two. Kept at
  // once; drawn at once when the page is on screen, else when it is next
  // opened (resume).
  function setMode(mode) {
    if (MODES.indexOf(mode) < 0) return null;
    const project = projectKey();
    modeSeen.set(project, mode);
    storageSet(modeKey(project), mode);
    syncSwitch();
    return layoutEl && onDesk() ? applyMode(true) : null;
  }

  // The page opened again with its drawing kept (app.html activateView).
  function resume() {
    syncSwitch();
    return layoutEl ? applyMode(false) : null;
  }

  // The attempts' rows of the list marked, as the map marks their cards
  // (14.4); the first is brought into view when `scroll`.
  function markListRows(ids, scroll) {
    if (!listEl || typeof listEl.querySelectorAll !== "function") return;
    const want = new Set(ids);
    let first = null;
    Array.from(listEl.querySelectorAll(".route-row")).forEach((row) => {
      const on = want.has(row.dataset.node);
      row.classList.toggle("picked", on);
      if (on && !first) first = row;
    });
    if (scroll && first && typeof first.scrollIntoView === "function") first.scrollIntoView({ block: "nearest" });
  }

  // The list zooms as the reading pages do (app.html shellZoom); the map keeps
  // its own camera.
  function listZoom(value) {
    listZoomed = value;
    if (listEl) listEl.style.zoom = value;
  }

  // A row of the column picked (14.4): the attempts it touches are marked on
  // the map -- or, in the list, on their rows -- and brought into view;
  // picked again, unmarked. `on` true: marked whatever it was (its proposal
  // opened in the panel).
  function pick(key, ids, on) {
    picked = on || !(picked && picked.key === key) ? { key: key, ids: ids } : null;
    if (columnEl) columnEl.querySelectorAll("[data-pick]").forEach((r) => r.classList.toggle("picked", !!picked && r.dataset.pick === picked.key));
    if (listShown()) markListRows(picked ? picked.ids : [], true);
    else if (routeMap) routeMap.highlight(picked ? picked.ids : []);
  }

  // `title`: what marking it does, when the row is not a proposal's -- worded
  // for the map or for the list, whichever the page is.
  function pickLink(row, text, key, ids, title) {
    row.dataset.pick = key;
    drawnKeys.add(key);
    if (picked && picked.key === key) row.classList.add("picked");
    const hint = title || (listShown() ? t("在列表中标出它涉及的尝试") : t("在路线图上标出它涉及的尝试"));
    return rowLink(text, "strong", () => pick(key, ids), ids.length ? hint : "");
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

  function gotIt() {
    const key = gotItKey(projectKey(), meId());
    if (!gotItNow.has(key)) {
      let kept = [];
      try { kept = JSON.parse(storageGet(key) || "[]"); } catch (err) { kept = []; }
      gotItNow.set(key, new Set(Array.isArray(kept) ? kept.map(String) : []));
    }
    return gotItNow.get(key);
  }

  // 「知道了」 on a returned proposal: it leaves 等你处理 and the attempt panel's part of it.
  function sayGotIt(p) {
    const ids = gotIt();
    ids.add(String(p.id));
    storageSet(gotItKey(projectKey(), meId()), JSON.stringify(Array.from(ids).slice(-GOT_IT_KEPT)));
    renderSidebar();
    if (columnEl && lastData) renderColumn(columnEl, lastData);
    redrawLists();
    const s = state.panelSubject;
    return s && s.kind === "desk-attempt" && panelShown() ? drawPanel(s) : null;
  }

  // A proposal in the column (14.4): what approving it would do, who and
  // when -- or, for its author, how it stands. Its title marks the attempts
  // it touches; 「查看」 opens it in the panel (14.3). In an attempt's panel
  // (`subject`), 「查看」 opens it in place, under its row.
  function proposalRow(p, forPi, subject, plain) {
    const row = mk("li", "desk-row");
    const main = mk("div", "desk-main");
    const head = mk("div", "desk-head");
    const what = proposalWhat(p);
    const name = p.sentence || what || p.id;
    head.appendChild(mk("span", "status-label tone-plain", kindWord(p.kind) || t("提议", { n: 1 })));
    head.appendChild(subject || plain ? mk("span", "desk-what", name) : pickLink(row, name, "p:" + p.id, p.touches || []));
    main.appendChild(head);
    if (what && p.sentence) main.appendChild(mk("p", "desk-note plain", what));
    const state = forPi ? (p.state === "conflict" ? stateWord(p.state) : "") : mineState(p);
    main.appendChild(metaLine([forPi ? p.by_label || p.by : "", state || (forPi && !subject ? t("等你批准") : ""), whenWords(p.at, now())]));
    const actions = mk("div", "desk-actions");
    main.appendChild(actions);
    if (!subject) {
      actions.appendChild(mkButton(t("查看"), "ws-text-link", () => openProposal(p)));
      if (!forPi && p.state === "returned") actions.appendChild(mkButton(t("知道了"), "btn btn-small", () => sayGotIt(p)));
    } else {
      const key = (p.by || "") + "/" + p.id;
      const inner = mk("div", "dp-in-place");
      main.appendChild(inner);
      const open = () => { subject.open.add(key); actions.textContent = ""; openInPlace(subject, p, inner); };
      if (subject.open.has(key)) open();
      else actions.appendChild(mkButton(t("查看"), "ws-text-link", open));
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

  // -- 等你处理 (DESIGN.md 21.4) ------------------------------------------------------------------
  // One list, drawn here for 研究路线's column and, through `needsList`, for 项目首页 (workspace.js): the engine's rows
  // (researchos.now_doing.needs_you), each saying in a chip what it is, which attempt and task it concerns, and the
  // one action that handles it. Proposals, 待复核 judgments, a newer official version and a line with a missing end
  // are what the column held (14.4); the person's unopened handbacks and their runs that wait, failed or went out of
  // bounds are the rest (21.2). Nothing is counted for its own sake: a row is a thing to do.

  function rowShell(chip, tone) {
    const row = mk("li", "desk-row");
    const main = mk("div", "desk-main");
    const head = mk("div", "desk-head");
    head.appendChild(mk("span", "status-label tone-" + tone, chip));
    main.appendChild(head);
    row.appendChild(main);
    return { row: row, main: main, head: head };
  }

  // Which attempt (opened in the panel, as a card of the map opens it) and which task a row is about.
  function concernLine(r) {
    const meta = mk("div", "desk-meta");
    if (r.attempt) meta.appendChild(rowLink(attemptName(r.attempt), "", () => openAttemptAnywhere(r.attempt.id)));
    if (r.task && r.task.text) meta.appendChild(mk("span", "desk-task", (r.attempt ? " · " : "") + r.task.text));
    return meta;
  }

  function handbackRow(r) {
    const s = rowShell(t("交回"), "ochre");
    s.head.appendChild(mk("span", "desk-what", r.title));
    if (r.headline) s.main.appendChild(mk("p", "desk-note plain", r.headline));
    s.main.appendChild(concernLine(r));
    const actions = mk("div", "desk-actions");
    actions.appendChild(mkButton(t("打开交回"), "ws-text-link", () => openHandback(r.report, null)));
    s.main.appendChild(actions);
    return s.row;
  }

  // The states of a run that ask the person who started it (attempt_tasks.ATTENTION): the words are the task row's.
  const RUN_CHIPS = {
    answer: [() => t("等你回答"), "ochre"], failed: [() => t("失败"), "clay"], out_of_bounds: [() => t("越过了限制"), "clay"],
  };

  // The place that handles a run is its task's row on 项目首页: shown the way 「运行日志」 on a handback shows it.
  function goToRun(how, taskId) {
    const home = typeof currentView === "undefined" || currentView === "workspace";
    if (home && window.ROWorkspace) return how === "answer" ? window.ROWorkspace.answerRun(taskId) : window.ROWorkspace.showRunLog(taskId);
    if (how === "answer" && typeof answerRunOnHome === "function") return answerRunOnHome(taskId);
    return typeof openRunLogOnHome === "function" ? openRunLogOnHome(taskId) : null;
  }

  // 「知道了」 on a failed run (kept on this Mac, in the Research OS home, not in the project): its row leaves the list.
  async function setAside(r, btn, status) {
    btn.disabled = true;
    try {
      await apiPost("/api/workspace/run/aside", { project: state.currentPath, run: r.run });
    } catch (err) {
      btn.disabled = false;
      setStatus(status, "err", t("没能记下你已经看过这一条。"), err);
      return;
    }
    await refreshAfterAct();
  }

  function runRow(r) {
    const chip = RUN_CHIPS[r.kind];
    const s = rowShell(chip[0](), chip[1]);
    if (r.kind === "answer" && r.question) s.head.appendChild(mk("span", "desk-what", r.question));
    s.main.appendChild(concernLine(r));
    const actions = mk("div", "desk-actions");
    const status = mk("div", "notice-status");
    if (r.kind === "answer") {
      actions.appendChild(mkButton(t("回答"), "btn btn-small", () => goToRun("answer", r.task.id)));
    } else {
      actions.appendChild(mkButton(t("运行日志"), "ws-text-link", () => goToRun("log", r.task.id)));
      const gotIt = mkButton(t("知道了"), "btn btn-small", () => setAside(r, gotIt, status));
      actions.appendChild(gotIt);
    }
    s.main.append(actions, status);
    return s.row;
  }

  // 待复核 (14.4): the judgments whose basis changed, a row for each attempt that holds one (14.3's membership rule) and
  // one for those that belong to none (claims, variable cards, files no attempt holds).
  function reviewRow(r, plain) {
    const s = rowShell(t("待复核"), "ochre");
    if (r.attempt) {
      const name = attemptName(r.attempt);
      s.head.appendChild(plain ? mk("span", "desk-what", name)
        : pickLink(s.row, name, r.key, r.attempts || [], listShown() ? t("在列表中标出这条尝试") : t("在路线图上标出这条尝试")));
      const own = ownMark(r);
      if (own) s.head.appendChild(own);
      s.head.appendChild(rowLink(t("复核"), "", () => openAttemptAnywhere(r.attempt.id, true))); // judged in its panel (14.3)
    } else {
      s.head.appendChild(mk("span", "desk-what", t("不属于任何尝试")));
      s.head.appendChild(rowLink(t("复核"), "", () => { if (mayLeavePanel()) openReviewPanel(null); }));
    }
    s.main.appendChild(mk("p", "desk-sub", t("{n} 项的依据变了，等你复核。", { n: numberText(r.n) })));
    s.main.appendChild(reviewItems(r.items || [], r.cards || []));
    return s.row;
  }

  // 「连线的一端找不到了」 (14.2): a line whose end the table no longer has (a renumbered row) waits in the record; it is
  // said here, never dropped.
  function missingRow(r) {
    const l = r.line || {};
    const s = rowShell(t("连线的一端找不到了"), "clay");
    s.head.appendChild(mk("span", "desk-what", t("#{child} 源自 #{parent}", { child: l.child_number, parent: l.parent_number })));
    const own = ownMark(l);
    if (own) s.head.appendChild(own);
    const gone = (l.missing || []).map((end) => t("尝试表里没有 #{n}", { n: end === "parent" ? l.parent_number : l.child_number }));
    s.main.appendChild(metaLine(gone.concat(l.by_label || "")));
    if (l.note) s.main.appendChild(mk("p", "desk-note", l.note));
    return s.row;
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

  // 正式版本有更新 and 同步 (14.4, a member), as the desk had them: what 同步
  // will write is said first (the engine's sync-preview), and the button
  // waits for it; after a sync, what it did. A shared folder not set or not
  // open is the column's notice instead.
  function officialRow(last) {
    const s = rowShell(t("正式版本有更新"), "ochre");
    s.main.appendChild(mk("p", "desk-sub", publishedWords(last, whenInLine(last.at, now()))));
    const writes = mk("p", "team-line writes", t("正在查看同步会写入什么…"));
    const status = mk("div", "notice-status");
    const btn = mkButton(t("同步"), "btn", () => syncNow(btn, status));
    btn.disabled = true;
    const actions = mk("div", "desk-actions");
    actions.append(btn);
    s.main.append(writes, actions, status);
    renderSyncShown(s.main);
    apiGet("/api/share/sync-preview").then((p) => {
      writes.textContent = ROTeam.syncWrites(p);
      btn.disabled = !!p.untrusted;
    }).catch((err) => {
      setStatus(writes, "err", t("没能查看同步会写入什么"), err);
      btn.disabled = false;
    });
    return s.row;
  }

  // After a sync, until the desk is opened afresh: the official version, up to date, and what the sync did.
  function syncDoneRow(sync) {
    const last = sync && sync.last_snapshot;
    const s = rowShell(t("正式版本"), "olive");
    if (last) s.main.appendChild(emptyLine(t("你的工作副本已经是最新的正式版本（第 {seq} 版）。", { seq: numberText(last.seq) })));
    renderSyncShown(s.main);
    return s.row;
  }

  // What the PI's last publish says (13.6: Research OS's own note -- one
  // published on approving -- by its code, with the proposal's sentence
  // when it is the member's own; a note the PI typed, as typed).
  function publishedWords(last, when) {
    const seq = numberText(last.seq);
    if (last.note_code === "approved") {
      const mine = ((lastData && lastData.column && lastData.column.mine) || []).find((p) => p.id === last.proposal);
      const what = mine && (mine.sentence || proposalWhat(mine));
      return what ? t("PI 于 {when} 批准提议「{what}」后发布了第 {seq} 版。", { when: when, seq: seq, what: what })
        : t("PI 于 {when} 批准提议后发布了第 {seq} 版。", { when: when, seq: seq });
    }
    return last.note ? t("PI 于 {when} 发布了第 {seq} 版：{note}", { when: when, seq: seq, note: last.note })
      : t("PI 于 {when} 发布了第 {seq} 版。", { when: when, seq: seq });
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
    await reloadDesk();
    const shown = !offDesk() ? views.desk.querySelector(".desk-sync-done") : null;
    if (shown && typeof shown.scrollIntoView === "function") shown.scrollIntoView({ block: "center" });
  }

  // A row as its kind draws it. `plain`: on 项目首页 there is no map to mark, so a title is words, not a link that marks.
  function needsRow(r, plain) {
    switch (r.kind) {
      case "handback": return handbackRow(r);
      case "answer": case "failed": case "out_of_bounds": return runRow(r);
      case "proposal": return proposalRow(r.proposal, true, null, plain);
      case "mine": return proposalRow(r.proposal, false, null, plain);
      case "official": return officialRow(r.snapshot || {});
      case "review": return reviewRow(r, plain);
      case "line_missing": return missingRow(r);
      default: return null;
    }
  }

  // The notices a role or a shared folder that cannot be read puts above the rows, then the rows, or one quiet line.
  // `place`: "column" is 研究路线's, "home" is 项目首页's (a title there is words, not a link that marks a card). Both keep what a
  // sync just did where the official version's row stood, until the desk is opened afresh: the row goes with the sync.
  function needsBody(needs, place) {
    const box = mk("div", "desk-needs");
    const notices = (needs && needs.notices) || [];
    notices.forEach((n) => box.appendChild(mk("p", "desk-alone", n)));
    const ul = mk("ul", "desk-list");
    let drawn = 0;
    const rows = waitingRows(needs && needs.rows);
    rows.forEach((r) => {
      const li = needsRow(r, place !== "column");
      if (li) { ul.appendChild(li); drawn += 1; }
    });
    if (syncShown && !rows.some((r) => r.kind === "official")) {
      ul.appendChild(syncDoneRow(lastData && lastData.column && lastData.column.sync));
      drawn += 1;
    }
    if (drawn) box.appendChild(ul);
    else if (!notices.length) box.appendChild(emptyLine(t("现在没有要处理的事。")));
    return box;
  }

  // 项目首页 draws the same list in `host` (workspace.js): `get` hands it the engine's answer each time it is drawn, and
  // an act that changes the list here (a returned proposal said 「知道了」 to) draws it again.
  const drawnLists = new Set();

  function drawList(entry) {
    entry.host.textContent = "";
    entry.host.appendChild(needsBody(entry.get(), "home"));
  }

  function needsList(host, get) {
    const entry = Array.from(drawnLists).find((e) => e.host === host) || { host: host, get: get };
    entry.get = get;
    drawnLists.add(entry);
    drawList(entry);
  }

  function redrawLists() {
    drawnLists.forEach((entry) => {
      if (entry.host.isConnected === false) drawnLists.delete(entry);
      else drawList(entry);
    });
  }

  // The column (21.4): the same list, under 等你处理; with nothing waiting it folds to a strip that gives its width to
  // the map or the list, and it opens when a row arrives. A sync just done keeps it open to say what it did.
  function renderColumn(col, data) {
    const needs = (data && data.needs) || { rows: [], notices: [] };
    const top = col.scrollTop;
    col.textContent = "";
    drawnKeys.clear();
    const folded = !waitingRows(needs.rows).length && !(needs.notices || []).length && !syncShown;
    col.classList.toggle("folded", folded);
    col.title = folded ? t("现在没有要处理的事。") : "";
    col.appendChild(mk("h2", "desk-column-title", t("等你处理")));
    if (!folded) col.appendChild(needsBody(needs, "column"));
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
        mapEl = mk("div", "desk-map");
        routeMap = window.RORouteMap.create({ onOpen: openAttempt, onOpenHandback: (report) => openHandback(report, null), onChanged: () => loadDesk(views.desk, true),
          firstOpen: firstMapOpen });
        routeMap.mount(mapEl);
        layoutEl.appendChild(mapEl);
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
      // Who I am first (it keys "last seen"); then the desk, drawn from
      // /api/desk alone -- it waits on the shared folder SHARED_TIMEOUT at
      // most --, the context (the sidebar's count, the shared folder's
      // line) coming in on its own.
      if (!ctx.me || ctx.project !== projectKey()) ctx.me = await apiGet("/api/me").catch(() => null);
      since = deskSince();
      loadContext().then((ok) => { if (ok && epoch === state.dataEpoch) renderSidebar(); }, () => {});
      data = await apiGet("/api/desk");
    } catch (err) {
      if (epoch !== state.dataEpoch) return;
      renderViewFailure(drawn ? columnEl : host, err); // a desk drawn keeps its map
      return;
    }
    if (epoch !== state.dataEpoch) return;
    if (drawnEpoch !== epoch) picked = null;
    setNeeds(data.needs);
    if (ctx.epoch === epoch) renderSidebar();
    lastData = data;
    lastChanged = changedSince(since);
    lastFrom = sinceTime(since);
    renderColumn(deskColumn(host), data);
    if (routeMap) routeMap.update(data, { changed: lastChanged });
    drawnEpoch = epoch;
    host.dataset.loaded = "1";
    showMode();
    loadList(refresh || drawn); // 列表 only: the grouped list beside the same column (18.3)
    catchUp(data, epoch);
  }

  // The desk's reading taken in without drawing the desk: for the attempt panel and the list of 等你处理 when the page on
  // screen is 项目首页 (which asks for an attempt's panel, and answers a proposal in it). False when the project moved on.
  async function readDeskOffScreen() {
    const epoch = state.dataEpoch;
    if (!ctx.me || ctx.project !== projectKey()) ctx.me = await apiGet("/api/me").catch(() => null);
    const since = deskSince();
    const data = await apiGet("/api/desk");
    if (epoch !== state.dataEpoch) return false;
    lastData = data;
    lastChanged = changedSince(since);
    lastFrom = sinceTime(since);
    setNeeds(data.needs);
    if (ctx.epoch === epoch) renderSidebar();
    return true;
  }

  // An act that changes what waits (a run's 「知道了」, a proposal answered, a sync): the page on screen is read again, the
  // desk when it is that, and with it the count beside 项目首页.
  function refreshAfterAct() {
    if (!offDesk()) return loadDesk(views.desk, true);
    return typeof refreshCurrentView === "function" ? refreshCurrentView() : null;
  }

  // ... and, from 项目首页, the desk's own reading first: the attempt's panel draws from it.
  async function reloadDesk() {
    if (offDesk()) {
      try { await readDeskOffScreen(); } catch (err) { /* the panel keeps what it had */ }
    }
    return refreshAfterAct();
  }

  // A shared folder too slow for the desk's wait (14.4: slow, or stale --
  // what it last read): the desk reads itself again a little later, which
  // joins the read still in flight, so the column catches up with the
  // folder rather than saying 「稍后再看」 until something else moves.
  const CATCH_UP_MS = 5000;
  const CATCH_UP_TRIES = 3;
  let catchUpTimer = null;
  let catchUpTries = 0;
  function catchUp(data, epoch) {
    const shared = ((data && data.column) || {}).shared || {};
    if (shared.state !== "slow" && shared.state !== "stale") { catchUpTries = 0; return; }
    if (catchUpTimer || catchUpTries >= CATCH_UP_TRIES || !onDesk()) return;
    catchUpTries += 1;
    catchUpTimer = setTimeout(() => {
      catchUpTimer = null;
      if (epoch === state.dataEpoch && onDesk()) loadDesk(views.desk, true);
    }, CATCH_UP_MS);
  }

  // -- the side panel (DESIGN.md 14.3) ----------------------------------------------------
  // A card opens its attempt in app.html's side panel, on the desk, the map
  // live beside it (no backdrop): the attempt -- its verdict and result
  // saved at once through the map file's write with 撤销 (18.3), dirty cells
  // only; a member's change becomes a verdict-change proposal (12.4); turning
  // a route abandoned asks its reason and is previewed first --, 写备注,
  // 来龙去脉 (the node page's trace), 源自 and 由它而来 with remove, 等你处理
  // (the proposals touching it, each opened in place and answered there),
  // its 待复核 judged in place, what changed since the last visit (14.5),
  // 打开详情页. The column's 查看 opens a proposal there as its own subject
  // (team.js draws it, reading /api/proposals/<id>?from=). One panel at a
  // time: what is half typed in it is never replaced unasked.

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
    // 返回 asked a moment ago and was told yes (app.html's mayReplacePanel keeps the answer): the opener it calls does not ask again (21.8)
    if (typeof mayReplacePanel === "function" && Date.now() < (mayReplacePanel.answeredYesUntil || 0)) return true;
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

  // The panel opened for `subject`; what an earlier write said is not carried over. On the desk the map stays live beside it
  // (14.3: no backdrop); opened from 项目首页 or any other page it covers the page, and has the scrim every panel that covers
  // the page has (21.8).
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
    if (backdrop && !offDesk()) backdrop.classList.add("hidden");
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
    subject.reopen = () => openProposal(p);   // 返回 from a panel opened from this one (21.8)
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
    await ensureContext(); // 「批准以后会怎样」 names the team and what members sent (team.js reads the context)
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
    await reloadDesk();
    if (state.panelSubject !== subject) return;
    await (subject.kind === "desk-proposal" ? drawProposal(subject) : drawPanel(subject));
  }

  // One proposal opened in place under its row in an attempt's panel.
  async function openInPlace(subject, p, inner) {
    const host = mk("div", "dp-proposal");
    inner.textContent = "";
    inner.appendChild(mk("p", "empty-note", t("载入中…")));
    await ensureContext();
    await ROTeam.renderProposalInto(host, p.id, p.by, (said) => answered(subject, subject.id, said), { panel: true, open: leaveTo });
    inner.textContent = "";
    inner.appendChild(host);
  }

  // 等你处理 (14.3: its urgent work; it was 待办): the proposals touching this attempt -- the
  // PI's waiting ones, a member's own still waiting (14.4) --, which are the rows of the list (21.4) that name it,
  // each opened in place with the full 「批准以后会怎样」 and its answers.
  function urgentPart(subject, id) {
    const rows = waitingRows(((lastData && lastData.needs) || {}).rows)
      .filter((r) => (r.kind === "proposal" || r.kind === "mine") && (r.attempts || []).indexOf(id) >= 0);
    if (!rows.length) return null;
    const s = part(t("等你处理"));
    const ul = mk("ul", "desk-list");
    rows.forEach((r) => ul.appendChild(proposalRow(r.proposal, r.kind === "proposal", subject)));
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
    body.appendChild(tasksPart(a, id));
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

  // A handback (21.2) opened from a card of the map or from an attempt's panel; from the panel it keeps the way back to
  // the attempt, which is asked for like any other opener of a panel (a half-written assessment is not thrown away).
  // Reading or confirming the handback on 项目首页 reloads only the home, never the desk's reading the attempt's panel
  // draws from, so the way back goes through `openAttemptAnywhere`, which reads it again when the desk is not on screen.
  function openHandback(report, attemptId) {
    if (!window.ROWorkspace || typeof window.ROWorkspace.openHandback !== "function") return null;
    const back = attemptId ? () => { if (typeof mayReplacePanel !== "function" || mayReplacePanel()) openAttemptAnywhere(attemptId); } : null;
    return window.ROWorkspace.openHandback(report, { back: back });
  }

  // 任务 (21.1): what this attempt holds, in `after` order -- each with its owner, its state in words and, once it has
  // handed something back, the way to open the handback (21.2).
  // 「在这条尝试下加任务」 writes the new task on 项目首页, whose form has this attempt chosen and its last task as the one the
  // new task continues (21.4: the person may change it). The states are the
  // engine's codes (attempt_tasks.state_of); the words are the ones 项目首页 gives a run.
  const TASK_STATES = {
    not_started: [() => t("未开始"), "gray"], running: [() => t("运行中"), "ochre"], needs_answer: [() => t("等你回答"), "ochre"],
    to_read: [() => t("等你看"), "ochre"], done: [() => t("已完成"), "olive"], failed: [() => t("失败"), "clay"],
    out_of_bounds: [() => t("越过了限制"), "clay"],
  };

  // The words and tone of a state code; a code this page does not know reads as 未开始. 项目首页 says its tasks' states
  // with these words too (21.4), so the table is kept once.
  function taskStateWords(code) {
    const known = Object.prototype.hasOwnProperty.call(TASK_STATES, code) ? TASK_STATES[code] : TASK_STATES.not_started;
    return { words: known[0](), tone: known[1] };
  }
  function taskState(code) {
    const known = taskStateWords(code);
    return mk("span", "status-label tone-" + known.tone, known.words);
  }

  function tasksPart(a, id) {
    const s = part(t("任务", { n: 2 }));
    const items = (a && a.work && a.work.items) || [];
    if (!items.length) s.appendChild(emptyLine(t("这条尝试下还没有任务。")));
    else {
      const ul = mk("ul", "desk-list dp-tasks");
      items.forEach((item) => {
        const li = mk("li", "desk-row dp-task");
        li.dataset.task = item.id;
        const main = mk("div", "desk-main");
        const head = mk("div", "desk-head");
        const first = String(item.text || "").split("\n")[0];
        const text = mk("span", "dp-task-text", first);
        if (first !== item.text) text.title = item.text;
        head.append(text, taskState(item.state));
        main.appendChild(head);
        main.appendChild(metaLine([ownerWords(item), item.due]));
        if (item.report) {
          const actions = mk("div", "desk-actions");
          actions.appendChild(mkButton(t("打开交回"), "ws-text-link", () => { openHandback(item.report, id); }));
          main.appendChild(actions);
        }
        li.appendChild(main);
        ul.appendChild(li);
      });
      s.appendChild(ul);
    }
    const role = deskRole();
    if (role === "single" || role === "pi" || role === "member") {
      const actions = mk("div", "desk-actions");
      // the new task continues the attempt's last one (`after` order), as 再派一个任务 does; the form lets the person change it
      const last = items.length ? items[items.length - 1] : null;
      actions.appendChild(mkButton(t("在这条尝试下加任务"), "btn btn-small", () => {
        if (mayLeavePanel() && typeof startTaskOnHome === "function") startTaskOnHome({ attempt: id, after: last ? last.id : "" });
      }));
      s.appendChild(actions);
    }
    return s;
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
        // 21.7: the file's name is the title and opens its page; its path is the quiet line under it
        what.appendChild(rowLink(e.file.path ? e.file.path.split("/").pop() : e.file.node, "", () => leaveTo(e.file.node), t("打开它的详情页")));
        main.appendChild(what);
        if (e.file.path && e.file.path.indexOf("/") >= 0) main.appendChild(mk("div", "tl-meta mono", e.file.path));
        main.appendChild(mk("div", "tl-meta", t("文件有改动")));
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
      setStatus(box, "err", t("没能读到待复核的连线确认"), review.failed);
      s.appendChild(box);
    } else if (review && review.ledger && review.ledger.state && review.ledger.state !== "ok") {
      s.appendChild(mk("p", "desk-alone", t("连线确认的记录文件当前无法读取，请先恢复它")));
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
    actions.appendChild(mkButton(t("打开详情页"), "ws-text-link", () => leaveTo(id)));
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

  // -- 研究判断 as a choice and a reason (DESIGN.md 21.3) ---------------------------------------------------------
  // The research assessment is one cell of the attempt table: the researcher's marker for a status and their own
  // words ("🕒 活跃候选"). The page shows the two apart -- a status chosen from the project's (`statuses`, as the engine
  // lists them: the default words and any the researcher named in [verdict_labels], each with its marker or markers,
  // 其它 last) and a 理由 field with the words -- and the marker itself is never in a field (12.11). Reading splits a
  // cell as the engine's verdict_status does: the first known marker (the earliest; the longer when two start together)
  // is the status and what is left is the reason; a cell with no known marker is 其它 and all of it the reason.
  // Writing is the status's marker, a space and the reason (the reason alone for 其它; the marker the cell already
  // had when its status is not changed). A cell nothing was changed in is written back byte for byte: it is not
  // written at all -- `cell()` hands the original text back. attempts.compose_verdict is the engine's twin of
  // composeAssessment; the tests hold the two to the same answers.
  const OTHER_WORD = defaultVerdict("other")[0]; // the word a status with no marker has (13.4), as the engine writes it
  const SELECTOR_CHARS = /[\ufe0e\ufe0f]/;
  let chooserSeq = 0;

  function markersOf(s) { return s && s.markers && s.markers.length ? s.markers : s && s.marker ? [s.marker] : []; }

  // The statuses the engine listed, 其它 last and always there: a page that was given none still reads a cell as 其它.
  function chooserStatuses(list) {
    const all = Array.isArray(list) ? list.filter((s) => s && typeof s.status === "string") : [];
    const other = all.find((s) => s.other) || { status: OTHER_WORD, marker: "", markers: [], other: true, code: "other", abandons: false };
    return all.filter((s) => !s.other).concat([other]);
  }

  // `cell` split into the status (its word), the reason and the marker the cell wrote the status with.
  function parseAssessment(cell, statuses) {
    const text = String(cell == null ? "" : cell);
    const list = chooserStatuses(statuses);
    const kept = [], where = [];
    for (let i = 0; i < text.length; i++) if (!SELECTOR_CHARS.test(text[i])) { kept.push(text[i]); where.push(i); }
    const bare = kept.join("");
    let best = null;
    list.forEach((s) => {
      if (s.other) return;
      markersOf(s).forEach((marker) => {
        const key = String(marker).replace(/[\ufe0e\ufe0f]/g, "").trim();
        const at = key ? bare.indexOf(key) : -1;
        if (at >= 0 && (best === null || at < best.at || (at === best.at && key.length > best.length))) best = { at: at, length: key.length, status: s, marker: marker };
      });
    });
    if (best === null) return { status: list[list.length - 1].status, reason: text, marker: "" };
    const start = where[best.at];
    let end = where[best.at + best.length - 1] + 1;
    while (end < text.length && SELECTOR_CHARS.test(text[end])) end += 1;
    const reason = (text.slice(0, start).replace(/\s+$/, "") + " " + text.slice(end).replace(/^\s+/, "")).trim();
    return { status: best.status.status, reason: reason, marker: best.marker };
  }

  // The cell for `status` (its word) and `reason`; `own`, the marker the cell was read with, stays when it names the status.
  function composeAssessment(status, reason, statuses, own) {
    const entry = chooserStatuses(statuses).find((s) => s.status === status);
    const words = String(reason == null ? "" : reason).trim();
    const marker = entry && !entry.other ? (own && markersOf(entry).indexOf(own) >= 0 ? own : entry.marker) : "";
    return marker ? (words ? marker + " " + words : marker) : words;
  }

  // A status as the screen says it: a default word in the screen's language (13.4), the researcher's own as written.
  function statusWord(s) { return verdictWord({ status: s.code, label: s.status }); }

  // The chooser (21.3): one button per status with the chosen one pressed, and a 理由 field with the researcher's
  // words. `opts`: `statuses`; the start, either `cell` (the table's text: read, and kept to write back unchanged) or
  // `status` and `reason` (a new one, or one prefilled); `label` (what the group is called); `onChange(chooser)`. A
  // status that is not among `statuses` leaves nothing chosen. Its two fields are an <input type="hidden"> holding the
  // status word and the reason's <textarea>, so that a half-made choice is guarded like any typed words (halfTyped).
  // Returns { el, status(), reason(), cell(), changed(), shown(), set(cell), markWritten(), setDisabled(on),
  // focusReason(), reasonEl, statusEl, picks }.
  function statusChooser(opts) {
    const o = opts || {};
    const list = chooserStatuses(o.statuses);
    const made = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
    let original = null, own = "", status = "", locked = false, initial = { status: "", reason: "" };
    const seq = ++chooserSeq;

    const root = made("div", "status-chooser");
    root.setAttribute("role", "group");
    if (o.label) root.setAttribute("aria-label", o.label);
    const row = made("div", "status-picks");
    const value = made("input", "status-value");
    value.type = "hidden";
    const caption = made("label", "status-caption", t("理由"));
    const reason = made("textarea", "form-textarea status-reason");
    reason.rows = 2;
    reason.id = "status-reason-" + seq;
    caption.htmlFor = reason.id;
    const picks = list.map((s) => {
      const b = made("button", "status-pick", statusWord(s));
      b.type = "button";
      b.dataset.status = s.status;
      b.setAttribute("aria-pressed", "false");
      b.addEventListener("click", () => { if (!locked) choose(s.status, true); });
      row.appendChild(b);
      return b;
    });
    root.append(row, value, caption, reason);

    // the hidden field holds the status as the screen says it (13.4), so that nothing on an English screen is Chinese; the word on disk is `status()`
    const said = () => { const s = list.find((x) => x.status === status); return s ? statusWord(s) : ""; };
    function paint() {
      value.value = said();
      picks.forEach((b) => {
        const on = b.dataset.status === status;
        b.classList.toggle("selected", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }
    function choose(word, byPerson) {
      status = list.some((s) => s.status === word) ? word : "";
      if (byPerson) value.dataset.typed = "1";
      paint();
      if (byPerson && typeof o.onChange === "function") o.onChange(api);
    }
    // The start, or the cell read again (after a write or 撤销).
    function begin(cell, st, why) {
      if (cell !== null && cell !== undefined) {
        original = String(cell);
        const p = parseAssessment(original, list);
        st = p.status; why = p.reason; own = p.marker;
      } else { original = null; own = ""; if (!st) st = list[list.length - 1].status; } // nothing named: 其它, which has no marker
      reason.value = why || "";
      choose(st || "", false);
      initial = { status: status, reason: reason.value };
      value.dataset.initial = said();
      reason.dataset.initial = reason.value;
      delete value.dataset.typed;
      delete reason.dataset.typed;
    }
    const touched = () => status !== initial.status || reason.value !== initial.reason;
    function cell() { return original !== null && !touched() ? original : composeAssessment(status, reason.value, list, own); }
    reason.addEventListener("input", () => { if (typeof o.onChange === "function") o.onChange(api); });

    const api = {
      el: root, reasonEl: reason, statusEl: value, picks: picks,
      status: () => status, reason: () => reason.value, cell: cell,
      // whether writing it would change the cell the table holds (a new one is always to be written)
      changed: () => (original === null ? touched() : cell() !== original),
      shown: () => original,
      set: (text) => begin(text),
      // what is shown is now what the table holds
      markWritten: () => { begin(original === null ? null : cell(), original === null ? status : "", reason.value); },
      setDisabled: (on) => { locked = !!on; picks.forEach((b) => { b.disabled = locked; }); reason.disabled = locked; },
      focusReason: () => reason.focus(),
    };
    begin(o.cell, o.status, o.reason);
    return api;
  }

  // -- an existing row's 研究判断 or 结果 saves at once (18.3) ---------------------------------
  // For the PI and a single researcher (a member's change is still a proposal,
  // a new row keeps its preview). The engine's preview is still asked, but not
  // shown: it says whether this write turns the route abandoned (then the
  // reason and the confirmation stay, 14.3) and what the cells hold now
  // (`cells`, server.attempts_preview_payload). The write is the same one --
  // only what changed, backed up, in the history, checked against the file as
  // it is -- and 撤销 writes the previous cells back through it: a second
  // entry in the history, not an erasure. Shared with app.html's older form.
  const AT_ONCE_KEYS = ["verdict", "result"];

  // The table's two endpoints (the panel's save, its confirmation and 撤销 all use these).
  const previewRow = (b) => apiPost("/api/attempts/preview", b);
  const writeRow = (b) => apiPost("/api/attempts/write", b);

  // Compared with every space and line break taken out: an <input> keeps no line break of a cell with <br>.
  function squash(s) { return String(s == null ? "" : s).replace(/\s+/g, ""); }

  // Whether the cells `b` writes are only of this kind, in an existing row.
  function atOnceKind(b) {
    const keys = Object.keys((b && b.fields) || {});
    return !!b && b.op === "update" && keys.length > 0 && keys.every((k) => AT_ONCE_KEYS.indexOf(k) >= 0);
  }

  // The preview `p` of `b` against the text the form showed (`shown`: key ->
  // text): "once" (write now), "abandons" (it turns the route abandoned: the
  // reason and the confirmation as before) or "moved" (a cell is no longer
  // the text the form showed -- the file changed meanwhile --: the preview is
  // shown, with the row as it is now).
  function atOnceVerdict(p, b, shown) {
    if (p.reason_required) return "abandons";
    const cells = p.cells || {};
    return Object.keys(b.fields).some((k) => cells[k] && squash(cells[k].was_text) !== squash(shown[k])) ? "moved" : "once";
  }

  // What 撤销 will do: write back the cells as the file held them (`was`, from
  // the preview; the form's own text if the engine did not say), once the
  // row still reads as this write left it (`now`). `text` is what a field
  // goes back to: the text the form showed, unless the file had moved on
  // from it before this write -- then the file's own text (`was_text`), which
  // is what `was` puts back.
  function undoOf(p, b, shown) {
    const cells = p.cells || {};
    const u = { number: b.number, was: {}, now: {}, text: {} };
    Object.keys(b.fields).forEach((k) => {
      u.was[k] = cells[k] && typeof cells[k].was === "string" ? cells[k].was : toCellText(String(shown[k] == null ? "" : shown[k]));
      const seen = String(shown[k] == null ? "" : shown[k]);
      const filed = cells[k] && typeof cells[k].was_text === "string" ? cells[k].was_text : seen;
      u.text[k] = squash(filed) === squash(seen) ? seen : filed;
      if (cells[k] && typeof cells[k].now_text === "string") u.now[k] = cells[k].now_text;
    });
    return u;
  }

  // Whether the row (as its page says it, `details`) still reads as the write left it.
  function stillSo(u, details) {
    return Object.keys(u.now).every((k) => squash((details || {})[k]) === squash(u.now[k]));
  }

  // 撤销: the same way as the write -- the preview, then the write. {state}:
  // "abandons" (writing the old cells back would abandon the route again,
  // which needs its reason), "moved" (the row changed again meanwhile) or
  // "done" (with `res`, the write's answer). Throws what the engine refuses.
  async function undoWrite(u) {
    const b = { op: "update", number: u.number, fields: u.was };
    const p = await previewRow(b);
    if (p.reason_required) return { state: "abandons" };
    const cells = p.cells || {};
    if (Object.keys(u.now).some((k) => cells[k] && squash(cells[k].was_text) !== squash(u.now[k]))) return { state: "moved" };
    return { state: "done", res: await writeRow(b) };
  }

  // 已保存 and 撤销 in `el`, a status line (its class set by the caller).
  function savedLine(el, onUndo) {
    el.textContent = "";
    el.append(mk("span", null, t("已保存")), mkButton(t("撤销"), "err-detail-toggle", onUndo));
  }

  // The attempt's verdict and result (14.3, 18.3): the PI's and a single
  // researcher's save at once, with 撤销; turning a route abandoned, and a
  // member's change, are previewed and confirmed. Only the cells changed are
  // sent. Description, date and variables are 研究路线's.
  function editPart(subject, a, page) {
    const d = page.details || {};
    const role = deskRole();
    const s = part(t("研究判断与结果"));
    if (role !== "single" && role !== "pi" && role !== "member") {
      if (d.result) s.appendChild(mk("p", "desk-note", d.result));
      const c = (lastData && lastData.column) || {};
      s.appendChild(mk("p", "desk-alone", role === "none" ? t("你还不是这个项目的成员：请 PI 把你加入团队，然后同步")
        : c.notice || t("现在说不清你在这个项目里的角色，研究判断先不能改。")));
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
    // 21.3: a status and a reason, not the cell's text; the table's own cell is written back untouched until one of them is changed
    const verdict = statusChooser({ statuses: state.attemptStatuses, cell: d.verdict || "", label: t("研究判断") });
    const result = typedField("textarea", d.result || "");
    s.append(formFieldEl(t("研究判断"), verdict.el), formFieldEl(t("结果"), result));
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
    const go = mkButton(proposing ? t("预览这一行") : t("保存"), "btn btn-primary", () => preview());
    const row = mk("div", "form-actions");
    row.appendChild(go);
    s.append(row, status, shown);
    const fields = [result, reason].concat(sentence ? [sentence] : []);
    const freeze = (on) => { fields.forEach((f) => { f.disabled = on; }); verdict.setDisabled(on); go.disabled = on; subject.hold = on; };
    const shownText = () => ({ verdict: verdict.shown(), result: result.dataset.initial });

    // 18.3: 已保存 and 撤销 while the row still reads as the last save left it; a
    // row that changed again (a refresh found it so) is not offered back.
    function offer(u) {
      setStatus(status, "ok", "");
      savedLine(status, () => undoIt(u));
    }
    if (subject.undo && !proposing && !stillSo(subject.undo, d)) subject.undo = null;
    if (subject.undo && !proposing) offer(subject.undo);

    function body() {
      const changed = {};
      if (verdict.changed()) changed.verdict = toCellText(verdict.cell());
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
      setStatus(status, "", proposing ? t("正在生成预览…") : t("正在保存…"));
      let p;
      try {
        p = await previewRow(b);
      } catch (err) {
        setStatus(status, "err", proposing ? t("无法生成预览") : t("写入失败，文件没有被修改"), err);
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
      const how = proposing ? "preview" : atOnceVerdict(p, b, shownText());
      if (how === "once") return saveNow(p, b);
      showPreview(p, b, how === "moved");
    }

    function showPreview(p, b, moved) {
      freeze(true);
      go.classList.remove("btn-primary");   // 21.8: the preview's confirm is the one primary button while it stands
      shown.textContent = "";
      if (p.old_row != null) shown.append(mk("div", "diff-label", t("原行")), mk("div", "diff-row old", p.old_row));
      shown.append(mk("div", "diff-label", t("新行")), mk("div", "diff-row new", p.new_row));
      shown.appendChild(mk("div", "confirm-note", proposing
        ? t("保存到工作副本：{file}。保存前自动备份。", { file: p.file })
        : t("保存到 {file}，保存前自动备份。", { file: p.file })));
      if (newRow) {
        shown.appendChild(mk("div", "confirm-note", own.state === "proposed" && own.proposal
          ? t("这一行还不在正式版本里：它会整行作为新的尝试再提议一次，替换你还在等待的那份提议。")
          : t("这一行还不在正式版本里：它会整行作为一条新的尝试提议。")));
      }
      if (!proposing && p.reason_required) shown.appendChild(mk("div", "confirm-note", t("这一行会放弃这条路线：理由写进结果一栏，它当时的依据冻结下来。")));
      if (moved) shown.appendChild(mk("div", "confirm-note", t("这一行在你打开它以后被改过：上面的原行是文件里现在的样子。")));
      const actions = mk("div", "form-actions");
      const ok = mkButton(proposing ? t("保存后查看提议") : t("保存修改"), "btn btn-primary",
        () => (moved ? saveNow(p, b) : write(b, ok, back)));
      const back = mkButton(t("返回修改"), "btn", () => { shown.textContent = ""; freeze(false); go.classList.add("btn-primary"); setStatus(status, "", ""); });
      actions.append(ok, back);
      shown.appendChild(actions);
    }

    async function write(b, ok, back) {
      ok.disabled = true;
      back.disabled = true;
      setStatus(status, "", t("正在写入…"));
      let res;
      try {
        res = await writeRow(b); // the confirmed body, verbatim
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
        await ensureContext(); // the draft says where it is written: the member's own area, from the context
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
      verdict.markWritten();
      shown.textContent = "";
      freeze(false);
      subject.undo = null; // the frozen basis is not written back by 撤销: an abandonment is not offered
      panelSaid = { id: subject.id, text: t("已写入 {file}。", { file: res.file }) };
      setStatus(status, "ok", panelSaid.text);
      await drawPanel(subject);
    }

    // 18.3: written at once -- the body the preview was made for, verbatim --, then 已保存 and 撤销.
    async function saveNow(p, b) {
      freeze(true);
      shown.textContent = "";
      setStatus(status, "", t("正在写入…"));
      let res;
      try {
        res = await writeRow(b);
      } catch (err) {
        freeze(false);
        setStatus(status, "err", t("写入失败，文件没有被修改"), err);
        return;
      }
      if (typeof pollGeneration === "function") pollGeneration();
      freeze(false);
      if (res.ingest_error) {
        setStatus(status, "err", t("已写入并备份，但重扫失败"), res.ingest_error);
        return;
      }
      const u = undoOf(p, b, shownText());
      reason.value = "";
      reasonField.classList.add("hidden");
      fields.forEach((f) => { f.dataset.initial = f.value; delete f.dataset.typed; });
      verdict.markWritten();
      subject.undo = u;
      panelSaid = null;
      offer(u);
      await drawPanel(subject);
    }

    // 撤销: the previous cells written back the same way; the panel then says so and offers nothing more.
    async function undoIt(u) {
      freeze(true);
      setStatus(status, "", t("正在写入…"));
      let r;
      try {
        r = await undoWrite(u);
      } catch (err) {
        freeze(false);
        setStatus(status, "err", t("写入失败，文件没有被修改"), err);
        return;
      }
      freeze(false);
      if (r.state !== "done") {
        subject.undo = null;
        setStatus(status, "err", r.state === "abandons"
          ? t("撤销会让这条路线重新放弃，需要写明理由：请把研究判断改回已放弃，再保存。")
          : t("这一行之后又改过，不能撤销。"));
        return;
      }
      if (typeof pollGeneration === "function") pollGeneration();
      subject.undo = null;
      if (r.res.ingest_error) {
        setStatus(status, "err", t("已写入并备份，但重扫失败"), r.res.ingest_error);
        return;
      }
      Object.keys(u.was).forEach((k) => {
        if (k === "verdict") { verdict.set(u.text[k]); return; }
        result.value = u.text[k];
        result.dataset.initial = u.text[k];
        delete result.dataset.typed;
      });
      panelSaid = { id: subject.id, text: t("撤销了上一步") };
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
    input.setAttribute("aria-label", t("备注", { n: 1 }));
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
    await reloadDesk(); // the map without it (or marked), then the panel
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

  // An attempt's panel from a row of 等你处理 or a card of 项目首页, wherever the page is: the panel draws from the desk's
  // reading, which is taken first when the desk is not the page on screen. `toReview`: brought to its 待复核.
  async function openAttemptAnywhere(id, toReview) {
    if (offDesk() || !lastData) {
      let ok = false;
      try { ok = await readDeskOffScreen(); } catch (err) { ok = !!lastData; }
      if (!ok) return null;
    }
    return openAttempt(id, toReview);
  }

  // The context's freshness: fetched again once the project (epoch) moved.
  async function ensureContext() {
    if (ctx.epoch !== state.dataEpoch || ctx.project !== projectKey() || ctx.mode === null) {
      try { await loadContext(); } catch (err) { /* the callers say what they cannot read */ }
    }
    return ctx;
  }

  function context() { return ctx; }

  window.RODesk = Object.freeze({
    loadDesk, refreshSidebar, renderSidebar, verdictInline, statusLabel, ensureContext, context, mapZoom,
    setMode, resume, syncSwitch, listZoom, mode: routesMode, onePerson,
    kindWord, stateWord, verdictWord, proposalWhat, proposalStateText, nameOf, whenWords, dayWords, relationWord,
    // a task's state code in words and tone (21.1), the attempt's panel and 项目首页's 全部任务 say it alike
    taskStateWords,
    // a long number to four significant digits, the value as written left to the caller's title and record (21.8)
    formatNumber, showNumber,
    openAttempt, openProposal, revalidatePanel, mayLeavePanel, leaveDesk,
    // 等你处理 (21.4): the list the engine works out, taken in for the sidebar's count, drawn on 项目首页 and in the column
    noteNeeds, needsCount, needsList, openAttemptAnywhere,
    // an existing row's 研究判断 or 结果 saved at once with 撤销 (18.3): the panel's, and app.html's older form
    atOnce: Object.freeze({ kind: atOnceKind, verdict: atOnceVerdict, undoOf: undoOf, undo: undoWrite, line: savedLine }),
    statusChooser: statusChooser, assessment: Object.freeze({ parse: parseAssessment, compose: composeAssessment }),
    // pure, for tests/test_v8_u2.py
    _whenWords: whenWords, _shareLineText: shareLineText, _proposalWhat: proposalWhat, _firstMapOpen: firstMapOpen,
    _proposalStateText: proposalStateText, _seenKey: seenKey, _modeKey: modeKey, _toneOf: toneOf,
  });
})();
