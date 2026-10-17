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
    "今天": "Today",
    "昨天": "Yesterday",
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
    "PI 于 {when} 批准提议后发布了第 {seq} 版。": "The PI published version {seq} ({when}) on approving a proposal.",
    "PI 于 {when} 批准提议「{what}」后发布了第 {seq} 版。":
      "The PI published version {seq} ({when}) on approving the proposal “{what}”.",
    "你的工作副本已经是最新的官方版本（第 {seq} 版）。": "Your working copy is the latest official version (version {seq}).",
    "正在查看同步会写入什么…": "Checking what syncing will write…",
    "没能查看同步会写入什么": "Could not check what syncing will write",
    "同步": "Sync",
    "正在同步…": "Syncing…",
    "没能同步，工作副本没有改动": "Could not sync; the working copy is unchanged",
    // What 同步 will write and what it did (team.js, shared with 成员), and the
    // difference beside an .official copy (team.js differenceBlock, node.js diffBlock).
    "{n} 条备注": { one: "{n} note", other: "{n} notes" },
    "{n} 条关联判断": { one: "{n} linked judgment", other: "{n} linked judgments" },
    "{n} 处「设为论文正文」的标记": { one: "{n} “Set as the paper” mark", other: "{n} “Set as the paper” marks" },
    "增加 {n} 条连线": { one: "{n} link added", other: "{n} links added" },
    "删除 {n} 条连线": { one: "{n} link removed", other: "{n} links removed" },
    "你的工作副本所依据的版本在公盘上已经无法验证，不能同步。":
      "The version your working copy is based on can no longer be verified in the shared folder, so it cannot sync.",
    "同步会先把你还没发送的 {sending}写进公盘的「{area}」：不需要批准，PI 下次发布时加入正式记录。":
      "Syncing first writes what you have not sent yet ({sending}) into “{area}” (your proposals folder) in the shared folder: no approval is needed, and the PI adds it to the official record at the next publish.",
    "{send}你的工作副本已经是最新的官方版本。": "{send} Your working copy is already the latest official version.",
    "你的工作副本已经是最新的官方版本，没有要发送的备注、判断或连线。":
      "Your working copy is already the latest official version, and there are no notes, judgments or links to send.",
    "更新 {n} 个你没改过的文件": { one: "update {n} file you have not changed", other: "update {n} files you have not changed" },
    "删除 {n} 个正式版本已删除的文件": {
      one: "remove {n} file the official version deleted", other: "remove {n} files the official version deleted",
    },
    "{n} 个两边都改过的文件留着你的，把官方的一份放在旁边（.official）": {
      one: "keep your version of {n} file changed on both sides and put the official copy beside it (.official)",
      other: "keep your versions of {n} files changed on both sides and put the official copies beside them (.official)",
    },
    "备注、判断和连线的记录文件：你的条目都已进入正式版本的，换成正式的那一份；还没进入的，留着你的":
      "replace each record file of notes, judgments and links with the official one where all your entries are in it, and keep yours where they are not yet",
    "：{parts}": ": {parts}",
    "；": "; ",
    "{send}然后把工作副本带到第 {seq} 版{parts}。从不合并，你改过的内容不会被覆盖。":
      "{send} Then it brings the working copy to version {seq}{parts}. It never merges: nothing you changed is overwritten.",
    "同步会把工作副本带到第 {seq} 版{parts}。从不合并，你改过的内容不会被覆盖。":
      "Syncing brings the working copy to version {seq}{parts}. It never merges: nothing you changed is overwritten.",
    "已把你的 {n} 条备注、判断或连线改动发送到公盘": {
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
    "你的备注和连线还在等 PI 发布，记录文件暂时留着你的":
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
    "在路线图上标出这条尝试": "Mark this attempt on the route map",
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
    "连线的一端不在你上次同步的正式版本里：请同步后再试":
      "One end of the line is not in the official version you last synced: sync, then try again",
    "#{n} 还不在正式版本里：PI 批准它以后才能画这条连线":
      "#{n} is not in the official version yet: the line can be drawn once the PI approves it",
    "#{n} 还不在正式版本里：在它的提议里写明它源自哪里，或等 PI 批准以后再画这条连线":
      "#{n} is not in the official version yet: name what it grew out of in its proposal, or draw the line once the PI approves it",
    "#{n} 已经批准，同步以后才能画这条连线": "#{n} is approved: the line can be drawn once you sync",
    // A member's own new row changed in the desk's panel (14.3): proposed whole, as a new attempt.
    "这一行已经批准，正式版本里有了：先同步，再改它。":
      "This row is approved and in the official version: sync first, then change it.",
    "这一行还不在正式版本里：它会整行作为一条新的尝试提议。":
      "This row is not in the official version yet: it is proposed whole, as a new attempt.",
    "这一行还不在正式版本里：它会整行作为新的尝试再提议一次，替换你还在等待的那份提议。":
      "This row is not in the official version yet: it is proposed whole as a new attempt again, replacing your proposal still waiting.",
    // A member's new attempt names what it grew out of (14.2): 提议新的尝试 and 提交提议.
    "源自（可选）": "Grew out of (optional)",
    "它源自的正式尝试；PI 批准后，这些连线画到路线图上":
      "The official attempts it grew out of; once the PI approves, these lines are drawn on the route map",
    "例如 16, 17": "For example 16, 17",
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
    "这一行一开始就放弃了这条路线：结果和理由都必须填写，PI 批准后它们写进尝试表，这条路线当时的依据也会冻结下来。":
      "This row abandons the route from the start: the result and the reason are both required; once the PI approves, they go into the attempt table and the route's basis is frozen.",
    "放弃一条路线时必须写明理由": "Abandoning a route needs a reason",
    "放弃一条路线时必须写明理由：它会写进结果一栏，这条路线当时的依据也会冻结下来。":
      "Abandoning a route needs a reason: it is written into the result, and the route's basis is frozen.",
    "成员文件（team.toml）当前无法读取，无法判断你能否直接画或删这条连线":
      "The members file (team.toml) cannot be read right now, so whether you may draw or remove this line directly cannot be told",
    "成员文件（team.toml）当前无法读取，无法判断谁是 PI":
      "The members file (team.toml) cannot be read right now, so who the PI is cannot be told",
    "你是这个项目的成员：连线的增删要作为提议交给 PI 批准":
      "You are a member of this project: drawing or removing a line goes to the PI as a proposal",
    "成员文件（team.toml）当前无法读取，无法判断你能否直接放弃这条路线":
      "The members file (team.toml) cannot be read right now, so whether you may abandon this route directly cannot be told",
    // Who abandoned a route (12.4, ruled 2026-10-13): directly (no proposal), or proposed and approved.
    "由 {who} 放弃": "Abandoned by {who}",
    "放弃时冻结的文件": "Files frozen when it was abandoned",
    "提议人 {who}": "Proposed by {who}",
    "批准人 {who}": "Approved by {who}",
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
    // 原行: the row before the change ("Current" is 13.4's word for 现行).
    "原行": "Earlier row",
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
    // 待复核 (9.6), which the desk's column opens (14.4, 14.6): a judgment, its link and its
    // basis in words (app.html's judgment wording), the acts, the variable cards' part.
    "{n} 条判断待复核": { one: "{n} judgment needs review", other: "{n} judgments need review" },
    "查看证据变了的旧判断": "See the earlier judgments whose evidence changed",
    "确认": "Confirmed",
    "否决": "Rejected",
    "撤回": "Withdraw",
    "撤销": "Undo",
    "在关系图上": "on the relation map",
    "在命令行里": "in the command line",
    "通过 MCP": "through MCP",
    "引用文献": "cites",
    "论断": "Claim",
    "实验": "Experiment",
    "提交": "Commit",
    "论文": "Paper",
    "尝试": "Attempt",
    "迁移自旧索引（原判断时间未知）": "Migrated from the old index (when it was judged is not known)",
    "只凭这条关联本身": "The link itself, nothing more",
    "调用：{calls}": "Calls: {calls}",
    "产物：{artifacts}": "Artifacts: {artifacts}",
    "论断：「{sentence}」": "Claim: “{sentence}”",
    "数值：{number}": "Number: {number}",
    "，": ", ",
    "旧索引里记着几种不同的依据：{bases}": "The old index held several different bases: {bases}",
    "指标：{metrics}": "Metrics: {metrics}",
    "没有对得上的指标": "no matching metric",
    "文中的引用：{cited}": "Cited in the text: {cited}",
    "文末条目的 DOI：{dois}": "DOI of the reference-list entry: {dois}",
    "文末条目：{entries}": "Reference-list entry: {entries}",
    "「{entry}」": "“{entry}”",
    "{key}：{value}": "{key}: {value}",
    "{kind}（{name}）": "{kind} ({name})",
    "文献《{title}》": "Reference “{title}”",
    "文献（DOI {doi}）": "Reference (DOI {doi})",
    "{draft} 的文末条目第 {n} 条": "Entry {n} of the reference list in {draft}",
    "{draft} 的文末条目": "The reference list in {draft}",
    "Zotero 里的一篇文献": "A reference in Zotero",
    "撤销了上一步": "Undid the last step",
    "撤回了判断": "Withdrew the judgment",
    "{when} · 补回文件里缺少的判断：{what}": "{when} · A judgment the file was missing, put back: {what}",
    "{when} · 你{via}{what}": "{when} · {what} ({via})",
    "{when} · 你{what}": "{when} · {what}",
    "证据变了的旧判断，等你再看一眼": "Earlier judgments whose evidence changed, waiting for another look",
    "本次扫描读到了两端，但没有得出这条关联": "This scan read both ends but did not find this link",
    "关联的一端不在本次扫描结果里": "One end of the link is not in this scan",
    "本次扫描没有给出依据": "This scan gave no basis",
    "（没有记录依据）": "(no basis recorded)",
    "已撤回": "Withdrawn",
    "定为确认": "Settle as confirmed",
    "已定为确认": "Settled as confirmed",
    "定为否决": "Settle as rejected",
    "已定为否决": "Settled as rejected",
    "改为否决": "Change to rejected",
    "已改为否决": "Changed to rejected",
    "改为确认": "Change to confirmed",
    "已改为确认": "Changed to confirmed",
    "仍然成立": "Still holds",
    "已记录：仍然成立": "Recorded: still holds",
    "正在记录…": "Recording…",
    "没能记录这条判断": "Could not record this judgment",
    "{done}：{link}": "{done}: {link}",
    "正在撤销…": "Undoing…",
    "无法撤销": "Could not undo",
    "（{type} · {extractor}）": "({type} · {extractor})",
    "迁移来的旧判断和记录里已有的判断相反（两台电脑各判断过一次）。Research OS 不按迁移日期替你选，请写一条新判断定下它。":
      "The migrated judgment contradicts the one already in the records (two computers each judged it once). Research OS does not choose for you by the migration date: record a new judgment to settle it.",
    "两份记录各自追加了不同的内容（可能是两台电脑各改了一份，再被同步合并）。Research OS 不按时间替你选，请写一条新判断定下它。":
      "Two copies of the records each gained different entries (perhaps two computers each changed one, and a sync merged them). Research OS does not choose for you by time: record a new judgment to settle it.",
    "共同的记录：{entries}": "Entries both share: {entries}",
    "记录里原有的": "Already in the records",
    "迁移来的": "Migrated",
    "第 {n} 份": "Copy {n}",
    "原判断：{verdict} · {when}": "Earlier judgment: {verdict} · {when}",
    "备注：{note}": "Note: {note}",
    "当时的依据": "The basis then",
    "现在": "Now",
    "可能对应的新关联（同一次扫描里新出现）：旧判断不会自己转过去，点了才会在它上面记下一条新判断，旧的这条保持原样。":
      "Links that may correspond (new in the same scan): the earlier judgment does not move by itself; a click records a new judgment on one, and this one stays as it is.",
    "已用到这条关联上（{verdict}）": "Applied to this link ({verdict})",
    "把这条旧判断用到它上面": "Apply the earlier judgment to it",
    "已用到新关联上（{verdict}）": "Applied to the new link ({verdict})",
    "判断记录文件当前无法读取，请先恢复它": "The judgments’ record file cannot be read right now; restore it first",
    "修好之前，这里的操作都不会被记录。": "Until it is fixed, nothing done here is recorded.",
    "这些判断做出时依据的事实，这次扫描看到的不一样了。判断没有被删掉，也没有被套用：连线暂时按机器的结果显示，等你决定。":
      "The facts these judgments rested on look different in this scan. The judgments are neither deleted nor applied: the links show as the machine found them until you decide.",
    "没有待复核的旧判断。": "No earlier judgment needs review.",
    "不计入待复核": "Not counted in Needs review",
    "来源文件暂不可读：{files}": "Source file cannot be read right now: {files}",
    "判断照旧（{verdict}）": "Judgment kept ({verdict})",
    "图谱里还没有这条关联": "This link is not recorded yet",
    "你的判断：{verdict}（{when}）": "Your judgment: {verdict} ({when})",
    "未比对": "Not compared",
    "实现脚本 {path}：{text}": "Implementing script {path}: {text}",
    "实现脚本：{text}": "Implementing script: {text}",
    "输入 {dataset}：{text}": "Input {dataset}: {text}",
    "新的数据版本说明（必填），例：2017-01 至 2024-12，2026-10 重新下载":
      "The new data version (required), e.g. 2017-01 to 2024-12, downloaded again 2026-10",
    "卡片指定的代码块或函数找不到了：如果只是改了名字，在卡片文件里改正 chunk / function，再回答「这是更正」；否则选「口径已变」。":
      "The code chunk or function the card names cannot be found: if only its name changed, correct chunk / function in the card file, then answer “This is a correction”; otherwise choose “Construction changed”.",
    "口径未变": "Construction unchanged",
    "输入数据变了：请写下新的数据版本说明": "The input data changed: write the new data version",
    "没能记下口径未变": "Could not record “Construction unchanged”",
    "记下：实现变了，但这一版的口径没有变（附上现在的指纹）":
      "Record that the implementation changed but this version’s construction did not (with the current fingerprint)",
    "口径已变": "Construction changed",
    "没能打开下一版草稿": "Could not open the next draft",
    "打开下一版草稿，在草稿里写下新的口径": "Open the next draft and write the new construction in it",
    "变量卡：实现依据有变化": "Variable cards: the implementation changed",
    "输入数据": "Input data",
    "已为它打开下一版草稿，不计入待复核数；确认草稿之后这一项自然消失。":
      "Its next draft is open, so it is not counted in Needs review; once the draft is confirmed, this item goes away.",
    "全部口径未变": "All constructions unchanged",
    "没能全部记下口径未变": "Could not record “Construction unchanged” for all",
    "为下面每张卡各记一条「口径未变」": "Record “Construction unchanged” for each card below",
    "下面 {n} 张卡各记一条；也可以逐个回答。": "Records one entry for each of the {n} cards below; you can also answer them one by one.",
    // A proposal decided in place (14.3, 14.6: team.js renderProposalInto, on the desk's panel and
    // on 提议): its head and answers, what approving does, what it carries, the answers to give.
    "{kind}提议": "{kind} proposal",
    "没能打开这份提议": "Could not open this proposal",
    "基于官方版本第 {seq} 版": "Based on official version {seq}",
    "已被后来的提议替代": "Replaced by a later proposal",
    "已验证": "Verified",
    "签名和成员名单里这个人的公钥相符": "The signature matches this person's public key in the members list",
    "这份提议没有可以核对的签名信息": "This proposal carries no signature to check",
    "没有公钥可以核对": "No public key to check it against",
    "这台电脑无法核对签名": "This Mac cannot check signatures",
    // The engine's sentence for a signature (signing.CHECK_MESSAGES), shown by its Chinese.
    "签名已验证": "Signature verified",
    "成员名单里没有这个人的公钥：无法验证签名，只能依靠公盘的共享权限":
      "The members list has no public key for this person: the signature cannot be verified, so only the shared folder's permissions vouch for it",
    "这台电脑没有 ssh-keygen：无法验证签名，只能依靠公盘的共享权限":
      "This Mac has no ssh-keygen: the signature cannot be verified, so only the shared folder's permissions vouch for it",
    "没有签名：无法确认是谁写的": "Not signed: who wrote it cannot be confirmed",
    "签名不对：不是名单上这个人的密钥签的，或者签名之后内容被改过":
      "The signature is wrong: it was not made with this person's key in the list, or the content changed after signing",
    "答复": "Answers",
    "{who} 批准了": "{who} approved",
    "{who} 退回了": "{who} returned it",
    "{who} 说": "{who} said",
    "这份提议的答复记录互相矛盾（同时有批准和退回）：请在公盘上检查 {folder} 里它的答复。":
      "This proposal's answer records contradict each other (it is both approved and returned): check its answers in {folder} (the published answers) in the shared folder.",
    "批准以后会怎样": "What approving does",
    "提议之后正式版本又有了改动；只有两边改了同一处时才会挡住批准。":
      "The official version changed after this proposal; approval is blocked only where both sides changed the same place.",
    "这份提议的批准上次没有做完：再次批准会接着把剩下的做完。":
      "Approving this proposal did not finish last time: approving again completes the rest.",
    "在尝试表 {file} 里新增第 {number} 行": "Add row {number} to the attempt table {file}",
    "尝试表第 {number} 行的判决 {old} 改为 {new}": "Row {number} of the attempt table: the verdict changes from {old} to {new}",
    "第 {number} 行的判决 {old} 改为 {new}": "Row {number}: the verdict changes from {old} to {new}",
    "结果：{result}": "Result: {result}",
    "结果改为：{result}": "New result: {result}",
    "理由：{reason}": "Reason: {reason}",
    "放弃理由：{reason}": "Reason for abandoning: {reason}",
    "批准会把结果和理由写进尝试表，并冻结这条路线当时的依据：它的步骤脚本和它们写出的文件。":
      "Approving writes the result and the reason into the attempt table and freezes the route's basis at this moment: its step scripts and the files they write.",
    "正式版本里这一行已经是这样了。": "The official version already has this row as it is.",
    "新建变量卡 {card} 的第 {version} 版定义": "New variable card {card}: definition version {version}",
    "确认变量 {card} 的第 {version} 版定义": "Confirm variable {card}: definition version {version}",
    "成员的回答：现有的输出文件是按这个定义生成的吗？{answer}":
      "Were the existing output files made by this definition? The member answers: {answer}",
    "是": "Yes",
    "否": "No",
    "不确定": "Not sure",
    "说明：{note}": "Note: {note}",
    "正式版本的连线记录里已经是这样了。": "The official version's line records already have this.",
    "这份提议没有带来任何改动。": "This proposal brings no change.",
    "批准还会在 decisions.toml 里记下一条：提议人 {who}，批准人是你；然后把正式版本发布到公盘。":
      "Approving also records an entry in decisions.toml, proposed by {who} and approved by you, then publishes the official version to the shared folder.",
    "批准还会在 decisions.toml 里记下一条：提议人 {who}，批准人是你；然后把正式版本发布到公盘。发布前会先把成员同步来的 {carried}加进正式记录（不需要批准）。":
      "Approving also records an entry in decisions.toml, proposed by {who} and approved by you, then publishes the official version to the shared folder. Before publishing, it first adds what members synced ({carried}) to the official record, with no approval needed.",
    "会删除的连线（{n} 条）": { one: "The link to be removed ({n})", other: "Links to be removed ({n})" },
    "删除人 {who}": "Removed by {who}",
    "没有随提议发送的改动": "Changes not sent with the proposal",
    "这一处改动没有包含在提议里": "This change is not part of the proposal",
    "现在还不能批准": "Cannot be approved yet",
    "：{reason}": ": {reason}",
    "Research OS 不会替两个人合并同一个文件。请成员先同步到最新的官方版本，在那上面重新提议；这份可以退回并说明。":
      "Research OS never merges one file for two people. Ask the member to sync to the latest official version and propose again on it; this one can be returned with a reason.",
    "已写入正式版本的改动": "Changes written into the official version",
    "提议带来的改动": "Changes the proposal brings",
    "尝试表新增第 {number} 行：{description}": "New row {number} in the attempt table: {description}",
    // A proposal's files (12.3, 12.13).
    "没有文件。": "No files.",
    "新增": "Added",
    "改动": "Changed",
    "删除": "Removed",
    "批准后写入": "Written on approval",
    "正式版本里已经是这样": "Already so in the official version",
    "两边都改过": "Changed on both sides",
    "有问题": "Has a problem",
    "这次批准已经写入": "Written by this approval",
    "{n} 字节": { one: "{n} byte", other: "{n} bytes" },
    "大文件，按内容存放在公盘": "A large file, stored in the shared folder by its content",
    "大文件，按内容存放在公盘：": "A large file, stored in the shared folder by its content:",
    "指纹 ": "Fingerprint ",
    "表格：{words}": "Table: {words}",
    "查看改动": "Show the change",
    "用到的变量版本": "Variable versions it uses",
    "{name} 第 {version} 版": "{name}, version {version}",
    // The PI's answers (12.3; 13.4: 批准并加入 “Approve”, 退回并说明 “Return with reason”).
    "你的答复": "Your answer",
    "给这次批准写一句附言（可以不写）": "A note with this approval (optional)",
    "批准附言": "Note with the approval",
    "批准并加入": "Approve",
    "有两边都改过的地方或问题，现在不能批准（见上面）。":
      "Something was changed on both sides or has a problem, so it cannot be approved now (see above).",
    "为什么退回：{who} 会看到这句话": "Why it is returned: {who} will see this",
    "退回的理由（必填）": "Reason for returning (required)",
    "退回并说明": "Return with reason",
    "请写明退回的理由": "Write why it is returned",
    "想问或想说的话：提议保持打开": "What you want to ask or say: the proposal stays open",
    "讨论": "Discuss",
    "请写下要讨论的内容": "Write what you want to discuss",
    "答复会签名后写进公盘的「{folder}」，{who} 同步时就会看到；写成以后不再改动。":
      "The answer is signed and written into “{folder}” (the published answers) in the shared folder; {who} sees it on their next sync. It is never changed once written.",
    "正在批准并发布…": "Approving and publishing…",
    "正在写入答复…": "Writing the answer…",
    "没能批准，正式版本没有改动": "Could not approve; the official version is unchanged",
    "没能写入答复": "Could not write the answer",
    "已批准，改动已写入正式版本。": "Approved: the change is written into the official version.",
    "已批准，改动已写入正式版本，并发布为第 {seq} 版：成员同步后就会看到。":
      "Approved: the change is written into the official version and published as version {seq}; members see it once they sync.",
    "已批准，改动已写入正式版本；但这次没有发布到公盘：{why}。可以在「成员」一栏里再发布一次。":
      "Approved: the change is written into the official version, but it was not published to the shared folder this time: {why}. You can publish again in “Members”.",
    "发布没有完成": "Publishing did not finish",
    "已退回：{who} 会看到你的理由。": "Returned: {who} will see your reason.",
    "已写下：提议保持打开。": "Written: the proposal stays open.",
    // A member's draft (team.js composeInto, renderDraft): what it carries and what sending writes.
    "正在比较你的工作副本和官方版本…": "Comparing your working copy with the official version…",
    "没能生成提议": "Could not prepare the proposal",
    "将要发送的提议": "The proposal to send",
    "变量 {card} 的第 {version} 版定义（成员的回答：{answer}）":
      "Variable {card}: definition version {version} (the member's answer: {answer})",
    "带上的文件": "Files it carries",
    "改判只带尝试表里的这一行；要一起发送文件，在下面勾选。":
      "A verdict change carries only its row of the attempt table; to send files with it, tick them below.",
    "新的尝试带上它这一行，和与它关联的文件（描述里写的步骤脚本、用「关联文件」挂上的文件）；可以加上或去掉。":
      "A new attempt carries its row and the files linked to it (the step scripts its description names, and files added with “Link files”); you can add or untick files.",
    "变量定义只带新版本的定义文件；要一起发送文件，在下面勾选。":
      "A variable definition carries only the new version's definition file; to send files with it, tick them below.",
    "只带勾选的文件。": "Only the ticked files.",
    "工作副本里没有和官方版本不同的文件。": "No file in the working copy differs from the official version.",
    "{size}，大文件": "{size}, a large file",
    "与这条尝试关联": "Linked to this attempt",
    "这个文件曾在被退回的提议里：先看看退回的理由，再决定是否带上。":
      "This file was in a returned proposal: read why it was returned before deciding whether to carry it.",
    "按勾选的文件重新查看": "Review again with the ticked files",
    "不会发送的改动": "Changes that will not be sent",
    "你自己的提议文件夹（{path}）": "your own proposals folder ({path})",
    "公盘的「{area}」": "“{area}” (your proposals folder) in the shared folder",
    "，带上 {n} 个文件": { one: ", carrying {n} file", other: ", carrying {n} files" },
    "（其中 {n} 个大文件按内容只存一份）": {
      one: " ({n} of them a large file, stored once by its content)",
      other: " ({n} of them large files, each stored once by its content)",
    },
    "发送会在{where}里写入一份新的提议{files}；写成以后不再改动。你的工作副本不会改变。":
      "Sending writes a new proposal into {where}{files}. It is never changed once written, and your working copy does not change.",
    "发送提议": "Send proposal",
    "正在写入公盘…": "Writing to the shared folder…",
    "没能发送提议，公盘上没有写入": "Could not send the proposal; nothing was written to the shared folder",
    "已发送：PI 会在收件箱里看到它。在 PI 批准之前，它不是正式记录。":
      "Sent: the PI will see it in the inbox. Until the PI approves it, it is not part of the official record.",
    "已发送": "Sent",
    // V10 D4 (14.3-14.5): an attempt's 待复核 judged in its panel; what changed on it since the
    // last visit (the node page's timeline, reduced); a member's returned proposal put away.
    "知道了": "Got it",
    "没能读到待复核的判断": "Could not read the judgments that need review",
    "这一行有改动": "This row changed",
    "文件有改动": "File changed",
    "打开它的详情页": "Open its details page",
    "经 {who} 批准": "Approved by {who}",
    "更早改动的 {n} 个文件没有列在这里。": {
      one: "{n} file changed earlier is not listed here.", other: "{n} files changed earlier are not listed here.",
    },
    "记下这条尝试，判决为「{verdict}」": "Attempt recorded with the verdict “{verdict}”",
    "判决从「{was}」改为「{became}」": "Verdict changed from “{was}” to “{became}”",
    "判决从「{was}」改为「{became}」，结果改为：{result}": "Verdict changed from “{was}” to “{became}”; new result: {result}",
    "判决不变（{verdict}）": "Verdict unchanged ({verdict})",
    "随第 {n} 版发布同步": "Synced with published version {n}",
    "扫描时记下": "Recorded at a scan",
    "发布时记下": "Recorded at a publish",
    "git 提交": "Git commit",
    "定义卡记录": "Recorded on the card",

    // Node details, Outputs, Data & code, and proposal navigation.
    "清单": "List",
    "它不是文件，没有版本": "This item is not a file and has no file versions",
    "更正": "Corrected",
    "实现变动后确认口径未变": "Reconfirmed after the implementation changed",
    "弃用": "Retired",
    "恢复": "Restored",
    "以文件为准移除": "Removed to match the file",
    "两份记录合并后，指明以哪几条为准": "Resolved the merged records",
    "待确认": "Awaiting confirmation",
    "已确认": "Confirmed",
    "已否决": "Rejected",
    "机器提取": "Extracted from code",
    "确认了": "Confirmed",
    "否决了": "Rejected",
    "撤回了对它的判断": "Withdrew the judgment on",
    "撤销了上一次判断": "Undid the previous judgment on",
    "全部": "All",
    "无来源输入": "Input without a source",
    "血缘链": "Data chains",
    "断链": "Broken links",
    "同名拷贝": "Duplicate filenames",
    "人工标注": "Author-added link",
    "记录冲突，待处理": "Conflicting records; needs attention",
    "{n} 行以上": {"one": "At least {n} row", "other": "At least {n} rows"},
    "{n} 行": {"one": "{n} row", "other": "{n} rows"},
    "列 {columns}": "Columns: {columns}",
    // A Stata .dta file (DESIGN.md 15.4): its columns are its variables.
    "变量 {columns}": "Variables: {columns}",
    "{rows} × {n} 个变量": {"one": "{rows} × {n} variable", "other": "{rows} × {n} variables"},
    "数据集标签：{label}": "Dataset label: {label}",
    "变量名": "Name",
    "标签": "Label",
    "类型": "Type",
    "只列出了前 {n} 个变量": "Only the first {n} variables are listed",
    "无法读取这个 .dta 文件": "This .dta file cannot be read",
    // Stata globals and the project folders they stand for (DESIGN.md 15.3, 15.5; 13.4: 全局变量 "global",
    // 展开不了 "Not resolved", 项目里的文件夹 "project folder").
    "Stata 全局变量": "Stata globals",
    "项目文件夹本身": "the project folder itself",
    "项目里的文件夹 {folder}": "the project folder {folder}",
    "{global} 已确认指{folder}": "{global} is confirmed as {folder}",
    "上次的回答是「不是」（{who}）": "The last answer was “No” ({who})",
    "回答的人：{who}": "Answered by {who}",
    "不是": "No",
    "正在记下回答…": "Recording the answer…",
    "没能记下这个回答": "Could not record the answer",
    "没能读取 Stata 全局变量": "Could not read the Stata globals",
    "全局变量记录文件当前无法读取": "The record of globals cannot be read now",
    "展开不了": "Not resolved",
    "展开不了（{n}）": "Not resolved ({n})",
    "{reason}（{n}）": "{reason} ({n})",
    "全局变量没有定义": "The global is not defined",
    "指向项目以外的位置": "Points outside the project",
    "被赋了不同的值": "Assigned different values",
    "不是写定的值": "Not a fixed value",
    "在循环里拼出": "Built in a loop",
    "改变工作文件夹的 cd 无法跟随": "A cd that cannot be followed",
    "工作文件夹无法确定": "The working folder cannot be told",
    "从两个工作文件夹运行": "Run from two working folders",
    "do 文件相互运行": "Do-files that run each other",
    // The file panel's subtitle: what kind of file it is.
    "数据集": "Dataset",
    "文本 / 文档": "Text / document",
    "配置": "Configuration",
    "{n} 列": {"one": "{n} column", "other": "{n} columns"},
    "没能打开这一页": "Could not open this page",
    "返回上一页": "Back to the previous page",
    "返回{section}": "Back to {section}",
    "{n} 条关联待复核": {"one": "{n} link needs review", "other": "{n} links need review"},
    "查看内容": "View content",
    "打开": "Open",
    "在 Finder 中显示": "Show in Finder",
    "提议改判": "Propose a verdict change",
    "编辑这一行": "Edit this row",
    "打开定义卡": "Open definition card",
    "论文正文": "Paper draft",
    "不是论文正文": "Not a paper draft",
    "处理中…": "Working…",
    "已在 Finder 中显示：{path}": "Shown in Finder: {path}",
    "已打开：{path}": "Opened: {path}",
    "无法在 Finder 中显示": "Could not show in Finder",
    "无法打开": "Could not open",
    "日期": "Date",
    "途径": "Approach",
    "（无）": "(none)",
    " 第 {n} 行": " · Line {n}",
    "出处": "Source",
    "找不到的步骤脚本": "Missing step scripts",
    "原文": "Original text",
    "位置": "Location",
    "第 {n} 行": "Line {n}",
    "作者": "Authors",
    "年份": "Year",
    "刊物": "Publication",
    "使用中": "In use",
    "第 {n} 版": "Version {n}",
    "状态": "Status",
    "定义卡的记录现在无法读取": "The definition card records cannot be read right now",
    "补上来源": "Add a source",
    "关联文件": "Link files",
    "生成它的脚本": "Script that generates it",
    "要关联的文件": "File to link",
    "它属于的尝试": "Attempt it belongs to",
    "选择生成它的脚本": "Choose the script that generates it",
    "选择要关联的文件": "Choose a file to link",
    "选择它属于的尝试": "Choose the attempt it belongs to",
    "备注（可不填）": "Note (optional)",
    "项目里还没有脚本可选。": "This project has no scripts to choose from yet.",
    "没有可以选择的项目。": "There are no items to choose from.",
    "将记下：{script} 生成 {file}": "Record that {script} generates {file}",
    "将记下：{script} 写出 {file}": "Record that {script} writes {file}",
    "将记下：{file} 属于 {attempt}": "Record that {file} belongs to {attempt}",
    "正在记下…": "Recording…",
    "没能关联文件": "Could not link the file",
    "没能补上来源": "Could not add the source",
    "已经写进 mappings.toml，但图谱还没有更新；稍后会自动重试": "Saved to mappings.toml; the index has not updated yet and will retry automatically",
    "已记下。": "Recorded.",
    "还有文件属于这条尝试？": "More files belong to this attempt?",
    "（{text}）": "({text})",
    "使用变量": "Uses variable",
    "{variable} 第 {n} 版": "{variable} version {n}",
    "（定义卡写明输出到这里）": "(the definition card names this output)",
    "（按列名 {field} 对上）": "(matched by column name: {field})",
    "改动与备注": "Changes and notes",
    "这里的历史现在读不到": "The history cannot be read right now",
    "下面的版本是它所在的文稿 {path} 的版本。": "The versions below belong to the containing draft, {path}.",
    "这里的笔记现在读不到": "The notes cannot be read right now",
    "{reason}；下面是图谱上次读到的笔记。": "{reason}; the notes below are from the last successful read.",
    "笔记记录文件现在无法读取": "The notes record cannot be read right now",
    "还没有记录到改动或笔记。": "No changes or notes have been recorded yet.",
    "连线：{link}": "Link: {link}",
    "{action}这条关联：{link}": "{action} this link: {link}",
    "判断了": "Judged",
    "工作区里还没有提交的改动": "Uncommitted changes",
    "最早记下的版本": "First recorded version",
    "数据有改动": "Data changed",
    "内容有改动": "Content changed",
    "查看这一版的改动": "View changes in this version",
    "没能比较这两个版本": "Could not compare these versions",
    "内容相同": "Identical content",
    "笔记": "Note",
    "已撤回（{when}）": "Withdrawn ({when})",
    "正在撤回…": "Withdrawing…",
    "没能撤回这条笔记": "Could not withdraw this note",
    "你的邮箱": "your email",
    "这条笔记": "This note",
    "这条连线": "This link",
    "你的确认和否决": "Your link judgments",
    "这次删除": "This removal",
    "「设为论文正文」的标记": "This “Set as the paper” mark",
    "这条记录": "This record",
    "正式记录": "the official record",
    "PI 下次发布时从正式的 mappings.toml 里删去这条连线": "the PI removes this link from the official mappings.toml at the next publish",
    "PI 下次发布时加入{record}": "the PI adds it to {record} at the next publish",
    "你自己的提议文件夹": "your proposals folder",
    "公盘的「{folder}」": "“{folder}” in the shared folder",
    "{what}先记在你的工作副本里；下次同步时写入{where}，{then}，署你的名字，不需要批准。": "{what} will be recorded in your working copy first. Your next sync sends it to {where}; {then}, under your name, without individual approval.",
    "正式的 mappings.toml": "the official mappings.toml",
    "正式的 papers.toml": "the official papers.toml",
    "写一条笔记：为什么这样做、要注意什么": "Add a note: why this was done and what to watch for",
    "添加笔记": "Add note",
    "笔记是空的": "Enter a note first",
    "笔记没有保存": "The note was not saved",
    "笔记记录文件现在无法写入": "The notes record cannot be written right now",
    "判断": "Link judgments",
    "还没有和它关联的连线。": "No links touch this item yet.",
    "机器提取的关联由你确认或否决；人工标注的关联来自 mappings.toml。": "Confirm or reject extracted links. Author-added links are kept in mappings.toml.",
    "显示全部 {n} 条": {"one": "Show the {n} link", "other": "Show all {n} links"},
    "（": "(",
    "）": ")",
    "删除标注": "Remove link",
    "删除这条人工标注？\n\n{link}\n\n它会从 .researchos/mappings.toml 里移除。": "Remove this author-added link?\n\n{link}\n\nIt will be removed from .researchos/mappings.toml.",
    "已经从 mappings.toml 删除，但图谱还没有更新；稍后会自动重试": "Removed from mappings.toml; the index has not updated yet and will retry automatically",
    "没能删除这条标注": "Could not remove this link",
    "没能记下这个判断": "Could not record this judgment",
    "关系图": "Relationship map",
    "关系图只画脚本、数据和图表；它的关联见上面的来龙去脉。": "The relationship map shows scripts, data and figures. See this item’s links in the provenance above.",
    "收起关系图": "Hide relationship map",
    "打开关系图": "Open relationship map",
    "打开它的来龙去脉": "Open its provenance",
    "文稿里的图、表和论断。每一项都能打开它的来龙去脉：完整，或者缺来源、缺尝试。": "Figures, tables and claims in your drafts. Open an item to inspect its provenance and any missing sources or attempts.",
    "图与表": "Figures and tables",
    "文稿里还没有引用图或表。": "No figures or tables are cited in the drafts yet.",
    "没有被文稿引用的图": "Figures not cited in a draft",
    "不再作为论文正文": "Unset as the paper",
    "设为论文正文": "Set as the paper",
    "没能记下": "Could not record the change",
    "已不再作为论文正文。": "No longer marked as the paper.",
    "已设为论文正文：成果会列出它的论断。": "Set as the paper. Its claims will appear in Outputs.",
    "还没有读到文稿。": "No drafts have been found yet.",
    "成果只列出论文正文里的论断：修订记录、审稿意见里的数字不算。还没有文稿设为论文正文——": "Outputs lists claims from the paper itself; it excludes numbers in revision logs and review comments. No draft is marked as the paper yet. ",
    "在下面的文稿旁点「设为论文正文」，或在文稿自己的页面上点它。": "Choose “Set as the paper” beside a draft below or on its own page.",
    "只列出设为论文正文的文稿里的论断；展开一份文稿才载入它的论断。": "Only drafts marked as the paper contribute claims. Expand a draft to load them.",
    "打开这份文稿的页面": "Open this draft",
    "{n} 处带数字的句子，不列出": {"one": "{n} sentence with a number; excluded", "other": "{n} sentences with numbers; excluded"},
    "没有带数字的句子": "No sentences with numbers",
    "{n} 条论断": {"one": "{n} claim", "other": "{n} claims"},
    "没能载入这份文稿的论断": "Could not load this draft’s claims",
    "数字 {number}": "Number: {number}",
    "这份文稿共有 {total} 条，只列出了前 {shown} 条。": "This draft has {total} claims; the first {shown} are shown.",
    " 等 {n} 个": " and others ({n} total)",
    "读取 {files}": "Reads {files}",
    "写出 {files}": "Writes {files}",
    "没有读到读写记录。": "No reads or writes were found.",
    "由这些脚本写出：{files}": "Written by: {files}",
    "被这些脚本读取：{files}": "Read by: {files}",
    "没有脚本读取或写出它": "No script reads or writes this file",
    "打开待复核的判断": "Open judgments needing review",
    // 8.8: the Chinese brackets name the relation again; the English says it once.
    "断链：{script} {relation} {target}（写出，文件不存在）": "Broken link: {script} {relation} {target} (file not found)",
    "断链：{script} {relation} {target}（读取，文件不存在）": "Broken link: {script} {relation} {target} (file not found)",
    "其它拷贝：{files}": "Other copies: {files}",
    "筛选": "Filter",
    "还没有扫描到脚本或数据文件。": "No scripts or datasets have been scanned yet.",
    "没有。": "None.",
    "这是单人项目：你的改动直接生效，不需要提议和批准。要和别人一起做，在「成员」一栏里开始协作。": "This is a single-person project. Your changes take effect directly, without proposals or approval. Start collaboration in “Members”.",
    "去成员一栏": "Go to Members",
    "签名未验证": "Signature unverified",
    "退回的理由：{reason}": "Reason for return: {reason}",
    "PI 说：{text}": "The PI says: {text}",
    "公盘上读不了的东西": "Unreadable shared-folder items",
    "这些内容没有读取，也没有当作提议：多半是同步服务留下的冲突副本，或者还没写完。": "These items were not read or treated as proposals. They may be sync-conflict copies or incomplete writes.",
    "无法读取": "Cannot read",
    "收件箱": "Inbox",
    "已经答复的": "Answered",
    "返回提议列表": "Back to proposals",
    "脚本": "Scripts",
    "数据": "Data",
    "图表": "Figure",
    "文稿章节": "Draft section",
    "实验记录": "Run record",
    "条目": "Item",
    "中间数据": "Intermediate data",
    "本页": "This item",
    "被引用": "Used in",
    "被读取": "Read by",
    "引用的图表与文献": "Cited figures and references",
    "被这些地方引用": "Cited by",
    "所在章节": "Containing section",
    "输入": "Inputs",
    "输出": "Outputs",
    "定义": "defines",
    "完整": "Complete",
    "缺来源": "Missing source",
    "缺尝试": "Missing attempt",
    "图": "Figure",
    "表": "Table",
    "没有找到生成它的脚本": "No generating script was found",
    "还没有归入任何尝试": "Not linked to an attempt yet",
    "脚本暂时无法读取或解析": "The script cannot be read or parsed right now",
    "脚本文件不在磁盘上": "The script is missing from disk",
    "数据文件不在磁盘上": "The data file is missing from disk",
    "没有找到支撑这个数字的结果": "No result was found to support this number",
    "定义卡还没有写明实现它的脚本和输出文件": "The definition card does not name its implementation script or output yet",
    "（不在任何章节下）": "(outside any section)",
    "第 {n} 版的定义": "Definition version {n}",

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
