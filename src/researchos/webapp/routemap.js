// The route map (路线图) on the desk -- DESIGN.md Section 14 (14.1, 14.2);
// task V10, increment B1. Served verbatim at /routemap.js by the same
// process and loaded by app.html with a plain same-origin <script src>,
// exactly as /canvas.js and /desk.js are. It defines one global,
// window.RORouteMap, a factory: the desk (desk.js) makes one map and feeds
// it GET /api/desk's attempts, lines, role and layout. It is a renderer of
// its own, not a second use of 关系图's canvas object (14.1), so the desk,
// 数据与代码 and the node page never share state; it borrows that canvas's
// camera, fit and curve. It calls app.html's apiPost only when it writes.
//
// What it draws (14.1): the attempts as cards -- 「#16」 and the
// researcher's title, the date as written, the verdict as a text status in
// its tone (route_status), abandoned cards quieter, and marks as quiet
// words: the engine's 等你批准 / 提议中 / 待复核 N, a member's own work as
// 12.12 says, and 有改动 when the desk says so. A card whose attempt holds
// tasks (21.1) says under its status 「任务 n，未完成 m」, 「等你看 n」 when a
// handback is unopened and the first line of its latest handback (a click
// on that line opens the handback, 21.2, on 「等你看」 the first one not
// opened yet; the rest of the card opens the attempt); a card
// with no tasks is as it was, and `after` is not drawn. The 「源自」 lines are a
// human's lines (ink, 21.8; clay in 8.2): a parent stands left of what grew out of it,
// no arrowheads; a line a proposal carries is dashed and says so. The
// layout is a forest by generation, siblings in the map's order (by date),
// and the attempts with no line in 「还没标来处」 below it (layoutRoutes:
// pure, run under node by the tests). Positions are per person, the view
// `routes` of canvas.json: the whole view is pinned on the first move;
// afterwards a new attempt joins the end of 「还没标来处」 and nothing
// pinned moves; 「重新排列」 asks, then lays the view out afresh (8.4).
//
// What it does (14.2): drag from the handle on a card's right edge onto
// another card -- the popover says 「#17 源自 #16」 with an optional note.
// The PI and a single researcher write the line (POST /api/mappings/add,
// type derives_from); a member proposes it (POST /api/proposals, kind
// route_link). A click without a drag opens the card (`onOpen`). A click
// on a line selects it; 「删除这条连线」 asks, then deletes it or proposes
// its removal. A refusal says its own sentence (8.8), the engine's text
// one click away. Keys act only while the map is visible.
//
// 12.11: no emoji, no arrow characters. 8.8: the words for node and canvas
// never reach the screen. 13.6: every word goes through t() (14.6: the
// desk is a converted surface).
(function () {
  "use strict";

  // The words for the screen (DESIGN.md 13.6): /i18n.js's table, looked up
  // at call time; without it (this file evaluated alone) the Chinese.
  function fillVars(s, v) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return v && Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }); }
  function t(s, v) { var I = typeof window !== "undefined" && window.ROI18N; return I ? I.t(s, v) : fillVars(s, v); }

  const SVG_NS = "http://www.w3.org/2000/svg";
  const CARD_W = 232;
  const CARD_H = 88;
  const CARD_TALL = CARD_H + 20;    // a card whose marks wrap to a second line (app.html .rm-card min-height)
  const CARD_WORK_H = 120;          // a card that also says its tasks and its latest handback (21.1): two more lines
  const COL_PITCH = CARD_W + 112;   // one generation to the next: room for the curve
  const ROW_PITCH = CARD_H + 28;    // rows of the forest
  const WORK_PITCH = CARD_WORK_H + 28;
  const ROW_X = CARD_W + 24;        // 「还没标来处」, card to card
  const ROW_Y = CARD_H + 24;
  const WORK_ROW_Y = CARD_WORK_H + 24;
  const ROW_GAP = 72;               // the forest to the row's caption
  const CAPTION_H = 30;
  const ROW_MIN = 4;                // the row wraps, at least this wide, toward a page of 1.6:1
  const ASPECT = 1.6;
  const ZOOM_MIN = 0.25;
  const ZOOM_MAX = 2.5;
  const FIT_PAD = 40;
  const TOOLBAR_H = 52;
  const GRID = 24;
  const SAVE_MS = 400;
  const RESIZE_MS = 150;
  const DRAG_PX = 3;
  const HANDLE_PX = 16;             // a drag this far from where it started is a drag, not a jittery click
  const NOTICE_MS = 5000;
  const SCOPE = "routes";           // researchos.webapp.canvas.SCOPE_ROUTES
  const UNUSABLE = ["invalid", "unreadable", "dataless"];

  function hasOwn(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function lineKey(child, parent) { return child + "\n" + parent; }
  function validPos(p) { return Array.isArray(p) && p.length === 2 && isFinite(p[0]) && isFinite(p[1]); }

  // -- pure (run under node by tests/test_v10_route_map.py) ---------------------

  // 14.1's layout. `attempts` in the map's order (the payload's: by date
  // where the table makes it readable, then by number); `lines` [{child,
  // parent}]; `opts.fixed` {id: [x, y]} the arrangement saved for the view.
  // Unpinned: the attempts with a line form a forest -- the column is the
  // generation (the longest path from a root), each tree below the ones
  // before it, a card on its first parent's row when that row is free in
  // its column, siblings in the map's order -- and the others stand in
  // 「还没标来处」 below it, in order, wrapping toward a page of 1.6:1.
  // Pinned (any card has a position): nothing pinned moves; a card the
  // arrangement does not hold joins the end of 「还没标来处」 -- the lowest
  // line of the cards with no line, as arranged -- or starts it below all;
  // the caption stands over the highest of those cards, leftmost first.
  function hasWork(a) { return !!(a && a.work && a.work.tasks > 0); }

  function layoutRoutes(attempts, lines, opts) {
    const fixed = (opts && opts.fixed) || {};
    // 21.1: when any card says its tasks, every row of the forest is as tall as such a card (a map of cards of one height).
    const rich = attempts.some(hasWork);
    const cardH = rich ? CARD_WORK_H : CARD_H, pitch = rich ? WORK_PITCH : ROW_PITCH, rowY = rich ? WORK_ROW_Y : ROW_Y;
    const ids = attempts.map((a) => a.id);
    const rank = new Map(ids.map((id, i) => [id, i]));
    const parents = new Map(ids.map((id) => [id, []]));
    const children = new Map(ids.map((id) => [id, []]));
    (lines || []).forEach((l) => {
      if (!rank.has(l.child) || !rank.has(l.parent) || l.child === l.parent) return;
      if (parents.get(l.child).includes(l.parent)) return;
      parents.get(l.child).push(l.parent);
      children.get(l.parent).push(l.child);
    });
    children.forEach((xs) => xs.sort((a, b) => rank.get(a) - rank.get(b)));
    const linked = ids.filter((id) => parents.get(id).length || children.get(id).length);
    const row = ids.filter((id) => !parents.get(id).length && !children.get(id).length);
    // A line that would close a circle (the engine refuses and ignores one) is not followed.
    const gen = new Map(), path = new Set();
    const generation = (id) => {
      if (gen.has(id)) return gen.get(id);
      path.add(id);
      let g = 0;
      parents.get(id).forEach((p) => { if (!path.has(p)) g = Math.max(g, generation(p) + 1); });
      path.delete(id);
      gen.set(id, g);
      return g;
    };
    linked.forEach(generation);
    const rowOf = new Map(), next = new Map();
    const bottom = () => Math.max(0, ...next.values());
    const place = (id, want) => {
      const col = gen.get(id);
      const r = Math.max(want, next.get(col) || 0);
      rowOf.set(id, r);
      next.set(col, r + 1);
      children.get(id).forEach((c) => { if (!rowOf.has(c)) place(c, r); });
    };
    linked.forEach((id) => { if (!parents.get(id).length) place(id, bottom()); });
    linked.forEach((id) => { if (!rowOf.has(id)) place(id, bottom()); }); // only inside a circle
    const auto = {};
    linked.forEach((id) => { auto[id] = [gen.get(id) * COL_PITCH, rowOf.get(id) * pitch]; });
    const top = linked.length ? bottom() * pitch - (pitch - cardH) + ROW_GAP + CAPTION_H : CAPTION_H;
    const per = Math.max(ROW_MIN, Math.ceil(Math.sqrt(row.length * ASPECT * rowY / ROW_X)));
    row.forEach((id, i) => { auto[id] = [(i % per) * ROW_X, top + Math.floor(i / per) * rowY]; });
    const held = ids.filter((id) => hasOwn(fixed, id) && validPos(fixed[id]));
    if (!held.length) return { positions: auto, caption: row.length ? [0, top - CAPTION_H] : null, row, pinned: false };
    const positions = {};
    held.forEach((id) => { positions[id] = [Number(fixed[id][0]), Number(fixed[id][1])]; });
    const inRow = held.filter((id) => row.includes(id));
    let x, y;
    if (inRow.length) {
      y = Math.max(...inRow.map((id) => positions[id][1]));
      x = Math.max(...held.filter((id) => Math.abs(positions[id][1] - y) < cardH).map((id) => positions[id][0])) + ROW_X;
    } else {
      y = Math.max(...held.map((id) => positions[id][1])) + cardH + ROW_GAP + CAPTION_H;
      x = Math.min(...held.map((id) => positions[id][0]));
    }
    ids.forEach((id) => { if (!hasOwn(positions, id)) { positions[id] = [x, y]; x += ROW_X; } });
    const first = row.map((id) => positions[id]).sort((a, b) => a[1] - b[1] || a[0] - b[0])[0];
    return { positions, caption: first ? [first[0], first[1] - CAPTION_H] : null, row, pinned: true };
  }

  // A card's marks, as words (14.1): a member's own row as 12.12 marks it,
  // the proposals touching it and its 待复核 (the engine's words, in the
  // request's language), then 有改动; each said once.
  function markWords(a, changed) {
    const out = [];
    const add = (w) => { if (w && !out.includes(String(w))) out.push(String(w)); };
    const own = a.officialness;
    if (own && own.state !== "official") add(own.label);
    const marks = a.marks || {};
    (marks.proposals || []).forEach((p) => add(p.label));
    if (marks.review) add(marks.review.label);
    if (changed) add(t("有改动"));
    return out;
  }

  // A card's work lines (21.1): how many tasks its attempt holds and how many are unfinished, how many handbacks wait
  // to be opened, and the first line of the latest handback; nothing for an attempt with no tasks.
  function workWords(a) {
    if (!hasWork(a)) return { tasks: "", look: "", line: "" };
    const w = a.work, n = w.tasks, open = w.unfinished || 0;
    return {
      tasks: open ? t("任务 {n}，未完成 {open}", { n: n, open: open }) : t("任务 {n}，全部完成", { n: n }),
      look: w.unopened > 0 ? t("等你看 {n}", { n: w.unopened }) : "",
      line: (w.latest && w.latest.line) || "",
    };
  }

  function lineWords(child, parent) {
    return t("#{child} 源自 #{parent}", { child: child, parent: parent });
  }

  // 8.8: a refused line says its own sentence, chosen by the engine's code
  // (13.6: never by its words) -- a direct write answers mapping_*, a
  // member's proposal the proposal's own code; null for any other cause.
  function refusal(code) {
    switch (code) {
      case "self_link": case "mapping_self_link": return t("一个尝试不能源自它自己");
      case "circle": case "mapping_circle": return t("这条连线会绕成一个圈：它要连的来处本身就源自它");
      case "mapping_exists": return t("这两条尝试之间已经有这条连线了");
      case "line_exists": return t("这条连线已经在正式版本里了");
      case "mapping_attempt_not_found": return t("连线的一端在尝试表里找不到了，可能刚被改了编号；请刷新后再试");
      case "attempt_not_found": return t("连线的一端不在你上次同步的正式版本里：请同步后再试");
      case "no_such_line": return t("正式版本里没有这条连线");
      case "mapping_member_proposes": return t("你是这个项目的成员：连线要作为提议交给 PI 批准；请刷新后再画");
      case "mapping_role_unknown": return t("成员文件（team.toml）当前无法读取，无法判断你能否直接画或删这条连线");
      case "team_unreadable": return t("成员文件（team.toml）当前无法读取，无法判断谁是 PI");
      case "pi_changes_directly": return t("你是 PI：连线直接写入正式版本；请刷新后再画");
      case "not_in_team": return t("你还不是这个项目的成员：请 PI 把你加入团队，然后同步");
      case "session_required": return t("这个窗口没有连上 Research OS 的写入权限：请从 Research OS 应用打开它。如果你已经在应用里，说明应用是旧版本：请运行 researchos app 重新生成。"); // 18.1
      default: return null;
    }
  }

  // What refuses a new line before it is asked (the engine checks again):
  // a line to itself, one already drawn, one that would close a circle --
  // the parent already grows out of the child.
  function checkLine(lines, child, parent) {
    if (child === parent) return "self_link";
    if (lines.some((l) => l.child === child && l.parent === parent)) return "mapping_exists";
    const seen = new Set(), todo = [parent];
    while (todo.length) {
      const id = todo.pop();
      if (id === child) return "circle";
      if (seen.has(id)) continue;
      seen.add(id);
      lines.forEach((l) => { if (l.child === id) todo.push(l.parent); });
    }
    return null;
  }

  // Where a line's word stands (14.2: the word stays on the map, and no card
  // may cover it -- cards are drawn above the lines). Centred on the line's
  // midpoint, above its dot, when it fits the free span between the two
  // cards; else broken where its language breaks (「已批准，」「待同步」,
  // "Approved," "awaiting sync") into two lines, one above the dot and one
  // below; else -- a gap narrower than its longest line, the cards moved
  // there -- just above the higher card, or below the lower one. A place
  // another card (`boxes`, [x, y, w, h]) would cover is not taken; a line
  // swinging below both (its child left of its parent) tries its midpoint
  // first. `width(text)` measures a line of it.
  const LABEL_PAD = 8;
  const LABEL_LIFT = 6;
  const LABEL_LINE = 13;
  const WIDE_COMMA = String.fromCharCode(0xff0c); // 「，」: where a Chinese word breaks, never a word on screen
  function splitLabel(word) {
    const w = String(word);
    let at = w.indexOf(WIDE_COMMA) >= 0 ? w.indexOf(WIDE_COMMA) + 1 : w.indexOf(", ") >= 0 ? w.indexOf(", ") + 1 : -1;
    if (at < 0) {
      const spaces = [];
      for (let i = 0; i < w.length; i += 1) if (w[i] === " ") spaces.push(i);
      if (spaces.length) at = spaces.reduce((b, i) => (Math.abs(i - w.length / 2) < Math.abs(b - w.length / 2) ? i : b));
      else at = Math.ceil(w.length / 2);
    }
    return [w.slice(0, at).trim(), w.slice(at).trim()].filter(Boolean);
  }

  function labelCovered(plan, boxes, width) {
    return plan.lines.some((text, i) => {
      const base = i ? plan.below : plan.y, half = width(text) / 2;
      return (boxes || []).some((b) => plan.x - half < b[0] + b[2] && b[0] < plan.x + half && base - 9 < b[1] + b[3] && b[1] < base + 3);
    });
  }

  function labelPlan(word, parentPos, childPos, width, boxes) {
    const e = { x1: parentPos[0] + CARD_W, y1: parentPos[1] + CARD_H / 2, x2: childPos[0], y2: childPos[1] + CARD_H / 2 };
    const mid = curve(e).mid;
    const one = { x: mid.x, y: mid.y - LABEL_LIFT, lines: [String(word)] };
    const swings = e.x2 < e.x1 + 20; // below both cards: the midpoint is between no two cards
    const free = swings ? Infinity : childPos[0] - (parentPos[0] + CARD_W) - 2 * LABEL_PAD;
    const two = splitLabel(word);
    const tall = Math.max(...(boxes || []).filter((b) => (b[0] === parentPos[0] && b[1] === parentPos[1])
      || (b[0] === childPos[0] && b[1] === childPos[1])).map((b) => b[3]), CARD_H);
    const tries = [
      width(word) <= free ? one : null,
      !swings && two.length === 2 && Math.max(width(two[0]), width(two[1])) <= free
        ? { x: mid.x, y: mid.y - LABEL_LIFT, lines: two, below: mid.y + LABEL_LINE } : null,
      { x: mid.x, y: Math.min(parentPos[1], childPos[1]) - LABEL_LIFT, lines: [String(word)] },
      { x: mid.x, y: Math.max(parentPos[1], childPos[1]) + tall + LABEL_LINE, lines: [String(word)] },
    ].filter(Boolean);
    return tries.find((plan) => !labelCovered(plan, boxes, width)) || tries[0];
  }

  // ComfyUI's curve, as 关系图 draws it: horizontal tangents; a line whose
  // child stands left of its parent (moved there) swings below both.
  function curve(e) {
    let c;
    if (e.x2 < e.x1 + 20) {
      const drop = Math.max(e.y1, e.y2) + 140;
      c = [e.x1 + 80, drop, e.x2 - 80, drop];
    } else {
      const dx = Math.max(40, (e.x2 - e.x1) * 0.5);
      c = [e.x1 + dx, e.y1, e.x2 - dx, e.y2];
    }
    const mid = { x: (e.x1 + 3 * c[0] + 3 * c[2] + e.x2) / 8, y: (e.y1 + 3 * c[1] + 3 * c[3] + e.y2) / 8 };
    return { d: "M" + e.x1 + "," + e.y1 + " C" + c[0] + "," + c[1] + " " + c[2] + "," + c[3] + " " + e.x2 + "," + e.y2, mid: mid };
  }

  // -- one map ---------------------------------------------------------------------

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function svgEl(tag, attrs, cls) {
    const e = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, String(v)));
    if (cls) e.setAttribute("class", cls);
    return e;
  }

  function emptySave() { return { positions: {}, viewport: undefined, reset: false }; }

  // `options.onOpen(attemptId)`: a click on a card; `options.onChanged()`:
  // after a line was written or proposed (the desk reads itself again);
  // `options.firstOpen(project)`: true the first time this project's map is
  // opened (21.8: the desk remembers that per project, in the browser, as it
  // remembers the way the page is looked at), when the map fits all its cards.
  function create(options) {
    const opts = options || {};
    const m = {
      dom: null, data: null, project: undefined, role: "single", changed: null,
      attempts: [], byId: new Map(), lines: [], optimistic: [],
      saved: {}, local: {}, resetting: false, writable: true,
      positions: {}, caption: null, pinned: false,
      camera: { x: 0, y: 0, zoom: 1 }, autoFit: true, needsFit: false, cameraSet: false,
      selected: null, selectedLine: null, focus: null, drag: null, pop: null, gesture: null, later: null,
      marked: [], save: emptySave(), saveTimer: null, resizeTimer: null, statusTimer: null,
    };

    // Anyone but the PI and a single researcher proposes a line (14.2) -- kept
    // for an unknown role too, so its write never goes to /api/mappings/*.
    function proposes() { return m.role !== "pi" && m.role !== "single"; }
    // Whether lines are drawn or removed here at all: not by someone not in
    // the team yet, nor while team.toml cannot be read (ruled 2026-10-13;
    // the panel offers no editing either).
    function mayEdit() { return m.role === "pi" || m.role === "single" || m.role === "member"; }
    // Why not, in the column's own words (the engine's, in the request's language).
    function notEditable() {
      const notice = m.data && m.data.column && m.data.column.notice;
      return notice || refusal(m.role === "none" ? "not_in_team" : "team_unreadable");
    }
    function numberOf(id) { const a = m.byId.get(id); return a ? String(a.number).replace(/^#/, "") : String(id).split("#").pop(); }
    function allLines() { return m.lines.concat(m.optimistic); }
    function cardH() { return m.attempts.some(hasWork) ? CARD_WORK_H : CARD_H; } // the height every card is laid out for (21.1)
    function lineOf(key) { return allLines().find((l) => lineKey(l.child, l.parent) === key) || null; }

    // -- the DOM ---------------------------------------------------------------------

    function mount(host) {
      if (m.dom) { // one map, one DOM: mounted elsewhere, it moves there whole
        if (!host.contains(m.dom.root)) host.appendChild(m.dom.root);
        return;
      }
      const root = el("div", "rm-root");
      root.setAttribute("role", "group");
      root.setAttribute("aria-label", t("路线图"));
      const world = el("div", "rm-world");
      const lines = svgEl("svg", { width: 1, height: 1 }, "rm-lines");
      const links = svgEl("g");
      const draft = svgEl("path", { d: "" }, "rm-draft");
      lines.append(links, draft);
      const cards = el("div", "rm-cards");
      world.append(lines, cards);
      const bar = el("div", "cv-toolbar rm-toolbar");
      const button = (text, title, fn) => {
        const b = el("button", "cv-btn", text);
        b.type = "button";
        if (title) b.title = title;
        b.addEventListener("click", fn);
        return b;
      };
      bar.append(
        button(t("适应全部"), t("适应全部（F）"), () => fitAll()),
        button("100%", t("实际大小"), () => zoomCenter(1)),
        button(t("放大"), "", () => zoomCenter(m.camera.zoom * 1.2)),
        button(t("缩小"), "", () => zoomCenter(m.camera.zoom / 1.2)),
        button(t("重新排列"), t("丢弃你在路线图上摆放的位置，重新排列"), () => relayout()),
      );
      const zoom = el("div", "cv-zoom", "100%");
      const status = el("div", "cv-status hidden");
      const empty = el("div", "cv-empty hidden");
      const outside = el("div", "cv-note rm-outside hidden");   // 21.8: one quiet line when cards are out of view
      bar.append(outside);   // under the toolbar, which the note follows if it wraps (21.8)
      root.append(world, bar, zoom, status, empty);
      host.appendChild(root);
      m.dom = { root, world, links, draft, cards, zoom, status, empty, outside };
      root.addEventListener("pointerdown", onDown);
      root.addEventListener("pointermove", onMove);
      root.addEventListener("pointerup", onUp);
      root.addEventListener("pointercancel", onUp);
      root.addEventListener("wheel", onWheel, { passive: false });
      root.addEventListener("gesturestart", (e) => { e.preventDefault(); m.gesture = m.camera.zoom; });
      root.addEventListener("gesturechange", (e) => {
        e.preventDefault();
        if (m.gesture !== null) zoomAt(m.gesture * e.scale, e.clientX, e.clientY);
      });
      root.addEventListener("gestureend", (e) => { e.preventDefault(); m.gesture = null; });
      document.addEventListener("keydown", onKey);
      if (typeof ResizeObserver === "function") {
        new ResizeObserver(() => {
          clearTimeout(m.resizeTimer);
          m.resizeTimer = setTimeout(() => { if ((m.autoFit || m.needsFit) && m.data && !m.drag) fit(); else syncOutside(); }, RESIZE_MS);
        }).observe(root);
      }
      applyCamera();
    }

    // The desk's payload, read again: cards, lines and arrangement follow it;
    // the camera, the selection and this page's own placements stay. Another
    // project starts afresh, with its own arrangement and camera.
    function update(data, o) {
      if (!m.dom) return;
      if (m.drag) { m.later = [data, o]; return; } // a refresh during a drag waits for the drop
      const layout = (data && data.layout) || {};
      const project = layout.project || null;
      if (project !== m.project) {
        clearTimeout(m.saveTimer);
        Object.assign(m, { project, save: emptySave(), local: {}, resetting: false, selected: null, selectedLine: null,
          optimistic: [], focus: null, cameraSet: false, marked: [] });
        closePop();
        hideStatus();
        if (UNUSABLE.includes(layout.state)) {
          showStatus(t("位置记录文件现在无法读取：路线图上的摆放不会保存。"), null, { notice: true });
        }
      }
      m.data = data;
      m.writable = layout.writable !== false;
      m.role = data.role || "single";
      m.changed = (o && o.changed) || null;
      m.attempts = data.attempts || [];
      m.byId = new Map(m.attempts.map((a) => [a.id, a]));
      m.lines = (data.lines || []).filter((l) => m.byId.has(l.child) && m.byId.has(l.parent));
      m.optimistic = m.optimistic.filter((x) => !x.done && m.byId.has(x.child) && m.byId.has(x.parent)
        && !m.lines.some((l) => l.child === x.child && l.parent === x.parent));
      if (!m.resetting) m.saved = layout.positions || {};
      const before = held();
      arrange();
      if (m.selected && !m.byId.has(m.selected)) m.selected = null;
      if (m.selectedLine && !lineOf(m.selectedLine)) selectLine(null);
      m.marked = m.marked.filter((id) => m.byId.has(id));
      render();
      if (!m.cameraSet) {
        m.cameraSet = true;
        const vp = layout.viewport, saved = !!vp && isFinite(vp.x) && isFinite(vp.y) && vp.zoom > 0;
        // 21.8: the first time this project's map is opened it fits all its cards; a camera saved before that is forgotten, as 适应全部 does
        if (typeof opts.firstOpen === "function" && opts.firstOpen(project)) {
          autoFit();
          if (saved) queueViewport(true);
        } else if (saved) {
          m.camera = { x: vp.x, y: vp.y, zoom: clamp(vp.zoom, ZOOM_MIN, ZOOM_MAX) };
          m.autoFit = false;
          applyCamera();
        } else autoFit();
      } else hold(before);
      if (m.focus && m.lines.some((l) => l.child === m.focus)) m.focus = null; // its line has landed
    }

    function arrange() {
      const fixed = {};
      m.attempts.forEach((a) => {
        const p = hasOwn(m.local, a.id) ? m.local[a.id] : m.resetting ? null : m.saved[a.id];
        if (validPos(p)) fixed[a.id] = p;
      });
      const got = layoutRoutes(m.attempts, allLines(), { fixed });
      m.positions = got.positions;
      m.caption = got.caption;
      m.pinned = got.pinned;
    }

    function render() {
      const d = m.dom;
      d.cards.textContent = "";
      d.empty.textContent = t("尝试表里还没有尝试。");
      d.empty.classList.toggle("hidden", m.attempts.length > 0);
      if (m.caption) {
        const c = el("div", "rm-caption", t("还没标来处"));
        c.style.left = m.caption[0] + "px";
        c.style.top = m.caption[1] + "px";
        d.cards.appendChild(c);
      }
      m.attempts.forEach((a) => d.cards.appendChild(cardEl(a)));
      renderLines();
      syncOutside();
    }

    function verdictWord(s) {
      return window.RODesk && window.RODesk.verdictWord ? window.RODesk.verdictWord(s) : (s && s.label) || t("其它");
    }

    function cardEl(a) {
      const tone = a.tone || "plain";
      const c = el("div", "rm-card tone-" + tone + (a.abandoned ? " abandoned" : "") + (a.id === m.selected ? " selected" : "")
        + (m.marked.includes(a.id) ? " marked" : ""));
      c.dataset.id = a.id;
      const num = "#" + numberOf(a.id);
      const words = a.route_status && a.route_status.words;
      const said = markWords(a, !!(m.changed && m.changed(a)));
      const work = workWords(a);
      c.title = num + " " + (a.title || "") + (words ? "\n" + words : "")
        + (work.tasks ? "\n" + [work.tasks, work.look].filter(Boolean).join(" / ") : "") + (work.line ? "\n" + work.line : "")
        + (said.length ? "\n" + said.join(" / ") : "");
      const head = el("div", "rm-head");
      head.append(el("span", "rm-num", num), el("span", "rm-title", a.title || ""));
      const meta = el("div", "rm-meta");
      if (a.date) meta.appendChild(el("span", "rm-date", a.date));
      meta.appendChild(el("span", "status-label tone-" + tone, verdictWord(a.route_status)));
      const marks = el("div", "rm-marks");
      said.forEach((w) => marks.appendChild(el("span", "rm-mark", w)));
      const handle = el("div", "rm-handle");
      handle.title = t("从这里拖到另一条尝试上：那一条源自这一条");
      c.append(head, meta);
      if (work.tasks) {
        c.classList.add("has-work");
        const progress = el("div", "rm-work");
        progress.appendChild(el("span", "rm-tasks", work.tasks));
        if (work.look) progress.appendChild(el("span", "rm-look", work.look));
        c.appendChild(progress);
        if (work.line) c.appendChild(el("div", "rm-headline", work.line));
      }
      c.append(marks, handle);
      placeCard(c, a.id);
      return c;
    }

    function placeCard(c, id) {
      const p = m.positions[id] || [0, 0];
      c.style.left = p[0] + "px";
      c.style.top = p[1] + "px";
    }

    function ends(l) {
      const p = m.positions[l.parent], c = m.positions[l.child];
      return { x1: p[0] + CARD_W, y1: p[1] + CARD_H / 2, x2: c[0], y2: c[1] + CARD_H / 2 };
    }

    // A line a proposal carries says so: a member's own as 12.12 marks it
    // (提议中, 已批准，待同步), the removal a member proposed to the PI.
    function lineMark(l) {
      const own = l.officialness;
      if (own && own.state !== "official" && own.label) return own.label;
      if (l.pending) return l.pending.op === "remove" ? t("提议删除") : t("提议中");
      return null;
    }

    function renderLines() {
      const g = m.dom.links;
      g.textContent = "";
      allLines().forEach((l) => {
        if (!m.positions[l.child] || !m.positions[l.parent]) return;
        const key = lineKey(l.child, l.parent);
        const grp = svgEl("g", { "data-key": key }, "rm-line" + (l.pending || l.optimistic ? " pending" : "")
          + (key === m.selectedLine ? " selected" : ""));
        const c = curve(ends(l));
        grp.append(svgEl("path", { d: c.d }, "rm-line-hit"), svgEl("path", { d: c.d }, "rm-line-path"),
          svgEl("circle", { cx: c.mid.x, cy: c.mid.y, r: 3 }, "rm-line-dot"));
        const word = lineMark(l);
        g.appendChild(grp);
        if (word) grp.appendChild(lineLabel(word, l));
      });
    }

    // The line's word, laid out by labelPlan, measured as drawn (an estimate
    // while the map is not shown and nothing can be measured).
    function lineLabel(word, l) {
      const label = svgEl("text", { "text-anchor": "middle" }, "rm-line-label");
      m.dom.links.appendChild(label);
      const width = (text) => {
        label.textContent = text;
        const got = typeof label.getComputedTextLength === "function" ? label.getComputedTextLength() : 0;
        return got || Array.from(String(text)).reduce((n, ch) => n + (/[\u2e80-\uffef]/.test(ch) ? 11 : 6.2), 0);
      };
      const boxes = m.attempts.map((a) => m.positions[a.id]).filter(Boolean).map((p) => [p[0], p[1], CARD_W, cardH() + (CARD_TALL - CARD_H)]);
      const plan = labelPlan(word, m.positions[l.parent], m.positions[l.child], width, boxes);
      label.remove();
      label.textContent = "";
      label.setAttribute("x", plan.x);
      label.setAttribute("y", plan.y);
      if (plan.lines.length === 1) label.textContent = plan.lines[0];
      else {
        plan.lines.forEach((text, i) => {
          const span = svgEl("tspan", { x: plan.x, y: i ? plan.below : plan.y });
          span.textContent = text;
          label.appendChild(span);
        });
      }
      return label;
    }

    function drawDraft(from, to) {
      const p = m.positions[from];
      m.dom.draft.setAttribute("d", to ? curve({ x1: p[0] + CARD_W, y1: p[1] + CARD_H / 2, x2: to.x, y2: to.y }).d : "");
    }

    function markTarget(id) {
      m.dom.cards.querySelectorAll(".rm-card.target").forEach((c) => c.classList.remove("target"));
      const c = id ? m.dom.cards.querySelector('.rm-card[data-id="' + CSS.escape(id) + '"]') : null;
      if (c) c.classList.add("target");
    }

    function select(id) {
      m.selected = id;
      m.dom.cards.querySelectorAll(".rm-card").forEach((c) => c.classList.toggle("selected", c.dataset.id === id));
    }

    // The cards a row of the desk's column touches (14.4): marked, and the
    // first brought to the middle when none of them is on screen.
    function highlight(ids) {
      m.marked = (ids || []).filter((id) => m.byId.has(id));
      if (!m.dom) return;
      m.dom.cards.querySelectorAll(".rm-card").forEach((c) => c.classList.toggle("marked", m.marked.includes(c.dataset.id)));
      const v = size(), z = m.camera.zoom;
      if (!m.marked.length || !v.w) return;
      if (m.marked.some((id) => inView(id, v))) return;
      const p = m.positions[m.marked[0]];
      m.camera = { x: v.w / 2 - (p[0] + CARD_W / 2) * z, y: v.h / 2 - (p[1] + cardH() / 2) * z, zoom: z };
      applyCamera();
      userCamera();
    }

    // -- the camera (关系图's: 25%-250%, fit never past 100%) -------------------------

    function applyCamera() {
      if (!m.dom) return;
      const { x, y, zoom } = m.camera;
      m.dom.world.style.transform = "translate(" + x + "px, " + y + "px) scale(" + zoom + ")";
      m.dom.root.style.backgroundSize = GRID * zoom + "px " + GRID * zoom + "px";
      m.dom.root.style.backgroundPosition = x + "px " + y + "px";
      m.dom.zoom.textContent = Math.round(zoom * 100) + "%";
      syncOutside();
    }

    // Whether a card shows in the view: any part of it between the edges, below the toolbar (it covers the top of the map).
    function inView(id, v) {
      const p = m.positions[id];
      if (!p) return true;
      const z = m.camera.zoom, x = p[0] * z + m.camera.x, y = p[1] * z + m.camera.y;
      return x + CARD_W * z > 0 && x < v.w && y + cardH() * z > TOOLBAR_H && y < v.h;
    }

    // 21.8: how many cards are out of the view, in one quiet line, when some are (the view is the camera's: a fit that cannot hold
    // them all at the smallest zoom, or a pan, leaves some out; 适应全部 brings them back).
    function syncOutside() {
      if (!m.dom || !m.dom.outside) return;
      const v = typeof m.dom.root.getBoundingClientRect === "function" ? size() : { w: 0, h: 0 };
      const n = v.w && v.h ? m.attempts.filter((a) => !inView(a.id, v)).length : 0;
      m.dom.outside.textContent = n ? t("有 {n} 张卡片在视野之外", { n: n }) : "";
      m.dom.outside.classList.toggle("hidden", !n);
    }

    function size() {
      const r = m.dom.root.getBoundingClientRect();
      return { w: r.width, h: r.height, left: r.left, top: r.top };
    }

    function worldAt(cx, cy) {
      const v = size();
      return { x: (cx - v.left - m.camera.x) / m.camera.zoom, y: (cy - v.top - m.camera.y) / m.camera.zoom };
    }

    function zoomAt(zoom, cx, cy) {
      const v = size(), sx = cx - v.left, sy = cy - v.top, z0 = m.camera.zoom, z = clamp(zoom, ZOOM_MIN, ZOOM_MAX);
      m.camera = { x: sx - (sx - m.camera.x) / z0 * z, y: sy - (sy - m.camera.y) / z0 * z, zoom: z };
      applyCamera();
      userCamera();
    }

    function zoomCenter(zoom) {
      const v = size();
      zoomAt(zoom, v.left + v.w / 2, v.top + v.h / 2);
      return true;
    }

    function fit() {
      const v = size();
      if (!v.w || !v.h) { m.needsFit = true; return; }
      m.needsFit = false;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      const take = (p, w, h) => { x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0] + w); y1 = Math.max(y1, p[1] + h); };
      Object.values(m.positions).forEach((p) => take(p, CARD_W, cardH()));
      if (m.caption) take(m.caption, CARD_W, CAPTION_H);
      if (!isFinite(x0)) {
        m.camera = { x: FIT_PAD, y: TOOLBAR_H + FIT_PAD, zoom: 1 };
      } else {
        const availW = v.w - 2 * FIT_PAD, availH = v.h - TOOLBAR_H - 2 * FIT_PAD;
        const z = clamp(Math.min(availW / (x1 - x0), availH / (y1 - y0), 1), ZOOM_MIN, ZOOM_MAX);
        m.camera = { x: (v.w - (x1 - x0) * z) / 2 - x0 * z, y: TOOLBAR_H + FIT_PAD + (availH - (y1 - y0) * z) / 2 - y0 * z, zoom: z };
      }
      applyCamera();
    }

    function autoFit() { m.autoFit = true; fit(); }

    // 适应全部 (button, F): the researcher asks for the fit, so the view's
    // saved camera is forgotten and a resize re-fits it (8.4).
    function fitAll() { autoFit(); queueViewport(true); return true; }

    function userCamera() { m.autoFit = false; queueViewport(false); }

    // Where the card just linked stands on screen, so a re-layout moves the
    // map under the researcher's hand rather than away from it (8.4).
    function held() {
      const p = m.focus && m.positions[m.focus];
      return p ? { id: m.focus, x: p[0] * m.camera.zoom + m.camera.x, y: p[1] * m.camera.zoom + m.camera.y } : null;
    }

    function hold(before) {
      if (m.autoFit) { fit(); return; }
      const p = before && m.positions[before.id];
      if (!p) return;
      m.camera = { x: before.x - p[0] * m.camera.zoom, y: before.y - p[1] * m.camera.zoom, zoom: m.camera.zoom };
      applyCamera();
    }

    // -- the arrangement (8.4, 9.2: the view `routes` of canvas.json) ---------------

    function schedule() {
      clearTimeout(m.saveTimer);
      m.saveTimer = setTimeout(flush, SAVE_MS);
    }

    function queuePosition(id, pos) {
      m.local[id] = pos;
      m.save.positions[id] = pos;
      schedule();
    }

    function queueViewport(forget) {
      if (!m.data) return;
      m.save.viewport = forget ? null : { x: m.camera.x, y: m.camera.y, zoom: m.camera.zoom };
      schedule();
    }

    // 8.4's pin on first move: the moved card and every card the view holds
    // no position for yet -- in a view not pinned, every card.
    function cardMoved(id) {
      m.attempts.forEach((a) => {
        const kept = hasOwn(m.local, a.id) || (!m.resetting && validPos(m.saved[a.id]));
        if (a.id === id || !kept) queuePosition(a.id, m.positions[a.id].slice());
      });
      arrange();
      render();
    }

    async function flush() {
      clearTimeout(m.saveTimer);
      m.saveTimer = null;
      const slot = m.save, project = m.project;
      m.save = emptySave();
      // A record that cannot be read, a frozen or read-only project: the
      // arrangement lives in this page only, nothing is sent to be refused.
      if (!m.writable || !project) { m.resetting = false; return; }
      const body = { project: project, scope: SCOPE };
      if (slot.reset) body.reset = true;
      if (Object.keys(slot.positions).length) body.positions = slot.positions;
      if (slot.viewport !== undefined) body.viewport = slot.viewport;
      if (Object.keys(body).length === 2) return;
      try {
        await apiPost("/api/canvas/layout", body);
        if (slot.reset && project === m.project) m.resetting = false;
      } catch (err) {
        if ((err && err.state === "project_changed") || project !== m.project) return;
        // Kept for the next save of this view, under anything newer.
        if (slot.reset) m.save.reset = true;
        Object.keys(slot.positions).forEach((id) => { if (!hasOwn(m.save.positions, id)) m.save.positions[id] = slot.positions[id]; });
        if (m.save.viewport === undefined) m.save.viewport = slot.viewport;
        showStatus(refusal(err && err.state) || t("位置没能保存，下次移动时会重试"), err);
      }
    }

    // 「重新排列」 (14.1, 8.4): forget this view's arrangement after asking,
    // lay it out afresh and fit.
    function relayout() {
      if (!m.data || !window.confirm(t("重新排列路线图？你在路线图上摆放的位置会被丢弃。"))) return;
      m.local = {};
      m.saved = {};
      m.resetting = true;
      m.save = { positions: {}, viewport: null, reset: true };
      schedule();
      arrange();
      render();
      autoFit();
    }

    // -- the chip, the popover, the line's card --------------------------------------

    function showStatus(text, err, o) {
      if (!m.dom) return;
      o = o || {};
      const s = m.dom.status;
      s.textContent = "";
      s.appendChild(el("span", "err-text", text));
      s.classList.toggle("notice", !!o.notice);
      const raw = err ? String(err.detail || err.message || err) : "";
      if (raw) {
        const detail = el("span", "err-detail hidden", raw);
        const toggle = el("button", "err-detail-toggle has-chevron", t("详情"));   // 12.13: the chevron, turned when it is open
        toggle.type = "button";
        toggle.setAttribute("aria-expanded", "false");
        toggle.addEventListener("click", () => toggle.setAttribute("aria-expanded", String(detail.classList.toggle("hidden") === false)));
        s.append(toggle, detail);
      }
      if (!o.ms) {
        const close = el("button", "cv-status-action", t("关闭"));
        close.type = "button";
        close.addEventListener("click", () => hideStatus());
        s.appendChild(close);
      }
      s.classList.remove("hidden");
      clearTimeout(m.statusTimer);
      m.statusTimer = o.ms ? setTimeout(hideStatus, o.ms) : null;
    }

    function hideStatus() {
      clearTimeout(m.statusTimer);
      if (m.dom) m.dom.status.classList.add("hidden");
    }

    function floating(node, wx, wy) {
      m.dom.root.appendChild(node);
      const v = size(), w = node.offsetWidth, h = node.offsetHeight;
      let x = wx * m.camera.zoom + m.camera.x + 14, y = wy * m.camera.zoom + m.camera.y + 14;
      if (x + w > v.w - 8) x = Math.max(8, x - w - 28);
      if (y + h > v.h - 8) y = Math.max(8, y - h - 28);
      node.style.left = Math.round(x) + "px";
      node.style.top = Math.round(y) + "px";
      node.addEventListener("pointerdown", (e) => e.stopPropagation());
    }

    function closePop() {
      if (m.pop) m.pop.el.remove();
      m.pop = null;
      if (m.dom) drawDraft(null, null);
    }

    function askLine(parent, child) {
      closePop();
      if (!mayEdit()) {
        drawDraft(null, null);
        showStatus(notEditable(), null, { ms: NOTICE_MS });
        return;
      }
      const why = checkLine(allLines(), child, parent);
      const early = why ? refusal(why) : notYetOfficial(child, parent);
      if (early) {
        drawDraft(null, null);
        showStatus(early, null, { ms: NOTICE_MS });
        return;
      }
      const pop = el("div", "cv-pop rm-pop");
      pop.setAttribute("role", "dialog");
      pop.setAttribute("aria-label", t("新的连线"));
      pop.appendChild(el("div", "cv-pop-line", lineWords(numberOf(child), numberOf(parent))));
      if (proposes()) pop.appendChild(el("p", "rm-pop-sub", t("这会作为一条提议交给 PI，批准后才成为正式的连线。")));
      const field = el("label", "cv-pop-field");
      const input = el("input", "form-input cv-pop-note");
      input.type = "text";
      input.placeholder = t("备注（可选）");
      input.setAttribute("aria-label", t("备注", { n: 1 }));
      input.maxLength = 200;
      field.appendChild(input);
      const actions = el("div", "cv-pop-actions");
      const ok = el("button", "btn btn-primary", proposes() ? t("提议这条连线") : t("确认连线"));
      ok.type = "button";
      ok.addEventListener("click", () => confirmLine());
      const cancel = el("button", "btn", t("取消"));
      cancel.type = "button";
      cancel.addEventListener("click", () => closePop());
      actions.append(ok, cancel);
      pop.append(field, actions);
      pop.addEventListener("keydown", (e) => {
        e.stopPropagation(); // nothing typed here is a shortcut of the map
        if (e.key === "Escape") { e.preventDefault(); closePop(); }
        else if (e.key === "Enter" && e.target === input && !e.isComposing) { e.preventDefault(); confirmLine(); }
      });
      m.pop = { el: pop, kind: "new", parent: parent, child: child, input: input };
      const at = m.positions[child];
      drawDraft(parent, { x: at[0], y: at[1] + CARD_H / 2 });
      floating(pop, at[0], at[1] + CARD_H / 2);
      input.focus();
    }

    // A member proposes a line against the official version they synced to
    // (14.2): an end that is their own row not yet official -- or approved
    // and not synced yet -- is refused here, before anything is asked, with
    // what they can do instead; the engine would refuse it too.
    function notYetOfficial(child, parent) {
      if (!proposes()) return null;
      const state = (id) => { const own = (m.byId.get(id) || {}).officialness; return own && own.state ? own.state : "official"; };
      const end = [child, parent].find((id) => state(id) !== "official");
      if (!end) return null;
      const n = numberOf(end);
      if (state(end) === "approved") return t("#{n} 已经批准，同步以后才能画这条连线", { n: n });
      return end === child && state(parent) === "official"
        ? t("#{n} 还不在正式版本里：在它的提议里写明它源自哪里，或等 PI 批准以后再画这条连线", { n: n })
        : t("#{n} 还不在正式版本里：PI 批准它以后才能画这条连线", { n: n });
    }

    function writeLine(op, child, parent, note) {
      if (proposes()) {
        const body = { kind: "route_link", op: op, child: child, parent: parent, write: true };
        if (note) body.note = note;
        return apiPost("/api/proposals", body);
      }
      const body = { from: child, to: parent, type: "derives_from" };
      if (note) body.note = note;
      return apiPost(op === "add" ? "/api/mappings/add" : "/api/mappings/delete", body);
    }

    function changed() {
      return typeof opts.onChanged === "function" ? opts.onChanged() : null;
    }

    // Drawn at once (dashed, as anything not yet on record), then written;
    // a refusal takes it back and says why.
    async function confirmLine() {
      const p = m.pop;
      if (!p || p.kind !== "new") return;
      const note = p.input.value.trim();
      closePop();
      const line = { child: p.child, parent: p.parent, note: note, optimistic: true };
      m.optimistic.push(line);
      m.focus = p.child;
      const before = held();
      arrange();
      render();
      hold(before);
      try {
        await writeLine("add", p.child, p.parent, note);
      } catch (err) {
        m.optimistic = m.optimistic.filter((x) => x !== line);
        m.focus = null;
        arrange();
        render();
        showStatus(refusal(err && err.state) || t("没能保存这条连线"), err);
        return;
      }
      line.done = true;
      if (proposes()) showStatus(t("已把这条连线作为提议交给 PI。"), null, { notice: true, ms: NOTICE_MS });
      await changed();
    }

    function canRemove(l) {
      return mayEdit() && !l.optimistic && !(l.pending && (l.pending.op === "add" || proposes()));
    }

    async function removeLine(l) {
      const ask = proposes() ? t("提议删除这条连线？PI 批准后它才会从路线图上消失。")
        : t("删除这条连线？它会从项目的记录里删去。");
      if (!window.confirm(ask)) return;
      selectLine(null);
      try {
        await writeLine("remove", l.child, l.parent);
      } catch (err) {
        showStatus(refusal(err && err.state) || t("没能删除这条连线"), err);
        return;
      }
      if (proposes()) showStatus(t("已把删除这条连线作为提议交给 PI。"), null, { notice: true, ms: NOTICE_MS });
      await changed();
    }

    // A line selected: what it says, its note, who drew it and when, and
    // 「删除这条连线」 where it can be removed -- 「提议删除这条连线」 for a member.
    function selectLine(key) {
      if (m.pop && m.pop.kind === "line") closePop();
      m.selectedLine = key;
      if (!m.dom) return;
      m.dom.links.querySelectorAll(".rm-line").forEach((g) => g.classList.toggle("selected", g.getAttribute("data-key") === key));
      const l = key ? lineOf(key) : null;
      if (!l) return;
      closePop();
      const card = el("div", "cv-linkcard human rm-linecard");
      card.appendChild(el("div", "cv-linkcard-assert", lineWords(numberOf(l.child), numberOf(l.parent))));
      if (l.note) card.appendChild(el("div", "cv-linkcard-hint", l.note));
      const day = window.RODesk && window.RODesk.dayWords ? window.RODesk.dayWords(l.date, new Date()) : l.date;
      const who = [l.by_label, day].filter(Boolean).join(" · ");
      if (who) card.appendChild(el("div", "cv-linkcard-hint", who));
      const mark = lineMark(l);
      if (mark) card.appendChild(el("div", "cv-linkcard-hint", mark));
      if (canRemove(l)) {
        const actions = el("div", "cv-linkcard-actions");
        const del = el("button", "cv-text-btn", proposes() ? t("提议删除这条连线") : t("删除这条连线"));
        del.type = "button";
        del.addEventListener("click", () => removeLine(l));
        actions.appendChild(del);
        card.appendChild(actions);
      }
      m.pop = { el: card, kind: "line" };
      const mid = curve(ends(l)).mid;
      floating(card, mid.x, mid.y);
    }

    // -- the pointer and the keys ----------------------------------------------------

    function cardAt(x, y) {
      for (let i = m.attempts.length - 1; i >= 0; i -= 1) {
        const id = m.attempts[i].id, p = m.positions[id];
        if (p && x >= p[0] && x <= p[0] + CARD_W && y >= p[1] && y <= p[1] + cardH()) return id;
      }
      return null;
    }

    function onDown(e) {
      if (e.button !== 0 || !m.data) return;
      if (e.target.closest(".cv-toolbar, .cv-status, .cv-pop, .cv-linkcard")) return;
      closePop();
      const card = e.target.closest(".rm-card");
      const base = { sx: e.clientX, sy: e.clientY, moved: false };
      if (card && e.target.closest(".rm-handle")) {
        m.drag = Object.assign(base, { kind: "line", from: card.dataset.id, target: null });
      } else if (card) {
        m.drag = Object.assign(base, { kind: "card", id: card.dataset.id, el: card, start: m.positions[card.dataset.id].slice(),
          // the handback lines of a card are their own click (21.2)
          handback: e.target.closest(".rm-look") ? "waiting" : e.target.closest(".rm-headline") ? "latest" : null });
      } else {
        const line = e.target.closest(".rm-line");
        m.drag = Object.assign(base, { kind: "pan", cam: Object.assign({}, m.camera), line: line ? line.getAttribute("data-key") : null });
      }
      try { m.dom.root.setPointerCapture(e.pointerId); } catch (err) { /* a synthetic event */ }
      e.preventDefault();
    }

    function onMove(e) {
      const d = m.drag;
      if (!d) return;
      const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
      if (!d.moved && Math.hypot(dx, dy) < DRAG_PX) return;
      d.moved = true;
      if (d.kind === "pan") {
        m.dom.root.classList.add("panning");
        m.camera = { x: d.cam.x + dx, y: d.cam.y + dy, zoom: d.cam.zoom };
        applyCamera();
      } else if (d.kind === "card") {
        d.el.classList.add("dragging");
        m.positions[d.id] = [Math.round(d.start[0] + dx / m.camera.zoom), Math.round(d.start[1] + dy / m.camera.zoom)];
        placeCard(d.el, d.id);
        renderLines();
      } else {
        m.dom.root.classList.add("linking");
        const w = worldAt(e.clientX, e.clientY), hit = cardAt(w.x, w.y);
        d.target = hit && hit !== d.from ? hit : null;
        d.over = hit; // the card under the pointer, its own included (a self-line, 14.8 #2)
        if (hit !== d.from || Math.hypot(dx, dy) > HANDLE_PX) d.left = true;
        markTarget(d.target);
        drawDraft(d.from, d.target ? { x: m.positions[d.target][0], y: m.positions[d.target][1] + CARD_H / 2 } : w);
      }
    }

    function onUp(e) {
      const d = m.drag;
      if (!d) return;
      endDrag();
      try { m.dom.root.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
      if (d.kind === "pan") {
        if (d.moved) userCamera();
        else if (d.line) selectLine(d.line);
        else { select(null); selectLine(null); }
      } else if (d.kind === "card") {
        d.el.classList.remove("dragging");
        if (d.moved) cardMoved(d.id);
        else if (!(d.handback && openHandbackOf(d.id, d.handback))) openCard(d.id);
      } else if (!d.moved || e.type === "pointercancel") {
        drawDraft(null, null);
        if (!d.moved && e.type !== "pointercancel") openCard(d.from); // a click on the handle is a click on its card
      } else if (d.target) askLine(d.from, d.target);
      else if (d.over === d.from && d.left) askLine(d.from, d.from); // back on its own card: refused, said
      else drawDraft(null, null); // dropped on nothing: nothing happens
      afterDrag();
    }

    // What every end of a drag clears -- a drop and Escape alike: the drag,
    // the cursor's state and the card marked as the target.
    function endDrag() {
      m.drag = null;
      m.dom.root.classList.remove("panning", "linking");
      markTarget(null);
    }

    function afterDrag() {
      const later = m.later;
      m.later = null;
      if (later) update(later[0], later[1]);
    }

    function openCard(id) {
      select(id);
      if (typeof opts.onOpen === "function") opts.onOpen(id);
    }

    // A click on a card's handback line (21.2) opens that handback, `onOpenHandback(report)`: the line of the latest
    // handback opens the latest, 「等你看 n」 the first of those not opened yet; a click on the rest of the card opens
    // the attempt. False when the card has no handback to open.
    function openHandbackOf(id, which) {
      const a = m.byId.get(id), work = (a && a.work) || {}, latest = work.latest;
      const waiting = which === "waiting" ? (work.items || []).find((item) => item.unopened && item.report) : null;
      const report = (waiting && waiting.report) || (latest && latest.report);
      if (!report || typeof opts.onOpenHandback !== "function") return false;
      select(id);
      opts.onOpenHandback(report);   // not from a panel: there is nothing to go back to
      return true;
    }

    function onWheel(e) {
      if (!m.data) return;
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
      if (e.ctrlKey || e.metaKey) {
        zoomAt(m.camera.zoom * Math.exp(-clamp(e.deltaY * unit, -60, 60) * 0.01), e.clientX, e.clientY);
        return;
      }
      m.camera = { x: m.camera.x - e.deltaX * unit, y: m.camera.y - e.deltaY * unit, zoom: m.camera.zoom };
      applyCamera();
      userCamera();
    }

    // Hidden itself or inside anything hidden (another section shows, no
    // project is open): not drawn, so no box.
    function visible() {
      return !!(m.dom && m.dom.root.isConnected && !m.dom.root.closest(".hidden") && m.dom.root.getClientRects().length);
    }

    function onKey(e) {
      if (!visible()) return;
      const target = e.target;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT"
        || target.isContentEditable)) return;
      const panel = document.getElementById("panel");
      if (panel && !panel.classList.contains("hidden")) return; // the side panel has the keys
      if (e.key === "Escape") {
        if (m.drag && m.drag.kind === "line") { endDrag(); drawDraft(null, null); afterDrag(); return; }
        if (m.pop) { closePop(); if (m.selectedLine) selectLine(null); return; }
        select(null);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if ((e.key === "Backspace" || e.key === "Delete") && m.selectedLine) {
        e.preventDefault();
        const l = lineOf(m.selectedLine);
        if (l && canRemove(l)) removeLine(l);
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        fitAll();
      }
    }

    return Object.freeze({
      mount, update, visible, fitAll, highlight,
      zoomIn: () => zoomCenter(m.camera.zoom * 1.2),
      zoomOut: () => zoomCenter(m.camera.zoom / 1.2),
      zoomReset: () => zoomCenter(1),
    });
  }

  window.RORouteMap = Object.freeze({
    create,
    lineRefusal: refusal, // the desk's side panel says a refused removal the same way (14.3)
    // pure, for tests/test_v10_route_map.py
    _layoutRoutes: layoutRoutes, _markWords: markWords, _workWords: workWords, _lineWords: lineWords, _refusal: refusal,
    _checkLine: checkLine, _curve: curve, _labelPlan: labelPlan, _splitLabel: splitLabel,
    _geometry: Object.freeze({ CARD_W, CARD_H, CARD_WORK_H, COL_PITCH, ROW_PITCH, WORK_PITCH, ROW_X, ROW_Y, WORK_ROW_Y, CAPTION_H }),
  });
})();
