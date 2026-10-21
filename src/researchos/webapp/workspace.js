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
  function button(words, action, cls) {
    const node = el("button", cls || "btn btn-small", t(words));
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  }
  function field(words, input) {
    const label = el("label", "ws-field");
    label.append(el("span", "ws-field-label", t(words, { n: 1 })), input);
    return label;
  }
  function input(type, placeholder) {
    const node = el("input", "form-input");
    node.type = type || "text";
    if (placeholder) node.placeholder = t(placeholder);
    return node;
  }
  function status(node, words, kind) {
    if (!node) return;
    node.textContent = words || "";
    node.className = "ws-status" + (kind ? " " + kind : "");
  }
  function errorWords(words, err) {
    return t(words) + (err && (err.engineMessage || err.messageZh || err.message) ? " " + (err.engineMessage || err.messageZh || err.message) : "");
  }

  let epoch = 0, request = 0, docRequest = 0, createRequest = 0;
  let host = null, data = null, draft = null, saved = null, ui = null;
  let taskDraft = blankDraft(), taskBusy = false, taskConflict = false;
  let createSubject = null, foreignBlocked = false;
  // AI agents carry out tasks (DESIGN.md Section 17): what the page knows of this Mac's agents, the people
  // an owner can be, the project's runs and what is open in each task's row. The programs and every limit
  // on them are the engine's; the page asks, shows words and follows a run by polling.
  const confirmedAgents = new Set();   // owners the person has already accepted on this page load (17.1)
  let agentInfo = null, agentInfoError = null, agentInfoLoading = false;
  let people = { loaded: false, loading: false, me: null, team: [] };
  let runsInfo = { runs: [], active: null }, details = {}, detailPending = {};
  let agentUiState = {}, agentBusy = {}, agentNotes = {}, sections = {}, metaNodes = {}, wordsNodes = {}, rowControls = {};
  let settingsOpen = false, settingsBusy = false, settingsNote = null, settingsInputs = blankInputs(), modelsState = {};
  let pollTimer = null, pollFails = 0;
  const kindWords = { overview: "研究问题与计划", note: "笔记", result: "结果记录" };
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
  function isDirty() { return documentDirty() || taskDirty() || !!createDirty(); }
  function mayLeave() { return !isDirty() || window.confirm(t("有未保存的内容，仍要离开吗？")); }
  function blankDraft() { return { id: null, text: "", owner: "", due: "", custom: false }; }
  function blankInputs() { return { paths: {}, runner: "claude-code", model: "", minutes: null }; }
  function reset() {
    epoch += 1; request += 1; docRequest += 1; createRequest += 1;
    host = null; data = null; draft = null; saved = null; ui = null; createSubject = null;
    foreignBlocked = false;
    taskDraft = blankDraft(); taskBusy = false; taskConflict = false;
    stopPolling();
    agentInfo = null; agentInfoError = null; agentInfoLoading = false;
    people = { loaded: false, loading: false, me: null, team: [] };
    runsInfo = { runs: [], active: null }; details = {}; detailPending = {};
    agentUiState = {}; agentBusy = {}; agentNotes = {}; sections = {}; metaNodes = {}; wordsNodes = {}; rowControls = {};
    settingsOpen = false; settingsBusy = false; settingsNote = null; settingsInputs = blankInputs(); modelsState = {};
  }

  function kindLabel(kind) { return t(kindWords[kind] || "笔记", { n: 1 }); }
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
  function referencedFiles() {
    const result = new Set([draft.path]);
    const links = /!?\[(?:\\.|[^\]\\\n])*\]\(([^)\n]+)\)/g;
    let match;
    while ((match = links.exec(draft.content))) {
      const path = resolvePath(draft.path, match[1]);
      if (path && (data.files || []).some((file) => file.path === path)) result.add(path);
    }
    return Array.from(result);
  }

  function projectChangedElsewhere() {
    if (!data || foreignBlocked || !ui) return;
    foreignBlocked = true;
    const banner = el("div", "ws-readonly ws-retargeted");
    const feedback = el("p", "ws-status", t("项目已在其他窗口切换，当前草稿已保留。"));
    const reopen = button("重新打开这个项目", async () => {
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
        status(feedback, errorWords("没能重新打开项目，草稿仍保留在这里。", err), "err");
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
        const known = local && data && (data.files || []).some((f) => f.path === local);
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
    node.replaceChildren();
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
    const note = button("新建笔记", () => newDocument("note"));
    const result = button("记录结果", () => newDocument("result"));
    note.disabled = result.disabled = !editable();
    actions.append(note, result); top.append(heading, actions);
    const columns = el("div", "ws-columns"), editor = el("section", "ws-editor-host"), tasks = el("section", "ws-tasks");
    columns.append(rail, editor); page.append(top, columns, tasks);
    if ((data.errors || []).length) page.prepend(el("p", "ws-status err", t("部分文档暂时无法读取，其他内容仍可使用。")));
    if (data.read_only) page.prepend(el("p", "ws-readonly", t("项目以只读方式打开。可以阅读，暂时不能保存。")));
    host.append(page); ui = { rail: rail, editor: editor, tasks: tasks, note: note, result: result };
  }
  function renderNav() {
    if (!ui) return;
    ui.rail.replaceChildren();
    ["overview", "note", "result"].forEach((kind) => {
      const group = el("div", "ws-document-group");
      group.append(el("h3", "ws-group-title", kind === "note" ? t("笔记", { n: 2 }) : kindLabel(kind)));
      const documents = (data.documents || []).filter((d) => d.kind === kind);
      documents.forEach((doc) => {
        const b = button(doc.title || kindLabel(kind), () => openDocument(doc.path), "ws-document");
        // Authored titles are never translated as UI strings.
        b.textContent = doc.title || kindLabel(kind);
        b.setAttribute("aria-current", draft && draft.path === doc.path ? "page" : "false");
        if (doc.excerpt) b.title = doc.excerpt;
        if (doc.error) { b.append(el("span", "ws-unavailable", t("暂时无法读取"))); b.title = doc.error.message || ""; }
        group.append(b);
      });
      if (!documents.length) group.append(el("p", "ws-empty", t(kind === "overview" ? "还没有研究方案。" : kind === "note" ? "还没有笔记。" : "还没有结果记录。")));
      ui.rail.append(group);
    });
  }
  function setDraft(doc) {
    const listed = data && (data.documents || []).find((item) => item.path === doc.path);
    draft = { path: doc.path || null, kind: doc.kind || "note", title: doc.title || "", content: doc.content || "", revision: doc.revision == null ? null : doc.revision, officialness: doc.officialness || (listed && listed.officialness) || null };
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
      if (quiet && draft && doc.path === draft.path && doc.revision === draft.revision) return;
      setDraft(doc);
    } catch (err) {
      if (!live(mark) || token !== docRequest) return;
      if (!draft && ui) { ui.editor.replaceChildren(el("p", "ws-status err", errorWords("没能读取文档。", err)), button("重试", () => openDocument(path))); }
      else status(ui && ui.docStatus, errorWords("没能读取文档。", err), "err");
    }
  }
  function newDocument(kind) {
    if (!editable()) return;
    if (documentDirty() && !window.confirm(t("当前文档还没有保存。放弃这些修改并新建吗？"))) return;
    docRequest += 1;
    setDraft({ kind: kind, title: "", content: "", revision: null });
    ui.title.focus();
  }
  function previewContent() {
    const title = draft.title.trim(), text = draft.content;
    if (!title) return text;
    // Show the same heading that saving will keep, including edits made in the body.
    if (draft.revision == null) return text.split(/\r?\n/)[0] === "# " + title ? text : "# " + title + "\n\n" + text;
    if (title === saved.title) return text;
    const heading = /^#{1,6}[ \t]+.+$/m;
    return heading.test(text) ? text.replace(heading, () => "# " + title) : "# " + title + "\n\n" + text;
  }
  function renderEditor() {
    const box = ui.editor; box.replaceChildren();
    const header = el("div", "ws-editor-header"), label = el("span", "ws-kind", kindLabel(draft.kind));
    const tools = el("div", "ws-actions"), save = button("保存", saveDocument, "btn btn-primary btn-small");
    save.disabled = !editable();
    const previewToggle = button("预览", () => {
      ui.previewing = !ui.previewing;
      ui.title.classList.toggle("hidden", ui.previewing);
      ui.text.classList.toggle("hidden", ui.previewing); ui.preview.classList.toggle("hidden", !ui.previewing);
      previewToggle.textContent = t(ui.previewing ? "继续编辑" : "预览");
      previewToggle.setAttribute("aria-pressed", String(ui.previewing));
      if (ui.previewing) renderMarkdown(ui.preview, previewContent(), draft.path || fallbackPath(draft.kind));
      else ui.text.focus();
    });
    previewToggle.setAttribute("aria-pressed", "false");
    if (draft.officialness && draft.officialness.label) label.append(el("span", "ws-officialness", draft.officialness.label));
    tools.append(previewToggle, save); header.append(label, tools);
    const title = input("text", "标题"); title.value = draft.title; title.disabled = !editable();
    title.className = "ws-title-input"; title.setAttribute("aria-label", t("标题"));
    const text = el("textarea", "ws-writing"); text.value = draft.content; text.disabled = !editable(); text.spellcheck = false;
    text.placeholder = t(draft.kind === "overview" ? "研究问题、思路与计划" : draft.kind === "result" ? "结果、解释与待确认的问题" : "写笔记…");
    text.setAttribute("aria-label", kindLabel(draft.kind));
    const preview = el("div", "ws-preview hidden"), docStatus = el("div", "ws-status"); docStatus.setAttribute("role", "status");
    ui.title = title; ui.text = text; ui.preview = preview; ui.save = save; ui.docStatus = docStatus; ui.previewing = false;
    function changed() { draft.title = title.value; draft.content = text.value; status(docStatus, documentDirty() ? t("尚未保存") : ""); }
    title.addEventListener("input", changed); text.addEventListener("input", changed);
    text.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") { event.preventDefault(); if (!save.disabled) saveDocument(); } });
    box.append(header, title, attachmentTools(), text, preview, docStatus);
    const footer = el("div", "ws-document-footer");
    if (draft.path) footer.append(button("查看文件", () => { if (typeof openFilePanel === "function") openFilePanel(draft.path); }));
    if (draft.path) footer.append(button("修改历史", () => { if (window.RONode) window.RONode.openNode(draft.path); }));
    if (data.mode === "member") {
      const propose = button("提交修改", () => {
        if (documentDirty()) { status(docStatus, t("请先保存，再提交修改。")); return; }
        if (typeof window.ROWorkspace.onPropose === "function") window.ROWorkspace.onPropose(referencedFiles());
      });
      propose.disabled = !editable() || !draft.path; footer.append(propose);
      footer.append(el("p", "ws-empty", t("保存到自己的工作副本；提交并经 PI 批准后进入正式版本。")));
    }
    box.append(footer);
  }
  function insertText(words) {
    const text = ui.text, start = text.selectionStart == null ? text.value.length : text.selectionStart;
    const end = text.selectionEnd == null ? start : text.selectionEnd;
    text.value = text.value.slice(0, start) + words + text.value.slice(end);
    draft.content = text.value;
    if (text.setSelectionRange) text.setSelectionRange(start + words.length, start + words.length);
    status(ui.docStatus, t("尚未保存"));
    if (ui.previewing) renderMarkdown(ui.preview, previewContent(), draft.path || fallbackPath(draft.kind));
    else text.focus();
  }
  function attachmentTools() {
    const tools = el("details", "ws-attachments"); tools.append(el("summary", null, t("引用文件或尝试")));
    ui.attachments = tools;
    const row = el("div", "ws-attachment-row"), select = el("select", "form-input");
    select.setAttribute("aria-label", t("选择项目文件"));
    const blank = el("option", null, t("选择项目文件")); blank.value = ""; select.append(blank);
    (data.files || []).forEach((file) => { const option = el("option", null, file.path); option.value = file.path; select.append(option); });
    const attach = button("插入引用", () => {
      if (!select.value) return;
      const image = /\.(png|jpe?g|gif|webp)$/i.test(select.value);
      insertText("\n" + markdownLink(draft.path || fallbackPath(draft.kind), select.value, image) + "\n");
    }); attach.disabled = !editable(); row.append(select, attach); tools.append(row);
    if ((data.attempts || []).length) {
      const attemptRow = el("div", "ws-attachment-row"), attempts = el("select", "form-input");
      attempts.setAttribute("aria-label", t("选择研究尝试"));
      const empty = el("option", null, t("选择研究尝试")); empty.value = ""; attempts.append(empty);
      data.attempts.forEach((attempt) => { const option = el("option", null, attempt.label || attempt.id); option.value = attempt.id; attempts.append(option); });
      const insert = button("引用尝试", () => {
        const attempt = data.attempts.find((a) => a.id === attempts.value); if (!attempt) return;
        insertText("\n[" + String(attempt.label || attempt.id).replace(/[[\]\\]/g, "\\$&") + "](researchos:attempt/" + encodeURIComponent(attempt.id).replace(/[()]/g, (c) => c === "(" ? "%28" : "%29") + ")\n");
      }); insert.disabled = !editable(); attemptRow.append(attempts, insert); tools.append(attemptRow);
    }
    const refreshStatus = el("div", "ws-status");
    const refresh = button("刷新文件", async () => {
      const mark = stamp(); refresh.disabled = true;
      status(refreshStatus, t("正在读取…"));
      try {
        const fresh = await apiGet("/api/workspace");
        if (!live(mark) || fresh.project !== project()) return;
        data.files = fresh.files || []; data.attempts = fresh.attempts || [];
        redrawAttachments();
        status(ui.attachments.querySelector(".ws-reference-status"), t("文件列表已更新。"));
      } catch (err) {
        if (live(mark)) { refresh.disabled = false; status(refreshStatus, errorWords("没能读取项目文件。", err), "err"); }
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
    status(ui.docStatus, errorWords("文件已在其他地方修改，当前草稿已保留。", err), "err");
    const row = el("div", "ws-conflict-actions");
    row.append(button("保留草稿", () => row.remove()), button("重新载入文件", () => {
      if (window.confirm(t("重新载入会放弃当前文档里尚未保存的修改，继续吗？"))) openDocument(draft.path, true, true);
    }));
    ui.docStatus.append(row);
  }
  async function saveDocument() {
    if (!draft || !editable() || ui.save.disabled) return;
    if (draft.kind !== "overview" && !draft.title.trim()) { status(ui.docStatus, t("请写一个标题。"), "err"); ui.title.focus(); return; }
    const mark = stamp(), target = draft, payload = { project: project(), kind: draft.kind, title: draft.title.trim(), content: draft.content, revision: draft.revision };
    if (draft.path) payload.path = draft.path;
    const titleSent = draft.title, contentSent = draft.content;
    ui.save.disabled = true; status(ui.docStatus, t("正在保存…"));
    try {
      const response = await apiPost("/api/workspace/document", payload);
      if (!live(mark) || draft !== target) return;
      draft.path = response.path; draft.revision = response.revision; draft.officialness = response.officialness || draft.officialness;
      const unchanged = draft.title === titleSent && draft.content === contentSent;
      saved = { title: response.title == null ? payload.title : response.title, content: response.content == null ? contentSent : response.content };
      if (unchanged) { draft.title = saved.title; draft.content = saved.content; renderEditor(); }
      else ui.save.disabled = !editable();
      const summary = { path: draft.path, kind: draft.kind, title: saved.title, excerpt: saved.content.slice(0, 100), officialness: draft.officialness };
      const index = (data.documents || []).findIndex((d) => d.path === draft.path);
      if (index < 0) data.documents.push(summary); else data.documents[index] = summary;
      renderNav();
      status(ui.docStatus, documentDirty() ? t("已保存；之后输入的内容尚未保存。") : response.history_error ? t("文件已保存，但修改历史暂时未能记录。") : t("已保存"), documentDirty() || response.history_error ? "" : "ok");
      if (typeof pollGeneration === "function") pollGeneration();
    } catch (err) {
      if (!live(mark) || draft !== target) return;
      ui.save.disabled = !editable();
      if (err && /conflict|revision|changed/.test(err.state || "")) documentConflict(err);
      else status(ui.docStatus, errorWords("没能保存，当前内容已保留。", err), "err");
    }
  }

  function renderTasks() {
    const box = ui.tasks; box.replaceChildren();
    sections = {}; metaNodes = {}; wordsNodes = {}; rowControls = {};
    const header = el("div", "ws-task-header"); header.append(el("h2", "ws-task-title", t("任务", { n: 2 }))); box.append(header);
    if (data.tasks_officialness && data.tasks_officialness.label) header.append(el("span", "ws-officialness", data.tasks_officialness.label));
    const agents = button("AI 执行者", toggleSettings); agents.setAttribute("aria-pressed", String(settingsOpen)); header.append(agents);
    ui.settings = el("div", "ws-agent-settings hidden"); box.append(ui.settings);
    const list = el("div", "ws-task-list");
    (data.tasks || []).forEach((task) => {
      const run = latestRun(task.id);
      const row = el("div", "ws-task" + (task.done ? " done" : ""));
      const check = input("checkbox"); check.checked = !!task.done;
      check.setAttribute("aria-label", t(task.done ? "标为未完成" : "标为完成") + " " + task.text);
      check.addEventListener("change", () => mutateTasks(data.tasks.map((x) => x.id === task.id ? Object.assign({}, x, { done: check.checked }) : x)));
      const words = el("div", "ws-task-words"); words.append(el("span", "ws-task-text", task.text));
      const meta = el("span", "ws-task-meta"); metaNodes[task.id] = meta; setMeta(task, meta); words.append(meta);
      wordsNodes[task.id] = words;
      if (parseAgent(task.owner) || run) { sections[task.id] = agentSection(task); words.append(sections[task.id]); }
      const edit = button("编辑", () => {
        if (taskDirty() && !window.confirm(t("放弃当前任务里尚未保存的填写吗？"))) return;
        taskDraft = { id: task.id, text: task.text, owner: task.owner || "", due: task.due || "", custom: false }; renderTasks(); ui.taskText.focus();
      });
      const remove = button("移除", () => { if (window.confirm(t("移除这项任务吗？"))) mutateTasks(data.tasks.filter((x) => x.id !== task.id)); });
      rowControls[task.id] = [check, edit, remove]; lockRow(task);
      row.append(check, words, edit, remove); list.append(row);
    }); box.append(list);
    const form = el("form", "ws-task-form"), text = input("text", "例如：整理下一次组会要讨论的问题"); text.value = taskDraft.text;
    const owner = el("select", "form-input"); ui.ownerSelect = owner; fillOwnerOptions(owner);
    const name = taskDraft.custom ? input("text", "姓名（可留空）") : null;
    if (name) { name.value = taskDraft.owner; name.addEventListener("input", () => { taskDraft.owner = name.value; }); }
    ui.ownerName = name;
    const due = input("date"); due.value = taskDraft.due;
    const submit = button(taskDraft.id ? "保存任务" : "添加任务", () => addTask(), "btn btn-small");
    [text, owner, name, due, submit].forEach((node) => { if (node) node.disabled = !tasksEditable() || taskBusy || taskConflict; });
    function changed() { taskDraft = Object.assign({}, taskDraft, { text: text.value, due: due.value }); }
    [text, due].forEach((node) => node.addEventListener("input", changed));
    owner.addEventListener("change", () => chooseOwner(owner));
    form.addEventListener("submit", (event) => { event.preventDefault(); addTask(); });
    const ownerField = field("负责人", owner), dateField = field("日期", due);
    ownerField.className += " ws-field-owner"; dateField.className += " ws-field-date";
    form.append(field("任务", text), ownerField);
    if (name) { const nameField = field("姓名", name); nameField.className += " ws-field-name"; form.append(nameField); }
    form.append(dateField, submit);
    form.append(button("清空", () => { taskDraft = blankDraft(); renderTasks(); }));
    if (data.tasks_error) box.append(el("p", "ws-status err", data.tasks_error.message || t("任务文件暂时无法读取，其他文档仍可编辑。")));
    const taskStatus = el("div", "ws-status"); taskStatus.setAttribute("role", "status");
    box.append(form, taskStatus); ui.taskText = text; ui.taskStatus = taskStatus;
    if (taskConflict) showTaskConflict();
    if (data.mode === "member") {
      const propose = button("提交修改", () => {
        if (taskDirty() || taskBusy) { status(ui.taskStatus, t("请先保存任务，再提交修改。")); return; }
        if (typeof window.ROWorkspace.onPropose === "function") window.ROWorkspace.onPropose(["research/tasks.json"]);
      }); propose.disabled = !tasksEditable(); box.append(propose);
    }
    renderSettings();
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
    status(ui.taskStatus, t("任务已在其他地方修改，当前填写已保留。请重新载入后再保存。"), "err");
    ui.taskStatus.append(button("重新载入任务", async () => {
      const mark = stamp();
      try {
        const fresh = await apiGet("/api/workspace");
        if (!live(mark)) return;
        data.tasks = fresh.tasks; data.tasks_revision = fresh.tasks_revision; data.tasks_error = fresh.tasks_error; taskConflict = false; renderTasks();
        status(ui.taskStatus, t("已载入最新任务，当前填写仍保留。"));
      } catch (err) { if (live(mark)) status(ui.taskStatus, errorWords("没能读取任务。", err), "err"); }
    }));
  }
  async function mutateTasks(tasks, clearForm) {
    if (!tasksEditable() || taskBusy || taskConflict) return;
    const mark = stamp(), snapshot = JSON.stringify(taskDraft);
    taskBusy = true; renderTasks(); status(ui.taskStatus, t("正在保存…"));
    try {
      const response = await apiPost("/api/workspace/tasks", { project: project(), tasks: tasks, revision: data.tasks_revision });
      if (!live(mark)) return;
      data.tasks = response.tasks; data.tasks_revision = response.tasks_revision; data.tasks_officialness = response.tasks_officialness || data.tasks_officialness; taskBusy = false;
      if (clearForm && snapshot === JSON.stringify(taskDraft)) taskDraft = blankDraft();
      renderTasks(); status(ui.taskStatus, response.history_error ? t("任务已保存，但修改历史暂时未能记录。") : t("已保存"), response.history_error ? "" : "ok");
      if (typeof pollGeneration === "function") pollGeneration();
    } catch (err) {
      if (!live(mark)) return;
      taskBusy = false; taskConflict = !!(err && /conflict|revision|changed/.test(err.state || ""));
      renderTasks();
      if (!taskConflict) status(ui.taskStatus, errorWords("没能保存任务，当前填写已保留。", err), "err");
    }
  }

  // -- AI agents carry out tasks (DESIGN.md Section 17) ---------------------------------------
  // A task whose owner is an agent on this Mac gets its run in the row: 运行, then the state in
  // words (17.9), 停止, 运行记录, 回答 / 打回, 撤回本次运行 and 打开结果. The page only asks the
  // engine and follows a run by polling; what an agent may not do is held there (17.5).
  const RUNNER_NAMES = { "claude-code": "Claude Code", opencode: "OpenCode" };
  const PROGRAMS = { "claude-code": "claude", opencode: "opencode" };
  const OWNER_OTHER = "\u0000other", OWNER_SETTINGS = "\u0000settings";
  const POLL_MS = 2000;
  const REPLY_FROM = ["done", "ended", "failed", "stopped", "interrupted"];
  const MCP_INSTALL = ['uv tool install --force --with "mcp<2" git+https://github.com/LinyingL/research-os', 'pip install "researchos[mcp]"'];
  // state code -> [words, tone] (tone classes are app.html's .status-label.tone-*).
  const STATE_LABELS = {
    preparing: ["准备中", "ochre"], running: ["运行中", "ochre"], needs_input: ["等你回答", "clay"], done: ["已完成", "olive"],
    ended: ["未报告", "gray"], failed: ["失败", "clay"], stopped: ["已停止", "gray"], interrupted: ["已中断", "gray"],
  };
  const STEP_KINDS = { start: "开始", say: "它说", tool: "操作", refused: "被拒绝", error: "出错", end: "结束" };
  const VIOLATION_WORDS = {
    data_changed: "改动了运行前就有的数据文件", data_deleted: "删除了运行前就有的数据文件",
    record_changed: "改动了研究记录", record_deleted: "删除了研究记录",
    researchos_changed: "改动了 Research OS 自己的记录文件夹", git_changed: "改变了 git 的状态",
  };
  const ERROR_WORDS = {
    timeout: "运行超过了时间限制，已被停止。", shutdown: "运行期间 Research OS 被关闭了。", engine_gone: "运行期间 Research OS 退出了。",
    aborted: "程序在给出结果之前就结束了。", exit: "程序异常退出。", model_error: "模型返回了错误。", start_failed: "程序没能启动。",
    check_failed: "运行结束后核对改动时出了错，改动清单可能不完整。", error_max_budget_usd: "费用达到了上限。",
  };
  const REVERT_SKIPPED = { changed_since: "运行之后又被改动过", no_copy: "没有运行前的备份" };
  const PRIVACY = {
    "claude-code": "Claude Code 会把它读到的项目内容发送给 Anthropic。",
    opencode: "OpenCode 会把它读到的项目内容发送给你在 OpenCode 里配置的模型服务，也可能是这台电脑上的本地服务。",
  };

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
  function activeIds() { return runsInfo.runs.filter((r) => isActiveState(r.state)).map((r) => r.id); }
  function anyActive() { return activeIds().length > 0 || !!runsInfo.active; }
  function latestRun(taskId) { return runsInfo.runs.find((r) => r.task_id === taskId) || null; }
  function agentState(taskId) { return agentUiState[taskId] || (agentUiState[taskId] = { log: false, reply: null, text: "", open: {} }); }
  // A <details> (12.11) that stays as the person left it when the row is drawn again by the next poll.
  function more(st, key, words, content) {
    const box = el("details", "ws-agent-more");
    box.open = !!st.open[key];
    box.addEventListener("toggle", () => { st.open[key] = box.open; });
    box.append(el("summary", null, t(words)), el("pre", "ws-code", content));
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
  // Who an owner can be: me and, in a team, its members. /api/share reads the shared folder and may be
  // slow, so it never holds the page; a failure leaves the list as it is.
  async function loadPeople(mark) {
    if (people.loaded || people.loading) return;
    people.loading = true;
    try {
      const mine = await apiGet("/api/me").catch(() => null);
      if (!live(mark)) return;
      const me = mine && mine.me && typeof mine.me.name === "string" && mine.me.name.trim() ? mine.me : null;
      people.me = me ? me.name.trim() : null; refreshOwnerOptions();
      if (["pi", "member"].includes(data && data.mode)) {
        const share = await apiGet("/api/share").catch(() => null);
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
      pollFails += 1; if (pollFails < 5) schedulePoll();
      return false;
    }
    if (!live(mark)) return false;
    pollFails = 0; applyRuns(fresh);
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
  function schedulePoll() {
    if (pollTimer !== null || !host || !anyActive() || !onWorkspace()) return;
    const mark = stamp();
    pollTimer = setTimeout(() => { pollTimer = null; if (live(mark) && onWorkspace()) syncRuns(mark, {}); }, POLL_MS);
  }
  function stopPolling() { if (pollTimer !== null) clearTimeout(pollTimer); pollTimer = null; pollFails = 0; }
  // The page calls this when the project home is shown again after another section: polling stops
  // when the workspace is not the view on screen.
  function resumePolling() {
    if (!host || !data || pollTimer !== null) return;
    syncRuns(stamp(), {});
  }
  function ensureDetails(mark) {
    Object.keys(agentUiState).forEach((taskId) => {
      const run = agentUiState[taskId].log && latestRun(taskId), known = run && details[run.id];
      if (run && (!known || known.state !== run.state || isActiveState(run.state))) fetchDetail(run, mark);
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
  function agentSig(task) {
    const run = latestRun(task.id), st = agentState(task.id), d = run && details[run.id];
    return JSON.stringify([task.owner, task.done, task.report || "", run, st.log, st.reply, !!agentBusy[task.id], agentNotes[task.id] || null,
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
      const fresh = agentSection(task);
      old.replaceWith(fresh); sections[task.id] = fresh;
    });
  }
  function runLabel(run) {
    const [words, tone] = run.reverted ? ["已撤回本次运行", "gray"] : STATE_LABELS[run.state] || ["", "plain"];
    return el("span", "status-label tone-" + tone, words ? t(words) : String(run.state || ""));
  }
  function agentSection(task) {
    const box = el("div", "ws-agent"), run = latestRun(task.id), st = agentState(task.id), agent = parseAgent(task.owner);
    box.dataset.sig = agentSig(task);
    const busy = !!agentBusy[task.id], allowed = may() && !busy, active = !!run && isActiveState(run.state);
    const line = el("div", "ws-agent-line");
    if (run) {
      line.append(runLabel(run));
      if (run.changes && !active) line.append(el("span", "ws-agent-meta", t("改动了 {n} 个文件", { n: run.changes })));
    }
    if (line.childElementCount) box.append(line);
    if (run && run.state === "needs_input" && run.question) box.append(el("p", "ws-agent-question", run.question));
    if (run && run.violations > 0) box.append(el("p", "ws-agent-oob", t("有 {n} 处越界改动，详见运行记录。", { n: run.violations })));
    if (run && run.error) box.append(errorBlock(run, st));
    const actions = el("div", "ws-agent-actions");
    if (agent && !task.done && (!run || (!active && !(run.state === "needs_input" && run.can_reply)))) {
      const go = button("运行", () => startRun(task, agent));
      go.disabled = !allowed || anyActive();
      if (allowed && anyActive()) go.title = t("这个项目里还有一次运行没有结束。");
      actions.append(go);
    }
    if (run && run.can_stop) { const stop = button("停止", () => stopRun(task, run)); stop.disabled = busy; actions.append(stop); }
    if (run) {
      const log = button("运行记录", () => toggleLog(task)); log.setAttribute("aria-expanded", String(st.log)); actions.append(log);
    }
    if (run && run.state === "needs_input" && run.can_reply) {
      const answer = button("回答", () => toggleReply(task, "answer")); answer.setAttribute("aria-expanded", String(st.reply === "answer"));
      answer.disabled = !allowed; actions.append(answer);
    }
    if (run && REPLY_FROM.includes(run.state) && run.can_reply) {
      const back = button("打回", () => toggleReply(task, "send_back")); back.setAttribute("aria-expanded", String(st.reply === "send_back"));
      back.disabled = !allowed; actions.append(back);
    }
    if (run && run.can_revert) { const undo = button("撤回本次运行", () => revertRun(task, run)); undo.disabled = !allowed; actions.append(undo); }
    if (task.report) actions.append(button("打开结果", () => openReport(task.report)));
    if (actions.childElementCount) box.append(actions);
    const note = agentNotes[task.id];
    if (note) box.append(noteBlock(note));
    if (run && st.reply) box.append(replyForm(task, run, st));
    if (run && st.log) box.append(runDetails(run, details[run.id], st));
    return box;
  }
  function errorBlock(run, st) {
    const box = el("div", "ws-agent-error"), code = run.error && run.error.code;
    box.append(el("p", "ws-status err", t(Object.prototype.hasOwnProperty.call(ERROR_WORDS, code) ? ERROR_WORDS[code] : "这次运行没有成功。")));
    if (run.error && run.error.detail) box.append(more(st, "error", "详情", String(run.error.detail)));
    return box;
  }
  function installBlock() {
    const box = el("div", "ws-install");
    MCP_INSTALL.forEach((command) => box.append(el("code", "ws-install-cmd", command)));
    return box;
  }
  function mcpWords() { return t("运行 AI 执行者需要 mcp 组件，这个 Research OS 里还没有安装。按你安装 Research OS 的方式，运行下面其中一条命令，然后重新打开 Research OS："); }
  function noteBlock(note) {
    const box = el("div", "ws-agent-note");
    box.append(el("p", "ws-status" + (note.kind ? " " + note.kind : ""), note.words));
    if (note.install) box.append(installBlock());
    if (note.skipped && note.skipped.length) {
      box.append(el("p", "ws-agent-quiet", t("这些文件没有动：")));
      const list = el("ul", "ws-agent-files");
      note.skipped.forEach((item) => {
        const li = el("li"); li.append(el("code", null, item.path));
        li.append(" " + t(Object.prototype.hasOwnProperty.call(REVERT_SKIPPED, item.reason) ? REVERT_SKIPPED[item.reason] : "没有改动这个文件"));
        list.append(li);
      });
      box.append(list);
    }
    return box;
  }
  function setNote(taskId, words, kind, extra) {
    agentNotes[taskId] = words ? Object.assign({ words: words, kind: kind || "" }, extra || {}) : null;
  }

  // -- the actions ---------------------------------------------------------------------------------
  function confirmAgent(agent) {
    if (!agent || confirmedAgents.has(agent.owner)) return true;
    // 17.1: where the project's content goes, and that the agent changes files directly, before anything is sent.
    if (!window.confirm(t(PRIVACY[agent.runner]) + "\n\n" + t("运行时它会直接修改项目里的文件，不会先问你；运行之后可以撤回。要继续吗？"))) return false;
    confirmedAgents.add(agent.owner);
    return true;
  }
  // One request for one task at a time. opts: failed (the words when it is refused), after (on success),
  // reload (see syncRuns), done (the note on success, from the response).
  async function agentAction(task, call, opts) {
    if (agentBusy[task.id]) return;
    const mark = stamp(), before = activeIds();
    agentBusy[task.id] = true; setNote(task.id, null); updateAgentSections();
    let response;
    try { response = await call(); } catch (err) {
      if (!live(mark)) return;
      agentBusy[task.id] = false;
      setNote(task.id, errorWords(opts.failed, err), "err", { install: !!(err && err.state === "agent_mcp_missing") });
      updateAgentSections(); syncRuns(mark, { reload: false });
      return;
    }
    if (!live(mark)) return;
    agentBusy[task.id] = false;
    mergeRun(response && response.run ? response.run : response);
    if (opts.after) opts.after(response);
    if (opts.done) opts.done(response);
    updateAgentSections();
    await syncRuns(mark, { before: before, reload: opts.reload });
  }
  function startRun(task, agent) {
    if (agentInfo && agentInfo.mcp === false) {
      setNote(task.id, mcpWords(), "err", { install: true }); updateAgentSections(); return;
    }
    if (!confirmAgent(agent)) return;
    return agentAction(task, () => apiPost("/api/agents/runs", { project: project(), task: task.id }), { failed: "没能开始这次运行。" });
  }
  function stopRun(task, run) {
    return agentAction(task, () => apiPost("/api/agents/runs/" + encodeURIComponent(run.id) + "/stop", { project: project() }), { failed: "没能停止这次运行。" });
  }
  function revertRun(task, run) {
    if (!window.confirm(t("撤回这次运行吗？它新建的文件会被移到运行的文件夹里，改过的文件会恢复成运行前的样子；运行之后又被改动的文件不会动。"))) return;
    return agentAction(task, () => apiPost("/api/agents/runs/" + encodeURIComponent(run.id) + "/revert", { project: project() }), {
      failed: "没能撤回这次运行。", reload: true,
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
  }
  async function openReport(path) {
    await openDocument(path);
    if (ui && ui.editor && typeof ui.editor.scrollIntoView === "function") ui.editor.scrollIntoView();
  }
  function replyForm(task, run, st) {
    const kind = st.reply, form = el("div", "ws-agent-reply"), area = el("textarea", "form-textarea");
    area.value = st.text; area.spellcheck = false;
    area.setAttribute("aria-label", t(kind === "answer" ? "你的回答" : "要它改什么"));
    area.placeholder = t(kind === "answer" ? "写下你的回答…" : "写下要它改动或补做的事…");
    area.addEventListener("input", () => { st.text = area.value; });
    const send = button("发送", () => {
      const message = st.text.trim();
      if (!message) { setNote(task.id, t("请先写下内容。"), "err"); updateAgentSections(); return; }
      if (!confirmAgent(parseAgent(run.owner))) return;
      return agentAction(task, () => apiPost("/api/agents/runs/" + encodeURIComponent(run.id) + "/reply", { project: project(), message: message }), {
        failed: "没能发送。", reload: true, after: () => { st.reply = null; st.text = ""; },
      });
    }, "btn btn-primary btn-small");
    const cancel = button("取消", () => { st.reply = null; st.text = ""; updateAgentSections(); });
    const actions = el("div", "ws-agent-actions"); actions.append(send, cancel);
    form.append(area, actions);
    return form;
  }

  // -- the run log: what it said and did, what it changed ---------------------------------------------
  function pathList(paths) {
    const list = el("ul", "ws-agent-files");
    paths.forEach((path) => { const li = el("li"); li.append(el("code", null, path)); list.append(li); });
    return list;
  }
  function runDetails(run, loaded, st) {
    const box = el("div", "ws-run-details");
    if (!loaded) { box.append(el("p", "ws-status", t("正在读取…"))); return box; }
    if (loaded.error || !loaded.data) { box.append(el("p", "ws-status err", errorWords("没能读取运行记录。", loaded.error))); return box; }
    const d = loaded.data;
    if (run.state !== "needs_input" && run.question) box.append(el("p", "ws-agent-question", run.question));
    if ((d.violations || []).length) {
      const group = el("div", "ws-run-group ws-oob");
      group.append(el("h4", "ws-run-heading", t("越界改动")));
      const list = el("ul", "ws-agent-files");
      d.violations.forEach((v) => {
        const li = el("li"); li.append(el("code", null, v.path));
        li.append(" " + t(Object.prototype.hasOwnProperty.call(VIOLATION_WORDS, v.reason) ? VIOLATION_WORDS[v.reason] : "超出了允许的范围"));
        list.append(li);
      });
      group.append(list); box.append(group);
    }
    const changes = d.changes || {};
    [["created", "新建的文件"], ["modified", "修改的文件"], ["deleted", "删除的文件"]].forEach(([key, words]) => {
      if (!(changes[key] || []).length) return;
      const group = el("div", "ws-run-group");
      group.append(el("h4", "ws-run-heading", t(words)), pathList(changes[key])); box.append(group);
    });
    if ((d.steps || []).length) {
      const group = el("div", "ws-run-group"), list = el("ol", "ws-steps");
      group.append(el("h4", "ws-run-heading", t("经过")));
      if (d.steps_total > d.steps.length) group.append(el("p", "ws-agent-quiet", t("只显示最近 {shown} 步，共 {n} 步。", { shown: d.steps.length, n: d.steps_total })));
      d.steps.forEach((step) => {
        const li = el("li", "ws-step" + (step.kind === "refused" || step.kind === "error" ? " bad" : ""));
        li.append(el("span", "ws-step-kind", t(Object.prototype.hasOwnProperty.call(STEP_KINDS, step.kind) ? STEP_KINDS[step.kind] : "操作")));
        li.append(el("span", "ws-step-text" + (step.kind === "tool" || step.kind === "refused" ? " mono" : ""), step.text || ""));
        list.append(li);
      });
      group.append(list); box.append(group);
    }
    if (typeof d.cost_usd === "number") {
      box.append(el("p", "ws-agent-quiet", t("费用约 {cost} 美元。", { cost: d.cost_usd.toFixed(d.cost_usd < 0.1 ? 3 : 2) })));
    }
    if (d.stderr_tail) box.append(more(st, "stderr", "程序的错误输出", d.stderr_tail));
    if (d.instruction) box.append(more(st, "instruction", "发给它的指示", d.instruction));
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
    settingsBusy = true; settingsNote = null; renderSettings();
    try {
      const response = await apiPost("/api/settings/agents", body);
      if (!live(mark)) return;
      agentInfo = response; agentInfoError = null; settingsNote = { words: t(doneWords), kind: "ok" };
      if (body.commands) settingsInputs.paths = {};
      if (onDone) onDone();
    } catch (err) {
      if (!live(mark)) return;
      settingsNote = { words: errorWords("没能保存设置。", err), kind: "err" };
    }
    settingsBusy = false;
    refreshOwnerOptions(); renderSettings(); updateAgentSections();
  }
  function currentAgents() { return ((agentInfo && agentInfo.agents) || []).map((a) => ({ runner: a.runner, model: a.model })); }
  function quiet(words, vars) { return el("p", "ws-agent-quiet", t(words, vars)); }
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
      const path = input("text");
      path.placeholder = t("程序的完整路径，例如 /usr/local/bin/{program}", { program: program });
      path.className += " form-mono"; path.value = settingsInputs.paths[r.runner] || ""; path.disabled = settingsBusy;
      path.addEventListener("input", () => { settingsInputs.paths[r.runner] = path.value; });
      const save = button("保存", () => {
        const value = path.value.trim();
        if (!value) { settingsNote = { words: t("请填写程序的完整路径。"), kind: "err" }; renderSettings(); return; }
        saveAgentSettings({ commands: { [r.runner]: value } }, "已保存");
      }); save.disabled = settingsBusy;
      const line = el("div", "ws-agent-add"); line.append(path, save); row.append(line);
    }
    row.append(quiet(PRIVACY[r.runner] || PRIVACY.opencode));
    if (r.runner === "opencode") row.append(quiet("OpenCode 没有逐步检查：运行结束后才核对哪些文件被改动，越界的改动会列出来，可以撤回。"));
    return row;
  }
  function fillModelList() {
    if (!ui || !ui.modelList) return;
    const runner = settingsInputs.runner;
    const names = runner === "claude-code" ? ((agentInfo && agentInfo.claude_models) || []) : (modelsState.list || []);
    ui.modelList.replaceChildren(...names.map((name) => { const option = el("option"); option.value = name; return option; }));
    if (ui.modelNote) {
      ui.modelNote.textContent = runner === "opencode" && modelsState.error ? errorWords("没能列出 OpenCode 的模型，可以直接写模型名。", modelsState.error)
        : runner === "opencode" && modelsState.loading ? t("正在读取…") : "";
    }
  }
  async function loadOpencodeModels() {
    if (modelsState.loading || modelsState.list) return;
    const found = agentInfo && (agentInfo.runners || []).find((r) => r.runner === "opencode");
    if (!found || !found.found) return;
    const mark = stamp();
    modelsState = { loading: true }; fillModelList();
    try {
      const response = await apiGet("/api/agents/models?runner=opencode");
      if (!live(mark)) return;
      modelsState = { list: Array.isArray(response && response.models) ? response.models : [] };
    } catch (err) {
      if (!live(mark)) return;
      modelsState = { error: err };
    }
    fillModelList();
  }
  function agentsBlock() {
    const box = el("div", "ws-agent-list");
    box.append(el("h4", "ws-runner-name", t("这台电脑上的 AI 执行者")));
    const agents = agentInfo.agents || [];
    if (!agents.length) box.append(quiet("还没有添加。在下面选一个程序，写上模型名。"));
    agents.forEach((a) => {
      const row = el("div", "ws-agent-item");
      row.append(el("span", "ws-agent-label", a.label));
      if (a.available === false) row.append(el("span", "ws-agent-meta bad", t("找不到它的程序")));
      const remove = button("移除", () => saveAgentSettings({ agents: currentAgents().filter((x) => x.runner + ":" + x.model !== a.runner + ":" + a.model) }, "已移除"));
      remove.disabled = settingsBusy; row.append(remove); box.append(row);
    });
    const form = el("div", "ws-agent-add"), runner = el("select", "form-input"), model = input("text", "模型名");
    (agentInfo.runners || []).forEach((r) => { const option = el("option", null, RUNNER_NAMES[r.runner] || r.label || r.runner); option.value = r.runner; runner.append(option); });
    runner.value = settingsInputs.runner; model.value = settingsInputs.model; model.className += " form-mono";
    model.setAttribute("list", "ws-model-list");
    ui.modelList = el("datalist"); ui.modelList.id = "ws-model-list"; ui.modelNote = el("p", "ws-agent-quiet");
    runner.addEventListener("change", () => { settingsInputs.runner = runner.value; fillModelList(); if (runner.value === "opencode") loadOpencodeModels(); });
    model.addEventListener("input", () => { settingsInputs.model = model.value; });
    const add = button("添加", () => {
      const name = model.value.trim();
      if (!name) { settingsNote = { words: t("请写上模型名。"), kind: "err" }; renderSettings(); return; }
      saveAgentSettings({ agents: currentAgents().concat([{ runner: runner.value, model: name }]) }, "已添加", () => { settingsInputs.model = ""; });
    }); add.disabled = runner.disabled = model.disabled = settingsBusy;
    form.append(field("程序", runner), field("模型", model), add);
    box.append(form, ui.modelList, ui.modelNote);
    fillModelList();
    return box;
  }
  function limitBlock() {
    const row = el("div", "ws-agent-add"), minutes = input("number");
    minutes.min = "5"; minutes.max = "1440"; minutes.step = "1"; minutes.disabled = settingsBusy;
    minutes.value = settingsInputs.minutes == null ? String(agentInfo.timeout_minutes == null ? "" : agentInfo.timeout_minutes) : settingsInputs.minutes;
    minutes.addEventListener("input", () => { settingsInputs.minutes = minutes.value; });
    const save = button("保存", () => {
      const value = Number(minutes.value);
      if (!Number.isFinite(value) || value < 5 || value > 1440) { settingsNote = { words: t("时间限制要在 5 到 1440 分钟之间。"), kind: "err" }; renderSettings(); return; }
      settingsInputs.minutes = null;
      saveAgentSettings({ timeout_minutes: Math.round(value) }, "已保存");
    }); save.disabled = settingsBusy;
    row.append(field("每次运行的时间限制（分钟）", minutes), save);
    return row;
  }
  function renderSettings() {
    if (!ui || !ui.settings) return;
    const box = ui.settings; box.replaceChildren(); box.classList.toggle("hidden", !settingsOpen);
    if (!settingsOpen) return;
    box.append(el("h3", "ws-agent-heading", t("AI 执行者")));
    box.append(quiet("负责人可以选这台电脑上的 AI 执行者：运行时它会直接在项目文件夹里做这项任务，写代码、运行程序、写笔记和结果。"));
    if (!agentInfo) {
      if (agentInfoError) {
        box.append(el("p", "ws-status err", errorWords("没能读取 AI 执行者的设置。", agentInfoError)));
        box.append(button("重试", () => loadAgentInfo(stamp(), true)));
      } else box.append(el("p", "ws-status", t("正在读取…")));
      return;
    }
    if (agentInfo.mcp === false) { const warn = el("div", "ws-agent-warn"); warn.append(el("p", "ws-status err", mcpWords()), installBlock()); box.append(warn); }
    (agentInfo.runners || []).forEach((r) => box.append(runnerBlock(r)));
    box.append(agentsBlock(), limitBlock());
    if (settingsNote) box.append(el("p", "ws-status " + settingsNote.kind, settingsNote.words));
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
    const current = stamp(), thisRequest = request;
    try {
      const fresh = await apiGet("/api/workspace");
      if (!live(current) || thisRequest !== request || fresh.project !== project()) return;
      const retainTasks = taskDirty() || taskBusy || taskConflict;
      const oldTasks = data && data.tasks, oldRevision = data && data.tasks_revision, oldTaskError = data && data.tasks_error;
      data = fresh; data.documents = data.documents || [];
      if (retainTasks && oldTasks) { data.tasks = oldTasks; data.tasks_revision = oldRevision; if (oldTaskError) data.tasks_error = oldTaskError; }
      if (!ui || target !== ui.root) { buildPage(); ui.root = target; }
      renderNav(); if (!retainTasks || !ui.taskText) renderTasks();
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
      if (ui && ui.docStatus) status(ui.docStatus, errorWords("没能读取项目首页。", err), "err");
      else host.replaceChildren(el("p", "ws-status err", errorWords("没能读取项目首页。", err)), button("重试", () => load(target, refresh)));
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
    const form = el("form", "ws-create"), name = input("text", "项目名称"), parent = input("text", "例如 /Users/你的名字/Documents");
    const subject = { kind: "workspace-create", name: name, parent: parent };
    createSubject = subject; state.panelSubject = subject;
    [name, parent].forEach((node) => node.addEventListener("input", () => { node.dataset.typed = "1"; }));
    panelTitle.textContent = t("新建项目"); panelSubtitle.textContent = ""; panelBody.replaceChildren();
    form.append(field("项目名称", name), field("保存位置", parent));
    const choose = button("选择文件夹…", async () => {
      const mark = stamp();
      const selected = await chooseFolder("create");
      if (selected && createLive(subject, mark)) { parent.value = selected; parent.dataset.typed = "1"; }
    });
    const feedback = el("div", "ws-status"); feedback.setAttribute("role", "status");
    const create = button("创建项目", async () => {
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
        create.disabled = name.disabled = parent.disabled = choose.disabled = false; status(feedback, errorWords("没能创建项目。", err), "err");
      }
    }, "btn btn-primary");
    form.addEventListener("submit", (event) => { event.preventDefault(); if (!create.disabled) create.click(); });
    form.append(choose, create, feedback); panelBody.append(form); openPanel(); name.focus();
  }

  window.ROWorkspace = { load: load, mayLeave: mayLeave, reset: reset, openCreateProject: openCreateProject, isDirty: isDirty, projectChangedElsewhere: projectChangedElsewhere, resume: resumePolling };
  window.addEventListener("beforeunload", (event) => { if (isDirty()) { event.preventDefault(); event.returnValue = ""; } });
})();
