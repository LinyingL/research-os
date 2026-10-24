// 研究路线, 提议 and 成员 -- DESIGN.md 12.1-12.5, 12.9, 12.11; task V8
// phase U4. Served verbatim at /team.js by the same process and loaded by
// app.html with a plain same-origin <script src>, exactly as /desk.js and
// /node.js are: zero build, zero external resources, same origin check.
//
// It defines one global, window.ROTeam, and calls app.html's helpers (mk,
// mkButton, apiGet, apiPost, setStatus, renderBlockingError,
// renderViewFailure, clearProjectState, ownMark, openPanel, closePanel,
// panelTitle, panelSubtitle, panelBody, activateView, reloadAllViews,
// shellCan, chooseFolder, chooseKeyFile, pollGeneration, state, views,
// currentView), RODesk (the context: who I am, my role, the shared folder,
// the proposals) and RONode (openNode, _diffRows, diffBlock) only at run
// time, never while loading.
//
// What it reads (all GET, all origin-checked by the server):
//   /api/routes             -- 研究路线: the attempts grouped by status;
//   /api/evidence?node=     -- an abandoned route's frozen basis;
//   /api/history/version?node=&sha256= (or &version=) -- one kept version's content;
//   /api/proposals, /api/proposals/<id>[?from=] -- the inbox / 我的提议, one proposal;
//   /api/share, /api/share/sync-preview -- the shared folder, what 同步 would do;
//   /api/me, /api/me/key    -- who I am, my public key.
// What it writes, each only after saying what it will write:
//   POST /api/proposals            -- the dry run, then (write) the proposal;
//   POST /api/proposals/<id>/approve | return | discuss -- the PI's answers;
//   POST /api/me, /api/me/key      -- who I am (first use only), my key;
//   POST /api/team/init, /api/team/add -- the team file, a member;
//   POST /api/share/set, /api/share/publish, /api/share/sync;
//   POST /api/share/join-preview (writes nothing), /api/share/join.
// Paths the researcher chooses (the shell's folder or file panel, or a
// path field where there is none) are the only paths sent; nothing shown
// on the page is ever read back into a path.
//
// 12.11: no emoji, no arrow characters; relations in words; verdicts as
// text status labels followed by the researcher's own words; the
// researcher's own text (titles, verdict words, file names) as written.
(function () {
  "use strict";

  // The words for the screen (DESIGN.md 13.6): /i18n.js's table, looked up
  // at call time; without it (this file evaluated alone) the Chinese. So far
  // the sentences a member's desk shows about 同步 (13.7: the desk entirely)
  // -- packetWords, syncWrites, differenceBlock, syncDone, syncFlash -- which
  // 成员 shares (one helper, every page); and (14.6) a proposal as the desk's
  // panel decides it (renderProposalInto, which 提议 draws too) and a
  // member's draft (composeInto); and (18.2) the lines that joined translated and bare
  // fragments -- an abandoned route's date and what it froze, a proposed change of a research
  // assessment -- and 提交提议 (kindChoices, renderComposer); and (18.3) 研究路线's list half, which
  // the page shows beside the route map, and 「当时的依据」 (loadRoutes to evidenceFile).
  function fillVars(s, v) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return v && Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }); }
  function t(s, v) { var I = typeof window !== "undefined" && window.ROI18N; return I ? I.t(s, v) : fillVars(s, v); }

  function english() { const I = typeof window !== "undefined" && window.ROI18N; return !!I && I.lang === "en"; }

  // A number as 13.5 writes it: 1,497 in English; in Chinese as before.
  function numberText(n) {
    const s = String(n);
    return english() && /^\d{4,}$/.test(s) ? s.replace(/\B(?=(\d{3})+$)/g, ",") : s;
  }

  // Items in a sentence: 「、」 in Chinese; in English commas, "and" before the last.
  function andSeries(xs) {
    return english() && xs.length > 1 ? xs.slice(0, -1).join(t("、")) + " and " + xs[xs.length - 1] : xs.join(t("、"));
  }

  // -- pure wording (loaded into node by tests/test_v8_u4.py) ----------------------
  // A proposal's kind is the desk's word for it (RODesk.kindWord); 14.6: the
  // words a proposal is decided and drafted with go through t().

  // The kinds a member can propose, as [code, name, what it carries]; looked up at call time (13.6).
  function kindChoices() {
    return [
      ["attempt_new", t("新的尝试"), t("尝试表里新加的一行，连同与它关联的文件")],
      ["verdict_change", t("修改研究判断"), t("只有尝试表里这一行的研究判断和结果")],
      ["variable_version", t("变量定义"), t("一张变量定义卡的新版本")],
      ["files", t("只有文件"), t("你选的脚本、数据和文稿")],
    ];
  }
  // What a proposal does to a file.
  function fileAction(action) {
    switch (action) {
      case "added": return t("新增");
      case "changed": return t("改动");
      case "removed": return t("删除");
      default: return "";
    }
  }

  // Where a file stands against the official version (12.3).
  function fileState(state) {
    switch (state) {
      case "apply": return t("批准后写入");
      case "same": return t("正式版本里已经是这样");
      case "conflict": return t("两边都改过");
      case "problem": return t("有问题");
      case "done": return t("这次批准已经写入");
      default: return String(state || "");
    }
  }

  // The member's answer: were the existing outputs made by this definition?
  function attestedWord(answer) {
    switch (answer) {
      case "yes": return t("是");
      case "no": return t("否");
      case "unknown": return t("不确定");
      default: return String(answer || "");
    }
  }

  // Why a signature could not be checked, when the engine names no sentence.
  function verifyWord(state) {
    switch (state) {
      case "verified": return t("签名已验证");
      case "no_key": return t("没有公钥可以核对");
      case "no_tool": return t("这台电脑无法核对签名");
      default: return "";
    }
  }

  const ROLE_WORDS = { pi: "PI", member: "成员" };

  // A size as a person reads it.
  function sizeWords(n) {
    const v = Number(n);
    if (n == null || !isFinite(v) || v < 0) return "";
    if (v < 1024) return t("{n} 字节", { n: v });
    if (v < 1024 * 1024) return (v / 1024).toFixed(v < 10 * 1024 ? 1 : 0) + " KB";
    if (v < 1024 * 1024 * 1024) return (v / 1024 / 1024).toFixed(v < 10 * 1024 * 1024 ? 1 : 0) + " MB";
    return (v / 1024 / 1024 / 1024).toFixed(1) + " GB";
  }

  // A proposal's state as a label: [words, tone].
  function stateLabel(p) {
    const state = p && p.state;
    switch (state) {
      case "pending": return { label: t("提议中"), tone: "ochre" };
      case "conflict": return { label: t("批准与退回的记录互相矛盾"), tone: "clay" };
      case "approved": return { label: t("已批准"), tone: "olive" };
      case "returned": return { label: t("已退回"), tone: "gray" };
      case "superseded": return { label: t("已被后来的提议替代"), tone: "plain" };
      default: return { label: String(state || ""), tone: "plain" };
    }
  }

  // The signature, as 12.5 asks the PI to read it: 已验证, or 未验证 with
  // the reason -- the engine's sentence (signing.CHECK_MESSAGES, a fixed
  // table) in the screen's language by its Chinese.
  function signatureWords(v) {
    if (!v || typeof v !== "object") return { label: t("未验证"), tone: "ochre", reason: t("这份提议没有可以核对的签名信息") };
    if (v.state === "verified") return { label: t("已验证"), tone: "olive", reason: t("签名和成员名单里这个人的公钥相符") };
    return { label: t("未验证"), tone: v.trusted === false ? "clay" : "ochre", reason: v.message ? t(v.message) : verifyWord(v.state) };
  }

  // The last answer of one kind on a proposal.
  function lastOutcome(p, act) {
    const outs = ((p && p.outcomes) || []).filter((o) => o.act === act);
    return outs.length ? outs[outs.length - 1] : null;
  }

  // What approving did, in one sentence (12.3: approving publishes).
  function approveSentence(res) {
    const pub = (res && res.published) || {};
    if (pub.published === false) {
      return t("已批准，改动已写入正式版本；但这次没有发布到公盘：{why}。可以在「成员」一栏里再发布一次。",
        { why: pub.message ? t(pub.message) : t("发布没有完成") });
    }
    if (pub.seq != null) return t("已批准，改动已写入正式版本，并发布为第 {seq} 版：成员同步后就会看到。", { seq: numberText(pub.seq) });
    return t("已批准，改动已写入正式版本。");
  }

  // What a proposal of this kind carries unless the member adds or unticks (12.13).
  function carryWords(kind) {
    switch (kind) {
      case "verdict_change": return t("修改研究判断只带尝试表里的这一行；要一起发送文件，在下面勾选。");
      case "attempt_new":
        return t("新的尝试带上它这一行，和与它关联的文件（描述里写的步骤脚本、用「关联文件」挂上的文件）；可以加上或去掉。");
      case "variable_version": return t("变量定义只带新版本的定义文件；要一起发送文件，在下面勾选。");
      default: return t("只带勾选的文件。");
    }
  }

  // What sending a proposal writes, before it is sent.
  function sendWrites(draft, me) {
    const files = ((draft && draft.file) || []).filter((f) => f.action !== "removed");
    const blobs = files.filter((f) => f.store === "blob");
    const c = typeof RODesk !== "undefined" ? RODesk.context() : null;
    // 12.13: or a folder of its own; 13.1: the member's area, 提议/<id>/, a fixed name shown as it is.
    const where = c && c.share && c.share.mine && c.share.mine.path ? t("你自己的提议文件夹（{path}）", { path: c.share.mine.path })
      : t("公盘的「{area}」", { area: me ? "提议/" + me + "/" : "提议/你的邮箱/" });
    const carried = files.length ? t("，带上 {n} 个文件", { n: numberText(files.length) }) +
      (blobs.length ? t("（其中 {n} 个大文件按内容只存一份）", { n: numberText(blobs.length) }) : "") : "";
    return t("发送会在{where}里写入一份新的提议{files}；写成以后不再改动。你的工作副本不会改变。", { where: where, files: carried });
  }

  // What the members' packets carry, in words: 「1 条备注、增加 2 条连线、删除 1 条连线」.
  function packetWords(c) {
    if (!c) return "";
    const parts = [];
    if (c.notes) parts.push(t("{n} 条备注", { n: numberText(c.notes) }));
    if (c.judgements) parts.push(t("{n} 条连线确认", { n: numberText(c.judgements) }));
    if (c.papers) parts.push(t("{n} 处「设为论文正文」的标记", { n: numberText(c.papers) }));
    if (c.links_added) parts.push(t("增加 {n} 条连线", { n: numberText(c.links_added) }));
    if (c.links_removed) parts.push(t("删除 {n} 条连线", { n: numberText(c.links_removed) }));
    return andSeries(parts);
  }

  // What 发布 writes, before it is pressed (12.12: the members' packets
  // are appended to the official ledgers in the project first).
  function publishWrites(share) {
    const last = share && share.last_snapshot;
    const next = last ? Number(last.seq) + 1 : 1;
    const pending = (share && share.pending_publish) || {};
    const coming = (share && share.incoming) || {};
    const carried = packetWords(coming);
    let s = "";
    if (carried) {
      s += "发布前，先把成员同步来的 " + carried + "（" + ((coming.members || []).join("、") || "成员") +
        "）加进项目里的正式记录" + ((coming.files || []).length ? "（" + coming.files.join("、") + "）" : "") +
        "，署成员的名字，不需要批准" + (coming.links_removed ? "；删除的连线会从正式版本里去掉" : "") + "。然后";
    } else {
      s += "发布";
    }
    if (last && !pending.count && !carried) {
      return "和第 " + last.seq + " 版相比没有文件不同：发布不会写入新的版本。项目文件夹里不写入任何东西。";
    }
    s += (carried ? "在" : "会在") + "公盘的「已发布/snapshots/」里写入第 " + next + " 版快照，并用你的密钥签名";
    if (last && pending.count) s += "：和第 " + last.seq + " 版相比有 " + pending.count + " 个文件不同";
    else if (last) s += "：带上这些记录的改动";
    else s += "：这是第一版，正式版本的每个文件都会写进去（大文件按内容只存一份）";
    return s + "。" + (carried ? "" : "项目文件夹里不写入任何东西。");
  }

  // What 同步 writes, from the engine's look at it (sync-preview): first
  // what it sends (12.12: notes, judgments and links, without approval),
  // then what it brings.
  function syncWrites(p) {
    if (!p) return "";
    if (p.untrusted) return t("你的工作副本所依据的版本在公盘上已经无法验证，不能同步。");
    const sending = packetWords(p.sending);
    // 13.1: the member's area is a fixed name on the shared drive (提议/<id>/), shown as it is.
    const send = sending ? t("同步会先把你还没发送的 {sending}写进公盘的「{area}」：不需要批准，PI 下次发布时加入正式记录。",
      { sending: sending, area: p.area || "提议/你的邮箱/" }) : "";
    if (p.up_to_date) {
      return send ? t("{send}你的工作副本已经是最新的正式版本。", { send: send })
        : t("你的工作副本已经是最新的正式版本，没有要发送的备注、连线确认或手工连线。");
    }
    const parts = [];
    if (p.update && p.update.length) parts.push(t("更新 {n} 个你没改过的文件", { n: numberText(p.update.length) }));
    if (p.remove && p.remove.length) parts.push(t("删除 {n} 个正式版本已删除的文件", { n: numberText(p.remove.length) }));
    if (p.beside && p.beside.length) {
      parts.push(t("{n} 个两边都改过的文件留着你的，把正式的一份放在旁边（.official）", { n: numberText(p.beside.length) }));
    }
    if (p.records && p.records.length) parts.push(t("备注、连线确认和手工连线的记录文件：你的条目都已进入正式版本的，换成正式的那一份；还没进入的，留着你的"));
    const listed = parts.length ? t("：{parts}", { parts: parts.join(t("；")) }) : "";
    return send ? t("{send}然后把工作副本带到第 {seq} 版{parts}。从不合并，你改过的内容不会被覆盖。",
      { send: send, seq: numberText(p.to), parts: listed })
      : t("同步会把工作副本带到第 {seq} 版{parts}。从不合并，你改过的内容不会被覆盖。", { seq: numberText(p.to), parts: listed });
  }

  // What 加入 writes, from the engine's look at it (join-preview).
  function joinWrites(p) {
    if (!p) return "";
    return "加入会新建文件夹 " + p.folder + "，从第 " + p.seq + " 版写入 " + p.files + " 个文件（共 " + sizeWords(p.size) +
      "），并在这台电脑上记下 PI 的公钥：以后只接受用这把钥匙签名的版本。";
  }

  // A route abandoned by a direct write (14.3): a decision with no proposal.
  function directAbandonment(d) {
    return !!d && d.act === "abandoned" && !d.proposal;
  }

  // A route row's reason line: where the reason came from -- 「由 X 放弃」 for
  // a direct abandonment, else who proposed and who approved it.
  function reasonSource(item) {
    if (!item || !item.reason) return "";
    if (item.reason_from === "decision" && item.decision) {
      const by = item.decision.by && item.decision.by.label;
      const ok = item.decision.approved_by && item.decision.approved_by.label;
      if (directAbandonment(item.decision)) return by ? t("由 {who} 放弃", { who: by }) : "";
      if (by && ok) return t("提议人 {proposer}，批准人 {approver}", { proposer: by, approver: ok });
      return by ? t("提议人 {who}", { who: by }) : ok ? t("批准人 {who}", { who: ok }) : "";
    }
    return item.reason_from === "table" ? t("写在尝试表里") : "";
  }

  // Which proposals belong on 研究路线: routes proposed or re-judged.
  function routeProposals(list, mode) {
    return (list || []).filter((p) => (p.kind === "attempt_new" || p.kind === "verdict_change") &&
      (mode === "pi" ? (p.state === "pending" || p.state === "conflict") : (p.state === "pending" || p.state === "returned")));
  }

  // -- runtime ---------------------------------------------------------------------------

  const view = { filter: null, proposal: null, member: null, compose: null, joinSubject: null };
  // What an act of the researcher's just did, said at the top of its view
  // until they open the view afresh: the page is rebuilt by the refresh
  // the act itself sets off, and the sentence must not go with it.
  const flash = { routes: [], proposals: [], members: [] };

  function showFlash(page, key, before) {
    const items = flash[key] || [];
    if (!items.length) return;
    const box = mk("div", "team-flash");
    items.forEach((f) => {
      box.appendChild(mk("p", "team-done" + (f.strong ? " strong" : ""), f.text));
      if (f.diff || f.summary) box.appendChild(differenceBlock(f, true));
    });
    page.insertBefore(box, before || page.firstChild);
  }

  // After an act: the view rebuilt at once (even over a field still
  // holding text), its sentence kept.
  // The sentence is brought into view: the researcher acted far down the
  // page (添加成员, 讨论) and must see what the act did -- and, after 加入团队,
  // what they must do in OneDrive.
  async function after(key, items) {
    flash[key] = items || [];
    const host = key === "members" ? views.members : key === "proposals" ? views.proposals : views.desk;
    if (key === "members") await loadMembers(host, true, true);
    else if (key === "proposals") await loadProposals(host, true, true);
    else await RODesk.loadDesk(host, true); // 研究路线: the page, with its list in list mode (18.3)
    if (flash[key].length) revealFlash(host);
  }

  function revealFlash(host) {
    const box = host && host.querySelector(".team-flash");
    if (!box) return;
    box.setAttribute("tabindex", "-1");
    box.setAttribute("role", "status");
    if (typeof box.scrollIntoView === "function") box.scrollIntoView({ block: "center" });
    try { box.focus({ preventScroll: true }); } catch (err) { /* focus is a convenience */ }
  }

  // 12.3: the difference between the member's file and the official one
  // put beside it -- yours first, then the official one.
  function differenceBlock(f, open) {
    const d = mk("details", "proposal-diff");
    d.appendChild(mk("summary", null, t("你的和正式的有什么不同")));
    // 12.13: a table's rows and columns first (the engine's sentence, in the
    // page's language), its line diff below when small.
    if (f.summary) d.appendChild(mk("p", "desk-note plain", f.summary));
    if (f.diff) d.appendChild(RONode.diffBlock(RONode._diffRows(f.diff), f.truncated));
    else if (f.table) d.appendChild(mk("p", "desk-note plain", t("表格较大：只比较行和列，不逐行对比。")));
    else if (!f.summary) d.appendChild(mk("p", "desk-note plain", t("两个文件不同（不是文本，没有逐行比较）")));
    if (open) d.open = true;
    return d;
  }

  function now() { return new Date(); }
  function when(at) { return at ? RODesk.whenWords(at, now()) || String(at) : ""; }
  // A date inside a line starts lower case in English (13.5), as desk.js writes it.
  function whenInLine(at) { const w = when(at); return w.charAt(0).toLowerCase() + w.slice(1); }
  function mode() { const c = RODesk.context(); return c && c.mode; }

  // A refusal: the engine's own Chinese sentence when it names one, the
  // framing otherwise; the raw text behind 「详情」 (8.8). On an English
  // screen the engine's sentence only where it has English (14.6; errors
  // are 13.7's own increment), else the framing its caller worded.
  function failed(el, framing, err) {
    const own = err && typeof err.engineMessage === "string" && err.engineMessage ? err.engineMessage : "";
    setStatus(el, "err", own && (!english() || t(own) !== own) ? own : framing, err);
  }

  // A sentence holding elements -- a file name, a verdict's label (12.11):
  // its template already through t() with its words filled, each {field}
  // named in `nodes` put where it stands (13.6: one template per sentence).
  function phrase(host, text, nodes) {
    String(text).split(/\{(\w+)\}/).forEach((piece, i) => {
      if (i % 2) host.append(Object.prototype.hasOwnProperty.call(nodes, piece) ? nodes[piece] : "{" + piece + "}");
      else if (piece) host.append(piece);
    });
    return host;
  }

  function label(text, tone) {
    return mk("span", "status-label tone-" + (tone || "plain"), text);
  }

  function block(title, cls) {
    const sec = mk("section", "team-block" + (cls ? " " + cls : ""));
    sec.appendChild(mk("h2", "team-title", title));
    return sec;
  }

  function line(text, cls) {
    return mk("p", "team-line" + (cls ? " " + cls : ""), text);
  }

  function field(labelText, input, help) {
    const wrap = mk("div", "form-field");
    const l = mk("label", null, labelText);
    const id = "f-" + Math.random().toString(36).slice(2, 9);
    input.id = id;
    l.setAttribute("for", id);
    wrap.append(l, input);
    if (help) wrap.appendChild(mk("div", "team-help", help));
    return wrap;
  }

  function textInput(placeholder, mono) {
    const i = mk("input", "form-input" + (mono ? " form-mono" : ""));
    i.type = "text";
    if (placeholder) i.placeholder = placeholder;
    return i;
  }

  function textArea(rows, placeholder) {
    const t = mk("textarea", "form-textarea");
    t.rows = rows || 2;
    if (placeholder) t.placeholder = placeholder;
    return t;
  }

  // A path the researcher chooses: the shell's panel when there is one,
  // and always a path field.
  function pathField(labelText, purpose, placeholder) {
    const wrap = mk("div", "form-field");
    const l = mk("label", null, labelText);
    const row = mk("div", "team-path-row");
    const input = textInput(placeholder, true);
    input.id = "p-" + Math.random().toString(36).slice(2, 9);
    l.setAttribute("for", input.id);
    row.appendChild(input);
    const can = purpose === "key" ? shellCan("choose-file-key") : shellCan("choose-folder-share");
    if (can) {
      row.appendChild(mkButton("选择…", "btn btn-small", async () => {
        const chosen = purpose === "key" ? await chooseKeyFile() : await chooseFolder(purpose);
        if (chosen) { input.value = chosen; input.dispatchEvent(new Event("change")); }
      }));
    }
    wrap.append(l, row);
    return { wrap: wrap, input: input };
  }

  // A refresh (the generation moved) must not take a half-typed form away.
  function keepsTyping(host) {
    const active = document.activeElement;
    if (active && host.contains(active) && (active.tagName === "TEXTAREA" || active.tagName === "INPUT")) return true;
    return Array.from(host.querySelectorAll("textarea, input[type=text]")).some((el) => el.value.trim() !== "");
  }

  function copyButton(text) {
    const b = mkButton("复制", "btn btn-small", async () => {
      try {
        await navigator.clipboard.writeText(text);
        b.textContent = "已复制";
      } catch (err) {
        b.textContent = "请手动选中复制";
      }
    });
    return b;
  }

  // -- 研究路线 (12.9) ------------------------------------------------------------------

  // 研究路线 (18.3) opened on the list with only the abandoned routes shown.
  function showAbandoned() {
    view.filter = "abandoned";
    RODesk.setMode("list"); // on the page already: the list drawn again, with the filter
    if (currentView === "desk") return;
    delete views.desk.dataset.loaded; // else opened afresh, the list read again
    activateView("desk");
  }

  async function loadRoutes(host, refresh, acted) {
    const epoch = state.dataEpoch;
    if (refresh && !acted && keepsTyping(host)) return;
    if (!refresh) flash.routes = [];
    if (!refresh) host.replaceChildren(mk("p", "empty-note", t("载入中…")));
    let data, ctx;
    try {
      [data, ctx] = await Promise.all([apiGet("/api/routes"), RODesk.ensureContext()]);
    } catch (err) {
      if (epoch !== state.dataEpoch) return;
      renderViewFailure(host, err);
      return;
    }
    if (epoch !== state.dataEpoch) return;
    clearProjectState();
    const top = host.scrollTop; // the list is a pane of its own beside 待办 (18.3)
    host.innerHTML = "";
    const page = mk("div", "routes");
    const list = (ctx && ctx.proposals && ctx.proposals.proposals) || [];
    if (ctx && ctx.mode === "none") page.appendChild(mk("p", "desk-alone", t("你还不是这个项目的成员：请 PI 把你加入团队，然后同步")));
    if (ctx && (ctx.mode === "pi" || ctx.mode === "member")) {
      const shown = routeProposals(list, ctx.mode);
      if (shown.length) page.appendChild(routeProposalBlock(shown, ctx));
    }
    if (!data.groups || !data.groups.length) {
      page.appendChild(line(t("还没有研究尝试。点击“新增尝试”开始记录。"), "quiet"));
      host.appendChild(page);
      host.dataset.loaded = "1";
      return;
    }
    if (data.decisions_problem) {
      const warn = mk("p", "team-line warn");
      renderBlockingError(warn, t("批准记录（decisions.toml）现在无法读取：放弃理由只显示尝试表里写着的"), data.decisions_problem);
      page.appendChild(warn);
    }
    if (view.filter === "abandoned") {
      const bar = mk("div", "tree-filter");
      bar.appendChild(mk("span", null, t("只显示已放弃的路线，每条附当时的结果和理由。")));
      bar.appendChild(mkButton(t("显示全部路线"), "btn btn-small", () => { view.filter = null; loadRoutes(host); }));
      page.appendChild(bar);
    }
    data.groups.forEach((g) => {
      if (view.filter === "abandoned" && g.status !== "abandoned") return;
      const sec = mk("section", "route-group");
      // 12.11: the heading is a word set in weight; the tinted label is
      // the verdict's, on each row, followed by the researcher's words. The heading is the
      // desk's verdict word of the group's status code (13.4, 13.6): a default status in the
      // screen's language, a word the researcher named as written -- as the chips on its rows.
      sec.appendChild(mk("h2", "route-group-title", RODesk.verdictWord(g)));
      const ul = mk("ul", "route-list");
      g.items.forEach((item) => ul.appendChild(routeRow(item)));
      sec.appendChild(ul);
      page.appendChild(sec);
    });
    host.appendChild(page);
    host.dataset.loaded = "1";
    if (refresh) host.scrollTop = top;
  }

  function routeRow(item) {
    const li = mk("li", "route-row");
    li.dataset.node = item.id;
    const head = mk("div", "route-head");
    head.appendChild(mk("span", "route-num mono", "#" + String(item.number || "").replace(/^#/, "")));
    head.appendChild(mk("span", "route-date", item.date || ""));
    head.appendChild(mkButton(item.title || t("（无描述）"), "route-title", () => RODesk.openAttempt(item.id)));
    head.appendChild(RODesk.verdictInline(item.verdict_status));
    const own = ownMark(item);
    if (own) head.appendChild(own);
    li.appendChild(head);
    const abandoned = item.verdict_status && item.verdict_status.status === "abandoned";
    if (abandoned) {
      const body = mk("div", "route-basis");
      body.appendChild(mk("p", "route-fact", t("结果：{result}", { result: item.result || t("（没有写）") })));
      if (item.reason) {
        const r = mk("p", "route-fact", t("理由：{reason}", { reason: item.reason }));
        const src = reasonSource(item);
        if (src) r.appendChild(mk("span", "route-src", src));
        body.appendChild(r);
      }
      if (item.decision && item.decision.act === "abandoned") {
        const act = mk("div", "route-actions");
        act.appendChild(mkButton(t("当时的依据"), "btn btn-small", () => openEvidence(item.id, item)));
        const d = item.decision;
        act.appendChild(mk("span", "route-src", d.evidence_count
          ? t("放弃于 {when}，记录了 {n} 个文件的状态", { when: whenInLine(d.at), n: d.evidence_count })
          : t("放弃于 {when}，当时这条路线没有关联的文件", { when: whenInLine(d.at) })));
        body.appendChild(act);
      }
      li.appendChild(body);
    }
    return li;
  }

  function routeProposalBlock(shown, ctx) {
    const pi = ctx.mode === "pi";
    const sec = mk("section", "route-group route-proposals");
    sec.appendChild(mk("h2", "route-group-title serif", pi ? t("提议中") : t("我的提议")));
    sec.appendChild(line(pi ? t("这些路线还不是正式记录：批准以后才会进入尝试表和每一条来龙去脉。")
      : t("你提出的路线和修改研究判断：PI 批准以后才进入正式版本。"), "quiet"));
    const team = (ctx.share && ctx.share.team) || [];
    const ul = mk("ul", "route-list");
    shown.forEach((p) => {
      const li = mk("li", "route-row");
      const head = mk("div", "route-head");
      const a = p.attempt || null;
      const v = p.verdict || null;
      if (a) {
        head.appendChild(mk("span", "route-num mono", "#" + String(a.number || "").replace(/^#/, "")));
        head.appendChild(mk("span", "route-date", a.date || ""));
        head.appendChild(mkButton(a.description || p.sentence || t("（无描述）"), "route-title", () => openProposal(p.id, p.by)));
        if (a.verdict_status) head.appendChild(RODesk.verdictInline(a.verdict_status));
      } else if (v) {
        head.appendChild(mk("span", "route-num mono", "#" + String(v.number || "").replace(/^#/, "")));
        head.appendChild(mkButton(p.sentence || t("修改研究判断"), "route-title", () => openProposal(p.id, p.by)));
        head.appendChild(phrase(mk("span", "desk-what"), t("研究判断 {old} 改为 {new}"),
          { old: RODesk.verdictInline(v.old_status), new: RODesk.verdictInline(v.new_status) }));
      }
      const st = stateLabel(p);
      head.appendChild(label(st.label, st.tone));
      li.appendChild(head);
      const meta = [pi ? RODesk.nameOf(p.by, team) : "", when(p.at)];
      li.appendChild(mk("div", "desk-meta", meta.filter(Boolean).join(" · ")));
      if (p.state === "returned") {
        const o = lastOutcome(p, "returned");
        if (o && o.text) li.appendChild(mk("p", "desk-note", t("退回的理由：{reason}", { reason: o.text })));
      }
      ul.appendChild(li);
    });
    sec.appendChild(ul);
    return sec;
  }

  // 「当时的依据」 (12.4): the files an approved abandonment froze, in the
  // side panel; each opens its node page or, when its content was kept,
  // shows it as it was.
  async function openEvidence(id, item) {
    const subject = { kind: "evidence", id: id };
    state.panelSubject = subject;
    panelTitle.textContent = t("当时的依据");
    panelTitle.title = "";
    panelSubtitle.textContent = item ? "#" + String(item.number || "").replace(/^#/, "") + " " + (item.title || "") : "";
    panelBody.innerHTML = "";
    panelBody.appendChild(mk("p", "empty-note", t("载入中…")));
    openPanel();
    let data;
    try {
      data = await apiGet("/api/evidence?node=" + encodeURIComponent(id));
    } catch (err) {
      if (state.panelSubject !== subject) return;
      panelBody.innerHTML = "";
      const box = mk("div", "panel-status err");
      failed(box, t("没能读到这条路线当时的依据"), err);
      panelBody.appendChild(box);
      return;
    }
    if (state.panelSubject !== subject) return;
    panelTitle.title = data.name || "";
    panelBody.innerHTML = "";
    const decisions = (data.decisions || []).filter((d) => (d.files || []).length || d.act === "abandoned");
    if (!decisions.length) {
      panelBody.appendChild(line(t("这条路线没有经批准记下的依据：它是在尝试表里直接改的。"), "quiet"));
      return;
    }
    decisions.forEach((d) => {
      const sec = mk("section", "evidence");
      const head = mk("div", "desk-head");
      head.appendChild(mk("span", "desk-what strong", d.act === "abandoned" ? t("放弃这条路线") : t("批准")));
      if (d.verdict_status) head.appendChild(RODesk.verdictInline(d.verdict_status));
      sec.appendChild(head);
      const direct = directAbandonment(d);
      const by = d.by ? (direct ? t("由 {who} 放弃", { who: d.by.label }) : t("提议人 {who}", { who: d.by.label })) : "";
      const who = [when(d.at), by, d.approved_by ? t("批准人 {who}", { who: d.approved_by.label }) : ""];
      sec.appendChild(mk("div", "desk-meta", who.filter(Boolean).join(" · ")));
      if (d.result) sec.appendChild(mk("p", "desk-note", t("结果：{result}", { result: d.result })));
      if (d.reason) sec.appendChild(mk("p", "desk-note", t("理由：{reason}", { reason: d.reason })));
      if (d.note) sec.appendChild(mk("p", "desk-note", t("PI 的附言：{note}", { note: d.note })));
      if (d.missing_from_file) sec.appendChild(line(t("这条记录已经不在 decisions.toml 里了（这台电脑记得它）。"), "warn"));
      sec.appendChild(mk("h3", "panel-h3", direct ? t("放弃时的文件状态") : t("批准时的文件状态")));
      const ul = mk("ul", "evidence-files");
      (d.files || []).forEach((f) => ul.appendChild(evidenceFile(f)));
      if (!(d.files || []).length) ul.appendChild(mk("li", "node-empty", t("当时没有找到属于这条路线的文件。")));
      sec.appendChild(ul);
      panelBody.appendChild(sec);
    });
  }

  // A frozen file's role, what was kept of it and how it stands now (12.4): the payload's codes
  // (role, state, now) in words for the screen -- the engine's own Chinese labels beside them are not shown.
  function evidenceRole(role) {
    switch (role) {
      case "script": return t("步骤脚本");
      case "output": return t("脚本写出的文件");
      default: return t("关联的文件");
    }
  }

  function evidenceState(state) {
    switch (state) {
      case "kept": return t("内容已保存");
      case "fingerprint": return t("只记下了指纹");
      case "missing": return t("当时文件已不存在");
      case "dataless": return t("当时文件只在云端");
      case "unreadable": return t("当时文件无法读取");
      default: return "";
    }
  }

  function evidenceNow(now) {
    switch (now) {
      case "missing": return t("现在已不在项目里");
      case "same": return t("现在仍是这一版");
      case "changed": return t("之后改过");
      default: return "";
    }
  }

  // Why a kept version shows no text: the payload's code (the engine's sentence beside it is not shown).
  function versionReason(v) {
    switch (v.reason_code) {
      case "not_kept": return t("这一版只记下了指纹和大小，没有保存内容");
      case "not_text": return t("这一版不是文本，不在这里显示");
      default: return t("这一版没有保存内容。");
    }
  }

  function evidenceFile(f) {
    const li = mk("li", "evidence-file");
    const head = mk("div", "desk-head");
    head.appendChild(mk("span", "evidence-role", evidenceRole(f.role)));
    head.appendChild(mkButton(f.path, "desk-link mono", () => RONode.openNode(f.node)));
    li.appendChild(head);
    const facts = [evidenceState(f.state), f.size != null ? sizeWords(f.size) : "", evidenceNow(f.now)].filter(Boolean);
    li.appendChild(mk("div", "desk-meta", facts.join(" · ")));
    if (f.sha256) li.appendChild(mk("div", "evidence-sha mono", t("指纹 {sha}", { sha: String(f.sha256).slice(0, 16) })));
    if (f.viewable) {
      const slot = mk("div", "evidence-slot");
      const btn = mkButton(t("查看当时的内容"), "btn btn-small", async () => {
        if (slot.childElementCount) { slot.innerHTML = ""; btn.textContent = t("查看当时的内容"); btn.setAttribute("aria-expanded", "false"); return; }
        btn.disabled = true;
        try {
          // By fingerprint: the version number is the approving machine's own.
          const v = await apiGet("/api/history/version?node=" + encodeURIComponent(f.node) +
            (f.sha256 ? "&sha256=" + encodeURIComponent(f.sha256) : "&version=" + encodeURIComponent(f.version)));
          slot.innerHTML = "";
          if (v.text == null) slot.appendChild(line(versionReason(v), "quiet"));
          else {
            slot.appendChild(mk("div", "desk-meta", v.at ? t("当时的内容，记下于 {when}", { when: whenInLine(v.at) }) : t("当时的内容")));
            slot.appendChild(mk("pre", "evidence-text", v.text));
            if (v.truncated) slot.appendChild(line(t("内容太长，只显示了开头。"), "quiet"));
          }
          btn.textContent = t("收起");
          btn.setAttribute("aria-expanded", "true");
        } catch (err) {
          const s = mk("div", "notice-status");
          failed(s, t("没能读到当时的内容"), err);
          slot.innerHTML = "";
          slot.appendChild(s);
        }
        btn.disabled = false;
      });
      btn.setAttribute("aria-expanded", "false");
      li.append(btn, slot);
    }
    return li;
  }

  // -- 提议 (12.3, 12.9) ----------------------------------------------------------------

  function openProposal(id, member) {
    view.proposal = id;
    view.member = member || null;
    delete views.proposals.dataset.loaded;
    if (typeof closePanel === "function") closePanel();
    if (currentView === "proposals") loadProposals(views.proposals);
    else activateView("proposals");
    window.scrollTo(0, 0);
  }

  function backToList() {
    view.proposal = null;
    view.member = null;
    loadProposals(views.proposals);
  }

  // The sidebar's 提议: the list (inbox or 我的提议), read afresh, even
  // from one proposal's page.
  function showList() {
    view.proposal = null;
    view.member = null;
    delete views.proposals.dataset.loaded;
    if (currentView === "proposals") loadProposals(views.proposals);
    else activateView("proposals");
    window.scrollTo(0, 0);
  }

  async function loadProposals(host, refresh, acted) {
    const epoch = state.dataEpoch;
    if (refresh && !acted && keepsTyping(host)) return;
    if (!refresh) flash.proposals = [];
    if (!refresh) host.replaceChildren(mk("p", "empty-note", t("载入中…")));
    let ctx;
    try {
      ctx = await RODesk.ensureContext();
      if (refresh) { await RODesk.refreshSidebar(); ctx = RODesk.context(); }
    } catch (err) {
      if (epoch !== state.dataEpoch) return;
      renderViewFailure(host, err);
      return;
    }
    if (epoch !== state.dataEpoch) return;
    if (view.proposal) { await renderProposalPage(host, view.proposal, view.member, refresh); return; }
    if (epoch !== state.dataEpoch || view.proposal) return;
    // 18.3: a project of one person has no 提议 page. One reached before the role was known (a view kept from
    // another project, opened at start) goes on to 开始协作, as activateView does once it knows.
    if (RODesk.onePerson() && currentView === "proposals") {
      delete views.members.dataset.loaded;
      activateView("members");
      return;
    }
    const top = window.scrollY;
    host.innerHTML = "";
    const page = mk("div", "team");
    const m = ctx && ctx.mode;
    if (m === "single" || !m) {
      page.appendChild(block(t("提议"), "plain"));
      page.lastChild.appendChild(line(t("这是单人项目：你的改动直接生效，不需要提议和批准。要和别人一起做，在「成员」一栏里开始协作。"), "quiet"));
      page.lastChild.appendChild(mkButton(t("去成员一栏"), "btn btn-small", () => activateView("members")));
    } else if (m === "none") {
      page.appendChild(mk("p", "desk-alone", t("你还不是这个项目的成员：请 PI 把你加入团队，然后同步")));
    } else if (m === "pi") {
      renderInbox(page, ctx);
    } else {
      renderMine(page, ctx);
      renderComposer(page);
    }
    showFlash(page, "proposals");
    host.appendChild(page);
    host.dataset.loaded = "1";
    if (refresh) window.scrollTo(0, top);
  }

  function proposalRow(p, team, forPi) {
    const li = mk("li", "desk-row");
    const main = mk("div", "desk-main");
    const head = mk("div", "desk-head");
    head.appendChild(label(RODesk.kindWord(p.kind) || t("提议"), "plain"));
    head.appendChild(mkButton(p.sentence || RODesk.proposalWhat(p) || p.id, "desk-link strong", () => openProposal(p.id, p.by)));
    const st = stateLabel(p);
    head.appendChild(label(st.label, st.tone));
    main.appendChild(head);
    const what = RODesk.proposalWhat(p);
    if (what && p.sentence) main.appendChild(mk("p", "desk-note plain", what));
    const meta = [];
    if (forPi) meta.push(RODesk.nameOf(p.by, team));
    if (p.verification && p.verification.state !== "verified") meta.push(t("签名未验证"));
    main.appendChild(mk("div", "desk-meta", meta.join(" · ")));
    if (p.state === "returned") {
      const o = lastOutcome(p, "returned");
      if (o && o.text) main.appendChild(mk("p", "desk-note", t("退回的理由：{reason}", { reason: o.text })));
    } else if (p.state === "pending") {
      const talk = lastOutcome(p, "discussed");
      if (talk && talk.text) main.appendChild(mk("p", "desk-note", t("PI 说：{text}", { text: talk.text })));
    }
    li.append(mk("span", "desk-when", when(p.at)), main);
    return li;
  }

  function problemsBlock(page, problems) {
    if (!problems || !problems.length) return;
    const sec = block(t("公盘上读不了的东西"), "warn");
    sec.appendChild(line(t("这些内容没有读取，也没有当作提议：多半是同步服务留下的冲突副本，或者还没写完。"), "quiet"));
    const ul = mk("ul", "desk-files");
    problems.slice(0, 20).forEach((p) => {
      const li = mk("li");
      li.appendChild(mk("span", "mono", p.path || ""));
      const s = mk("span", "team-help");
      renderBlockingError(s, t("无法读取"), p.detail || p.kind || "");
      li.appendChild(s);
      ul.appendChild(li);
    });
    sec.appendChild(ul);
    page.appendChild(sec);
  }

  function renderInbox(page, ctx) {
    const team = (ctx.share && ctx.share.team) || [];
    const list = (ctx.proposals && ctx.proposals.proposals) || [];
    const open = list.filter((p) => p.state === "pending" || p.state === "conflict");
    const done = list.filter((p) => !(p.state === "pending" || p.state === "conflict"));
    const sec = block(t("收件箱"));
    if (!open.length) sec.appendChild(line(t("没有等你批准的提议。"), "quiet"));
    else {
      const ul = mk("ul", "desk-list");
      open.forEach((p) => ul.appendChild(proposalRow(p, team, true)));
      sec.appendChild(ul);
    }
    page.appendChild(sec);
    if (done.length) {
      const old = block(t("已经答复的"));
      const ul = mk("ul", "desk-list");
      done.slice().reverse().slice(0, 30).forEach((p) => ul.appendChild(proposalRow(p, team, true)));
      old.appendChild(ul);
      page.appendChild(old);
    }
    problemsBlock(page, ctx.proposals && ctx.proposals.problems);
  }

  function renderMine(page, ctx) {
    const team = (ctx.share && ctx.share.team) || [];
    const list = (ctx.proposals && ctx.proposals.proposals) || [];
    const sec = block(t("我的提议"));
    if (!list.length) sec.appendChild(line(t("你还没有提交过提议。"), "quiet"));
    else {
      const ul = mk("ul", "desk-list");
      list.forEach((p) => ul.appendChild(proposalRow(p, team, false)));
      sec.appendChild(ul);
    }
    page.appendChild(sec);
  }

  // -- one proposal's page -------------------------------------------------------------

  // The 提议 page: the way back, then the proposal itself.
  async function renderProposalPage(host, id, member, refresh) {
    const epoch = state.dataEpoch;
    const page = mk("div", "node-page proposal-page");
    page.appendChild(backBar());
    const full = await renderProposalInto(page, id, member, (said) => after("proposals", [{ text: said.text }]));
    if (epoch !== state.dataEpoch || view.proposal !== id) return;
    const top = window.scrollY;
    host.innerHTML = "";
    if (full) showFlash(page, "proposals", page.children[1] || null);
    host.appendChild(page);
    host.dataset.loaded = "1";
    if (full && refresh) window.scrollTo(0, top);
  }

  // One proposal drawn into `host` -- the 提议 page or the desk's side panel
  // (14.3) --, read first, so `host` is untouched until it arrives (a
  // refusal is said in it instead): who and when, its answers, what
  // approving would do (the PI) or what it carries, the variable versions
  // its files use, and while it waits 批准并加入 / 退回并说明 / 讨论, each
  // answer followed by onDone({text, act, result}). `member` names whose it
  // is (an id in two members' areas). opts.panel: the name is left to the
  // panel's title; opts.open(nodeId) opens a variable (else its page).
  // Resolves to the proposal, or null when it could not be read.
  async function renderProposalInto(host, id, member, onDone, opts) {
    const options = opts || {};
    let full;
    try {
      full = await apiGet("/api/proposals/" + encodeURIComponent(id) + (member ? "?from=" + encodeURIComponent(member) : ""));
    } catch (err) {
      const box = mk("div", "node-failed");
      failed(box, t("没能打开这份提议"), err);
      host.appendChild(box);
      return null;
    }
    const ctx = RODesk.context();
    const team = (ctx && ctx.share && ctx.share.team) || [];
    // who, when, kind, the member's sentence
    const head = mk("header", "node-head");
    const kind = RODesk.kindWord(full.kind);
    head.appendChild(mk("div", "node-kind", kind ? t("{kind}提议", { kind: kind }) : t("提议", { n: 1 })));
    if (!options.panel) head.appendChild(mk("h2", "node-name", full.sentence || id));
    const meta = [RODesk.nameOf(full.by, team), when(full.at)]; // 12.13: the name, never the raw id
    if (full.base_seq != null) meta.push(t("基于正式版本第 {seq} 版", { seq: numberText(full.base_seq) }));
    head.appendChild(mk("div", "desk-meta", meta.join(" · ")));
    const marks = mk("div", "node-marks");
    const st = stateLabel(full);
    marks.appendChild(label(st.label, st.tone));
    const sig = signatureWords(full.verification);
    marks.appendChild(label(sig.label, sig.tone));
    marks.appendChild(mk("span", "node-sub-inline", sig.reason));
    head.appendChild(marks);
    host.appendChild(head);
    // its answers so far
    const outs = full.outcomes || [];
    if (outs.length) {
      const s = sec(t("答复"));
      const ul = mk("ul", "desk-list");
      outs.forEach((o) => {
        const li = mk("li", "desk-row");
        const who = RODesk.nameOf(o.by, team);
        const said = o.act === "approved" ? t("{who} 批准了", { who: who }) : o.act === "returned" ? t("{who} 退回了", { who: who })
          : o.act === "discussed" ? t("{who} 说", { who: who }) : who + " " + o.act;
        const main = mk("div", "desk-main");
        main.appendChild(mk("div", "desk-head", said));
        if (o.text) main.appendChild(mk("p", "desk-note", o.text));
        li.append(mk("span", "desk-when", when(o.at)), main);
        ul.appendChild(li);
      });
      s.appendChild(ul);
      host.appendChild(s);
    }
    // What approving would do is the PI's question while it waits; once
    // answered, it says what the proposal carried.
    if (full.changes && (full.state === "pending" || full.state === "conflict")) renderWhatApprovingDoes(host, full);
    else renderCarried(host, full);
    renderUses(host, full.uses, options.open);
    if (full.changes && full.state === "pending") renderDecide(host, full, team, onDone);
    if (full.state === "conflict") {
      // 13.1: the shared drive's 已发布 folder, a fixed name shown as it is.
      host.appendChild(line(t("这份提议的答复记录互相矛盾（同时有批准和退回）：请在公盘上检查 {folder} 里它的答复。",
        { folder: "已发布/outcomes/" }), "warn"));
    }
    return full;
  }

  function backBar() {
    const bar = mk("div", "node-back");
    bar.appendChild(mkButton(t("返回提议列表"), "node-link", backToList));
    return bar;
  }

  function sec(title) {
    const s = mk("section", "node-sec");
    s.appendChild(mk("h3", "node-sec-title", title));
    return s;
  }

  function diffOf(text, truncated) {
    return RONode.diffBlock(RONode._diffRows(text), truncated);
  }

  // The member's own view: what the proposal carries.
  function renderCarried(page, full) {
    const s = sec(full.state === "approved" ? t("已写入正式版本的改动") : t("提议带来的改动"));
    if (full.attempt) {
      const a = full.attempt;
      const p = mk("p", "desk-head");
      p.append(t("尝试 #{number}：{description}", { number: a.number, description: a.description || "" }), " ");
      if (a.verdict_status) p.appendChild(RODesk.verdictInline(a.verdict_status));
      s.appendChild(p);
      if (a.abandons === "yes") {
        s.appendChild(mk("p", "desk-note", t("结果：{result}", { result: a.result || "" })));
        s.appendChild(mk("p", "desk-note", t("理由：{reason}", { reason: a.reason || "" })));
      }
      (a.parent_words || []).forEach((w) => s.appendChild(mk("p", "desk-note plain", w)));
    }
    if (full.verdict) {
      const v = full.verdict;
      s.appendChild(phrase(mk("p", "desk-head"), t("尝试 #{number} 的研究判断 {old} 改为 {new}", { number: v.number }),
        { old: RODesk.verdictInline(v.old_status), new: RODesk.verdictInline(v.new_status) }));
      if (v.result) s.appendChild(mk("p", "desk-note", t("结果：{result}", { result: v.result })));
      if (v.reason) s.appendChild(mk("p", "desk-note", t("理由：{reason}", { reason: v.reason })));
    }
    if (full.variable) {
      s.appendChild(line(t("变量 {card} 的第 {version} 版定义", { card: full.variable.card, version: full.variable.version }), ""));
    }
    if (full.route) s.appendChild(line(RODesk.proposalWhat(full), ""));
    if (full.route && full.route.note) s.appendChild(mk("p", "desk-note", t("备注：{note}", { note: full.route.note })));
    s.appendChild(fileList(full.files || [], null));
    page.appendChild(s);
  }

  function fileList(files, changes) {
    const ul = mk("ul", "proposal-files");
    if (!files.length) ul.appendChild(mk("li", "node-empty", t("没有文件。")));
    files.forEach((f) => {
      const li = mk("li", "proposal-file");
      const head = mk("div", "desk-head");
      head.appendChild(mk("span", "evidence-role", fileAction(f.action)));
      head.appendChild(mk("span", "mono", f.path));
      if (f.size != null && f.action !== "removed") head.appendChild(mk("span", "desk-meta", sizeWords(f.size)));
      if (changes && f.state) head.appendChild(label(fileState(f.state), f.state === "conflict" || f.state === "problem" ? "clay" : f.state === "apply" ? "olive" : "plain"));
      li.appendChild(head);
      if (f.store === "blob" && f.action !== "removed") {
        li.appendChild(mk("div", "desk-meta", f.content ? t("大文件，按内容存放在公盘：") : t("大文件，按内容存放在公盘")));
        if (f.content) li.appendChild(mk("div", "evidence-sha mono", f.content));
      }
      if (changes && f.reason) li.appendChild(mk("p", "desk-break", f.reason));
      // 12.3: for data, the fingerprint and -- for tables -- rows and columns.
      if (f.action !== "removed" && f.sha256 && (f.store === "blob" || f.table_words || f.summary_words)) {
        const fp = mk("div", "desk-meta", t("指纹 "));
        fp.appendChild(mk("span", "mono", String(f.sha256).slice(0, 16)));
        fp.title = String(f.sha256);
        li.appendChild(fp);
      }
      // 12.13: a table's rows and columns first; its line diff below only when small.
      if (f.table_words && f.table_words !== f.summary_words) {
        li.appendChild(mk("p", "desk-note plain", t("表格：{words}", { words: f.table_words })));
      }
      if (f.summary_words) li.appendChild(mk("p", "desk-note plain", f.summary_words));
      else if (f.sentence_words && f.store === "blob") li.appendChild(mk("p", "desk-note plain", f.sentence_words));
      if (f.diff && f.table_small === false) {
        li.appendChild(mk("p", "desk-note plain", t("表格较大：只比较行和列，不逐行对比。")));
      } else if (f.diff) {
        const d = mk("details", "proposal-diff");
        d.appendChild(mk("summary", null, t("查看改动")));
        d.appendChild(diffOf(f.diff, f.truncated));
        if (f.action === "added" || files.length <= 3) d.open = true;
        li.appendChild(d);
      }
      ul.appendChild(li);
    });
    return ul;
  }

  // An abandonment approving would record (12.4): its result, its reason --
  // as the proposal carries them (`carried`); `a.reason` is a problem's
  // sentence when there is one -- and that the route's basis freezes.
  function abandonsLines(part, carried, a) {
    part.appendChild(mk("p", "desk-note", t("结果：{result}", { result: carried.result || a.result || "" })));
    part.appendChild(mk("p", "desk-note", t("放弃理由：{reason}", { reason: carried.reason || "" })));
    part.appendChild(line(t("批准会把结果和理由写进尝试表，并冻结这条路线当时的依据：它的步骤脚本和它们写出的文件。"), "quiet"));
  }

  // The PI's view: exactly what approving would do (12.3).
  function renderWhatApprovingDoes(page, full) {
    const c = full.changes;
    const s = sec(t("批准以后会怎样"));
    if (c.moved) s.appendChild(line(t("提议之后正式版本又有了改动；只有两边改了同一处时才会挡住批准。"), "quiet"));
    if (c.resuming) s.appendChild(line(t("这份提议的批准上次没有做完：再次批准会接着把剩下的做完。"), "warn"));
    const a = c.attempt;
    if (a) {
      const part = mk("div", "proposal-part");
      if (a.op === "append") {
        const p = phrase(mk("p", "desk-head"), t("在 {file} 中新增尝试 #{number}", { number: a.number }),
          { file: mk("span", "mono", a.file || "") });
        if (a.verdict_status) p.appendChild(RODesk.verdictInline(a.verdict_status));
        part.appendChild(p);
        if (a.abandons) abandonsLines(part, full.attempt || {}, a);
        (a.parent_words || []).forEach((w) => part.appendChild(mk("p", "desk-note plain", w))); // 14.2, in the engine's words
      } else {
        part.appendChild(phrase(mk("p", "desk-head"), t("尝试 #{number} 的研究判断 {old} 改为 {new}", { number: a.number }),
          { old: RODesk.verdictInline(a.old_status), new: RODesk.verdictInline(a.new_status) }));
        const v = full.verdict || {};
        if (a.abandons) {
          abandonsLines(part, v, a);
        } else if (v.result && v.result !== v.old_result) {
          part.appendChild(mk("p", "desk-note", t("结果改为：{result}", { result: v.result })));
        }
      }
      if (a.state === "conflict" || a.state === "problem") part.appendChild(mk("p", "desk-break", a.reason || ""));
      else if (a.state === "same") part.appendChild(line(t("正式版本里这一行已经是这样了。"), "quiet"));
      if (a.old_row || a.new_row) {
        if (a.old_row) { part.appendChild(mk("div", "diff-label", t("原行"))); part.appendChild(mk("div", "diff-row old", a.old_row)); }
        if (a.new_row) { part.appendChild(mk("div", "diff-label", t("新行"))); part.appendChild(mk("div", "diff-row new", a.new_row)); }
      }
      s.appendChild(part);
    }
    const v = c.variable;
    if (v) {
      const part = mk("div", "proposal-part");
      const which = { card: v.card, version: v.number || "" };
      part.appendChild(mk("p", "desk-head", v.new_card ? t("新建变量卡 {card} 的第 {version} 版定义", which)
        : t("确认变量 {card} 的第 {version} 版定义", which)));
      part.appendChild(mk("p", "desk-note plain", t("成员的回答：现有的输出文件是按这个定义生成的吗？{answer}",
        { answer: attestedWord(v.attested) })));
      if (v.reason) part.appendChild(mk("p", "desk-break", v.reason));
      if (v.diff) part.appendChild(diffOf(v.diff, v.truncated));
      s.appendChild(part);
    }
    const r = c.route; // a 「源自」 line (14.2), in the engine's words
    if (r) {
      const box = mk("div", "proposal-part");
      box.appendChild(mk("p", "desk-head", r.words || ""));
      if (r.note) box.appendChild(mk("p", "desk-note", t("备注：{note}", { note: r.note })));
      if (r.state === "conflict" || r.state === "problem") box.appendChild(mk("p", "desk-break", r.reason || ""));
      else if (r.state === "same") box.appendChild(line(t("正式版本的连线记录里已经是这样了。"), "quiet"));
      s.appendChild(box);
    }
    const files = c.files || [];
    if (files.length) {
      s.appendChild(mk("h4", "proposal-h4", t("文件")));
      s.appendChild(fileList(files, c));
    }
    if (!a && !v && !r && !files.length) s.appendChild(line(t("这份提议没有带来任何改动。"), "quiet"));
    const ctxNow = RODesk.context();
    const coming = ctxNow && ctxNow.share && ctxNow.share.incoming;
    const carried = packetWords(coming);
    s.appendChild(line(carried
      ? t("批准后发布到公盘，同时加入成员同步的{carried}。",
        { carried: carried })
      : t("批准后发布到公盘。"),
    carried ? "writes" : "quiet"));
    if (carried) s.appendChild(removalList(coming));
    page.appendChild(s);
    if ((c.not_carried || []).length) {
      const n = sec(t("没有随提议发送的改动"));
      const ul = mk("ul", "desk-files");
      c.not_carried.forEach((x) => {
        const li = mk("li");
        li.appendChild(mk("span", "mono", x.path || ""));
        const why = mk("span", "team-help");
        renderBlockingError(why, x.words || x.reason_zh || t("这一处改动没有包含在提议里"), x.reason || "");
        li.appendChild(why);
        ul.appendChild(li);
      });
      n.appendChild(ul);
      page.appendChild(n);
    }
    if ((c.conflicts || []).length || (c.problems || []).length) {
      const k = sec(t("现在还不能批准"));
      (c.conflicts || []).forEach((x) => {
        const p = mk("p", "desk-break");
        p.append(mk("span", "mono", x.path || ""), t("：{reason}", { reason: x.reason || t("两边都改过") }));
        k.appendChild(p);
      });
      if ((c.conflicts || []).length) {
        k.appendChild(line(t("Research OS 不会替两个人合并同一个文件。请成员先同步到最新的正式版本，在那上面重新提议；这份可以退回并说明。"), ""));
      }
      (c.problems || []).forEach((x) => k.appendChild(mk("p", "desk-break", x)));
      page.appendChild(k);
    }
  }

  function renderUses(page, uses, open) {
    if (!uses || !uses.length) return;
    const s = sec(t("用到的变量版本"));
    const ul = mk("ul", "desk-files");
    uses.forEach((u) => {
      const li = mk("li");
      li.appendChild(mkButton(t("{name} 第 {version} 版", { name: u.name, version: u.version }), "desk-link", () => (open || RONode.openNode)("variable:" + u.card)));
      li.appendChild(mk("span", "team-help", u.how || ""));
      ul.appendChild(li);
    });
    s.appendChild(ul);
    page.appendChild(s);
  }

  // 批准并加入 / 退回并说明 / 讨论 (12.3); what the answer did goes to onDone.
  function renderDecide(page, full, team, onDone) {
    const c = full.changes;
    const s = sec(t("你的答复"));
    const status = mk("div", "notice-status");
    const name = RODesk.nameOf(full.by, team);
    // approve
    const approve = mk("div", "decide");
    const note = textArea(2, t("给这次批准写一句附言（可以不写）"));
    approve.appendChild(field(t("批准附言"), note));
    const approveBtn = mkButton(t("批准并加入"), "btn btn-primary", () => decide("approve", { note: note.value.trim() || undefined }));
    approve.appendChild(approveBtn);
    if (!c.approvable) {
      approveBtn.disabled = true;
      approve.appendChild(line(t("有两边都改过的地方或问题，现在不能批准（见上面）。"), "warn"));
    }
    s.appendChild(approve);
    // return
    const ret = mk("div", "decide");
    const reason = textArea(2, t("为什么退回：{who} 会看到这句话", { who: name }));
    ret.appendChild(field(t("退回的理由（必填）"), reason));
    const retBtn = mkButton(t("退回并说明"), "btn", () => {
      if (!reason.value.trim()) { setStatus(status, "err", t("请写明退回的理由")); reason.focus(); return; }
      decide("return", { reason: reason.value.trim() });
    });
    ret.appendChild(retBtn);
    s.appendChild(ret);
    // discuss
    const dis = mk("div", "decide");
    const text = textArea(2, t("想问或想说的话：提议保持打开"));
    dis.appendChild(field(t("讨论"), text));
    const disBtn = mkButton(t("讨论"), "btn", () => {
      if (!text.value.trim()) { setStatus(status, "err", t("请写下要讨论的内容")); text.focus(); return; }
      decide("discuss", { text: text.value.trim() });
    });
    dis.appendChild(disBtn);
    s.appendChild(dis);
    // 13.1: the shared drive's 已发布 folder, a fixed name shown as it is.
    s.appendChild(line(t("答复会签名后写进公盘的「{folder}」，{who} 同步时就会看到；写成以后不再改动。",
      { folder: "已发布/outcomes/", who: name }), "quiet"));
    s.appendChild(status);
    page.appendChild(s);
    const buttons = [approveBtn, retBtn, disBtn];

    async function decide(act, body) {
      buttons.forEach((b) => { b.disabled = true; });
      setStatus(status, "", act === "approve" ? t("正在批准并发布…") : t("正在写入答复…"));
      let res;
      try {
        res = await apiPost("/api/proposals/" + encodeURIComponent(full.id) + "/" + act, Object.assign({ from: full.by }, body));
      } catch (err) {
        buttons.forEach((b) => { b.disabled = false; });
        if (!c.approvable) approveBtn.disabled = true;
        failed(status, act === "approve" ? t("没能批准，正式版本没有改动") : t("没能写入答复"), err);
        return;
      }
      const done = act === "approve" ? approveSentence(res)
        : act === "return" ? t("已退回：{who} 会看到你的理由。", { who: name }) : t("已写下：提议保持打开。");
      // Answered: nothing here is left half typed, and it says what it did
      // until whoever drew it draws it again.
      [note, reason, text].forEach((f) => { f.value = ""; delete f.dataset.typed; });
      setStatus(status, "ok", done);
      await RODesk.refreshSidebar();
      markViewsStale(); // 研究路线 and the desk list this proposal too
      if (typeof onDone === "function") await onDone({ text: done, act: act, result: res });
      if (act === "approve" && typeof pollGeneration === "function") pollGeneration();
    }
  }

  // -- 提交提议 (a member) -----------------------------------------------------------------

  function renderComposer(page) {
    const s = block(t("提交提议"));
    s.dataset.anchor = "compose"; // the shell's 文件 > 提交提议…
    const form = mk("div", "compose");
    const kinds = mk("div", "compose-kinds");
    kinds.setAttribute("role", "radiogroup");
    kinds.setAttribute("aria-label", t("提议的类型"));
    let kind = (view.compose && view.compose.kind) || "attempt_new";
    const extra = mk("div", "compose-extra");
    const inputs = {};
    kindChoices().forEach(([k, text, help]) => {
      const lab = mk("label", "compose-kind");
      const r = mk("input");
      r.type = "radio";
      r.name = "compose-kind";
      r.value = k;
      r.checked = k === kind;
      r.addEventListener("change", () => { kind = k; drawExtra(); });
      lab.append(r, mk("span", "compose-kind-name", text), mk("span", "team-help", help));
      kinds.appendChild(lab);
    });
    const sentence = textArea(2, t("用你自己的话说明这份提议：PI 会先看到这句话"));
    function drawExtra() {
      extra.innerHTML = "";
      Object.keys(inputs).forEach((k) => delete inputs[k]);
      if (kind === "attempt_new" || kind === "verdict_change") {
        inputs.number = textInput(t("不填：只有一行时自动找出"), true);
        extra.appendChild(field(t("尝试的编号"), inputs.number, t("工作副本里改了不止一行时，填上要提议的那一行")));
      }
      if (kind === "attempt_new") {
        inputs.parents = textInput(t("例如 16, 17"), true);
        extra.appendChild(field(t("源自（可选）"), inputs.parents, t("它源自的正式尝试；PI 批准后，这些连线画到路线图上")));
      }
      if (kind === "verdict_change") {
        inputs.result = textArea(2, t("不填：用尝试表里这一行现在的结果"));
        extra.appendChild(field(t("结果"), inputs.result));
        inputs.reason = textArea(2, t("放弃一条路线时必须写明"));
        extra.appendChild(field(t("理由"), inputs.reason, t("放弃（研究判断标为已放弃）时，结果和理由都必须填写")));
      }
      if (kind === "variable_version") {
        inputs.card = textInput(t("变量卡的编号，例如 topicshift"), true);
        extra.appendChild(field(t("变量卡"), inputs.card));
        const sel = mk("select", "form-input");
        [["", t("请选择")], ["yes", t("是")], ["no", t("否")], ["unknown", t("不确定")]].forEach(([v, words]) => {
          const o = mk("option", null, words);
          o.value = v;
          sel.appendChild(o);
        });
        inputs.attested = sel;
        extra.appendChild(field(t("现有的输出文件是按这个定义生成的吗"), sel));
      }
    }
    drawExtra();
    form.append(kinds, field(t("一句话说明"), sentence), extra);
    const status = mk("div", "notice-status");
    const look = mkButton(t("查看将要发送的内容"), "btn btn-primary", async () => {
      if (!sentence.value.trim()) { setStatus(status, "err", t("请用一句话说明这份提议")); sentence.focus(); return; }
      const body = { kind: kind, sentence: sentence.value.trim() };
      if (inputs.number && inputs.number.value.trim()) body.number = inputs.number.value.trim().replace(/^#/, "");
      if (inputs.result && inputs.result.value.trim()) body.result = inputs.result.value.trim();
      if (inputs.reason && inputs.reason.value.trim()) body.reason = inputs.reason.value.trim();
      const parents = inputs.parents ? parentNumbers(inputs.parents.value) : [];
      if (parents.length) body.parents = parents;
      if (inputs.card) body.card = inputs.card.value.trim();
      if (inputs.attested) {
        if (!inputs.attested.value) { setStatus(status, "err", t("请回答：现有的输出文件是否按这个定义生成")); return; }
        body.attested = inputs.attested.value;
      }
      view.compose = { kind: kind };
      await composeInto(result, body, status);
    });
    form.append(look, status);
    const result = mk("div", "compose-result");
    s.append(form, result);
    page.appendChild(s);
  }

  // 「源自」 numbers as typed -- "16, 17", "#16 #17", 「16、17」 -- one each.
  function parentNumbers(text) {
    return Array.from(new Set(String(text || "").split(/[\s,，、;；]+/).map((x) => x.replace(/^#/, "").trim()).filter(Boolean)));
  }

  // The engine's dry run of a proposal, shown exactly, then 发送提议. Used
  // by 提交提议 and by the attempt form's 提议新的尝试 / 提议修改研究判断.
  async function composeInto(host, body, status) {
    setStatus(status, "", t("正在比较你的工作副本和正式版本…"));
    host.innerHTML = "";
    let draft;
    try {
      draft = await apiPost("/api/proposals", body);
    } catch (err) {
      failed(status, t("没能生成提议"), err);
      return null;
    }
    setStatus(status, "", "");
    renderDraft(host, body, draft, status);
    return draft;
  }

  function renderDraft(host, body, draft, status) {
    host.innerHTML = "";
    const d = draft.proposal || {};
    const box = mk("div", "draft");
    box.appendChild(mk("h3", "node-sec-title", t("将要发送的提议")));
    box.appendChild(mk("p", "proposal-sentence", d.sentence || ""));
    if (d.attempt) {
      const p = mk("p", "desk-head");
      p.append(t("尝试 #{number}：{description}", { number: d.attempt.number, description: d.attempt.description || "" }), " ");
      if (d.attempt.verdict_status) p.appendChild(RODesk.verdictInline(d.attempt.verdict_status));
      box.appendChild(p);
      if (d.attempt.abandons === "yes") {
        box.appendChild(mk("p", "desk-note", t("结果：{result}", { result: d.attempt.result || t("（空）") })));
        box.appendChild(mk("p", "desk-note", t("理由：{reason}", { reason: d.attempt.reason || "" })));
      }
      (d.attempt.parent_words || []).forEach((w) => box.appendChild(mk("p", "desk-note plain", w)));
    }
    if (d.verdict) {
      const v = d.verdict;
      box.appendChild(phrase(mk("p", "desk-head"), t("尝试 #{number} 的研究判断 {old} 改为 {new}", { number: v.number }),
        { old: RODesk.verdictInline(v.old_status), new: RODesk.verdictInline(v.new_status) }));
      box.appendChild(mk("p", "desk-note", t("结果：{result}", { result: v.result || t("（空）") })));
      if (v.reason) box.appendChild(mk("p", "desk-note", t("理由：{reason}", { reason: v.reason })));
    }
    if (d.variable) {
      box.appendChild(line(t("变量 {card} 的第 {version} 版定义（成员的回答：{answer}）",
        { card: d.variable.card, version: d.variable.version, answer: attestedWord(d.variable.attested) }), ""));
    }
    // 12.13: what travels depends on the kind -- a verdict change its row
    // only, a new attempt its row and the files linked to it, a files
    // proposal the files chosen. Every file that differs is offered, ticked
    // when it travels; the member adds or unticks, then looks again.
    const carried = {};
    ((draft.preview && draft.preview.files) || d.file || []).forEach((f) => { carried[f.path] = f; });
    const candidates = (draft.preview && draft.preview.candidates) ||
      Object.keys(carried).map((p) => Object.assign({ chosen: true }, carried[p]));
    box.appendChild(mk("h4", "proposal-h4", t("带上的文件")));
    box.appendChild(line(carryWords(d.kind), "quiet"));
    if (!candidates.length) box.appendChild(line(t("工作副本里没有和正式版本不同的文件。"), "quiet"));
    const picks = [];
    candidates.forEach((c) => {
      const f = carried[c.path] || c;
      const row = mk("div", "draft-file" + (c.chosen ? "" : " left-out"));
      const lab = mk("label", "desk-head");
      const cb = mk("input");
      cb.type = "checkbox";
      cb.checked = !!c.chosen;
      cb.value = c.path;
      picks.push(cb);
      lab.append(cb, mk("span", "evidence-role", fileAction(c.action)), mk("span", "mono", c.path));
      if (c.size != null && c.action !== "removed") {
        lab.appendChild(mk("span", "desk-meta", c.store === "blob" ? t("{size}，大文件", { size: sizeWords(c.size) }) : sizeWords(c.size)));
      }
      if (c.linked) lab.appendChild(mk("span", "desk-meta", t("与这条尝试关联")));
      const own = c.officialness && (c.officialness.state === "proposed" || c.officialness.state === "approved") ? ownMark(c) : null;
      if (own) lab.appendChild(own);
      row.appendChild(lab);
      if ((c.returned || []).length) row.appendChild(mk("p", "desk-break", t("这个文件曾在被退回的提议里：先看看退回的理由，再决定是否带上。")));
      if (c.chosen && f.diff) {
        const det = mk("details", "proposal-diff");
        det.appendChild(mk("summary", null, t("查看改动")));
        det.appendChild(diffOf(f.diff, f.truncated));
        row.appendChild(det);
      }
      box.appendChild(row);
    });
    if (candidates.length) {
      const again = mkButton(t("按勾选的文件重新查看"), "btn btn-small", () => {
        const only = picks.filter((c) => c.checked).map((c) => c.value);
        composeInto(host, Object.assign({}, body, { only: only }), status);
      });
      box.appendChild(again);
    }
    if ((d.not_carried || []).length) {
      box.appendChild(mk("h4", "proposal-h4", t("不会发送的改动")));
      const ul = mk("ul", "desk-files");
      d.not_carried.forEach((x) => {
        const li = mk("li");
        li.appendChild(mk("span", "mono", x.path || ""));
        const why = mk("span", "team-help");
        renderBlockingError(why, x.words || x.reason_zh || t("这一处改动没有包含在提议里"), x.reason || "");
        li.appendChild(why);
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }
    const ctx = RODesk.context();
    const me = ctx && ctx.me && ctx.me.me ? ctx.me.me.id : null;
    box.appendChild(line(sendWrites(d, me), "writes"));
    const sendStatus = mk("div", "notice-status");
    const send = mkButton(t("发送提议"), "btn btn-primary", async () => {
      send.disabled = true;
      setStatus(sendStatus, "", t("正在写入公盘…"));
      let res;
      try {
        res = await apiPost("/api/proposals", Object.assign({}, body, { write: true, expected: draft.fingerprint }));
      } catch (err) {
        send.disabled = false;
        failed(sendStatus, t("没能发送提议，公盘上没有写入"), err);
        return;
      }
      const sent = t("已发送：PI 会在收件箱里看到它。在 PI 批准之前，它不是正式记录。");
      setStatus(sendStatus, "ok", sent);
      send.textContent = t("已发送");
      view.compose = null;
      await RODesk.refreshSidebar();
      // Every view now marks what this proposal carries 「提议中」: the
      // hidden ones reload on their next showing, the open one now.
      markViewsStale();
      if (currentView === "proposals" && !view.proposal) await after("proposals", [{ text: sent }]);
      else if (typeof refreshCurrentView === "function" && currentView !== "proposals") await refreshCurrentView();
      if (typeof pollGeneration === "function") pollGeneration();
      return res;
    });
    box.append(send, sendStatus);
    host.appendChild(box);
  }

  // -- 成员 (12.1, 12.2, 12.5) ---------------------------------------------------------

  async function loadMembers(host, refresh, acted) {
    const epoch = state.dataEpoch;
    if (refresh && !acted && keepsTyping(host)) return;
    if (!refresh) flash.members = [];
    if (!refresh) host.innerHTML = '<p class="empty-note">载入中…</p>';
    let me, key, share, ctx;
    try {
      ctx = await RODesk.ensureContext();
      [me, key, share] = await Promise.all([
        apiGet("/api/me"),
        apiGet("/api/me/key").catch(() => null),
        apiGet("/api/share").catch((err) => ({ failed: err })),
      ]);
    } catch (err) {
      if (epoch !== state.dataEpoch) return;
      renderViewFailure(host, err);
      return;
    }
    if (epoch !== state.dataEpoch) return;
    clearProjectState();
    const top = window.scrollY;
    host.innerHTML = "";
    const page = mk("div", "team");
    const m = ctx && ctx.mode;
    const role = share && !share.failed ? share.role : null;
    if (m === "none") page.appendChild(mk("p", "desk-alone", "你还不是这个项目的成员：请 PI 把你加入团队，然后同步"));
    renderMe(page, me);
    if (share && share.failed) {
      const warn = mk("p", "team-line warn");
      failed(warn, "没能读到这个项目的团队和公盘", share.failed);
      page.appendChild(warn);
    } else {
      renderTeam(page, share, me);
      if (role === "pi") {
        renderShared(page, share);
        memberAreas(page, share);
        renderAddMember(page, share);
      } else if (share.joined) {
        renderMemberShare(page, share);
      } else {
        renderStart(page, share, me);
      }
    }
    renderKey(page, key, role === "pi" ? "pi" : share && share.joined ? "member" : "single");
    const other = block("加入别人的项目");
    other.appendChild(line("PI 把公盘文件夹共享给你以后，可以从那里做一份自己的工作副本。", "quiet"));
    other.appendChild(mkButton("从公盘加入项目…", "btn btn-small", () => openJoinDialog()));
    page.appendChild(other);
    showFlash(page, "members");
    host.appendChild(page);
    host.dataset.loaded = "1";
    if (refresh) window.scrollTo(0, top);
  }

  // 我是谁: set once, on first use -- the only form of its kind (12.1).
  function renderMe(page, me) {
    const s = block("我是谁");
    const who = me && me.me;
    if (who) {
      const p = mk("p", "team-me");
      p.append(mk("span", "team-me-name", who.name || who.id), mk("span", "mono", who.email || who.id));
      s.appendChild(p);
      s.appendChild(line("你在这台电脑上写下的每一条记录都署这个名字。", "quiet"));
    } else {
      s.appendChild(meForm(me, () => { RODesk.refreshSidebar(); after("members", [{ text: "已保存你是谁。" }]); }));
    }
    page.appendChild(s);
  }

  function meForm(me, done) {
    const wrap = mk("div", "compose");
    if (me && me.state && me.state !== "absent") {
      const warn = mk("p", "team-line warn");
      renderBlockingError(warn, "这台电脑上记着的「我是谁」现在读不出来；重新填写会替换它", me.error || "");
      wrap.appendChild(warn);
    }
    wrap.appendChild(line("第一次使用时填一次：你的名字，和你用来共享公盘的那个邮箱。邮箱就是你在团队里的编号。", "quiet"));
    const name = textInput("名字");
    const email = textInput("邮箱", true);
    wrap.append(field("名字", name), field("邮箱", email));
    wrap.appendChild(line("将写入这台电脑上的 " + ((me && me.path) || "~/.researchos/me.toml") + "；项目里不写入任何东西。", "writes"));
    const status = mk("div", "notice-status");
    const btn = mkButton("保存", "btn btn-primary", async () => {
      if (!name.value.trim() || !email.value.trim()) { setStatus(status, "err", "请填写名字和邮箱"); return; }
      btn.disabled = true;
      try {
        await apiPost("/api/me", { name: name.value.trim(), email: email.value.trim() });
      } catch (err) {
        btn.disabled = false;
        failed(status, "没能保存", err);
        return;
      }
      setStatus(status, "ok", "已保存。");
      if (done) done();
    });
    wrap.append(btn, status);
    return wrap;
  }

  function renderTeam(page, share, me) {
    const team = (share && share.team) || [];
    const s = block("团队");
    if (!team.length) {
      s.appendChild(line("这个项目还没有成员名单：现在只有你一个人，改动直接生效。", "quiet"));
      page.appendChild(s);
      return;
    }
    const table = mk("table", "team-table");
    const thead = mk("thead");
    const hr = mk("tr");
    ["名字", "角色", "邮箱", "公钥指纹"].forEach((h) => hr.appendChild(mk("th", null, h)));
    thead.appendChild(hr);
    const tbody = mk("tbody");
    const myId = me && me.me ? me.me.id : null;
    team.forEach((t) => {
      const tr = mk("tr");
      const nameCell = mk("td", "team-name", t.name || t.id);
      if (t.id === myId) nameCell.appendChild(mk("span", "team-you", "你"));
      // Only the fingerprint, one long unbroken string, may break anywhere.
      tr.append(nameCell, mk("td", "team-role", ROLE_WORDS[t.role] || t.role), mk("td", "mono team-email", t.id),
        mk("td", "mono team-fp", t.fingerprint || "没有公钥"));
      tbody.appendChild(tr);
    });
    table.append(thead, tbody);
    s.appendChild(table);
    page.appendChild(s);
  }

  // A project of one person: how collaboration starts (12.1).
  function renderStart(page, share, me) {
    const s = block("开始协作");
    if (!(me && me.me)) {
      s.appendChild(line("先在上面填好「我是谁」。", "warn"));
      page.appendChild(s);
      return;
    }
    const status = mk("div", "notice-status");
    const hasTeam = (share.team || []).length > 0;
    if (!hasTeam) {
      s.appendChild(line(t("创建团队后，你是 PI。签名私钥仅保存在这台电脑上。"), "writes"));
      const btn = mkButton("建立成员名单", "btn btn-primary", async () => {
        btn.disabled = true;
        setStatus(status, "", "正在写入…");
        try {
          await apiPost("/api/team/init", {});
        } catch (err) {
          btn.disabled = false;
          failed(status, "没能建立成员名单", err);
          return;
        }
        await RODesk.refreshSidebar();
        after("members", [{ text: "已建立成员名单：你是 PI。下一步，选好公盘文件夹。" }]);
      });
      s.append(btn, status);
    }
    page.appendChild(s);
    if (hasTeam) renderShared(page, share);
  }

  // The PI's shared folder: choose it, its state, 发布 (12.2, 12.3).
  function renderShared(page, share) {
    const s = block("公盘");
    s.dataset.anchor = "publish"; // the shell's 文件 > 发布
    if (!share.shared || !share.reachable) {
      if (share.shared && !share.reachable) {
        const warn = mk("p", "team-line warn");
        warn.append("记着的公盘文件夹现在打不开：", mk("span", "mono", share.shared));
        s.appendChild(warn);
      }
      s.appendChild(line("选一个和团队共享的 OneDrive 文件夹（或别的同步文件夹）。它不能在项目文件夹里面。", "quiet"));
      const pf = pathField("公盘文件夹", "shared", "公盘文件夹的完整路径");
      s.appendChild(pf.wrap);
      s.appendChild(line(t("此设置仅保存在本机。"), "writes"));
      const status = mk("div", "notice-status");
      const btn = mkButton("设为公盘", "btn btn-primary", async () => {
        const path = pf.input.value.trim();
        if (!path) { setStatus(status, "err", "请先选择或填写公盘文件夹"); return; }
        btn.disabled = true;
        try {
          await apiPost("/api/share/set", { shared: path });
        } catch (err) {
          btn.disabled = false;
          failed(status, "没能设为公盘", err);
          return;
        }
        await RODesk.refreshSidebar();
        after("members", [{ text: "已设为公盘。下一步，发布第一版。" }]);
      });
      s.append(btn, status);
      page.appendChild(s);
      return;
    }
    const where = mk("p", "team-line");
    where.append("公盘文件夹：", mk("span", "mono", share.shared));
    s.appendChild(where);
    const last = share.last_snapshot;
    if (last) {
      const p = mk("p", "desk-head");
      p.append("最近发布：第 " + last.seq + " 版，" + when(last.at) + (last.note ? "，" + last.note : ""));
      if (share.verification === "verified") p.appendChild(label("签名已验证", "olive"));
      else if (share.verification) p.appendChild(label("未验证", "ochre"));
      s.appendChild(p);
    } else {
      s.appendChild(line("还没有发布过。成员要从公盘加入，先发布第一版。", "quiet"));
    }
    (share.warnings || []).forEach((w) => s.appendChild(line(w, "warn")));
    const pending = share.pending_publish || {};
    if ((pending.dataless || []).length) {
      s.appendChild(line("有 " + pending.dataless.length + " 个文件只在云端：先下载下来，才能发布。", "warn"));
    }
    if (pending.count) {
      const det = mk("details", "team-pending");
      det.appendChild(mk("summary", null, last ? "和第 " + last.seq + " 版相比有 " + pending.count + " 个文件不同"
        : "第一版会带上正式版本的 " + pending.count + " 个文件"));
      const ul = mk("ul", "desk-files");
      (pending.files || []).slice(0, 60).forEach((f) => ul.appendChild(mk("li", "mono", f)));
      det.appendChild(ul);
      s.appendChild(det);
    }
    if ((share.refused || []).length || (share.problems || []).length) {
      s.appendChild(line("公盘上有 " + ((share.refused || []).length + (share.problems || []).length) + " 处内容没有通过检查，没有读取。", "warn"));
    }
    const note = textInput("这一版的说明（可以不写）");
    s.appendChild(field("发布说明", note));
    s.appendChild(line(publishWrites(share), "writes"));
    s.appendChild(removalList(share.incoming));
    const status = mk("div", "notice-status");
    const btn = mkButton("发布", "btn btn-primary", async () => {
      btn.disabled = true;
      setStatus(status, "", "正在发布…");
      let res;
      try {
        res = await apiPost("/api/share/publish", note.value.trim() ? { note: note.value.trim() } : {});
      } catch (err) {
        btn.disabled = false;
        failed(status, "没能发布，公盘上没有写入新的版本", err);
        return;
      }
      note.value = "";
      await RODesk.refreshSidebar();
      markViewsStale(); // the records the packets brought are in every view now
      await after("members", [{ text: res.published === false ? "和上一版相比没有改动，没有写入新的版本。"
        : "已发布第 " + (res.seq != null ? res.seq : "") + " 版：成员同步以后就会看到。" }]);
    });
    s.append(btn, status);
    page.appendChild(s);
  }

  // The PI adds a member with the key they sent (12.5, 12.12).
  function renderAddMember(page, share) {
    const s = block("添加成员");
    s.appendChild(line("请对方先在自己的 Research OS 里生成公钥（「成员」一栏的「我的公钥」），把公钥文件或那一行文字和邮箱发给你。", "quiet"));
    const email = textInput("对方共享公盘用的邮箱", true);
    const name = textInput("名字");
    const pf = pathField("公钥文件", "key", "公钥文件的完整路径，例如 ~/Downloads/researchos-key-wang.pub");
    const paste = textArea(2, "或者把公钥那一行文字粘贴在这里（ssh-ed25519 开头）");
    s.append(field("邮箱", email), field("名字", name), pf.wrap, field("公钥文字", paste));
    const writes = line("", "writes");
    const say = () => {
      const id = email.value.trim() || "对方的邮箱";
      writes.textContent = "将在项目的 .researchos/team.toml 末尾追加这位成员（只追加，不改写），并在公盘上建好他的提议区「提议/" + id +
        "/」（只建一次）。" + (share.shared ? "" : "公盘还没有设置：提议区会在设置公盘后的下一次发布时建好。");
    };
    email.addEventListener("input", say);
    say();
    s.appendChild(writes);
    const status = mk("div", "notice-status");
    const btn = mkButton("添加成员", "btn btn-primary", async () => {
      if (!email.value.trim() || !name.value.trim()) { setStatus(status, "err", "请填写成员的邮箱和名字"); return; }
      const body = { id: email.value.trim(), name: name.value.trim() };
      if (paste.value.trim()) body.key = paste.value.trim();
      else if (pf.input.value.trim()) body.key_file = pf.input.value.trim();
      else { setStatus(status, "err", "请选择对方的公钥文件，或粘贴公钥文字"); return; }
      btn.disabled = true;
      setStatus(status, "", "正在写入…");
      let res;
      try {
        res = await apiPost("/api/team/add", body);
      } catch (err) {
        btn.disabled = false;
        failed(status, "没能把这位成员加入团队", err);
        return;
      }
      btn.disabled = false;
      email.value = ""; name.value = ""; paste.value = ""; pf.input.value = "";
      await RODesk.refreshSidebar();
      await after("members", [
        { text: "已加入团队：" + res.name + "（" + res.member + "），公钥指纹 " + (res.fingerprint || "") + "。" },
        { text: res.sentence || "", strong: true },
        { text: "然后发布一次：成员同步以后，就会在自己的工作副本里看到自己在团队里。" },
      ].filter((f) => f.text));
    });
    s.append(btn, status);
    page.appendChild(s);
  }

  // The link removals a publish would apply, each named (12.12: a member's
  // removal takes a link out of the official version), its relation in the
  // desk's words (RODesk.relationWord).
  function removalList(coming) {
    const wrap = mk("div", "team-removals");
    const list = (coming && coming.removals) || [];
    if (!list.length) return wrap;
    const det = mk("details", "team-pending");
    det.appendChild(mk("summary", null, t("会删除的连线（{n} 条）", { n: numberText(list.length) })));
    const ul = mk("ul", "desk-files");
    list.forEach((r) => {
      const li = mk("li");
      li.append(mk("span", "mono", r.from), " " + RODesk.relationWord(r.type) + " ", mk("span", "mono", r.to),
        mk("span", "team-help", t("删除人 {who}", { who: r.by || "" })));
      ul.appendChild(li);
    });
    det.appendChild(ul);
    wrap.appendChild(det);
    return wrap;
  }

  // A member's copy: the PI's key as trusted on joining, the shared
  // folder, 同步 with what it will write first (12.3, 12.5).
  function renderMemberShare(page, share) {
    const s = block("公盘与同步");
    const j = share.joined || {};
    const pi = mk("p", "team-line");
    pi.append("加入时记下的 PI 公钥指纹：", mk("span", "mono strong", j.pi_fingerprint || "（PI 没有公钥）"));
    s.appendChild(pi);
    s.appendChild(line("和 PI 当面或电话核对过这一串，就可以相信公盘上用它签名的每一版。" +
      (j.trusted_on_first_join ? "（" + when(j.trusted_on_first_join) + "记下）" : ""), "quiet"));
    if (share.two_folders) {
      const where = mk("p", "team-line");
      where.append("公盘的「已发布」文件夹：", mk("span", "mono", share.shared), share.reachable ? "" : "（现在打不开）");
      s.appendChild(where);
      const mine = mk("p", "team-line");
      if (share.mine) mine.append("你自己的提议文件夹：", mk("span", "mono", share.mine.path), share.mine.reachable ? "" : "（现在打不开）");
      else mine.append("你自己的提议文件夹还没有选：选好以后才能发送提议、备注和连线。");
      s.appendChild(mine);
    } else if (share.shared) {
      const where = mk("p", "team-line");
      where.append("公盘文件夹：", mk("span", "mono", share.shared), share.reachable ? "" : "（现在打不开）");
      s.appendChild(where);
    }
    s.appendChild(twoFolders(share));
    const last = share.last_snapshot;
    s.appendChild(line("你的工作副本基于第 " + j.seq + " 版" + (last && share.behind ? "；公盘上已经有第 " + last.seq + " 版" : "，已是最新") +
      (j.synced ? "。上次同步：" + when(j.synced) : "。"), ""));
    (share.warnings || []).forEach((w) => s.appendChild(line(w, "warn")));
    if ((share.refused || []).length) s.appendChild(line("公盘上有 " + share.refused.length + " 个版本没有通过签名验证，没有采用；采用的是最后一个通过验证的版本。", "warn"));
    if ((j.official_copies || []).length) {
      s.appendChild(mk("h3", "panel-h3", "放在旁边的正式版本"));
      s.appendChild(line("你的文件没有动；正式的一份放在它旁边。看完不同之处，自己决定留哪一份、怎么改。", "quiet"));
      const ul = mk("ul", "desk-files");
      const beside = j.beside || j.official_copies.map((c) => ({ copy: c }));
      beside.forEach((b) => {
        const li = mk("li", "beside-item");
        const head = mk("div", "desk-head");
        if (b.path) {
          head.append(mkButton(b.path, "desk-link mono", () => RONode.openNode(b.path)), " 旁边：");
        }
        head.appendChild(mkButton(b.copy, "desk-link mono", () => RONode.openNode(b.copy)));
        li.appendChild(head);
        if (b.diff || b.summary) li.appendChild(differenceBlock(b, false));
        else if (b.gone) li.appendChild(mk("p", "desk-note plain", "你自己的那一份已经不在了。"));
        ul.appendChild(li);
      });
      s.appendChild(ul);
    }
    const writes = line("正在查看同步会写入什么…", "writes");
    const status = mk("div", "notice-status");
    const btn = mkButton("同步", "btn btn-primary", async () => {
      btn.disabled = true;
      setStatus(status, "", "正在同步…");
      let res;
      try {
        res = await apiPost("/api/share/sync", {});
      } catch (err) {
        btn.disabled = false;
        failed(status, "没能同步，工作副本没有改动", err);
        return;
      }
      btn.disabled = false;
      markViewsStale(); // every view now shows the newer official version
      await RODesk.refreshSidebar();
      await after("members", syncFlash(res));
      if (typeof pollGeneration === "function") pollGeneration();
    });
    btn.disabled = true;
    s.append(writes, btn, status);
    page.appendChild(s);
    apiGet("/api/share/sync-preview").then((p) => {
      writes.textContent = syncWrites(p);
      btn.disabled = !!p.untrusted;
      if ((p.beside || []).length) {
        const ul = mk("ul", "desk-files");
        p.beside.forEach((b) => {
          const li = mk("li");
          li.append(mk("span", "mono", b.path), mk("span", "team-help", t("正式的一份会放在 {copy}", { copy: b.official_copy })));
          ul.appendChild(li);
        });
        writes.after(ul);
      }
    }).catch((err) => {
      failed(writes, "没能查看同步会写入什么", err);
      btn.disabled = false;
    });
  }

  // 12.13: on a personal OneDrive the PI shares 「已发布」 (view only) and
  // the member's own proposals folder (edit) separately, and they arrive as
  // two shortcuts: the member chooses each. Both are checked before use --
  // the published folder holds this project's signed snapshots, and the
  // member can write into their own folder.
  function twoFolders(share) {
    const det = mk("details", "team-pending");
    det.appendChild(mk("summary", null, share.two_folders ? "更改两个文件夹" : "PI 把两个文件夹分开共享给你？"));
    det.appendChild(line("在个人版 OneDrive 里，PI 分别共享「已发布」（只能查看）和你自己的提议文件夹（可以编辑），它们会在你的电脑上变成两个文件夹。分别选好：", "quiet"));
    const pub = pathField("公盘的「已发布」文件夹（PI 共享给你，只能查看）", "shared", "已发布文件夹的完整路径");
    const mine = pathField("你自己的提议文件夹（PI 共享给你，可以编辑）", "mine", "你的提议文件夹的完整路径");
    if (share.two_folders && share.shared) pub.input.value = share.shared;
    if (share.mine && share.mine.path) mine.input.value = share.mine.path;
    det.append(pub.wrap, mine.wrap);
    det.appendChild(line("使用前会检查：「已发布」里有这个项目经过 PI 签名的快照；你能写入自己的提议文件夹（在里面建一个空文件，马上删掉，不留下任何东西）。只记在这台电脑的设置里。", "writes"));
    const status = mk("div", "notice-status");
    const btn = mkButton("检查并使用这两个文件夹", "btn btn-primary", async () => {
      const p = pub.input.value.trim(), m = mine.input.value.trim();
      if (!p || !m) { setStatus(status, "err", "请选好两个文件夹"); return; }
      btn.disabled = true;
      setStatus(status, "", "正在检查…");
      let res;
      try {
        res = await apiPost("/api/share/set", { published: p, mine: m });
      } catch (err) {
        btn.disabled = false;
        failed(status, "没有使用这两个文件夹", err);
        return;
      }
      pub.input.value = ""; mine.input.value = "";
      await RODesk.refreshSidebar();
      await after("members", [{ text: "已检查并使用：「已发布」里有第 " + res.seq + " 版快照，签名已验证；你能写入自己的提议文件夹。" }]);
    });
    det.append(btn, status);
    return det;
  }

  // The PI: where each member's area is (12.13) -- under 提议/ on the
  // shared folder, or recorded elsewhere on this machine.
  function memberAreas(page, share) {
    const areas = share.areas || [];
    if (!areas.length) return;
    const s = block("成员的提议区");
    s.appendChild(line("每位成员的提议区默认在公盘的「提议/成员邮箱/」。如果某位成员的提议区在这台电脑的别处，可以在这里记下它的位置。", "quiet"));
    const ul = mk("ul", "desk-files");
    areas.forEach((a) => {
      const li = mk("li", "beside-item");
      const head = mk("div", "desk-head");
      head.append(mk("span", "strong", a.name || a.member), mk("span", "mono", a.area));
      if (!a.reachable) head.appendChild(label("现在打不开", "ochre"));
      li.appendChild(head);
      const det = mk("details", "team-pending");
      det.appendChild(mk("summary", null, "提议区在别处"));
      const pf = pathField("这位成员的提议区在这台电脑上的位置", "shared", "完整路径");
      const status = mk("div", "notice-status");
      const btn = mkButton("记下", "btn btn-small", async () => {
        const where = pf.input.value.trim();
        if (!where) { setStatus(status, "err", "请选择文件夹"); return; }
        btn.disabled = true;
        try {
          await apiPost("/api/share/set", { member: a.member, area: where });
        } catch (err) {
          btn.disabled = false;
          failed(status, "没有记下", err);
          return;
        }
        pf.input.value = "";
        await after("members", [{ text: "已记下" + (a.name || a.member) + "的提议区：" + where + "。" }]);
      });
      det.append(pf.wrap, line("只记在这台电脑的设置里（projects.json）；公盘和项目里都不写入任何东西。", "writes"), btn, status);
      li.appendChild(det);
      ul.appendChild(li);
    });
    s.appendChild(ul);
    page.appendChild(s);
  }

  // What 同步 did, counted from what it reports: `conflicts` are the
  // files with an .official copy beside them; `kept_yours` were left as
  // yours with nothing beside (the canvas, a file the official version
  // deleted); `notes_waiting` are record files whose entries have not
  // reached the official version yet.
  function syncDone(res) {
    const sent = res.entries_sent ? t("已把你的 {n} 条备注、连线确认或手工连线改动发送到公盘", { n: numberText(res.entries_sent) }) : "";
    if (res.up_to_date) return sent ? t("已经是最新的正式版本；{sent}。", { sent: sent }) : t("已经是最新的正式版本。");
    const parts = [t("已同步到第 {seq} 版", { seq: numberText(res.seq) })];
    if (sent) parts.push(sent);
    if ((res.updated || []).length) parts.push(t("更新了 {n} 个文件", { n: numberText(res.updated.length) }));
    if ((res.removed || []).length) parts.push(t("删除了 {n} 个正式版本已删除的文件", { n: numberText(res.removed.length) }));
    if ((res.conflicts || []).length) {
      parts.push(t("{n} 个两边都改过的文件留着你的，正式的一份放在旁边", { n: numberText(res.conflicts.length) }));
    }
    // The engine's reason, in the page's language (13.2).
    if ((res.kept_yours || []).length) {
      parts.push(t("{n} 个文件留着你的（{reason}）", { n: numberText(res.kept_yours.length),
        reason: res.kept_yours.map((k) => k.reason).filter(Boolean)[0] }));
    }
    if ((res.notes_waiting || []).length) parts.push(t("你的备注和连线还在等 PI 发布，记录文件暂时留着你的"));
    return t("{parts}。", { parts: parts.join(t("；")) });
  }

  // The flash lines of one sync: the sentence, then each .official copy
  // with its difference.
  function syncFlash(res) {
    const lists = [{ text: syncDone(res) }];
    (res.conflicts || []).forEach((c) => lists.push({
      text: t("正式版本的 {path} 放在了旁边：{copy}；你的没有动。", { path: c.path, copy: c.official_copy || "" }),
      diff: c.diff, truncated: c.truncated, summary: c.summary }));
    return lists;
  }

  // 我的公钥: what a member sends the PI (12.5).
  function renderKey(page, key, role) {
    const s = block("我的公钥");
    if (!key) { s.appendChild(line("没能读到这台电脑的公钥。", "warn")); page.appendChild(s); return; }
    if (!key.ssh_keygen) {
      s.appendChild(line("这台电脑没有 ssh-keygen：提议和快照无法签名，也无法验证，只能依靠公盘的共享权限。", "warn"));
      page.appendChild(s);
      return;
    }
    if (key.key) {
      const fp = mk("p", "team-line");
      fp.append("指纹：", mk("span", "mono strong", key.fingerprint || ""));
      s.appendChild(fp);
      if (role !== "pi") {
        s.appendChild(line("要加入别人的项目，把下面这一行和你的邮箱发给 PI：", "quiet"));
        const pre = mk("pre", "team-key mono", key.key);
        s.append(pre, copyButton(key.key));
      } else {
        s.appendChild(line("成员加入时会看到这一串，请他们和你核对。", "quiet"));
      }
    } else {
      s.appendChild(line("还没有签名密钥。将在这台电脑上生成一对，放在 " + String(key.path || "").replace(/\.pub$/, "") +
        "（私钥不离开这台电脑）；项目里不写入任何东西。", "writes"));
      const status = mk("div", "notice-status");
      const btn = mkButton("生成我的公钥", "btn btn-primary", async () => {
        btn.disabled = true;
        try {
          await apiPost("/api/me/key", {});
        } catch (err) {
          btn.disabled = false;
          failed(status, "没能生成公钥", err);
          return;
        }
        after("members", [{ text: "已生成你的公钥。" }]);
      });
      s.append(btn, status);
    }
    page.appendChild(s);
  }

  // -- 加入 (a member; 12.3, 12.5) -------------------------------------------------------
  // In the side panel, with or without a project open: who I am, my key,
  // then the shared folder and a new folder -- looked at first (what it
  // writes, the PI's key fingerprint to compare), joined only on the
  // button that says so.

  async function openJoinDialog() {
    if (window.ROWorkspace && !ROWorkspace.mayLeave()) return;
    if (typeof closeProjectMenu === "function") closeProjectMenu(false);
    const subject = { kind: "join" };
    view.joinSubject = subject;
    state.panelSubject = subject;
    panelTitle.textContent = "从公盘加入项目";
    panelTitle.title = "";
    panelSubtitle.textContent = t("选择公盘和本地保存位置");
    panelBody.innerHTML = '<p class="empty-note">载入中…</p>';
    openPanel();
    await drawJoin(subject);
  }

  function joinLive(subject) {
    return state.panelSubject === subject && !panel.classList.contains("hidden");
  }

  async function drawJoin(subject) {
    const [me, key] = await Promise.all([apiGet("/api/me").catch(() => null), apiGet("/api/me/key").catch(() => null)]);
    if (!joinLive(subject)) return;
    panelBody.innerHTML = "";
    // 1. who I am
    panelBody.appendChild(mk("h3", "panel-h3", "1. 我是谁"));
    if (me && me.me) {
      const p = mk("p", "team-me");
      p.append(mk("span", "team-me-name", me.me.name), mk("span", "mono", me.me.id));
      panelBody.appendChild(p);
    } else {
      panelBody.appendChild(meForm(me, () => { RODesk.refreshSidebar(); drawJoin(subject); }));
      return;
    }
    // 2. my key
    panelBody.appendChild(mk("h3", "panel-h3", "2. 我的公钥"));
    if (key && key.key) {
      panelBody.appendChild(line("把这一行和你的邮箱发给 PI，PI 用它把你加入团队：", "quiet"));
      panelBody.append(mk("pre", "team-key mono", key.key), copyButton(key.key));
    } else if (key && key.ssh_keygen) {
      panelBody.appendChild(line("将在这台电脑上生成一对签名密钥（私钥不离开这台电脑）。", "writes"));
      const status = mk("div", "notice-status");
      const btn = mkButton("生成我的公钥", "btn btn-small", async () => {
        btn.disabled = true;
        try {
          await apiPost("/api/me/key", {});
        } catch (err) {
          btn.disabled = false;
          failed(status, "没能生成公钥", err);
          return;
        }
        drawJoin(subject);
      });
      panelBody.append(btn, status);
    } else {
      panelBody.appendChild(line("这台电脑没有 ssh-keygen：你的提议无法签名，PI 那边会显示为未验证。", "warn"));
    }
    // 3. where
    panelBody.appendChild(mk("h3", "panel-h3", "3. 公盘和工作副本"));
    const shared = pathField("公盘文件夹，或 PI 共享给你的「已发布」文件夹", "shared", "公盘文件夹的完整路径");
    const mine = pathField("你自己的提议文件夹（PI 单独共享给你时才选；也可以以后在「成员」里选）", "mine", "可以不填");
    const folder = pathField("工作副本放在哪里（一个新的或空的文件夹）", "working", "例如 ~/Documents/某某项目_我的副本");
    panelBody.append(shared.wrap, mine.wrap, folder.wrap);
    const status = mk("div", "notice-status");
    const result = mk("div", "join-result");
    const look = mkButton("查看", "btn btn-primary", () => lookJoin(subject, shared.input.value.trim(), folder.input.value.trim(), status, result,
      mine.input.value.trim()));
    panelBody.append(look, status, result);
  }

  async function lookJoin(subject, shared, folder, status, result, mine) {
    result.innerHTML = "";
    if (!shared || !folder) { setStatus(status, "err", "请选择公盘文件夹和工作副本的位置"); return; }
    setStatus(status, "", "正在查看公盘…");
    let p;
    const where = { shared: shared, folder: folder };
    if (mine) where.mine = mine;
    try {
      p = await apiPost("/api/share/join-preview", where);
    } catch (err) {
      if (!joinLive(subject)) return;
      failed(status, "没能查看这个公盘文件夹", err);
      return;
    }
    if (!joinLive(subject)) return;
    setStatus(status, "", "");
    const box = mk("div", "draft");
    const head = mk("p", "team-line");
    head.append("项目由 ", mk("strong", null, p.pi_name || p.pi), " 发布；最新的是第 " + p.seq + " 版，" + when(p.at) + (p.note ? "：" + p.note : "。"));
    box.appendChild(head);
    const fp = mk("div", "join-fp");
    fp.append(mk("div", "team-help", "PI 公钥指纹"), mk("div", "mono strong", p.pi_fingerprint || "（PI 没有公钥：快照无法验证）"));
    box.appendChild(fp);
    box.appendChild(line("请和 PI 当面或电话核对这一串。一致再加入：以后只接受用这把钥匙签名的版本。", "strong"));
    if (p.role === "none") box.appendChild(line("你还不在团队名单里。可以先加入；PI 把你加入团队并发布以后，同步一次你就是成员了。", "quiet"));
    (p.warnings || []).forEach((w) => box.appendChild(line(w, "warn")));
    if ((p.refused || []).length) box.appendChild(line("公盘上有 " + p.refused.length + " 个版本没有通过签名验证，不会采用。", "warn"));
    if ((p.waiting || []).length) box.appendChild(line("有 " + p.waiting.length + " 个文件还在从云端下载：等它们下载完再加入。", "warn"));
    if (p.mine) box.appendChild(line("你自己的提议文件夹：" + p.mine + "（已检查：你能写入它）", "quiet"));
    else if (p.two_folders) {
      box.appendChild(line("还没有选你自己的提议文件夹：加入以后在「成员」里选好，才能发送提议。", "quiet"));
    }
    box.appendChild(line(joinWrites(p), "writes"));
    const st = mk("div", "notice-status");
    const btn = mkButton("核对无误，加入", "btn btn-primary", async () => {
      btn.disabled = true;
      setStatus(st, "", "正在写入工作副本…");
      let res;
      try {
        res = await apiPost("/api/share/join", Object.assign({}, where, { expect_pi: p.pi_fingerprint || "" }));
      } catch (err) {
        btn.disabled = false;
        failed(st, "没能加入，什么都没有写入", err);
        return;
      }
      setStatus(st, "ok", "已加入：工作副本在 " + res.working_copy + "，共 " + res.files + " 个文件。" +
        (res.opened === false ? "没能自动打开它，请在项目菜单里打开。" : "正在打开并扫描…"));
      if (res.opened !== false && typeof reloadAllViews === "function") {
        await reloadAllViews({ scanStarted: !!res.scanning });
        activateView("members");
      }
    });
    if ((p.waiting || []).length) btn.disabled = true;
    box.append(btn, st);
    result.appendChild(box);
  }

  window.ROTeam = Object.freeze({
    loadRoutes, loadProposals, loadMembers, openProposal, openEvidence, openJoinDialog, showAbandoned, showList,
    composeInto, mode, syncWrites, syncDone, syncFlash, differenceBlock, renderProposalInto,
    // pure, for tests/test_v8_u4.py
    _sizeWords: sizeWords, _stateLabel: stateLabel, _signatureWords: signatureWords, _approveSentence: approveSentence,
    _sendWrites: sendWrites, _carryWords: carryWords, _publishWrites: publishWrites, _syncWrites: syncWrites, _joinWrites: joinWrites,
    _packetWords: packetWords, _syncDone: syncDone,
    _reasonSource: reasonSource, _routeProposals: routeProposals, _parentNumbers: parentNumbers,
  });
})();
