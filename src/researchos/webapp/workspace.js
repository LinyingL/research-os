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
    label.append(el("span", "ws-field-label", t(words)), input);
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
  let taskDraft = { id: null, text: "", owner: "", due: "" }, taskBusy = false, taskConflict = false;
  let createSubject = null, foreignBlocked = false;
  const kindWords = { overview: "研究问题与计划", note: "笔记", result: "结果整理" };
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
  function reset() {
    epoch += 1; request += 1; docRequest += 1; createRequest += 1;
    host = null; data = null; draft = null; saved = null; ui = null; createSubject = null;
    foreignBlocked = false;
    taskDraft = { id: null, text: "", owner: "", due: "" }; taskBusy = false; taskConflict = false;
  }

  function kindLabel(kind) { return t(kindWords[kind] || "笔记"); }
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
    heading.append(el("h2", "ws-project-name", data.project_name || data.name || (typeof state !== "undefined" && state.projectLabel) || t("项目首页")),
      el("p", "ws-intro", t("从研究问题写起，随时记下想法和结果。")));
    const actions = el("div", "ws-actions");
    const note = button("写笔记", () => newDocument("note"));
    const result = button("整理结果", () => newDocument("result"));
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
      group.append(el("h3", "ws-group-title", kindLabel(kind)));
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
      if (!documents.length) group.append(el("p", "ws-empty", t(kind === "overview" ? "还没有研究方案。" : kind === "note" ? "随时记下一个想法。" : "图表、发现和判断放在这里。")));
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
  function renderEditor() {
    const box = ui.editor; box.replaceChildren();
    const header = el("div", "ws-editor-header"), label = el("span", "ws-kind", kindLabel(draft.kind));
    const tools = el("div", "ws-actions"), save = button("保存", saveDocument, "btn btn-primary btn-small");
    save.disabled = !editable();
    const previewToggle = button("预览", () => {
      ui.previewing = !ui.previewing;
      ui.text.classList.toggle("hidden", ui.previewing); ui.preview.classList.toggle("hidden", !ui.previewing);
      previewToggle.textContent = t(ui.previewing ? "继续编辑" : "预览");
      previewToggle.setAttribute("aria-pressed", String(ui.previewing));
      if (ui.previewing) renderMarkdown(ui.preview, draft.content, draft.path || fallbackPath(draft.kind));
      else ui.text.focus();
    });
    previewToggle.setAttribute("aria-pressed", "false");
    if (draft.officialness && draft.officialness.label) label.append(el("span", "ws-officialness", draft.officialness.label));
    tools.append(previewToggle, save); header.append(label, tools);
    const title = input("text", "标题"); title.value = draft.title; title.disabled = !editable();
    title.className = "ws-title-input"; title.setAttribute("aria-label", t("标题"));
    const text = el("textarea", "ws-writing"); text.value = draft.content; text.disabled = !editable(); text.spellcheck = false;
    text.placeholder = t(draft.kind === "overview" ? "你想研究什么？为什么值得研究？准备怎样探索？可以边做边改。" : draft.kind === "result" ? "放入图表，写下发现、解释和仍不确定的地方。" : "记下想法、讨论或一个还没想清楚的问题。");
    text.setAttribute("aria-label", kindLabel(draft.kind));
    const preview = el("div", "ws-preview hidden"), docStatus = el("div", "ws-status"); docStatus.setAttribute("role", "status");
    ui.title = title; ui.text = text; ui.preview = preview; ui.save = save; ui.docStatus = docStatus; ui.previewing = false;
    function changed() { draft.title = title.value; draft.content = text.value; status(docStatus, documentDirty() ? t("尚未保存") : ""); }
    title.addEventListener("input", changed); text.addEventListener("input", changed);
    text.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") { event.preventDefault(); if (!save.disabled) saveDocument(); } });
    box.append(header, title, attachmentTools(), text, preview, docStatus);
    const footer = el("div", "ws-document-footer");
    footer.append(el("span", "ws-path", draft.path || t("保存后写入项目文件夹")));
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
    if (ui.previewing) renderMarkdown(ui.preview, draft.content, draft.path || fallbackPath(draft.kind));
    else text.focus();
  }
  function attachmentTools() {
    const tools = el("details", "ws-attachments"); tools.append(el("summary", null, t("关联文件与尝试")));
    ui.attachments = tools;
    const row = el("div", "ws-attachment-row"), select = el("select", "form-input");
    select.setAttribute("aria-label", t("选择项目文件"));
    const blank = el("option", null, t("选择项目文件")); blank.value = ""; select.append(blank);
    (data.files || []).forEach((file) => { const option = el("option", null, file.path); option.value = file.path; select.append(option); });
    const attach = button("插入文件", () => {
      if (!select.value) return;
      const image = /\.(png|jpe?g|gif|webp)$/i.test(select.value);
      insertText("\n" + markdownLink(draft.path || fallbackPath(draft.kind), select.value, image) + "\n");
    }); attach.disabled = !editable(); row.append(select, attach); tools.append(row);
    if ((data.attempts || []).length) {
      const attemptRow = el("div", "ws-attachment-row"), attempts = el("select", "form-input");
      attempts.setAttribute("aria-label", t("选择研究尝试"));
      const empty = el("option", null, t("选择研究尝试")); empty.value = ""; attempts.append(empty);
      data.attempts.forEach((attempt) => { const option = el("option", null, attempt.label || attempt.id); option.value = attempt.id; attempts.append(option); });
      const insert = button("插入尝试", () => {
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
    tools.append(refresh, refreshStatus, el("p", "ws-empty", t("引用保留在文档中。脚本仍用 Stata 等工具编辑和运行。")));
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
      status(ui.docStatus, documentDirty() ? t("已保存；之后输入的内容尚未保存。") : response.history_error ? t("文件已保存，但修改历史暂时未能记录。") : response.backup ? t("已保存，修改前的版本已保留。") : t("已保存"), documentDirty() || response.history_error ? "" : "ok");
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
    const header = el("div", "ws-task-header"); header.append(el("h2", "ws-task-title", t("接下来想做的事")), el("p", "ws-empty", t("按需记任务、负责人和日期，也可以暂时不安排。"))); box.append(header);
    if (data.tasks_officialness && data.tasks_officialness.label) header.append(el("span", "ws-officialness", data.tasks_officialness.label));
    const list = el("div", "ws-task-list");
    (data.tasks || []).forEach((task) => {
      const row = el("div", "ws-task" + (task.done ? " done" : ""));
      const check = input("checkbox"); check.checked = !!task.done; check.disabled = !tasksEditable() || taskBusy || taskConflict;
      check.setAttribute("aria-label", t(task.done ? "标为未完成" : "标为完成") + " " + task.text);
      check.addEventListener("change", () => mutateTasks(data.tasks.map((x) => x.id === task.id ? Object.assign({}, x, { done: check.checked }) : x)));
      const words = el("div", "ws-task-words"); words.append(el("span", "ws-task-text", task.text));
      if (task.owner || task.due) words.append(el("span", "ws-task-meta", [task.owner, task.due].filter(Boolean).join(" · ")));
      const edit = button("编辑", () => {
        if (taskDirty() && !window.confirm(t("放弃当前任务里尚未保存的填写吗？"))) return;
        taskDraft = { id: task.id, text: task.text, owner: task.owner || "", due: task.due || "" }; renderTasks(); ui.taskText.focus();
      });
      const remove = button("移除", () => { if (window.confirm(t("移除这项任务吗？"))) mutateTasks(data.tasks.filter((x) => x.id !== task.id)); });
      edit.disabled = remove.disabled = !tasksEditable() || taskBusy || taskConflict;
      row.append(check, words, edit, remove); list.append(row);
    }); box.append(list);
    const form = el("form", "ws-task-form"), text = input("text", "例如：整理下一次组会要讨论的问题"); text.value = taskDraft.text;
    const owner = input("text", "姓名（可留空）"); owner.value = taskDraft.owner;
    const due = input("date"); due.value = taskDraft.due;
    const submit = button(taskDraft.id ? "保存任务" : "添加任务", () => addTask(), "btn btn-small");
    [text, owner, due, submit].forEach((node) => { node.disabled = !tasksEditable() || taskBusy || taskConflict; });
    function changed() { taskDraft = { id: taskDraft.id, text: text.value, owner: owner.value, due: due.value }; }
    [text, owner, due].forEach((node) => node.addEventListener("input", changed));
    form.addEventListener("submit", (event) => { event.preventDefault(); addTask(); });
    form.append(field("任务", text), field("负责人", owner), field("日期", due), submit);
    form.append(button("取消填写", () => { taskDraft = { id: null, text: "", owner: "", due: "" }; renderTasks(); }));
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
  }
  async function addTask() {
    if (!tasksEditable() || taskBusy || taskConflict) return;
    if (!taskDraft.text.trim()) { status(ui.taskStatus, t("请写下要做的事。"), "err"); ui.taskText.focus(); return; }
    const next = (data.tasks || []).map((task) => Object.assign({}, task));
    const existing = next.find((task) => task.id === taskDraft.id);
    if (taskDraft.id && !existing) { status(ui.taskStatus, t("这项任务已被移除。可以把当前填写添加为新任务。"), "err"); taskDraft.id = null; return; }
    const item = { id: taskDraft.id || (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : "task-" + Date.now().toString(36) + Math.random().toString(36).slice(2)), text: taskDraft.text.trim(), owner: taskDraft.owner.trim(), due: taskDraft.due, done: existing ? !!existing.done : false };
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
      if (clearForm && snapshot === JSON.stringify(taskDraft)) taskDraft = { id: null, text: "", owner: "", due: "" };
      renderTasks(); status(ui.taskStatus, response.history_error ? t("任务已保存，但修改历史暂时未能记录。") : t("已保存"), response.history_error ? "" : "ok");
      if (typeof pollGeneration === "function") pollGeneration();
    } catch (err) {
      if (!live(mark)) return;
      taskBusy = false; taskConflict = !!(err && /conflict|revision|changed/.test(err.state || ""));
      renderTasks();
      if (!taskConflict) status(ui.taskStatus, errorWords("没能保存任务，当前填写已保留。", err), "err");
    }
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
    panelTitle.textContent = t("新建项目"); panelSubtitle.textContent = t("选一个位置，开始写研究问题与计划。"); panelBody.replaceChildren();
    form.append(field("项目名称", name), field("保存到哪个文件夹", parent));
    const choose = button("选择文件夹…", async () => {
      const mark = stamp();
      const selected = await chooseFolder("create");
      if (selected && createLive(subject, mark)) { parent.value = selected; parent.dataset.typed = "1"; }
    });
    const note = el("p", "ws-empty", t("会在所选位置建立项目文件夹。已有研究可以用「添加已有项目」加入。"));
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
    form.append(choose, note, create, feedback); panelBody.append(form); openPanel(); name.focus();
  }

  window.ROWorkspace = { load: load, mayLeave: mayLeave, reset: reset, openCreateProject: openCreateProject, isDirty: isDirty, projectChangedElsewhere: projectChangedElsewhere };
  window.addEventListener("beforeunload", (event) => { if (isDirty()) { event.preventDefault(); event.returnValue = ""; } });
})();
