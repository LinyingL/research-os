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
// 12.12 says, and 有改动 when the desk says so. The 「源自」 lines are a
// human's lines (clay, 8.2): a parent stands left of what grew out of it,
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
  const COL_PITCH = CARD_W + 112;   // one generation to the next: room for the curve
  const ROW_PITCH = CARD_H + 28;    // rows of the forest
  const ROW_X = CARD_W + 24;        // 「还没标来处」, card to card
  const ROW_Y = CARD_H + 24;
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
  function layoutRoutes(attempts, lines, opts) {
    const fixed = (opts && opts.fixed) || {};
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
    linked.forEach((id) => { auto[id] = [gen.get(id) * COL_PITCH, rowOf.get(id) * ROW_PITCH]; });
    const top = linked.length ? bottom() * ROW_PITCH - (ROW_PITCH - CARD_H) + ROW_GAP + CAPTION_H : CAPTION_H;
    const per = Math.max(ROW_MIN, Math.ceil(Math.sqrt(row.length * ASPECT * ROW_Y / ROW_X)));
    row.forEach((id, i) => { auto[id] = [(i % per) * ROW_X, top + Math.floor(i / per) * ROW_Y]; });
    const held = ids.filter((id) => hasOwn(fixed, id) && validPos(fixed[id]));
    if (!held.length) return { positions: auto, caption: row.length ? [0, top - CAPTION_H] : null, row, pinned: false };
    const positions = {};
    held.forEach((id) => { positions[id] = [Number(fixed[id][0]), Number(fixed[id][1])]; });
    const inRow = held.filter((id) => row.includes(id));
    let x, y;
    if (inRow.length) {
      y = Math.max(...inRow.map((id) => positions[id][1]));
      x = Math.max(...held.filter((id) => Math.abs(positions[id][1] - y) < CARD_H).map((id) => positions[id][0])) + ROW_X;
    } else {
      y = Math.max(...held.map((id) => positions[id][1])) + CARD_H + ROW_GAP + CAPTION_H;
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
      case "mapping_attempt_not_found": return t("连线的一端在年表里找不到了，可能刚被改了编号；请刷新后再试");
      case "attempt_not_found": return t("连线的一端不是正式版本里的尝试：新的尝试请在它自己的提议里写明源自哪里");
      case "no_such_line": return t("正式版本里没有这条连线");
      case "mapping_member_proposes": return t("你是这个项目的成员：连线要作为提议交给 PI 批准；请刷新后再画");
      case "pi_changes_directly": return t("你是 PI：连线直接写入正式版本；请刷新后再画");
      case "not_in_team": return t("你还不是这个项目的成员：请 PI 把你加入团队，然后同步");
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
  // after a line was written or proposed (the desk reads itself again).
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

    function proposes() { return m.role !== "pi" && m.role !== "single"; }
    function numberOf(id) { const a = m.byId.get(id); return a ? String(a.number).replace(/^#/, "") : String(id).split("#").pop(); }
    function allLines() { return m.lines.concat(m.optimistic); }
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
      root.append(world, bar, zoom, status, empty);
      host.appendChild(root);
      m.dom = { root, world, links, draft, cards, zoom, status, empty };
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
          m.resizeTimer = setTimeout(() => { if ((m.autoFit || m.needsFit) && m.data && !m.drag) fit(); }, RESIZE_MS);
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
        const vp = layout.viewport;
        if (vp && isFinite(vp.x) && isFinite(vp.y) && vp.zoom > 0) {
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
      d.empty.textContent = t("年表里还没有尝试。");
      d.empty.classList.toggle("hidden", m.attempts.length > 0);
      if (m.caption) {
        const c = el("div", "rm-caption", t("还没标来处"));
        c.style.left = m.caption[0] + "px";
        c.style.top = m.caption[1] + "px";
        d.cards.appendChild(c);
      }
      m.attempts.forEach((a) => d.cards.appendChild(cardEl(a)));
      renderLines();
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
      c.title = num + " " + (a.title || "") + (words ? "\n" + words : "");
      const head = el("div", "rm-head");
      head.append(el("span", "rm-num", num), el("span", "rm-title", a.title || ""));
      const meta = el("div", "rm-meta");
      if (a.date) meta.appendChild(el("span", "rm-date", a.date));
      meta.appendChild(el("span", "status-label tone-" + tone, verdictWord(a.route_status)));
      const marks = el("div", "rm-marks");
      markWords(a, !!(m.changed && m.changed(a))).forEach((w) => marks.appendChild(el("span", "rm-mark", w)));
      const handle = el("div", "rm-handle");
      handle.title = t("从这里拖到另一条尝试上：那一条源自这一条");
      c.append(head, meta, marks, handle);
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
        if (word) {
          const label = svgEl("text", { x: c.mid.x + 8, y: c.mid.y - 8 }, "rm-line-label");
          label.textContent = word;
          grp.appendChild(label);
        }
        g.appendChild(grp);
      });
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
      const shown = (id) => {
        const x = m.positions[id][0] * z + m.camera.x, y = m.positions[id][1] * z + m.camera.y;
        return x + CARD_W * z > 0 && x < v.w && y + CARD_H * z > TOOLBAR_H && y < v.h;
      };
      if (m.marked.some(shown)) return;
      const p = m.positions[m.marked[0]];
      m.camera = { x: v.w / 2 - (p[0] + CARD_W / 2) * z, y: v.h / 2 - (p[1] + CARD_H / 2) * z, zoom: z };
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
      Object.values(m.positions).forEach((p) => take(p, CARD_W, CARD_H));
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
        showStatus(t("位置没能保存，下次移动时会重试"), err);
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
        const toggle = el("button", "err-detail-toggle", t("详情"));
        toggle.type = "button";
        toggle.addEventListener("click", () => detail.classList.toggle("hidden"));
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
      const why = checkLine(allLines(), child, parent);
      if (why) {
        drawDraft(null, null);
        showStatus(refusal(why), null, { ms: NOTICE_MS });
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
      input.setAttribute("aria-label", t("备注"));
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
      return !l.optimistic && !(l.pending && (l.pending.op === "add" || proposes()));
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
    // 「删除这条连线」 where it can be removed (or its removal proposed).
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
      const who = [l.by_label, l.date].filter(Boolean).join(" · ");
      if (who) card.appendChild(el("div", "cv-linkcard-hint", who));
      const mark = lineMark(l);
      if (mark) card.appendChild(el("div", "cv-linkcard-hint", mark));
      if (canRemove(l)) {
        const actions = el("div", "cv-linkcard-actions");
        const del = el("button", "cv-text-btn", t("删除这条连线"));
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
        if (p && x >= p[0] && x <= p[0] + CARD_W && y >= p[1] && y <= p[1] + CARD_H) return id;
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
        m.drag = Object.assign(base, { kind: "card", id: card.dataset.id, el: card, start: m.positions[card.dataset.id].slice() });
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
        markTarget(d.target);
        drawDraft(d.from, d.target ? { x: m.positions[d.target][0], y: m.positions[d.target][1] + CARD_H / 2 } : w);
      }
    }

    function onUp(e) {
      const d = m.drag;
      if (!d) return;
      m.drag = null;
      m.dom.root.classList.remove("panning", "linking");
      try { m.dom.root.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
      markTarget(null);
      if (d.kind === "pan") {
        if (d.moved) userCamera();
        else if (d.line) selectLine(d.line);
        else { select(null); selectLine(null); }
      } else if (d.kind === "card") {
        d.el.classList.remove("dragging");
        if (d.moved) cardMoved(d.id);
        else openCard(d.id);
      } else if (!d.moved || e.type === "pointercancel") {
        drawDraft(null, null);
        if (!d.moved && e.type !== "pointercancel") openCard(d.from); // a click on the handle is a click on its card
      } else if (d.target) askLine(d.from, d.target);
      else drawDraft(null, null); // dropped on nothing: nothing happens
      afterDrag();
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
        if (m.drag && m.drag.kind === "line") { m.drag = null; drawDraft(null, null); markTarget(null); afterDrag(); return; }
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
    // pure, for tests/test_v10_route_map.py
    _layoutRoutes: layoutRoutes, _markWords: markWords, _lineWords: lineWords, _refusal: refusal,
    _checkLine: checkLine, _curve: curve,
    _geometry: Object.freeze({ CARD_W, CARD_H, COL_PITCH, ROW_PITCH, ROW_X, ROW_Y, CAPTION_H }),
  });
})();
