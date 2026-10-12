// The interface language (DESIGN.md Section 13; task V9). Served at /i18n.js
// by the same process and loaded by app.html FIRST, before every other
// script, with a plain same-origin <script src>. The server writes one line
// above this file at serve time -- `window.ROI18N_SAVED = "zh" | "en" | null;`,
// the person's saved choice from settings.json (13.2) -- so the language is
// known synchronously, before anything is drawn, with no request.
//
// It defines one global, window.ROI18N:
//   lang    "zh" or "en": the saved choice; with none, the Mac's (13.2) --
//           inside the shell its own answer, `window.ROI18N_SYSTEM`, which
//           it injects at document start from Locale.preferredLanguages (a
//           web view in an app with no localizations may report English in
//           navigator.languages on a Chinese Mac); without it, the first of
//           navigator.languages: starting with "zh" is Chinese, anything else
//           (nothing known included) English;
//   saved   the saved choice, or null;
//   t(zh, vars)  the words for the screen (13.6): the Chinese text in the
//           source is the key; in English, its entry in the table below,
//           else the Chinese -- never a key, never a blank. {name} fields are
//           filled from vars; a plural entry {one, other} is chosen by
//           vars.n (one when it is 1);
//   en      the English table;
//   translateMarkup(root)  the page's static markup in English: an element
//           marked data-i18n gets t(its own text); data-i18n-attr="title
//           aria-label placeholder" (any of these) gets t() of each named
//           attribute's value; the switch (data-i18n-switch) is named in the
//           language it switches to (13.3). Applied to the document as this
//           file loads -- it is loaded after the markup -- and a no-op on a
//           Chinese screen, which stays exactly as written.
// It also sets <html lang> to zh-CN or en.
//
// Every other module looks t up at CALL time with this one helper pair, so a
// module evaluated alone (the node-run tests) is Chinese, exactly as before:
//   function fillVars(s, v) { ... }   (the same line as below)
//   function t(s, v) { var I = typeof window !== "undefined" && window.ROI18N; return I ? I.t(s, v) : fillVars(s, v); }
//
// English entries obey 13.4 (the glossary; sentence case) and 12.11: no
// arrows, no emoji; quotation marks “ ” where the Chinese has 「 」.
// tests/test_v9_language.py checks every key of a converted surface has an
// entry and every entry those rules.
(function () {
  "use strict";

  // -- the English table: Chinese source text to English (13.4, 13.6) ---------
  var EN = {
    // The switch (13.3).
    "切换界面语言": "Switch the interface language",
    "没能切换界面语言": "Could not switch the interface language",

    // The frame (13.7): the sidebar, the page titles, the stopped page.
    "项目与栏目": "Project and sections",
    "研究脉络": "Research provenance",
    "栏目": "Sections",
    "工作台": "Desk",
    "研究路线": "Research routes",
    "成果": "Outputs",
    "变量": "Variables",
    "文献": "Literature",
    "数据与代码": "Data & code",
    // The section, and with {n: 1} one proposal's label on the desk.
    "提议": { one: "Proposal", other: "Proposals" },
    // The section, and with {n: 1} the role (13.4: 成员 as a role is “Member”).
    "成员": { one: "Member", other: "Members" },
    "尚未加入团队": "Not in the team yet",
    "还没有设置你是谁": "Who you are is not set yet",
    "在「成员」一栏里设置": "Set it in “Members”",
    "{n} 条提议等你批准": { one: "{n} proposal awaiting your approval", other: "{n} proposals awaiting your approval" },
    "公盘：{folder}": "Shared folder: {folder}",
    "公盘现在打不开": "The shared folder cannot be opened right now",
    "还没有发布过": "Not published yet",
    "有改动尚未发布": "Changes not yet published",
    "已发布 {when}": "Published {when}",
    "公盘上有未通过验证的版本": "The shared folder holds a version that failed verification",
    "有新的官方版本": "A new official version is available",
    "未验证": "Unverified",
    "已同步 {when}": "Synced {when}",
    "已同步": "Synced",
    "记录": "Records",
    "查看这个项目的人工记录放在哪里": "See where this project’s human records are kept",
    "停止服务": "Stop service",
    "停止本地服务进程": "Stop the local service process",
    "确定要停止本地服务吗？停止后这个页面将无法继续使用。": "Stop the local service? Once it stops, this page can no longer be used.",
    "服务已停止": "Service stopped",
    "本地服务已经关闭，可以关闭此页。": "The local service has stopped. You can close this page.",
    "下次要查看时，双击 Research OS.app，或在终端运行 {command}。": "To look again, double-click Research OS.app or run {command} in Terminal.",
    // The project line, its switcher and menu, and the GitHub lines in it
    // (the state sentence itself comes from the engine in the request's language).
    "项目": "Project",
    "还没有项目": "No project yet",
    "切换项目或管理列表": "Switch project or manage the list",
    "添加项目": "Add project",
    "列表里还没有项目": "No projects in the list yet",
    "不在列表里": "Not in the list",
    "找不到文件夹": "Folder not found",
    "未初始化": "Not initialized",
    "选择新位置…": "Choose a new location…",
    "推送到 GitHub…": "Push to GitHub…",
    "添加项目…": "Add project…",
    "从公盘加入项目…": "Join a project from the shared folder…",
    "重新扫描这个项目": "Rescan this project",
    "重命名显示名称…": "Change display name…",
    "从列表中移除…": "Remove from the list…",
    "GitHub：": "GitHub: ",
    "正在从 GitHub 获取…": "Fetching from GitHub…",
    "从 GitHub 获取最新状态": "Fetch the latest state from GitHub",
    // The top bar's action button (the attempt form it opens is a later increment).
    "新增尝试": "New attempt",
    "提议新的尝试": "Propose a new attempt",
    // The welcome, shown while there is no project (10.1).
    "Research OS 读取一个研究文件夹里的脚本、数据和文稿，理清它们之间是怎么一步步连起来的。":
      "Research OS reads the scripts, data and drafts in a research folder and works out how they connect, step by step.",
    "你对这些关联做出的判断，Research OS 会替你记下来，和这项研究的文件放在一起。":
      "Research OS records the judgments you make about these links and keeps them with the study’s files.",
    "项目就是存放一项研究的那个文件夹。": "A project is the folder that holds one study.",

    // Dates (13.5): Today 09:24, Yesterday 18:02, 4 Oct, 4 Oct 2025.
    "今天 {time}": "Today {time}",
    "昨天 {time}": "Yesterday {time}",
    "{month}{day}日": "{day} {month}",
    "{year}年{month}{day}日": "{day} {month} {year}",
    "1月": "Jan", "2月": "Feb", "3月": "Mar", "4月": "Apr", "5月": "May", "6月": "Jun",
    "7月": "Jul", "8月": "Aug", "9月": "Sep", "10月": "Oct", "11月": "Nov", "12月": "Dec",

    // The desk (13.7). Verdict words by status code (13.4); a word the
    // researcher named in [verdict_labels] is shown as written.
    "现行": "Current",
    "已放弃": "Abandoned",
    "待定": "Pending",
    "不利证据": "Adverse evidence",
    "被否": "Rejected",
    "归档": "Archived",
    "其它": "Other",
    "、": ", ",
    "载入中…": "Loading…",
    "等你批准": "Awaiting your approval",
    "没有等你批准的提议。": "No proposals await your approval.",
    "我的提议": "My proposals",
    "你还没有提交过提议。": "You have not made a proposal yet.",
    "你还不是这个项目的成员：请 PI 把你加入团队，然后同步": "You are not a member of this project yet: ask the PI to add you to the team, then sync",
    // A proposal: its kind, what approving it would do, how it stands.
    "新尝试": "New attempt",
    "判决变更": "Verdict change",
    "变量定义": "Variable definition",
    "文件": "Files",
    "新增尝试 #{number}：{description}": "New attempt #{number}: {description}",
    "（无描述）": "(no description)",
    "#{number} 的判决从「{was}」改为「{became}」": "#{number}: verdict changed from “{was}” to “{became}”",
    "，放弃这条路线": ", abandoning the route",
    "变量 {card} 的第 {version} 版定义": "Variable {card}: definition version {version}",
    "，附带 {n} 个文件": { one: ", with {n} file", other: ", with {n} files" },
    "{n} 个文件：{names}": { one: "{n} file: {names}", other: "{n} files: {names}" },
    "{n} 个文件：{names} 等": "{n} files: {names} and others",
    "等待 PI 批准": "Awaiting PI approval",
    "等待 PI 批准 · PI 说：{text}": "Awaiting PI approval · The PI says: {text}",
    "批准与退回的记录互相矛盾": "The approval and return records contradict each other",
    "已批准": "Approved",
    "已退回": "Returned",
    "已退回：{reason}": "Returned: {reason}",
    "已被你后来的提议替代": "Replaced by your later proposal",
    "查看": "View",
    // Changes since you last looked.
    "你上次查看以来的改动": "Changes since you last looked",
    "还没有记录你上次查看的时间，这里列出过去 {n} 天的改动。": {
      one: "When you last looked is not recorded yet, so this lists the changes of the past {n} day.",
      other: "When you last looked is not recorded yet, so this lists the changes of the past {n} days.",
    },
    "上次查看：{when}": "Last looked: {when}",
    "这段时间里没有改动。": "No changes in this period.",
    "更早的改动没有列出，可以在各个文件的历史里查看。": "Earlier changes are not listed; see each file’s history.",
    "打开这个文件": "Open this file",
    "{what}（还没有提交）": "{what} (not committed yet)",
    "文件不存在": "File not found",
    "打开这条尝试": "Open this attempt",
    "判决 {was} 改为 {became}": "Verdict changed from {was} to {became}",
    "新增的尝试 {verdict}": "New attempt, verdict {verdict}",
    "结果改为：{result}": "Result changed to: {result}",
    "（空）": "(empty)",
    "放弃了这条路线": "Abandoned this route",
    "批准了一条新尝试提议": "Approved a proposal for a new attempt",
    "批准了一条判决变更提议": "Approved a proposal to change a verdict",
    "批准了一条变量定义提议": "Approved a proposal for a variable definition",
    "批准了一条文件提议": "Approved a proposal of files",
    "批准了一条提议": "Approved a proposal",
    "笔记，写在": "Note on",
    "一处记录": "a record",
    "提议人 {who}": "Proposed by {who}",
    "批准人 {who}": "Approved by {who}",
    "经 {who} 批准": "Approved by {who}",
    "这段时间里改了 {n} 次": { one: "Changed {n} time in this period", other: "Changed {n} times in this period" },
    "理由：{reason}": "Reason: {reason}",
    // Outputs whose basis changed.
    "依据变了的成果": "Outputs whose basis changed",
    "所有成果的来源都还在。": "Every output still has its sources.",
    "打开这个成果": "Open this output",
    "{label}：{path}": "{label}: {path}",
    "可以{repair}": "Fix with “{repair}”",
    "它的来源里有 {n} 条关联的判断待复核": {
      one: "{n} linked judgment in its sources needs review",
      other: "{n} linked judgments in its sources need review",
    },
    "复核": "Review",
    "文稿 {drafts} 引用了它": "Cited by {drafts}",
    "{n} 条判断的依据变了，等你复核。": {
      one: "The basis of {n} judgment changed; it awaits your review.",
      other: "The basis of {n} judgments changed; they await your review.",
    },
    // Abandoned routes.
    "已放弃的路线": "Abandoned routes",
    "还没有放弃过的路线。": "No route has been abandoned yet.",
    "一条尝试": "An attempt",
    "打开这条路线": "Open this route",
    "结果：{result}": "Result: {result}",
    "在研究路线中查看它们当时的依据": "See their basis at the time in Research routes",
    // A member's official version.
    "官方版本": "Official version",
    "官方版本有更新": "Official version updated",
    "这个工作副本还没有连上公盘。": "This working copy is not connected to the shared folder yet.",
    "公盘现在打不开，等它同步好再来看。": "The shared folder cannot be opened right now. Come back once it has synced.",
    "PI 于 {when} 发布了第 {seq} 版：{note}": "The PI published version {seq} ({when}): {note}",
    "PI 于 {when} 发布了第 {seq} 版。": "The PI published version {seq} ({when}).",
    "你的工作副本已经是最新的官方版本（第 {seq} 版）。": "Your working copy is the latest official version (version {seq}).",
    "PI 还没有发布过官方版本。": "The PI has not published an official version yet.",
    "正在查看同步会写入什么…": "Checking what syncing will write…",
    "没能查看同步会写入什么": "Could not check what syncing will write",
    "同步": "Sync",
    "正在同步…": "Syncing…",
    "没能同步，工作副本没有改动": "Could not sync; the working copy is unchanged",
  };

  // Each language's own name, written in itself: the switch's label (13.3).
  var NAMES = { zh: "中文", en: "English" };
  var MARKUP_ATTRS = ["title", "aria-label", "placeholder"];

  function fillVars(s, v) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return v && Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }); }

  function validLang(value) { return value === "zh" || value === "en" ? value : null; }

  // 13.2: with no saved choice, the Mac's first preferred language: the
  // shell's answer when it gave one ("zh" or "en", nothing else), then the
  // browser's.
  function macLang() {
    var shell = validLang(typeof window !== "undefined" ? window.ROI18N_SYSTEM : null);
    if (shell) return shell;
    var nav = typeof navigator !== "undefined" ? navigator : null;
    var first = nav ? ((nav.languages && nav.languages[0]) || nav.language || "") : "";
    return /^zh/i.test(String(first)) ? "zh" : "en";
  }

  var saved = validLang(typeof window !== "undefined" ? window.ROI18N_SAVED : null);
  var lang = saved || macLang();

  function t(zh, vars) {
    var s = zh;
    if (lang === "en" && Object.prototype.hasOwnProperty.call(EN, zh)) {
      var e = EN[zh];
      if (e && typeof e === "object") e = (vars && Number(vars.n) === 1) ? e.one : e.other;
      if (typeof e === "string" && e) s = e;
    }
    return fillVars(s, vars);
  }

  function translateMarkup(root) {
    if (lang !== "en" || !root || typeof root.querySelectorAll !== "function") return;
    var marked = root.querySelectorAll("[data-i18n], [data-i18n-attr]");
    for (var i = 0; i < marked.length; i++) {
      var el = marked[i];
      // Only an element holding text alone: its children are never dropped.
      if (el.hasAttribute("data-i18n") && el.children.length === 0) el.textContent = t(el.textContent.trim());
      var names = (el.getAttribute("data-i18n-attr") || "").split(/\s+/);
      for (var j = 0; j < names.length; j++) {
        if (MARKUP_ATTRS.indexOf(names[j]) >= 0 && el.hasAttribute(names[j])) {
          el.setAttribute(names[j], t(el.getAttribute(names[j])));
        }
      }
    }
    var switches = root.querySelectorAll("[data-i18n-switch]");
    for (var k = 0; k < switches.length; k++) {
      switches[k].textContent = NAMES.zh;
      switches[k].setAttribute("lang", "zh-CN");
    }
  }

  window.ROI18N = Object.freeze({
    lang: lang, saved: saved, t: t, fill: fillVars, translateMarkup: translateMarkup,
    en: Object.freeze(EN), names: Object.freeze(NAMES),
  });

  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.setAttribute("lang", lang === "en" ? "en" : "zh-CN");
    translateMarkup(document);
  }
})();
