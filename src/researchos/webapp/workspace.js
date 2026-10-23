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
  let createSubject = null, foreignBlocked = false;
  // AI agents carry out tasks (DESIGN.md Section 17): what the page knows of this Mac's agents, the people
  // an owner can be, the project's runs and what is open in each task's row. The programs and every limit
  // on them are the engine's; the page asks, shows words and follows a run by polling.
  const confirmedAgents = new Set();   // owners the person has already accepted on this page load (17.1)
  let agentInfo = null, agentInfoError = null, agentInfoLoading = false;
  let people = { loaded: false, loading: false, me: null, team: [] };
  let runsInfo = { runs: [], active: null }, details = {}, detailPending = {};
  let agentUiState = {}, agentBusy = {}, agentNotes = {}, sections = {}, metaNodes = {}, wordsNodes = {}, rowControls = {}, rowNodes = {};
  let offline = false;
  let settingsOpen = false, settingsBusy = false, settingsNote = null, settingsInputs = blankInputs(), modelsState = {};
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
  function documentDirty() { return !!draft && !!saved && (draft.title !== saved.title || draft.content !== saved.content); }
  function taskDirty() { return !!(taskDraft.id || taskDraft.text || taskDraft.owner || taskDraft.due); }
  function createDirty() {
    return !!createSubject && typeof state !== "undefined" && state.panelSubject === createSubject &&
      (createSubject.name.value || createSubject.parent.value) &&
      typeof panel !== "undefined" && !panel.classList.contains("hidden");
  }
  // Words typed into an open reply form and not yet sent (17.8) are unsaved writing too.
  function replyDirty() { return Object.keys(agentUiState).some((id) => !!agentUiState[id].reply && !!String(agentUiState[id].text).trim()); }
  function isDirty() { return documentDirty() || taskDirty() || replyDirty() || !!createDirty(); }
  function mayLeave() { return !isDirty() || window.confirm(t("有未保存的内容，仍要离开吗？")); }
  function blankDraft() { return { id: null, text: "", owner: "", due: "", custom: false }; }
  function blankInputs() { return { paths: {}, runner: "claude-code", model: "", customModel: false, minutes: null, budget: null }; }
  function reset() {
    epoch += 1; request += 1; docRequest += 1; createRequest += 1;
    host = null; data = null; draft = null; saved = null; ui = null; createSubject = null;
    foreignBlocked = false; knownCache = new Map(); unchecked = [];
    taskDraft = blankDraft(); taskBusy = false; taskConflict = false; taskWrites = 0;
    stopPolling(); offline = false;
    agentInfo = null; agentInfoError = null; agentInfoLoading = false;
    people = { loaded: false, loading: false, me: null, team: [] };
    runsInfo = { runs: [], active: null }; details = {}; detailPending = {};
    agentUiState = {}; agentBusy = {}; agentNotes = {}; sections = {}; metaNodes = {}; wordsNodes = {}; rowControls = {}; rowNodes = {};
    settingsOpen = false; settingsBusy = false; settingsNote = null; settingsInputs = blankInputs(); modelsState = {};
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
    return [own].concat(named.filter(knownFile));
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
    renderTasks();
  }

  // Deliberately small Markdown reader: DOM nodes only. User HTML remains text;
  // project images and links are resolved within the project and served locally.
  function inline(parent, text, from) {
    const pattern = /(!?\[((?:\\.|[^\]\\\n])*)\]\(([^)\n]+)\)|`([^`\n]+)`|\*\*([^*\n]+)\*\*)/g;
    let start = 0, match;
    while ((match = pattern.exec(text))) {
      parent.append(text.slice(start, match.index));
      if (match[4]) parent.append(el("code", null, match[4]));
      else if (match[5]) parent.append(el("strong", null, match[5]));
      else {
        const image = match[1].startsWith("!"), label = match[2].replace(/\\([\[\]\\])/g, "$1"), target = match[3];
        const local = resolvePath(from, target);
        const known = !!local && knownFile(local);
        if (local && !known) wantLookup(local);
        if (image && known && /\.(png|jpe?g|gif|webp)$/i.test(local)) {
          const img = el("img", "ws-image");
          img.src = "/api/workspace/asset?path=" + encodeURIComponent(local);
          img.alt = label; img.loading = "lazy";
          parent.append(img);
        } else if (known) {
          const a = el("a", "ws-file-link", label || local);
          a.href = "/api/file?path=" + encodeURIComponent(local);
          a.addEventListener("click", (event) => {
            event.preventDefault();
            if (typeof openFilePanel === "function") openFilePanel(local);
          });
          parent.append(a);
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
  function setDraft(doc) {
    draft = { path: doc.path || null, kind: doc.kind || "note", title: doc.title || "", content: doc.content || "", revision: doc.revision == null ? null : doc.revision, officialness: officialnessOf(doc) };
    saved = { title: draft.title, content: draft.content };
    renderEditor(); renderNav();
  }
  async function openDocument(path, quiet, discard) {
    if (!quiet && !discard && documentDirty() && !window.confirm(t("当前文档还没有保存。放弃这些修改并打开另一份吗？"))) return;
    const token = ++docRequest, mark = stamp();
    const leaving = draft, leavingTitle = draft && draft.title, leavingContent = draft && draft.content;
    if (!quiet) status(ui && ui.docStatus, t("正在读取…"));
    try {
      const doc = await apiGet("/api/workspace/document?path=" + encodeURIComponent(path) + "&project=" + encodeURIComponent(project()));
      if (!live(mark) || token !== docRequest || (doc.project && doc.project !== mark.project)) return;
      if (draft === leaving && draft && (draft.title !== leavingTitle || draft.content !== leavingContent)) {
        status(ui.docStatus, t("读取期间有新的输入，已保留当前文档。请再次选择要打开的文档。")); return;
      }
      if (quiet && !discard && documentDirty()) return;
      if (quiet && draft && doc.path === draft.path && doc.revision === draft.revision) {
        draft.officialness = officialnessOf(doc); // the same text: only its word may have moved
        drawOfficialness();
        return;
      }
      setDraft(doc);
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
  function renderEditor() {
    const box = ui.editor; box.replaceChildren();
    const header = el("div", "ws-editor-header"), label = el("span", "ws-kind", kindLabel(draft.kind));
    const tools = el("div", "ws-actions"), save = button(t("保存"), saveDocument, "btn btn-primary btn-small");
    save.disabled = !editable();
    const previewToggle = button(t("预览"), () => {
      ui.previewing = !ui.previewing;
      ui.title.classList.toggle("hidden", ui.previewing);
      ui.text.classList.toggle("hidden", ui.previewing); ui.preview.classList.toggle("hidden", !ui.previewing);
      previewToggle.textContent = ui.previewing ? t("继续编辑") : t("预览");
      previewToggle.setAttribute("aria-pressed", String(ui.previewing));
      if (ui.previewing) renderPreview();
      else ui.text.focus();
    });
    previewToggle.setAttribute("aria-pressed", "false");
    ui.kindLabel = label; drawOfficialness();
    tools.append(previewToggle, save); header.append(label, tools);
    const title = input("text", t("标题")); title.value = draft.title; title.disabled = !editable();
    title.className = "ws-title-input"; title.setAttribute("aria-label", t("标题"));
    const text = el("textarea", "ws-writing"); text.value = draft.content; text.disabled = !editable(); text.spellcheck = false;
    text.placeholder = draft.kind === "overview" ? t("研究问题、思路与计划") : draft.kind === "result" ? t("结果、解释与待确认的问题") : t("写笔记…");
    text.setAttribute("aria-label", kindLabel(draft.kind));
    const preview = el("div", "ws-preview hidden"), docStatus = el("div", "ws-status"); docStatus.setAttribute("role", "status");
    ui.title = title; ui.text = text; ui.preview = preview; ui.save = save; ui.docStatus = docStatus; ui.previewing = false;
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
      saved = { title: response.title == null ? payload.title : response.title, content: response.content == null ? contentSent : response.content };
      if (unchanged) { draft.title = saved.title; draft.content = saved.content; renderEditor(); }
      else { ui.save.disabled = !editable(); carryHeading(titleSent, contentSent, response.content, wasNew, shown); }
      const summary = { path: draft.path, kind: draft.kind, title: saved.title, excerpt: saved.content.slice(0, 100), officialness: draft.officialness, modified: response.modified || new Date().toISOString() };
      const index = (data.documents || []).findIndex((d) => d.path === draft.path);
      if (index < 0) data.documents.push(summary); else data.documents[index] = summary;
      renderNav();
      status(ui.docStatus, documentDirty() ? t("已保存；之后输入的内容尚未保存。") : response.history_error ? t("文件已保存，但修改历史暂时未能记录。") : t("已保存"), documentDirty() || response.history_error ? "" : "ok");
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
    list.replaceChildren();
    sections = {}; metaNodes = {}; wordsNodes = {}; rowControls = {}; rowNodes = {};
    showTaskError();
    (data.tasks || []).forEach((task) => {
      const run = latestRun(task.id);
      const row = el("div", "ws-task" + (task.done ? " done" : ""));
      const check = keyed(input("checkbox"), "check:" + task.id); check.checked = !!task.done;
      check.setAttribute("aria-label", (task.done ? t("标为未完成") : t("标为完成")) + " " + task.text);
      check.addEventListener("change", () => mutateTasks(data.tasks.map((x) => x.id === task.id ? Object.assign({}, x, { done: check.checked }) : x)));
      const words = el("div", "ws-task-words"); words.append(el("span", "ws-task-text", task.text));
      const meta = el("span", "ws-task-meta"); metaNodes[task.id] = meta; setMeta(task, meta); words.append(meta);
      wordsNodes[task.id] = words; rowNodes[task.id] = row;
      if (parseAgent(task.owner) || run) { sections[task.id] = agentSection(task); words.append(sections[task.id]); }
      const edit = keyed(button(t("编辑"), () => {
        if (taskDirty() && !window.confirm(t("放弃当前任务里尚未保存的填写吗？"))) return;
        taskDraft = { id: task.id, text: task.text, owner: task.owner || "", due: task.due || "", custom: false }; renderTasks(); ui.taskText.focus();
      }), "edit:" + task.id);
      const remove = keyed(button(t("移除"), () => { if (window.confirm(t("移除这项任务吗？"))) mutateTasks(data.tasks.filter((x) => x.id !== task.id)); }), "remove:" + task.id);
      named(edit, t("编辑：{task}", { task: task.text })); named(remove, t("移除：{task}", { task: task.text }));
      rowControls[task.id] = [check, edit, remove]; lockRow(task);
      row.append(check, words, edit, remove); list.append(row);
    });
    restoreFocus(list, snap);
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
    [text, owner, name, due, submit].forEach((node) => { if (node) node.disabled = !tasksEditable() || taskBusy || taskConflict; });
    function changed() { taskDraft = Object.assign({}, taskDraft, { text: text.value, due: due.value }); }
    [text, due].forEach((node) => node.addEventListener("input", changed));
    owner.addEventListener("change", () => chooseOwner(owner));
    form.addEventListener("submit", (event) => { event.preventDefault(); addTask(); });
    const ownerField = field(t("负责人"), owner), dateField = field(t("日期"), due);
    ownerField.className += " ws-field-owner"; dateField.className += " ws-field-date";
    form.append(field(t("任务", { n: 1 }), text), ownerField);
    if (name) { const nameField = field(t("姓名"), name); nameField.className += " ws-field-name"; form.append(nameField); }
    form.append(dateField, submit);
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
    const next = (data.tasks || []).map((task) => Object.assign({}, task));
    const existing = next.find((task) => task.id === taskDraft.id);
    if (taskDraft.id && !existing) { status(ui.taskStatus, t("这项任务已被移除。可以把当前填写添加为新任务。"), "err"); taskDraft.id = null; return; }
    const item = { id: taskDraft.id || (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : "task-" + Date.now().toString(36) + Math.random().toString(36).slice(2)), text: taskDraft.text.trim(), owner: taskDraft.owner.trim(), due: taskDraft.due, done: existing ? !!existing.done : false };
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
      if (clearForm && snapshot === JSON.stringify(taskDraft)) taskDraft = blankDraft();
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
      const old = sections[task.id];
      if (!old) {
        // The runs of a project arrive after its tasks are drawn.
        if (wordsNodes[task.id] && (parseAgent(task.owner) || latestRun(task.id))) { sections[task.id] = agentSection(task); wordsNodes[task.id].append(sections[task.id]); }
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
    if (task.report) actions.append(keyed(named(button(t("打开结果"), () => openReport(task.report)), t("打开结果：{task}", who)), k("report")));
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
    if (settingsOpen) { settingsOpen = false; renderTasks(); return; }
    openSettings();
  }
  function openSettings() {
    settingsOpen = true; renderTasks();
    // The programs may have been installed since the page opened.
    if (agentInfo) loadAgentInfo(stamp(), true);
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
  function currentAgents() { return ((agentInfo && agentInfo.agents) || []).map((a) => ({ runner: a.runner, model: a.model })); }
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
  }
  async function loadModels(runner, refresh) {
    if (runner === "claude-code" || runner === "deepseek-harness") return;
    const catalog = modelsState[runner] || {};
    if (catalog.loading || (!refresh && (catalog.list || catalog.error))) return;
    const found = agentInfo && (agentInfo.runners || []).find((r) => r.runner === runner);
    if (!found || !found.found) return;
    const mark = stamp();
    modelsState[runner] = { loading: true }; fillModelList();
    try {
      const response = await apiGet("/api/agents/models?runner=" + encodeURIComponent(runner));
      if (!live(mark)) return;
      modelsState[runner] = { list: Array.isArray(response && response.models) ? response.models : [] };
    } catch (err) {
      if (!live(mark)) return;
      modelsState[runner] = { error: err };
    }
    fillModelList();
  }
  function agentsBlock() {
    const box = el("div", "ws-agent-list");
    box.append(el("h4", "ws-runner-name", t("这台电脑上的 AI 执行者")));
    const agents = agentInfo.agents || [];
    if (!agents.length) box.append(quiet(t("还没有添加。在下面选一个程序，写上模型名。")));
    agents.forEach((a) => {
      const row = el("div", "ws-agent-item");
      row.append(el("span", "ws-agent-label", a.label));
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
    ui.modelList.addEventListener("change", () => {
      settingsInputs.customModel = ui.modelList.value === "__custom__";
      settingsInputs.model = settingsInputs.customModel ? "" : ui.modelList.value;
      fillModelList(); if (settingsInputs.customModel) model.focus();
    });
    runner.addEventListener("change", () => {
      settingsInputs.runner = runner.value; settingsInputs.model = ""; settingsInputs.customModel = false;
      fillModelList(); loadModels(runner.value);
    });
    model.addEventListener("input", () => { settingsInputs.model = model.value; });
    const add = button(t("添加"), () => {
      const name = model.value.trim();
      if (!name) { settingsNote = { words: t("请写上模型名。"), kind: "err" }; renderSettings(true); return; }
      saveAgentSettings({ agents: currentAgents().concat([{ runner: runner.value, model: name }]) }, t("已添加"), () => { settingsInputs.model = ""; settingsInputs.customModel = false; });
    }); keyed(add, "set-add"); add.disabled = runner.disabled = model.disabled = settingsBusy;
    const refresh = button(t("刷新模型列表"), () => loadModels(settingsInputs.runner, true));
    const suggested = () => runner.value === "claude-code" || runner.value === "deepseek-harness";
    refresh.disabled = settingsBusy; refresh.classList.toggle("hidden", suggested());
    runner.addEventListener("change", () => refresh.classList.toggle("hidden", suggested()));
    form.append(field(t("程序"), runner), field(t("模型版本"), ui.modelList), ui.modelCustom, add, refresh);
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
    box.append(agentsBlock(), limitBlock(), budgetBlock());
    ui.settingsNote = el("p"); ui.settingsNote.setAttribute("role", "status"); box.append(ui.settingsNote); showSettingsNote();
    restoreFocus(box, snap);
    loadModels(settingsInputs.runner);
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

  window.ROWorkspace = { load: load, mayLeave: mayLeave, reset: reset, openCreateProject: openCreateProject, isDirty: isDirty, projectChangedElsewhere: projectChangedElsewhere, resume: resumePolling, stop: stopForGood };
  window.addEventListener("beforeunload", (event) => { if (isDirty()) { event.preventDefault(); event.returnValue = ""; } });
})();
