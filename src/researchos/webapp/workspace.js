// The project starts with questions and a plan. Notes, results and optional
// tasks stay ordinary files in the project; writing here never runs code.
(function () {
  "use strict";
  function fillVars(s, v) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return v && Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }); }
  function t(s, v) { var I = typeof window !== "undefined" && window.ROI18N; return I ? I.t(s, v) : fillVars(s, v); }
  function el(tag, cls, words) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (words != null) node.textContent = words;
    return node;
  }
  // The helpers below take words that are already for the screen: every call passes t("...") (13.6).
  function button(words, action, cls) {
    const node = el("button", cls || "btn btn-small", words);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  }
  function field(words, input) {
    const label = el("label", "ws-field");
    label.append(el("span", "ws-field-label", words), input);
    return label;
  }
  function input(type, placeholder) {
    const node = el("input", "form-input");
    node.type = type || "text";
    if (placeholder) node.placeholder = placeholder;
    return node;
  }
  function status(node, words, kind) {
    if (!node) return;
    node.textContent = words || "";
    node.className = "ws-status" + (kind ? " " + kind : "");
  }
  function errorWords(words, err) {
    return words + (err && (err.engineMessage || err.messageZh || err.message) ? " " + (err.engineMessage || err.messageZh || err.message) : "");
  }

  // Keyboard focus survives a redraw (DESIGN.md 12.11): controls that are drawn again carry a data-key, and a text
  // control the person is typing in is never rebuilt while it has focus.
  function descendants(root) {
    const out = [];
    (function walk(node) { Array.from(node.children || []).forEach((child) => { out.push(child); walk(child); }); })(root);
    return out;
  }
  function within(root, node) { for (let n = node; n; n = n.parentNode) if (n === root) return true; return false; }
  function activeIn(root) {
    const node = typeof document !== "undefined" ? document.activeElement : null;
    return root && node && node !== root && within(root, node) ? node : null;
  }
  function isTyping(node) {
    const tag = node ? String(node.tagName || "").toLowerCase() : "";
    return tag === "textarea" || (tag === "input" && !["checkbox", "radio", "button", "submit"].includes(node.type));
  }
  function keyed(node, key) { node.dataset.key = key; return node; }
  function focusSnapshot(root) {
    const node = activeIn(root);
    return node && node.dataset && node.dataset.key ? { key: node.dataset.key, start: node.selectionStart, end: node.selectionEnd } : null;
  }
  function focusKey(root, key, snap) {
    const node = root && descendants(root).find((x) => x.dataset && x.dataset.key === key && !x.disabled);
    if (!node) return false;
    node.focus();
    try { if (snap && snap.start != null && typeof node.setSelectionRange === "function") node.setSelectionRange(snap.start, snap.end); } catch (err) { /* not a text control */ }
    return true;
  }
  function restoreFocus(root, snap) { return !!snap && focusKey(root, snap.key, snap); }
  // A control named by the task it belongs to: the same visible words, a longer accessible name (12.11).
  function named(node, words) { node.setAttribute("aria-label", words); return node; }

  let epoch = 0, request = 0, docRequest = 0, createRequest = 0;
  // Paths a document names that are not among the listed files (the list stops at the engine's limit), and what the
  // engine said of each: true when it is a file the document may link to (DESIGN.md 18.3).
  let knownCache = new Map(), unchecked = [];
  let host = null, data = null, draft = null, saved = null, ui = null;
  let taskDraft = blankDraft(), taskBusy = false, taskConflict = false, taskWrites = 0;
  let taskOpen = {}, taskDisclosures = {};
  let createSubject = null, folderSubject = null, foreignBlocked = false;
  // AI agents carry out tasks (DESIGN.md Section 17): what the page knows of this Mac's agents, the people
  // an owner can be, the project's runs and what is open in each task's row. The programs and every limit
  // on them are the engine's; the page asks, shows words and follows a run by polling.
  const confirmedAgents = new Set();   // owners the person has already accepted on this page load (17.1)
  let agentInfo = null, agentInfoError = null, agentInfoLoading = false;
  let people = { loaded: false, loading: false, me: null, team: [] };
  let runsInfo = { runs: [], active: null }, details = {}, detailPending = {};
  let agentUiState = {}, agentBusy = {}, agentNotes = {}, sections = {}, metaNodes = {}, wordsNodes = {}, rowControls = {}, rowNodes = {};
  // 任务输出 in a task's row (DESIGN.md 19.6): the place it is drawn in, the removal in progress and the sentence that
  // says it failed, each by task id; and what the person opened, which a redraw keeps (19.12 item 2): { process: the
  // 过程文件 group is open, all: { kind: true } for a group shown past its first files }.
  let outputNodes = {}, outputBusy = {}, outputNotes = {}, outputOpen = {};
  let offline = false;
  let settingsOpen = false, settingsBusy = false, settingsNote = null, settingsInputs = blankInputs(), modelsState = {};
  // 运行占用的空间 (DESIGN.md 19.8): what the engine says of the open project's run store, the sentence for a read or an
  // action that failed, the action in progress and the timer that asks again while the engine tidies an old store.
  let storeInfo = null, storeError = null, storeBusy = false, storeNote = null, storeTimer = null;
  let pollTimer = null, pollFails = 0;
  // The run poll rests while the window is hidden (DESIGN.md 18.4 item 2): a poll that was due is noted in `pollHeld`
  // and one poll is made when the window is shown again. `pollStopped` is 停止服务: the service is gone, for good.
  let pollHeld = false, pollStopped = false;
  function project() { return typeof state !== "undefined" ? state.currentPath || "" : ""; }
  function appEpoch() { return typeof state !== "undefined" ? state.dataEpoch : 0; }
  function stamp() { return { epoch: epoch, app: appEpoch(), project: project() }; }
  function live(mark) { return mark.epoch === epoch && mark.app === appEpoch() && mark.project === project(); }
  function editable() { return !!data && !foreignBlocked && !data.read_only && data.can_write !== false && ["single", "pi", "member"].includes(data.mode); }
  function tasksEditable() { return editable() && !data.tasks_error; }
  function documentDirty() { return !!draft && !!saved && (draft.title !== saved.title || draft.content !== saved.content || researchRecordDirty()); }
  function taskDirty() { return !!(taskDraft.id || taskDraft.text || taskDraft.owner || taskDraft.due || (taskDraft.contextReport && taskDraft.contextReport !== taskDraft.presetContext)); }
  function createDirty() {
    return !!createSubject && typeof state !== "undefined" && state.panelSubject === createSubject &&
      (createSubject.name.value || createSubject.parent.value) &&
      typeof panel !== "undefined" && !panel.classList.contains("hidden");
  }
  // Words typed into an open reply form and not yet sent (17.8) are unsaved writing too.
  function replyDirty() { return Object.keys(agentUiState).some((id) => !!agentUiState[id].reply && !!String(agentUiState[id].text).trim()); }
  function folderDirty() { return !!folderSubject && state.panelSubject === folderSubject && folderSubject.dirty; }
  function mayLeaveFolders() {
    if (!folderDirty()) return true;
    if (!window.confirm(t("有未保存的内容，仍要离开吗？"))) return false;
    folderSubject.dirty = false;
    return true;
  }
  function isDirty() { return documentDirty() || taskDirty() || replyDirty() || !!createDirty() || !!folderDirty(); }
  function mayLeave() { return !isDirty() || window.confirm(t("有未保存的内容，仍要离开吗？")); }
  // `attempt` and `after` (DESIGN.md 21.1) are choices, not writing: a draft that holds only them is not unsaved work, and
  // neither is the research context a handback chose for the task it sends on (`presetContext`, 21.2).
  function blankDraft(attempt, context) { return { id: null, text: "", owner: "", due: "", custom: false, contextReport: context || "", presetContext: context || "", attempt: attempt || "", after: "" }; }
  function blankInputs() { return { paths: {}, runner: "claude-code", model: "", effort: "", customModel: false, minutes: null, budget: null, storeLimit: null }; }
  function reset() {
    epoch += 1; request += 1; docRequest += 1; createRequest += 1;
    host = null; data = null; draft = null; saved = null; ui = null; createSubject = null; folderSubject = null;
    foreignBlocked = false; knownCache = new Map(); unchecked = [];
    taskDraft = blankDraft(); taskBusy = false; taskConflict = false; taskWrites = 0;
    taskOpen = {}; taskDisclosures = {};
    stopPolling(); offline = false;
    agentInfo = null; agentInfoError = null; agentInfoLoading = false;
    people = { loaded: false, loading: false, me: null, team: [] };
    runsInfo = { runs: [], active: null }; details = {}; detailPending = {};
    agentUiState = {}; agentBusy = {}; agentNotes = {}; sections = {}; metaNodes = {}; wordsNodes = {}; rowControls = {}; rowNodes = {};
    outputNodes = {}; outputBusy = {}; outputNotes = {}; outputOpen = {};
    settingsOpen = false; settingsBusy = false; settingsNote = null; settingsInputs = blankInputs(); modelsState = {};
    stopStoreTimer(); storeInfo = null; storeError = null; storeBusy = false; storeNote = null;
  }

  function kindLabel(kind) { return kind === "overview" ? t("研究问题与计划") : kind === "result" ? t("结果记录") : t("笔记", { n: 1 }); }
  function fallbackPath(kind) { return kind === "overview" ? "research/overview.md" : "research/" + (kind === "result" ? "results" : "notes") + "/draft.md"; }
  function relativePath(from, to) {
    const a = String(from).split("/").slice(0, -1), b = String(to).split("/");
    while (a.length && b.length && a[0] === b[0]) { a.shift(); b.shift(); }
    return a.map(() => "..").concat(b).join("/");
  }
  function encodePath(path) { return path.split("/").map((p) => encodeURIComponent(p).replace(/[()']/g, (c) => "%" + c.charCodeAt(0).toString(16))).join("/"); }
  function resolvePath(from, target) {
    if (/^(?:[a-z][a-z0-9+.-]*:|[\\/])/i.test(target)) return null;
    let decoded;
    try { decoded = decodeURIComponent(target); } catch (err) { return null; }
    if (/[\\\x00-\x1f]/.test(decoded) || decoded.startsWith("/")) return null;
    const parts = from.split("/").slice(0, -1);
    for (const part of decoded.split("/")) {
      if (!part || part === ".") continue;
      if (part === "..") { if (!parts.length) return null; parts.pop(); }
      else parts.push(part);
    }
    return parts.join("/");
  }
  function markdownLink(from, target, image) {
    const name = target.split("/").pop().replace(/[[\]\\]/g, "\\$&");
    return (image ? "!" : "") + "[" + name + "](" + encodePath(relativePath(from, target)) + ")";
  }
  // A project file a document may link to: one of the listed files, or, when the list was cut at the engine's limit, one
  // the engine has said exists (a link to an existing file beyond the first 500 is still a link, DESIGN.md 18.3).
  function listedFile(path) { return !!data && (data.files || []).some((file) => file.path === path); }
  function knownFile(path) { return listedFile(path) || knownCache.get(path) === true; }
  // A path the render could not place, to be asked about once; only when the list is not the whole project.
  function wantLookup(path) {
    if (data && data.files_truncated && !knownCache.has(path) && !unchecked.includes(path)) unchecked.push(path);
  }
  async function lookUpFiles(paths) {
    const mark = stamp(), asked = paths.filter((p) => p.length <= 500).slice(0, 100);   // one short request
    if (!asked.length) return false;
    try {
      const got = await apiGet("/api/workspace/exists?project=" + encodeURIComponent(project()) + asked.map((p) => "&path=" + encodeURIComponent(p)).join(""));
      if (!live(mark)) return false;
      const found = new Set(got.exists || []);
      asked.forEach((p) => knownCache.set(p, found.has(p)));
      return found.size > 0;
    } catch (err) {
      return false;   // not an answer: nothing is remembered and the link stays text until it is asked again
    }
  }
  // The project files the document links to, in the order it names them, the document itself first.
  async function referencedFiles() {
    const own = draft.path, named = [], links = /!?\[(?:\\.|[^\]\\\n])*\]\(([^)\n]+)\)/g;
    let match;
    while ((match = links.exec(draft.content))) {
      const path = resolvePath(own, match[1]);
      if (path && path !== own && !named.includes(path)) named.push(path);
    }
    if (data.files_truncated) await lookUpFiles(named.filter((path) => !listedFile(path) && !knownCache.has(path)));
    return [own].concat(named.filter(knownFile), draft.researchRecord.path || []).filter((path, index, all) => path && all.indexOf(path) === index);
  }

  function projectChangedElsewhere() {
    if (!data || foreignBlocked || !ui) return;
    foreignBlocked = true;
    const banner = el("div", "ws-readonly ws-retargeted");
    const feedback = el("p", "ws-status", t("项目已在其他窗口切换，当前草稿已保留。"));
    const reopen = button(t("重新打开这个项目"), async () => {
      reopen.disabled = true;
      try {
        await apiPost("/api/projects/switch", { path: data.project, id: data.id });
        foreignBlocked = false; banner.remove();
        if (typeof loadProjects === "function") await loadProjects();
        if (typeof syncGeneration === "function") await syncGeneration();
        renderEditor(); renderTasks();
        ui.note.disabled = ui.result.disabled = !editable();
        await load(host, true);
      } catch (err) {
        foreignBlocked = true; reopen.disabled = false;
        status(feedback, errorWords(t("没能重新打开项目，草稿仍保留在这里。"), err), "err");
      }
    });
    banner.append(feedback, reopen); host.prepend(banner);
    ui.save.disabled = true; ui.note.disabled = ui.result.disabled = true;
    if (ui.researchRecord) descendants(ui.researchRecord).forEach((node) => { if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(node.tagName)) node.disabled = true; });
    renderTasks();
  }

  // A link to a project file that opens it in the file panel.
  function fileLink(local, label) {
    const a = el("a", "ws-file-link");
    a.append(label);
    a.href = "/api/file?path=" + encodeURIComponent(local);
    a.addEventListener("click", (event) => {
      event.preventDefault();
      if (typeof openFilePanel === "function") openFilePanel(local);
    });
    return a;
  }
  // The project-relative file path an inline code span is, or null (DESIGN.md 19.6): a plain path of visible names,
  // without a leading slash, a backslash, a control character, or a `.` or `..` step.
  function codePath(text) {
    if (!text || text.length > 500 || text.startsWith("/") || /[\\\x00-\x1f]/.test(text)) return null;
    return text.split("/").every((part) => part && part !== "." && part !== "..") ? text : null;
  }

  // Deliberately small Markdown reader: DOM nodes only. User HTML remains text;
  // project images and links are resolved within the project and served locally. A record names its files from the
  // project root as often as from its own folder (DESIGN.md 19.6): an inline code span that is the whole path of a
  // file the project has is a link to it, and a link that points at no file from the document's folder but at one from
  // the project root opens that one. Both ask the engine about the paths the file list cannot place only once (the
  // same `wantLookup` and `knownCache` as every link), and nothing is ever written to the document.
  function inline(parent, text, from) {
    const pattern = /(!?\[((?:\\.|[^\]\\\n])*)\]\(([^)\n]+)\)|`([^`\n]+)`|\*\*([^*\n]+)\*\*)/g;
    let start = 0, match;
    while ((match = pattern.exec(text))) {
      parent.append(text.slice(start, match.index));
      if (match[4]) {
        const path = codePath(match[4]), code = el("code", null, match[4]), known = !!path && knownFile(path);
        if (path && !known && /[./]/.test(path)) wantLookup(path);
        parent.append(known ? fileLink(path, code) : code);
      } else if (match[5]) parent.append(el("strong", null, match[5]));
      else {
        const image = match[1].startsWith("!"), label = match[2].replace(/\\([\[\]\\])/g, "$1"), target = match[3];
        let local = resolvePath(from, target), known = !!local && knownFile(local);
        if (local && !known) {
          const rooted = resolvePath("", target);
          if (rooted && rooted !== local && knownFile(rooted)) { local = rooted; known = true; }
          else { wantLookup(local); if (rooted && rooted !== local) wantLookup(rooted); }
        }
        if (image && known && /\.(png|jpe?g|gif|webp)$/i.test(local)) {
          const img = el("img", "ws-image");
          img.src = "/api/workspace/asset?path=" + encodeURIComponent(local);
          img.alt = label; img.loading = "lazy";
          parent.append(img);
        } else if (known) {
          parent.append(fileLink(local, label || local));
        } else if (/^https?:\/\//i.test(target)) {
          const a = el("a", null, label || target);
          a.href = target; a.target = "_blank"; a.rel = "noopener noreferrer";
          parent.append(a);
        } else if (/^researchos:attempt\//.test(target)) {
          const a = el("a", "ws-file-link", label);
          a.href = "#";
          a.addEventListener("click", (event) => {
            event.preventDefault();
            let id; try { id = decodeURIComponent(target.slice("researchos:attempt/".length)); } catch (err) { return; }
            if (window.RONode && typeof window.RONode.openNode === "function") window.RONode.openNode(id);
          });
          parent.append(a);
        } else parent.append(match[0]);
      }
      start = pattern.lastIndex;
    }
    parent.append(text.slice(start));
  }
  function renderMarkdown(node, content, from) {
    node.replaceChildren(); unchecked = [];
    const lines = String(content || "").replace(/\r\n?/g, "\n").split("\n");
    let code = null, list = null, table = null;
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        if (code !== null) { node.append(el("pre", "ws-code", code.join("\n"))); code = null; }
        else { code = []; list = null; table = null; }
        continue;
      }
      if (code !== null) { code.push(line); continue; }
      if (!line.trim()) { list = null; table = null; continue; }
      let m;
      if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
        table = el("table", "ws-table");
        const row = el("tr");
        line.replace(/^\s*\||\|\s*$/g, "").split("|").forEach((cell) => { const th = el("th"); inline(th, cell.trim(), from); row.append(th); });
        table.append(row); node.append(table); i += 1; list = null; continue;
      }
      if (table && line.includes("|")) {
        const row = el("tr");
        line.replace(/^\s*\||\|\s*$/g, "").split("|").forEach((cell) => { const td = el("td"); inline(td, cell.trim(), from); row.append(td); });
        table.append(row); continue;
      }
      table = null;
      if ((m = /^(#{1,6})\s+(.+)$/.exec(line))) {
        const h = el("h" + m[1].length); inline(h, m[2], from); node.append(h); list = null;
      } else if ((m = /^\s*(?:[-*]|\d+\.)\s+(.+)$/.exec(line))) {
        const ordered = /^\s*\d/.test(line);
        if (!list || list.tagName.toLowerCase() !== (ordered ? "ol" : "ul")) { list = el(ordered ? "ol" : "ul"); node.append(list); }
        const li = el("li"); inline(li, m[1], from); list.append(li);
      } else {
        const quote = /^>\s?/.test(line), p = el(quote ? "blockquote" : "p");
        inline(p, quote ? line.replace(/^>\s?/, "") : line, from); node.append(p); list = null;
      }
    }
    if (code !== null) node.append(el("pre", "ws-code", code.join("\n")));
    if (!node.childElementCount) node.append(el("p", "ws-empty", t("这里还没有内容。")));
  }

  function buildPage() {
    host.replaceChildren();
    const page = el("div", "ws-page"), top = el("div", "ws-heading"), rail = el("nav", "ws-documents");
    rail.setAttribute("aria-label", t("方案、笔记与结果"));
    const heading = el("div");
    heading.append(el("h2", "ws-project-name", data.project_name || data.name || (typeof state !== "undefined" && state.projectLabel) || t("项目首页")));
    const actions = el("div", "ws-actions");
    const note = button(t("新建笔记"), () => newDocument("note"));
    const result = button(t("记录结果"), () => newDocument("result"));
    note.disabled = result.disabled = !editable();
    actions.append(note, result); top.append(heading, actions);
    const columns = el("div", "ws-columns"), editor = el("section", "ws-editor-host"), tasks = el("section", "ws-tasks");
    const notices = el("div", "ws-notices");
    columns.append(rail, editor); page.append(notices, top, columns, tasks);
    host.append(page); ui = { rail: rail, editor: editor, tasks: tasks, note: note, result: result, notices: notices };
    renderNotices();
  }
  // What the page has to say about the project as a whole, drawn again with every read of it: why it cannot be
  // written (a Mac that is not a member of a team project says so, DESIGN.md 18.3), a project identity that cannot be
  // read right now (the engine's sentence, in the screen's language), and documents that cannot be read.
  function renderNotices() {
    if (!ui || !ui.notices) return;
    const box = ui.notices; box.replaceChildren();
    if (data.mode === "none") box.append(el("p", "ws-readonly", t("这台 Mac 不是这个项目的成员，只能查看")));
    else if (data.read_only) box.append(el("p", "ws-readonly", t("项目以只读方式打开。可以阅读，暂时不能保存。")));
    if (data.identity_error) box.append(el("p", "ws-status err", data.identity_error.message));
    if ((data.errors || []).length) box.append(el("p", "ws-status err", t("部分文档暂时无法读取，其他内容仍可使用。")));
  }
  // Notes and result records by their last change, newest first (DESIGN.md 18.3); the engine's order stands for
  // documents that changed together or whose time is not known.
  function changeOrder(documents) {
    const when = (doc) => { const n = Date.parse(doc.modified || ""); return isNaN(n) ? -Infinity : n; };
    return documents.map((doc, index) => [doc, index]).sort((a, b) => (when(b[0]) - when(a[0])) || (a[1] - b[1])).map((pair) => pair[0]);
  }
  function renderNav() {
    if (!ui) return;
    ui.rail.replaceChildren();
    ["overview", "note", "result"].forEach((kind) => {
      const group = el("div", "ws-document-group");
      group.append(el("h3", "ws-group-title", kind === "note" ? t("笔记", { n: 2 }) : kindLabel(kind)));
      const documents = changeOrder((data.documents || []).filter((d) => d.kind === kind));
      documents.forEach((doc) => {
        const b = button(doc.title || kindLabel(kind), () => openDocument(doc.path), "ws-document");
        // Authored titles are never translated as UI strings.
        b.textContent = doc.title || kindLabel(kind);
        b.setAttribute("aria-current", draft && draft.path === doc.path ? "page" : "false");
        if (doc.excerpt) b.title = doc.excerpt;
        if (doc.error) { b.append(el("span", "ws-unavailable", t("暂时无法读取"))); b.title = doc.error.message || ""; }
        group.append(b);
      });
      if (!documents.length) group.append(el("p", "ws-empty", kind === "overview" ? t("还没有研究方案。") : kind === "note" ? t("还没有笔记。") : t("还没有结果记录。")));
      ui.rail.append(group);
    });
  }
  // The officialness words of a document: the engine's answer for it, or the list's.
  function officialnessOf(doc) {
    const listed = data && (data.documents || []).find((item) => item.path === doc.path);
    return doc.officialness || (listed && listed.officialness) || null;
  }
  // The word beside the kind, drawn on its own: the same text can be read again under another word (提议中 once the
  // proposal is sent, 已批准，待同步 once approved, none once synced), and what is typed must not be redrawn for it.
  function drawOfficialness() {
    if (!ui || !ui.kindLabel || !draft) return;
    const old = ui.kindLabel.querySelector(".ws-officialness");
    if (old) old.remove();
    if (draft.officialness && draft.officialness.label) ui.kindLabel.append(el("span", "ws-officialness", draft.officialness.label));
  }
  function setDraft(doc, previewing) {
    draft = { path: doc.path || null, kind: doc.kind || "note", title: doc.title || "", content: doc.content || "", revision: doc.revision == null ? null : doc.revision, officialness: officialnessOf(doc), researchRecord: doc.research_record || {}, researchContext: emptyResearchContext(doc.research_record && doc.research_record.context), researchOpen: doc.kind === "result" && doc.revision != null };
    saved = { title: draft.title, content: draft.content, researchContext: emptyResearchContext(draft.researchContext) };
    renderEditor(previewing == null ? draft.revision != null || !editable() : previewing); renderNav();
  }
  async function openDocument(path, quiet, discard) {
    if (!quiet && !discard && documentDirty() && !window.confirm(t("当前文档还没有保存。放弃这些修改并打开另一份吗？"))) return;
    const token = ++docRequest, mark = stamp();
    const leaving = draft, leavingTitle = draft && draft.title, leavingContent = draft && draft.content, leavingResearch = draft && JSON.stringify(draft.researchContext);
    if (!quiet) status(ui && ui.docStatus, t("正在读取…"));
    try {
      const doc = await apiGet("/api/workspace/document?path=" + encodeURIComponent(path) + "&project=" + encodeURIComponent(project()));
      if (doc.kind === "result") {
        try { doc.research_record = await apiGet("/api/workspace/research-record?report=" + encodeURIComponent(path) + "&project=" + encodeURIComponent(mark.project)); }
        catch (err) { doc.research_record = { error: errorWords(t("没能读取研究记录。"), err) }; }
      }
      if (!live(mark) || token !== docRequest || (doc.project && doc.project !== mark.project)) return;
      if (draft === leaving && draft && (draft.title !== leavingTitle || draft.content !== leavingContent || JSON.stringify(draft.researchContext) !== leavingResearch)) {
        status(ui.docStatus, t("读取期间有新的输入，已保留当前文档。请再次选择要打开的文档。")); return;
      }
      if (quiet && !discard && documentDirty()) return;
      if (quiet && draft && doc.path === draft.path && doc.revision === draft.revision) {
        draft.officialness = officialnessOf(doc); // the same text: only its word may have moved
        drawOfficialness();
        if (doc.kind === "result" && !researchRecordDirty()) {
          draft.researchRecord = doc.research_record || {}; draft.researchContext = emptyResearchContext(draft.researchRecord.context);
          saved.researchContext = emptyResearchContext(draft.researchContext); redrawResearchRecord();
        }
        return;
      }
      const previewing = quiet && draft && doc.path === draft.path ? ui.previewing : undefined;
      setDraft(doc, previewing);
    } catch (err) {
      if (!live(mark) || token !== docRequest) return;
      if (!draft && ui) { ui.editor.replaceChildren(el("p", "ws-status err", errorWords(t("没能读取文档。"), err)), button(t("重试"), () => openDocument(path))); }
      else status(ui && ui.docStatus, errorWords(t("没能读取文档。"), err), "err");
    }
  }
  function newDocument(kind) {
    if (!editable()) return;
    if (documentDirty() && !window.confirm(t("当前文档还没有保存。放弃这些修改并新建吗？"))) return;
    docRequest += 1;
    setDraft({ kind: kind, title: "", content: "", revision: null });
    ui.title.focus();
  }
  // The text the engine keeps for a title and a writing area (workspace.save_document, DESIGN.md 18.3): the title is
  // written into the text only when it is a title the person typed, one different from the one the file showed
  // (`shown`; a document not written yet shows none, but for the overview's own default). A new document gets it as
  // its first line, an existing one in place of its first heading, or above the text when it has none.
  function headedContent(title, text, isNew, shown) {
    title = title.trim();
    if (!title || title === shown) return text;
    if (isNew) return text.split(/\r?\n/)[0] === "# " + title ? text : "# " + title + "\n\n" + text;
    const heading = /^#{1,6}[ \t]+.+$/m;
    return heading.test(text) ? text.replace(heading, () => "# " + title) : "# " + title + "\n\n" + text;
  }
  // Show the same heading that saving will keep, including edits made in the body.
  function previewContent() { return headedContent(draft.title, draft.content, draft.revision == null, saved.title); }
  // The preview is drawn at once with the files the page knows; links to files it cannot place are asked about once,
  // and it is drawn again if the engine knows some of them (a file beyond the listed ones, DESIGN.md 18.3).
  function renderPreview() {
    const node = ui.preview, from = draft.path || fallbackPath(draft.kind);
    renderMarkdown(node, previewContent(), from);
    const asked = unchecked.slice();
    if (!asked.length) return;
    lookUpFiles(asked).then((found) => {
      if (found && ui && ui.preview === node && ui.previewing) renderMarkdown(node, previewContent(), draft.path || fallbackPath(draft.kind));
    });
  }
  function renderEditor(previewing) {
    ui.previewing = !editable() || (previewing == null ? !!ui.previewing : previewing);
    const box = ui.editor; box.replaceChildren();
    const header = el("div", "ws-editor-header"), label = el("span", "ws-kind", kindLabel(draft.kind));
    const tools = el("div", "ws-actions"), save = button(t("保存"), saveDocument, "btn btn-primary btn-small");
    save.disabled = !editable();
    const previewToggle = button("", () => {
      ui.previewing = !ui.previewing;
      showMode();
      if (!ui.previewing) ui.text.focus();
    });
    previewToggle.disabled = !editable();
    previewToggle.classList.toggle("hidden", !editable());
    ui.kindLabel = label; drawOfficialness();
    tools.append(previewToggle, save); header.append(label, tools);
    const title = input("text", t("标题")); title.value = draft.title; title.disabled = !editable();
    title.className = "ws-title-input"; title.setAttribute("aria-label", t("标题"));
    const text = el("textarea", "ws-writing"); text.value = draft.content; text.disabled = !editable(); text.spellcheck = false;
    text.placeholder = draft.kind === "overview" ? t("研究问题、思路与计划") : draft.kind === "result" ? t("结果、解释与待确认的问题") : t("写笔记…");
    text.setAttribute("aria-label", kindLabel(draft.kind));
    const preview = el("div", "ws-preview hidden"), docStatus = el("div", "ws-status"); docStatus.setAttribute("role", "status");
    ui.title = title; ui.text = text; ui.preview = preview; ui.save = save; ui.docStatus = docStatus;
    ui.composing = false; ui.afterComposing = null;
    text.addEventListener("compositionstart", () => { ui.composing = true; });
    text.addEventListener("compositionend", () => {
      ui.composing = false;
      const later = ui.afterComposing; ui.afterComposing = null;
      if (later) setTimeout(later, 0);   // after the input method's last input event has been taken
    });
    function changed() { draft.title = title.value; draft.content = text.value; status(docStatus, documentDirty() ? t("尚未保存") : ""); }
    title.addEventListener("input", changed); text.addEventListener("input", changed);
    text.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") { event.preventDefault(); if (!save.disabled) saveDocument(); } });
    box.append(header, title, attachmentTools(), text, preview, docStatus);
    if (draft.kind === "result") box.append(researchRecordSection());
    const footer = el("div", "ws-document-footer");
    if (draft.path) footer.append(button(t("查看文件"), () => { if (typeof openFilePanel === "function") openFilePanel(draft.path); }));
    if (draft.path) footer.append(button(t("修改历史"), () => { if (window.RONode) window.RONode.openNode(draft.path); }));
    if (data.mode === "member") {
      const propose = button(t("提交修改"), async () => {
        if (documentDirty()) { status(docStatus, t("请先保存，再提交修改。")); return; }
        const files = await referencedFiles();
        if (typeof window.ROWorkspace.onPropose === "function") window.ROWorkspace.onPropose(files);
      });
      propose.disabled = !editable() || !draft.path; footer.append(propose);
      footer.append(el("p", "ws-empty", t("保存到自己的工作副本；提交并经 PI 批准后进入正式版本。")));
    }
    // The PI changes the official version directly, so a note or a result record is already theirs to publish: it goes
    // to the members with the next 发布 (DESIGN.md 18.3).
    if (data.mode === "pi" && (draft.kind === "note" || draft.kind === "result")) footer.append(el("p", "ws-empty", t("笔记和结果记录会随下一次发布交给成员。")));
    box.append(footer);
    function showMode() {
      title.classList.toggle("hidden", ui.previewing);
      text.classList.toggle("hidden", ui.previewing);
      preview.classList.toggle("hidden", !ui.previewing);
      save.classList.toggle("hidden", ui.previewing);
      ui.attachments.classList.toggle("hidden", ui.previewing);
      previewToggle.textContent = ui.previewing ? t("编辑") : t("预览");
      if (ui.previewing) renderPreview();
      if (draft.kind === "result") redrawResearchRecord();
    }
    showMode();
  }
  function emptyResearchContext(value) {
    const context = Object.assign({ question: "", method: "", finding: "", limits: "", next_step: "", attempts: [], variables: [], references: [], materials: [], purposes: {} }, value || {});
    ["attempts", "variables", "references", "materials"].forEach((key) => { context[key] = (context[key] || []).map((item) => typeof item === "object" ? Object.assign({}, item) : item); });
    context.purposes = Object.assign({}, context.purposes);
    if (context.attempt_draft) context.attempt_draft = Object.assign({}, context.attempt_draft, { parents: (context.attempt_draft.parents || []).slice() });
    return context;
  }
  function researchRecordDirty() { return !!draft && !!saved && JSON.stringify(draft.researchContext) !== JSON.stringify(saved.researchContext); }
  function researchRecordSection() {
    const box = el("details", "ws-research-record"); box.open = !!draft.researchOpen;
    box.append(disclosureSummary(t("研究记录")));
    box.addEventListener("toggle", () => { draft.researchOpen = box.open; });
    const context = draft.researchContext;
    if (draft.researchRecord.error) { box.append(el("p", "ws-status err", draft.researchRecord.error)); ui.researchRecord = box; return box; }
    (draft.researchRecord.notices || []).forEach((notice) => {
      const english = typeof uiLang === "function" && uiLang() === "en";
      const words = notice.kind === "older_version" && notice.id ? t("变量 {card}：引用第 {expected} 版，当前确认第 {current} 版", { card: notice.id, expected: notice.version, current: notice.current }) : (!english && notice.message_zh ? notice.message_zh : notice.message);
      if (words) box.append(el("p", "ws-status err", words));
    });
    function changed() { status(ui.docStatus, documentDirty() ? t("尚未保存") : ""); const save = box.querySelector(".ws-record-save"); if (save) save.disabled = !draft.path || draft.researchBusy || !researchRecordDirty(); }
    const fields = el("div", "ws-research-fields");
    [["question", t("研究问题")], ["method", t("方法与比较")], ["finding", t("发现与解释")], ["limits", t("局限与待确认")], ["next_step", t("下一步")]].forEach(([key, label]) => {
      if (ui.previewing) {
        if (context[key]) { const item = el("div", "ws-research-value"); item.append(el("h3", null, label), el("p", null, context[key])); fields.append(item); }
      } else {
        const area = keyed(el("textarea", "form-input ws-research-input"), "research:" + key); area.value = context[key] || ""; area.rows = 2; area.disabled = !editable();
        area.addEventListener("input", () => { context[key] = area.value; changed(); }); fields.append(field(label, area));
      }
    });
    box.append(fields);
    const links = el("div", "ws-research-links"); box.append(links);
    function linkedGroup(key, label, choices, open) {
      const valueKey = (item) => typeof item === "object" ? item.id + "@" + item.version : item;
      const group = el("div", "ws-research-group"); group.append(el("h3", null, label));
      (context[key] || []).forEach((value) => {
        const row = el("div", "ws-research-link"), choice = choices.find((item) => item.id === valueKey(value));
        const view = button(choice ? choice.label : valueKey(value), () => open(value), "btn btn-small ws-file-link"); row.append(view);
        if (!ui.previewing && editable()) row.append(named(button(t("移除"), () => { context[key] = context[key].filter((item) => valueKey(item) !== valueKey(value)); if ((key === "references" || key === "materials") && !context.references.includes(value) && !context.materials.includes(value)) delete context.purposes[value]; changed(); redrawResearchRecord(); }), t("移除：{task}", { task: choice ? choice.label : valueKey(value) })));
        if (key === "references" || key === "materials") {
          if (ui.previewing) { if (context.purposes[value]) row.append(el("span", "ws-research-purpose", context.purposes[value])); }
          else {
            const purpose = keyed(input("text", t("用途或关联意义（可选）")), "research-purpose:" + value); purpose.value = context.purposes[value] || "";
            purpose.setAttribute("aria-label", t("用途：{path}", { path: value })); purpose.addEventListener("input", () => { context.purposes[value] = purpose.value; changed(); }); row.append(purpose);
          }
        }
        group.append(row);
      });
      if (!ui.previewing && editable()) {
        const row = el("div", "ws-attachment-row"), select = keyed(el("select", "form-input"), "research-link:" + key);
        select.setAttribute("aria-label", label); const blank = el("option", null, t("选择…")); blank.value = ""; select.append(blank);
        choices.filter((item) => !context[key].some((value) => valueKey(value) === item.id)).forEach((item) => { const option = el("option", null, item.label); option.value = item.id; select.append(option); });
        const add = button(t("加入关联"), () => { if (select.value && !context[key].some((value) => valueKey(value) === select.value)) { context[key].push(key === "variables" ? choices.find((item) => item.id === select.value).value : select.value); changed(); redrawResearchRecord(); } });
        add.disabled = choices.every((item) => context[key].some((value) => valueKey(value) === item.id)); row.append(select, add); group.append(row);
      }
      if (group.children.length > 1) links.append(group);
    }
    linkedGroup("attempts", t("研究尝试"), (data.attempts || []).map((item) => ({ id: item.id, label: item.label || item.id })), (id) => { if (window.RONode) window.RONode.openNode(id); });
    linkedGroup("variables", t("变量定义"), ((draft.researchRecord.choices || {}).variables || []).map((item) => ({ id: item.id + "@" + item.version, label: item.label && item.label.endsWith(" v" + item.version) ? item.label : (item.label || item.id) + " · v" + item.version, value: { id: item.id, version: item.version } })), (value) => { if (typeof window.ROWorkspace.onOpenVariable === "function" && mayLeave()) window.ROWorkspace.onOpenVariable(value.id); });
    linkedGroup("materials", t("研究材料"), (data.files || []).map((item) => ({ id: item.path, label: item.path })), (path) => { if (typeof openFilePanel === "function") openFilePanel(path); });
    linkedGroup("references", t("文献文件"), (data.files || []).map((item) => ({ id: item.path, label: item.path })), (path) => { if (typeof openFilePanel === "function") openFilePanel(path); });
    const outputs = draft.researchRecord.outputs || [], variables = draft.researchRecord.variable_drafts || [];
    if (outputs.length || variables.length) {
      const materials = el("div", "ws-research-group"); materials.append(el("h3", null, t("相关输出")));
      const paths = new Set(); outputs.concat(variables).forEach((item) => {
        if (!item.path || paths.has(item.path)) return; paths.add(item.path);
        const row = el("div", "ws-research-link"); row.append(button(item.path, () => { if (typeof openFilePanel === "function") openFilePanel(item.path); }, "btn btn-small ws-file-link"));
        if (!item.variable_card && variables.some((value) => value.path === item.path) && editable()) {
          const cardId = keyed(input("text", t("变量 ID")), "research-import:" + item.path); cardId.setAttribute("aria-label", t("变量 ID"));
          const feedback = el("span", "ws-status");
          const importButton = button(t("导入为变量草稿"), async () => {
            if (!editable()) return;
            if (!cardId.value.trim()) { cardId.focus(); return; }
            const mark = stamp(), target = draft; importButton.disabled = true;
            try {
              const response = await apiPost("/api/workspace/research-record/variable", { project: mark.project, report: draft.path, path: item.path, id: cardId.value.trim() });
              if (!live(mark) || draft !== target) return;
              const id = response.id || cardId.value.trim(), version = response.version;
              (draft.researchRecord.outputs || []).concat(draft.researchRecord.variable_drafts || []).filter((value) => value.path === item.path).forEach((value) => { value.variable_card = id; value.draft = version; value.in_use = response.in_use == null ? null : response.in_use; });
              if (Number.isInteger(version) && version > 0) {
                if (!context.variables.some((value) => value.id === id && value.version === version)) context.variables.push({ id, version });
                const choices = draft.researchRecord.choices || (draft.researchRecord.choices = {});
                choices.variables = choices.variables || [];
                if (!choices.variables.some((value) => value.id === id && value.version === version)) choices.variables.push({ id, version, label: id + " v" + version });
              }
              redrawResearchRecord(); status(ui.docStatus, t("变量草稿已创建；关联尚未保存。"), "ok");
            } catch (err) { if (live(mark) && draft === target) { importButton.disabled = false; status(feedback, errorWords(t("没能导入变量草稿。"), err), "err"); } }
          }); row.append(cardId, importButton, feedback);
        }
        if (item.variable_card) row.append(button(t("打开定义卡"), () => { if (typeof window.ROWorkspace.onOpenVariable === "function" && mayLeave()) window.ROWorkspace.onOpenVariable(item.variable_card); })); materials.append(row);
      });
      if (variables.length) materials.append(el("p", "ws-empty", t("变量草稿仍需单独审阅和确认。")));
      box.append(materials);
    }
    if (context.attempt_draft) {
      const attempt = context.attempt_draft, group = el("div", "ws-research-group"); group.append(el("h3", null, t("尝试草稿")));
      [["description", t("尝试描述")], ["result", t("尝试结果")]].forEach(([key, label]) => {
        if (attempt[key]) { const line = el("p", "ws-research-draft", attempt[key]); line.setAttribute("aria-label", label); group.append(line); }
      });
      (attempt.parents || []).forEach((id) => { group.append(button(id, () => { if (window.RONode) window.RONode.openNode(id); }, "btn btn-small ws-file-link")); });
      group.append(el("p", "ws-empty", t("尝试草稿仍需单独提交为新的尝试。"))); box.append(group);
    }
    if (editable() && (!ui.previewing || context.attempt_draft || context.method || context.finding)) {
      const actions = el("div", "ws-actions");
      actions.append(button(t("记录研究尝试…"), () => { if (editable() && typeof window.ROWorkspace.onCreateAttempt === "function") window.ROWorkspace.onCreateAttempt(context.attempt_draft || { description: context.method, result: context.finding, parents: context.attempts }, (attempt, savedState) => {
        if (!attempt || !attempt.id || !draft || draft.researchContext !== context) return;
        if (!context.attempts.includes(attempt.id)) context.attempts.push(attempt.id);
        if (!(data.attempts || []).some((item) => item.id === attempt.id)) data.attempts.push({ id: attempt.id, label: (attempt.attrs || {}).description || attempt.id });
        redrawResearchRecord(); status(ui.docStatus, savedState && savedState.workingCopy ? t("尝试已保存到工作副本；研究记录中的关联尚未保存。") : t("尝试已保存；研究记录中的关联尚未保存。"));
      }); }));
      if (!ui.previewing && typeof window.ROWorkspace.onOpenVariableDrafts === "function") actions.append(button(t("查看变量草稿…"), () => { if (mayLeave()) window.ROWorkspace.onOpenVariableDrafts(); }));
      box.append(actions);
    }
    if (ui.previewing && !fields.children.length && !links.children.length && !outputs.length && !variables.length) box.append(el("p", "ws-empty", t("尚未补充研究记录。")));
    const feedback = el("p", "ws-status"); feedback.setAttribute("role", "status");
    if (editable() && (!ui.previewing || researchRecordDirty())) {
      const save = button(t("保存研究记录"), () => saveResearchRecord(feedback, save), "btn btn-small ws-record-save");
      save.disabled = !draft.path || draft.researchBusy || !researchRecordDirty(); box.append(save);
      if (!draft.path) box.append(el("p", "ws-empty", t("先保存结果文档，再保存研究记录。")));
    }
    box.append(feedback); ui.researchRecord = box; return box;
  }
  async function saveResearchRecord(feedback, control) {
    if (!draft || !draft.path || !editable() || draft.researchBusy || !researchRecordDirty()) return;
    const mark = stamp(), target = draft, sent = emptyResearchContext(draft.researchContext);
    draft.researchBusy = true; control.disabled = true; status(feedback, t("正在保存…"));
    try {
      const response = await apiPost("/api/workspace/research-record", { project: mark.project, report: draft.path, revision: draft.researchRecord.revision || null, context: sent });
      if (!live(mark) || draft !== target) return;
      draft.researchBusy = false; draft.researchRecord = response; saved.researchContext = emptyResearchContext(response.context || sent);
      if (JSON.stringify(draft.researchContext) === JSON.stringify(sent)) {
        const normalized = emptyResearchContext(saved.researchContext);
        Object.keys(draft.researchContext).forEach((key) => { if (!(key in normalized)) delete draft.researchContext[key]; });
        Object.assign(draft.researchContext, normalized);
      }
      status(feedback, response.history_warning ? t("研究记录已保存，但修改历史暂时未能记录。") : researchRecordDirty() ? t("已保存；之后输入的内容尚未保存。") : t("研究记录已保存。"), response.history_warning ? "" : "ok");
      control.disabled = !researchRecordDirty(); if (ui.previewing && !researchRecordDirty()) control.classList.add("hidden"); status(ui.docStatus, documentDirty() ? t("尚未保存") : "");
    } catch (err) {
      if (!live(mark) || draft !== target) return;
      draft.researchBusy = false; control.disabled = false;
      status(feedback, errorWords(t("没能保存研究记录，当前填写已保留。"), err), "err");
      if (/conflict|revision|changed/.test(err.state || "")) feedback.append(button(t("重新载入研究记录"), async () => {
        if (!window.confirm(t("重新载入会放弃尚未保存的研究记录，继续吗？"))) return;
        try {
          const response = await apiGet("/api/workspace/research-record?report=" + encodeURIComponent(target.path) + "&project=" + encodeURIComponent(mark.project));
          if (!live(mark) || draft !== target) return;
          draft.researchRecord = response; draft.researchContext = emptyResearchContext(response.context); saved.researchContext = emptyResearchContext(response.context); redrawResearchRecord();
        } catch (readError) { if (live(mark) && draft === target) status(feedback, errorWords(t("没能读取研究记录。"), readError), "err"); }
      }));
    }
  }
  function redrawResearchRecord() {
    if (!ui || !ui.researchRecord || !draft || draft.kind !== "result") return;
    const old = ui.researchRecord, snap = focusSnapshot(old); draft.researchOpen = old.open;
    old.replaceWith(researchRecordSection()); restoreFocus(ui.researchRecord, snap);
  }
  function insertText(words) {
    const text = ui.text, start = text.selectionStart == null ? text.value.length : text.selectionStart;
    const end = text.selectionEnd == null ? start : text.selectionEnd;
    text.value = text.value.slice(0, start) + words + text.value.slice(end);
    draft.content = text.value;
    if (text.setSelectionRange) text.setSelectionRange(start + words.length, start + words.length);
    status(ui.docStatus, t("尚未保存"));
    if (ui.previewing) renderPreview();
    else text.focus();
  }
  function attachmentTools() {
    const tools = el("details", "ws-attachments"); tools.append(disclosureSummary(t("引用文件或尝试")));
    tools.classList.toggle("hidden", !!ui.previewing);
    ui.attachments = tools;
    const row = el("div", "ws-attachment-row"), select = el("select", "form-input");
    select.setAttribute("aria-label", t("选择项目文件"));
    const blank = el("option", null, t("选择项目文件")); blank.value = ""; select.append(blank);
    (data.files || []).forEach((file) => { const option = el("option", null, file.path); option.value = file.path; select.append(option); });
    const attach = button(t("插入引用"), () => {
      if (!select.value) return;
      const image = /\.(png|jpe?g|gif|webp)$/i.test(select.value);
      insertText("\n" + markdownLink(draft.path || fallbackPath(draft.kind), select.value, image) + "\n");
    }); attach.disabled = !editable(); row.append(select, attach); tools.append(row);
    // The list stops at the engine's limit; a reader who cannot find a file here is told why (DESIGN.md 18.3).
    if (data.files_truncated) tools.append(el("p", "ws-empty ws-files-cut", t("项目里的文件多于 {n} 个，这里只列出前 {n} 个；其余的可以在正文里手写链接。", { n: data.files_limit || (data.files || []).length })));
    if ((data.attempts || []).length) {
      const attemptRow = el("div", "ws-attachment-row"), attempts = el("select", "form-input");
      attempts.setAttribute("aria-label", t("选择研究尝试"));
      const empty = el("option", null, t("选择研究尝试")); empty.value = ""; attempts.append(empty);
      data.attempts.forEach((attempt) => { const option = el("option", null, attempt.label || attempt.id); option.value = attempt.id; attempts.append(option); });
      const insert = button(t("引用尝试"), () => {
        const attempt = data.attempts.find((a) => a.id === attempts.value); if (!attempt) return;
        insertText("\n[" + String(attempt.label || attempt.id).replace(/[[\]\\]/g, "\\$&") + "](researchos:attempt/" + encodeURIComponent(attempt.id).replace(/[()]/g, (c) => c === "(" ? "%28" : "%29") + ")\n");
      }); insert.disabled = !editable(); attemptRow.append(attempts, insert); tools.append(attemptRow);
    }
    const refreshStatus = el("div", "ws-status");
    const refresh = button(t("刷新文件"), async () => {
      const mark = stamp(); refresh.disabled = true;
      status(refreshStatus, t("正在读取…"));
      try {
        const fresh = await apiGet("/api/workspace");
        if (!live(mark) || fresh.project !== project()) return;
        data.files = fresh.files || []; data.attempts = fresh.attempts || [];
        data.files_truncated = !!fresh.files_truncated; data.files_limit = fresh.files_limit; knownCache = new Map();
        redrawAttachments();
        status(ui.attachments.querySelector(".ws-reference-status"), t("文件列表已更新。"));
      } catch (err) {
        if (live(mark)) { refresh.disabled = false; status(refreshStatus, errorWords(t("没能读取项目文件。"), err), "err"); }
      }
    });
    refreshStatus.className += " ws-reference-status";
    tools.append(refresh, refreshStatus);
    return tools;
  }
  function redrawAttachments() {
    if (!ui || !ui.attachments) return;
    const old = ui.attachments, opened = old.open;
    const values = Array.from(old.querySelectorAll("select")).map((select) => select.value);
    const replacement = attachmentTools(); replacement.open = opened;
    Array.from(replacement.querySelectorAll("select")).forEach((select, index) => {
      if (Array.from(select.options || select.children).some((option) => option.value === values[index])) select.value = values[index];
    });
    old.replaceWith(replacement);
  }
  function documentConflict(err) {
    status(ui.docStatus, errorWords(t("文件已在其他地方修改，当前草稿已保留。"), err), "err");
    const row = el("div", "ws-conflict-actions");
    row.append(button(t("保留草稿"), () => row.remove()), button(t("重新载入文件"), () => {
      if (window.confirm(t("重新载入会放弃当前文档里尚未保存的修改，继续吗？"))) openDocument(draft.path, true, true);
    }));
    ui.docStatus.append(row);
  }
  // The person went on typing while a save was out, so the writing area holds the text that was sent plus their new words,
  // and the heading the engine wrote into the sent text (a new document's title, a changed title) is not in it. It is
  // written into the area now, around what they typed -- or the next save would take it away (DESIGN.md 18.3). Only when
  // the engine did what headedContent says it does; while an input method is composing, after it ends.
  function carryHeading(title, sent, answered, wasNew, shown) {
    if (!ui || !ui.text || answered == null || answered === sent || headedContent(title, sent, wasNew, shown) !== answered) return;
    if (ui.composing) { ui.afterComposing = () => carryHeading(title, sent, answered, wasNew, shown); return; }
    const area = ui.text, before = area.value, next = headedContent(title, draft.content, wasNew, shown);
    if (next === draft.content || before !== draft.content) return;
    let head = 0, tail = 0;
    const most = Math.min(before.length, next.length);
    while (head < most && before.charCodeAt(head) === next.charCodeAt(head)) head += 1;
    while (tail < most - head && before.charCodeAt(before.length - 1 - tail) === next.charCodeAt(next.length - 1 - tail)) tail += 1;
    const where = (at) => at == null ? at : at <= head ? at : at >= before.length - tail ? at + next.length - before.length : next.length - tail;
    const from = where(area.selectionStart), to = where(area.selectionEnd), top = area.scrollTop;
    area.value = next; draft.content = next;
    if (from != null && typeof area.setSelectionRange === "function") area.setSelectionRange(from, to);
    if (top != null) area.scrollTop = top;
    if (ui.previewing) renderPreview();
  }
  async function saveDocument() {
    if (!draft || !editable() || ui.save.disabled) return;
    if (draft.kind !== "overview" && !draft.title.trim()) { status(ui.docStatus, t("请写一个标题。"), "err"); ui.title.focus(); return; }
    const mark = stamp(), target = draft, payload = { project: project(), kind: draft.kind, title: draft.title.trim(), content: draft.content, revision: draft.revision };
    if (draft.path) payload.path = draft.path;
    const titleSent = draft.title, contentSent = draft.content, wasNew = draft.revision == null, shown = saved ? saved.title : "";
    ui.save.disabled = true; status(ui.docStatus, t("正在保存…"));
    try {
      const response = await apiPost("/api/workspace/document", payload);
      if (!live(mark) || draft !== target) return;
      draft.path = response.path; draft.revision = response.revision; draft.officialness = response.officialness || draft.officialness;
      const unchanged = draft.title === titleSent && draft.content === contentSent;
      saved = { title: response.title == null ? payload.title : response.title, content: response.content == null ? contentSent : response.content, researchContext: saved.researchContext };
      if (unchanged) { draft.title = saved.title; draft.content = saved.content; renderEditor(true); }
      else { ui.save.disabled = !editable(); carryHeading(titleSent, contentSent, response.content, wasNew, shown); }
      const summary = { path: draft.path, kind: draft.kind, title: saved.title, excerpt: saved.content.slice(0, 100), officialness: draft.officialness, modified: response.modified || new Date().toISOString() };
      const index = (data.documents || []).findIndex((d) => d.path === draft.path);
      if (index < 0) data.documents.push(summary); else data.documents[index] = summary;
      renderNav();
      status(ui.docStatus, documentDirty() ? t("已保存；之后输入的内容尚未保存。") : response.history_error ? t("文件已保存，但修改历史暂时未能记录。") : t("已保存"), documentDirty() || response.history_error ? "" : "ok");
      if (wasNew && draft.kind === "result") {
        try {
          const record = await apiGet("/api/workspace/research-record?report=" + encodeURIComponent(draft.path) + "&project=" + encodeURIComponent(mark.project));
          if (live(mark) && draft === target) { draft.researchRecord = record; redrawResearchRecord(); }
        } catch (err) { /* The document is saved; its research draft remains available for a later save. */ }
      }
      if (typeof pollGeneration === "function") pollGeneration();
    } catch (err) {
      if (!live(mark) || draft !== target) return;
      ui.save.disabled = !editable();
      if (err && /conflict|revision|changed/.test(err.state || "")) documentConflict(err);
      else status(ui.docStatus, errorWords(t("没能保存，当前内容已保留。"), err), "err");
    }
  }

  function renderTaskList() {
    const list = ui.list, snap = focusSnapshot(list);
    // A native toggle can be queued just before a refresh; read it before replacing the row.
    Object.values(taskDisclosures).forEach((view) => view.remember());
    list.replaceChildren();
    sections = {}; metaNodes = {}; wordsNodes = {}; rowControls = {}; rowNodes = {}; outputNodes = {};
    taskDisclosures = {};
    // Tasks are appended in creation order. Older titles stay visible, with only the newest three open by default.
    const recent = new Set((data.tasks || []).slice(-3).map((task) => task.id));
    showTaskError();
    // 21.1: the tasks stand under the attempt they belong to, in `after` order; those without one (or whose attempt is
    // gone from the table) under 「未归入尝试」, last.
    const sequence = [];
    taskGroups().forEach((group) => { sequence.push({ heading: group }); group.tasks.forEach((task) => sequence.push({ task: task })); });
    sequence.forEach((entry) => {
      if (entry.heading) {
        const heading = el("h3", "ws-task-group", entry.heading.title); heading.dataset.attempt = entry.heading.id || "";
        list.append(heading);
        if (!entry.heading.id && hasLooseTasks()) list.append(openGroupingButton("group-tasks-loose"));
        return;
      }
      const task = entry.task, run = latestRun(task.id);
      const row = el("div", "ws-task" + (task.done ? " done" : ""));
      const check = keyed(input("checkbox"), "check:" + task.id); check.checked = !!task.done;
      check.setAttribute("aria-label", (task.done ? t("标为未完成") : t("标为完成")) + " " + task.text);
      check.addEventListener("change", () => {
        // DESIGN.md 19.6: clearing the done mark of a task that has a result record asks first (the list the page holds
        // now, not the row as drawn: the row is kept while a reply is typed). Declined, the box is ticked again and
        // nothing is saved. Ticking never asks.
        const now = (data.tasks || []).find((x) => x.id === task.id) || task;
        if (!check.checked && now.report && !window.confirm(t("这项任务已经有结果。改回未完成后可以再次运行，再次运行会改写同一份结果记录。改回未完成吗？"))) {
          check.checked = true;
          return;
        }
        return mutateTasks(data.tasks.map((x) => x.id === task.id ? Object.assign({}, x, { done: check.checked }) : x));
      });
      const disclosure = el("details", "ws-task-details");
      disclosure.open = Object.prototype.hasOwnProperty.call(taskOpen, task.id) ? taskOpen[task.id] : recent.has(task.id);
      const view = { open: disclosure.open, node: disclosure, remember: () => {
        if (view.open !== disclosure.open) { view.open = disclosure.open; taskOpen[task.id] = disclosure.open; }
      } };
      taskDisclosures[task.id] = view;
      disclosure.addEventListener("toggle", () => { if (taskDisclosures[task.id] === view) view.remember(); });
      const summary = keyed(disclosureSummary(task.text), "task-details:" + task.id);
      summary.className += " ws-task-summary"; summary.lastChild.className = "ws-task-text";
      const body = el("div", "ws-task-body"), words = el("div", "ws-task-words");
      if (task.context && task.context.report) {
        const report = (data.documents || []).find((doc) => doc.path === task.context.report);
        words.append(keyed(button(t("研究记录") + ": " + (report ? report.title : task.context.report), () => openDocument(task.context.report), "btn btn-small ws-file-link"), "context:" + task.id));
      }
      const meta = el("span", "ws-task-meta"); metaNodes[task.id] = meta; setMeta(task, meta); words.append(meta);
      wordsNodes[task.id] = words; rowNodes[task.id] = row;
      if (parseAgent(task.owner) || run) { sections[task.id] = agentSection(task); words.append(sections[task.id]); }
      outputNodes[task.id] = el("div", "ws-task-outputs"); words.append(outputNodes[task.id]); drawTaskOutputs(task);
      const edit = keyed(button(t("编辑"), () => {
        if (taskDirty() && !window.confirm(t("放弃当前任务里尚未保存的填写吗？"))) return;
        taskDraft = { id: task.id, text: task.text, owner: task.owner || "", due: task.due || "", custom: false, contextReport: (task.context || {}).report || "", attempt: task.attempt || "", after: task.after || "" }; renderTasks(); ui.taskText.focus();
      }), "edit:" + task.id);
      const remove = keyed(button(t("移除"), () => removeTask(task)), "remove:" + task.id);
      named(edit, t("编辑：{task}", { task: task.text })); named(remove, t("移除：{task}", { task: task.text }));
      rowControls[task.id] = [check, edit, remove]; lockRow(task);
      const actions = el("div", "ws-task-actions"); actions.append(edit, remove);
      body.append(words, actions); disclosure.append(summary, body);
      row.append(check, disclosure); list.append(row);
    });
    restoreFocus(list, snap);
  }
  // The removal of a task, after its question; a task that continued the removed one now continues what that one continued.
  // True when the question was agreed to (the save follows, and may still fail: the caller looks at the list).
  async function removeTask(task) {
    if (!window.confirm(t("移除这项任务吗？"))) return false;
    await mutateTasks(data.tasks.filter((x) => x.id !== task.id).map((x) => x.after === task.id ? Object.assign({}, x, { after: task.after || null }) : x));
    return true;
  }
  function showTaskError() {
    if (!ui.taskError) return;
    ui.taskError.replaceChildren();
    if (data.tasks_error) ui.taskError.append(el("p", "ws-status err", data.tasks_error.message || t("任务文件暂时无法读取，其他文档仍可编辑。")));
  }
  function showConnection() {
    if (!ui || !ui.connection) return;
    ui.connection.textContent = offline ? t("暂时连不上 Research OS，正在重试。") : "";
    ui.connection.className = "ws-agent-quiet ws-connection" + (offline ? "" : " hidden");
  }
  function renderTasks() {
    const box = ui.tasks, snap = focusSnapshot(box); box.replaceChildren();
    const header = el("div", "ws-task-header"); header.append(el("h2", "ws-task-title", t("任务", { n: 2 }))); box.append(header);
    if (data.tasks_officialness && data.tasks_officialness.label) header.append(el("span", "ws-officialness", data.tasks_officialness.label));
    const agents = keyed(button(t("AI 执行者"), toggleSettings), "agents-toggle"); agents.setAttribute("aria-pressed", String(settingsOpen)); header.append(agents);
    const folders = keyed(button(t("输出目录"), openOutputFolders), "output-folders"); folders.disabled = !editable(); header.append(folders);
    if (hasLooseTasks()) header.append(openGroupingButton("group-tasks"));
    ui.connection = el("p"); ui.connection.setAttribute("role", "status"); box.append(ui.connection); showConnection();
    ui.settings = el("div", "ws-agent-settings hidden"); box.append(ui.settings);
    const list = el("div", "ws-task-list"); ui.list = list; box.append(list);
    ui.taskError = el("div"); box.append(ui.taskError);
    renderTaskList();
    const form = el("form", "ws-task-form"), text = keyed(input("text", t("例如：整理下一次组会要讨论的问题")), "task-text"); text.value = taskDraft.text;
    const owner = keyed(el("select", "form-input"), "task-owner"); ui.ownerSelect = owner; fillOwnerOptions(owner);
    const name = taskDraft.custom ? keyed(input("text", t("姓名（可留空）")), "task-name") : null;
    if (name) { name.value = taskDraft.owner; name.addEventListener("input", () => { taskDraft.owner = name.value; }); }
    ui.ownerName = name;
    const due = keyed(input("date"), "task-due"); due.value = taskDraft.due;
    const submit = keyed(button(taskDraft.id ? t("保存任务") : t("添加任务"), () => addTask(), "btn btn-small"), "task-submit");
    // 21.1: the task's place on the route comes first -- the attempt it belongs to, and the task it continues.
    const attempt = keyed(el("select", "form-input"), "task-attempt"), after = keyed(el("select", "form-input"), "task-after");
    ui.taskAttempt = attempt; ui.taskAfter = after; fillAttemptOptions(attempt); fillAfterOptions(after);
    attempt.addEventListener("change", () => chooseAttempt(attempt, after));
    after.addEventListener("change", () => { taskDraft = Object.assign({}, taskDraft, { after: after.value }); });
    [text, owner, name, due, submit, attempt].forEach((node) => { if (node) node.disabled = !tasksEditable() || taskBusy || taskConflict; });  // `after` decides for itself
    function changed() { taskDraft = Object.assign({}, taskDraft, { text: text.value, due: due.value }); }
    [text, due].forEach((node) => node.addEventListener("input", changed));
    owner.addEventListener("change", () => chooseOwner(owner));
    form.addEventListener("submit", (event) => { event.preventDefault(); addTask(); });
    const ownerField = field(t("负责人"), owner), dateField = field(t("日期"), due);
    ownerField.className += " ws-field-owner"; dateField.className += " ws-field-date";
    const attemptField = field(t("属于哪条尝试"), attempt), afterField = field(t("接着哪个任务"), after), textField = field(t("任务", { n: 1 }), text);
    attemptField.className += " ws-field-attempt"; afterField.className += " ws-field-after"; textField.className += " ws-field-text";
    form.append(attemptField, afterField, textField, ownerField);
    if (name) { const nameField = field(t("姓名"), name); nameField.className += " ws-field-name"; form.append(nameField); }
    const context = keyed(el("select", "form-input"), "task-context");
    const noContext = el("option", null, t("无关联研究记录")); noContext.value = ""; context.append(noContext);
    const records = (data.documents || []).filter((doc) => doc.kind === "result");
    records.forEach((doc) => { const option = el("option", null, doc.title || doc.path); option.value = doc.path; context.append(option); });
    if (taskDraft.contextReport && !records.some((doc) => doc.path === taskDraft.contextReport)) { const option = el("option", null, taskDraft.contextReport); option.value = taskDraft.contextReport; context.append(option); }
    context.value = taskDraft.contextReport || ""; context.disabled = !tasksEditable() || taskBusy || taskConflict;
    context.addEventListener("change", () => { taskDraft.contextReport = context.value; });
    const contextField = field(t("研究上下文（可选）"), context); contextField.className += " ws-field-context";
    form.append(dateField, contextField, submit);
    form.append(keyed(button(t("清空"), () => { taskDraft = blankDraft(); renderTasks(); }), "task-clear"));
    const taskStatus = el("div", "ws-status"); taskStatus.setAttribute("role", "status");
    box.append(form, taskStatus); ui.taskText = text; ui.taskStatus = taskStatus;
    if (taskConflict) showTaskConflict();
    if (data.mode === "member") {
      const propose = button(t("提交修改"), () => {
        if (taskDirty() || taskBusy) { status(ui.taskStatus, t("请先保存任务，再提交修改。")); return; }
        // The result records the changed tasks link go with them (DESIGN.md 18.3), or the PI's approval would leave a task
        // pointing at a record that is not in the official project.
        if (typeof window.ROWorkspace.onPropose === "function") window.ROWorkspace.onPropose(["research/tasks.json"].concat(data.changed_task_reports || []));
      }); propose.disabled = !tasksEditable(); box.append(propose);
    }
    renderSettings(true);
    restoreFocus(box, snap);
  }
  // The page refreshes on every change of the project's files, which an agent moves about every 2 s. What the person is
  // typing is never rebuilt under them (the draft lives outside data.tasks): a reply is patched in place, the rows
  // beside the task form are drawn again, and the settings keep their inputs.
  function refreshTasks() {
    if (!ui.taskText) { renderTasks(); return; }
    const typing = activeIn(ui.tasks);
    if (!typing || !isTyping(typing)) { renderTasks(); return; }
    if (within(ui.list, typing)) { updateAgentSections(); syncRowMarks(); return; }
    renderTaskList();
    // The file moved under an edit in progress: the form stays as it is (still being typed in), but it cannot save.
    if (taskConflict) {
      const submit = descendants(ui.tasks).find((x) => x.dataset && x.dataset.key === "task-submit");
      if (submit) submit.disabled = true;
      showTaskConflict();
    }
  }
  function syncRowMarks() {
    (data.tasks || []).forEach((task) => {
      const controls = rowControls[task.id], row = rowNodes[task.id];
      if (controls) controls[0].checked = !!task.done;
      if (row) row.className = "ws-task" + (task.done ? " done" : "");
    });
  }
  async function addTask() {
    if (!tasksEditable() || taskBusy || taskConflict) return;
    if (!taskDraft.text.trim()) { status(ui.taskStatus, t("请写下要做的事。"), "err"); ui.taskText.focus(); return; }
    // A new task belongs to an attempt (21.1); a task from before that may stay without one.
    if (!taskDraft.id && !taskDraft.attempt) { status(ui.taskStatus, t("新任务要属于一条尝试：请在「属于哪条尝试」里选一条，或选「新开一条尝试…」。"), "err"); ui.taskAttempt.focus(); return; }
    const next = (data.tasks || []).map((task) => Object.assign({}, task));
    const existing = next.find((task) => task.id === taskDraft.id);
    if (taskDraft.id && !existing) { status(ui.taskStatus, t("这项任务已被移除。可以把当前填写添加为新任务。"), "err"); taskDraft.id = null; return; }
    const item = { id: taskDraft.id || (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : "task-" + Date.now().toString(36) + Math.random().toString(36).slice(2)), text: taskDraft.text.trim(), owner: taskDraft.owner.trim(), due: taskDraft.due, done: existing ? !!existing.done : false };
    if (taskDraft.contextReport) item.context = { report: taskDraft.contextReport }; else if (existing && existing.context) item.context = null;
    if (taskDraft.attempt) item.attempt = taskDraft.attempt; else if (existing && existing.attempt) item.attempt = null;
    if (taskDraft.after && taskDraft.attempt) item.after = taskDraft.after; else if (existing && existing.after) item.after = null;
    // Object.assign keeps what the form does not edit: a task's report (17.2) stays with it.
    if (existing) Object.assign(existing, item); else next.push(item);
    await mutateTasks(next, true);
  }
  function showTaskConflict() {
    if (ui.taskStatus.dataset.conflict) return;
    ui.taskStatus.dataset.conflict = "1";
    status(ui.taskStatus, t("任务已在其他地方修改，当前填写已保留。请重新载入后再保存。"), "err");
    ui.taskStatus.append(button(t("重新载入任务"), async () => {
      const mark = stamp();
      try {
        const fresh = await apiGet("/api/workspace");
        if (!live(mark)) return;
        data.tasks = fresh.tasks; data.tasks_revision = fresh.tasks_revision; data.tasks_error = fresh.tasks_error; taskConflict = false;
        data.changed_task_reports = fresh.changed_task_reports || []; renderTasks();
        status(ui.taskStatus, t("已载入最新任务，当前填写仍保留。"));
      } catch (err) { if (live(mark)) status(ui.taskStatus, errorWords(t("没能读取任务。"), err), "err"); }
    }));
  }
  async function mutateTasks(tasks, clearForm) {
    if (!tasksEditable() || taskBusy || taskConflict) return;
    const mark = stamp(), snapshot = JSON.stringify(taskDraft);
    taskBusy = true; renderTasks(); status(ui.taskStatus, t("正在保存…"));
    try {
      const response = await apiPost("/api/workspace/tasks", { project: project(), tasks: tasks, revision: data.tasks_revision });
      if (!live(mark)) return;
      data.tasks = response.tasks; data.tasks_revision = response.tasks_revision; data.tasks_officialness = response.tasks_officialness || data.tasks_officialness; taskBusy = false; taskWrites += 1;
      data.changed_task_reports = response.changed_task_reports || [];
      if (clearForm && snapshot === JSON.stringify(taskDraft)) taskDraft = blankDraft(taskDraft.attempt);
      renderTasks(); status(ui.taskStatus, response.history_error ? t("任务已保存，但修改历史暂时未能记录。") : t("已保存"), response.history_error ? "" : "ok");
      if (typeof pollGeneration === "function") pollGeneration();
    } catch (err) {
      if (!live(mark)) return;
      taskBusy = false; taskConflict = !!(err && /conflict|revision|changed/.test(err.state || ""));
      renderTasks();
      if (!taskConflict) status(ui.taskStatus, errorWords(t("没能保存任务，当前填写已保留。"), err), "err");
    }
  }

  // -- AI agents carry out tasks (DESIGN.md Section 17) ---------------------------------------
  // A task whose owner is an agent on this Mac gets its run in the row: 运行, then the state in
  // words (17.9), 停止, 运行记录, 回答 / 打回, 撤回本次运行 and 打开结果. The page only asks the
  // engine and follows a run by polling; what an agent may not do is held there (17.5).
  const RUNNER_NAMES = { "claude-code": "Claude Code", opencode: "OpenCode", codex: "Codex", "deepseek-harness": "DeepSeek Harness" };
  const PROGRAMS = { "claude-code": "claude", opencode: "opencode", codex: "codex", "deepseek-harness": "dsh" };
  const OWNER_OTHER = "\u0000other", OWNER_SETTINGS = "\u0000settings";
  const POLL_MS = 2000, POLL_MAX_MS = 30000;
  const REPLY_FROM = ["done", "ended", "failed", "stopped", "interrupted"];
  // How the mcp package is added: to the uv tool install (the documented route), or to a source checkout (README).
  const MCP_INSTALL = [
    [() => t("用 uv 安装的："), 'uv tool install --force --with "mcp<2" git+https://github.com/LinyingL/research-os'],
    [() => t("从源码目录安装的："), 'python -m pip install -e ".[mcp]"'],
  ];
  // The tables below hold functions (13.6): each sentence is looked up when it is shown, in the page's language,
  // and each Chinese text is the whole argument of its own t().
  // state code -> [words, tone] (tone classes are app.html's .status-label.tone-*).
  const STATE_LABELS = {
    preparing: [() => t("准备中"), "ochre"], running: [() => t("运行中"), "ochre"], needs_input: [() => t("等你回答"), "clay"],
    done: [() => t("已完成"), "olive"], ended: [() => t("未报告"), "gray"], failed: [() => t("失败"), "clay"],
    stopped: [() => t("已停止"), "gray"], interrupted: [() => t("已中断"), "gray"], out_of_bounds: [() => t("越过了限制"), "clay"],
  };
  const STEP_KINDS = {
    start: () => t("开始"), say: () => t("它说"), tool: () => t("操作"), refused: () => t("被拒绝"), error: () => t("出错"), end: () => t("结束"),
  };
  const VIOLATION_WORDS = {
    data_changed: () => t("改动了运行前就有的数据文件"), data_deleted: () => t("删除了运行前就有的数据文件"),
    record_changed: () => t("改动了研究记录"), record_deleted: () => t("删除了研究记录"),
    researchos_changed: () => t("改动了 Research OS 自己的记录文件夹"), git_changed: () => t("改变了 git 的状态"),
    git_hooks_changed: () => t("改动了 git 的钩子（git 会自动运行的程序）"), git_config_changed: () => t("改动了 git 的配置"),
    backup_deleted: () => t("删除了 Research OS 的备份"),
    agent_settings_changed: () => t("改动了 AI 执行程序的项目设置（.claude、.opencode、.codex、opencode.json）"),
  };
  // A run's error code, and the code of an end step the engine writes (`stopped` is no error: it words a stopped run).
  const ERROR_WORDS = {
    timeout: () => t("运行超过了时间限制，已被停止。"), shutdown: () => t("运行期间 Research OS 被关闭了。"),
    engine_gone: () => t("运行期间 Research OS 退出了。"), aborted: () => t("程序在给出结果之前就结束了。"),
    exit: () => t("程序异常退出。"), model_error: () => t("模型返回了错误。"), start_failed: () => t("程序没能启动。"),
    check_failed: () => t("运行结束后核对改动时出了错，改动清单可能不完整。"), error_max_budget_usd: () => t("费用达到了上限。"),
    guard_failed: () => t("Research OS 的检查程序没有正常启动，为了安全没有运行。"),
    internal: () => t("Research OS 在运行中出了错。"), error_max_turns: () => t("达到了最多可以走的步数。"),
    error_during_execution: () => t("程序在运行中出了错。"), stopped: () => t("运行已被你停止。"),
  };
  // A step of the run log that carries a code (18.1 item 6): why the hook (or the program's own permission rules)
  // refused a call, and an error the engine knows. The person reads the sentence; the program's own words stay
  // beneath it as the detail.
  const STEP_WORDS = {
    unchecked: () => t("Research OS 没能检查这一步，为了安全没有放行。"), no_file: () => t("这一步没有指明项目里的哪个文件，没有放行。"),
    outside_project: () => t("它想改项目文件夹以外的文件，被拦下了。"),
    records_folder: () => t("它想动 Research OS 的记录文件夹（.researchos），被拦下了。"),
    git_files: () => t("它想改 git 文件夹里的东西，被拦下了。"),
    agent_settings: () => t("它想改 Claude Code 或 OpenCode 在项目里的设置，被拦下了。"),
    protected_file: () => t("它想改动或删除运行前就有的数据文件（或尝试表、任务文件），被拦下了。"),
    off_limits: () => t("它想读 Research OS 的主文件夹或 ~/.ssh，被拦下了。"),
    research_os_command: () => t("它想运行 researchos 命令，被拦下了。"),
    git_state: () => t("它想改变 git 的状态（提交、推送等），被拦下了。"),
    engine_api: () => t("它想直接联系 Research OS 应用的接口，被拦下了。"),
    permission_rule: () => t("它想做的事被运行的权限规则拒绝了，比如 git 命令。"), tool_unavailable: () => t("它调用了一个不可用的工具。"),
    mcp_failed: () => t("Research OS 的工具没有启动，AI 执行者无法汇报结果。"),
  };
  const REVERT_SKIPPED = {
    not_recorded: () => t("目录内容未完整记录，无法安全撤回"),
    changed_since: () => t("运行之后又改动过"), no_copy: () => t("没有运行前的副本"), in_cloud: () => t("文件只在云端"),
    kept: () => t("你选择保留"), protected: () => t("属于 Research OS 的记录文件夹或 git，撤回不会动它"),
  };
  const PRIVACY = {
    "deepseek-harness": () => t("DeepSeek Harness 会把它读到的项目内容发送给 DeepSeek。"),
    codex: () => t("Codex 会把它读到的项目内容发送给 OpenAI。"),
    "claude-code": () => t("Claude Code 会把它读到的项目内容发送给 Anthropic。"),
    opencode: () => t("OpenCode 会把它读到的项目内容发送给你在 OpenCode 里配置的模型服务，也可能是这台电脑上的本地服务。"),
  };
  // The sentence a table holds for a code, else other(): a code the page does not know reads as a generic
  // sentence, never as the code.
  function byCode(table, code, other) {
    return typeof code === "string" && Object.prototype.hasOwnProperty.call(table, code) ? table[code]() : other();
  }

  function parseAgent(owner) {
    const m = /^agent:([^:]+):(.+)$/.exec(String(owner || ""));
    return m && RUNNER_NAMES[m[1]] ? { runner: m[1], model: m[2], owner: m[0], label: RUNNER_NAMES[m[1]] + " · " + m[2] } : null;
  }
  // The owner as people read it: an agent by its label, never by the raw agent: value.
  function ownerLabel(owner) {
    if (!owner) return "";
    const known = agentInfo && (agentInfo.agents || []).find((a) => a.owner === owner);
    if (known && known.label) return known.label;
    const parsed = parseAgent(owner);
    if (parsed) return parsed.label;
    return /^agent:/.test(owner) ? t("AI 执行者") : owner;
  }
  function setMeta(task, node) {
    const words = [ownerLabel(task.owner), task.due].filter(Boolean).join(" · ");
    node.textContent = words; node.classList.toggle("hidden", !words);
  }
  function isActiveState(value) { return value === "preparing" || value === "running"; }
  // How a run ended apart from the warning (17.9): an out-of-bounds run reads 越过了限制, and what it would have read
  // (outcome) is what decides whether it can be answered or sent back.
  function outcomeOf(run) { return run && run.state === "out_of_bounds" && run.outcome ? run.outcome : run && run.state; }
  function activeIds() { return runsInfo.runs.filter((r) => isActiveState(r.state)).map((r) => r.id); }
  function anyActive() { return activeIds().length > 0 || !!runsInfo.active; }
  function latestRun(taskId) { return runsInfo.runs.find((r) => r.task_id === taskId) || null; }
  // undo: the open 撤回本次运行 list of the task's latest run (18.1 item 5), { run, plan, error, off } -- off holds the
  // paths whose box the person cleared.
  function agentState(taskId) { return agentUiState[taskId] || (agentUiState[taskId] = { log: false, reply: null, text: "", open: {}, undo: null }); }
  // The summary of a disclosure (12.13): the quiet line chevron the node page draws, turned by CSS when it is open,
  // and the words; the browser's own triangle is hidden by .res-draft-summary.
  function disclosureSummary(words) {
    const summary = el("summary", "res-draft-summary");
    if (typeof chevronIcon === "function") summary.append(chevronIcon());
    summary.append(el("span", null, words));
    return summary;
  }
  // A <details> (12.11) that stays as the person left it when the row is drawn again by the next poll.
  function more(st, key, words, content) {
    const box = el("details", "ws-agent-more");
    box.open = !!st.open[key];
    box.addEventListener("toggle", () => { st.open[key] = box.open; });
    box.append(disclosureSummary(words), el("pre", "ws-code", content));
    return box;
  }
  function may() { return tasksEditable() && !taskBusy && !taskConflict; }

  // -- the attempt a task belongs to, and the task it continues (DESIGN.md 21.1) ---------------
  const ATTEMPT_NEW = "\u0000new";
  function attemptLabel(item) {
    const number = String(item.id).split("#").pop(), words = item.label && item.label !== item.id ? item.label : "";
    return "#" + number + (words ? " " + words : "");
  }
  // The tasks of one attempt in `after` order (researchos.attempt_tasks.ordered): each right after the one it
  // continues, the others by the order of the list; a circle written by hand is left to the end.
  function orderedTasks(tasks) {
    const ids = new Set(tasks.map((task) => task.id)), followers = new Map(), roots = [], out = [], seen = new Set();
    tasks.forEach((task) => {
      if (task.after && ids.has(task.after) && task.after !== task.id) { if (!followers.has(task.after)) followers.set(task.after, []); followers.get(task.after).push(task); }
      else roots.push(task);
    });
    const walk = (first) => {
      const pending = [first];
      while (pending.length) {
        const task = pending.pop();
        if (seen.has(task.id)) continue;
        seen.add(task.id); out.push(task);
        (followers.get(task.id) || []).slice().reverse().forEach((x) => pending.push(x));
      }
    };
    roots.forEach(walk); tasks.forEach(walk);
    return out;
  }
  function taskGroups() {
    const attempts = data.attempts || [], known = new Set(attempts.map((a) => a.id)), by = new Map(), loose = [];
    (data.tasks || []).forEach((task) => {
      if (task.attempt && known.has(task.attempt)) { if (!by.has(task.attempt)) by.set(task.attempt, []); by.get(task.attempt).push(task); }
      else loose.push(task);
    });
    const groups = attempts.filter((a) => by.has(a.id)).map((a) => ({ id: a.id, title: attemptLabel(a), tasks: orderedTasks(by.get(a.id)) }));
    if (loose.length) groups.push({ id: null, title: t("未归入尝试"), tasks: orderedTasks(loose) });
    return groups;
  }
  function fillAttemptOptions(select) {
    const choices = [{ value: "", words: t("请选择一条尝试") }].concat((data.attempts || []).map((a) => ({ value: a.id, words: attemptLabel(a) })));
    // An attempt the draft names that is no longer in the table stays shown while it is the value.
    if (taskDraft.attempt && !choices.some((c) => c.value === taskDraft.attempt)) choices.push({ value: taskDraft.attempt, words: attemptLabel({ id: taskDraft.attempt }) });
    choices.push({ value: ATTEMPT_NEW, words: t("新开一条尝试…") });
    select.replaceChildren(...choices.map((c) => { const option = el("option", null, c.words); option.value = c.value; return option; }));
    select.value = taskDraft.attempt;
  }
  // The tasks the chosen attempt holds that this one may continue: not itself, nor one that continues it.
  function fillAfterOptions(select) {
    const choices = [{ value: "", words: t("不接着任何任务") }];
    if (taskDraft.attempt) {
      const follows = new Set(taskDraft.id ? [taskDraft.id] : []);
      for (let grew = true; grew;) {
        grew = false;
        (data.tasks || []).forEach((task) => { if (task.after && follows.has(task.after) && !follows.has(task.id)) { follows.add(task.id); grew = true; } });
      }
      orderedTasks((data.tasks || []).filter((task) => task.attempt === taskDraft.attempt)).filter((task) => !follows.has(task.id))
        .forEach((task) => choices.push({ value: task.id, words: String(task.text).split("\n")[0].slice(0, 60) }));
    }
    if (taskDraft.after && !choices.some((c) => c.value === taskDraft.after)) taskDraft = Object.assign({}, taskDraft, { after: "" });
    select.replaceChildren(...choices.map((c) => { const option = el("option", null, c.words); option.value = c.value; return option; }));
    select.value = taskDraft.after;
    select.disabled = !taskDraft.attempt || !tasksEditable() || taskBusy || taskConflict;
  }
  function chooseAttempt(select, after) {
    if (select.value === ATTEMPT_NEW) { select.value = taskDraft.attempt; startAttempt(); return; }
    taskDraft = Object.assign({}, taskDraft, { attempt: select.value, after: "" });
    fillAfterOptions(after);
  }
  // 「新开一条尝试…」: the existing new-attempt form opens; once its row is saved and read back, the form names it, and a
  // task already written is added under it.
  function startAttempt() {
    if (!editable() || typeof window.ROWorkspace.onCreateAttempt !== "function") return;
    const mark = stamp();
    window.ROWorkspace.onCreateAttempt({}, (created) => {
      if (!live(mark) || !created || !created.id || !ui) return;
      if (!(data.attempts || []).some((a) => a.id === created.id)) data.attempts.push({ id: created.id, label: (created.attrs || {}).description || created.id });
      taskDraft = Object.assign({}, taskDraft, { attempt: created.id, after: "" });
      renderTasks();
      if (taskDraft.text.trim() && !taskDraft.id) addTask();
    });
  }
  // 「在这条尝试下加任务」 (the attempt's panel): the form with that attempt chosen, the way 再派一个任务 will ask for a
  // task that continues another (`after`) and works from a research record (`context`, a result record's path).
  function startTask(choice) {
    if (!ui || !ui.taskText || !editable()) return false;
    if (taskDirty() && !window.confirm(t("放弃当前任务里尚未保存的填写吗？"))) return false;
    taskDraft = Object.assign(blankDraft(choice && choice.attempt, choice && choice.context), { after: (choice && choice.after) || "" });
    renderTasks(); ui.taskText.focus();
    if (ui.tasks && typeof ui.tasks.scrollIntoView === "function") ui.tasks.scrollIntoView();
    return true;
  }

  // -- 把这些任务归入尝试…: grouping the tasks a project already has (DESIGN.md 21.1) -----------------------------------
  // The engine proposes (`GET /api/workspace/task-groups`): groups of the tasks that belong to no attempt, each with the
  // attempt row it would add, and the tasks that are word for word the same as an earlier one. The side panel lets the
  // person rename a group (and change its number and date), move tasks in or out, merge it with another or drop it;
  // nothing is written until one is confirmed, and confirming is one call (`POST` of the same path) that writes the
  // attempt row and sets `attempt` (and `after`, for a task that continues another of the group) on that group's tasks. A
  // duplicate is removed by the task row's own removal, with its question; the same question given to several agents is
  // not a duplicate and is only said. The numbers the groups carry follow the table: once a group is written, the others
  // move to the next free numbers, but a number the person typed stays. The panel is the folders form's kind of subject
  // (`folderSubject`): leaving it after a change asks first.
  // a task whose attempt is gone from the table is as loose as one with none (the list puts it under 「未归入尝试」 too)
  function hasLooseTasks() {
    const known = new Set((data.attempts || []).map((a) => a.id));
    return (data.tasks || []).some((task) => !task.attempt || !known.has(task.attempt));
  }
  function openGroupingButton(key) {
    const open = keyed(button(t("把这些任务归入尝试…"), openGrouping), key);
    open.disabled = !tasksEditable();
    return open;
  }
  function taskNumbered(subject, ids) { return ids.slice().sort((a, b) => subject.info[a].number - subject.info[b].number); }
  function cutWords(words, size) { const text = String(words).split("\n")[0]; return text.length > size ? text.slice(0, size) + "…" : text; }
  function groupWords(group) { return group.description.trim() || group.term; }
  function wholeNumber(value) { const found = /^\s*(\d+)/.exec(String(value)); return found ? parseInt(found[1], 10) : 0; }
  // The table has a new row, so the numbers still on offer move past it -- to the next free ones in the order shown --
  // except a number the person typed, which is theirs (and which the others step round).
  function renumberGroups(subject, written) {
    subject.biggest = Math.max(subject.biggest, wholeNumber(written));
    const used = new Set(subject.groups.filter((group) => group.typed).map((group) => group.number.trim()));
    let next = subject.biggest + 1;
    subject.groups.forEach((group) => {
      if (group.typed) return;
      while (used.has(String(next))) next += 1;
      group.number = String(next); used.add(group.number);
    });
  }
  function moveGroupedTask(subject, id, to) {
    subject.groups.forEach((group) => { if (group.tasks.indexOf(id) >= 0) { group.tasks = group.tasks.filter((x) => x !== id); group.edited = true; } });
    subject.pool = subject.pool.filter((x) => x !== id);
    const target = subject.groups.find((group) => group.id === to);
    if (target) { target.tasks = taskNumbered(subject, target.tasks.concat(id)); target.edited = true; } else subject.pool = taskNumbered(subject, subject.pool.concat(id));
    drawGrouping(subject);
  }
  function mergeGroups(subject, from, into) {
    into.tasks = taskNumbered(subject, into.tasks.concat(from.tasks));
    if (from.date && (!into.date || (from.date.length === into.date.length && from.date < into.date))) into.date = from.date;
    subject.groups = subject.groups.filter((group) => group !== from);
    into.edited = true; drawGrouping(subject);
  }
  function dropGroup(subject, group) {
    subject.pool = taskNumbered(subject, subject.pool.concat(group.tasks));
    subject.groups = subject.groups.filter((x) => x !== group);
    subject.dropped = true; drawGrouping(subject);
  }
  // A duplicate goes by the removal the task list has, and its question; once it is gone the panel forgets it.
  async function removeGroupedTask(subject, id) {
    const task = (data.tasks || []).find((x) => x.id === id);
    if (!task || !(await removeTask(task))) return;
    if (!live(subject.mark) || state.panelSubject !== subject) return;
    if ((data.tasks || []).some((x) => x.id === id)) return;   // the save failed: the task is still there
    subject.groups.forEach((group) => { group.tasks = group.tasks.filter((x) => x !== id); });
    subject.pool = subject.pool.filter((x) => x !== id);
    delete subject.same[id]; delete subject.info[id]; delete subject.after[id];
    drawGrouping(subject);
  }
  function groupedTaskRow(subject, id, group) {
    const info = subject.info[id], row = el("li", "ws-group-task"), locked = subject.busy || !editable();
    const words = el("span", "ws-group-task-text", cutWords(info.text, 90)); words.title = info.text;
    row.append(el("span", "ws-group-task-n", t("第 {n} 条", { n: info.number })), words);
    if (info.report_title) row.append(el("span", "ws-agent-quiet", t("报告：{title}", { title: cutWords(info.report_title, 50) })));
    if ((info.asked_of || []).length > 1) row.append(el("span", "ws-agent-quiet", t("同一个问题，分别交给了 {agents}", { agents: info.asked_of.map((who) => who || t("没有指定负责人")).join(t("、")) })));
    const prior = subject.after[id];
    if (prior && group && subject.info[prior] && group.tasks.indexOf(prior) >= 0) row.append(el("span", "ws-agent-quiet", t("接在第 {n} 条之后", { n: subject.info[prior].number })));
    if (subject.same[id]) {
      row.append(el("span", "ws-agent-quiet", t("和第 {n} 条一样", { n: subject.same[id] })));
      const remove = keyed(named(button(t("移除"), () => removeGroupedTask(subject, id)), t("移除：{task}", { task: cutWords(info.text, 40) })), "group-remove:" + id);
      remove.disabled = locked; row.append(remove);
    }
    const move = keyed(named(el("select", "form-input"), t("移动：{task}", { task: cutWords(info.text, 40) })), "group-move:" + id);
    const here = el("option", null, t("移到…")); here.value = ""; move.append(here);
    subject.groups.filter((other) => other !== group).forEach((other) => { const option = el("option", null, groupWords(other)); option.value = other.id; move.append(option); });
    if (group) { const none = el("option", null, t("不归入任何组")); none.value = "\u0000none"; move.append(none); }
    move.value = ""; move.disabled = locked;
    move.addEventListener("change", () => { if (move.value) moveGroupedTask(subject, id, move.value === "\u0000none" ? "" : move.value); });
    row.append(move);
    return row;
  }
  function groupCard(subject, group) {
    const card = el("section", "ws-group"), locked = subject.busy || !editable(), member = data.mode === "member";
    card.dataset.group = group.id;
    const number = keyed(input("text"), "group-number:" + group.id), date = keyed(input("text"), "group-date:" + group.id), name = keyed(input("text"), "group-name:" + group.id);
    number.value = group.number; date.value = group.date; name.value = group.description;
    const numberField = field(t("编号"), number), dateField = field(t("日期"), date), nameField = field(t("尝试的描述"), name);
    numberField.className += " ws-field-number"; dateField.className += " ws-field-day"; nameField.className += " ws-field-describe";
    [number, date, name].forEach((box) => { box.disabled = locked; });
    const keep = keyed(button(t("确认这一组"), () => confirmGroup(subject, group), "btn btn-small"), "group-confirm:" + group.id);
    keep.disabled = locked || !group.tasks.length;
    number.addEventListener("input", () => { group.number = number.value; group.typed = true; group.edited = true; });
    date.addEventListener("input", () => { group.date = date.value; group.edited = true; });
    name.addEventListener("input", () => { group.description = name.value; group.edited = true; keep.disabled = subject.busy || !editable() || !group.tasks.length; });
    const head = el("div", "ws-group-head"); head.append(numberField, dateField, nameField); card.append(head);
    card.append(quiet(t("共同点：{term}", { term: group.term })), quiet(t("研究判断：{status}，结果先留空", { status: t(group.verdictWord) })));
    const list = el("ul", "ws-group-tasks");
    if (!group.tasks.length) card.append(quiet(t("这一组还没有任务。")));
    group.tasks.forEach((id) => list.append(groupedTaskRow(subject, id, group)));
    card.append(list);
    if (member) {
      const sentence = keyed(el("textarea", "form-input"), "group-sentence:" + group.id);
      sentence.rows = 2; sentence.value = group.sentence || t("把已有的任务归入新尝试 #{number}", { number: group.number.trim() }); sentence.disabled = locked;
      sentence.addEventListener("input", () => { group.sentence = sentence.value; group.edited = true; });
      card.append(field(t("一句话说明"), sentence));
    }
    const actions = el("div", "ws-agent-actions"), merge = keyed(named(el("select", "form-input"), t("合并这一组：{group}", { group: groupWords(group) })), "group-merge:" + group.id);
    const first = el("option", null, t("合并到…")); first.value = ""; merge.append(first);
    subject.groups.filter((other) => other !== group).forEach((other) => { const option = el("option", null, groupWords(other)); option.value = other.id; merge.append(option); });
    merge.value = ""; merge.disabled = locked || subject.groups.length < 2;
    merge.addEventListener("change", () => { const into = subject.groups.find((other) => other.id === merge.value); if (into) mergeGroups(subject, group, into); });
    const drop = keyed(button(t("不要这一组"), () => dropGroup(subject, group)), "group-drop:" + group.id);
    drop.disabled = locked;
    actions.append(keep, merge, drop); card.append(actions);
    if (group.note) { const says = el("p", "ws-status"); says.setAttribute("role", "status"); status(says, group.note.words, group.note.kind); card.append(says); }
    return card;
  }
  function drawGrouping(subject) {
    if (state.panelSubject !== subject) return;
    const snap = focusSnapshot(panelBody), body = el("div", "ws-groups");
    body.append(quiet(t("还没归入尝试的任务，按任务文字和报告标题里的版本号（例如 v0.2）或反复出现的同一个词分成几组。每一组都可以改名、调整任务、和别的组合并，或者不要；你确认哪一组，才会在尝试表里新增一行，并把这一组的任务归入它。文件不会移动。")));
    if (data.mode === "member") body.append(quiet(t("你是这个项目的成员：确认一组会先把新的一行写进你的工作副本，再生成提议，PI 批准后才进入正式版本。")));
    subject.done.forEach((node) => body.append(node));
    if (!subject.groups.length && !subject.pool.length && !subject.done.length) body.append(quiet(t("没有可以归入尝试的任务。")));
    subject.groups.forEach((group) => body.append(groupCard(subject, group)));
    if (subject.pool.length) {
      const rest = el("section", "ws-group");
      rest.append(el("h3", "ws-task-group", t("没有分到组的任务")));
      const list = el("ul", "ws-group-tasks");
      subject.pool.forEach((id) => list.append(groupedTaskRow(subject, id, null)));
      rest.append(list); body.append(rest);
    }
    panelBody.replaceChildren(body);
    restoreFocus(panelBody, snap);
  }
  async function confirmGroup(subject, group) {
    const mark = subject.mark, here = () => live(mark) && state.panelSubject === subject;
    if (subject.busy || !editable() || !here() || !group.tasks.length) return;
    const number = group.number.trim(), description = group.description.trim();
    if (!number || !description) { group.note = { words: t("请写下这条尝试的编号和描述。"), kind: "err" }; drawGrouping(subject); return; }
    const ids = group.tasks.slice(), member = data.mode === "member";
    subject.busy = true; group.note = { words: t("正在保存…") }; drawGrouping(subject);
    let response;
    try {
      // The attempt table the first group is written into is made first, as the new-attempt form makes it (a project with none).
      if (!subject.table) { await apiPost("/api/attempts/initialize", { project: project() }); subject.table = true; }
      const body = { project: project(), tasks: ids, attempt: { number: number, date: group.date.trim(), description: description } };
      // a task that continues another of this group goes after it; one whose earlier task is not in the group has no `after`
      const follows = {}; ids.forEach((id) => { if (subject.after[id] && ids.indexOf(subject.after[id]) >= 0) follows[id] = subject.after[id]; });
      if (Object.keys(follows).length) body.after = follows;
      response = await apiPost("/api/workspace/task-groups", body);
    } catch (err) {
      if (!here()) return;
      subject.busy = false; group.note = { words: errorWords(t("没能归入这一组。"), err), kind: "err" }; drawGrouping(subject);
      return;
    }
    if (!live(mark)) return;
    // What the page holds of the project follows what was written, as after any task save.
    data.tasks = response.tasks; data.tasks_revision = response.tasks_revision; data.tasks_officialness = response.tasks_officialness || data.tasks_officialness;
    data.changed_task_reports = response.changed_task_reports || [];
    if (!(data.attempts || []).some((a) => a.id === response.attempt.id)) (data.attempts = data.attempts || []).push({ id: response.attempt.id, label: response.attempt.label });
    renderTasks();
    if (typeof pollGeneration === "function") pollGeneration();
    if (!here()) return;
    subject.busy = false;
    const moved = response.moved || [], line = el("div", "ws-group-done");
    subject.groups = subject.groups.filter((x) => x !== group);
    subject.pool = subject.pool.filter((x) => moved.indexOf(x) < 0);
    moved.forEach((id) => { delete subject.same[id]; delete subject.info[id]; delete subject.after[id]; });
    renumberGroups(subject, number);   // the row is in the table, whether or not the tasks could be moved into it
    if (!response.linked) {
      line.append(el("p", "ws-status err", t("尝试 #{number} 已经写进尝试表，但没能重新读取它，任务还没有归入。稍后在任务的「属于哪条尝试」里选这一条。", { number: number })));
    } else if (member) {
      line.append(el("p", "ws-status ok", t("已写进你的工作副本：尝试 #{number}，{n} 个任务归入了它。提议还没有发送。", { number: number, n: moved.length })));
      const says = el("div", "notice-status"), host = el("div", "compose-result");
      line.append(says, host);
      const sentence = (group.sentence || "").trim() || t("把已有的任务归入新尝试 #{number}", { number: number });
      if (typeof ROTeam !== "undefined" && ROTeam.composeInto) ROTeam.composeInto(host, { kind: "attempt_new", sentence: sentence, number: number }, says);
    } else {
      line.append(el("p", "ws-status ok", t("已新增尝试 #{number}，{n} 个任务归入了它。", { number: number, n: moved.length })));
    }
    subject.done.push(line);
    drawGrouping(subject);
  }
  async function openGrouping() {
    if (!editable() || !mayReplacePanel()) return;
    const mark = stamp();
    // Unsaved work: a group typed in or rearranged and not confirmed yet, or a group dropped (agreeing to leave clears it).
    const subject = { kind: "task-groups", mark: mark, busy: false, table: true, info: {}, same: {}, after: {}, biggest: 0, groups: [], pool: [], done: [], dropped: false,
      get dirty() { return this.dropped || this.groups.some((group) => group.edited); },
      set dirty(on) { if (!on) { this.dropped = false; this.groups.forEach((group) => { group.edited = false; }); } } };
    folderSubject = subject; state.panelSubject = subject;
    panelTitle.textContent = t("把这些任务归入尝试"); panelSubtitle.textContent = ""; panelBody.replaceChildren(); openPanel();
    const note = el("p", "ws-status"); panelBody.append(note); status(note, t("载入中…"));
    try {
      const plan = await apiGet("/api/workspace/task-groups?project=" + encodeURIComponent(mark.project));
      if (!live(mark) || state.panelSubject !== subject) return;
      subject.table = plan.table !== false;
      (plan.tasks || []).forEach((task) => { subject.info[task.id] = task; });
      (plan.duplicates || []).forEach((pair) => { subject.same[pair.task] = pair.number; });
      subject.groups = (plan.groups || []).map((group) => ({ id: group.id, term: group.term, number: group.attempt.number, date: group.attempt.date,
        description: group.attempt.description, verdictWord: (group.verdict_status || {}).label || "", tasks: group.tasks.slice(), sentence: "", note: null, edited: false, typed: false }));
      (plan.groups || []).forEach((group) => Object.assign(subject.after, group.after || {}));
      // the first group carries the next free number of the table (the engine numbers them from there)
      if (subject.groups.length) subject.biggest = Math.max(0, wholeNumber(subject.groups[0].number) - 1);
      subject.pool = (plan.ungrouped || []).slice();
      drawGrouping(subject);
    } catch (err) { if (live(mark) && state.panelSubject === subject) status(note, errorWords(t("没能读取任务的分组。"), err), "err"); }
  }

  // -- 交回: a handback, read in one place (DESIGN.md 21.2) -----------------------------------------------------------
  // What an agent handed back with `researchos_complete_task`: the headline, the key numbers each with its file (the
  // engine marks a value its file does not hold), what is undone, what only the researcher can decide, the agent's
  // suggested assessment, then what it handed over and the report in full. The engine arranges it (`GET
  // /api/workspace/handback`); this shows it in the side panel -- from 项目首页, the attempt's panel or the route map's
  // card -- and tells the engine that this person has opened it (`POST .../opened`: kept on this Mac, not in the
  // project). 「确认这个判断」 writes the attempt's research assessment (`POST .../confirm`: the write the attempt's panel
  // uses, with the history saying the person confirmed it, and whether they took the suggestion unchanged); a member's
  // is written into their working copy and proposed (12.4). Like the grouping panel it is a `folderSubject`: leaving it
  // with a half-made assessment asks first.
  const HANDBACK_WRITERS = ["single", "pi", "member"];
  function hbPart(title) { const part = el("section", "ws-hb-part"); if (title) part.append(el("h3", "ws-hb-title", title)); return part; }
  function hbWriter(subject) { return HANDBACK_WRITERS.indexOf(subject.view.mode) >= 0; }
  function hbWho(view) { return view.run ? [view.run.label, view.run.by_name ? t("{name} 指派", { name: view.run.by_name }) : "", whenWords(view.run.ended)].filter(Boolean).join(" · ") : ""; }
  function hbStatusChip(view, word) {
    const known = (view.statuses || []).find((item) => item.status === word);
    const tone = typeof verdictTone === "function" ? verdictTone({ status: known ? known.code : "other" }) : "plain";
    return el("span", "status-label tone-" + tone, t(word));
  }
  function numberWords(value) { return window.RODesk && typeof window.RODesk.formatNumber === "function" ? window.RODesk.formatNumber(value) : String(value); }
  function hbNumbers(view) {
    const part = hbPart(t("关键数字")), list = el("ul", "ws-hb-numbers");
    view.key_numbers.forEach((item) => {
      // 21.8: a long number is shown to four significant digits; the value as the agent wrote it (and as the file and the
      // number check have it) is on hover and in the record
      const row = el("li", "ws-hb-number"), shown = numberWords(item.value), value = el("code", "ws-hb-value", shown);
      if (shown !== item.value) value.title = item.value;
      row.append(el("span", "ws-hb-what", item.what), value,
        named(button(item.file, () => { if (typeof openFilePanel === "function") openFilePanel(item.file); }, "btn btn-small ws-file-link"), t("打开：{file}", { file: item.file })));
      if (item.check === "missing") row.append(el("span", "ws-status err ws-hb-flag", t("在所引文件里没找到这个数")));
      else if (item.check === "unreadable") row.append(el("span", "ws-agent-quiet ws-hb-flag", t("所引文件读不了，没法核对")));
      list.append(row);
    });
    part.append(list);
    return part;
  }
  // The chooser of the assessment (21.3): the project's statuses as buttons with the chosen one pressed, and the
  // reason -- the shared one (RODesk.statusChooser), prefilled --, and beside it the reason 14.3 asks when the status
  // turns the route abandoned. A suggestion whose status the project no longer has leaves nothing chosen, and 保存 waits.
  function hbChooser(subject) {
    const view = subject.view, choose = subject.choose, box = el("div", "ws-hb-choose"), locked = subject.busy;
    const abandons = () => (view.statuses.find((item) => item.status === choose.status) || {}).abandons === true;
    // a member's abandonment becomes a proposal, which is refused without the route's result (12.4): the row may have none
    const asksResult = () => view.mode === "member" && abandons() && !((view.attempt && view.attempt.result) || "").trim();
    const abandonBox = el("div", "ws-hb-abandon"), abandon = keyed(el("textarea", "form-input"), "hb-abandon");
    abandon.rows = 2; abandon.value = choose.abandon; abandon.disabled = locked;
    const result = keyed(el("textarea", "form-input"), "hb-result"), resultBox = el("div", "ws-hb-result");
    result.rows = 2; result.value = choose.result || ""; result.disabled = locked;
    resultBox.append(field(t("结果（放弃一条路线时必填）"), result));
    resultBox.classList.toggle("hidden", !asksResult());
    abandonBox.append(field(view.mode === "member" ? t("理由（放弃一条路线时必填）") : t("放弃的理由（必填）"), abandon),
      quiet(t("理由会写进结果一栏，这条路线当时的依据也会冻结下来。")));
    abandonBox.classList.toggle("hidden", !abandons());
    const save = keyed(button(t("保存"), () => {
      if (abandons() && !choose.abandon.trim()) { subject.note = { words: t("放弃一条路线时必须写明理由"), kind: "err" }; drawHandback(subject); return; }
      if (asksResult() && !(choose.result || "").trim()) { subject.note = { words: t("放弃一条路线时，结果和理由都必须填写"), kind: "err" }; drawHandback(subject); return; }
      confirmHandback(subject, choose.status, choose.reason, abandons() ? choose.abandon : "", asksResult() ? choose.result : "");
    }, "btn btn-primary btn-small"), "hb-save");
    const picker = window.RODesk.statusChooser({ statuses: view.statuses, status: choose.status, reason: choose.reason, label: t("研究判断"),
      onChange: (now) => {
        choose.status = now.status(); choose.reason = now.reason(); choose.edited = true;
        abandonBox.classList.toggle("hidden", !abandons()); resultBox.classList.toggle("hidden", !asksResult());
        save.disabled = subject.busy || !choose.status;
      } });
    keyed(picker.statusEl, "hb-status"); keyed(picker.reasonEl, "hb-reason");
    picker.picks.forEach((pick) => keyed(pick, "hb-pick-" + pick.dataset.status));
    picker.setDisabled(locked);
    choose.status = picker.status(); // a status the project has since taken out is none chosen: the person picks one
    abandonBox.classList.toggle("hidden", !abandons()); resultBox.classList.toggle("hidden", !asksResult());
    abandon.addEventListener("input", () => { choose.abandon = abandon.value; choose.edited = true; });
    result.addEventListener("input", () => { choose.result = result.value; choose.edited = true; });
    save.disabled = locked || !choose.status;
    const cancel = keyed(button(t("取消"), () => { subject.choose = null; subject.note = null; drawHandback(subject); }), "hb-cancel");
    cancel.disabled = locked;
    const actions = el("div", "ws-agent-actions"); actions.append(save, cancel);
    const group = el("div", "ws-field"); group.append(el("span", "ws-field-label", t("研究判断")), picker.el);
    box.append(group, resultBox, abandonBox, actions);
    return box;
  }
  function hbOpenChooser(subject, abandoning) {
    const view = subject.view, now = view.attempt && view.attempt.verdict != null ? view.attempt : null, sug = view.suggested;
    // without a suggestion: the assessment the attempt has, read from its cell (a cell with no known marker is 其它, all of it the reason)
    const have = now ? window.RODesk.assessment.parse(now.verdict, view.statuses) : { status: (view.statuses.find((item) => item.other) || view.statuses[0]).status, reason: "" };
    subject.choose = sug ? { status: sug.status, reason: sug.reason, abandon: abandoning ? sug.reason : "", result: "", edited: false }
      : { status: have.status, reason: have.reason, abandon: "", result: "", edited: false };
    subject.note = null; drawHandback(subject); focusKey(panelBody, "hb-reason");
  }
  // What the assessment part says once a person has confirmed (now, or before): who, which status, and how it came.
  function hbConfirmedLine(view, confirmed) {
    const line = el("p", "ws-hb-confirmed"), by = confirmed.by_label || confirmed.by;
    line.append(el("span", null, t("{who} 已确认：", { who: by })), hbStatusChip(view, confirmed.status));
    if (view.suggested) line.append(el("span", "ws-agent-quiet", confirmed.adopted ? t("采纳了代理的建议") : t("改动了代理的建议")));
    return line;
  }
  // The attempt the handback belongs to (its number and title) and the research assessment it has now -- what 「确认这个判断」
  // replaces, read from the cell as the engine gave it (a cell with no known marker is 其它, all of it the reason).
  function hbAttempt(view) {
    const attempt = view.attempt, part = hbPart(t("尝试 #{number}：{description}", { number: attempt.number, description: attempt.label }));
    part.classList.add("ws-hb-attempt");
    if (attempt.verdict != null) {
      const have = window.RODesk.assessment.parse(attempt.verdict, view.statuses), line = el("p", "ws-hb-now");
      line.append(el("span", "ws-agent-quiet", t("现在的研究判断：")), hbStatusChip(view, have.status));
      if (have.reason) line.append(el("span", "ws-hb-reason", have.reason));
      part.append(line);
    }
    return part;
  }
  // A run that went out of bounds after handing in (18.1 item 4: the done mark stays the agent's and the run is what
  // warns) says so before anything is confirmed on its word.
  function hbWarning(view) {
    const run = view.run;
    if (!run || run.state !== "out_of_bounds") return null;
    const box = el("div", "ws-hb-warning"); box.setAttribute("role", "status");
    box.append(runLabel(run));
    if (run.violations > 0) box.append(el("p", "ws-agent-oob", t("有 {n} 处越界改动。确认这个判断之前，请先看一下运行日志。", { n: run.violations })));
    return box;
  }
  function hbAssessment(subject) {
    const view = subject.view, sug = view.suggested, part = hbPart(t("代理建议")), confirmed = view.confirmed, writer = hbWriter(subject);
    if (sug) {
      const line = el("p", "ws-hb-suggested");
      line.append(hbStatusChip(view, sug.status));
      if (sug.reason) line.append(el("span", "ws-hb-reason", sug.reason));
      part.append(line);
    } else part.append(quiet(t("代理没有给出建议。")));
    if (confirmed) { part.append(hbConfirmedLine(view, confirmed)); return part; }
    if (!writer) {
      part.append(quiet(view.mode === "none" ? t("你还不是这个项目的成员：请 PI 把你加入团队，然后同步") : t("现在说不清你在这个项目里的角色，研究判断先不能改。")));
      return part;
    }
    if (!view.task || !view.attempt) {
      // a task whose attempt row was removed or renumbered by hand names an attempt the table no longer has
      part.append(quiet(view.task && view.task.attempt ? t("这份交回的任务所属的尝试已经不在尝试表里了，请先把任务归入一条尝试。")
        : t("这份交回的任务还没有归入尝试，所以没有可写的研究判断。")));
      return part;
    }
    if (view.run_active) { part.append(quiet(t("这个任务又有一次运行在进行，等它交回以后再确认这一份。"))); return part; }
    const own = (view.mode === "member" && view.attempt.officialness) || {};
    if (own.new_row && own.state === "approved") { part.append(quiet(t("这一行已经批准，正式版本里有了：先同步，再改它。"))); return part; }
    if (view.mode === "member") part.append(quiet(t("你是这个项目的成员：确认会先写进你的工作副本，再生成提议；PI 批准后才进入正式版本。")));
    if (sug && !sug.known) part.append(quiet(t("这个状态已经不在项目的选项里，请用「改一下」另选一个。")));
    if (subject.choose) part.append(hbChooser(subject));
    else if (subject.later) part.append(quiet(t("先不判断。之后可以在尝试的面板里写研究判断。")));
    else {
      const actions = el("div", "ws-agent-actions");
      if (sug) {
        // a suggestion that abandons the route still asks for the reason first (14.3): the chooser opens with it, the agent's words in it
        const ok = keyed(button(t("确认这个判断"), () => (sug.abandons ? hbOpenChooser(subject, true) : confirmHandback(subject, sug.status, sug.reason, "")), "btn btn-primary btn-small"), "hb-confirm");
        ok.disabled = subject.busy || !sug.known; actions.append(ok);
        const change = keyed(button(t("改一下"), () => hbOpenChooser(subject)), "hb-change"); change.disabled = subject.busy; actions.append(change);
        const later = keyed(button(t("先不判断"), () => { subject.later = true; drawHandback(subject); }), "hb-later"); later.disabled = subject.busy; actions.append(later);
      } else {
        const write = keyed(button(t("写下研究判断"), () => hbOpenChooser(subject), "btn btn-primary btn-small"), "hb-write"); write.disabled = subject.busy; actions.append(write);
      }
      part.append(actions);
    }
    return part;
  }
  // After a confirmation: the three ways on (21.2).
  function hbNext(subject) {
    const view = subject.view, actions = el("div", "ws-agent-actions ws-hb-next"), writer = hbWriter(subject);
    if (writer && view.task && view.attempt) {
      actions.append(keyed(button(t("再派一个任务"), () => hbNewTask(subject), "btn btn-primary btn-small"), "hb-next-task"),
        keyed(button(t("从这条开新尝试"), () => hbNewAttempt(subject)), "hb-next-attempt"));
    }
    actions.append(keyed(button(t("这条先到这里"), () => { if (typeof closePanel === "function") closePanel(); }), "hb-next-stop"));
    return actions;
  }
  function hbNewTask(subject) {
    const view = subject.view;
    if (typeof closePanel === "function") closePanel();
    // the new task continues this one, and its research context is this handback's own record
    const choice = { attempt: view.attempt.id, after: view.task.id, context: subject.report };
    if (typeof startTaskOnHome === "function") startTaskOnHome(choice);
    else startTask(choice);
  }
  // The new-attempt form (the attempt's table, 21.3), prefilled from the agent's `attempt_draft`, the new row 源自 this
  // attempt: the form writes the line with the mapping write it has always used for a parent it was given.
  // An agent that gave no draft still wrote a next step (Section 20): that is where the new attempt starts, cut to the
  // length an attempt's description is written with elsewhere (the grouping panel's 500 characters).
  const ATTEMPT_TEXT_LIMIT = 500;
  function cutWords(text, limit) {
    const chars = Array.from(String(text || "").trim());
    return chars.length <= limit ? chars.join("") : chars.slice(0, limit - 1).join("").trimEnd() + "…";
  }
  function hbNewAttempt(subject) {
    const view = subject.view, draft = view.attempt_draft || {};
    const parents = [view.attempt.id].concat((draft.parents || []).filter((id) => id !== view.attempt.id));
    if (typeof window.ROWorkspace.onCreateAttempt !== "function") return;
    const description = (draft.description || "").trim() ? draft.description : cutWords(view.next_step, ATTEMPT_TEXT_LIMIT);
    window.ROWorkspace.onCreateAttempt({ description: description, result: draft.result || "", parents: parents }, () => {});
  }
  function hbOutputs(view) {
    const part = hbPart(t("输出")), kinds = outputKinds(), seen = [];
    view.outputs.forEach((item) => { if (seen.indexOf(item.kind) < 0) seen.push(item.kind); });
    seen.forEach((kind) => {
      const group = el("div", "ws-hb-outputs"), list = el("ul", "ws-outputs-files");
      group.append(el("h4", "ws-outputs-kind", kinds[kind] || kind));
      view.outputs.filter((item) => item.kind === kind).forEach((item) => {
        const li = el("li", "ws-hb-output"), name = item.title || item.path, gone = item.state === "gone", cloud = item.state === "cloud";
        if (gone || cloud) li.append(el("span", null, name));
        else li.append(named(button(name, () => { if (typeof openFilePanel === "function") openFilePanel(item.path); }, "btn btn-small ws-file-link"), t("打开：{file}", { file: item.path })));
        if (name !== item.path) li.append(el("code", "ws-output-path", item.path));
        if (item.state === "changed") li.append(el("span", "ws-agent-quiet", t("文件已改动")));
        if (gone) li.append(el("span", "ws-agent-quiet", t("文件已不在")));
        if (cloud) li.append(el("span", "ws-agent-quiet", t("文件只在云端")));
        if (!gone && !cloud && kind === "figure" && /\.(png|jpe?g|gif|webp)$/i.test(item.path)) {
          const preview = el("div", "ws-output-preview"), image = el("img", "ws-output-figure");
          image.src = "/api/workspace/asset?path=" + encodeURIComponent(item.path); image.alt = name; image.loading = "lazy";
          preview.append(image); li.append(preview);
        }
        list.append(li);
      });
      group.append(list); part.append(group);
    });
    return part;
  }
  // A report written without the 21.2 fields still has Section 20's context: its finding stands where the headline would,
  // then its limitations and next step when it has them, under the names the key points editor gives them.
  function hbContext(view) {
    return [["finding", t("发现与解释")], ["limits", t("局限与待确认")], ["next_step", t("下一步")]].filter(([key]) => view[key]).map(([key, title]) => {
      const part = hbPart(title); part.classList.add("ws-hb-" + key.replace("_", "-"));
      part.append(el("p", "ws-hb-text", view[key]));
      return part;
    });
  }
  // The report in full, folded -- unfolded when the handback has nothing else to read (no 21.2 field, no finding).
  function hbReport(subject, unfolded) {
    const view = subject.view, box = el("details", "ws-hb-report");
    box.open = !!unfolded;
    box.append(disclosureSummary(t("报告")));
    const body = el("div", "ws-preview"), keep = unchecked;
    renderMarkdown(body, view.text, view.report); unchecked = keep;
    box.append(body);
    const tools = el("div", "ws-agent-actions");
    tools.append(keyed(button(t("编辑报告"), () => hbEdit(subject), "ws-hb-link"), "hb-edit"));
    box.append(tools);
    return box;
  }
  function hbEdit(subject) {
    if (typeof closePanel === "function") closePanel();
    if (typeof openResultOnHome === "function") openResultOnHome(subject.report);
    else openReport(subject.report);
  }
  function hbRunLog(subject) {
    // the ways out of the panel are links, not buttons (21.8: a link that only navigates is a text link)
    const view = subject.view, link = keyed(button(t("运行日志"), () => {
      if (typeof closePanel === "function") closePanel();
      if (typeof openRunLogOnHome === "function") openRunLogOnHome(view.task.id); else showRunLog(view.task.id);
    }, "ws-hb-link"), "hb-log");
    return link;
  }
  function drawHandback(subject) {
    if (state.panelSubject !== subject || !subject.view) return;
    const view = subject.view, snap = focusSnapshot(panelBody), body = el("div", "ws-hb");
    panelTitle.textContent = view.title; panelSubtitle.textContent = hbWho(view);
    if (panelTitle.classList) panelTitle.classList.add("plain-title");
    if (subject.back) body.append(keyed(button(t("返回"), () => { if (subject.back) subject.back(); }, "ws-hb-link"), "hb-back"));
    const empty = !view.headline && !view.key_numbers.length && !view.undone && !view.decide.length && !view.suggested;
    const warning = hbWarning(view);
    if (warning) body.append(warning);
    if (view.context_error) body.append(el("p", "ws-status err", t("这份报告的要点读不出来。")));
    else if (empty && !view.confirmed) body.append(quiet(t("这份交回没有附上要点。")));
    if (view.headline) body.append(el("p", "ws-hb-headline", view.headline));
    else hbContext(view).forEach((part) => body.append(part));
    if (view.key_numbers.length) body.append(hbNumbers(view));
    if (view.undone) { const part = hbPart(t("没做完的")); part.append(el("p", "ws-hb-text", view.undone)); body.append(part); }
    if (view.decide.length) {
      const part = hbPart(t("需要你决定的")), list = el("ul", "ws-hb-decide");
      view.decide.forEach((question) => list.append(el("li", null, question)));
      part.append(list); body.append(part);
    }
    if (view.attempt) body.append(hbAttempt(view));
    if (view.suggested || view.confirmed || (view.task && view.attempt) || subject.done) {
      const part = hbAssessment(subject);
      if (subject.note) { const says = el("p", "ws-status"); says.setAttribute("role", "status"); status(says, subject.note.words, subject.note.kind); part.append(says); }
      if (subject.done) part.append(hbDone(subject));
      body.append(part);
    }
    if (view.confirmed) body.append(hbNext(subject));
    if (view.outputs.length) body.append(hbOutputs(view));
    body.append(hbReport(subject, (!!view.context_error || (empty && !view.confirmed)) && !view.finding));
    if (view.task && view.run) body.append(hbRunLog(subject));
    panelBody.replaceChildren(body);
    restoreFocus(panelBody, snap);
  }
  // The answer of a confirmation: what was written, what could not be recorded, and for a member the proposal to send.
  // The PI's and a single researcher's write has 撤销 on its line (18.3), as the panel's save has -- the cell goes back as the
  // file held it, once the row still reads as this write left it; the confirmation itself stays recorded in the report's
  // key points (it is a record, 21.2), and the line says so. An abandonment is not offered back: its basis was frozen.
  function hbDone(subject) {
    const done = subject.done, response = done.response, box = el("div", "ws-hb-done");
    if (response.mode === "member") {
      box.append(el("p", "ws-status ok", t("已写进你的工作副本：尝试 #{number} 的研究判断。提议还没有发送。", { number: response.number })));
      box.append(done.says, done.host);
    } else if (subject.undone) {
      // the confirmation went with it (the buttons are back); only when the report's record could not be taken back is it still there
      box.append(el("p", "ws-status ok", subject.undone.kept
        ? t("已撤销：尝试 #{number} 的研究判断改回了确认之前的样子。报告里仍记着你确认过。", { number: response.number })
        : t("已撤销，研究判断回到了 {status}", { status: t(subject.undone.status) })));
    } else {
      const line = el("p", "ws-status ok", response.written ? t("已写入尝试 #{number} 的研究判断。", { number: response.number }) : t("尝试 #{number} 的研究判断本来就是这样，只记下了你的确认。", { number: response.number }));
      if (subject.undo) {
        const undo = keyed(button(t("撤销"), () => hbUndo(subject), "btn btn-small"), "hb-undo");
        undo.disabled = subject.busy; line.append(" ", undo);
      }
      box.append(line);
    }
    if (response.ingest_error) box.append(el("p", "ws-status err", t("已写入并备份，但重扫失败")));
    if (response.history_error) box.append(el("p", "ws-status err", t("研究判断已写入，但修改历史没能记下这次确认。")));
    if (response.context_error) box.append(el("p", "ws-status err", t("研究判断已写入，但报告的要点里没能记下这次确认。")));
    return box;
  }
  // The handback read again, for a page whose attempt moved under it (a confirmation refused as `handback_moved`): the
  // attempt and its assessment as they are now, what the person was choosing left as it is.
  async function hbReread(subject) {
    const mark = subject.mark, here = () => live(mark) && state.panelSubject === subject;
    try {
      const fresh = await apiGet("/api/workspace/handback?report=" + encodeURIComponent(subject.report) + "&project=" + encodeURIComponent(mark.project));
      if (!here()) return;
      fresh.unopened = false; subject.view = fresh;
    } catch (err) { if (!here()) return; }
    subject.busy = false; subject.undo = null;
    subject.note = { words: t("这一行在你打开这份交回以后被改过：上面是它现在的研究判断，请看一眼再确认。"), kind: "err" };
    drawHandback(subject);
  }
  async function confirmHandback(subject, statusWord, reason, abandonReason, resultWords) {
    const mark = subject.mark, here = () => live(mark) && state.panelSubject === subject;
    if (subject.busy || !here() || !hbWriter(subject)) return;
    subject.busy = true; subject.note = { words: t("正在保存…") }; drawHandback(subject);
    const view = subject.view, sent = { project: mark.project, report: subject.report, status: statusWord, reason: reason, abandon_reason: abandonReason };
    // the cell the page showed: a row that has moved since is refused (18.3), not written over
    if (view.attempt && typeof view.attempt.verdict === "string") sent.was = view.attempt.verdict;
    if (resultWords && resultWords.trim()) sent.result = resultWords.trim();
    let response;
    try {
      response = await apiPost("/api/workspace/handback/confirm", sent);
    } catch (err) {
      if (!here()) return;
      if (err && err.state === "workspace_handback_moved") { await hbReread(subject); return; }
      subject.busy = false; subject.note = { words: errorWords(t("没能确认这个判断。"), err), kind: "err" }; drawHandback(subject);
      return;
    }
    if (!live(mark)) return;
    if (typeof pollGeneration === "function") pollGeneration();
    if (!here()) return;
    subject.busy = false; subject.note = null; subject.choose = null; subject.undone = false;
    view.confirmed = Object.assign({ by_label: response.by_label }, response.confirmed);
    if (response.written && view.attempt && response.before) view.attempt.verdict = response.before.now_text;   // the assessment the attempt has now
    const member = response.mode === "member";
    subject.undo = response.written && !member && !response.decision && !response.ingest_error && response.before && window.RODesk && window.RODesk.atOnce
      ? window.RODesk.atOnce.undoOf({ cells: { verdict: response.before } }, { number: response.number, fields: { verdict: response.cell } }, { verdict: response.before.was_text }) : null;
    subject.done = { response: response, says: el("div", "notice-status"), host: el("div", "compose-result") };
    drawHandback(subject);
    if (member && typeof ROTeam !== "undefined" && ROTeam.composeInto) {
      // The sentence is the stored Chinese one whatever the screen says: it goes into a proposal file on the shared drive,
      // which nothing in the person's language may change (13.1). A row of the member's own that the official version does
      // not have yet is proposed whole as a new attempt, replacing the proposal still waiting for it (as the panel does);
      // a row of the official table as a change of its verdict, with the report's record, which says who confirmed it.
      const own = (view.attempt && view.attempt.officialness) || {}, draft = { sentence: response.sentence.zh, number: response.number };
      if (own.new_row) {
        draft.kind = "attempt_new";
        if (own.state === "proposed" && own.proposal) draft.supersedes = own.proposal;
      } else {
        draft.kind = "verdict_change";
        if (response.context_path && !response.context_error) draft.only = [response.context_path];
      }
      if (abandonReason && abandonReason.trim()) draft.reason = abandonReason.trim();
      ROTeam.composeInto(subject.done.host, draft, subject.done.says);
    }
  }
  // 撤销 on the line of a written confirmation: the same way as the panel's (the preview, then the write).
  async function hbUndo(subject) {
    const mark = subject.mark, here = () => live(mark) && state.panelSubject === subject, back = subject.undo;
    if (!back || subject.busy || !here()) return;
    subject.busy = true; subject.note = { words: t("正在写入…") }; drawHandback(subject);
    let result;
    try {
      result = await window.RODesk.atOnce.undo(back);
    } catch (err) {
      if (!here()) return;
      subject.busy = false; subject.note = { words: errorWords(t("写入失败，文件没有被修改"), err), kind: "err" }; drawHandback(subject);
      return;
    }
    if (!live(mark)) return;
    if (typeof pollGeneration === "function") pollGeneration();
    if (!here()) return;
    subject.busy = false; subject.undo = null;
    if (result.state !== "done") {
      subject.note = { words: result.state === "abandons" ? t("撤销会让这条路线重新放弃，需要写明理由：请把研究判断改回已放弃，再保存。") : t("这一行之后又改过，不能撤销。"), kind: "err" };
      drawHandback(subject);
      return;
    }
    const view = subject.view;
    if (view.attempt && typeof back.text.verdict === "string") view.attempt.verdict = back.text.verdict;
    // the status the row is back to, said on the line that replaces 撤销
    const now = typeof back.text.verdict === "string" ? window.RODesk.assessment.parse(back.text.verdict, view.statuses) : null;
    subject.undone = { status: now ? now.status : "", kept: false };
    await hbUnconfirm(subject);
  }
  // 撤销 also takes the confirmation back: the report's context loses `assessment_confirmed` (the one writer is the page's,
  // 21.2), so that the handback offers its three buttons again and does not say a person confirmed what the row no longer says.
  async function hbUnconfirm(subject) {
    const mark = subject.mark, here = () => live(mark) && state.panelSubject === subject;
    subject.busy = true; subject.note = { words: t("正在写入…") }; drawHandback(subject);
    try {
      await apiPost("/api/workspace/handback/unconfirm", { project: mark.project, report: subject.report });
    } catch (err) {
      if (!here()) return;
      subject.busy = false; subject.undone.kept = true;
      subject.note = { words: errorWords(t("研究判断已改回，但报告的要点里没能撤销这次确认。"), err), kind: "err" };
      drawHandback(subject);
      return;
    }
    if (!live(mark)) return;
    if (typeof pollGeneration === "function") pollGeneration();
    if (!here()) return;
    subject.busy = false; subject.note = null; subject.view.confirmed = null;
    drawHandback(subject);
  }
  // Marked opened for this person (kept on this Mac), and the cards that count what waits are drawn again.
  async function hbMarkOpened(subject) {
    if (!subject.view.unopened) return;
    try { await apiPost("/api/workspace/handback/opened", { project: subject.mark.project, report: subject.report }); } catch (err) { return; }
    if (!live(subject.mark)) return;
    subject.view.unopened = false;
    if (typeof refreshCurrentView === "function") refreshCurrentView();
  }
  async function openHandback(report, options) {
    if (typeof mayReplacePanel === "function" && !mayReplacePanel()) return false;
    const mark = stamp();
    const subject = { kind: "handback", mark: mark, report: report, view: null, busy: false, choose: null, note: null, done: null, later: false,
      back: (options && options.back) || null,
      get dirty() { return !!this.choose && !!this.choose.edited; },
      set dirty(on) { if (!on && this.choose) this.choose.edited = false; } };
    folderSubject = subject; state.panelSubject = subject;
    panelTitle.textContent = t("交回"); panelSubtitle.textContent = ""; panelBody.replaceChildren(); openPanel();
    const note = el("p", "ws-status"); panelBody.append(note); status(note, t("载入中…"));
    try {
      const view = await apiGet("/api/workspace/handback?report=" + encodeURIComponent(report) + "&project=" + encodeURIComponent(mark.project));
      if (!live(mark) || state.panelSubject !== subject) return false;
      subject.view = view; drawHandback(subject);
      hbMarkOpened(subject);
    } catch (err) {
      if (live(mark) && state.panelSubject === subject) status(note, errorWords(t("没能读取这份交回。"), err), "err");
      return false;
    }
    return true;
  }
  // 「运行日志」 (the link at the foot of a handback): the task's row on 项目首页 with its run's log open.
  function showRunLog(taskId) {
    if (!data || !ui) return false;
    // the log sits inside the row's disclosure: a task that is not among the newest three is closed until the person
    // opens it, and would show a closed title and no log
    const disclosure = taskDisclosures[taskId];
    taskOpen[taskId] = true;
    if (disclosure) { disclosure.open = true; if (disclosure.node) disclosure.node.open = true; }
    agentState(taskId).log = true; updateAgentSections();
    if (latestRun(taskId)) ensureDetails(stamp());
    const row = rowNodes[taskId];
    if (row && typeof row.scrollIntoView === "function") row.scrollIntoView();
    return true;
  }
  // 「打开结果」 on a task whose latest run handed its report back is the handback; any other report is opened to write in.
  function openResult(task) {
    const run = latestRun(task.id);
    if (run && run.report && run.report === task.report && !run.reverted) return openHandback(task.report);
    return openReport(task.report);
  }

  // -- the owner control: people and this Mac's agents ------------------------------------------
  function ownerChoices() {
    const list = [{ value: "", words: t("不指定") }];
    if (people.me) list.push({ value: people.me, words: t("{name}（我）", { name: people.me }) });
    people.team.forEach((name) => { if (!list.some((c) => c.value === name)) list.push({ value: name, words: name }); });
    return list;
  }
  function fillOwnerOptions(select) {
    const current = taskDraft.custom ? OWNER_OTHER : taskDraft.owner;
    const people_ = ownerChoices(), agents = ((agentInfo && agentInfo.agents) || []).map((a) => ({ value: a.owner, words: a.label }));
    const choices = people_.slice();
    // A name typed by hand earlier, or an agent this Mac does not have, stays selectable while it is the value.
    if (current && current !== OWNER_OTHER && ![...people_, ...agents].some((c) => c.value === current)) choices.push({ value: current, words: ownerLabel(current) });
    choices.push({ value: OWNER_OTHER, words: t("其他人…") }, ...agents);
    if (agentInfo && !agents.length) choices.push({ value: OWNER_SETTINGS, words: t("设置 AI 执行者…") });
    select.replaceChildren(...choices.map((c) => { const option = el("option", null, c.words); option.value = c.value; return option; }));
    select.value = current;
  }
  function refreshOwnerOptions() {
    if (ui && ui.ownerSelect) fillOwnerOptions(ui.ownerSelect);
    Object.keys(metaNodes).forEach((id) => { const task = data && (data.tasks || []).find((x) => x.id === id); if (task) setMeta(task, metaNodes[id]); });
  }
  function chooseOwner(select) {
    const value = select.value, wasCustom = taskDraft.custom;
    if (value === OWNER_SETTINGS) { fillOwnerOptions(select); openSettings(); return; }
    if (value === OWNER_OTHER) {
      taskDraft = Object.assign({}, taskDraft, { custom: true, owner: "" }); renderTasks();
      if (ui.ownerName) ui.ownerName.focus();
      return;
    }
    taskDraft = Object.assign({}, taskDraft, { custom: false, owner: value });
    if (wasCustom) renderTasks();
  }

  // -- what the page knows of the Mac, the people and the runs ----------------------------------
  function loadAgentContext(mark) {
    syncRuns(mark, { reload: false });
    if (!agentInfo) loadAgentInfo(mark);
    loadPeople(mark);
  }
  // GET /api/agents asks each program for its version (up to 10 s), so nothing here waits for it.
  async function loadAgentInfo(mark, again) {
    if (agentInfoLoading || (agentInfo && !again)) return;
    agentInfoLoading = true;
    try {
      const info = await apiGet("/api/agents");
      if (!live(mark)) return;
      agentInfo = info; agentInfoError = null;
    } catch (err) {
      if (!live(mark)) return;
      agentInfoError = err;
    } finally { if (live(mark)) agentInfoLoading = false; }
    refreshOwnerOptions(); renderSettings(); updateAgentSections();
  }
  // /api/me and /api/share are the page's shared context (desk.js: the sidebar, the desk and the panels read them
  // once per project, one read in flight at a time, DESIGN.md 18.4 item 7). The page takes from it only what it
  // already holds: the context the desk module has made for this project's time (`RODesk.context()`: its `epoch` is
  // this one's and its `mode` was filled). It never waits for a load that is still going, since /api/proposals and
  // /api/share read the shared folder with no time limit and a folder that hangs would hold who I am with it.
  // Without a context ready (no desk module, a load going or failed, another project's time) the page reads for
  // itself, as it always did. The answer is copied, since the desk fills the same object again; `shared` is false
  // when the context did not ask for the shared folder (`mode` is not a team's role).
  const TEAM_ROLES = ["pi", "member", "none"];
  function readyContext() {
    const desk = typeof window !== "undefined" ? window.RODesk : null;
    if (!desk || typeof desk.context !== "function") return null;
    let c = null;
    try { c = desk.context(); } catch (err) { return null; }
    const project = typeof state !== "undefined" ? (state.currentId || state.currentPath || "") : "";  // desk.js's projectKey()
    return c && c.mode !== null && c.epoch === appEpoch() && c.project === project ? { me: c.me, share: c.share, shared: TEAM_ROLES.includes(c.mode) } : null;
  }
  // Who an owner can be: me and, in a team, its members. /api/share reads the shared folder and may be
  // slow, so it never holds the page, nor who I am; a failure leaves the list as it is.
  async function loadPeople(mark) {
    if (people.loaded || people.loading) return;
    people.loading = true;
    try {
      let context = readyContext();
      const mine = context ? context.me : await apiGet("/api/me").catch(() => null);
      if (!live(mark)) return;
      const me = mine && mine.me && typeof mine.me.name === "string" && mine.me.name.trim() ? mine.me : null;
      people.me = me ? me.name.trim() : null; refreshOwnerOptions();
      if (["pi", "member"].includes(data && data.mode)) {
        context = context || readyContext();  // it may have been made while /api/me was read
        const share = context && context.shared ? context.share : await apiGet("/api/share").catch(() => null);
        if (!live(mark)) return;
        const seen = new Set(people.me ? [people.me] : []);
        people.team = ((share && share.team) || []).filter((member) => member && typeof member.name === "string" && member.name.trim() && !(me && member.id === me.id))
          .map((member) => member.name.trim()).filter((name) => !seen.has(name) && seen.add(name));
      }
      people.loaded = true; refreshOwnerOptions();
    } finally { if (live(mark)) people.loading = false; }
  }
  function applyRuns(fresh) {
    runsInfo = { runs: Array.isArray(fresh && fresh.runs) ? fresh.runs : [], active: (fresh && fresh.active) || null };
  }
  function mergeRun(run) {
    if (!run || !run.id) return;
    const index = runsInfo.runs.findIndex((r) => r.id === run.id);
    if (index < 0) runsInfo.runs.unshift(run); else runsInfo.runs[index] = run;
    if (isActiveState(run.state)) runsInfo.active = run.id; else if (runsInfo.active === run.id) runsInfo.active = null;
  }
  // Reads the project's runs, redraws the rows, reloads the page when a run has ended (its files and the
  // task's done mark moved) and keeps polling while one goes. opts: before (ids active before an action),
  // reload (true: always, false: never, otherwise when a run ended).
  async function syncRuns(mark, opts) {
    const before = (opts && opts.before) || activeIds();
    let fresh;
    try { fresh = await apiGet("/api/agents/runs"); } catch (err) {
      if (!live(mark)) return false;
      // A run goes on while the laptop sleeps or the engine restarts: keep asking, ever more slowly (17.3).
      pollFails += 1; offline = anyActive(); showConnection(); schedulePoll();
      return false;
    }
    if (!live(mark)) return false;
    pollFails = 0; offline = false; showConnection(); applyRuns(fresh);
    const ended = before.some((id) => !activeIds().includes(id));
    ensureDetails(mark); updateAgentSections();
    const reload = opts && opts.reload;
    if (reload === true || (reload !== false && ended)) reloadAfterRun();
    schedulePoll();
    return ended;
  }
  function reloadAfterRun() {
    if (host) load(host, true);
    if (typeof pollGeneration === "function") pollGeneration();
  }
  function onWorkspace() { return typeof currentView === "undefined" || currentView === "workspace"; }
  function windowHidden() { return typeof document !== "undefined" && document.hidden === true; }
  function schedulePoll() {
    if (pollStopped || pollTimer !== null || !host || !anyActive() || !onWorkspace()) return;
    if (windowHidden()) { pollHeld = true; return; }
    const mark = stamp();
    pollTimer = setTimeout(() => {
      pollTimer = null;
      if (!live(mark) || pollStopped || !onWorkspace()) return;
      if (windowHidden()) { pollHeld = true; return; }
      syncRuns(mark, {});
    }, pollDelay());
  }
  // 2 s while reads work; 4, 8, 16 and then 30 s while they fail.
  function pollDelay() { return pollFails ? Math.min(POLL_MAX_MS, POLL_MS * Math.pow(2, pollFails)) : POLL_MS; }
  function stopPolling() { if (pollTimer !== null) clearTimeout(pollTimer); pollTimer = null; pollFails = 0; pollHeld = false; }
  // The window is hidden: the timer of the next poll is taken away, and one poll is made once the window is shown
  // again (it ends the poll chain by itself when no run is going any more).
  function onVisibility() {
    if (windowHidden()) {
      if (pollTimer !== null) { clearTimeout(pollTimer); pollTimer = null; pollHeld = true; }
      return;
    }
    if (!pollHeld) return;
    pollHeld = false;
    if (pollStopped || !host || !data || pollTimer !== null || !onWorkspace()) return;
    syncRuns(stamp(), {});
  }
  if (typeof document !== "undefined" && typeof document.addEventListener === "function") document.addEventListener("visibilitychange", onVisibility);
  // 停止服务: the service is gone and nothing more is asked of it, not by a timer and not by a poll that was in flight.
  function stopForGood() { pollStopped = true; stopPolling(); }
  // The page calls this when the project home is shown again after another section: polling stops
  // when the workspace is not the view on screen.
  function resumePolling() {
    if (pollStopped || !host || !data || pollTimer !== null) return;
    syncRuns(stamp(), {});
  }
  function ensureDetails(mark) {
    Object.keys(agentUiState).forEach((taskId) => {
      const run = agentUiState[taskId].log && latestRun(taskId), known = run && details[run.id];
      // A log that could not be read is asked for again, on the next poll and whenever it is reopened.
      if (run && (!known || known.error || known.state !== run.state || isActiveState(run.state))) fetchDetail(run, mark);
    });
  }
  async function fetchDetail(run, mark) {
    if (detailPending[run.id]) return;
    detailPending[run.id] = true;
    try {
      const fresh = await apiGet("/api/agents/runs/" + encodeURIComponent(run.id));
      if (!live(mark)) return;
      details[run.id] = { state: run.state, data: fresh };
    } catch (err) {
      if (!live(mark)) return;
      details[run.id] = { state: run.state, error: err };
    } finally { delete detailPending[run.id]; }
    updateAgentSections();
  }

  // -- a task's row ---------------------------------------------------------------------------------
  // The reply form is drawn only while the button that opens it is offered (17.8): 回答 on a run waiting for an
  // answer, 打回 on a finished one. After that it is gone, with what was typed in it.
  function replyKind(run, st) {
    if (!st.reply || !run || !run.can_reply) return null;
    if (st.reply === "answer") return outcomeOf(run) === "needs_input" ? "answer" : null;
    return REPLY_FROM.includes(outcomeOf(run)) ? "send_back" : null;
  }
  // The undo list stays while the run it was opened for can still be undone (18.1 item 5); a box ticked or cleared
  // changes the list in place and is not part of the signature (a redraw would lose the place in a long list).
  function undoKind(run, st) { return !!st.undo && !!run && run.can_revert === true && st.undo.run === run.id; }
  function agentSig(task) {
    const run = latestRun(task.id), st = agentState(task.id), d = run && details[run.id];
    const u = undoKind(run, st) ? [st.undo.run, st.undo.plan || null, st.undo.error ? String(st.undo.error.engineMessage || st.undo.error.message || "") : null] : null;
    return JSON.stringify([task.owner, task.done, task.report || "", run, st.log, replyKind(run, st), u, !!agentBusy[task.id], agentNotes[task.id] || null,
      d ? [d.state, d.error ? String(d.error.message || "") : null, d.data || null] : null, anyActive(), may(),
      agentInfo ? [agentInfo.mcp, (agentInfo.agents || []).map((a) => a.owner + a.label)] : null]);
  }
  // A task being carried out cannot be edited, removed or ticked off under the agent.
  function lockRow(task) {
    const controls = rowControls[task.id], run = latestRun(task.id);
    if (!controls) return;
    const locked = !tasksEditable() || taskBusy || taskConflict || (!!run && isActiveState(run.state));
    controls.forEach((node) => { node.disabled = locked; });
  }
  function replyFormOf(section) { return descendants(section).find((x) => x.classList.contains("ws-agent-reply")) || null; }
  // Draws a task's section again. A reply box the person is typing in stays the node it is (a new one would
  // lose the cursor and the input method): the rest of the section is swapped around it. Otherwise focus goes
  // back to the same control by its key.
  function replaceSection(task, old, fresh) {
    const active = activeIn(old), oldForm = replyFormOf(old), newForm = replyFormOf(fresh);
    if (active && isTyping(active) && oldForm && newForm && within(oldForm, active)
        && oldForm.dataset.kind === newForm.dataset.kind && oldForm.dataset.run === newForm.dataset.run) {
      const kids = Array.from(fresh.children), at = kids.indexOf(newForm);
      Array.from(old.children).forEach((kid) => { if (kid !== oldForm) kid.remove(); });
      kids.forEach((kid, index) => { if (index < at) oldForm.before(kid); else if (index > at) old.append(kid); });
      old.dataset.sig = fresh.dataset.sig;
      return;
    }
    const snap = focusSnapshot(old);
    old.replaceWith(fresh); sections[task.id] = fresh;
    restoreFocus(fresh, snap);
  }
  function updateAgentSections() {
    if (!ui || !ui.tasks) return;
    (data.tasks || []).forEach((task) => {
      lockRow(task);
      drawTaskOutputs(task);
      const old = sections[task.id];
      if (!old) {
        // The runs of a project arrive after its tasks are drawn; the section goes above 任务输出, under the task's words.
        if (wordsNodes[task.id] && (parseAgent(task.owner) || latestRun(task.id))) {
          sections[task.id] = agentSection(task);
          if (outputNodes[task.id]) outputNodes[task.id].before(sections[task.id]); else wordsNodes[task.id].append(sections[task.id]);
        }
        return;
      }
      if (old.dataset.sig === agentSig(task)) return;
      replaceSection(task, old, agentSection(task));
    });
  }
  function runLabel(run) {
    const partly = run.reverted && run.revert_skipped > 0;
    const [words, tone] = partly ? [() => t("部分撤回"), "gray"] : run.reverted ? [() => t("已撤回本次运行"), "gray"] : STATE_LABELS[run.state] || [null, "plain"];
    // a state this page does not know reads as a generic word, never as the code the engine stores
    return el("span", "status-label tone-" + tone + (run.state === "out_of_bounds" && !run.reverted ? " oob" : ""), words ? words() : t("状态未知"));
  }
  // The state word, the question and what went wrong are one polite live region (role=status), and so is a note,
  // so a screen reader hears a run end or a request be refused without the person looking for it (12.11).
  function agentSection(task) {
    const box = el("div", "ws-agent"), run = latestRun(task.id), st = agentState(task.id), agent = parseAgent(task.owner);
    const reply = replyKind(run, st);
    if (st.reply && !reply) { st.reply = null; st.text = ""; }
    if (st.undo && !undoKind(run, st)) st.undo = null;
    box.dataset.sig = agentSig(task);
    const busy = !!agentBusy[task.id], allowed = may() && !busy, active = !!run && isActiveState(run.state);
    const k = (name) => "agent:" + task.id + ":" + name, who = { task: task.text };
    const line = el("div", "ws-agent-line"), live = el("div", "ws-agent-status");
    live.setAttribute("role", "status");
    if (run) {
      line.append(runLabel(run));
      if (run.changes && !active) line.append(el("span", "ws-agent-meta", t("改动了 {n} 个文件", { n: run.changes })));
    }
    if (line.childElementCount) live.append(line);
    if (run && outcomeOf(run) === "needs_input" && run.question) live.append(el("p", "ws-agent-question", run.question));
    if (run && run.violations > 0) live.append(el("p", "ws-agent-oob", t("有 {n} 处越界改动，详见运行记录。", { n: run.violations })));
    if (run && run.error) live.append(errorBlock(run, st));
    box.append(live);
    const actions = el("div", "ws-agent-actions");
    if (agent && !task.done && (!run || (!active && !(outcomeOf(run) === "needs_input" && run.can_reply)))) {
      const go = keyed(named(button(t("运行"), () => startRun(task, agent)), t("运行：{task}", who)), k("run"));
      go.disabled = !allowed || anyActive();
      if (allowed && anyActive()) go.title = t("这个项目里还有一次运行没有结束。");
      actions.append(go);
    }
    if (run && run.can_stop) { const stop = keyed(named(button(t("停止"), () => stopRun(task, run)), t("停止：{task}", who)), k("stop")); stop.disabled = busy; actions.append(stop); }
    if (run) {
      const log = keyed(named(button(t("运行记录"), () => toggleLog(task)), t("运行记录：{task}", who)), k("log")); log.setAttribute("aria-expanded", String(st.log)); actions.append(log);
    }
    if (run && outcomeOf(run) === "needs_input" && run.can_reply) {
      const answer = keyed(named(button(t("回答"), () => toggleReply(task, "answer")), t("回答：{task}", who)), k("answer")); answer.setAttribute("aria-expanded", String(reply === "answer"));
      answer.disabled = !allowed; actions.append(answer);
    }
    if (run && REPLY_FROM.includes(outcomeOf(run)) && run.can_reply) {
      const back = keyed(named(button(t("打回"), () => toggleReply(task, "send_back")), t("打回：{task}", who)), k("send_back")); back.setAttribute("aria-expanded", String(reply === "send_back"));
      back.disabled = !allowed; actions.append(back);
    }
    if (run && run.can_revert) {
      const undo = keyed(named(button(t("撤回本次运行"), () => revertRun(task, run)), t("撤回本次运行：{task}", who)), k("undo"));
      undo.setAttribute("aria-expanded", String(!!st.undo)); undo.disabled = !allowed; actions.append(undo);
    }
    if (task.report) actions.append(keyed(named(button(t("打开结果"), () => openResult(task)), t("打开结果：{task}", who)), k("report")));
    if (actions.childElementCount) box.append(actions);
    const note = agentNotes[task.id];
    if (note) box.append(noteBlock(note));
    if (run && st.undo) box.append(undoPanel(task, run, st));
    if (run && reply) box.append(replyForm(task, run, st, reply));
    if (run && st.log) box.append(runDetails(run, details[run.id], st, task));
    return box;
  }
  function errorBlock(run, st) {
    const box = el("div", "ws-agent-error"), code = run.error && run.error.code;
    box.append(el("p", "ws-status err", byCode(ERROR_WORDS, code, () => t("这次运行没有成功。"))));
    if (run.error && run.error.detail) box.append(more(st, "error", t("详情"), String(run.error.detail)));
    return box;
  }
  function installBlock() {
    const box = el("div", "ws-install");
    MCP_INSTALL.forEach(([caption, command]) => { box.append(el("span", "ws-agent-quiet ws-install-caption", caption()), el("code", "ws-install-cmd", command)); });
    return box;
  }
  function mcpWords() { return t("运行 AI 执行者需要 mcp 组件，这个 Research OS 里还没有安装。按你安装 Research OS 的方式，运行下面其中一条命令，然后重新打开 Research OS："); }
  // The files an undo left alone, each with why (17.6): in the note after the click and in the run log after a reload.
  function skippedList(skipped) {
    const list = el("ul", "ws-agent-files");
    skipped.forEach((item) => {
      const li = el("li"); li.append(el("code", null, item.path));
      li.append(" " + byCode(REVERT_SKIPPED, item.reason, () => t("没有改动这个文件")));
      list.append(li);
    });
    return list;
  }
  function noteBlock(note) {
    const box = el("div", "ws-agent-note");
    box.setAttribute("role", "status");
    box.append(el("p", "ws-status" + (note.kind ? " " + note.kind : ""), note.words));
    if (note.install) box.append(installBlock());
    if (note.skipped && note.skipped.length) {
      box.append(el("p", "ws-agent-quiet", t("这些文件没有动：")), skippedList(note.skipped));
    }
    return box;
  }
  function setNote(taskId, words, kind, extra) {
    agentNotes[taskId] = words ? Object.assign({ words: words, kind: kind || "" }, extra || {}) : null;
  }

  // -- 任务输出: a task's files in its row (DESIGN.md 19.6) -------------------------------------------------------
  // Under the run controls of a task with a result record: the files registered for it by kind (the 输出目录 panel's
  // words), each opening in the file panel and each with 移除登记 (the 过程文件 group is its count until opened, any other
  // past 8 files has 「另外 N 个」: 19.12 item 2); the new files that nobody registered, of every run of the task that is
  // not undone (19.12 item 7); the task's process folder (19.12 item 1) with the files in it now. The engine's
  // `task_outputs` (one answer with the page) has the
  // facts; the place sits inside the task's details, so it collapses with them, and is drawn again only when what it
  // shows has changed, so a poll leaves it and its focus alone.
  function outputKinds() {
    return { code: t("代码"), data: t("数据"), figure: t("图"), table: t("表"), literature: t("文献"), document: t("文稿与报告"), variable: t("变量草稿"), work: t("过程文件") };
  }
  // 13.5: English separates thousands with commas, Chinese stays as it is; a counted noun still picks its form by n.
  function countText(n) { return typeof window !== "undefined" && window.ROI18N && window.ROI18N.lang === "en" ? String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",") : n; }
  function outputsOf(task) { return (data && task.report && data.task_outputs && data.task_outputs[task.id]) || null; }
  function outputsSig(task) {
    const run = latestRun(task.id), opened = outputOpen[task.id];
    // what is shown past a group's first files changes the drawing; whether the 过程文件 disclosure is open does not (the browser keeps it)
    return JSON.stringify([outputsOf(task), outputNotes[task.id] || null, !!outputBusy[task.id], editable(), !!run && isActiveState(run.state), run ? run.id : null,
      (opened && opened.all) || {}]);
  }
  function outputRow(task, item, locked) {
    const li = el("li", "ws-output"), name = item.title || item.path, gone = item.state === "gone", cloud = item.state === "cloud";
    // a registration whose file is gone stays here, to be removed (19.6): there is nothing to open, nothing to preview;
    // so does one whose file is there but only in the cloud, which cannot be read on this Mac
    if (gone || cloud) li.append(el("span", null, name));
    else li.append(keyed(button(name, () => { if (typeof openFilePanel === "function") openFilePanel(item.path); }), "output-open:" + task.id + ":" + item.path));
    if (name !== item.path) li.append(el("code", "ws-output-path", item.path));
    if (item.state === "changed") li.append(el("span", "ws-agent-quiet", t("文件已改动")));
    if (gone) li.append(el("span", "ws-agent-quiet", t("文件已不在")));
    if (cloud) li.append(el("span", "ws-agent-quiet", t("文件只在云端")));
    const remove = keyed(named(button(t("移除登记"), () => removeRegistration(task, item)), t("移除登记：{file}", { file: name })), "output-remove:" + task.id + ":" + item.path);
    remove.disabled = locked; li.append(remove);
    if (!gone && !cloud && item.kind === "figure" && /\.(png|jpe?g|gif|webp)$/i.test(item.path)) {
      const preview = el("div", "ws-output-preview"), image = el("img", "ws-output-figure");
      image.src = "/api/workspace/asset?path=" + encodeURIComponent(item.path); image.alt = name; image.loading = "lazy";
      preview.append(image); li.append(preview);
    }
    return li;
  }
  // 登记… on the line of the count (DESIGN.md 19.5, 19.6, 19.12 item 7): the list of the new files the count is about,
  // those of every run of the task on this Mac that is not undone (a question and its answer are two runs of one
  // conversation), for review. `info.filing` has the count; the engine's listing, asked for the task, has the paths.
  function registerNewFilesHook(task, info, line) {
    const run = latestRun(task.id);
    if (!run) return;
    const open = keyed(named(button(t("登记…"), () => openBackfill({ task: task.id })), t("登记：{task}", { task: task.text })), "output-register:" + task.id);
    open.disabled = !editable() || !!outputBusy[task.id] || isActiveState(run.state);
    line.append(open);
  }
  // How many files of a group the row shows before 「另外 N 个」 (DESIGN.md 19.12 item 2); the 过程文件 group, once opened,
  // shows them all.
  const OUTPUT_SHOWN = 8;
  function outputState(task) { return outputOpen[task.id] || (outputOpen[task.id] = { process: false, all: {} }); }
  // The rest of a group, opened: drawn again with every file, and the focus goes to the first file that was not shown.
  function showAllOutputs(task, kind, first) {
    outputState(task).all[kind] = true;
    drawTaskOutputs(task);
    if (!focusKey(outputNodes[task.id], "output-open:" + task.id + ":" + first.path)) focusKey(outputNodes[task.id], "output-remove:" + task.id + ":" + first.path);
  }
  // The block, or nothing when the task has nothing to show: no file registered, no new file, no process folder.
  function taskOutputsBlock(task) {
    const info = outputsOf(task);
    if (!info) return null;
    const entries = info.entries || [], unregistered = info.filing && info.filing.counts ? Number(info.filing.counts.unregistered) || 0 : 0;
    if (!entries.length && !info.error && !unregistered && !info.work_folder) return null;
    const run = latestRun(task.id), locked = !editable() || !!outputBusy[task.id] || (!!run && isActiveState(run.state));
    const box = el("div", "ws-outputs");
    box.append(el("h3", "ws-outputs-title", t("任务输出")));
    if (info.error) box.append(el("p", "ws-status err", t("输出清单暂时无法读取。")));
    else if (!entries.length) box.append(quiet(t("还没有登记的文件。")));
    const kinds = outputKinds();
    Object.keys(kinds).forEach((kind) => {
      const group = entries.filter((item) => item.kind === kind);
      if (!group.length) return;
      const opened = outputState(task), list = el("ul", "ws-outputs-files");
      const count = el("span", "ws-agent-quiet", t("{n} 个文件", { n: countText(group.length) }));
      if (kind === "work") {
        // 19.12 item 2: process files are many and rarely read, so the group is its count until the person opens it
        // (and stays open, or closed, when the row is drawn again)
        const fold = el("details", "ws-outputs-process"), summary = keyed(disclosureSummary(kinds[kind]), "output-group:" + task.id + ":work");
        summary.classList.add("ws-outputs-kind"); summary.append(count);
        group.forEach((item) => list.append(outputRow(task, item, locked)));
        fold.open = !!opened.process;
        fold.addEventListener("toggle", () => { opened.process = fold.open; });
        fold.append(summary, list);
        box.append(fold);
        return;
      }
      // any other group past OUTPUT_SHOWN files shows its first files and 「另外 N 个」 for the rest
      const shown = opened.all[kind] ? group : group.slice(0, OUTPUT_SHOWN), head = el("p", "ws-outputs-kind");
      head.append(el("span", null, kinds[kind]), count);
      shown.forEach((item) => list.append(outputRow(task, item, locked)));
      box.append(head, list);
      if (shown.length < group.length) {
        const rest = countText(group.length - shown.length);
        box.append(keyed(named(button(t("另外 {n} 个", { n: rest }), () => showAllOutputs(task, kind, group[shown.length])), t("另外 {n} 个：{kind}", { n: rest, kind: kinds[kind] })),
          "output-more:" + task.id + ":" + kind));
      }
    });
    const note = outputNotes[task.id];
    if (note) { const says = el("p", "ws-status err", note); says.setAttribute("role", "status"); box.append(says); }
    if (unregistered > 0) {
      const line = el("p", "ws-outputs-line");
      line.append(el("span", null, t("{n} 个新文件未登记", { n: countText(unregistered) })));
      registerNewFilesHook(task, info, line);
      box.append(line);
    }
    if (info.work_folder) {
      const files = Number(info.work_files) || 0, line = el("p", "ws-outputs-line");
      line.append(el("span", null, t("过程文件夹")), el("code", "ws-output-path", info.work_folder),
        el("span", "ws-agent-quiet", info.work_files_more ? t("超过 {n} 个文件", { n: countText(files) }) : t("{n} 个文件", { n: countText(files) })));
      box.append(line);
    }
    return box;
  }
  function drawTaskOutputs(task) {
    const holder = outputNodes[task.id];
    if (!holder) return;
    const sig = outputsOf(task) || outputNotes[task.id] ? outputsSig(task) : "";
    if (holder.dataset.sig === sig) return;
    const snap = focusSnapshot(holder), block = taskOutputsBlock(task);
    holder.dataset.sig = sig;
    holder.replaceChildren(...(block ? [block] : []));
    restoreFocus(holder, snap);
  }
  // 移除登记: asked first, then the registration goes (the file stays), and the answer is read again for the counts.
  async function removeRegistration(task, item) {
    if (outputBusy[task.id] || !editable()) return;
    if (!window.confirm(t("移除这个文件的登记吗？文件本身不会改动。"))) return;
    const mark = stamp();
    outputBusy[task.id] = true; outputNotes[task.id] = null; drawTaskOutputs(task);
    try {
      await apiPost("/api/outputs/unregister", { project: project(), report: task.report, path: item.path });
    } catch (err) {
      if (!live(mark)) return;
      outputBusy[task.id] = false; outputNotes[task.id] = errorWords(t("没能移除登记。"), err); drawTaskOutputs(task);
      focusKey(outputNodes[task.id], "output-remove:" + task.id + ":" + item.path);   // the button that was pressed is still there
      return;
    }
    if (!live(mark)) return;
    const info = outputsOf(task);
    if (info) info.entries = (info.entries || []).filter((x) => x.path !== item.path);
    try {
      const fresh = await apiGet("/api/workspace");
      if (live(mark) && data && fresh.project === project() && fresh.task_outputs) data.task_outputs = fresh.task_outputs;
    } catch (err) { /* the page reads the project again on its own; what was removed is already gone from the row */ }
    if (!live(mark)) return;
    outputBusy[task.id] = false; drawTaskOutputs(task);
    if (typeof pollGeneration === "function") pollGeneration();
  }

  // -- 补登记以往运行的文件: the review list (DESIGN.md 19.5, 19.10 item 4) ----------------------------------------
  // The engine lists the files that runs on this Mac created and nobody registered, by task and run, each with the kind
  // it suggests (`GET /api/outputs/backfill`). The person reviews them in the side panel: a box and a kind for each file
  // (the box is on when a kind is suggested; 不登记 is no kind and leaves the box off), a disclosure for each task and a
  // select-all in it. 登记所选 registers each task's checked files with one call to /api/outputs/register, then reads the
  // list again; a task whose call failed says so and keeps what the person chose. The list is the panel's subject like
  // the folders form (`folderSubject`), so leaving it after a change asks first.
  const BACKFILL_OPEN = 40;   // up to this many files every task starts open; with more, the person opens the ones to review
  // scope: { task } for the runs of one task (a task row's 登记…, 19.12 item 7), { run } for one run, or none for every run.
  function backfillUrl(scope) {
    const query = [];
    if (data && data.id) query.push("project=" + encodeURIComponent(data.id));
    if (scope && scope.task) query.push("task=" + encodeURIComponent(scope.task));
    if (scope && scope.run) query.push("run=" + encodeURIComponent(scope.run));
    return "/api/outputs/backfill" + (query.length ? "?" + query.join("&") : "");
  }
  // Each file knows the run that created it, which the registration records (19.12 item 4): the date shown for it
  // afterwards is that run's.
  function withRuns(listing) {
    ((listing && listing.groups) || []).forEach((group) => (group.runs || []).forEach((run) => (run.files || []).forEach((file) => { if (!file.run) file.run = run.id; })));
    return listing;
  }
  function backfillFiles(group) { return (group.runs || []).flatMap((run) => run.files || []); }
  // What the person has chosen for a file: its kind ("" is 不登记) and whether its box is on. A choice is made once, from
  // the suggestion, and kept when the list is read again.
  function backfillChoice(subject, file) {
    return subject.choices[file.path] || (subject.choices[file.path] = { kind: file.kind || "", on: !!file.kind });
  }
  function backfillTicked(subject, group) {
    return backfillFiles(group).filter((file) => { const choice = backfillChoice(subject, file); return choice.on && !!choice.kind; });
  }
  function backfillLocked(subject) { return subject.busy || !editable(); }
  // The counts, the pictures of the rows and the sentence under the buttons are drawn from `subject`, so that ticking a
  // box changes them and not the list: a long list keeps its place.
  function drawBackfill(subject) {
    const groups = (subject.listing && subject.listing.groups) || [], kinds = outputKinds();
    const body = el("div", "ws-backfill"), total = groups.reduce((n, group) => n + backfillFiles(group).length, 0);
    const syncs = [], repaints = [];
    const count = el("p", "ws-backfill-total"), says = el("p", "ws-status"), footer = el("div", "ws-agent-actions");
    says.setAttribute("role", "status");
    const register = keyed(button(t("登记所选"), () => registerBackfill(subject), "btn btn-primary btn-small"), "backfill-register");
    footer.append(register);
    function sync() {
      syncs.forEach((f) => f());
      const n = groups.reduce((sum, group) => sum + backfillTicked(subject, group).length, 0);
      count.textContent = t("已选 {n} 项，共 {total} 项", { n: countText(n), total: countText(total) });
      register.disabled = !n || backfillLocked(subject);
    }
    subject.lock = () => { repaints.forEach((f) => f()); sync(); };
    panelBody.replaceChildren(body);
    if (!groups.length) body.append(quiet(t("没有需要补登记的文件。")));
    else body.append(quiet(t("这些文件由以往的运行创建，还没有登记。勾选的文件会登记到各自的任务下，文件本身不会移动。")));
    groups.forEach((group) => {
      const files = backfillFiles(group), box = el("details", "res-draft"), summary = disclosureSummary(group.text || group.task);
      const tally = el("span", "ws-agent-quiet"), inner = el("div", "res-draft-body"), rows = {}, bulk = [];
      summary.append(tally);
      syncs.push(() => { tally.textContent = t("已选 {n} 项，共 {total} 项", { n: countText(backfillTicked(subject, group).length), total: countText(files.length) }); });
      box.open = subject.notes[group.task] ? true
        : Object.prototype.hasOwnProperty.call(subject.open, group.task) ? subject.open[group.task] : total <= BACKFILL_OPEN;
      if (subject.notes[group.task]) { const problem = el("p", "ws-status err", subject.notes[group.task]); problem.setAttribute("role", "status"); inner.append(problem); }
      function paint(file) {
        const row = rows[file.path], choice = backfillChoice(subject, file);
        if (!row) return;
        row.select.value = choice.kind; row.select.disabled = backfillLocked(subject);
        row.box.checked = choice.on && !!choice.kind; row.box.disabled = !choice.kind || backfillLocked(subject);
      }
      function touched(file) { subject.dirty = true; paint(file); sync(); }
      function fileRow(file) {
        const li = el("li", "ws-backfill-file"), label = el("label", "ws-undo-row"), tick = keyed(el("input"), "backfill:" + file.path);
        const kind = keyed(named(el("select", "form-input"), t("登记为：{file}", { file: file.path })), "backfill-kind:" + file.path);
        tick.type = "checkbox";
        tick.addEventListener("change", () => { backfillChoice(subject, file).on = tick.checked; touched(file); });
        Object.keys(kinds).forEach((name) => { const option = el("option", null, kinds[name]); option.value = name; kind.append(option); });
        const none = el("option", null, t("不登记")); none.value = ""; kind.append(none);
        kind.addEventListener("change", () => {
          const choice = backfillChoice(subject, file);
          choice.on = kind.value ? (choice.kind ? choice.on : true) : false;   // a kind for a file without one ticks it; 不登记 clears it
          choice.kind = kind.value; touched(file);
        });
        label.append(tick, el("code", null, file.path));
        rows[file.path] = { box: tick, select: kind };
        li.append(label, kind); paint(file);
        return li;
      }
      function setAll(on) {
        files.forEach((file) => { const choice = backfillChoice(subject, file); if (choice.kind) choice.on = on; paint(file); });
        subject.dirty = true; sync();
      }
      // The rows of a task are drawn when its disclosure is open, not before: a long list costs nothing until it is read.
      let filled = false;
      function fill() {
        if (filled || !box.open) return;
        filled = true;
        const all = keyed(button(t("全部勾选"), () => setAll(true)), "backfill-all:" + group.task);
        const none = keyed(button(t("全部取消勾选"), () => setAll(false)), "backfill-none:" + group.task);
        const actions = el("div", "ws-agent-actions");
        bulk.push(all, none); actions.append(all, none); inner.append(actions);
        (group.runs || []).forEach((run) => {
          const head = el("p", "ws-backfill-run"), list = el("ul", "ws-agent-files ws-undo-list");
          head.append(el("span", null, whenWords(run.date)), el("span", null, run.agent || ""));
          (run.files || []).forEach((file) => list.append(fileRow(file)));
          inner.append(head, list);
        });
        all.disabled = none.disabled = backfillLocked(subject);
      }
      repaints.push(() => { files.forEach(paint); bulk.forEach((node) => { node.disabled = backfillLocked(subject); }); });
      box.addEventListener("toggle", () => { subject.open[group.task] = box.open; fill(); });
      box.append(summary, inner); body.append(box);
      fill();
    });
    const more = Number(subject.listing && subject.listing.more) || 0;
    if (more > 0) body.append(quiet(t("另有 {n} 个没有列出。", { n: countText(more) })), quiet(t("登记已列出的文件后再打开，才会列出其余的。")));
    if (groups.length) body.append(count, footer);
    if (subject.status) status(says, subject.status.words, subject.status.kind);
    body.append(says);
    sync();
  }
  // The rows' 任务输出 and the other views after files were registered: the counts are read again, as removing a
  // registration does, and the page's poll is asked to look at the project.
  async function readOutputsAgain(mark) {
    try {
      const fresh = await apiGet("/api/workspace");
      if (live(mark) && data && fresh.project === project() && fresh.task_outputs) data.task_outputs = fresh.task_outputs;
    } catch (err) { /* the page reads the project again on its own */ }
    if (!live(mark)) return;
    ((data && data.tasks) || []).forEach(drawTaskOutputs);
    if (typeof pollGeneration === "function") pollGeneration();
  }
  async function registerBackfill(subject) {
    const mark = subject.mark, here = () => live(mark) && state.panelSubject === subject;
    if (subject.busy || !editable() || !here()) return;
    const chosen = ((subject.listing && subject.listing.groups) || []).map((group) => ({ group: group, files: backfillTicked(subject, group) })).filter((x) => x.files.length);
    if (!chosen.length) return;
    subject.busy = true; subject.notes = {}; subject.status = null; subject.lock();
    let done = 0, failed = 0;
    for (const entry of chosen) {
      try {
        await apiPost("/api/outputs/register", { project: project(), report: entry.group.report,
          items: entry.files.map((file) => Object.assign({ kind: backfillChoice(subject, file).kind, path: file.path }, file.run ? { run: file.run } : {})) });
        done += entry.files.length;
      } catch (err) {
        failed += 1; subject.notes[entry.group.task] = errorWords(t("没能登记这项任务的文件。"), err);
      }
      if (!live(mark)) break;   // the project changed: what was asked of the old one is done, the rest is not this project's
    }
    if (done && live(mark)) await readOutputsAgain(mark);
    if (!here()) return;
    subject.busy = false; subject.dirty = failed > 0;
    const lines = [];
    if (done) lines.push(t("已登记 {n} 个文件。", { n: countText(done) }));
    if (failed) lines.push(t("有 {n} 项任务的文件没能登记，选择都还在。", { n: countText(failed) }));
    subject.status = { words: lines.join("\n"), kind: failed ? "err" : "ok" };
    try {
      subject.listing = withRuns(await apiGet(backfillUrl(subject.scope)));
    } catch (err) { subject.status = { words: errorWords(t("没能读取要补登记的文件。"), err), kind: "err" }; }
    if (!here()) return;
    drawBackfill(subject);
    focusKey(panelBody, "backfill-register");
  }
  // scope: { task } to list the runs of that task (the task row's 登记…, 19.12 item 7), { run } for one run, or none for
  // every run (the 输出目录 panel's button).
  async function openBackfill(scope) {
    if (!mayReplacePanel()) return;
    const mark = stamp();
    const subject = { kind: "backfill", dirty: false, mark: mark, scope: scope || null, busy: false, listing: null, choices: {}, open: {}, notes: {}, status: null, lock: null };
    folderSubject = subject; state.panelSubject = subject;
    panelTitle.textContent = t("补登记以往运行的文件"); panelSubtitle.textContent = ""; panelBody.replaceChildren(); openPanel();
    const note = el("p", "ws-status"); panelBody.append(note); status(note, t("载入中…"));
    try {
      const listing = withRuns(await apiGet(backfillUrl(subject.scope)));
      if (!live(mark) || state.panelSubject !== subject) return;
      subject.listing = listing; drawBackfill(subject);
    } catch (err) { if (live(mark) && state.panelSubject === subject) status(note, errorWords(t("没能读取要补登记的文件。"), err), "err"); }
  }

  // -- the actions ---------------------------------------------------------------------------------
  // A size as the confirmation names it: whole megabytes, a tenth of a gigabyte from 1 GB up.
  function sizeWords(bytes) {
    const mb = bytes / (1024 * 1024);
    return mb >= 1024 ? (mb / 1024).toFixed(1) + " GB" : Math.round(mb) + " MB";
  }
  // The files over 16 MB the engine names (at most ten, then a count): a run cannot be undone for them (18.1 item 5).
  function largeFilesWords(big) {
    const named = big && Array.isArray(big.largest) ? big.largest : [];
    if (!named.length) return "";
    const count = Math.max(Number(big.count) || 0, named.length), more = count - named.length;
    const names = named.map((f) => t("{path}（{size}）", { path: f.path, size: sizeWords(f.size) })).join(t("、"));
    const lines = [t("项目里有 {n} 个文件超过 16 MB。运行前不会备份它们，被改动或删除后就恢复不了：{names}", { n: count, names: names })];
    if (more > 0) lines.push(t("另有 {n} 个没有列出。", { n: more }));
    if (big.partial) lines.push(t("项目文件太多，这份清单只查了前 50,000 个文件。"));
    return lines.join("\n");
  }
  // 17.1 and 18.1 item 5: before a person's first run of an agent -- where the project's content goes, that the agent
  // changes files directly, what an undo cannot bring back, and which large files of the project are among them.
  let confirming = false;
  async function confirmAgent(agent) {
    if (!agent || confirmedAgents.has(agent.owner)) return true;
    if (confirming) return false;
    confirming = true;
    try {
      const mark = stamp();
      let big = null;
      // The engine names the files; the confirmation is shown without them when it cannot be asked.
      try { big = await apiGet("/api/agents/large-files"); } catch (err) { big = null; }
      if (!live(mark)) return false;
      const parts = [(PRIVACY[agent.runner] || PRIVACY.opencode)(),
        t("运行时它会直接修改项目里的文件，不会先问你。运行之后可以撤回它在项目里的改动，但这些撤不回：安装的软件、下载的内容、git 里的操作，以及项目文件夹以外的一切。")];
      const large = largeFilesWords(big);
      if (large) parts.push(large);
      parts.push(t("要继续吗？"));
      if (!window.confirm(parts.join("\n\n"))) return false;
      confirmedAgents.add(agent.owner);
      return true;
    } finally { confirming = false; }
  }
  // One request for one task at a time. opts: failed (the words when it is refused), after (on success),
  // reload (see syncRuns), done (the note on success, from the response).
  async function agentAction(task, call, opts) {
    if (agentBusy[task.id]) return;
    const mark = stamp(), before = activeIds(), snap = focusSnapshot(sections[task.id]);
    // The control that was pressed may be gone after the run moved on; focus goes back when it is still there.
    const settle = () => { if (ui && ui.tasks && !activeIn(ui.tasks)) restoreFocus(sections[task.id], snap); };
    agentBusy[task.id] = true; setNote(task.id, null); updateAgentSections();
    let response;
    try { response = await call(); } catch (err) {
      if (!live(mark)) return;
      agentBusy[task.id] = false;
      setNote(task.id, errorWords(opts.failed, err), "err", { install: !!(err && err.state === "agent_mcp_missing") });
      updateAgentSections(); settle(); syncRuns(mark, { reload: false });
      return;
    }
    if (!live(mark)) return;
    agentBusy[task.id] = false;
    mergeRun(response && response.run ? response.run : response);
    if (opts.after) opts.after(response);
    if (opts.done) opts.done(response);
    updateAgentSections();
    await syncRuns(mark, { before: before, reload: opts.reload });
    if (live(mark)) settle();
  }
  function closeReply(st) { st.reply = null; st.text = ""; }
  async function startRun(task, agent) {
    if (agentInfo && agentInfo.mcp === false) {
      setNote(task.id, mcpWords(), "err", { install: true }); updateAgentSections(); return;
    }
    if (!(await confirmAgent(agent))) return;
    // A reply form left open on the run before has nothing to answer now.
    return agentAction(task, () => apiPost("/api/agents/runs", { project: project(), task: task.id }), { failed: t("没能开始这次运行。"), after: () => closeReply(agentState(task.id)) });
  }
  function stopRun(task, run) {
    return agentAction(task, () => apiPost("/api/agents/runs/" + encodeURIComponent(run.id) + "/stop", { project: project() }), { failed: t("没能停止这次运行。") });
  }
  // -- 撤回本次运行: the list first, then the person's yes (18.1 item 5) ---------------------------------------------
  // The button opens what the undo would do -- the files it puts back, the files it moves away, each with its time and
  // a box that is ticked, and the files it cannot undo with why. Only 确认撤回 writes, and only the ticked files.
  function revertRun(task, run) {
    const st = agentState(task.id);
    if (st.undo && st.undo.run === run.id) {
      st.undo = null; updateAgentSections(); focusKey(sections[task.id], "agent:" + task.id + ":undo");
      return;
    }
    st.undo = { run: run.id, plan: null, error: null, off: {} };
    return loadUndoPlan(task, run);
  }
  async function loadUndoPlan(task, run) {
    const st = agentState(task.id), open = st.undo, mark = stamp();
    if (!open) return;
    open.plan = null; open.error = null; updateAgentSections();
    try {
      const plan = await apiGet("/api/agents/runs/" + encodeURIComponent(run.id) + "/revert-plan");
      if (!live(mark) || st.undo !== open) return;
      open.plan = plan;
    } catch (err) {
      if (!live(mark) || st.undo !== open) return;
      open.error = err;
    }
    updateAgentSections();
    // The first box is ready when the list opens; with nothing to tick, the way out is.
    const first = undoItems(open)[0];
    focusKey(sections[task.id], "agent:" + task.id + (first ? ":undo:" + first.path : ":undo-cancel"));
  }
  function undoItems(open) { return open && open.plan ? (open.plan.restore || []).concat(open.plan.remove || []) : []; }
  function undoTicked(open) { return undoItems(open).filter((item) => !open.off[item.path]); }
  function whenWords(iso) { return typeof iso === "string" && iso.length >= 16 ? iso.slice(0, 10) + " " + iso.slice(11, 16) : ""; }
  // A group of the list under a disclosure with 12.13's chevron, open until the person closes it (and kept as they left it).
  function undoGroup(st, key, title, content) {
    const box = el("details", "res-draft"), summary = el("summary", "res-draft-summary");
    box.open = st.open[key] !== false;
    box.addEventListener("toggle", () => { st.open[key] = box.open; });
    if (typeof chevronIcon === "function") summary.append(chevronIcon());
    summary.append(el("span", null, title));
    const body = el("div", "res-draft-body"); body.append(...content);
    box.append(summary, body);
    return box;
  }
  function undoPanel(task, run, st) {
    const open = st.undo, k = (name) => "agent:" + task.id + ":" + name, who = { task: task.text };
    const box = el("div", "ws-agent-undo"), actions = el("div", "ws-agent-actions");
    box.setAttribute("role", "group"); box.setAttribute("aria-label", t("撤回本次运行：{task}", who));
    box.append(el("h4", "ws-run-heading", t("撤回本次运行")));
    const cancel = keyed(named(button(t("取消"), () => { st.undo = null; updateAgentSections(); focusKey(sections[task.id], k("undo")); }), t("取消：{task}", who)), k("undo-cancel"));
    if (!open.plan) {
      if (open.error) {
        box.append(el("p", "ws-status err", errorWords(t("没能读出撤回的清单。"), open.error)));
        actions.append(keyed(button(t("重试"), () => loadUndoPlan(task, run)), k("undo-retry")), cancel);
      } else {
        box.append(el("p", "ws-status", t("正在读取…")));
        actions.append(cancel);
      }
      box.append(actions);
      return box;
    }
    const restore = open.plan.restore || [], remove = open.plan.remove || [], cannot = open.plan.cannot || [], items = undoItems(open);
    const boxes = [];
    const count = el("p", "ws-agent-quiet"), kept = el("p", "ws-agent-quiet");
    const confirm = keyed(named(button(t("确认撤回"), () => confirmUndo(task, run, st), "btn btn-primary btn-small"), t("确认撤回：{task}", who)), k("undo-confirm"));
    // Ticking or clearing a box changes the buttons and the two lines here, not the list: a long list keeps its place.
    function sync() {
      const n = undoTicked(open).length;
      count.textContent = t("已选 {n} 项，共 {total} 项", { n: n, total: items.length });
      kept.classList.toggle("hidden", n === items.length);
      confirm.disabled = !n || !!agentBusy[task.id] || !may();
    }
    function row(item) {
      const li = el("li"), label = el("label", "ws-undo-row"), tick = keyed(el("input"), k("undo:" + item.path));
      tick.type = "checkbox"; tick.checked = !open.off[item.path];
      tick.addEventListener("change", () => { if (tick.checked) delete open.off[item.path]; else open.off[item.path] = true; sync(); });
      boxes.push([item, tick]);
      label.append(tick);
      if (item.task != null) label.append(el("span", null, t("任务的完成标记：{task}", { task: item.task })));
      else label.append(el("code", null, item.path));
      const when = whenWords(item.time);
      if (when) label.append(el("span", "ws-agent-meta", when));
      if (item.from) label.append(el("span", "ws-agent-meta", t("从 {from} 移回", { from: item.from })));
      li.append(label);
      return li;
    }
    function list(entries) { const ul = el("ul", "ws-agent-files ws-undo-list"); entries.forEach((item) => ul.append(row(item))); return ul; }
    if (!items.length) box.append(el("p", "ws-status", t("没有可以撤回的文件。")));
    else {
      box.append(el("p", "ws-agent-quiet", t("勾选的文件会被撤回，没勾选的保持现状。")));
      const all = keyed(button(t("全部勾选"), () => { boxes.forEach(([item, tick]) => { tick.checked = true; delete open.off[item.path]; }); sync(); }), k("undo-all"));
      const none = keyed(button(t("全部取消勾选"), () => { boxes.forEach(([item, tick]) => { tick.checked = false; open.off[item.path] = true; }); sync(); }), k("undo-none"));
      const bulk = el("div", "ws-agent-actions"); bulk.append(all, none); box.append(bulk);
    }
    if (restore.length) box.append(undoGroup(st, "undo-restore", t("放回运行前的样子（{n}）", { n: restore.length }), [list(restore)]));
    if (remove.length) box.append(undoGroup(st, "undo-remove", t("移走（{n}）", { n: remove.length }),
      [el("p", "ws-agent-quiet", t("移到这次运行的文件夹里，不会删除。")), list(remove)]));
    if (cannot.length) box.append(undoGroup(st, "undo-cannot", t("撤不回的文件（{n}）", { n: cannot.length }), [skippedList(cannot)]));
    if (items.length) {
      kept.textContent = t("没勾选的文件会保持现状，之后这次运行就不能再撤回它们了。");
      box.append(count, kept);
      actions.append(confirm);
    }
    actions.append(cancel);
    box.append(actions);
    sync();
    return box;
  }
  function confirmUndo(task, run, st) {
    const open = st.undo, files = undoTicked(open).map((item) => item.path);
    if (!open || !files.length) return;
    return agentAction(task, () => apiPost("/api/agents/runs/" + encodeURIComponent(run.id) + "/revert", { project: project(), files: files }), {
      failed: t("没能撤回这次运行。"), reload: true, after: () => { st.undo = null; closeReply(st); },
      done: (res) => {
        const n = (res && res.restored ? res.restored.length : 0) + (res && res.removed ? res.removed.length : 0), skipped = (res && res.skipped) || [];
        setNote(task.id, t("撤回了 {n} 个文件的改动。", { n: n }), skipped.length ? "" : "ok", { skipped: skipped });
      },
    });
  }
  function toggleLog(task) {
    const st = agentState(task.id), run = latestRun(task.id);
    st.log = !st.log; updateAgentSections();
    if (st.log && run) ensureDetails(stamp());
  }
  function toggleReply(task, kind) {
    const st = agentState(task.id);
    st.reply = st.reply === kind ? null : kind; updateAgentSections();
    // The box is ready to type in when it opens; closing leaves focus on the button that opened it.
    if (st.reply) focusKey(sections[task.id], "agent:" + task.id + ":reply"); else focusKey(sections[task.id], "agent:" + task.id + ":" + kind);
  }
  async function openReport(path) {
    await openDocument(path);
    if (ui && ui.editor && typeof ui.editor.scrollIntoView === "function") ui.editor.scrollIntoView();
  }
  function replyForm(task, run, st, kind) {
    const form = el("div", "ws-agent-reply"), area = keyed(el("textarea", "form-textarea"), "agent:" + task.id + ":reply");
    form.dataset.kind = kind; form.dataset.run = run.id;
    area.value = st.text; area.spellcheck = false;
    area.setAttribute("aria-label", kind === "answer" ? t("你的回答") : t("要它改什么"));
    area.placeholder = kind === "answer" ? t("写下你的回答…") : t("写下要它改动或补做的事…");
    area.addEventListener("input", () => { st.text = area.value; });
    const send = button(t("发送"), async () => {
      const message = st.text.trim();
      if (!message) { setNote(task.id, t("请先写下内容。"), "err"); updateAgentSections(); return; }
      if (!(await confirmAgent(parseAgent(run.owner)))) return;
      return agentAction(task, () => apiPost("/api/agents/runs/" + encodeURIComponent(run.id) + "/reply", { project: project(), message: message }), {
        failed: t("没能发送。"), reload: true, after: () => closeReply(st),
      });
    }, "btn btn-primary btn-small");
    const cancel = button(t("取消"), () => { closeReply(st); updateAgentSections(); focusKey(sections[task.id], "agent:" + task.id + ":" + kind); });
    keyed(send, "agent:" + task.id + ":send"); keyed(cancel, "agent:" + task.id + ":cancel");
    named(send, t("发送：{task}", { task: task.text })); named(cancel, t("取消：{task}", { task: task.text }));
    const actions = el("div", "ws-agent-actions"); actions.append(send, cancel);
    form.append(area, actions);
    return form;
  }

  // -- the run log: what it said and did, what it changed ---------------------------------------------
  // The sentence for a step that is told by its code (18.1 item 6), else null (the step shows its own text). A code the
  // page does not know reads as a generic sentence, never as the code.
  function stepWords(step) {
    if (step.kind === "refused") return byCode(STEP_WORDS, step.code, () => t("这一步被拒绝了。"));
    if (step.kind === "error") return byCode(STEP_WORDS, step.code, () => t("程序报告了一个错误。"));
    if (step.kind === "end" && step.code) return byCode(ERROR_WORDS, step.code, () => t("这次运行没有成功。"));
    return null;
  }
  function pathList(paths) {
    const list = el("ul", "ws-agent-files");
    paths.forEach((path) => { const li = el("li"); li.append(el("code", null, path)); list.append(li); });
    return list;
  }
  function runDetails(run, loaded, st, task) {
    const box = el("div", "ws-run-details");
    if (!loaded) { box.append(el("p", "ws-status", t("正在读取…"))); return box; }
    if (loaded.error || !loaded.data) {
      box.append(el("p", "ws-status err", errorWords(t("没能读取运行记录。"), loaded.error)));
      const again = keyed(button(t("重试"), () => { delete details[run.id]; fetchDetail(run, stamp()); updateAgentSections(); }), "agent:" + (task ? task.id : run.task_id) + ":retry");
      box.append(again);
      return box;
    }
    const d = loaded.data;
    if (Object.prototype.hasOwnProperty.call(d, "reasoning_effort")) box.append(quiet(t("推理强度：{level}", { level: effortLabel(d.reasoning_effort) })));
    // 19.8: the program keeps its own record of the conversation; the log names it so that it can be found there
    if (d.session_id) box.append(quiet(t("会话编号：{id}", { id: d.session_id })));
    // A folder with more files than the picture holds: what happened beyond them cannot be checked or undone (17.5).
    if (d.snapshot && d.snapshot.partial) box.append(el("p", "ws-status err", t("这个项目文件太多，运行前只记录了前 50,000 个文件：之后的文件（数据文件除外）改动无法核对，也无法撤回。")));
    if (outcomeOf(run) !== "needs_input" && run.question) box.append(el("p", "ws-agent-question", run.question));
    if ((d.violations || []).length) {
      const group = el("div", "ws-run-group ws-oob");
      group.append(el("h4", "ws-run-heading", t("越界改动")));
      const list = el("ul", "ws-agent-files");
      d.violations.forEach((v) => {
        const li = el("li"); li.append(el("code", null, v.path));
        li.append(" " + byCode(VIOLATION_WORDS, v.reason, () => t("超出了允许的范围")));
        list.append(li);
      });
      group.append(list); box.append(group);
    }
    const changes = d.changes || {};
    [["created", () => t("新建的文件")], ["modified", () => t("修改的文件")], ["deleted", () => t("删除的文件")]].forEach(([key, words]) => {
      if (!(changes[key] || []).length) return;
      const group = el("div", "ws-run-group");
      group.append(el("h4", "ws-run-heading", words()), pathList(changes[key])); box.append(group);
    });
    if ((d.steps || []).length) {
      const group = el("div", "ws-run-group"), list = el("ol", "ws-steps");
      group.append(el("h4", "ws-run-heading", t("经过")));
      if (d.steps_total > d.steps.length) group.append(el("p", "ws-agent-quiet", t("只显示最近 {shown} 步，共 {n} 步。", { shown: d.steps.length, n: d.steps_total })));
      d.steps.forEach((step, i) => {
        const li = el("li", "ws-step" + (step.kind === "refused" || step.kind === "error" ? " bad" : "")), words = stepWords(step);
        li.append(el("span", "ws-step-kind", byCode(STEP_KINDS, step.kind, () => t("操作"))));
        if (words) {
          const body = el("div", "ws-step-body");
          body.append(el("span", "ws-step-text", words));
          if (step.text) body.append(more(st, "step:" + (d.steps_total - d.steps.length + i), t("详情"), String(step.text)));
          li.append(body);
        } else li.append(el("span", "ws-step-text" + (step.kind === "tool" ? " mono" : ""), step.text || ""));
        list.append(li);
      });
      group.append(list); box.append(group);
    }
    if (d.revert && (d.revert.skipped || []).length) {
      const group = el("div", "ws-run-group");
      group.append(el("h4", "ws-run-heading", t("这些文件没有动：")), skippedList(d.revert.skipped)); box.append(group);
    }
    if (d.leftovers > 0) box.append(el("p", "ws-agent-quiet", t("程序结束后还有 {n} 个它启动的进程在运行，已一并结束。", { n: d.leftovers })));
    if (typeof d.cost_usd === "number") {
      box.append(el("p", "ws-agent-quiet", t("费用约 {cost} 美元。", { cost: d.cost_usd.toFixed(d.cost_usd < 0.1 ? 3 : 2) })));
    }
    // 18.1 item 2: the program is not given variables whose names look like credentials; the names, never the values.
    if ((d.env_removed || []).length) box.append(more(st, "env", t("没有交给它的环境变量（名字像密钥，只列名字）"), d.env_removed.join("\n")));
    if (d.stderr_tail) box.append(more(st, "stderr", t("程序的错误输出"), d.stderr_tail));
    if (d.instruction) box.append(more(st, "instruction", t("发给它的指示"), d.instruction));
    return box;
  }

  // -- 设置: this Mac's agents (17.1) -----------------------------------------------------------------
  function toggleSettings() {
    if (settingsOpen) { settingsOpen = false; stopStoreTimer(); renderTasks(); return; }
    openSettings();
  }
  function openSettings() {
    settingsOpen = true; renderTasks();
    // The programs may have been installed since the page opened.
    if (agentInfo) loadAgentInfo(stamp(), true);
    loadStore(stamp());
    if (ui && ui.settings && typeof ui.settings.scrollIntoView === "function") ui.settings.scrollIntoView();
  }
  async function saveAgentSettings(body, doneWords, onDone) {
    if (settingsBusy) return;
    const mark = stamp();
    settingsBusy = true; settingsNote = null; renderSettings(true);
    try {
      const response = await apiPost("/api/settings/agents", body);
      if (!live(mark)) return;
      agentInfo = response; agentInfoError = null; settingsNote = { words: doneWords, kind: "ok" };
      if (body.commands) settingsInputs.paths = {};
      if (onDone) onDone();
    } catch (err) {
      if (!live(mark)) return;
      settingsNote = { words: errorWords(t("没能保存设置。"), err), kind: "err" };
    }
    settingsBusy = false;
    refreshOwnerOptions(); renderSettings(); updateAgentSections();
  }
  function currentAgents() { return ((agentInfo && agentInfo.agents) || []).map((a) => {
    const item = { runner: a.runner, model: a.model };
    if (a.reasoning_effort) item.reasoning_effort = a.reasoning_effort;
    return item;
  }); }
  function effortLabel(level) {
    const words = { none: t("无推理"), off: t("关闭推理"), minimal: t("最低"), low: t("低"), medium: t("中"), high: t("高"), xhigh: t("很高"), max: t("最高"), ultra: "Ultra" };
    return level ? (words[level] || level) : t("默认");
  }
  function effortLevels(runner, model) {
    const catalog = modelsState[runner] || {};
    const known = ((agentInfo && agentInfo.reasoning_efforts) || {})[runner] || {};
    const levels = catalog.efforts || known;
    return levels[model] || (runner === "claude-code" && levels[model.replace(/\[1m\]$/, "").replace(/-\d{8}$/, "")]) || [];
  }
  function effortSelect(runner, model, value, key, onChange) {
    const select = keyed(el("select", "form-input"), key), levels = effortLevels(runner, model);
    const offered = value && !levels.includes(value) ? levels.concat([value]) : levels;
    ["", ...offered].forEach((level) => { const option = el("option", null, effortLabel(level)); option.value = level; select.append(option); });
    select.value = value || ""; select.disabled = settingsBusy;
    select.addEventListener("change", () => onChange(select.value));
    const control = field(t("推理强度"), select);
    control.classList.toggle("hidden", !offered.length);
    return control;
  }
  function fillEffortList() {
    if (!ui || !ui.effortSlot) return;
    ui.effortSlot.classList.toggle("hidden", !effortLevels(settingsInputs.runner, settingsInputs.model).length);
    if (!effortLevels(settingsInputs.runner, settingsInputs.model).includes(settingsInputs.effort)) settingsInputs.effort = "";
    ui.effortSlot.replaceChildren(effortSelect(settingsInputs.runner, settingsInputs.model, settingsInputs.effort, "set-effort", (level) => { settingsInputs.effort = level; }));
  }
  function quiet(words) { return el("p", "ws-agent-quiet", words); }
  function runnerBlock(r) {
    const row = el("div", "ws-runner"), program = PROGRAMS[r.runner] || r.runner;
    row.append(el("h4", "ws-runner-name", RUNNER_NAMES[r.runner] || r.label || r.runner));
    const where = el("p", "ws-runner-where");
    if (r.path) where.append(el("code", null, r.path));
    if (r.found && r.version) where.append(el("span", "ws-agent-meta", t("版本 {version}", { version: r.version })));
    if (r.problem === "missing" || !r.path) where.append(el("span", "ws-agent-meta", t("没有找到这个程序")));
    else if (r.problem === "not_executable") where.append(el("span", "ws-agent-meta bad", t("找到了文件，但它不能运行")));
    else if (r.problem === "no_version") where.append(el("span", "ws-agent-meta bad", t("找到了，但没能读到版本号")));
    row.append(where);
    if (!r.found || r.problem) {
      const path = keyed(input("text"), "set-path:" + r.runner), runnerName = RUNNER_NAMES[r.runner] || r.label || r.runner;
      path.placeholder = t("程序的完整路径，例如 /usr/local/bin/{program}", { program: program });
      named(path, t("{name} 的程序路径", { name: runnerName }));
      path.className += " form-mono"; path.value = settingsInputs.paths[r.runner] || ""; path.disabled = settingsBusy;
      path.addEventListener("input", () => { settingsInputs.paths[r.runner] = path.value; });
      const save = button(t("保存"), () => {
        const value = path.value.trim();
        if (!value) { settingsNote = { words: t("请填写程序的完整路径。"), kind: "err" }; renderSettings(true); return; }
        saveAgentSettings({ commands: { [r.runner]: value } }, t("已保存"));
      }); save.disabled = settingsBusy;
      keyed(save, "set-path-save:" + r.runner); named(save, t("保存：{name}", { name: runnerName }));
      const line = el("div", "ws-agent-add"); line.append(path, save); row.append(line);
    }
    row.append(quiet((PRIVACY[r.runner] || PRIVACY.opencode)()));
    if (r.runner === "opencode") row.append(quiet(t("OpenCode 没有逐步检查：运行结束后才核对哪些文件被改动，越界的改动会列出来，可以撤回。")));
    if (r.runner === "codex") row.append(quiet(t("Codex 使用工作目录写入沙箱；运行结束后核对改动，并提供可用的撤回。")));
    if (r.runner === "deepseek-harness") row.append(quiet(t("支持桌面端自带的 dsh。账户选项使用 Harness 登录；API Key 选项使用 Harness 的密钥设置。")));
    return row;
  }
  function fillModelList() {
    if (!ui || !ui.modelList) return;
    const runner = settingsInputs.runner;
    const catalog = modelsState[runner] || {};
    const names = runner === "claude-code" ? ((agentInfo && agentInfo.claude_models) || [])
      : runner === "deepseek-harness" ? ((agentInfo && agentInfo.harness_models) || []) : (catalog.list || []);
    const placeholder = el("option", null, t("选择模型版本…")); placeholder.value = "";
    const custom = el("option", null, t("手动填写完整 ID…")); custom.value = "__custom__";
    ui.modelList.replaceChildren(placeholder, ...names.map((name) => {
      const match = runner === "claude-code" && /^claude-(sonnet|opus|haiku|fable)-(\d+)(?:-(\d+))?(?:-(\d{8}))?$/.exec(name);
      const label = match ? match[1][0].toUpperCase() + match[1].slice(1) + " " + match[2] + (match[3] ? "." + match[3] : "") + " · " + name
        : runner === "deepseek-harness" ? (name.startsWith("deepseek-account/") ? t("账户") : "API Key") + " · " + name : name;
      const option = el("option", null, label); option.value = name; return option;
    }), custom);
    const manual = settingsInputs.customModel || (!!settingsInputs.model && !names.includes(settingsInputs.model));
    ui.modelList.value = manual ? "__custom__" : settingsInputs.model;
    ui.modelCustom.classList.toggle("hidden", !manual);
    ui.modelInput.value = settingsInputs.model;
    if (ui.modelNote) {
      ui.modelNote.textContent = catalog.error ? errorWords(t("没能读取模型列表，可以手动填写完整模型 ID。"), catalog.error)
        : catalog.loading ? t("正在读取…") : "";
    }
    fillEffortList();
  }
  async function loadModels(runner, refresh) {
    if (runner === "claude-code" || runner === "deepseek-harness") return;
    const catalog = modelsState[runner] || {};
    if (catalog.loading || (!refresh && (catalog.list || catalog.error))) return;
    const found = agentInfo && (agentInfo.runners || []).find((r) => r.runner === runner);
    if (!found || !found.found) return;
    const mark = stamp();
    modelsState[runner] = Object.assign({}, catalog, { loading: true }); fillModelList();
    try {
      const response = await apiGet("/api/agents/models?runner=" + encodeURIComponent(runner));
      if (!live(mark)) return;
      modelsState[runner] = { list: Array.isArray(response && response.models) ? response.models : [], efforts: (response && response.reasoning_efforts) || {} };
    } catch (err) {
      if (!live(mark)) return;
      modelsState[runner] = { error: err };
    }
    renderSettings(true);
  }
  function agentsBlock() {
    const box = el("div", "ws-agent-list");
    box.append(el("h4", "ws-runner-name", t("这台电脑上的 AI 执行者")));
    const agents = agentInfo.agents || [];
    if (!agents.length) box.append(quiet(t("还没有添加。在下面选一个程序，写上模型名。")));
    agents.forEach((a) => {
      const row = el("div", "ws-agent-item");
      row.append(el("span", "ws-agent-label", a.label));
      row.append(effortSelect(a.runner, a.model, a.reasoning_effort, "set-effort:" + a.owner, (level) => {
        const agents = currentAgents().map((item) => {
          if (item.runner === a.runner && item.model === a.model) {
            if (level) item.reasoning_effort = level; else delete item.reasoning_effort;
          }
          return item;
        });
        saveAgentSettings({ agents: agents }, t("已保存"));
      }));
      if (a.available === false) row.append(el("span", "ws-agent-meta bad", t("找不到它的程序")));
      const remove = keyed(named(button(t("移除"), () => saveAgentSettings({ agents: currentAgents().filter((x) => x.runner + ":" + x.model !== a.runner + ":" + a.model) }, t("已移除"))), t("移除：{name}", { name: a.label })), "set-remove:" + a.runner + ":" + a.model);
      remove.disabled = settingsBusy; row.append(remove); box.append(row);
    });
    const form = el("div", "ws-agent-add"), runner = keyed(el("select", "form-input"), "set-runner"), model = keyed(input("text", t("模型名")), "set-model");
    (agentInfo.runners || []).forEach((r) => { const option = el("option", null, RUNNER_NAMES[r.runner] || r.label || r.runner); option.value = r.runner; runner.append(option); });
    runner.value = settingsInputs.runner; model.value = settingsInputs.model; model.className += " form-mono";
    ui.modelInput = model;
    ui.modelList = keyed(el("select", "form-input form-mono"), "set-model-version");
    ui.modelList.id = "ws-model-list"; ui.modelList.disabled = settingsBusy;
    ui.modelCustom = field(t("完整模型 ID"), model);
    ui.modelNote = el("p", "ws-agent-quiet");
    ui.effortSlot = el("div", "ws-agent-effort");
    ui.modelList.addEventListener("change", () => {
      settingsInputs.customModel = ui.modelList.value === "__custom__";
      settingsInputs.model = settingsInputs.customModel ? "" : ui.modelList.value;
      fillModelList(); if (settingsInputs.customModel) model.focus();
    });
    runner.addEventListener("change", () => {
      settingsInputs.runner = runner.value; settingsInputs.model = ""; settingsInputs.customModel = false;
      fillModelList(); loadModels(runner.value);
    });
    model.addEventListener("input", () => { settingsInputs.model = model.value.trim(); fillEffortList(); });
    const add = button(t("添加"), () => {
      const name = model.value.trim();
      if (!name) { settingsNote = { words: t("请写上模型名。"), kind: "err" }; renderSettings(true); return; }
      const item = { runner: runner.value, model: name };
      if (settingsInputs.effort) item.reasoning_effort = settingsInputs.effort;
      saveAgentSettings({ agents: currentAgents().concat([item]) }, t("已添加"), () => { settingsInputs.model = ""; settingsInputs.effort = ""; settingsInputs.customModel = false; });
    }); keyed(add, "set-add"); add.disabled = runner.disabled = model.disabled = settingsBusy;
    const refresh = button(t("刷新模型列表"), () => loadModels(settingsInputs.runner, true));
    const suggested = () => runner.value === "claude-code" || runner.value === "deepseek-harness";
    refresh.disabled = settingsBusy; refresh.classList.toggle("hidden", suggested());
    runner.addEventListener("change", () => refresh.classList.toggle("hidden", suggested()));
    form.append(field(t("程序"), runner), field(t("模型版本"), ui.modelList), ui.modelCustom, ui.effortSlot, add, refresh);
    box.append(form, ui.modelNote);
    fillModelList();
    return box;
  }
  function limitBlock() {
    const row = el("div", "ws-agent-add"), minutes = keyed(input("number"), "set-minutes");
    minutes.min = "5"; minutes.max = "1440"; minutes.step = "1"; minutes.disabled = settingsBusy;
    minutes.value = settingsInputs.minutes == null ? String(agentInfo.timeout_minutes == null ? "" : agentInfo.timeout_minutes) : settingsInputs.minutes;
    minutes.addEventListener("input", () => { settingsInputs.minutes = minutes.value; });
    const save = button(t("保存"), () => {
      const value = Number(minutes.value);
      if (!Number.isFinite(value) || value < 5 || value > 1440) { settingsNote = { words: t("时间限制要在 5 到 1440 分钟之间。"), kind: "err" }; renderSettings(true); return; }
      settingsInputs.minutes = null;
      saveAgentSettings({ timeout_minutes: Math.round(value) }, t("已保存"));
    }); keyed(save, "set-minutes-save"); named(save, t("保存：{name}", { name: t("时间限制") })); save.disabled = settingsBusy;
    row.append(field(t("每次运行的时间限制（分钟）"), minutes), save);
    return row;
  }
  // The spending cap of a Claude Code run (17.3), in US dollars; empty takes the cap away.
  function budgetBlock() {
    const row = el("div", "ws-agent-add"), usd = keyed(input("number"), "set-budget");
    usd.min = "0.01"; usd.max = "1000"; usd.step = "0.01"; usd.disabled = settingsBusy;
    usd.value = settingsInputs.budget == null ? String(agentInfo.max_budget_usd == null ? "" : agentInfo.max_budget_usd) : settingsInputs.budget;
    usd.addEventListener("input", () => { settingsInputs.budget = usd.value; });
    const save = button(t("保存"), () => {
      const words = usd.value.trim(), value = Number(words);
      if (words && (!Number.isFinite(value) || value <= 0 || value > 1000)) { settingsNote = { words: t("费用上限要大于 0、不超过 1000 美元。"), kind: "err" }; renderSettings(true); return; }
      settingsInputs.budget = null;
      saveAgentSettings({ max_budget_usd: words ? value : null }, t("已保存"));
    }); keyed(save, "set-budget-save"); named(save, t("保存：{name}", { name: t("费用上限") })); save.disabled = settingsBusy;
    row.append(field(t("每次运行的费用上限（美元，只对 Claude Code 有效，留空为不限）"), usd), save);
    return row;
  }
  // The most a project's run store may take (DESIGN.md 19.8), in whole gigabytes; saved with the other limits.
  function storeLimitBlock() {
    const row = el("div", "ws-agent-add"), gb = keyed(input("number"), "set-store-limit");
    const range = agentInfo.store_limit_range || [1, 100];
    gb.min = String(range[0]); gb.max = String(range[1]); gb.step = "1"; gb.disabled = settingsBusy;
    gb.value = settingsInputs.storeLimit == null ? String(agentInfo.store_limit_gb == null ? "" : agentInfo.store_limit_gb) : settingsInputs.storeLimit;
    gb.addEventListener("input", () => { settingsInputs.storeLimit = gb.value; });
    const save = button(t("保存"), () => {
      const value = Number(gb.value);
      if (!gb.value.trim() || !Number.isInteger(value) || value < range[0] || value > range[1]) { settingsNote = { words: t("空间上限要在 1 到 100 GB 之间，是整数。"), kind: "err" }; renderSettings(true); return; }
      settingsInputs.storeLimit = null;
      saveAgentSettings({ store_limit_gb: value }, t("已保存"), () => loadStore(stamp()));
    }); keyed(save, "set-store-limit-save"); named(save, t("保存：{name}", { name: t("空间上限") })); save.disabled = settingsBusy;
    row.append(field(t("每个项目的运行空间上限（GB）"), gb), save);
    return row;
  }

  // -- 运行占用的空间 (DESIGN.md 19.8): the open project's run store against its limit ---------------------
  // A size as the panel names it: a tenth of a gigabyte from 1 GB up, whole megabytes below (13.5: thousands separated in
  // English), and "less than 1 MB" for what a few logs take.
  function storeSize(bytes) {
    const mb = bytes / (1024 * 1024);
    if (mb >= 1024) return (mb / 1024).toFixed(1) + " GB";
    return bytes > 0 && mb < 1 ? t("小于 1 MB") : countText(Math.round(mb)) + " MB";
  }
  function stopStoreTimer() { if (storeTimer !== null) clearTimeout(storeTimer); storeTimer = null; }
  // While the engine tidies an old store it says so; the panel asks again until it has finished.
  function scheduleStore() {
    stopStoreTimer();
    if (settingsOpen && storeInfo && storeInfo.converting) storeTimer = setTimeout(() => { storeTimer = null; loadStore(stamp()); }, 3000);
  }
  async function loadStore(mark) {
    if (!settingsOpen || !project()) return;
    try {
      const info = await apiGet("/api/agents/store?project=" + encodeURIComponent(project()));
      if (!live(mark)) return;
      storeInfo = info; storeError = null;
    } catch (err) {
      if (!live(mark)) return;
      storeError = err;
    }
    drawStore(); scheduleStore();
  }
  // One action on the store (release, delete): it answers the panel as it stands now.
  async function storeAction(path, body, doneWords, failWords) {
    if (storeBusy) return;
    const mark = stamp();
    storeBusy = true; storeNote = null; drawStore();
    try {
      const answer = await apiPost(path, Object.assign({ project: project() }, body));
      if (!live(mark)) { storeBusy = false; return; }
      storeInfo = answer; storeError = null; storeNote = { words: doneWords, kind: "ok" };
    } catch (err) {
      if (!live(mark)) { storeBusy = false; return; }
      storeNote = { words: errorWords(failWords, err), kind: "err" };
    }
    storeBusy = false; drawStore(); scheduleStore();
  }
  function storeRunRow(item) {
    const li = el("li", "ws-store-run");
    const task = item.task_removed || !item.task ? t("任务已移除") : item.task;
    li.append(el("span", "ws-store-task", task), el("span", "ws-agent-quiet", whenWords(item.date)), el("span", "ws-agent-quiet", storeSize(item.size)),
      el("span", "ws-agent-quiet", t("{n} 个文件", { n: countText(item.files) })));
    const remove = keyed(named(button(t("删除这些文件"), () => {
      if (!window.confirm(t("要删除这次运行撤回时移走的 {n} 个文件吗？删除后无法恢复。", { n: countText(item.files) }))) return;
      storeAction("/api/agents/store/delete-moved", { run: item.run }, t("已删除。"), t("没能删除这些文件。"));
    }), t("删除这些文件：{task}", { task: task })), "store-delete:" + item.run);
    remove.disabled = storeBusy; li.append(remove);
    return li;
  }
  function drawStore() {
    const slot = ui && ui.storeSlot;
    if (!slot) return;
    const snap = focusSnapshot(slot);
    slot.replaceChildren();
    // a read that failed says so, with what was known before it under it; the first read says it is going
    if (storeError) {
      slot.append(el("p", "ws-status err", errorWords(t("没能读取运行占用的空间。"), storeError)));
      slot.append(keyed(button(t("重试"), () => loadStore(stamp())), "store-retry"));
    }
    if (!storeInfo) {
      if (!storeError) slot.append(el("p", "ws-status", t("正在读取…")));
      return;
    }
    const info = storeInfo;
    if (info.converting) slot.append(el("p", "ws-status", t("正在整理撤回备份…")));
    else {
      const rows = el("div", "ws-store-rows");
      [[t("撤回备份"), info.backups], [t("撤回时移走的文件"), info.moved], [t("运行记录"), info.records]].forEach(([name, bytes]) => {
        const row = el("div", "ws-store-row");
        row.append(el("span", null, name), el("span", "ws-store-size", storeSize(bytes || 0)));
        rows.append(row);
      });
      slot.append(rows);
      slot.append(quiet(t("共占用 {used}，每个项目最多 {limit}。", { used: storeSize(info.total || 0), limit: storeSize(info.limit) })));
    }
    slot.append(quiet(t("超过上限时，从最早的运行开始释放撤回备份，正在运行的那一次不受影响。")));
    // 19.12 item 8: and what that means for the next run, since the limit is kept by releasing backups
    if (info.moved_over && !info.converting) slot.append(el("p", "ws-status err", t("撤回时移走的文件已经超过上限。它们不会被自动清理，只有你可以删除。在你删除它们之前，每次新运行的撤回备份会在运行结束时立刻释放，所以新的运行不能撤回。")));
    if ((info.moved_runs || []).length && !info.converting) {
      const list = el("ul", "ws-store-runs");
      info.moved_runs.slice().reverse().forEach((item) => list.append(storeRunRow(item)));
      slot.append(list);
    }
    const release = keyed(button(t("释放所有撤回备份"), () => {
      if (!window.confirm(t("要释放这个项目所有的撤回备份吗？释放后，这些运行就不能再撤回了。正在运行的那一次保留自己的备份。"))) return;
      storeAction("/api/agents/store/release", {}, t("已释放。"), t("没能释放撤回备份。"));
    }), "store-release");
    release.disabled = storeBusy || !!info.converting || !info.releasable; slot.append(release);
    if (storeNote) slot.append(el("p", "ws-status " + storeNote.kind, storeNote.words));
    restoreFocus(slot, snap);
  }
  function storeBlock() {
    const box = el("div", "ws-agent-list ws-store");
    box.append(el("h4", "ws-runner-name", t("运行占用的空间")));
    ui.storeSlot = el("div", "ws-store-body"); box.append(ui.storeSlot);
    drawStore();
    return box;
  }
  // `fresh`: the settings box was just made (or the person asked for the redraw). Otherwise an input the person is
  // typing in is not rebuilt: the program list and statuses of the next redraw wait, only the note is patched.
  function renderSettings(fresh) {
    if (!ui || !ui.settings) return;
    const box = ui.settings, typing = activeIn(box);
    if (!fresh && typing && isTyping(typing)) { showSettingsNote(); return; }
    const snap = focusSnapshot(box);
    box.replaceChildren(); box.classList.toggle("hidden", !settingsOpen);
    if (!settingsOpen) return;
    box.append(el("h3", "ws-agent-heading", t("AI 执行者")));
    box.append(quiet(t("负责人可以选这台电脑上的 AI 执行者：运行时它会直接在项目文件夹里做这项任务，写代码、运行程序、写笔记和结果。")));
    if (!agentInfo) {
      if (agentInfoError) {
        box.append(el("p", "ws-status err", errorWords(t("没能读取 AI 执行者的设置。"), agentInfoError)));
        box.append(button(t("重试"), () => loadAgentInfo(stamp(), true)));
      } else box.append(el("p", "ws-status", t("正在读取…")));
      return;
    }
    if (agentInfo.mcp === false) { const warn = el("div", "ws-agent-warn"); warn.append(el("p", "ws-status err", mcpWords()), installBlock()); box.append(warn); }
    (agentInfo.runners || []).forEach((r) => box.append(runnerBlock(r)));
    box.append(agentsBlock(), limitBlock(), budgetBlock(), storeLimitBlock(), storeBlock());
    ui.settingsNote = el("p"); ui.settingsNote.setAttribute("role", "status"); box.append(ui.settingsNote); showSettingsNote();
    restoreFocus(box, snap);
    loadModels(settingsInputs.runner);
    (agentInfo.agents || []).forEach((agent) => loadModels(agent.runner));
  }
  function showSettingsNote() {
    if (!ui || !ui.settingsNote) return;
    ui.settingsNote.textContent = settingsNote ? settingsNote.words : "";
    ui.settingsNote.className = settingsNote ? "ws-status " + settingsNote.kind : "hidden";
  }

  async function load(target, refresh) {
    if (foreignBlocked) return;
    request += 1;
    if (data && data.project !== project()) {
      if (isDirty()) { projectChangedElsewhere(); return; }
      reset();
    }
    host = target;
    if (!ui) host.replaceChildren(el("p", "ws-empty", t("载入中…")));
    // reset() changes the epoch, so take the mark after a project reset.
    const current = stamp(), thisRequest = request, writes = taskWrites;
    try {
      const fresh = await apiGet("/api/workspace");
      if (!live(current) || thisRequest !== request || fresh.project !== project()) return;
      // The task form keeps its own text (taskDraft lives outside data.tasks), so the list is always the fresh one:
      // an agent's done mark and report arrive while the person writes. It is kept only while a save is going or a
      // conflict waits, and when this answer is older than a save made since it was asked for.
      const retainTasks = taskBusy || taskConflict || writes !== taskWrites;
      const oldTasks = data && data.tasks, oldRevision = data && data.tasks_revision, oldTaskError = data && data.tasks_error, oldReports = data && data.changed_task_reports;
      data = fresh; data.documents = data.documents || []; knownCache = new Map();
      if (retainTasks && oldTasks) { data.tasks = oldTasks; data.tasks_revision = oldRevision; data.changed_task_reports = oldReports || []; if (oldTaskError) data.tasks_error = oldTaskError; }
      // An existing task is being edited and the file moved under it: the edit stays, the save waits for a reload.
      else if (oldTasks && taskDraft.id && fresh.tasks_revision !== oldRevision) taskConflict = true;
      if (!ui || target !== ui.root) { buildPage(); ui.root = target; }
      renderNav(); renderNotices(); refreshTasks();
      if (draft) redrawAttachments();
      ui.note.disabled = ui.result.disabled = !editable(); target.dataset.loaded = "1";
      loadAgentContext(current);
      if (!draft) {
        const overview = data.documents.find((doc) => doc.kind === "overview");
        if (overview) await openDocument(overview.path, true);
        else setDraft({ path: "research/overview.md", kind: "overview", title: t("研究问题与计划"), content: "", revision: null });
      } else if (refresh && !documentDirty() && draft.path) await openDocument(draft.path, true);
    } catch (err) {
      if (!live(current) || thisRequest !== request) return;
      if (ui && ui.docStatus) status(ui.docStatus, errorWords(t("没能读取项目首页。"), err), "err");
      else host.replaceChildren(el("p", "ws-status err", errorWords(t("没能读取项目首页。"), err)), button(t("重试"), () => load(target, refresh)));
    }
  }

  function createLive(subject, mark) {
    return live(mark) && createSubject === subject && typeof state !== "undefined" && state.panelSubject === subject && !panel.classList.contains("hidden");
  }
  function openCreateProject() {
    if (typeof mayReplacePanel === "function" && !mayReplacePanel()) return;
    if (!mayLeave()) return;
    if (typeof panelBody === "undefined") return;
    createRequest += 1;
    const form = el("form", "ws-create"), name = input("text", t("项目名称")), parent = input("text", t("例如 /Users/你的名字/Documents"));
    const subject = { kind: "workspace-create", name: name, parent: parent };
    createSubject = subject; state.panelSubject = subject;
    [name, parent].forEach((node) => node.addEventListener("input", () => { node.dataset.typed = "1"; }));
    panelTitle.textContent = t("新建项目"); panelSubtitle.textContent = ""; panelBody.replaceChildren();
    form.append(field(t("项目名称"), name), field(t("保存位置"), parent));
    const choose = button(t("选择文件夹…"), async () => {
      const mark = stamp();
      const selected = await chooseFolder("create");
      if (selected && createLive(subject, mark)) { parent.value = selected; parent.dataset.typed = "1"; }
    });
    const feedback = el("div", "ws-status"); feedback.setAttribute("role", "status");
    const create = button(t("创建项目"), async () => {
      if (!name.value.trim() || !parent.value.trim()) { status(feedback, t("请填写项目名称和保存位置。"), "err"); (!name.value.trim() ? name : parent).focus(); return; }
      const mark = stamp(), token = ++createRequest;
      create.disabled = true; name.disabled = parent.disabled = choose.disabled = true; status(feedback, t("正在创建…"));
      try {
        const result = await apiPost("/api/projects/create", { name: name.value.trim(), parent: parent.value.trim(), language: typeof uiLang === "function" ? uiLang() : "zh" });
        if (token !== createRequest || !live(mark)) return;
        // A successful create has opened the project on the server. The frame
        // owns its project list, generation baseline and sidebar refresh.
        createSubject = null;
        if (typeof window.ROWorkspace.onProjectCreated === "function") await window.ROWorkspace.onProjectCreated(result);
        else {
          reset(); closePanel(); await reloadAllViews();
          if (typeof currentView !== "undefined" && currentView === "workspace" && typeof views !== "undefined") await load(views.workspace);
          else activateView("workspace");
        }
      } catch (err) {
        if (token !== createRequest || !createLive(subject, mark)) return;
        create.disabled = name.disabled = parent.disabled = choose.disabled = false; status(feedback, errorWords(t("没能创建项目。"), err), "err");
      }
    }, "btn btn-primary");
    form.addEventListener("submit", (event) => { event.preventDefault(); if (!create.disabled) create.click(); });
    form.append(choose, create, feedback); panelBody.append(form); openPanel(); name.focus();
  }

  async function openOutputFolders() {
    if (!mayReplacePanel()) return;
    const mark = stamp(), subject = { kind: "output-folders", dirty: false };
    folderSubject = subject; state.panelSubject = subject;
    panelTitle.textContent = t("输出目录"); panelSubtitle.textContent = ""; panelBody.replaceChildren(); openPanel();
    const note = el("p", "ws-status"); panelBody.append(note); status(note, t("载入中…"));
    const active = () => live(mark) && state.panelSubject === subject;
    try {
      const info = await apiGet("/api/outputs/folders");
      if (!active()) return;
      panelBody.replaceChildren();
      panelBody.append(el("p", "ws-agent-quiet", t("已有文件保持原位。新任务按这些目录保存输出，可以在目录内继续分组。")));
      const labels = outputKinds();
      const form = el("form", "ws-output-folders"), inputs = {};
      let choosing = null;   // the line that says the work location needs choosing, until a save sets it
      Object.keys(labels).forEach((kind) => {
        const box = input("text"); box.value = info.folders[kind]; inputs[kind] = box;
        box.addEventListener("input", () => { subject.dirty = true; });
        form.append(field(labels[kind], box));
        // 19.2: the work location comes last, and says what it holds and what the app does with it; when no name is
        // usable for it (the settings still read: it is empty), the field says it needs choosing
        if (kind === "work") {
          if (Array.isArray(info.choose) && info.choose.indexOf("work") >= 0) form.append(choosing = el("p", "ws-status err", t("请为过程文件选一个目录。")));
          form.append(el("p", "ws-agent-quiet", t("过程文件：只为完成这次任务而写的脚本、自检输出、下载的源码和中间文件。不进索引，不算输出。")));
        }
      });
      const feedback = el("p", "ws-status"); feedback.setAttribute("role", "status");
      const save = button(t("保存"), async () => {
        save.disabled = true; Object.values(inputs).forEach((box) => { box.disabled = true; });
        try {
          const response = await apiPost("/api/outputs/folders", { project: mark.project, revision: info.revision,
            folders: Object.fromEntries(Object.entries(inputs).map(([kind, box]) => [kind, box.value.trim()])) });
          if (!active()) return;
          info.revision = response.revision; subject.dirty = false; save.disabled = false;
          Object.values(inputs).forEach((box) => { box.disabled = false; });
          // a save that set the work location answers the line (19.2); one that left it empty would have been refused
          if (choosing && inputs.work.value.trim()) { choosing.remove(); choosing = null; }
          status(feedback, t("已保存，下次任务运行时使用。"), "ok");
        } catch (err) { if (active()) { save.disabled = false; Object.values(inputs).forEach((box) => { box.disabled = false; }); status(feedback, errorWords(t("没能保存输出目录。"), err), "err"); } }
      });
      form.addEventListener("submit", (event) => { event.preventDefault(); if (!save.disabled) save.click(); });
      form.append(save, feedback); panelBody.append(form);
      // 19.5: the files earlier runs created and nobody registered, offered for review when there are any
      try {
        const earlier = await apiGet(backfillUrl(null));
        const n = active() && earlier ? (Number(earlier.listed) || 0) + (Number(earlier.more) || 0) : 0;
        if (n > 0) {
          const offer = el("div", "ws-backfill-offer");
          offer.append(quiet(t("以往的运行创建了 {n} 个还没登记的文件。", { n: countText(n) })), keyed(button(t("补登记以往运行的文件…"), () => openBackfill(null)), "output-backfill"));
          panelBody.append(offer);
        }
      } catch (err) { /* no list, no offer: the folders are what this panel is for */ }
    } catch (err) { if (active()) status(note, errorWords(t("没能读取输出目录。"), err), "err"); }
  }

  function renderResearchLinks(target, records, label) {
    if (!records || !records.length) return;
    const box = el("section", "ws-related-records"); box.append(el("h3", null, label || t("关联研究记录")));
    records.forEach((record) => {
      const row = el("div", "ws-related-record");
      row.append(button(record.title || record.report, () => openResearchRecord(record.report), "btn btn-small ws-file-link"));
      if (record.question) row.append(el("p", null, record.question));
      (record.matches || []).forEach((match) => { const line = el("p", "ws-research-purpose"); line.append(el("span", "mono", match.path)); if (match.purpose) line.append(" — " + match.purpose); row.append(line); });
      (record.variables || []).forEach((ref) => row.append(el("p", null, t("所用变量版本：{id} v{version}", ref))));
      box.append(row);
    }); target.append(box);
  }
  function openResearchRecord(path) {
    if (!mayLeave()) return;
    if (typeof openResultOnHome === "function") { openResultOnHome(path); return; }
    if (typeof activateView === "function") activateView("workspace");
    if (ui) openDocument(path, false, true);
  }
  window.ROWorkspace = { renderResearchLinks: renderResearchLinks, openResearchRecord: openResearchRecord, startTask: startTask, load: load, mayLeave: mayLeave, mayLeavePanel: mayLeaveFolders, reset: reset, openCreateProject: openCreateProject, openReport: openReport, openHandback: openHandback, showRunLog: showRunLog, isDirty: isDirty, projectChangedElsewhere: projectChangedElsewhere, resume: resumePolling, stop: stopForGood };
  window.addEventListener("beforeunload", (event) => { if (isDirty()) { event.preventDefault(); event.returnValue = ""; } });
})();
