// The interface language (DESIGN.md Section 13; task V9). Served at /i18n.js
// by the same process and loaded by app.html FIRST, before every other
// script, with a plain same-origin <script src>. The server writes one line
// above this file at serve time -- `window.ROI18N_SAVED = "zh" | "en" | null;`,
// the person's saved choice from settings.json (13.2) -- so the language is
// known synchronously, before anything is drawn, with no request.
//
// It defines one global, window.ROI18N:
//   lang    "zh" or "en": the saved choice; with none, the Mac's -- the first
//           of navigator.languages starting with "zh" is Chinese, anything
//           else English (13.2);
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
    "提议": "Proposals",
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

    // Dates (13.5): Today 09:24, Yesterday 18:02, 4 Oct, 4 Oct 2025.
    "今天 {time}": "Today {time}",
    "昨天 {time}": "Yesterday {time}",
    "{month}{day}日": "{day} {month}",
    "{year}年{month}{day}日": "{day} {month} {year}",
    "1月": "Jan", "2月": "Feb", "3月": "Mar", "4月": "Apr", "5月": "May", "6月": "Jun",
    "7月": "Jul", "8月": "Aug", "9月": "Sep", "10月": "Oct", "11月": "Nov", "12月": "Dec",
  };

  // Each language's own name, written in itself: the switch's label (13.3).
  var NAMES = { zh: "中文", en: "English" };
  var MARKUP_ATTRS = ["title", "aria-label", "placeholder"];

  function fillVars(s, v) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return v && Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }); }

  function validLang(value) { return value === "zh" || value === "en" ? value : null; }

  // 13.2: with no saved choice, the Mac's first preferred language.
  function macLang() {
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
