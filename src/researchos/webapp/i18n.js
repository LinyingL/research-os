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
    "#{number} 的判决从「{was}」改为「{became}」": "#{number}: change the verdict from “{was}” to “{became}”",
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
    "复核": "Review",
    "{n} 条判断的依据变了，等你复核。": {
      one: "The basis of {n} judgment changed; it awaits your review.",
      other: "The basis of {n} judgments changed; they await your review.",
    },
    // The first desk's block titles (13.4) and words of its rows that other pages show too; since
    // V10 the desk is the route map (14.5).
    "你上次查看以来的改动": "Changes since you last looked",
    "依据变了的成果": "Outputs whose basis changed",
    "已放弃的路线": "Abandoned routes",
    "文件不存在": "File not found",
    "（空）": "(empty)",
    "笔记，写在": "Note on",
    "一条尝试": "An attempt",
    // A member's official version.
    "官方版本": "Official version",
    "官方版本有更新": "Official version updated",
    "PI 于 {when} 发布了第 {seq} 版：{note}": "The PI published version {seq} ({when}): {note}",
    "PI 于 {when} 发布了第 {seq} 版。": "The PI published version {seq} ({when}).",
    "你的工作副本已经是最新的官方版本（第 {seq} 版）。": "Your working copy is the latest official version (version {seq}).",
    "正在查看同步会写入什么…": "Checking what syncing will write…",
    "没能查看同步会写入什么": "Could not check what syncing will write",
    "同步": "Sync",
    "正在同步…": "Syncing…",
    "没能同步，工作副本没有改动": "Could not sync; the working copy is unchanged",
    // What 同步 will write and what it did (team.js, shared with 成员), and the
    // difference beside an .official copy (team.js differenceBlock, node.js diffBlock).
    "{n} 条笔记": { one: "{n} note", other: "{n} notes" },
    "{n} 条关联判断": { one: "{n} linked judgment", other: "{n} linked judgments" },
    "{n} 处「设为论文正文」的标记": { one: "{n} “Set as the paper” mark", other: "{n} “Set as the paper” marks" },
    "增加 {n} 条连线": { one: "{n} link added", other: "{n} links added" },
    "删除 {n} 条连线": { one: "{n} link removed", other: "{n} links removed" },
    "你的工作副本所依据的版本在公盘上已经无法验证，不能同步。":
      "The version your working copy is based on can no longer be verified in the shared folder, so it cannot sync.",
    "同步会先把你还没发送的 {sending}写进公盘的「{area}」：不需要批准，PI 下次发布时加入正式记录。":
      "Syncing first writes what you have not sent yet ({sending}) into “{area}” (your proposals folder) in the shared folder: no approval is needed, and the PI adds it to the official record at the next publish.",
    "{send}你的工作副本已经是最新的官方版本。": "{send} Your working copy is already the latest official version.",
    "你的工作副本已经是最新的官方版本，没有要发送的笔记、判断或连线。":
      "Your working copy is already the latest official version, and there are no notes, judgments or links to send.",
    "更新 {n} 个你没改过的文件": { one: "update {n} file you have not changed", other: "update {n} files you have not changed" },
    "删除 {n} 个正式版本已删除的文件": {
      one: "remove {n} file the official version deleted", other: "remove {n} files the official version deleted",
    },
    "{n} 个两边都改过的文件留着你的，把官方的一份放在旁边（.official）": {
      one: "keep your version of {n} file changed on both sides and put the official copy beside it (.official)",
      other: "keep your versions of {n} files changed on both sides and put the official copies beside them (.official)",
    },
    "笔记、判断和连线的记录文件：你的条目都已进入正式版本的，换成正式的那一份；还没进入的，留着你的":
      "replace each record file of notes, judgments and links with the official one where all your entries are in it, and keep yours where they are not yet",
    "：{parts}": ": {parts}",
    "；": "; ",
    "{send}然后把工作副本带到第 {seq} 版{parts}。从不合并，你改过的内容不会被覆盖。":
      "{send} Then it brings the working copy to version {seq}{parts}. It never merges: nothing you changed is overwritten.",
    "同步会把工作副本带到第 {seq} 版{parts}。从不合并，你改过的内容不会被覆盖。":
      "Syncing brings the working copy to version {seq}{parts}. It never merges: nothing you changed is overwritten.",
    "已把你的 {n} 条笔记、判断或连线改动发送到公盘": {
      one: "sent your {n} change to notes, judgments or links to the shared folder",
      other: "sent your {n} changes to notes, judgments or links to the shared folder",
    },
    "已经是最新的官方版本。": "Already the latest official version.",
    "已经是最新的官方版本；{sent}。": "Already the latest official version; {sent}.",
    "已同步到第 {seq} 版": "Synced to version {seq}",
    "更新了 {n} 个文件": { one: "updated {n} file", other: "updated {n} files" },
    "删除了 {n} 个正式版本已删除的文件": {
      one: "removed {n} file the official version deleted", other: "removed {n} files the official version deleted",
    },
    "{n} 个两边都改过的文件留着你的，官方的一份放在旁边": {
      one: "kept your version of {n} file changed on both sides, with the official copy beside it",
      other: "kept your versions of {n} files changed on both sides, with the official copies beside them",
    },
    "{n} 个文件留着你的（{reason}）": { one: "kept your version of {n} file ({reason})", other: "kept your versions of {n} files ({reason})" },
    "你的笔记和连线还在等 PI 发布，记录文件暂时留着你的":
      "your notes and links still wait for the PI to publish, so the record files stay yours for now",
    "{parts}。": "{parts}.",
    "官方的 {path} 放在了旁边：{copy}；你的没有动。": "The official {path} is beside yours as {copy}; yours was not changed.",
    "你的和官方的有什么不同": "How yours differs from the official one",
    "表格较大：只比较行和列，不逐行对比。": "A large table: only its rows and columns are compared, not line by line.",
    "两个文件不同（不是文本，没有逐行比较）": "The two files differ (not text, so not compared line by line)",
    "收起": "Show less",
    "显示其余 {n} 行": { one: "Show the remaining {n} line", other: "Show the remaining {n} lines" },
    "改动太长，只比较了开头的一部分。": "The change is too long; only its beginning was compared.",
    // People words (13.4), shared by every page (13.7); the engine names people in the
    // request's language, and node.js falls back to this one.
    "最初的研究者": "Original researcher",
    // The route map (Section 14): a line between attempts (13.4 gains 源自), and
    // what refuses one (app.html's BLOCKING_ERROR_TEXT, 14.2).
    "源自": "Grew out of",
    "连线的一端在年表里找不到了，可能刚被改了编号；请刷新后再试":
      "One end of the line is no longer in the table, perhaps just renumbered; reload and try again",
    "一个尝试不能源自它自己": "An attempt cannot grow out of itself",
    "这条连线会绕成一个圈：它要连的来处本身就源自它":
      "This line would close a circle: the attempt it would grow out of already grows out of it",
    // The route map itself (routemap.js, 14.1, 14.2; 14.6 adds 路线图, 还没标来处, 有改动).
    "路线图": "Route map",
    "还没标来处": "Origin not marked yet",
    "有改动": "Changed",
    "提议中": "Proposed",
    "提议删除": "Removal proposed",
    "年表里还没有尝试。": "The attempt table has no attempts yet.",
    "#{child} 源自 #{parent}": "#{child} grew out of #{parent}",
    "从这里拖到另一条尝试上：那一条源自这一条": "Drag from here onto another attempt: that one grew out of this one",
    "新的连线": "New line",
    "这会作为一条提议交给 PI，批准后才成为正式的连线。": "This goes to the PI as a proposal; the line is official once approved.",
    "备注": "Note",
    "备注（可选）": "Note (optional)",
    "确认连线": "Confirm line",
    "提议这条连线": "Propose this line",
    "取消": "Cancel",
    // The column beside the map (14.4; 14.6 adds 待办), a proposal of a line, a line with a missing end.
    "待办": "To handle",
    "现在没有要处理的事。": "Nothing to handle right now.",
    "在路线图上标出它涉及的尝试": "Mark the attempts it touches on the route map",
    "提议中 · PI 说：{text}": "Proposed · The PI says: {text}",
    "已批准，待同步": "Approved, awaiting sync",
    "待复核": "Needs review",
    "不属于任何尝试": "Not part of any attempt",
    "连线": "Line",
    "加上连线：#{child} 源自 #{parent}": "Add the line: #{child} grew out of #{parent}",
    "删去连线：#{child} 源自 #{parent}": "Remove the line: #{child} grew out of #{parent}",
    "连线的一端找不到了": "Lines with a missing end",
    "年表里没有 #{n}": "#{n} is not in the table",
    // A judgment's link in words (12.11).
    "读取": "reads",
    "写出": "writes",
    "生成": "generates",
    "引用": "cites",
    "属于": "belongs to",
    "依据": "is based on",
    "产出": "produces",
    "实现": "implements",
    "使用": "uses",
    "支持": "supports",
    "关联": "is linked to",
    "作者是": "is by",
    "删除这条连线": "Delete this line",
    "删除这条连线？它会从项目的记录里删去。": "Delete this line? It is removed from the project’s records.",
    "提议删除这条连线？PI 批准后它才会从路线图上消失。": "Propose removing this line? It leaves the route map once the PI approves.",
    "已把这条连线作为提议交给 PI。": "The line went to the PI as a proposal.",
    "已把删除这条连线作为提议交给 PI。": "Removing the line went to the PI as a proposal.",
    "没能保存这条连线": "Could not save this line",
    "没能删除这条连线": "Could not delete this line",
    "这两条尝试之间已经有这条连线了": "These two attempts already have this line",
    "这条连线已经在正式版本里了": "This line is already in the official version",
    "正式版本里没有这条连线": "The official version has no such line",
    "连线的一端不是正式版本里的尝试：新的尝试请在它自己的提议里写明源自哪里":
      "One end of the line is not an attempt of the official version: a new attempt says what it grew out of in its own proposal",
    "你是这个项目的成员：连线要作为提议交给 PI 批准；请刷新后再画":
      "You are a member of this project: a line goes to the PI as a proposal; reload and draw it again",
    "你是 PI：连线直接写入正式版本；请刷新后再画": "You are the PI: a line goes straight into the official version; reload and draw it again",
    "适应全部": "Fit all",
    "适应全部（F）": "Fit all (F)",
    "实际大小": "Actual size",
    "放大": "Zoom in",
    "缩小": "Zoom out",
    "重新排列": "Rearrange",
    "丢弃你在路线图上摆放的位置，重新排列": "Forget where you placed the cards and lay the route map out afresh",
    "重新排列路线图？你在路线图上摆放的位置会被丢弃。": "Rearrange the route map? Where you placed its cards is forgotten.",
    "位置没能保存，下次移动时会重试": "Positions not saved; they are tried again on the next move",
    "位置记录文件现在无法读取：路线图上的摆放不会保存。":
      "The arrangement record cannot be read now: what you place on the route map is not saved.",
    "详情": "Details",
    "关闭": "Close",
    // Abandoning a route asks for its reason, the PI and a single researcher too (14.3):
    // the attempt form, and what refuses a write without one (BLOCKING_ERROR_TEXT).
    "为什么放弃这条路线": "Why this route is abandoned",
    "放弃的理由（必填）": "Reason for abandoning (required)",
    "理由会写进结果一栏，这条路线当时的依据也会冻结下来。":
      "The reason is written into the result, and the route's basis at this moment is frozen.",
    "这一行会放弃这条路线：理由写进结果一栏，它当时的依据冻结下来。":
      "This row abandons the route: the reason goes into the result, and its basis at this moment is frozen.",
    "放弃一条路线时必须写明理由": "Abandoning a route needs a reason",
    "放弃一条路线时必须写明理由：它会写进结果一栏，这条路线当时的依据也会冻结下来。":
      "Abandoning a route needs a reason: it is written into the result, and the route's basis is frozen.",
    "迁移后才能记录判断": "Judgments can be recorded once the old records are migrated",
    "只读打开：不会写入任何东西": "Opened read-only: nothing is written",
    // The desk's side panel (14.3): an attempt opened from its card.
    "这里有还没写入的内容，离开它就不保存了。继续吗？": "Something typed here is not written yet and is lost if you leave. Continue?",
    "没能打开这条尝试": "Could not open this attempt",
    "放弃的理由：{reason}": "Reason for abandoning: {reason}",
    "打开详情页": "Open the details page",
    "判决与结果": "Verdict and result",
    "现在说不清你在这个项目里的角色，判决先不能改。": "Your role in this project cannot be told right now, so the verdict cannot be changed yet.",
    "判决": "Verdict",
    "结果": "Result",
    "用你自己的话说明这份提议：PI 会先看到这句话": "Say what this proposal does in your own words: the PI reads this first",
    "一句话说明（必填）": "One sentence (required)",
    "理由（放弃一条路线时必填）": "Reason (required when abandoning a route)",
    "预览这一行": "Preview this row",
    "预览改动": "Preview the change",
    "请用一句话说明这份提议": "Say in one sentence what this proposal does",
    "没有需要写入的改动": "Nothing has changed to write",
    "正在生成预览…": "Preparing the preview…",
    "无法生成预览": "Could not prepare the preview",
    "放弃一条路线时，结果和理由都必须填写": "Abandoning a route needs both the result and the reason",
    "原行": "Current row",
    "新行": "New row",
    "将写入你的工作副本里的 {file}（已自动备份）。这是你自己的改动，尚未提交；下一步给你看提议的内容，确认后才发送给 PI。":
      "This writes {file} in your working copy (backed up first). It is your own change, not yet proposed; next you see the proposal, and it goes to the PI only once you confirm.",
    "将写入 {file}（已自动备份）": "This writes {file} (backed up first)",
    "写入工作副本，查看提议": "Write the working copy and see the proposal",
    "确认写入": "Write",
    "返回修改": "Back to editing",
    "正在写入…": "Writing…",
    "写入失败，文件没有被修改": "Could not write; the file is unchanged",
    "这一行已写进你的工作副本。": "The row is written in your working copy.",
    "这一行已经在你的工作副本里，可以稍后在「提议」一栏里提交。": "The row is in your working copy; you can propose it later in “Proposals”.",
    "已写入并备份，但重扫失败": "Written and backed up, but the rescan failed",
    "已写入 {file}。": "{file} is written.",
    "写备注": "Write a note",
    "没能读到这条尝试的备注": "Could not read the notes on this attempt",
    "更早的 {n} 条备注在详情页。": { one: "{n} earlier note is on the details page.", other: "{n} earlier notes are on the details page." },
    "写一条备注：为什么这样做、要注意什么": "Write a note: why it was done this way, what to watch for",
    "添加备注": "Add note",
    "备注是空的": "The note is empty",
    "正在保存…": "Saving…",
    "备注没有保存": "The note was not saved",
    "备注已保存。": "The note is saved.",
    "备注记录文件现在无法写入": "The notes record cannot be written right now",
    "备注先记在你的工作副本里；下次同步时交到公盘，PI 下次发布时加入正式记录，署你的名字，不需要批准。":
      "The note is kept in your working copy first; the next sync takes it to the shared folder, and the PI adds it to the official record at the next publish, under your name, with no approval needed.",
    "由它而来": "What grew out of it",
    "还没有尝试源自它": "No attempt grew out of it yet",
    "提议删除这条连线": "Propose removing this line",
    "这条连线已删去。": "The line is deleted.",
    // 来龙去脉 (12.7), drawn on the node page and in the desk's side panel (14.3).
    "来龙去脉": "Provenance",
    "更上游还有 {n} 个文件，这里只画出最近的几层。": {
      one: "{n} more file lies further upstream; only the nearest layers are drawn here.",
      other: "{n} more files lie further upstream; only the nearest layers are drawn here.",
    },
    "没有": "None",
    "{label}：{path}": "{label}: {path}",
    "步骤脚本": "Step script",
    "人工关联": "Linked by hand",
    "暂时无法读取": "Cannot be read right now",
    "原始数据": "Raw data",
    "{relation}（人工标注）": "{relation} (drawn by hand)",
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
