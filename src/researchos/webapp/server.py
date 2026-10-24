"""Local read-only web view over the graph (task V1, DESIGN.md section 7,
"Later"). stdlib `http.server` only -- zero third-party dependency, same
constraint as every other subcommand (pyproject.toml `dependencies = []`).

Bound to `127.0.0.1` only, hardcoded in `build_server` -- this is never a
multi-user or network-facing service, and nothing in this module accepts a
`--host` argument to change that.

Every endpoint below is a read over the graph (`researchos.db`/`researchos.lineage`) or the
project filesystem, with one deliberate exception: `POST /api/open`, which
shells out to macOS's `open` to reveal a path in Finder or open it with its
default application -- never to execute or modify project content, and
always with the path list-argument form (`subprocess.run([...])`, never
`shell=True`) so there is no shell-injection surface regardless of what the
path string contains.

Endpoints (all GET unless noted):

    GET  /api/summary   -- node/edge counts by type, project root, pending
                            confirmation queue size, plus an echo of the
                            attempts config's columns/file (or null when no
                            usable `.researchos/attempts.toml` exists) so the app
                            can build the edit form from the project's OWN
                            column names, never hardcoded ones (see
                            `summary_payload`).
    GET  /api/attempts  -- attempt nodes (human_fields verdict/result plus
                            every attrs field); `{"attempts": [], "hint":
                            ...}` when the graph has none (see
                            `attempts_payload`).
    GET  /api/tree      -- the decision-tree view, this app's own reason to
                            exist: attempts (natural "#" order, a "14a"/"14b"
                            split nested under "14" when it exists, else
                            siblings) -> each attempt's `attrs.step_files`
                            scripts -> each script's `reads`/`writes` data
                            files, tagged `has_generator`/`orphan_input`.
                            Derived entirely from existing graph edges --
                            zero inference (see `tree_payload`).
    GET  /api/lineage   -- `researchos.lineage.build_lineage_report`'s four blocks,
                            unchanged (see `lineage_payload`) -- this endpoint
                            does not re-implement that report.
    GET  /api/file      -- one project text file's content, UTF-8, capped at
                            `_FILE_SIZE_LIMIT` bytes (truncation flagged, never
                            silent); binary content is refused, not garbled
                            (see `file_payload`). Carries `github`: the
                            11.2 link `{url, commit, local_changes,
                            message}` or reason `{reason, message,
                            local_changes}`, null when not linked (also on
                            the binary refusal). A file only in a synced
                            folder's cloud whose client is not running is
                            not read: 503 `cloud_only` (11.1).
    GET  /api/github    -- 11.2: the linked GitHub repository's state from
                            local refs (`researchos.github.state`) or
                            `{"linked": false, "git": bool}`.
    POST /api/github/fetch -- 11.2: `git fetch <remote>` for the linked
                            remote, under the project lock; returns the new
                            state. Errors: `github_<code>` with `message_zh`.
    GET  /api/github/push-plan -- 11.3: what 「推送」 would do
                            (`researchos.githubpush.plan`): branch, unpushed
                            commits, uncommitted files, the records, the
                            refusals, a `token`. Reads only; no network.
    POST /api/github/commit-records -- 11.3 「先把人工记录提交一次」: commits
                            exactly the changed `.researchos/` files. Body `{}`.
    POST /api/github/create-repo -- 11.3, no remote: `{name, token}` -> a
                            PRIVATE repository via `gh`, added as origin.
    POST /api/github/push -- 11.3: `{token, commit_records?}` -> `git push`
                            of the planned branch (never forced). Errors of
                            all three: `github_<code>` with `message_zh`,
                            `detail`, and `blockers`/`command`/`plan` when
                            they apply.
    POST /api/open      -- body `{"path": REL, "reveal": bool}`: reveal a
                            project path in Finder (`open -R`) or open it with
                            its default application (`open`). macOS only --
                            every other platform gets 501 with an explanation,
                            never a confusing subprocess failure (see
                            `open_payload`/`_is_macos`).
    GET  /api/projects  -- the machine-managed project registry
                            (`researchos.webapp.registry`, `~/.researchos/projects.json`)
                            plus which project this server is currently
                            serving: `{"projects": [{path, label,
                            initialized, available}], "current": path}`.
                            `available` is DESIGN.md section 8.10 rule 3's
                            question -- is the directory still there at all
                            -- so the switcher can mark a dead entry and
                            offer to remove it (see `projects_payload`).
    POST /api/projects/remove -- body `{"path"}`: drop one entry from the
                            registry, matched by the same string equality
                            `/switch` uses. Removes a bookmark, never a
                            project: nothing on disk is touched (see
                            `remove_project_payload`).
    POST /api/projects/switch -- body `{"path"}`: repoint this running
                            server at another *registered, initialized*
                            project and return the new current. The path
                            must be string-equal to a registry entry --
                            see "Switch-target defense" below (see
                            `switch_project_payload`). Optional `"id"`
                            narrows the match to that entry (V5).
    POST /api/projects/locate -- body `{"id", "path"}`: 「选择新位置…」 for
                            an entry whose folder moved; adopts the chosen
                            folder only if it carries that id (V5, see
                            `locate_payload`).
    POST /api/projects/inspect -- body `{"path"}` (absolute, `~` expanded):
                            what the folder is and what adding it would do
                            -- `researchos.addproject.Inspection.payload()`: `kind`
                            already_registered | researchos_project | pre_v5 |
                            new_folder | refused | waiting_permission, the
                            9.4 `situation`, the `preview` counts, the
                            `refusal`, the `inspected` token. Writes
                            nothing (DESIGN.md 10.2).
    POST /api/projects/add -- body `{"path", "label", "inspected"}`: adds the
                            folder if it is still what was inspected (409
                            `inspected_changed` with a fresh `inspection`
                            otherwise), opens it as /switch does, and for a
                            new folder starts its first full scan in the
                            background; a folder whose 9.4 question is open
                            is served blocked and registered only when
                            /api/project/resolve answers it (see
                            `add_payload`).
    POST /api/projects/rescan -- no body: the full scan of the served
                            project in the background (10.3); 409
                            `scan_running` / `frozen`. Progress and the
                            result: `GET /api/generation`'s `scanning` and
                            `last_scan`.
    POST /api/projects/rename -- body `{"id", "label"}` (a pre-V5 entry:
                            `{"path", "label"}`): the registry label only
                            (10.4).
    POST /api/project/resolve -- body `{"answer": "fork"|"claim"|"other"|
                            "readonly"|"restore"|"adopt"}`: the answer to a
                            blocked project's question -- only one its
                            `situation.answers` lists (V5, DESIGN.md 9.4,
                            9.12; see `resolve_payload`).
    GET  /api/agents    -- 17.1/17.8: the AI agents of this Mac: the runners
                            found or not (path, version, why not), the saved
                            agents (each with `available`, `mcp`), whether
                            the optional `mcp` package is installed, the
                            suggested Claude Code models, the time limit and
                            spending cap. Works with no project served.
    GET  /api/agents/models?runner=opencode -- `opencode models` (Claude Code:
                            its suggestions); a runner that is not found:
                            409 `agent_runner_missing`.
    POST /api/settings/agents -- body `{agents?, commands?, timeout_minutes?,
                            max_budget_usd?}`: this Mac's agent settings
                            (`researchos.agents.profiles`; keys not sent are
                            kept, a refusal saves none of them); answers like
                            GET /api/agents. Works with no project served.
    GET  /api/agents/runs -- the served project's agent runs, newest first,
                            and which is active (`AgentManager.runs`).
    GET  /api/agents/runs/<id> -- one run: steps, changes, out-of-bounds
                            changes, question, report, error, the instruction
                            sent (`AgentManager.detail`).
    POST /api/agents/runs -- body `{project, task}`: start the agent that owns
                            the task; answers the run's summary at once.
    POST /api/agents/runs/<id>/reply -- `{project, message}`: answer a
                            question or send the task back.
    GET  /api/agents/runs/<id>/revert-plan -- what 撤回本次运行 would do now,
                            without doing it (18.1 item 5): `restore` (files
                            put back), `remove` (files moved away), each path
                            with its `time`, and `cannot` (files it leaves
                            alone, each with a reason code). Refuses as the
                            undo does (`agent_reverted`, `agent_later_run`,
                            `agent_not_revertible`, ...). Reads only.
    GET  /api/agents/large-files -- the served project's files over 16 MB,
                            which a run may change and an undo cannot restore
                            (18.1 item 5): `{count, largest: [{path, size}],
                            partial}`, the ten biggest, biggest first. Reads
                            only.
    POST /api/agents/runs/<id>/stop, /revert -- `{project}`: stop the run and
                            everything it started; undo it (17.6). `revert`
                            takes `files` (the paths the person left checked in
                            the plan; absent = all): the others are left as
                            they are and listed in the run's log as kept.
                            Every run endpoint needs an unblocked served
                            project, a POST carries `project` (409
                            `agent_project_changed` when it is no longer the
                            served one), and every refusal is `agent_<code>`
                            with `message` in the request's language and the
                            English `detail` (DESIGN.md 17.8). Starting,
                            answering and undoing are refused up front, with no
                            lock held, as the write guard would refuse them
                            (`agent_read_only`, `agent_needs_migration`); the
                            run manager takes the project lock itself where it
                            writes. A project with no identity lists no runs.
                            Start and answer also refuse with 409
                            `agent_person_unreadable` when `me.toml` exists but
                            cannot be read. When a run ends the page is told
                            (generation) and the project rescanned -- one that
                            ended while another project was served, when the
                            page switches back to it.
    GET  /api/engine    -- which project this engine serves: `{"engine":
                            "researchos", "version": 5, "pid", "project_root",
                            "project_id", "graph_path", "researchos_home",
                            "blocked", "needs_migration"}` -- what `researchos
                            migrate` asks before retiring an old index (9.12;
                            see `engine_payload`). Opens no index.
    POST /api/attempts/preview -- body `{"op": "append"|"update", "number",
                            "fields"}`: a pure dry run of an attempt-row
                            edit against the researcher's own map file --
                            unified diff plus old/new row, NOTHING written
                            (see `attempts_preview_payload` and
                            `researchos.webapp.mapedit`), `reason_required` (14.3)
                            and, for an update, `cells` (18.3).
    POST /api/attempts/write -- same body: actually performs the edit --
                            backup, atomic write into the source Markdown,
                            re-ingest under the watcher's own ingest lock,
                            generation bump -- and returns `{ok, backup,
                            generation, ingest_error, decision}`. A write
                            that turns a route abandoned carries `reason`
                            (14.3). See "Write-path defense" below (see
                            `attempts_write_payload`).
    GET  /api/generation -- the auto-refresh watcher's status
                            (`researchos.webapp.watcher`, task V3 phase 2):
                            `{"generation": int, "refreshing": bool,
                            "last_error": str|null, "scanning": {step,
                            label, n, m, started}|null, "last_scan":
                            {finished, ok, unreadable_sources, error,
                            message, findings, cloud}|null}` (the last two: the
                            served project's full scan, 10.2/10.3). The frontend polls
                            this and re-fetches its views whenever the
                            generation moved -- see "Auto-refresh" below.
    GET  /api/canvas    -- `?scope=all|<attempt id>` (default: the current
                            attempt): the node canvas's datasets/scripts/
                            figures, links, attempt frames, saved positions
                            and scope list (DESIGN.md 8.1/8.7; see
                            `researchos.webapp.canvas.build_canvas`).
    POST /api/canvas/layout -- body `{"project": <as GET /api/canvas
                            returned it>, "scope": "all"|<attempt id>|"routes",
                            "positions"?: {id: [x, y] | null},
                            "viewport"?: {x, y, zoom} | null,
                            "reset"?: bool}`: merge into THAT view's entry
                            of the arrangement record `.researchos/canvas.json`
                            (8.4, 9.2; see `canvas_layout_payload`); 409
                            `layout_unreadable` while that file cannot be
                            read -- it is never written over.
    POST /api/mappings/add -- body `{"from", "to", "type", "note"?}`: append
                            one human mapping to `.researchos/mappings.toml` and
                            re-ingest it; returns the resulting link (8.5;
                            see "Canvas write defense" below). With type
                            `derives_from`, both ends are attempts
                            (`attempt:<table>#<n>`, 14.2).
    POST /api/mappings/delete -- body `{"from", "to", "type"}`: remove that
                            entry from the file and re-ingest.
    POST /api/edges/reject -- body `{"src", "dst", "type", "extractor",
                            "note"?}`:
                            标记为错误提取 -- a `rejected` entry in
                            `.researchos/judgements.toml`, then applied (V5, 9.3;
                            `researchos.records.judgements.judge`, the one human
                            write path). A human mapping is refused:
                            deleting the entry is how it goes.
    POST /api/edges/restore -- same body: undo the above -- an `undone`
                            entry naming the reject (see
                            `edge_status_payload`).
    POST /api/judgements -- body `{"src", "dst", "type", "extractor",
                            "verdict": confirmed|rejected|withdrawn|undone,
                            "note"?}`: any human act on a machine link from
                            the app, through the same write path.
    GET  /api/review    -- 9.6's list: judgments under review or in
                            conflict (待复核), held because their source could
                            not be read, or whose link the index lacks, plus
                            the judgment ledger's trust state.
    GET  /api/variables, /api/variables/card?id=, /api/variables/code?id=&entry=,
         /api/variables/frozen?id=&entry=  -- the 「变量」 view's reads (9.11;
                            see `researchos.webapp.variables_api`); a blocked
                            project answers 409 `project_blocked` with its
                            situation, a folder moved under the engine 409
                            `project_moved`.
    POST /api/variables/<confirm|revise|reaffirm|abandon|revive|answer|
         full-compare>   -- only what Research OS authors on a card, through
                            `researchos.records.cards` (never the researcher's
                            text); see `researchos.webapp.variables_api`.
    GET  /api/citations -- the 「文献」 view (11.4): per draft, its citations
                            and how each resolves, candidates with the
                            links 确认 / 否决 write through /api/judgements;
                            see `researchos.webapp.literature_api`.
    POST /api/citations/lookup-setting -- `{"on": bool}`: 「用 DOI 联网查文献
                            信息」, Research OS's own setting; nothing is asked here.
    POST /api/settings/language -- `{"language": "zh"|"en"}` (DESIGN.md
                            13.2, 13.3): the switch beside 记录 and 停止服务
                            saves this Mac's interface language
                            (`researchos.settings`), that one key of
                            `settings.json`; anything else is a 400 and
                            writes nothing. Allowed with no project served.
    GET  /api/settings/llm, POST /api/settings/llm -- `{"base_url"?, "model"?}`:
                            the model server 「生成草稿」 uses (16), this
                            Mac's setting. GET: `{base_url, model}` in
                            effect (saved, else RCE_LLM_*, else the
                            defaults; no user:password@), `saved` (each
                            saved value or null), `where` ("this_mac" for
                            localhost, 127.0.0.0/8, ::1 only -- a `*.local`
                            name is another machine, "elsewhere") and
                            `host`. POST: only the keys sent change, ""
                            returns one to its default; an address that is
                            not http(s) with a host is a 400
                            `draft_bad_setting`, nothing written; answers
                            as GET. Both allowed with no project served.
    POST /api/attempts/draft -- `{"files": [REL], "language"?: "zh"|"en"}`:
                            a draft of a new attempt's description and
                            result from at most 5 chosen files (16,
                            `researchos.semantic.draft`); writes nothing,
                            takes no project lock while the model runs.
                            Each path is resolved inside the project and
                            sent, deduplicated (one file under two
                            spellings is one), as its root-relative POSIX
                            path; one file that cannot be read refuses the
                            whole draft, the body naming it under `file`
                            as typed: `draft_file_missing` (404, or 400
                            for a folder or an unusable path),
                            `draft_file_not_text` (400; a .dta too),
                            `draft_file_unreadable` (400: no permission,
                            a read error), `draft_file_cloud_only` (503),
                            `draft_file_outside` (403). Also
                            `draft_no_files`, `draft_too_many_files` (400),
                            `draft_too_long` (413: the model's context
                            overflowed) and `draft_model_failed` (502).
                            Answers `{fields, dropped, model, cut}`.
    POST /api/zotero/open-attachment -- `{"item_key", "attachment_key"?}`:
                            「打开 PDF」, only the file the Zotero database
                            names for that item, inside its storage
                            directory; never a path from the request.
    POST /api/zotero/open-item -- `{"item_key"}`: 「在 Zotero 中打开」
                            (`zotero://select/...` from a validated key).
    GET  /api/history   -- `?src&dst&type&extractor`: every ledger entry
                            for one link, in order (9.9 #12), each with
                            the basis it was made on.
    GET  /api/records   -- the 9.2 inventory (`researchos records`) for the app's
                            「记录」 panel (see `records_payload`).
    POST /api/project/reopen -- no body: the identity check again on the
                            served folder (「重新打开」; see
                            `reopen_payload`).
    POST /api/records/answer -- body `{"file": "judgements", "answer":
                            "file"|"restore"}` answers 「记录文件比图谱少了 N
                            条连线确认」 (9.3); `{"file": "canvas", "answer":
                            "set_aside"}` moves an arrangement record Research OS
                            cannot read into `.researchos/backups/`.
    GET  /api/migration -- 9.5: the pre-V5 indexes waiting for this folder,
                            each with what it holds and its match (how many
                            of its judged links' endpoints a scan of this
                            folder produces -- a scan into a scratch index
                            that is removed), and an unfinished migration.
    POST /api/migration/run -- body `{"answer": "migrate"|"not_mine"}`: the
                            explicit act of 9.5 (`researchos.migration.migrate`,
                            under the project lock from first step to last),
                            or 「这不是这个项目的」 (the old index untouched,
                            the refusal remembered for this folder). The
                            folder is reopened afterwards (it may now have
                            an id). `summary.migration` says what waits.
    POST /api/shutdown  -- respond `{"ok": true}`, then stop this server's
                            `serve_forever` loop from a separate thread
                            (task V3 phase 4) -- the app's 停止服务 button;
                            see "Shutdown defense" below. Optional body
                            `{"pid": int}`: 409 and keep serving unless it
                            is this process (Research OS.app's quit; see
                            `_check_shutdown_target`).
    GET  /             -- the single-page app (task V2), served verbatim from
                            `src/researchos/webapp/app.html`: inline CSS/JS, zero
                            external resources, zero build step -- it reads
                            this same JSON API entirely client-side (see that
                            file's own top comment for the two-view contract).
    GET  /i18n.js      -- the interface language and the English table
                            (DESIGN.md 13.6), loaded first by the page:
                            `src/researchos/webapp/i18n.js` read fresh, under
                            one line written at serve time,
                            `window.ROI18N_SAVED = "zh"|"en"|null;` (the
                            saved choice, 13.2), so the page knows its
                            language before it draws anything, with no
                            request; `Cache-Control: no-store`.
    Every API request from the page carries `X-ResearchOS-Lang: zh|en`
    (13.2); a sentence the engine composes for the page follows it
    (`RceRequestHandler._lang`, `researchos.settings.say`), Chinese without it.
    GET  /desk.js      -- the sidebar and the desk (DESIGN.md 12.0, 12.9;
                            task V8 phase U2), served verbatim from
                            `src/researchos/webapp/desk.js` exactly as
                            /canvas.js is: read fresh, same origin check,
                            packaged as `package-data`.
    GET  /node.js      -- the node page, 图表与论断 and 数据与代码 (DESIGN.md
                            12.6, 12.7, 12.9; task V8 phase U3), served the
                            same way.
    GET  /routemap.js  -- the desk's route map (DESIGN.md 14.1, 14.2; task
                            V10), its own renderer, served the same way.
    GET  /api/node     -- `?node=<id or path>`: one object's page -- its
                            trace as columns with the lines between them,
                            its breaks with their repairs, the links that
                            touch it (`researchos.nodepage.node`).
    GET  /api/results  -- 图表与论断: the drafts' figures and tables by section
                            with their trace state; `?draft=<file>`: that
                            draft's claims.
    GET  /api/data-code -- 数据与代码: every script and data file with its
                            reads, writes and lineage-report blocks.
    GET  /team.js      -- 研究路线, 提议 and 成员 (DESIGN.md 12.3, 12.4,
                            12.9; task V8 phase U4), served the same way.
    GET  /api/routes   -- 研究路线: the attempts grouped by status, an
                            abandoned route's result and reason
                            (`researchos.nodepage.routes`).
    GET  /api/evidence -- `?node=attempt:…`: the decisions about one attempt
                            with the files an approved abandonment froze;
                            `GET /api/history/version?node=&version=` one
                            kept version as it was.
    GET  /api/me/key   -- this machine's public key, if it has one (a GET
                            never makes one); POST makes it. POST
                            /api/team/init, /api/share/set {shared}, POST
                            /api/share/join-preview {shared, folder} (writes
                            nothing) and GET /api/share/sync-preview: U4's
                            collaborative acts, each said before it writes;
                            a join opens the new working copy.
    GET  /api/desk     -- the desk, the route map (DESIGN.md 14): the
                            attempts with their marks and when each last
                            changed, the 「源自」 lines and the column --
                            proposals, 待复核, a member's sync state, read
                            from the shared folder for at most a few
                            seconds (`researchos.desk.payload`) -- and
                            `layout`: the map's arrangement, the view
                            `routes` of canvas.json, with the `project` a
                            layout POST echoes (`desk_payload`). Reads only.
    GET  /canvas.js    -- the node canvas's script (DESIGN.md section 8, task
                            V4 phase 2a), served verbatim from
                            `src/researchos/webapp/canvas.js` with the same
                            read-fresh discipline as `/`. The page's one
                            same-origin `<script src>`: kept out of app.html
                            so the canvas (and the link editing that builds
                            on it) stays a file of its own rather than
                            doubling the page. Same origin check as every
                            other route -- a foreign page cannot even fetch
                            the script through a rebound hostname.

Path-traversal defense (`/api/file` and `/api/open` alike, both required by
task V1): `_resolve_within_root` resolves the requested path -- symlinks
included -- and rejects it unless the *resolved* path is still under the
project root. This is what actually stops `../../etc/passwd`, an absolute
path, and a symlink planted inside the project that points outside it: all
three end up outside `root` after `Path.resolve()`, so the same one check
catches every case rather than pattern-matching on `..` textually (which a
symlink would trivially evade).

Cross-origin defense (every endpoint, `RceRequestHandler._check_local_origin`,
security-review fix): a page open in the user's browser on any *other*
origin can still make this loopback server do something just by having the
browser send it a request -- a "simple" cross-origin POST (e.g.
`Content-Type: text/plain`) needs no CORS preflight at all, and even a plain
cross-origin GET always reaches the server; the browser only blocks the
*page's own script* from reading a cross-origin GET's response body (no
`Access-Control-Allow-Origin` header is ever sent here), which protects
nothing against `POST /api/open`'s side effect of shelling out to `open`.
DNS rebinding defeats even that read-block: a domain the attacker controls
can resolve to something else while the browser loads the page, then to
`127.0.0.1` on a later request, and the browser's same-origin check compares
against the *hostname it believes it dialed*, never the IP it actually
reached. Both `do_GET` and `do_POST` call `_check_local_origin` before doing
anything else: it rejects unless `Host` equals `127.0.0.1:<the port this
process actually bound>` (a rebound or attacker-controlled hostname never
produces that exact Host value, no matter what it resolves to) and, only
when a browser actually sends one, `Origin` equals that same
`http://127.0.0.1:<port>` or the portless `http://127.0.0.1` (Safari drops
the non-default port when serializing a same-origin POST's Origin -- a
foreign page's Origin still always names its own host, so the portless
loopback form proves the same thing the exact one does) -- missing entirely
is accepted, since a non-browser CLI caller (`curl`, this module's own test
suite) never sends one.

Engine session (DESIGN.md 18.1, every write): the origin check says a request
came from a page this loopback server itself served, not that the program
sending it is the researcher's page -- a script an AI agent writes and runs
can POST here with the right `Host` and no `Origin` at all. So every request
other than GET and HEAD must also carry the session the engine made when it
started (`serve`: 32 random bytes, kept in memory and in
`<home>/engine/<port>.session`, mode 0600): the cookie `ro_session_<port>`
(set by `GET /boot?session=...`, which `researchos serve` and the Mac app load
first) or the header `X-ResearchOS-Session`. `RceRequestHandler._check_session`
runs right after `_check_local_origin` and before routing or reading the body;
without it the answer is 403 `session_required` with a sentence in the
request's language, and nothing is changed. A page without the session still
reads (every GET but `/boot` is open, see `do_GET`'s audit note). The secret
is compared in constant time and is never printed or logged. `build_server`
takes the secret as `session`; None (the default, what the tests build) means
no session is asked for, `serve` never builds one that way.

Switch-target defense (`POST /api/projects/switch`, task V3 phase 1): this
is the one endpoint that changes what the whole server serves, so its input
is never treated as a filesystem path at all. The requested string must be
*string-equal* to a `researchos.webapp.registry.load()` entry's `"path"` -- no
resolution, no normalization, no prefix logic is ever applied to the
client-supplied value -- and that entry must be an initialized project
(its graph exists, `researchos.paths.graph_exists`). The registry lives at
`~/.researchos/projects.json`, outside every project root, and only
`researchos serve <path>` on this user's own
command line (plus a successful switch's recency bump) ever writes it; so
even a request that somehow got past `_check_local_origin` could only ever
choose among projects the user has already deliberately served, never point
the server at `/etc` or another arbitrary directory. `_check_local_origin`
still runs first, exactly as for every other endpoint -- this validation is
depth behind that check, not a replacement for it.

Write-path defense (`POST /api/attempts/preview`/`.../write`, task V3
phase 3): these are the endpoints that can change the researcher's own map
file, so the stakes are the drive-by page again -- `_check_local_origin`
runs first, exactly as for every other endpoint, and is what stands between
a hostile page's no-preflight cross-origin POST and a write into the user's
research log. Behind that check, depth: the file written is never named by
the request at all -- it is always the one `.researchos/attempts.toml` configures
(`researchos.webapp.mapedit` loads it server-side), so no request body can steer
the write to another path; the edit itself is validated against the file's
current content (duplicate/unknown row numbers, newlines, undecodable
content all refuse cleanly); the original is backed up to `.researchos/backups/`
before every write and the write is atomic; and the post-write re-ingest
runs under the watcher's own ingest lock so a UI write and a watcher poll
never ingest concurrently (`ProjectWatcher.ingest_lock`,
`record_external_change`). This is the one place the app writes project
content, and it writes only what DESIGN.md declares the single source of
truth -- the map file -- letting re-ingest mirror it into the graph, never
the graph directly ("resync from source", DESIGN.md section 4).

Canvas write defense (DESIGN.md section 8.5/8.6, task V4 phase 1b): the
canvas adds the second researcher-owned file the app writes,
`.researchos/mappings.toml`, and the same layers apply in the same order.
`_check_local_origin` first. The file written is never named by the
request -- it is always `researchos.ingest.mappings.mappings_path(root)`; the
request names only the entry's `from`/`to`, which the phase-1a validator
(`researchos.ingest.mappings`, never a second copy of its grammar) confines to the
project root with the same resolve-then-`relative_to` check every read path
takes, before anything is written. The write itself is that module's
fixed-schema writer (backup to `.researchos/backups/`, durable atomic replace),
run under the watcher's ingest lock together with the re-ingest of the
mappings file alone, after which the watcher re-baselines ONLY that file
(`record_external_change(absorb=...)`) so an unrelated save it has not
ingested yet is never swallowed. `_require_db` runs before the write, so a
project whose graph is missing or still in iCloud refuses cleanly instead
of writing a file it then cannot ingest (or blocking on a dataless open).
`.researchos/canvas.json` is written to a path computed from the served root
alone (`researchos.webapp.canvas.canvas_record_path`), never one a request names.
Since V5 the edge-status endpoints and `POST /api/judgements` write NO
status into the graph: they append to `.researchos/judgements.toml` through
`researchos.records.judgements.judge` -- record first, index second (9.1) -- and
only for links the canvas actually draws (`canvas.is_canvas_edge`) or the
review list names, never for a mapping edge (whose truth is its file).

Shutdown defense (`POST /api/shutdown`, task V3 phase 4): the one endpoint
whose side effect is the server itself, so `_check_local_origin` runs first
exactly as everywhere else -- a drive-by page must not be able to kill the
user's running app with a no-preflight cross-origin POST (denial of service
is a side effect too, and this endpoint needs no body at all, so nothing
else would stand in the way). POST-only like every other side effect; a GET
of the path falls through to the ordinary unknown-endpoint 404. The `{"ok":
true}` response is written before anything stops, and `httpd.shutdown()` is
then called from a NEW daemon thread: `shutdown()` blocks until
`serve_forever` has actually exited, so calling it synchronously from the
handler would, on a single-threaded HTTP server, deadlock the very loop it
waits on -- and even under `ThreadingHTTPServer` (where the handler thread
is not the serve loop's thread) detaching it keeps this handler's own
response/connection teardown independent of the loop's. Only the serve loop
is stopped here; closing the listening socket and stopping the watcher stay
where they already live -- `serve()`'s finally-block `server_close`, which
`serve_forever`'s return now reaches, so a UI-initiated stop and a Ctrl+C
tear down through the identical path.

The current project root itself is mutable state on `RceHTTPServer`, read
and written only through accessors holding a `threading.Lock`
(`get_project_root`/`set_project_root`) -- `ThreadingHTTPServer` handles
each request on its own thread, so a switch must never interleave with
another handler reading the root mid-request. Each handler reads the root
once per request (via `_project_root()`) and works with that snapshot; a
switch landing mid-request affects the *next* request, never tears this one.

Auto-refresh (task V3 phase 2): every `RceHTTPServer` owns one
`researchos.webapp.watcher.ProjectWatcher` -- a daemon polling thread that stats
the current project's attempts config/source file/steps_dir every ~2s,
re-runs the relevant ingest in-process on a change, and bumps the
generation counter `GET /api/generation` reports (see that module's own
docstring for the watch-set bounds and failure containment). The thread is
started by `serve()` -- never by `build_server`, so tests that only need
routing get no background polling -- and stopped by `server_close`. A
successful `POST /api/projects/switch` calls `watcher.retarget()` so
polling follows the new root and the frontend's next generation poll
triggers a re-fetch. The watcher endpoint is read-only status; it goes
through `_check_local_origin` exactly like every other endpoint.

Where the graph is, and what happens when it is not there (DESIGN.md
section 8.10, resilience rules 1 and 2): the graph is NOT in the project --
it is at `~/.researchos/graphs/<id>/graph.db`, resolved by `researchos.paths`, because a
project inside an iCloud-synced folder made `sqlite3.connect` block for over
a minute on an evicted file while `/api/generation` and `/api/projects` kept
answering. `_require_db` therefore does three things before any handler gets
a connection: it runs the one-time legacy migration (`resolve_graph_db`), it
refuses a missing graph with `ProjectNotInitializedError`, and -- the
non-obvious one -- it checks macOS's `SF_DATALESS` flag on the file and
raises `GraphDownloadingError` rather than calling `open()` on a file whose
content is still in the cloud, since that call is what blocks and there is
no non-blocking form of it. Both errors carry a machine-readable `state`
alongside their message so the page can render a header state in product
language (「项目不可用 — 图谱文件已不存在」 / 「图谱文件正在从云端下载…」) instead
of an English error box, with the project switcher left usable so the
researcher can move to another project. `/api/projects` and
`/api/generation` still deliberately bypass the database entirely, so a
degraded project can always be navigated away from.

Project identity (DESIGN.md 9.4, task V5). What the server serves is a
`ServedProject`: the folder, the id it was opened with (None for a pre-V5
folder), and -- when the identity check at the entry point (`researchos serve`,
a switch, 「选择新位置…」) found a situation the researcher must answer
first, or a registry entry whose folder is gone -- the machine-readable
`blocked` payload. A blocked project is served anyway: every endpoint that
reads or writes the project answers 409 with `state: "project_blocked"`
and the situation's data (`situation`), so the app can ask the question;
`/api/projects` and `/api/generation` keep answering; nothing is written.
`POST /api/project/resolve {answer: fork|claim|other|readonly|restore|adopt}` takes the
answer (origin-checked like every POST); `POST /api/projects/locate {id,
path}` re-attaches a registry entry whose folder moved, adopting the
chosen folder only if it carries that id -- the one endpoint that takes a
filesystem path from the page, and it reads nothing there but the
identity file and opens nothing that does not carry an id the researcher
already registered.

Every write a page can cause -- a human record (attempt rows, mappings,
link statuses, the canvas arrangement) or an index write (the watcher's
re-ingest) -- runs inside `RceHTTPServer.write_guard`: the cross-process
project lock (9.7), then the 9.4 re-check that the served folder still
exists, still carries the id it was opened with and is still that id's
home. A folder moved in Finder under a running engine therefore gets
nothing written at its old path, and the page is told 「项目已移动或已在
别处认领，请重新打开」 (`state: "project_moved"`). A pre-V5 project refuses
human records (`needs_migration`), and so does one whose migration has
not finished -- whose old index keeps serving reads until the new one is
installed (`_served_db`); a project opened read-only refuses
every write (`read_only`); a lock held by another process for too long is
`project_busy` (503), never a write without it.

No project (DESIGN.md 10.1, 10.5): with an empty registry `researchos serve` starts
anyway, serving `no_project_served()` -- and so does a server whose open
project was removed from the list with none left. Every project endpoint
then answers 409 `state: "no_project"` before routing; what still answers
is the page, `/api/projects` (an empty list, `current` null), `/api/engine`,
`/api/generation`, and what changes the served project (switch, locate,
remove, inspect, add, rename, shutdown).

Adding a project (DESIGN.md Section 10): `/api/projects/inspect`, `/add`,
`/rescan` and `/rename` are the first endpoints that let the page name an
arbitrary folder to write into. `_check_local_origin` runs first, as for
every endpoint; the named path is resolved by `researchos.addproject` (symlinks
followed) and the RESOLVED folder is the one checked against the refusals
(top-level and system folders, Research OS's home, a folder inside or containing a
registered project) and the one written into; adding writes only `.researchos/`
inside a folder that exists, and the index and registry under the Research OS home;
the add is refused unless the folder is still what the `inspected` token
says was seen.

Every handler-facing failure is one of the small `ApiError` subclasses below,
each carrying its own HTTP status; `RceRequestHandler` catches `ApiError`
once per request and renders `{"error": str(exc)}` -- plus `"state"` when the
error names one -- at that status, mirroring `researchos.cli`'s single `CliError`
catch in `main()`.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
import dataclasses
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from sqlite3 import Connection
from typing import Any, Callable, Iterator

from researchos import addproject, cloud, db, dta, github, githubpush, inventory, lineage, migration, names, newproject, officialness, paths, people, workspace
from researchos import settings as settings_mod
from researchos.agents import labels as agent_labels
from researchos.agents import manager as agent_manager
from researchos.agents import profiles as agent_profiles
from researchos.agents import reasoning as agent_reasoning
from researchos.agents import snapshot as agent_snapshot
from researchos.ingest import scan as scan_mod
from researchos import project as project_identity
from researchos import desk as desk_mod
from researchos.records import judgements
from researchos.records import ledger as ledger_mod
from researchos.records import lock as records_lock
from researchos.records import notes as notes_mod
from researchos.records import situation as records_situation
from researchos.ingest import attempts as attempts_ingest
from researchos.ingest import dataflow as dataflow_ingest
from researchos.ingest import git as git_ingest
from researchos.ingest import mappings as mappings_ingest
from researchos.semantic import backend as llm_backend
from researchos.semantic import draft as draft_mod
from researchos.webapp import canvas, literature_api, mapedit, node_api, variables_api
from researchos.records import implementation as card_implementation
from researchos.webapp import registry as project_registry
from researchos.webapp import watcher as project_watcher

logger = logging.getLogger(__name__)

# Kept as module attributes for the messages that quote them; researchos.paths
# owns the definitions (DESIGN.md section 8.10 rule 1).
PROJECT_DIRNAME = paths.PROJECT_DIRNAME
DB_FILENAME = paths.DB_FILENAME

_FILE_SIZE_LIMIT = 200 * 1024  # 200KB (task V1 spec)


# -- Errors -------------------------------------------------------------------


class ApiError(Exception):
    """Base for every error an endpoint can raise; `status` is the HTTP code
    `RceRequestHandler` sends back alongside `{"error": str(self)}`.

    `state` is the optional machine-readable name of a *degraded project*
    condition the frontend has its own product-language wording for
    (DESIGN.md section 8.10 rule 2). It exists because the alternative is
    the page pattern-matching English engine prose to decide what to
    render, which would break the first time a message is reworded. Only
    the two degraded-project states the design names, the canvas's two
    refusals that have their own product-language sentence
    (`mapping_exists` -> 「这条映射已存在」, `human_link` -> delete the
    标注 instead), the canvas layout's `project_changed` (the page drops
    the write, `ProjectChangedError`), the attempt form's coded refusals
    (`AttemptEditError`, `attempt_*`) and a 「源自」 line's (`MappingEditError`,
    `mapping_*`) carry one; every other error stays a plain message the page
    frames generically."""

    status = 400
    state: str | None = None
    # Extra machine-readable keys merged into the error body (the blocked
    # project's situation, the registry entry a locate was for).
    extra: dict[str, Any] | None = None


class NotFoundError(ApiError):
    status = 404


class MissingParamError(ApiError):
    status = 400


class PathTraversalError(ApiError):
    status = 403


class ForbiddenOriginError(ApiError):
    status = 403


#: 18.1: the engine's session, as a write carries it -- the cookie `ro_session_<port>` (the port is
#: in the name because cookies are shared across the ports of 127.0.0.1) or this header.
SESSION_COOKIE_PREFIX = "ro_session_"
SESSION_HEADER = "X-ResearchOS-Session"
#: What a window without the session is told when its first write is refused (18.1), in both languages.
#: The second sentence is for the window that is the app already: an app built before the session existed loads the
#: plain address, and only `researchos app` makes a new one.
SESSION_REQUIRED_ZH = ("这个窗口没有连上 Research OS 的写入权限：请从 Research OS 应用打开它。"
                       "如果你已经在应用里，说明应用是旧版本：请运行 researchos app 重新生成。")
SESSION_REQUIRED_EN = ("This window cannot save: open it from the Research OS app. "
                       "If you already did, the app is out of date: run researchos app to rebuild it.")


#: A `/boot?...` request target, and the `session=` values in it (the secret is the first one the engine reads).
_BOOT_TARGET = re.compile(r"/boot\?[^\s\"#]*")
_SESSION_VALUE = re.compile(r"(session=)[^&]*")


def _without_secret(text: str) -> str:
    """`text` (a request line, a path) with the secret of a `/boot?session=...` hidden, wherever it stands among
    the query's other parameters: whatever the engine logs about a request goes through this, since the log is a
    file any program of the user can read (18.1)."""
    return _BOOT_TARGET.sub(lambda m: _SESSION_VALUE.sub(r"\1<hidden>", m.group(0)), text)


class SessionRequiredError(ApiError):
    """A write that carries no valid engine session (18.1): 403, `state` and `code`
    `session_required`, `message` the sentence in the request's language, `detail`
    what the engine looked for. The page shows `message` where the write's status
    shows (app.html's BLOCKING_ERROR_TEXT)."""

    status = 403
    state = "session_required"

    def __init__(self, port: int, lang: str) -> None:
        super().__init__(
            f"the request carries no engine session: send the cookie {SESSION_COOKIE_PREFIX}{port} "
            f"or the header {SESSION_HEADER}")
        self.extra = {"code": self.state, "detail": str(self),
                      "message": settings_mod.say(SESSION_REQUIRED_ZH, SESSION_REQUIRED_EN, lang)}


class NotAFileError(ApiError):
    status = 400


class BinaryFileError(ApiError):
    status = 415


class WorkspaceApiError(ApiError):
    """An authoring refusal with a localized sentence and a stable code."""

    def __init__(self, exc: Exception, lang: str) -> None:
        super().__init__(str(exc))
        self.status = exc.status
        self.state = "workspace_" + exc.code
        self.extra = {"message": exc.message_zh if lang == "zh" else str(exc), "detail": str(exc)}


class _AgentRefusal(Exception):
    """A refusal of the agent endpoints that is not the run manager's
    (`AgentError`): same shape -- `code`, `status`, English text,
    `message_zh` -- so `AgentApiError` renders both alike."""

    def __init__(self, code: str, status: int, message: str, message_zh: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message_zh = message_zh


def _needs_migration_refusal(message: str) -> _AgentRefusal:
    """`agent_needs_migration`: the project has no identity yet or is frozen
    until `researchos migrate` (9.12) -- what the write guard refuses as
    `needs_migration`, in the agent endpoints' shape."""
    return _AgentRefusal("needs_migration", 409, message, "这个项目还需要先迁移到新版本，迁移前不会写入任何内容")


class AgentApiError(ApiError):
    """A refusal of the AI-agent endpoints (DESIGN.md 17.8): `state`
    `agent_<code>`, `message` the page's sentence in the request's
    language, `detail` the English one."""

    def __init__(self, exc: agent_manager.AgentError | _AgentRefusal, lang: str) -> None:
        super().__init__(str(exc))
        self.status = exc.status
        self.state = "agent_" + exc.code
        self.extra = {"message": exc.message_zh if lang == "zh" else str(exc), "detail": str(exc)}


class DtaApiError(ApiError):
    """A Stata .dta file the header reader refused (DESIGN.md 15.4):
    truncated, corrupt, not a .dta file at all. `state` "dta_refused",
    `code` the reader's code; `message` the page's sentence in the
    request's language, `detail` the reader's own account."""

    status = 422
    state = "dta_refused"

    def __init__(self, exc: dta.DtaError, lang: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(str(exc))
        self.extra = {"code": exc.code, "message": exc.say(lang), "detail": str(exc), **(extra or {})}


class UnsupportedPlatformError(ApiError):
    status = 501


class ProjectNotInitializedError(ApiError):
    """No graph for this project -- never initialized, or (section 8.10
    rule 2) it vanished mid-serve. The `state` is what turns this into the
    header state 「项目不可用 — 图谱文件已不存在」 with the project switcher
    still usable, rather than an English error box over an empty view."""

    status = 400
    state = "graph_missing"


class GraphDownloadingError(ApiError):
    """The graph file exists but macOS has evicted its content to iCloud
    (`SF_DATALESS`). 503 because it is transient by nature -- the download
    is presumably in flight -- and answering it immediately is the entire
    point: `open()`ing a dataless file blocks the handler thread until the
    transfer completes, which is the hang section 8.10 rule 1 was written
    to end. The page says 「图谱文件正在从云端下载…」 and keeps polling."""

    status = 503
    state = "graph_downloading"


class GraphMigrationError(ApiError):
    """A legacy in-project `graph.db` could not be moved out of the project
    safely (`researchos.paths.GraphMigrationError`). 500, not 400: nothing about
    the request is wrong, and the graph is still exactly where it was --
    this needs a human looking at a log line, not a retry."""

    status = 500


class AttemptEditError(ApiError):
    """`POST /api/attempts/preview`/`.../write` asked for an edit the map
    file's current state refuses (duplicate/unknown number, invalid field
    content, unusable config/table) -- 400: the request, not the server,
    is what cannot be satisfied. Wraps `researchos.webapp.mapedit.MapEditError`
    and `researchos.ingest.attempts.AttemptsConfigError` with their own messages
    intact, since those already say precisely what was wrong.

    The refusals a researcher can cause from the form carry a `state` --
    `attempt_` + `researchos.webapp.mapedit.error_code` (`attempt_duplicate`,
    `attempt_not_found`, `attempt_line_break`, `attempt_table_missing`,
    `attempt_unknown_field`) -- through the same `state` channel the
    mapping refusals use, so the page shows one Chinese sentence per cause
    (DESIGN.md 8.8 "Errors") without matching the English."""

    status = 400

    def __init__(self, message: str, code: str | None = None, message_zh: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.state = "attempt_" + code
        if message_zh:
            self.extra = {"message_zh": message_zh}


def _attempt_edit_error(exc: Exception, project_root: Path) -> AttemptEditError:
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    if isinstance(exc, proposals_mod.ProposalRefused):
        # 14.3: an abandonment with no reason (`attempt_reason_required`), or
        # a decisions.toml that cannot take its basis now -- said in Chinese.
        return AttemptEditError(str(exc), exc.code, exc.message_zh)
    return AttemptEditError(str(exc), mapedit.error_code(exc, project_root))


class MappingEditError(ApiError):
    """`POST /api/mappings/add`/`.../delete` asked for an entry the
    phase-1a writer refuses (grammar, confinement, unknown extension, line
    breaks, an unreadable file) -- 400, its English message intact for the
    hover title; the page frames it as 「无法标注：…」 (section 8.8).

    `code` is the writer's machine-readable cause. The refusals of a line
    between attempts the researcher can cause from the map (DESIGN.md 14.2:
    an attempt not in the index, a line to itself, one that closes a circle,
    a member's line, which goes as a `route_link` proposal, a line written
    while team.toml cannot be read) also carry a `state` -- `mapping_` + the
    code -- so the page says one sentence per cause; every other refusal
    stays framed generically."""

    status = 400
    #: The codes with a sentence of their own on the page (8.8).
    STATED = ("attempt_not_found", "self_link", "circle", "member_proposes", "role_unknown")

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        if code in self.STATED:
            self.state = "mapping_" + code


class MappingExistsError(ApiError):
    """Section 8.5: a duplicate assertion (same from/to/type) is refused at
    write time. 409 -- the request is well-formed, the file's current state
    is what conflicts -- with a `state` so the page says 「这条映射已存在」
    without pattern-matching the English message."""

    status = 409
    state = "mapping_exists"


class HumanLinkError(ApiError):
    """`POST /api/edges/reject`/`.../restore` named a human mapping. Its
    truth is `.researchos/mappings.toml`, so the way to remove it is deleting the
    entry (「删除标注」), never a status write the next ingest would undo.
    The `state` lets the page offer exactly that instead of an error box."""

    status = 400
    state = "human_link"


class EdgeStatusError(ApiError):
    """A restore of a link that is not currently rejected -- 409, the
    graph's current state conflicts with the request (a second click, or
    another window already restored it)."""

    status = 409


class NotThisEngineError(ApiError):
    """`POST /api/shutdown` named a process id that is not this server's
    (see `_check_shutdown_target`). 409: the request is well-formed, but
    the engine listening on the port is not the one it meant to stop."""

    status = 409


class UnknownProjectError(ApiError):
    """`POST /api/projects/switch` asked for a path that is not a registry
    member -- 403, same as the traversal/origin rejections, because whatever
    sent it is trying to steer the server somewhere the user never
    registered (see module docstring's "Switch-target defense")."""

    status = 403


class ProjectBlockedError(ApiError):
    """The served project is in a situation the researcher must answer
    first (DESIGN.md 9.4: a copy, a home that cannot be checked, a lost or
    unreadable identity) or its registry entry's folder is gone
    (`situation: "missing"`). 409, with the situation under `situation`;
    nothing was read from or written to the index."""

    status = 409
    state = "project_blocked"

    def __init__(self, blocked: dict[str, Any]) -> None:
        if blocked.get("situation") == NO_PROJECT_SITUATION:
            # 10.1: no project at all -- not a question to answer.
            super().__init__(NO_PROJECT_MESSAGE)
            self.state = NO_PROJECT_SITUATION
            self.extra = {"message": NO_PROJECT_MESSAGE, "situation": blocked}
            return
        message = blocked.get("detail") or blocked.get("message") or blocked.get("situation", "blocked")
        super().__init__(f"this project cannot be opened until a question is answered: {message}")
        self.extra = {"situation": blocked}


class ProjectMovedApiError(ApiError):
    """9.4's write-time re-check failed: the served folder moved, was
    removed, or was claimed elsewhere. Nothing was written."""

    status = 409
    state = "project_moved"


class NeedsMigrationApiError(ApiError):
    """A human record on a pre-V5 project (9.10). Nothing was written."""

    status = 409
    state = "needs_migration"


class GitHubApiError(ApiError):
    """DESIGN.md 11.2: 「从 GitHub 获取最新状态」 did not complete -- its
    Chinese sentence (`message_zh`) and git's own text, with any credential
    in a URL removed (`github.scrub`), behind 「详情」."""

    def __init__(self, exc: github.GitHubError) -> None:
        super().__init__(exc.detail)
        self.status = {"not_linked": 409, "unsafe_config": 409, "busy": 503, "timeout": 504}.get(exc.code, 502)
        self.state = "github_" + exc.code
        self.extra = {"message_zh": exc.message, "detail": exc.detail}


class GitHubPushApiError(ApiError):
    """DESIGN.md 11.3: a commit of the records, a repository creation or a
    push did not happen -- its Chinese sentence (`message_zh`), git's or
    gh's own text with credentials scrubbed (`detail`), and, when they
    apply, the refusals (`blockers`), the command to run (`command`) and
    the new plan (`plan`)."""

    _STATUS = {
        "invalid_name": 400, "busy": 503, "timeout": 504, "auth": 502, "push_failed": 502,
        "create_failed": 502, "commit_failed": 500, "records_check": 500, "git_missing": 500,
    }

    def __init__(self, exc: githubpush.PushError) -> None:
        super().__init__(exc.detail)
        self.status = self._STATUS.get(exc.code, 409)
        self.state = "github_" + exc.code
        self.extra = {"message_zh": exc.message, "detail": exc.detail, **exc.extra}


class CloudOnlyApiError(ApiError):
    """DESIGN.md 11.1: the file is only in a synced folder's cloud and the
    provider's client is not running -- not opened (a read would wait for a
    download nobody makes). 503: it becomes readable once the client runs."""

    status = 503
    state = "cloud_only"

    def __init__(self, exc: cloud.CloudOnlyError, extra: dict[str, Any] | None = None) -> None:
        super().__init__(str(exc))
        self.extra = {"message_zh": exc.message, "detail": str(exc), **(extra or {})}


class LiteratureApiError(ApiError):
    """An action of the 「文献」 view refused (`researchos.webapp.literature_api.
    LiteratureRefused`): nothing opened or written. `state` is
    `literature_<code>`; `message_zh` the page's sentence, `detail` the
    engine's text."""

    def __init__(self, exc: literature_api.LiteratureRefused) -> None:
        super().__init__(str(exc))
        self.status = exc.status
        self.state = exc.state
        self.extra = exc.extra


class LanguageSettingError(ApiError):
    """POST /api/settings/language refused (DESIGN.md 13.2): `state` is
    `language_bad_setting` (400: not "zh" or "en"; nothing written) or
    `language_setting_failed` (500: the file could not be written)."""

    def __init__(self, code: str, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.state = "language_" + code


class DraftError(ApiError):
    """「生成草稿」 or its model setting refused (DESIGN.md 16): `state`
    is `draft_<code>`; nothing is written to the project."""

    def __init__(self, code: str, status: int, detail: str, file: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.state = "draft_" + code
        if file is not None:
            self.extra = {"file": file}


class ReadOnlyError(ApiError):
    """The project was opened read-only (「原位置暂时不可用，先只读打开」)."""

    status = 409
    state = "read_only"


class ProjectBusyError(ApiError):
    """Another process held the project lock for longer than a request
    waits (9.7). 503: transient; nothing was written."""

    status = 503
    state = "project_busy"


class NotThisProjectError(ApiError):
    """`POST /api/projects/locate` chose a folder that does not carry the
    registry entry's id. Nothing was adopted."""

    status = 409
    state = "not_this_project"


class AnswerRefusedError(ApiError):
    """`POST /api/project/resolve` gave an answer the folder's situation,
    as it is now, does not take. Nothing was written."""

    status = 409
    state = "answer_refused"


class RecordRefusedError(ApiError):
    """A human record write refused because the record cannot be trusted
    right now (DESIGN.md 9.3): missing though expected, in the cloud,
    unreadable, a sync conflict copy beside it, or SHRUNK (state
    `record_shrunk`, with the missing entries). Nothing was written; the
    record's state is under `records`, its Chinese sentence under
    `message`."""

    status = 409
    state = "record_untrusted"

    def __init__(self, message: str, *, state: str | None = None, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        if state is not None:
            self.state = state
        self.extra = extra or {}


class LayoutUnreadableError(ApiError):
    """`.researchos/canvas.json` cannot be read: no arrangement is written over it
    until it is repaired or set aside (9.2). 409."""

    status = 409
    state = "layout_unreadable"


class NodeRefusedApiError(ApiError):
    """A node-page request refused (`researchos.webapp.node_api.NodeApiRefused`,
    DESIGN.md 12.1/12.6/12.7): its status, its code as `state`, and the
    Chinese sentence under `message`. Nothing was written."""

    def __init__(self, exc: node_api.NodeApiRefused) -> None:
        super().__init__(str(exc))
        self.status = exc.status
        self.state = exc.code
        self.extra = {"message": exc.message_zh, **exc.extra}


class NoQuestionError(ApiError):
    """`POST /api/records/answer` when there is no question to answer."""

    status = 409
    state = "no_question"


class MigrationRefusedError(ApiError):
    """`/api/migration[/run]`: nothing waits, or the folder cannot be
    migrated as it is now (9.5). Nothing was written. 409."""

    status = 409
    state = "migration_refused"


class CardRefusedApiError(ApiError):
    """A card action refused (`researchos.webapp.variables_api.CardActionRefused`):
    nothing was written. `state` is `card_<code>`; `message_zh` the page's
    sentence."""

    def __init__(self, exc: variables_api.CardActionRefused) -> None:
        super().__init__(str(exc))
        self.status = exc.status
        self.state = exc.state
        self.extra = exc.extra


class AddProjectError(ApiError):
    """A refusal of the add-project endpoints (DESIGN.md Section 10):
    inspecting, adding, rescanning, renaming. Nothing was written. `state`
    is the machine-readable code; `error` and `message` the app's Chinese
    sentence; `detail` the engine's own text (8.8: 「详情」)."""

    status = 409

    def __init__(
        self, code: str, message_zh: str, detail: str = "", *, status: int = 409, extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message_zh)
        self.state = code
        self.status = status
        self.extra = {"message": message_zh, "detail": detail or message_zh, **(extra or {})}


# 10.1: the engine serves no project at all (an empty registry, or the last
# entry removed). Every project endpoint answers 409 `no_project`.
NO_PROJECT_SITUATION = "no_project"
NO_PROJECT_MESSAGE = "还没有项目：请先添加一个项目文件夹"


@dataclass(frozen=True)
class ServedProject:
    """What this server serves (module docstring, "Project identity").

    `no_project` (10.1): there is no project; `root` is then a placeholder
    that names no folder, and every project endpoint refuses before it is
    used. `adding` (10.2): a folder chosen in the add dialog whose 9.4
    question is open -- served blocked, not registered; the answer
    (`resolve_payload`) registers it under `label`, the name chosen."""

    root: Path
    project_id: str | None = None
    needs_migration: bool = False
    read_only: bool = False
    blocked: dict[str, Any] | None = None
    label: str | None = None
    no_project: bool = False
    adding: bool = False


NO_PROJECT_ROOT = Path("/nonexistent/rce-no-project")


def no_project_served() -> ServedProject:
    """The no-project state (10.1, 10.5)."""
    blocked = {
        "situation": NO_PROJECT_SITUATION,
        "path": None,
        "message": NO_PROJECT_MESSAGE,
        "answers": [],
        "blocked": True,
    }
    return ServedProject(NO_PROJECT_ROOT, blocked=blocked, no_project=True)


def _missing(root: Path, project_id: str | None, label: str | None, reason: str) -> dict[str, Any]:
    return {
        "situation": "missing",
        "path": str(root),
        "project_id": project_id,
        "label": label or root.name,
        "reason": reason,
        "message": "找不到项目文件夹（可能已移动）",
        "answers": ["locate"],
        "blocked": True,
    }


def served_for(
    project_root: Path,
    *,
    expected_id: str | None = None,
    label: str | None = None,
    register: bool = False,
    probes: records_situation.Probes | None = None,
) -> ServedProject:
    """The identity check of a serving entry point, turned into what the
    server serves. `expected_id` is the registry entry's id when the
    folder is reached through the registry: a folder that is gone, or
    whose path now holds another project (or none), is the entry's
    "missing" state, and nothing about the folder found there is
    acted on. `register` records a successfully opened project as most
    recently served -- never a blocked one."""
    root = Path(project_root)
    if not root.is_dir():
        return ServedProject(root, expected_id, blocked=_missing(root, expected_id, label, "folder_gone"), label=label)
    if expected_id is not None:
        try:
            current = records_situation.classify(root, probes=probes)
        except NotADirectoryError:
            return ServedProject(root, expected_id, blocked=_missing(root, expected_id, label, "folder_gone"), label=label)
        if current.project_id != expected_id and current.situation is not records_situation.Situation.UNREADABLE_ID:
            return ServedProject(
                root, expected_id, blocked=_missing(root, expected_id, label, "path_holds_another_project"), label=label,
            )
    try:
        opened = project_identity.open_project(root, register=register, probes=probes)
    except project_identity.ProjectBlocked as exc:
        c = exc.classification
        return ServedProject(root, c.project_id, needs_migration=c.needs_migration, blocked=c.payload(), label=label)
    except project_identity.RenameRefused as exc:
        # 12.8: `.researchos/` could not become `.researchos/`; the old folder is as
        # it was, and the reason is the rename's own sentence.
        blocked = {"situation": "rename_blocked", "path": str(root), "message": exc.error.message_zh,
                   "detail": str(exc), "answers": [], "blocked": True}
        return ServedProject(root, expected_id, blocked=blocked, label=label)
    except project_identity.AnswerRefused as exc:
        blocked = {"situation": "changed", "path": str(root), "detail": str(exc), "answers": [], "blocked": True}
        return ServedProject(root, expected_id, blocked=blocked, label=label)
    return ServedProject(root, opened.project_id, needs_migration=opened.needs_migration, label=label)


def _served_graph_path(served: ServedProject) -> Path | None:
    """The index database this server reads for `served` -- `_served_db`'s
    choice without its checks (None when blocked: nothing is read)."""
    if served.blocked is not None:
        return None
    if served.project_id is None:
        return paths.graph_db_path(served.root)
    path = records_situation.index_db_path(served.project_id)
    if not path.exists() and served.needs_migration:
        return paths.graph_db_path(served.root)
    return path


def engine_payload(served: ServedProject) -> dict[str, Any]:
    """`GET /api/engine`: which project this engine serves, in the shape
    `researchos migrate` asks for before it retires an old index (DESIGN.md 9.12:
    only an engine serving THIS project, or the old index itself, blocks
    the retirement). Reads nothing but the served state: no index is
    opened, so it answers even for a blocked or missing project."""
    graph = _served_graph_path(served)
    return {
        "engine": names.ENGINE_NAME,
        "version": 5,
        "pid": os.getpid(),
        "project_root": None if served.no_project else str(served.root),
        "project_id": served.project_id,
        "graph_path": str(graph) if graph is not None else None,
        "researchos_home": str(paths.researchos_home()),
        "blocked": served.blocked is not None,
        "needs_migration": served.needs_migration,
    }


def _check_shutdown_target(body: dict[str, Any]) -> None:
    """`POST /api/shutdown`'s optional `{"pid": int}`: when present, stop
    only if it is THIS process.

    Why (adversarial review of the V4 work): Research OS.app sends the shutdown on
    quit whenever the engine it spawned is still running -- but that child
    may never have bound the port (e.g. blocked in a slow first-touch
    migration) while an engine the researcher started from a terminal
    did, and DESIGN.md 8.9 says such an engine is left alone. The shell
    now sends its child's pid, so the check happens here, atomically, in
    the only process that knows the answer; a mismatch is a 409 and this
    server keeps serving (the shell then stops its own child by signal).
    No `pid` (the page's own 停止服务 button) keeps the original contract."""
    if "pid" not in body:
        return
    pid = body["pid"]
    if not isinstance(pid, int) or isinstance(pid, bool):
        raise MissingParamError("request body 'pid' must be an integer when present")
    if pid != os.getpid():
        raise NotThisEngineError(
            f"this engine is process {os.getpid()}, not {pid} -- not shutting down"
        )


def _require_db(project_root: Path) -> Path:
    """Same message shape as `researchos.cli`/`researchos.mcp_server`'s own `_require_db`
    -- each subsystem owns its copy (existing convention in this codebase),
    since each raises its own module's error type -- and, in this copy
    only, the two checks that stand between a handler thread and a file it
    should not touch (module docstring, "Where the graph is"):

    1. the graph resolves through `researchos.paths.resolve_graph_db`, so a legacy
       in-project database is migrated out on the server's first touch;
    2. `is_dataless` is asked BEFORE the caller opens the file, because
       `sqlite3.connect` on a cloud-evicted file blocks for as long as the
       download takes and cannot be interrupted -- an honest 503 now beats
       a hung request thread later.
    """
    try:
        path = paths.resolve_graph_db(project_root)
    except paths.LegacyGraphDatalessError as exc:
        # The legacy in-project graph is the one most likely to be evicted
        # (it lives in the synced folder); the migration refused to open it
        # and asked for its download -- the same transient header state as
        # an evicted external graph, never a hung thread or a 500.
        raise GraphDownloadingError(str(exc)) from exc
    except paths.GraphMigrationError as exc:
        raise GraphMigrationError(str(exc)) from exc
    if not path.exists():
        raise ProjectNotInitializedError(
            f"no Research OS project at {project_root} (missing its graph at {path}); "
            f"run 'researchos init {project_root}' first"
        )
    if paths.is_dataless(path):
        raise GraphDownloadingError(
            f"the graph at {path} is not on this disk right now (macOS has evicted it to "
            f"iCloud); waiting for the download instead of opening it"
        )
    return path


def _served_db(served: ServedProject) -> Path:
    """The served project's index database, or the error that says why
    there is none to open: a blocked project (409, its situation), then
    `_require_db`'s own checks. A project with an id is opened at
    `~/.researchos/graphs/<id>/graph.db` by the id it was opened with -- never by
    re-reading the folder, which may have moved under the engine."""
    if served.blocked is not None:
        raise ProjectBlockedError(served.blocked)
    if served.project_id is None:
        return _require_db(served.root)
    path = records_situation.index_db_path(served.project_id)
    if not path.exists() and served.needs_migration:
        # 9.5: until the migration's tally balances, the old index keeps
        # serving (read-only for human records) -- `graph_db_path` names it.
        return _require_db(served.root)
    if not path.exists():
        raise ProjectNotInitializedError(
            f"the index of project {served.project_id} is missing ({path}); reopen the project to rebuild it"
        )
    if paths.is_dataless(path):
        raise GraphDownloadingError(
            f"the graph at {path} is not on this disk right now (macOS has evicted it to "
            f"iCloud); waiting for the download instead of opening it"
        )
    return path


# -- Path safety (shared by /api/file and /api/open) -------------------------


def _resolve_within_root(project_root: Path, rel_path: str) -> Path:
    """Resolve `rel_path` against `project_root` and reject it unless the
    *resolved* path (symlinks followed, `..` collapsed) is still under the
    resolved root -- see module docstring's "Path-traversal defense".

    Deliberately does not special-case an absolute `rel_path` or a literal
    `..` segment before resolving: `Path.resolve()` normalizes both the same
    way (an absolute `rel_path` simply replaces the join outright -- documented
    `pathlib` behaviour -- and a `..` walks up), so the one `relative_to`
    check below catches every shape of escape identically, including one a
    textual `..`-substring check would miss (a symlink whose target is
    outside the root but whose own written path contains no `..` at all).
    """
    root = project_root.resolve()
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise PathTraversalError(
            f"{rel_path!r} resolves outside the project root -- refusing"
        ) from None
    return candidate


# -- /api/summary -------------------------------------------------------------


def _attempts_config_echo(project_root: Path) -> dict[str, Any] | None:
    """The attempts config's shape, for the frontend's edit form (task V3
    phase 3): the form's fields come from the project's OWN `[columns]`
    header names, never hardcoded ones, so the app reads them here rather
    than inventing any. None when no usable config exists -- the form then
    explains that instead of guessing at columns (DESIGN.md section 0)."""
    try:
        config = attempts_ingest.load_config(project_root)
    except attempts_ingest.AttemptsConfigError:
        return None
    return {
        "file": config.file,
        "heading": config.heading,
        "columns": config.columns,
        "steps_dir": config.steps_dir,
    }


def summary_payload(conn: Connection, project_root: Path, served: ServedProject | None = None) -> dict[str, Any]:
    # Counted by SQL (18.4 item 7): no node or edge is loaded to be counted. The index's CHECK keeps a row to
    # the known types, so the zero-filled lists are exactly the old ones.
    node_found, edge_found = db.count_nodes_by_type(conn), db.count_edges_by_type(conn)
    node_counts = {t: node_found.get(t, 0) for t in sorted(db.NODE_TYPES)}
    edge_counts = {t: edge_found.get(t, 0) for t in sorted(db.EDGE_TYPES)}
    return {
        "project_root": str(project_root),
        # Section 8.10 rule 1: "`researchos status` and `/api/summary` report the
        # graph's actual location so nothing is hidden" -- it is no longer
        # inside the project, so a user who is not told cannot find it.
        "graph_path": str(paths.graph_db_path(project_root)),
        "nodes": node_counts,
        "edges": edge_counts,
        "pending": len(db.pending_edges(conn)),
        # 9.6: links whose old judgment waits for the researcher (待复核);
        # never counted in `pending` (待确认).
        "review": judgements.review_count(conn) + card_implementation.review_groups(conn)["count"],
        # 9.3: whether the judgment ledger can be trusted right now.
        "records": db.get_record_status(conn, judgements.RECORD_STATUS_NAME),
        "attempts_config": _attempts_config_echo(project_root),
        # 9.4 / 9.10: who this is, and whether human records can be saved.
        "project_id": served.project_id if served else None,
        "needs_migration": bool(served and served.needs_migration),
        "read_only": bool(served and served.read_only),
        # 9.5: pre-V5 indexes that may hold this folder's judgments, and an
        # unfinished migration ("legacy records waiting").
        "migration": migration.waiting_payload(project_root),
    }


# -- /api/attempts + /api/tree: shared attempt-node helpers -------------------

_NO_ATTEMPTS_HINT = (
    "No attempt nodes in the graph yet. Configure .researchos/attempts.toml and run "
    "'researchos attempts' to ingest your attempt timeline first."
)

_NUMBER_SPLIT_RE = re.compile(r"^(\d+)(.*)$")


def _split_number(number: str) -> tuple[str, str]:
    """`("14", "a")` for `"14a"`, `("14", "")` for `"14"`; a label with no
    leading digits (should not happen in practice -- same guard
    `researchos.ingest.attempts.attempt_sort_key` already applies) is returned as
    `(number, "")`, which always sorts to the top level below since an empty
    suffix never triggers the parent-nesting check."""
    m = _NUMBER_SPLIT_RE.match(number)
    if not m:
        return number, ""
    return m.group(1), m.group(2)


def _sorted_attempt_nodes(conn: Connection) -> list[dict[str, Any]]:
    nodes = db.get_nodes_by_type(conn, "attempt")
    return sorted(
        nodes,
        key=lambda n: (
            n["attrs"].get("source_file", ""),
            attempts_ingest.attempt_sort_key(n["attrs"].get("number", "")),
        ),
    )


def attempts_payload(conn: Connection, project_root: Path | None = None) -> dict[str, Any]:
    nodes = _sorted_attempt_nodes(conn)
    if not nodes:
        return {"attempts": [], "hint": _NO_ATTEMPTS_HINT}
    labels = attempts_ingest.verdict_labels(project_root)
    attempts = [
        {
            "id": node["id"],
            "attrs": node["attrs"],
            "verdict": node["human_fields"].get("verdict", ""),
            # 12.11: the verdict as a word and the researcher's own text.
            "verdict_status": attempts_ingest.verdict_status(node["human_fields"].get("verdict", ""), labels),
            "result": node["human_fields"].get("result", ""),
        }
        for node in nodes
    ]
    return {"attempts": attempts}


# -- /api/tree: the decision-tree view (task V1's core endpoint) -------------


def _occurrences(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Same defensive unwrap `researchos.lineage`/`researchos.cli` already use for a
    `reads`/`writes` edge's evidence -- see their own copies' docstrings for
    why a bare-dict fallback is kept (pre-T10 legacy row shape)."""
    occurrences = evidence.get("occurrences")
    if not isinstance(occurrences, list):
        occurrences = [evidence]
    return occurrences


def _target_path(conn: Connection, target_id: str) -> str:
    node = db.get_node(conn, target_id)
    if node is not None and node["title"]:
        return node["title"]
    return target_id.partition(":")[2] or target_id


def _lineage_role(conn: Connection, target_id: str) -> str:
    """Whether some script anywhere in the graph writes this target --
    `has_generator` if so, else `orphan_input` (the same "who wrote this
    input" question `researchos.lineage`'s own orphan block answers, reused here as
    a per-file tag rather than a separate report block)."""
    return "has_generator" if db.query_edges(conn, dst=target_id, type="writes") else "orphan_input"


def _connected_files(conn: Connection, script_id: str, edge_type: str) -> list[dict[str, Any]]:
    entries = []
    flags = judgements.link_flags(conn)
    for edge in db.query_edges(conn, src=script_id, type=edge_type):
        missing = any(occ.get("missing") for occ in _occurrences(edge["evidence"]))
        marks = flags.for_key(judgements.key_of(edge))
        entry = {
            "path": _target_path(conn, edge["dst"]),
            "role": _lineage_role(conn, edge["dst"]),
            "missing": missing,
            # 9.6 "Where it shows" (决策树): the link's status and whether
            # its judgment waits for review -- never shown as applied.
            "status": edge["status"],
            "review": marks["review"],
            "conflict": marks["conflict"],
            "judgement": marks["judgement"],
        }
        if marks["review"] or marks["conflict"]:
            # Which link, so the 「待复核」 tag opens the review list at it.
            entry["link"] = {k: edge[k] for k in ("src", "dst", "type", "extractor")}
        entries.append(entry)
    return sorted(entries, key=lambda e: e["path"])


def _script_layer(conn: Connection, script_rel_path: str) -> dict[str, Any]:
    script_id = f"script:{script_rel_path}"
    return {
        "path": script_rel_path,
        "reads": _connected_files(conn, script_id, "reads"),
        "writes": _connected_files(conn, script_id, "writes"),
    }


def _load_steps_dir(project_root: Path) -> str | None:
    """`.researchos/attempts.toml`'s `steps_dir`, or None if the config can't be
    loaded at all (e.g. it was removed after attempts were ingested).
    Attempt nodes only ever exist because `researchos attempts` ingested them
    through this same config, so this ordinarily succeeds whenever
    `/api/tree` has any attempts to show at all; the fallback degrades to
    "no scripts layer" rather than guessing at a steps_dir prefix (DESIGN.md
    section 0)."""
    try:
        return attempts_ingest.load_config(project_root).steps_dir
    except attempts_ingest.AttemptsConfigError:
        return None


def _attempt_entry(conn: Connection, node: dict[str, Any], steps_dir: str | None,
                   labels: dict[str, str] | None = None) -> dict[str, Any]:
    attrs = node["attrs"]
    scripts: list[dict[str, Any]] = []
    if steps_dir:
        for filename in attrs.get("step_files") or []:
            scripts.append(_script_layer(conn, f"{steps_dir}/{filename}"))
    return {
        "id": node["id"],
        "number": attrs.get("number", ""),
        "date": attrs.get("date", ""),
        "description": attrs.get("description", ""),
        "verdict": node["human_fields"].get("verdict", ""),
        "verdict_status": attempts_ingest.verdict_status(node["human_fields"].get("verdict", ""), labels),
        "scripts": scripts,
        "children": [],
    }


def tree_payload(conn: Connection, project_root: Path) -> dict[str, Any]:
    """The decision-tree JSON (task V1's own core endpoint): attempts (layer
    1) -> their step scripts (layer 2) -> each script's reads/writes data
    files (layer 3). Every layer comes from graph nodes/edges already
    written by `researchos attempts`/`researchos ingest` -- no re-parsing, no inference.

    Layer 1 nesting: a "14a"/"14b" split nests under a "14" node when one
    exists in the *same source file*; otherwise "14a"/"14b" are top-level
    siblings (task V1 spec: "父项不存在则作兄弟"). Scoped per source_file
    (an attempt id's own `attrs.source_file`), not globally by number alone,
    so two different attempt timelines' numbering can never cross-nest into
    each other's tree by coincidence.
    """
    nodes = _sorted_attempt_nodes(conn)
    if not nodes:
        return {"attempts": [], "hint": _NO_ATTEMPTS_HINT}
    steps_dir = _load_steps_dir(project_root)
    labels = attempts_ingest.verdict_labels(project_root)

    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for node in nodes:
        key = (node["attrs"].get("source_file", ""), node["attrs"].get("number", ""))
        entries[key] = _attempt_entry(conn, node, steps_dir, labels)

    top_level: list[dict[str, Any]] = []
    for node in nodes:
        source_file = node["attrs"].get("source_file", "")
        number = node["attrs"].get("number", "")
        leading, suffix = _split_number(number)
        parent_key = (source_file, leading)
        if suffix and leading != number and parent_key in entries:
            entries[parent_key]["children"].append(entries[(source_file, number)])
        else:
            top_level.append(entries[(source_file, number)])
    return {"attempts": top_level}


# -- /api/lineage -------------------------------------------------------------


def lineage_payload(conn: Connection, project_root: Path) -> dict[str, Any]:
    """Exactly `researchos.lineage.build_lineage_report`'s own structured result --
    the same function `researchos lineage --json` already calls (researchos.cli.cmd_lineage),
    reused rather than re-implemented here."""
    return lineage.build_lineage_report(conn, project_root)


# -- /api/file ----------------------------------------------------------------


def github_link_payload(project_root: Path, target: Path) -> dict[str, Any] | None:
    """DESIGN.md 11.2: the file panel's 「在 GitHub 上查看」 -- a link pinned
    to the newest commit GitHub has, or the reason there is none; None for
    a project not linked to GitHub. `target` is already confined to the
    project; the path git is asked about is its position under the root."""
    try:
        rel = target.relative_to(project_root.resolve()).as_posix()
        return github.link_for(project_root, rel)
    except (ValueError, OSError, github.GitHubError, git_ingest.GitIngestError):
        logger.info("no GitHub link for %s", target, exc_info=True)
        return None


def file_payload(project_root: Path, rel_path: str, lang: str = "zh") -> dict[str, Any]:
    target = _resolve_within_root(project_root, rel_path)
    if not target.is_file():
        if not target.exists():
            raise NotFoundError(f"no such file: {rel_path}")
        raise NotAFileError(f"not a regular file: {rel_path}")
    link = github_link_payload(project_root, target)
    if target.suffix.lower() == ".dta":
        return _dta_payload(target, rel_path, link, lang)
    try:  # one byte past the limit says "truncated"; the size is the open file's (18.4 item 5)
        raw, size = cloud.read_head(target, _FILE_SIZE_LIMIT + 1)
    except cloud.CloudOnlyError as exc:
        raise CloudOnlyApiError(exc, {"github": link}) from exc
    if b"\x00" in raw[:8192]:
        error = BinaryFileError(f"{rel_path} looks like a binary file; refusing to return its content")
        error.extra = {"github": link}
        raise error
    truncated = len(raw) > _FILE_SIZE_LIMIT
    content_bytes = raw[:_FILE_SIZE_LIMIT] if truncated else raw
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        if not truncated:
            error = BinaryFileError(f"{rel_path} is not valid UTF-8 text; refusing to return its content")
            error.extra = {"github": link}
            raise error from None
        # The 200KB cut can land mid multi-byte character; that is a
        # truncation artifact, not evidence the file is binary.
        content = content_bytes.decode("utf-8", errors="ignore")
    return {"path": rel_path, "content": content, "truncated": truncated, "size": size, "github": link}


def _dta_payload(target: Path, rel_path: str, link: Any, lang: str) -> dict[str, Any]:
    """A Stata .dta file in the panel (DESIGN.md 15.4): rows, the dataset
    label and the variables (name, label, type, format) from its header --
    the data is never read. A file only in the cloud is refused before any
    read, as a text file is; a file the reader refuses gets its sentence in
    the request's language. Names and labels are the file's text, for the
    page to set as text."""
    try:
        cloud.check_readable(target)
    except cloud.CloudOnlyError as exc:
        raise CloudOnlyApiError(exc, {"github": link}) from exc
    try:
        header = dta.read_header(target)
    except dta.DtaError as exc:
        raise DtaApiError(exc, lang, {"github": link}) from exc
    return {
        "path": rel_path, "kind": "dta", "size": target.stat().st_size, "github": link,
        "dta": {"release": header.release, "rows": header.rows, "label": header.label, "timestamp": header.timestamp,
                "variables": [{"name": v.name, "label": v.label, "type": v.type, "format": v.format}
                              for v in header.variables]},
    }


# -- GitHub (DESIGN.md 11.2) ----------------------------------------------------


def github_state_payload(served: ServedProject, lang: str = "zh") -> dict[str, Any]:
    """`GET /api/github`: `researchos.github.state` for the served project, or
    `{"linked": false}`. Reads local refs only; writes nothing. Its sentence
    is in the request's language (13.2)."""
    if served.no_project:
        return {"linked": False, "git": False}
    try:
        return github.state(served.root, lang)
    except git_ingest.GitIngestError:
        logger.info("no GitHub state for %s", served.root, exc_info=True)
        return {"linked": False, "git": False}


def github_fetch_payload(served: ServedProject, lang: str = "zh") -> dict[str, Any]:
    """`POST /api/github/fetch`: refused for a project whose question is
    open or that is opened read-only (a fetch writes into `.git`); the new
    state's sentence in the request's language."""
    if served.blocked is not None:
        raise ProjectBlockedError(served.blocked)
    if served.read_only:
        raise ReadOnlyError("the project is open read-only; nothing fetched")
    try:
        return github.fetch(served.root, served.project_id, lang)
    except github.GitHubError as exc:
        raise GitHubApiError(exc) from exc


def _push_served(served: ServedProject, *, writes: bool) -> Path:
    if served.blocked is not None:
        raise ProjectBlockedError(served.blocked)
    if writes and served.read_only:
        raise ReadOnlyError("the project is open read-only; nothing committed, created or pushed")
    return served.root


def github_push_plan_payload(served: ServedProject) -> dict[str, Any]:
    """`GET /api/github/push-plan` (11.3): reads only, no network."""
    root = _push_served(served, writes=False)
    try:
        return githubpush.plan(root)
    except githubpush.PushError as exc:
        raise GitHubPushApiError(exc) from exc


def github_commit_records_payload(served: ServedProject, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/github/commit-records` (11.3): the `.researchos/` files only."""
    root = _push_served(served, writes=True)
    try:
        return githubpush.commit_records(root, served.project_id)
    except githubpush.PushError as exc:
        raise GitHubPushApiError(exc) from exc


def _token_of(body: dict[str, Any]) -> str:
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise MissingParamError("'token' (from GET /api/github/push-plan) is required")
    return token


def github_create_repo_payload(served: ServedProject, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/github/create-repo` (11.3): `{name, token}`; private only."""
    root = _push_served(served, writes=True)
    token = _token_of(body)
    try:
        return githubpush.create_repo(root, body.get("name"), token, served.project_id)
    except githubpush.PushError as exc:
        raise GitHubPushApiError(exc) from exc


def github_push_payload(served: ServedProject, body: dict[str, Any], lang: str = "zh") -> dict[str, Any]:
    """`POST /api/github/push` (11.3): `{token, commit_records?}`; the
    returned state's sentence in `lang` (13.2)."""
    root = _push_served(served, writes=True)
    token = _token_of(body)
    tick = body.get("commit_records", False)
    if not isinstance(tick, bool):
        raise MissingParamError("'commit_records' must be true or false")
    try:
        return githubpush.push(root, token, served.project_id, commit_records=tick, lang=lang)
    except githubpush.PushError as exc:
        raise GitHubPushApiError(exc) from exc


# -- POST /api/open -----------------------------------------------------------


def _is_macos() -> bool:
    return sys.platform == "darwin"


def open_payload(project_root: Path, rel_path: str, reveal: bool) -> dict[str, Any]:
    """Reveal `rel_path` in Finder (`open -R`) or open it with its default
    application (`open`), macOS only. `subprocess.run` is always given a
    plain list of arguments -- never `shell=True` -- so there is no shell
    metacharacter to worry about regardless of what `rel_path` contains; the
    path itself is validated by `_resolve_within_root` before it ever reaches
    `subprocess.run`."""
    if not _is_macos():
        raise UnsupportedPlatformError(
            "'open' is only available on macOS; this server is running on a different platform"
        )
    target = _resolve_within_root(project_root, rel_path)
    if not target.exists():
        raise NotFoundError(f"no such path: {rel_path}")
    args = ["open", "-R", str(target)] if reveal else ["open", str(target)]
    subprocess.run(args, check=False)
    return {"opened": str(target), "reveal": reveal}


def open_external(target: str) -> None:
    """The 文献 view's two openers (11.4): `open` with one argument that
    `researchos.webapp.literature_api` built and checked -- a Zotero storage PDF
    or a `zotero://select/...` address. A plain argument list, never a
    shell. macOS only."""
    if not _is_macos():
        raise UnsupportedPlatformError(
            "'open' is only available on macOS; this server is running on a different platform"
        )
    subprocess.run(["open", target], check=False)


def literature_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """The 文献 view's POST endpoints (`researchos.webapp.literature_api`)."""
    try:
        if path == "/api/citations/lookup-setting":
            return literature_api.lookup_setting_payload(body)
        if path == "/api/zotero/open-attachment":
            return literature_api.open_attachment_payload(body, open_external)
        return literature_api.open_item_payload(body, open_external)
    except literature_api.LiteratureRefused as exc:
        raise LiteratureApiError(exc) from exc


# -- AI agents (DESIGN.md 17.1, 17.8) -----------------------------------------------------

#: The `settings.json` keys `POST /api/settings/agents` writes, so a refusal can put them back as they were.
_AGENT_SETTING_KEYS = (
    agent_profiles.AGENTS_SETTING, agent_profiles.COMMANDS_SETTING,
    agent_profiles.TIMEOUT_SETTING, agent_profiles.BUDGET_SETTING,
)
_agent_settings_lock = threading.Lock()


def agents_payload() -> dict[str, Any]:
    """`GET /api/agents`: the runners found (or why not), this Mac's agents
    (each with `available`: its runner was found, and `mcp`), whether the
    optional `mcp` package is installed, the suggested Claude Code models and
    the limits. Needs no project: it describes this Mac."""
    found = {runner: agent_profiles.find(runner) for runner in agent_labels.RUNNERS}
    mcp = agent_profiles.mcp_available()
    return {
        "runners": [found[runner].payload() for runner in agent_labels.RUNNERS],
        "agents": [dict(agent.payload(), available=found[agent.runner].found, mcp=mcp,
                        reasoning_effort=agent_profiles.reasoning_effort(agent))
                   for agent in agent_profiles.agents()],
        "mcp": mcp,
        "claude_models": list(agent_profiles.CLAUDE_MODEL_SUGGESTIONS),
        "harness_models": list(agent_profiles.HARNESS_MODEL_SUGGESTIONS),
        "reasoning_efforts": {
            runner: {model: list(agent_reasoning.known_levels(runner, model)) for model in models}
            for runner, models in (("claude-code", agent_profiles.CLAUDE_MODEL_SUGGESTIONS),
                                   ("deepseek-harness", agent_profiles.HARNESS_MODEL_SUGGESTIONS))
        },
        "timeout_minutes": agent_profiles.timeout_minutes(),
        "timeout_range": list(agent_profiles.TIMEOUT_RANGE),
        "max_budget_usd": agent_profiles.max_budget_usd(),
        "max_agents": agent_profiles.MAX_AGENTS,
    }


def agents_models_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    """`GET /api/agents/models?runner=`: the models to offer for a runner --
    Claude Code's versioned suggestions, or the program's catalog (the program
    must be found; the listing may take seconds)."""
    runner = (query.get("runner") or [""])[0]
    if runner not in agent_labels.RUNNERS:
        raise _AgentRefusal(
            "invalid", 400, f"runner must be one of {', '.join(agent_labels.RUNNERS)}", "没有指明是哪一种 AI 执行者",
        )
    if runner == "claude-code":
        return {"runner": runner, "models": list(agent_profiles.CLAUDE_MODEL_SUGGESTIONS)}
    if runner == "deepseek-harness":
        return {"runner": runner, "models": list(agent_profiles.HARNESS_MODEL_SUGGESTIONS)}
    found = agent_profiles.find(runner, check_version=False)
    if not found.found or found.path is None:
        raise agent_manager.AgentError("runner_missing")
    try:
        if runner == "codex":
            catalog = agent_profiles.codex_model_catalog(found.path)
            return {"runner": runner, "models": list(catalog), "reasoning_efforts": catalog}
        return {"runner": runner, "models": agent_profiles.opencode_models(found.path)}
    except RuntimeError as exc:
        words = "读取不到 Codex 的模型列表" if runner == "codex" else "读取不到 OpenCode 的模型列表"
        raise _AgentRefusal("models_failed", 502, str(exc), words) from exc


def agents_settings_payload(body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/settings/agents`: save the keys the body carries (`agents`,
    `commands`, `timeout_minutes`, `max_budget_usd`; the others stay), then
    answer like `GET /api/agents`. A value the setters refuse (ValueError)
    saves none of the keys: what was written before the refusal is put back."""
    with _agent_settings_lock:
        before = {k: v for k, v in settings_mod.read_settings().items() if k in _AGENT_SETTING_KEYS}
        wrote = False
        try:
            if "agents" in body:
                agent_profiles.set_agents(body["agents"])
                wrote = True
            if "commands" in body:
                agent_profiles.set_commands(body["commands"])
                wrote = True
            if "timeout_minutes" in body:
                agent_profiles.set_timeout_minutes(body["timeout_minutes"])
                wrote = True
            if "max_budget_usd" in body:
                agent_profiles.set_max_budget_usd(body["max_budget_usd"])
        except ValueError as exc:
            if wrote:
                data = {k: v for k, v in settings_mod.read_settings().items() if k not in _AGENT_SETTING_KEYS}
                settings_mod.write_json(settings_mod.settings_path(), {**data, **before})
            raise _AgentRefusal("invalid", 400, str(exc), "AI 执行者的设置内容不对，没有保存") from exc
    return agents_payload()


def language_setting_payload(body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/settings/language` {language}: saves this Mac's interface
    language (13.2), the one key `interface_language` of `settings.json`.
    The page reloads after it; the shell re-reads the file itself."""
    lang = body.get("language")
    if not isinstance(lang, str) or lang not in settings_mod.LANGUAGES:
        raise LanguageSettingError("bad_setting", 400, "request body 'language' must be \"zh\" or \"en\"")
    try:
        settings_mod.set_interface_language(lang)
    except OSError as exc:
        raise LanguageSettingError("setting_failed", 500, f"{settings_mod.settings_path()}: {exc}") from exc
    return {"ok": True, "language": lang}


# -- 「生成草稿」 for a new attempt (DESIGN.md 16) -----------------------------


def _origin(url: str) -> tuple[str, str, int | None] | None:
    try:
        parts = urllib.parse.urlsplit(url.strip())
        scheme = parts.scheme.lower()
        return scheme, parts.hostname or "", parts.port or {"http": 80, "https": 443}.get(scheme)
    except ValueError:
        return None


def _llm_client() -> llm_backend.LlmBackend:
    """The client for a draft: this Mac's saved setting, else RCE_LLM_*,
    else the defaults; RCE_LLM_TIMEOUT when set, else
    `draft_mod.TIMEOUT_SECONDS` (the model may take minutes). RCE_LLM_API_KEY
    goes only to the address it was set beside (RCE_LLM_BASE_URL, else the
    default), never to a different one saved here (16.2). Without the
    judge's remote warning: the draft logs its own."""
    saved = settings_mod.llm_settings()
    timeout = None if os.environ.get("RCE_LLM_TIMEOUT") else draft_mod.TIMEOUT_SECONDS
    key_for = os.environ.get("RCE_LLM_BASE_URL") or llm_backend.DEFAULT_BASE_URL
    api_key = None if saved["base_url"] is None or _origin(saved["base_url"]) == _origin(key_for) else ""
    return llm_backend.LlmBackend(base_url=saved["base_url"], model=saved["model"], timeout=timeout,
                                  api_key=api_key, warn_remote=False)


def _llm_host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""


def llm_setting_payload() -> dict[str, Any]:
    """`GET /api/settings/llm`: the address and model 「生成草稿」 uses,
    each value saved here (or null), and where the address is. Reads the
    setting without building a client (whose remote warning is the
    judge's)."""
    saved = settings_mod.llm_settings()
    base_url = settings_mod.normalize_llm_base_url(
        saved["base_url"] or os.environ.get("RCE_LLM_BASE_URL") or llm_backend.DEFAULT_BASE_URL)
    model = saved["model"] or os.environ.get("RCE_LLM_MODEL") or llm_backend.DEFAULT_MODEL
    host = _llm_host(base_url)
    return {"base_url": settings_mod.without_userinfo(base_url), "model": model,
            "saved": {"base_url": saved["base_url"], "model": saved["model"]},  # never with a password: refused
            "where": "this_mac" if llm_backend.on_this_mac(host) else "elsewhere", "host": host}


def llm_setting_set_payload(body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/settings/llm` {base_url?, model?}: this Mac's setting;
    only the keys sent change, an empty value returns that one to its
    default."""
    given = {key: body[key] for key in ("base_url", "model") if key in body}
    if not all(isinstance(v, str) for v in given.values()):
        raise DraftError("bad_setting", 400, "request body 'base_url' and 'model' must be strings")
    try:
        settings_mod.set_llm_settings(**given)
    except ValueError as exc:
        raise DraftError("bad_setting", 400, str(exc)) from exc
    except OSError as exc:
        raise DraftError("setting_failed", 500, f"{settings_mod.settings_path()}: {exc}") from exc
    return llm_setting_payload()


def _draft_file(project_root: Path, rel: str, typed: str) -> dict[str, Any]:
    """One chosen file read as `GET /api/file` reads it; any refusal is a
    DraftError naming the file as the researcher typed it."""
    try:
        read = file_payload(project_root, rel)
    except (NotFoundError, NotAFileError) as exc:
        raise DraftError("file_missing", exc.status, str(exc), typed) from exc
    except (BinaryFileError, DtaApiError) as exc:
        raise DraftError("file_not_text", 400, str(exc), typed) from exc
    except CloudOnlyApiError as exc:
        raise DraftError("file_cloud_only", exc.status, str(exc), typed) from exc
    except PathTraversalError as exc:
        raise DraftError("file_outside", exc.status, str(exc), typed) from exc
    except OSError as exc:  # no permission to read it, a name too long, a read error
        raise DraftError("file_unreadable", 400, f"{typed}: {exc}", typed) from exc
    if "content" not in read:  # a .dta: its header, not text
        raise DraftError("file_not_text", 400, f"{typed} is not a text file", typed)
    return read


def attempt_draft_payload(project_root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/attempts/draft` {files: [project-relative path], language?:
    "zh"|"en"}: each path resolved inside the project and deduplicated as
    its root-relative POSIX path, each file read as `GET /api/file` reads it
    (one refusal refuses the whole draft), then `draft_mod.draft_attempt`.
    Reads only and holds no lock while the model runs; the draft goes back
    to the form and nowhere else."""
    files = body.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and f for f in files):
        raise DraftError("no_files", 400, "request body 'files' must be a non-empty list of paths")
    root = project_root.resolve()
    chosen: dict[Any, tuple[str, str]] = {}  # the file -> (its normalized path, the path as typed)
    for typed in files:
        try:
            target = _resolve_within_root(project_root, typed)
        except PathTraversalError as exc:
            raise DraftError("file_outside", exc.status, str(exc), typed) from exc
        except (OSError, ValueError) as exc:  # a NUL in the path
            raise DraftError("file_missing", 400, f"{typed!r} is not a usable path: {exc}", typed) from exc
        rel = target.relative_to(root).as_posix()
        try:  # one file under two spellings (APFS ignores case), or hard-linked, is one file
            st = target.stat()
            key: Any = (st.st_dev, st.st_ino)
        except (OSError, ValueError):
            key = rel  # not there: refused below as draft_file_missing
        chosen.setdefault(key, (rel, typed))
    if len(chosen) > draft_mod.MAX_FILES:
        raise DraftError("too_many_files", 400, f"at most {draft_mod.MAX_FILES} files per draft, not {len(chosen)}")
    read = [_draft_file(project_root, rel, typed) for rel, typed in chosen.values()]
    language = body.get("language") if body.get("language") in ("zh", "en") else None
    try:
        client = _llm_client()
    except ValueError as exc:  # the constructor reads RCE_LLM_TIMEOUT as seconds
        raise DraftError("model_failed", 502, f"RCE_LLM_TIMEOUT is not a number of seconds: {exc}") from exc
    host = _llm_host(client.base_url)
    if not llm_backend.on_this_mac(host):  # 16.2: never silent (Section 7)
        logger.warning("draft: sending the contents of %d chosen file(s) to %s, which is not this Mac (model %s)",
                       len(read), host or settings_mod.without_userinfo(client.base_url), client.model)
    try:
        return draft_mod.draft_attempt(draft_mod.prepare_files(read), client, language)
    except llm_backend.LlmContextError as exc:
        raise DraftError("too_long", 413, settings_mod.without_userinfo(str(exc))) from exc
    except llm_backend.LlmError as exc:
        raise DraftError("model_failed", 502, settings_mod.without_userinfo(str(exc))) from exc


# -- /api/projects + POST /api/projects/switch (task V3 phase 1) --------------


def entry_state(entry: dict[str, Any]) -> dict[str, Any]:
    """One registry entry, checked fresh: `available` (the folder is
    there), `initialized` (its index exists), and `missing` -- DESIGN.md
    9.4's 「找不到项目文件夹（可能已移动）」: the folder is gone, or (for an
    entry with an id) the folder at that path carries another id or none.
    An identity file that cannot be read is not "missing": opening it
    shows its own situation."""
    root = Path(entry["path"])
    available = project_registry.is_available(root)
    project_id = entry.get("id")
    if project_id is None:
        return {"available": available, "initialized": project_registry.is_initialized(root), "missing": not available}
    missing = not available
    if available:
        got = records_situation.read_identity(root)
        if got.state is records_situation.IdentityState.ABSENT or (
            got.state is records_situation.IdentityState.PRESENT and got.identity and got.identity.id != project_id
        ):
            missing = True
    initialized = records_situation.index_db_path(project_id).exists()
    return {"available": available and not missing, "initialized": initialized, "missing": missing}


def projects_payload(current: Path | ServedProject) -> dict[str, Any]:
    """The registry (`researchos.webapp.registry.load()`, most-recently-served
    first), each entry's state checked fresh per request (`entry_state`)
    -- a project can be `researchos init`ed, moved, or its disk unmounted between
    two calls -- plus which project this server is currently serving and,
    when it is blocked, why. The current root is reported even when it is
    not (or no longer) a registry member: it is a fact about this server,
    not about the registry.

    `initialized` false means "registered but has no index yet"; `missing`
    (with `available` false) means the folder is gone or no longer this
    project, which the switcher shows as 「找不到项目文件夹（可能已移动）」
    with 「选择新位置…」 (`POST /api/projects/locate`) or 「移除失效项目」."""
    served = current if isinstance(current, ServedProject) else ServedProject(Path(current))
    projects = [
        {"id": entry.get("id"), "path": entry["path"], "label": entry["label"], **entry_state(entry)}
        for entry in project_registry.load()
    ]
    return {
        "projects": projects,
        "current": None if served.no_project else str(served.root),
        "current_id": served.project_id,
        # The served folder's display name when it is not (yet) in the list:
        # the one chosen in the add dialog while its question is open.
        "current_label": None if served.no_project else served.label,
        "no_project": served.no_project,
        "blocked": served.blocked,
        "read_only": served.read_only,
        "needs_migration": served.needs_migration,
    }


def switch_project_payload(requested: str, requested_id: str | None = None) -> tuple[ServedProject, dict[str, Any]]:
    """Validate a switch request and return `(served, response_payload)`;
    the caller (the handler) is the one that actually repoints the server,
    via `RceHTTPServer.set_served` -- this function owns the validation,
    the identity check and the registry recency bump, never the server
    state.

    The requested string is compared *string-equal* against registry
    entries' stored `"path"` values (and, when the page sends one, the
    entry's `"id"`) -- it is never resolved, joined, or otherwise
    interpreted as a filesystem path, so there is nothing here for a
    crafted value to traverse or normalize its way past (module docstring,
    "Switch-target defense"). Not a member: 403 (`UnknownProjectError`).

    Then the identity check (9.4) runs on the entry's folder before
    anything is written: a folder that is gone or now holds another
    project switches to that entry's "missing" state; a folder in a
    situation to be answered switches to its blocked state -- in both
    cases the server serves the question, and nothing (registry included)
    is written. A pre-V5 or never-initialized entry keeps its V3 rules: a
    member that is not an initialized project is a 400
    (`ProjectNotInitializedError`), and a legacy in-project graph is moved
    out here, on the server's first touch. Only a switch that opened the
    project bumps it to most-recently-served."""
    entry = next(
        (e for e in project_registry.load()
         if e["path"] == requested and (requested_id is None or e.get("id") == requested_id)),
        None,
    )
    if entry is None:
        raise UnknownProjectError(
            f"{requested!r} is not a registered project -- only paths already in the "
            f"registry (~/.researchos/{project_registry.REGISTRY_FILENAME}, written by "
            f"'researchos serve <path>') can be switched to"
        )
    new_root = Path(entry["path"])
    if entry.get("id") is None and new_root.is_dir():
        c = records_situation.classify(new_root)
        if c.situation is records_situation.Situation.NOT_A_PROJECT:
            raise ProjectNotInitializedError(
                f"registered project {entry['path']!r} is not initialized (missing its graph at "
                f"{paths.legacy_index_db_path(new_root)}); run 'researchos init {entry['path']}' first"
            )
        if c.situation is records_situation.Situation.LEGACY:
            try:
                # The server's first touch of a pre-V5 project (section 8.10
                # rule 1): its in-project graph moves out here, not on the
                # first read, so a switch followed by a read finds it.
                paths.migrate_legacy_graph(new_root)
            except paths.LegacyGraphDatalessError:
                pass  # its first read answers 「图谱文件正在从云端下载…」 and retries
            except paths.GraphMigrationError as exc:
                raise GraphMigrationError(str(exc)) from exc
    served = served_for(new_root, expected_id=entry.get("id"), label=entry["label"], register=True)
    return served, {
        "current": entry["path"],
        "label": entry["label"],
        "project_id": served.project_id,
        "blocked": served.blocked,
    }


def locate_payload(body: dict[str, Any]) -> tuple[ServedProject, dict[str, Any]]:
    """「选择新位置…」 (DESIGN.md 9.4): re-attach the registry entry `id` to
    the folder the researcher chose. The folder goes through the same
    identity check as any open, and is adopted ONLY if it carries that id
    -- Research OS never searches for a folder by itself, and never attaches one
    that is not this project. The chosen path must be absolute and an
    existing folder; only its identity file is read before that check."""
    project_id = body.get("id")
    chosen = body.get("path")
    if not isinstance(project_id, str) or not isinstance(chosen, str) or not chosen:
        raise MissingParamError("request body must carry string 'id' and 'path' keys")
    entry = project_registry.find(project_id) if records_lock.PROJECT_ID_RE.match(project_id) else None
    if entry is None:
        raise UnknownProjectError(f"{project_id!r} is not a registered project")
    candidate = Path(chosen)
    if not candidate.is_absolute():
        raise MissingParamError("'path' must be an absolute folder path")
    root = candidate.resolve()
    if not root.is_dir():
        raise NotFoundError(f"{chosen!r} is not an existing folder")
    found = records_situation.classify(root)
    if found.project_id != project_id:
        err = NotThisProjectError(
            f"{root} does not carry the project {project_id} (found: {found.situation.value}"
            f"{' ' + found.project_id if found.project_id else ''}); nothing was attached"
        )
        err.extra = {"found": found.payload()}
        raise err
    served = served_for(root, expected_id=project_id, label=entry["label"], register=True)
    # The entry as the move left it: a chosen display name stays (10.4).
    label = (project_registry.find(project_id) or entry)["label"]
    return served, {"current": str(root), "label": label, "project_id": project_id, "blocked": served.blocked}


_ANSWERS = {
    "fork": project_identity.fork,
    "claim": project_identity.claim,
    "other": project_identity.other,
    "adopt": project_identity.adopt,
    "restore": project_identity.restore,
}
RESOLVE_ANSWERS = ("fork", "claim", "other", "readonly", "adopt", "restore")


def resolve_payload(served: ServedProject, body: dict[str, Any]) -> tuple[ServedProject, dict[str, Any]]:
    """The researcher's answer to the served project's question (9.4,
    9.12): `fork` (「作为独立分支继续」), `claim` (「这里才是原项目」),
    `other` (「这是另一个项目」), `readonly` (「原位置暂时不可用，先只读打开」,
    for a home that cannot be checked: the index is read, nothing is
    adopted and nothing is written); for a lost identity file also
    `restore` (「从备份恢复项目身份文件」, offered only when a snapshot
    exists) and `adopt` (「沿用这些记录，建立新身份」). Only an answer the
    situation's `answers` lists is taken. Each answer re-checks the folder
    under the project lock and writes nothing if the question no longer
    stands. After `restore` the folder may ask a question of its own (a
    copy): `blocked` in the reply."""
    answer = body.get("answer")
    if answer not in RESOLVE_ANSWERS:
        raise MissingParamError(f"request body 'answer' must be one of {', '.join(RESOLVE_ANSWERS)}")
    blocked = served.blocked
    if blocked is None or answer not in blocked.get("answers", ()):
        raise AnswerRefusedError(
            f"this project has no open question that '{answer}' answers"
        )
    if answer == "readonly":
        if not served.project_id:
            raise AnswerRefusedError("there is no index to open read-only")
        return (
            ServedProject(served.root, served.project_id, read_only=True, label=served.label),
            {"ok": True, "answer": answer, "project_id": served.project_id, "read_only": True},
        )
    try:
        result = _ANSWERS[answer](served.root)
    except (project_identity.AnswerRefused, project_identity.ProjectBlocked) as exc:
        raise AnswerRefusedError(str(exc)) from exc
    except records_situation.WriteRefused as exc:
        raise AnswerRefusedError(str(exc)) from exc
    new_served = served_for(served.root, register=True, label=served.label)
    if served.adding and new_served.blocked is not None:
        # The answer led to a second question (a restored identity that is
        # a copy, say): still the add dialog's folder, so its answer, too,
        # registers it under the name chosen there.
        new_served = dataclasses.replace(new_served, adding=True)
    if served.adding and served.label and new_served.blocked is None and new_served.project_id is not None:
        # 10.2: a folder added from the app, whose question this answered,
        # is registered under the display name chosen in the add dialog.
        project_registry.register(Path(paths._canonical_path(served.root)), new_served.project_id, label=served.label)
    return new_served, {
        "ok": True,
        "answer": answer,
        "project_id": result.identity.id,
        "previous_id": result.previous_id,
        "moved_aside": list(result.moved_aside),
        "git_tracked_identity": result.git_tracked_identity,
        "build_error": result.build_error,
        "restored_from": result.restored_from,
        "blocked": new_served.blocked,
    }


def reopen_payload(served: ServedProject) -> tuple[ServedProject, dict[str, Any]]:
    """`POST /api/project/reopen` (no body): run the identity check again on
    the folder this server serves, as if it were opened anew -- 「重新打开」
    after 「项目已移动或已在别处认领」, after repairing an unreadable identity
    file, or to leave a read-only open. Takes no path from the page: the
    folder is the served one, and the id it is expected to carry is the one
    it was opened with (a folder that is gone, or now holds another project,
    becomes the 「找不到项目文件夹」 state with 「选择新位置…」). Nothing is
    written unless the folder opens normally (then, as any open, it may
    adopt a move and bump the registry)."""
    root = served.root
    new = served_for(root, expected_id=served.project_id, label=served.label, register=True)
    return new, {
        "current": str(root), "label": served.label or root.name,
        "project_id": new.project_id, "blocked": new.blocked, "read_only": new.read_only,
    }


def remove_project_payload(requested: str) -> dict[str, Any]:
    """Drop one entry from the registry (DESIGN.md section 8.10 rule 3), so
    the researcher never has to hand-edit `~/.researchos/projects.json` when a
    project directory is gone.

    Matched by string equality against a `registry.load()` entry, exactly
    as `switch_project_payload` matches and for exactly the same reason
    (module docstring, "Switch-target defense"): the client-supplied string
    is never resolved, joined, or treated as a filesystem path, so a
    crafted value has nothing to traverse -- it either names an entry the
    user already registered, or it names nothing (403).

    Deliberately NOT gated on the entry being dead: `available` is checked
    per request and can flip between the page rendering a button and the
    user clicking it (a disk remounts, a directory is restored), and
    refusing a removal because the project came back is a worse failure
    than removing a bookmark the user asked to remove. This deletes
    nothing on disk -- no graph, no project, no file -- so the blast
    radius of being wrong is one re-registration by `researchos serve <path>`.

    Removing the currently-served project is allowed too (DESIGN.md
    10.4): the handler then opens the next registered project, or the
    no-project state (`next_served_after_removal`)."""
    remove_project_entry(requested)
    return {"removed": requested}


def remove_project_entry(requested: str) -> dict[str, Any]:
    """`registry.remove_entry` (string equality, see above): the entry
    removed, or `UnknownProjectError` (403) when none matched."""
    entry = project_registry.remove_entry(requested)
    if entry is None:
        raise UnknownProjectError(
            f"{requested!r} is not a registered project -- only paths already in the "
            f"registry (~/{names.HOME_DIRNAME}/{project_registry.REGISTRY_FILENAME}) "
            f"can be removed from it"
        )
    return entry


def is_served_entry(served: ServedProject, entry: dict[str, Any]) -> bool:
    """Whether the registry `entry` is the project this server serves: the
    same id, or (an id-less entry) the same stored path."""
    if served.no_project:
        return False
    if entry.get("id") is not None:
        return entry["id"] == served.project_id
    return entry["path"] == str(served.root)


def next_served_after_removal() -> ServedProject:
    """10.4: removing the open project opens the next one -- the most
    recently served entry left, through the same identity check a bare
    `researchos serve` makes -- or the no-project state."""
    entries = project_registry.load()
    if not entries:
        return no_project_served()
    entry = entries[0]
    return served_for(Path(entry["path"]), expected_id=entry.get("id"), label=entry["label"], register=True)


# -- adding a project from the app (DESIGN.md Section 10, task V6) -------------

BAD_PATH_MESSAGE = "请输入完整的文件夹路径（以 / 或 ~ 开头）"


def _chosen_path(body: dict[str, Any]) -> str:
    """The folder the page named: a string, `~` expanded, absolute. It is
    resolved (symlinks followed) by the inspection itself, and the resolved
    folder is the one checked and written into (10.6)."""
    raw = body.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise AddProjectError("bad_path", BAD_PATH_MESSAGE, "request body must carry a string 'path'", status=400)
    expanded = os.path.expanduser(raw.strip())
    if not os.path.isabs(expanded):
        raise AddProjectError("bad_path", BAD_PATH_MESSAGE, f"{raw!r} is not an absolute path", status=400)
    return expanded


def inspect_payload(body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/projects/inspect {path}` (10.2 step 2): what the folder is,
    and what adding it would do. Writes nothing; past the deadline the
    answer is `waiting_permission` and the page asks again."""
    return addproject.inspect(_chosen_path(body), deadline=addproject.DEFAULT_DEADLINE_S).payload()


def add_payload(body: dict[str, Any]) -> tuple[ServedProject | None, dict[str, Any], bool]:
    """`POST /api/projects/add {path, label, inspected}` (10.2 step 3).
    Returns `(served, payload, scan)`: what the server now serves (None:
    unchanged), the reply, and whether the first full scan starts. A
    project that was registered is opened exactly as `/api/projects/switch`
    opens it; one whose question is open is served blocked, unregistered
    (the answer, through `/api/project/resolve`, registers it under the
    chosen name); one already in the list changes nothing."""
    path = _chosen_path(body)
    label = body.get("label")
    if label is not None and not isinstance(label, str):
        raise AddProjectError("bad_label", project_registry.LabelError._MESSAGES["empty"], "'label' must be a string", status=400)
    inspected = body.get("inspected")
    if not isinstance(inspected, str):
        raise AddProjectError("bad_request", "请求不完整，请重新查看这个文件夹", "request body must carry the string 'inspected'", status=400)
    try:
        added = addproject.add(path, label=label, inspected=inspected, deadline=addproject.DEFAULT_DEADLINE_S)
    except addproject.AddRefused as exc:
        extra = {"inspection": exc.inspection.payload()} if exc.inspection is not None else {}
        raise AddProjectError(exc.code, exc.message, exc.detail, extra=extra) from exc
    except project_registry.LabelError as exc:
        raise AddProjectError("bad_label", exc.message_zh, str(exc), status=400) from exc
    payload: dict[str, Any] = {
        "ok": True,
        "kind": added.kind,
        "root": str(added.root),
        "label": added.label,
        "project_id": added.project_id,
        "entry": added.entry,
        "registered": added.registered,
        "blocked": None,
        "current": None,
        "scanning": False,
        "inspection": added.inspection.payload(),
    }
    if added.kind == addproject.ALREADY_REGISTERED:
        return None, payload, False
    if added.classification is not None and added.entry is None:
        c = added.classification
        served = ServedProject(
            added.root, c.project_id, needs_migration=c.needs_migration, blocked=c.payload(),
            label=added.label, adding=True,
        )
        payload.update(blocked=served.blocked, current=str(added.root))
        return served, payload, False
    entry = added.entry
    if entry is None:  # pragma: no cover -- a registry that could not be written
        raise AddProjectError("not_registered", "无法写入项目列表", f"{added.root} could not be registered")
    served, switched = switch_project_payload(entry["path"], entry.get("id"))
    payload.update(current=switched["current"], blocked=switched["blocked"], project_id=switched["project_id"])
    scan = added.needs_scan and served.blocked is None
    payload["scanning"] = scan
    return served, payload, scan


def rename_payload(body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/projects/rename {id, label}` (10.4): the registry label
    only, never the folder. A pre-V5 entry (no id) is named by `path`,
    string-equal to its stored path, as `/remove` names it."""
    key = body.get("id") if isinstance(body.get("id"), str) else body.get("path")
    if not isinstance(key, str) or not key:
        raise AddProjectError("bad_request", "请求不完整", "request body must carry a string 'id' (or 'path')", status=400)
    try:
        entry = project_registry.rename(key, body.get("label"))
    except project_registry.LabelError as exc:
        raise AddProjectError("bad_label", exc.message_zh, str(exc), status=400) from exc
    if entry is None:
        raise AddProjectError(
            "unknown_project", "这个项目不在列表里", f"{key!r} is not a registered project", status=403,
        )
    return {"ok": True, "entry": entry}


# -- POST /api/attempts/preview + /api/attempts/write (task V3 phase 3) -------


def _parse_attempt_edit_body(body: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Both attempt-edit endpoints share one body contract -- `{"op":
    "append"|"update", "number": str, "fields": {key: str}}` -- so both go
    through this single shape check (semantic validation -- duplicate
    numbers, newline content, unknown field keys -- belongs to
    `researchos.webapp.mapedit`, which owns those rules)."""
    op = body.get("op")
    if op not in ("append", "update"):
        raise MissingParamError("request body 'op' must be \"append\" or \"update\"")
    number = body.get("number")
    if not isinstance(number, str):
        raise MissingParamError("request body must carry a string 'number' key")
    fields = body.get("fields", {})
    if not isinstance(fields, dict):
        raise MissingParamError("request body 'fields' must be a JSON object")
    if body.get("reason") is not None and not isinstance(body["reason"], str):
        raise MissingParamError("request body 'reason' must be a string")
    return op, number, fields


def attempts_preview_payload(project_root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """A pure dry run: `researchos.webapp.mapedit.preview_edit`'s
    `{file, diff, old_row, new_row}` -- nothing on disk moves -- and
    `reason_required` (14.3): the write turns the route abandoned, so the PI
    or a single researcher must say why (the body's `reason`); with one,
    the rows shown carry it in the result as the write will. Such a write
    while the team file cannot be read is refused here as it would be
    (`attempt_role_unknown`). An update also says, per cell it would change,
    what the file holds now and what it will hold (`cells`, 18.3: a row's
    研究判断 or 结果 saves at once, and 撤销 sends the old cell back)."""
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    op, number, fields = _parse_attempt_edit_body(body)
    try:
        out = mapedit.preview_edit(project_root, op, number, fields)
        turn = proposals_mod.direct_abandonment(project_root, op, number, fields)
        said = " ".join(str(body.get("reason") or "").split())
        if turn is not None and said:
            out = mapedit.preview_edit(project_root, op, number, proposals_mod.with_reason(fields, turn, said))
    except (mapedit.MapEditError, attempts_ingest.AttemptsConfigError, proposals_mod.ProposalRefused) as exc:
        raise _attempt_edit_error(exc, project_root) from exc
    out = {**out, "reason_required": turn is not None}
    if op == "update" and isinstance(out.get("old_row"), str):
        # 18.3: an existing row's 研究判断 or 结果 saves at once, so the page cannot show the old row first.
        # For each cell the write would change: the cell as the file holds it now (`was`, decoration and
        # <br> kept, so sending it back writes it as it was -- what 撤销 sends), its text as the parser
        # reads it (`was_text`, what the page showed while the file was unchanged) and as it will read
        # after the write (`now_text`). The page saves at once only while `was_text` is what it showed.
        try:
            cells = mapedit.row_cells(project_root, number) or {}
        except (mapedit.MapEditError, attempts_ingest.AttemptsConfigError) as exc:
            raise _attempt_edit_error(exc, project_root) from exc
        out["cells"] = {key: {"was": cells[key], "was_text": attempts_ingest._clean_cell(cells[key]),
                              "now_text": attempts_ingest._clean_cell(value)}
                        for key, value in fields.items() if key in cells and isinstance(value, str)}
    if isinstance(fields.get("verdict"), str):
        # U4: whether the verdict abandons the route (☠, or a marker of
        # attempts.toml's abandoned_verdicts) -- a member's 提议改判 then
        # asks for the result and the reason before anything is written.
        out = {**out, "abandons": proposals_mod.is_abandonment(fields["verdict"],
                                                                proposals_mod.abandon_markers(project_root)),
               "verdict_status": attempts_ingest.verdict_status(fields["verdict"],
                                                                attempts_ingest.verdict_labels(project_root))}
    return out


def attempts_write_payload(
    project_root: Path, body: dict[str, Any], watcher: project_watcher.ProjectWatcher
) -> dict[str, Any]:
    """The actual write: `researchos.webapp.mapedit.apply_edit` under the
    watcher's own ingest lock (so a UI write and a watcher poll never
    ingest concurrently -- module docstring's "Write-path defense"), then
    `record_external_change` re-baselines the watcher and bumps the
    generation, recording (or clearing) the write's own contained
    post-write ingest failure. `ingest_error` is passed through to the
    response so the UI can say "file written and backed up, but the rescan
    failed" rather than hiding a half-landed state behind a bare ok.

    14.3: a write that turns a route abandoned carries `reason`, else it is
    refused (`attempt_reason_required`, nothing written); the reason goes
    into the result and the basis is frozen in decisions.toml
    (`researchos.proposals.write_directly`) -- `decision` names that entry."""
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    op, number, fields = _parse_attempt_edit_body(body)
    try:
        result = proposals_mod.write_directly(
            project_root, op, number, fields, body.get("reason"), ingest_lock=watcher.ingest_lock,
        )
    except (mapedit.MapEditError, attempts_ingest.AttemptsConfigError, proposals_mod.ProposalRefused) as exc:
        raise _attempt_edit_error(exc, project_root) from exc
    generation = watcher.record_external_change(result["ingest_error"])
    return {
        "ok": True,
        "file": result["file"],
        "backup": result["backup"],
        "generation": generation,
        "ingest_error": result["ingest_error"],
        "decision": (result["decision"] or {}).get("id"),
    }


# -- The node canvas (DESIGN.md section 8, task V4 phase 1b) -----------------


def _canvas_project(project_root: Path) -> str:
    """The identity a canvas payload carries and a layout POST must echo:
    the served root, compared as a string and never used as a path."""
    return str(Path(project_root).resolve())


def canvas_payload(conn: Connection, project_root: Path, scope: str | None) -> dict[str, Any]:
    """`researchos.webapp.canvas.build_canvas`, with an unknown scope as a 404,
    plus `project` -- which project this picture is of (see
    `canvas_layout_payload`)."""
    try:
        payload = canvas.build_canvas(conn, project_root, scope)
    except canvas.UnknownScopeError as exc:
        raise NotFoundError(str(exc)) from exc
    payload["project"] = _canvas_project(project_root)
    return payload


def desk_payload(conn: Connection, served: ServedProject, lang: str) -> dict[str, Any]:
    """`GET /api/desk`: `researchos.desk.payload` plus `layout`, the route
    map's arrangement (DESIGN.md 14.1: the view `routes` of canvas.json,
    per person) -- `positions`, `viewport`, the record's `state`, the
    `project` a layout POST must echo, and `writable`: false for a record
    that cannot be read, a project frozen until it is migrated or opened
    read-only, so the page sends nothing that would only be refused."""
    out = desk_mod.payload(conn, served.root, lang=lang)
    layout = canvas.routes_layout(served.root)
    layout.update(project=_canvas_project(served.root),
                  writable=layout["writable"] and not (served.needs_migration or served.read_only))
    out["layout"] = layout
    return out


class ProjectChangedError(ApiError):
    """A layout POST made in a page still showing another project: the
    server was switched (from another window, or Research OS.app) after that page
    fetched its canvas. 409 -- well-formed, but it describes a picture of a
    project this server no longer serves, and two projects can share card
    ids (a copied replication package), so writing it would overwrite the
    other project's arrangement. The page drops such a write."""

    status = 409
    state = "project_changed"


def canvas_layout_payload(conn: Connection, project_root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Merge a layout body into its view of `canvas.json` (sections 8.4,
    8.6). Opened through `_open_conn`, so `_require_db` runs first: the
    file lives in the graph's own directory, which exists only for an
    initialized project, and the graph is what bounds the write -- a scope
    the project does not have is a 404 (as `GET /api/canvas`); a position
    for a card that view does not show is skipped. The body must carry the
    `project` its page's canvas payload named; any other value is a 409
    (`ProjectChangedError`) and nothing is written."""
    body = dict(body)
    project = body.pop("project", None)
    if not isinstance(project, str) or not project:
        raise MissingParamError("layout body must carry 'project', as GET /api/canvas returned it")
    if project != _canvas_project(project_root):
        raise ProjectChangedError("layout made for another project; this server now serves a different one")
    try:
        view = canvas.save_layout(conn, project_root, body)
    except canvas.UnknownScopeError as exc:
        raise NotFoundError(str(exc)) from exc
    except canvas.LayoutShapeError as exc:
        raise MissingParamError(str(exc)) from exc
    except canvas.LayoutRecordError as exc:
        error = LayoutUnreadableError(str(exc))
        error.extra = {"layout": exc.record.payload(), "message": exc.record.message}
        raise error from exc
    return {"ok": True, "scope": body["scope"], "positions": len(view["positions"]), "viewport": view["viewport"]}


def _string_fields(body: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    values = [body.get(k) for k in keys]
    if not all(isinstance(v, str) and v for v in values):
        quoted = ", ".join(f"'{k}'" for k in keys)
        raise MissingParamError(f"request body must carry non-empty string keys {quoted}")
    return values  # type: ignore[return-value]


def _mapping_write_error(exc: mappings_ingest.MappingsWriteError) -> ApiError:
    if exc.code == "duplicate":
        return MappingExistsError(str(exc))
    if exc.code == "not_found":
        return NotFoundError(str(exc))
    return MappingEditError(str(exc), exc.code)


def _reingest_mappings(project_root: Path) -> None:
    """Exactly `researchos mappings`' call, on its own connection, with the same
    never-conjure-a-graph refusal as `mapedit._reingest_attempts` (the
    graph could vanish between `_require_db` and here)."""
    db_path = paths.graph_db_path(project_root)
    if not db_path.exists():
        raise RuntimeError(
            f"no Research OS project at {project_root} (missing its graph at {db_path}); "
            "the mappings file was written and backed up, but the graph could not be re-ingested"
        )
    conn = db.connect(db_path)
    try:
        report = mappings_ingest.ingest_mappings(conn, project_root)
        logger.info("canvas write re-ingested mappings for %s: %s", project_root, report.counts)
        judgements.apply_after_scan(conn, project_root)  # 9.1: the end of a scan
    finally:
        conn.close()


def _mapping_link(project_root: Path, entry: dict[str, Any]) -> dict[str, Any] | None:
    """The link the just-written entry became, read back from the graph --
    so the response is what the canvas will draw, not an echo of the
    request. None if the re-ingest did not land it."""
    def end(value: str, attempt: bool) -> tuple[str | None, str]:
        # 12.7, 14.2: an attempt end is written as the attempt's node id.
        if attempt:
            return "attempt", value.removeprefix("attempt:")
        return dataflow_ingest.node_type_for_path(value), value

    kind = entry["type"]
    from_type, from_path = end(entry["from"], kind == mappings_ingest.DERIVES_FROM)
    to_type, to_path = end(entry["to"], kind in (mappings_ingest.ATTEMPT_LINK, mappings_ingest.DERIVES_FROM))
    if from_type is None or to_type is None:
        return None
    mapping = mappings_ingest.Mapping(
        from_path=from_path, to_path=to_path, type=kind,
        from_type=from_type, to_type=to_type,
    )
    db_path = paths.graph_db_path(project_root)
    if not db_path.exists():
        return None
    conn = db.connect(db_path)
    try:
        for edge in db.query_edges(conn, src=mapping.src_id, dst=mapping.dst_id, type=mapping.type):
            if edge["extractor"] == mappings_ingest.EXTRACTOR:
                return canvas.link_entry(edge)
    finally:
        conn.close()
    return None


def _write_mapping(
    project_root: Path,
    watcher: project_watcher.ProjectWatcher,
    write: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], str | None, int]:
    """The shared write discipline of both mapping endpoints (module
    docstring, "Canvas write defense"): graph checked first; the file write
    and the mappings re-ingest under the watcher's ingest lock; the
    re-ingest's failure contained and reported, never hiding that the file
    was written; only the mappings file re-baselined; generation bumped.

    When the re-ingest FAILED, the mappings file is deliberately not
    absorbed into the watcher's baseline and the failure is not written
    into the watcher's `last_error` (adversarial review of the V4 work):

    - absorbing it made the watcher believe the change had been ingested,
      so it never retried -- the file held the entry, the graph did not,
      and redrawing the link was refused with 「这条映射已存在」. Left
      un-absorbed, the change stays a visible difference and the next poll
      re-runs the mappings ingest by itself;
    - one cause deserves one message (section 8.10 rule 2's principle):
      the response's `ingest_error` already becomes the canvas's chip, so
      also setting `last_error` put the same failure on screen twice (the
      header's 「重扫失败…」 as well). If the watcher's own retry fails too,
      THAT is reported through `last_error`, as any poll failure is.

    Nothing in the graph changed on that path, so no generation bump
    either; the response carries the current one."""
    _require_db(project_root)
    with watcher.ingest_lock:
        try:
            result = write()
        except mappings_ingest.MappingsWriteError as exc:
            raise _mapping_write_error(exc) from exc
        ingest_error: str | None = None
        try:
            _reingest_mappings(project_root)
        except Exception as exc:  # noqa: BLE001 -- containment, same as apply_edit's
            logger.exception("post-write mappings re-ingest of %s failed -- file written", project_root)
            ingest_error = str(exc)
    if ingest_error is not None:
        return result, ingest_error, int(watcher.status_payload()["generation"])  # type: ignore[call-overload]
    generation = watcher.record_external_change(
        None, absorb={str(mappings_ingest.mappings_path(project_root))},
    )
    return result, ingest_error, generation


def mappings_add_payload(
    project_root: Path, body: dict[str, Any], watcher: project_watcher.ProjectWatcher
) -> dict[str, Any]:
    """Append one human mapping (section 8.5) and return the link it became.
    `note` is optional; an empty or whitespace-only note is no note (the
    popover's 备注 field left blank)."""
    from_path, to_path, edge_type = _string_fields(body, ("from", "to", "type"))
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise MissingParamError("request body 'note' must be a string when present")
    if note is not None and not note.strip():
        note = None
    result, ingest_error, generation = _write_mapping(
        project_root, watcher,
        lambda: mappings_ingest.add_mapping(project_root, from_path, to_path, edge_type, note=note),
    )
    return {
        "ok": True,
        "file": result["file"],
        "backup": result["backup"],
        "entry": result["entry"],
        "link": _mapping_link(project_root, result["entry"]),
        "generation": generation,
        "ingest_error": ingest_error,
    }


def mappings_delete_payload(
    project_root: Path, body: dict[str, Any], watcher: project_watcher.ProjectWatcher
) -> dict[str, Any]:
    """Remove one human mapping (「删除标注」); the re-ingest then drops its
    edge -- the only way a mapping edge ever leaves the graph (8.5)."""
    from_path, to_path, edge_type = _string_fields(body, ("from", "to", "type"))
    result, ingest_error, generation = _write_mapping(
        project_root, watcher,
        lambda: mappings_ingest.delete_mapping(project_root, from_path, to_path, edge_type),
    )
    return {
        "ok": True,
        "file": result["file"],
        "backup": result["backup"],
        "removed": result["removed"],
        "generation": generation,
        "ingest_error": ingest_error,
    }


def _judgement_error(exc: judgements.JudgementRefused) -> ApiError:
    """A refused human record write as the page's error (nothing written)."""
    if exc.code == "untrusted":
        decision = exc.decision
        records = None
        if decision is not None:
            records = {
                "state": decision.verdict.value, "reason": decision.reason, "message": decision.message,
                "detail": decision.detail, "line": decision.line,
                "missing": [judgements._summary(m) for m in decision.missing],
            }
        shrunk = decision is not None and decision.verdict.value == "shrunk"
        return RecordRefusedError(
            str(exc), state="record_shrunk" if shrunk else None,
            extra={"records": records, "message": exc.message_zh},
        )
    if exc.code == "mapping":
        return HumanLinkError(str(exc))
    if exc.code == "no_such_link":
        return NotFoundError(str(exc))
    if exc.code in ("not_rejected", "nothing_to_undo"):
        return EdgeStatusError(str(exc))
    if exc.code == "no_index":
        return ProjectNotInitializedError(str(exc))
    if exc.code == "no_question":
        return NoQuestionError(str(exc))
    if exc.code in ("question_changed", "would_lose"):
        decision = exc.decision
        return RecordRefusedError(
            str(exc), state="record_" + exc.code,
            extra={
                "records": None if decision is None else {
                    "state": decision.verdict.value, "reason": decision.reason, "message": decision.message,
                    "missing": [judgements._summary(m) for m in decision.missing],
                },
                "message": RECORD_ANSWER_MESSAGES[exc.code],
            },
        )
    return MissingParamError(str(exc))


# 8.8: what the page says when an answer to 9.3's question is refused.
RECORD_ANSWER_MESSAGES = {
    "question_changed": "记录文件在提问之后又变了，请重新查看问题再回答",
    "would_lose": "记录文件当前为空或无法使用，以文件为准会丢掉仅存的连线确认副本；请先恢复文件，或选择「把缺少的补回文件」",
}


def _judge(served: ServedProject, key: tuple[str, str, str, str], verdict: str, **kwargs: Any) -> judgements.Judged:
    try:
        return judgements.judge(
            served.root, key, verdict, via="canvas", expected_id=served.project_id,
            timeout=WRITE_LOCK_TIMEOUT_S, **kwargs,
        )
    except judgements.JudgementRefused as exc:
        raise _judgement_error(exc) from exc


def _link_after(conn: Connection, key: tuple[str, str, str, str]) -> dict[str, Any] | None:
    src, dst, edge_type, extractor = key
    for edge in db.query_edges(conn, src=src, dst=dst, type=edge_type):
        if edge["extractor"] == extractor:
            return judgements.link_flags(conn).annotate(canvas.link_entry(edge))
    return None


def _judged_payload(conn: Connection, key: tuple[str, str, str, str], judged: judgements.Judged) -> dict[str, Any]:
    state = judged.state
    return {
        "ok": True,
        "link": _link_after(conn, key),
        "entry": judgements._summary(judged.entry.data),
        "status": judged.status,
        "judgement": None if state is None else {
            "outcome": state["outcome"], "reason": state.get("reason"),
            "label": judgements.REASON_LABELS.get(state.get("reason") or ""),
        },
    }


def edge_status_payload(conn: Connection, served: ServedProject, body: dict[str, Any], action: str) -> dict[str, Any]:
    """`action` "reject" (标记为错误提取) or "restore" (its undo) on one
    canvas link -- since V5 a ledger entry, `rejected` or an `undone`
    naming the reject, through the one human write path
    (`researchos.records.judgements.judge`); the index follows from the record.

    Order of refusals: a `mapping` extractor first (whether or not such an
    edge exists, the answer is the same -- delete the entry), then a link
    that does not exist or is not one the canvas draws (404: the app can
    only change statuses it shows), then for restore a link whose last act
    is not a reject (409). The undo of a reject puts back whatever stood
    before it (8.12, kept by the ledger's undo): a link the researcher had
    *confirmed* is confirmed again. Reject is idempotent: a second click on
    a reject that stands and is applied writes nothing."""
    src, dst, edge_type, extractor = _string_fields(body, ("src", "dst", "type", "extractor"))
    key = (src, dst, edge_type, extractor)
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise MissingParamError("request body 'note' must be a string when present")
    if extractor == mappings_ingest.EXTRACTOR:
        raise HumanLinkError(
            "this link is a human mapping from .researchos/mappings.toml -- delete the mapping "
            "instead of marking it as a wrong extraction"
        )
    matches = [e for e in db.query_edges(conn, src=src, dst=dst, type=edge_type) if e["extractor"] == extractor]
    if not matches or not canvas.is_canvas_edge(conn, matches[0]):
        raise NotFoundError(f"no canvas link {src} --{edge_type}--> {dst} (extractor {extractor!r})")
    if action == "reject":
        # The card's optional 备注 goes with the reject (9.3 `note`).
        judged = _judge(served, key, "rejected", unless_standing=True, note=note)
    else:
        judged = _judge(served, key, "undone", undo_only="rejected")
    return _judged_payload(conn, key, judged)


def judgement_payload(conn: Connection, served: ServedProject, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/judgements`: confirm / reject / withdraw / undo one machine
    link from the app (9.3, 9.6's three review actions included), through
    the one write path. A link the index does not hold is accepted only
    when the ledger already has a history for it (settling a review of a
    link no scan produces any more)."""
    src, dst, edge_type, extractor, verdict = _string_fields(body, ("src", "dst", "type", "extractor", "verdict"))
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise MissingParamError("request body 'note' must be a string when present")
    key = (src, dst, edge_type, extractor)
    judged = _judge(served, key, verdict, note=note)
    return _judged_payload(conn, key, judged)


def _held_cloud_notes(project_root: Path | None, held: list[dict[str, Any]]) -> None:
    """DESIGN.md 11.1: a 「来源文件暂不可读」 item whose files are only in a
    synced folder's cloud, its client not running, says why (`cloud`)."""
    if project_root is None:
        return
    for item in held:
        detail = item.get("detail") or {}
        sources = detail.get("sources") if isinstance(detail.get("sources"), list) else None
        if not sources:
            src = str(item.get("src", ""))
            sources = [src.split(":", 1)[1]] if ":" in src else []
        files = {scan_mod.file_of(str(s)).split("#", 1)[0] for s in sources}
        notes = cloud.notes(project_root, files)
        if notes:
            item["cloud"] = notes[0]["message"]


def _reference_titles(conn: Connection, items: list[dict[str, Any]]) -> None:
    """A reference end (11.4: `ref:doi:` / `ref:zotero:` / `ref:entry:`)
    of a listed link -- or of one of its candidates -- gets its node's
    title (`src_title` / `dst_title`), so the page names the paper, never
    the engine's id. An end the index no longer holds gets none."""
    titles: dict[str, str | None] = {}
    for item in items:
        for link in (item, *(item.get("candidates") or [])):
            for end in ("src", "dst"):
                node_id = str(link.get(end) or "")
                if not node_id.startswith("ref:"):
                    continue
                if node_id not in titles:
                    node = db.get_node(conn, node_id)
                    titles[node_id] = node["title"] if node else None
                if titles[node_id]:
                    link[f"{end}_title"] = titles[node_id]


def review_payload(conn: Connection, project_root: Path | None = None, lang: str = "zh") -> dict[str, Any]:
    """`GET /api/review`: 9.6's list of links, plus the variable cards whose
    implementation moved under a confirmed version (9.11 stage (b)) --
    one list, one count, its reasons in the request's language (the desk
    opens it, 14.6). A pre-V5 index (a project frozen until it is
    migrated, 9.12) keeps no review: its judgments move into the record when
    it is migrated, so it is read as an empty list, as `researchos review` reads
    it -- never a crash on a column the old index does not have."""
    if not db._has_machine_status(conn):
        return {"review": [], "count": 0, "source_unreadable": [], "not_in_index": [], "ledger": None,
                "cards": card_implementation.review_groups(None, lang), "pre_v5": True}
    payload = judgements.review_items(conn, lang)
    _reference_titles(conn, [*payload["review"], *payload["source_unreadable"], *payload["not_in_index"]])
    _held_cloud_notes(project_root, payload["source_unreadable"])
    card_items = card_implementation.review_groups(conn, lang)
    payload["cards"] = card_items
    payload["count"] = payload["count"] + card_items["count"]
    return payload


def _variables_conn(served: ServedProject) -> Connection | None:
    """The index for the 「变量」 reads, or None (a blocked project, an index
    missing or in the cloud): the cards are then read from their files."""
    if served.blocked is not None:
        return None
    try:
        return db.connect(_served_db(served))
    except ApiError:
        return None


def _variables_served(served: ServedProject) -> None:
    """The 「变量」 view answers for the project the page serves, or says why
    it cannot -- never an empty list or a wrong reason for the researcher's
    cards (9.4): a project with a question to answer first (a copy, a folder
    that is gone: 「找不到项目文件夹（可能已移动）」 with 「选择新位置…」) is
    409 `project_blocked` with its situation, as on every other view; a
    folder that moved or was claimed under the running engine is 409
    `project_moved` (「请重新打开」)."""
    if served.blocked is not None:
        raise ProjectBlockedError(served.blocked)
    if served.project_id is None:
        return
    if not served.root.is_dir():
        raise ProjectMovedApiError(f"{served.root} is not there any more; reopen the project")
    got = records_situation.read_identity(served.root)
    if got.state is records_situation.IdentityState.PRESENT and got.identity and got.identity.id != served.project_id:
        raise ProjectMovedApiError(f"{served.root} now holds project {got.identity.id}, not {served.project_id}; reopen it")


def variables_get(served: ServedProject, path: str, query: dict[str, list[str]], lang: str = "zh") -> dict[str, Any]:
    """The 「变量」 view's GET endpoints (`researchos.webapp.variables_api`), for a
    project that is served as itself (`_variables_served`)."""
    arg = lambda name: (query.get(name) or [None])[0]  # noqa: E731
    _variables_served(served)
    conn = _variables_conn(served)
    try:
        if path == "/api/variables":
            return variables_api.list_payload(conn, served.root)
        if path == "/api/variables/card":
            return variables_api.card_payload(conn, served.root, arg("id"), lang)
        if path == "/api/variables/code":
            return variables_api.code_payload(served.root, arg("id"), arg("entry"))
        if path == "/api/variables/frozen":
            return variables_api.frozen_payload(served.root, arg("id"), arg("entry"))
        raise NotFoundError(f"no such endpoint: {path}")
    except variables_api.CardActionRefused as exc:
        raise CardRefusedApiError(exc) from exc
    finally:
        if conn is not None:
            conn.close()


def records_payload(served: ServedProject) -> dict[str, Any]:
    """`GET /api/records`: the inventory of 9.2 -- what `researchos records`
    prints -- for the app's 「记录」 panel: each kind of human record, where
    it lives (project-relative when inside the project), how many, the
    newest snapshot and any problem, each row with its machine-readable
    `code` and `facts` (the page words them in Chinese; `problems` stays
    the engine's English for 「详情」). Reads only. The index is consulted
    for the shrink check when it can be opened; a blocked project, or one
    whose index is missing or in the cloud, is listed from its files
    alone."""
    conn: Connection | None = None
    if served.blocked is None:
        try:
            conn = db.connect(_served_db(served))
        except ApiError:
            conn = None
    try:
        rows = inventory.inventory(conn, served.root)
    finally:
        if conn is not None:
            conn.close()
    return {"project_root": str(served.root), "rows": [r.payload() for r in rows]}


def history_payload(served: ServedProject, query_args: dict[str, list[str]]) -> dict[str, Any]:
    values = [(query_args.get(k) or [None])[0] for k in ("src", "dst", "type", "extractor")]
    if not all(isinstance(v, str) and v for v in values):
        raise MissingParamError("history needs query parameters src, dst, type and extractor")
    entries = judgements.history(served.root, values)
    return {"entries": entries, "readable": entries is not None}


def records_answer_payload(served: ServedProject, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/records/answer` (module docstring)."""
    record, answer = _string_fields(body, ("file", "answer"))
    if record == "judgements":
        # The ids of the missing entries the page showed (`records.missing[].id`):
        # an answer is tied to the question the researcher saw (9.3), never
        # applied to whatever is missing at click time.
        shown = body.get("missing")
        if not isinstance(shown, list) or not all(isinstance(i, str) and i for i in shown):
            raise MissingParamError(
                "request body must carry 'missing': the ids of the missing entries the question showed"
            )
        try:
            answered = judgements.answer_shrunk(
                served.root, answer, expected_missing=shown, expected_id=served.project_id,
                timeout=WRITE_LOCK_TIMEOUT_S,
            )
        except judgements.JudgementRefused as exc:
            raise _judgement_error(exc) from exc
        return {
            "ok": True, "file": "judgements", "answer": answer,
            "missing": len(answered.missing), "appended": len(answered.appended),
        }
    if record == "notes":
        shown = body.get("missing")
        if not isinstance(shown, list) or not all(isinstance(i, str) and i for i in shown):
            raise MissingParamError(
                "request body must carry 'missing': the ids of the missing entries the question showed"
            )
        try:
            return node_api.notes_answer_payload(served.root, served.project_id, answer, shown,
                                                 timeout=WRITE_LOCK_TIMEOUT_S)
        except node_api.NodeApiRefused as exc:
            raise NodeRefusedApiError(exc) from exc
    if record == "canvas" and answer == "set_aside":
        moved = canvas.set_aside_layout(served.root)
        if moved is None:
            raise NoQuestionError("the arrangement record is readable or absent; nothing to set aside")
        return {"ok": True, "file": "canvas", "answer": answer, "moved_to": moved.relative_to(served.root).as_posix()}
    raise MissingParamError("'file' must be 'judgements' (answer 'file' or 'restore') or 'canvas' (answer 'set_aside')")


# -- The single-page app (task V2) -------------------------------------------

_APP_HTML_PATH = Path(__file__).parent / "app.html"


def migration_payload(served: ServedProject) -> dict[str, Any]:
    """`GET /api/migration` (9.5, for the app's question): what waits for
    this folder -- each old index with what it holds and how many of its
    judged links' endpoints a scan of THIS folder produces -- and an
    unfinished migration. Reads only (the match is a scan into a scratch
    index that is removed)."""
    if served.blocked is not None:
        raise ProjectBlockedError(served.blocked)
    waiting = migration.waiting_payload(served.root)
    previews: list[dict[str, Any]] = []
    if waiting["waiting"] and waiting["migrating_from"] is None:
        try:
            previews = [p.payload() for p in migration.preview(served.root)]
        except migration.MigrationRefused as exc:
            raise MigrationRefusedError(str(exc)) from exc
    return {**waiting, "previews": previews}


MIGRATION_ANSWERS = ("migrate", "not_mine")


def migration_run_payload(served: ServedProject, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/migration/run {answer: migrate|not_mine}` -- the explicit
    act of 9.5. `migrate` is the researcher's yes (resuming an unfinished
    migration needs none); `not_mine` is 「这不是这个项目的」, which leaves
    the old indexes untouched and remembers the refusal for this folder.
    The retire step's checks (9.12) are the CLI's: another engine serving
    this project or this old index, or another process with it open,
    stops it; this engine itself never does (`migration.engine_holding`
    recognises its own pid)."""
    if served.blocked is not None:
        raise ProjectBlockedError(served.blocked)
    answer = body.get("answer")
    if answer not in MIGRATION_ANSWERS:
        raise MissingParamError(f"request body 'answer' must be one of {', '.join(MIGRATION_ANSWERS)}")
    try:
        if answer == "not_mine":
            return {"declined": migration.decline(served.root)}
        results = migration.migrate(served.root, yes=True)
    except migration.MigrationRefused as exc:
        raise MigrationRefusedError(str(exc)) from exc
    except project_identity.ProjectBlocked as exc:
        raise ProjectBlockedError(exc.classification.payload()) from exc
    except records_lock.ProjectLockTimeout as exc:
        raise ProjectBusyError(str(exc)) from exc
    return {"results": [r.payload() for r in results], "ok": all(r.ok for r in results)}


def _app_html() -> str:
    """`src/researchos/webapp/app.html` verbatim -- read fresh on every request
    rather than cached in memory, since this is a local single-user tool
    (no request volume to speak of) and a fresh read means a developer
    editing the file sees the change on the next reload with no server
    restart. Packaged as `package-data` (pyproject.toml) so it ships
    alongside `server.py` in an installed wheel, not just this editable
    checkout."""
    return _APP_HTML_PATH.read_text(encoding="utf-8")


_CANVAS_JS_PATH = Path(__file__).parent / "canvas.js"
_DESK_JS_PATH = Path(__file__).parent / "desk.js"
_NODE_JS_PATH = Path(__file__).parent / "node.js"
_TEAM_JS_PATH = Path(__file__).parent / "team.js"
_ROUTEMAP_JS_PATH = Path(__file__).parent / "routemap.js"
_I18N_JS_PATH = Path(__file__).parent / "i18n.js"
_WORKSPACE_JS_PATH = Path(__file__).parent / "workspace.js"
_WORKSPACE_CSS_PATH = Path(__file__).parent / "workspace.css"


def _canvas_js() -> str:
    """`src/researchos/webapp/canvas.js` verbatim, read fresh per request exactly
    like `_app_html` (and packaged alongside it as `package-data`). The
    page loads it with a plain same-origin `<script src="/canvas.js">`, so
    the app stays zero-build and zero-external-resource: two files served
    as written instead of one."""
    return _CANVAS_JS_PATH.read_text(encoding="utf-8")


def _desk_js() -> str:
    """`src/researchos/webapp/desk.js` verbatim (the sidebar and the desk,
    DESIGN.md 12.9), read fresh per request like `_canvas_js`."""
    return _DESK_JS_PATH.read_text(encoding="utf-8")


def _node_js() -> str:
    """`src/researchos/webapp/node.js` verbatim (the node page, 图表与论断 and
    数据与代码, DESIGN.md 12.6, 12.7, 12.9), read fresh per request."""
    return _NODE_JS_PATH.read_text(encoding="utf-8")


def _team_js() -> str:
    """`src/researchos/webapp/team.js` verbatim (研究路线, 提议 and 成员,
    DESIGN.md 12.3, 12.4, 12.9; V8 U4), read fresh per request."""
    return _TEAM_JS_PATH.read_text(encoding="utf-8")


def _routemap_js() -> str:
    """`src/researchos/webapp/routemap.js` verbatim (the desk's route map,
    DESIGN.md 14.1, 14.2; V10), read fresh per request."""
    return _ROUTEMAP_JS_PATH.read_text(encoding="utf-8")


def _i18n_js() -> str:
    """`src/researchos/webapp/i18n.js` (DESIGN.md 13.6), read fresh per
    request, under one line written now: the saved interface language
    (13.2), `"zh"`, `"en"` or `null` -- the page resolves its language
    from it synchronously, before it draws, with no request of its own."""
    saved = settings_mod.interface_language()
    return f"window.ROI18N_SAVED = {json.dumps(saved)};\n" + _I18N_JS_PATH.read_text(encoding="utf-8")


# -- HTTP plumbing -------------------------------------------------------------


# How long a request waits for another process's hold on the project lock
# before answering `project_busy` (9.7: writers take turns; a handler thread
# never waits forever).
WRITE_LOCK_TIMEOUT_S = 10.0


class RceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type,
        project_root: Path | None,
        watch_interval: float = project_watcher.DEFAULT_INTERVAL_SECONDS,
        served: ServedProject | None = None,
        session: str | None = None,
    ) -> None:
        # 18.1: the secret every write must carry (`RceRequestHandler._check_session`);
        # None asks for none -- the tests' servers. `serve` never passes None.
        self.session = session
        # Mutable since task V3 phase 1 (POST /api/projects/switch), and
        # only ever touched through the accessors below: each request runs
        # on its own thread (ThreadingHTTPServer), so a bare attribute would
        # let a switch interleave with another handler's read. Kept
        # name-mangled + locked rather than public so no future handler can
        # accidentally bypass the lock. Since V5 it is the whole
        # `ServedProject` (root, id, blocked state), computed by the
        # identity check when the caller did not pass one.
        if served is None:
            served = no_project_served() if project_root is None else served_for(Path(project_root))
        self.__served = served
        self.__served_lock = threading.Lock()
        # 10.2/10.3: the full scan running in the background, at most one
        # per project (`researchos.addproject.start_scan` holds the slot), and how
        # the last one ended -- per project root, reported by
        # `GET /api/generation` for the project being served.
        self.__scan_lock = threading.Lock()
        self.__scans: dict[str, dict[str, Any]] = {}
        self.__last_scans: dict[str, dict[str, Any]] = {}
        # Task V3 phase 2: the auto-refresh watcher. Created here (so
        # /api/generation always has status to report, and a switch always
        # has something to retarget) but its polling thread is only started
        # by serve() -- build_server alone spawns no background work, which
        # keeps every routing-only test thread-free. Its re-ingests run
        # under this server's write guard (V5: project lock + identity
        # re-check), and it does not poll a blocked or read-only project.
        self.watcher = project_watcher.ProjectWatcher(
            self.get_project_root, interval=watch_interval,
            write_guard=lambda: self.write_guard(human=False, timeout=None),
            active=self._watcher_active,
        )
        # 17.3: set BEFORE the socket is bound -- `TCPServer.__init__` calls
        # `server_close()` when the bind fails, which reads both; a busy port
        # must surface as the bind's own `OSError`, not an AttributeError.
        self._closing = False
        self.agents: agent_manager.AgentManager | None = None
        # 17.3: roots whose agent run ended while another project was served
        # (realpaths); the next switch to one of them scans it once.
        self.__pending_scans: set[str] = set()
        self.__pending_scans_lock = threading.Lock()
        super().__init__(server_address, handler_cls)
        # 17.3: the agent runs of the projects this engine serves. Created
        # after the socket is bound (the hook's guard names the engine's own
        # port); no thread starts until a run does. `server_close` stops them.
        self.agents = agent_manager.AgentManager(on_finish=self._agent_finished, engine_port=self.server_address[1])

    def get_served(self) -> ServedProject:
        with self.__served_lock:
            return self.__served

    def set_served(self, served: ServedProject) -> None:
        with self.__served_lock:
            self.__served = served

    def get_project_root(self) -> Path:
        return self.get_served().root

    def set_project_root(self, project_root: Path) -> None:
        """Serve `project_root` after running the identity check on it."""
        self.set_served(served_for(Path(project_root)))

    def _watcher_active(self) -> bool:
        """Not for a blocked or read-only project, and not for one frozen
        until it is migrated (9.12, acceptance 2026-10-05): a pre-V5 or
        still-migrating project is read from its old index, and no scan
        may land in it -- no re-ingest, and so no error chip either."""
        served = self.get_served()
        return (
            not served.no_project and served.blocked is None and not served.read_only and not served.needs_migration
        )

    # -- the full scan (DESIGN.md 10.2, 10.3) ----------------------------------

    def scan_status(self) -> dict[str, Any]:
        """`{scanning, last_scan}` for the served project, merged into
        `GET /api/generation` so the page shows 「正在扫描：<步骤>（n/m）」
        from the poll it already makes."""
        served = self.get_served()
        key = None if served.no_project else str(served.root)
        with self.__scan_lock:
            running = self.__scans.get(key) if key else None
            last = self.__last_scans.get(key) if key else None
            return {
                "scanning": dict(running) if running else None,
                "last_scan": dict(last) if last else None,
            }

    def start_scan(self, served: ServedProject) -> None:
        """Start the full scan of `served` in a background thread, or raise
        `AddProjectError` (a scan of it already running, a frozen pre-V5
        project, a folder that is no longer the project). Progress and the
        result land in `scan_status`; when it ends the watcher takes the
        folder as it was when the scan began as its baseline (an edit made
        during the scan is still seen) and the generation moves, so the
        views fill."""
        if served.no_project:
            raise ProjectBlockedError(served.blocked or {})
        if served.blocked is not None:
            raise ProjectBlockedError(served.blocked)
        if served.read_only:
            raise ReadOnlyError("this project was opened read-only; nothing is scanned")
        if served.needs_migration:
            raise AddProjectError(
                "frozen", addproject.SCAN_MESSAGES["frozen"],
                f"{served.root} is frozen until it is migrated (researchos migrate); nothing scanned",
            )
        try:
            ticket = addproject.start_scan(served.root, expected_id=served.project_id)
        except addproject.ScanRefused as exc:
            raise AddProjectError(exc.code, exc.message, exc.detail) from exc
        key = str(served.root)
        snapshot = project_watcher.take_snapshot(served.root)
        with self.__scan_lock:
            self.__scans[key] = {
                "step": None, "label": None, "n": 0, "m": len(addproject.SCAN_STEPS),
                "started": _now_iso(),
            }

        def progress(step: str, n: int, m: int) -> None:
            with self.__scan_lock:
                state = self.__scans.get(key)
                if state is not None:
                    state.update(step=step, label=addproject.STEP_LABELS.get(step, step), n=n, m=m)

        def run() -> None:
            result: dict[str, Any]
            try:
                report = ticket.run(progress=progress)
                result = {
                    "ok": report.ok, "unreadable_sources": report.unreadable_sources,
                    "error": report.error, "message": None if report.ok else "扫描没有完成",
                    "findings": report.findings, "cloud": report.cloud,
                }
            except addproject.ScanRefused as exc:
                result = {"ok": False, "unreadable_sources": [], "error": exc.code, "message": exc.message, "findings": None}
            except Exception as exc:  # noqa: BLE001 -- reported, never kills the engine
                logger.exception("the scan of %s failed", served.root)
                result = {"ok": False, "unreadable_sources": [], "error": str(exc), "message": "扫描没有完成", "findings": None}
            finally:
                ticket.release()
            result["finished"] = _now_iso()
            with self.__scan_lock:
                self.__scans.pop(key, None)
                self.__last_scans[key] = result
            self.watcher.scan_finished(served.root, snapshot)

        threading.Thread(target=run, name="researchos-scan", daemon=True).start()

    @contextlib.contextmanager
    def write_guard(self, *, human: bool, timeout: float | None = WRITE_LOCK_TIMEOUT_S) -> Iterator[None]:
        """Every write a request (or the watcher) makes: refused for a
        blocked or read-only project, then the project lock and the 9.4
        re-check (`researchos.records.situation.write_guard`), with each refusal
        turned into its `state` for the page. Covers refusals raised by
        the writers inside the block too (they take the same, re-entrant
        lock themselves)."""
        served = self.get_served()
        if served.blocked is not None:
            raise ProjectBlockedError(served.blocked)
        if served.read_only:
            raise ReadOnlyError("this project was opened read-only; nothing is written")
        try:
            with records_situation.write_guard(served.root, served.project_id, human=human, timeout=timeout):
                yield
        except records_situation.NeedsMigrationError as exc:
            raise NeedsMigrationApiError(str(exc)) from exc
        except records_situation.WriteRefused as exc:
            raise ProjectMovedApiError(str(exc)) from exc
        except records_lock.ProjectLockTimeout as exc:
            raise ProjectBusyError(f"another Research OS process is writing this project; try again ({exc})") from exc

    def _agent_finished(self, root: Path, run: dict[str, Any]) -> None:
        """A run ended (any way): when it was in the project being served,
        every open page re-fetches (the generation moves) and the project is
        rescanned, so the files the agent made become nodes (17.3). Called
        from the run's thread; nothing happens while the server is closing
        (the runs `server_close` interrupts are not worth a scan)."""
        if self._closing:
            return
        served = self.get_served()
        try:
            key = os.path.realpath(root)
            same = key == os.path.realpath(served.root)
        except (OSError, ValueError):
            return
        if served.no_project or served.blocked is not None or not same:
            # Not scannable now: remember the project, and scan it once when
            # the page is switched to it (a switch drops the watcher's
            # baseline, so the agent's files would never become nodes).
            with self.__pending_scans_lock:
                self.__pending_scans.add(key)
            return
        self.watcher.record_external_change(None)
        try:
            self.start_scan(served)
        except ApiError as exc:
            logger.info("no scan after the agent run %s: %s", run.get("id"), exc)

    def scan_pending_agent_project(self, served: ServedProject) -> bool:
        """The page was switched to `served`: when an agent run of it ended
        while another project was served, scan it now, once (17.3). A project
        that cannot be scanned right now (blocked, read-only, frozen) keeps its
        mark for the next switch. True when a scan was asked for."""
        if served.no_project or served.blocked is not None or served.read_only or served.needs_migration:
            return False
        try:
            key = os.path.realpath(served.root)
        except (OSError, ValueError):
            return False
        with self.__pending_scans_lock:
            if key not in self.__pending_scans:
                return False
            self.__pending_scans.discard(key)
        self.watcher.record_external_change(None)
        try:
            self.start_scan(served)
        except ApiError as exc:
            logger.info("no scan of %s after its agent run ended: %s", served.root, exc)
            return False
        return True

    def server_close(self) -> None:
        # 17.3: quitting stops every running agent (state `interrupted`)
        # before anything else is torn down -- their run threads still call
        # back into this server, and `_closing` keeps those callbacks from
        # starting scans (and `serve`'s SIGTERM handler from interrupting
        # this very shutdown). `agents` is None when the bind failed.
        # Idempotent, like the rest.
        self._closing = True
        if self.agents is not None:
            self.agents.shutdown()
        # The watcher thread must never outlive its server (it would keep
        # statting -- and on a change, re-ingesting -- a project nothing is
        # serving anymore). stop() is safe when the thread was never
        # started, and idempotent, so double server_close stays harmless.
        self.watcher.stop()
        super().server_close()


class RceRequestHandler(BaseHTTPRequestHandler):
    server_version = "ResearchOS/1"
    server: RceHTTPServer  # set by socketserver at construction time

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib's own name)
        # 18.1: the request line of `GET /boot?session=...` holds the secret; it is never logged
        logger.debug("%s - %s", self.address_string(), _without_secret(format % args))

    def _project_root(self) -> Path:
        """One locked read per call (RceHTTPServer.get_project_root) -- a
        handler takes its snapshot of the root and works with that; a
        concurrent switch affects the next request, never tears this one."""
        return self.server.get_project_root()

    def _served(self) -> ServedProject:
        return self.server.get_served()

    def _open_conn(self) -> Connection:
        return db.connect(_served_db(self._served()))

    def _require_unblocked(self) -> ServedProject:
        served = self._served()
        if served.blocked is not None:
            raise ProjectBlockedError(served.blocked)
        return served

    def _switch_to(self, served: ServedProject) -> None:
        self.server.set_served(served)
        # Re-target the auto-refresh watcher (task V3 phase 2): drops the
        # old root's baseline/error and bumps the generation, so every open
        # page's next poll re-fetches.
        self.server.watcher.retarget()
        # 17.3: an agent run of this project ended while it was not served
        self.server.scan_pending_agent_project(served)

    def _send_api_error(self, exc: ApiError) -> None:
        """`{"error": msg}` plus `"state"` when the error names one -- the
        two degraded-project conditions the page has its own product
        language for (see `ApiError.state`). Absent for every other error,
        so the page's generic error box stays the default."""
        body: dict[str, Any] = {"error": str(exc)}
        if exc.state is not None:
            body["state"] = exc.state
        if exc.extra:
            body.update(exc.extra)
        self._send_json(exc.status, body)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html: str) -> None:
        self._send_text(status, html, "text/html; charset=utf-8")

    def _send_text(self, status: int, text: str, content_type: str, *, no_store: bool = False) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if no_store:  # an answer that depends on a setting (/i18n.js): never reused after a switch
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_from_conn(self, fn: Callable[[Connection], Any]) -> None:
        conn = self._open_conn()
        try:
            payload = fn(conn)
        finally:
            conn.close()
        self._send_json(200, payload)

    def _official(self, payload: Any, *, cards: bool = False) -> Any:
        """12.12 "A member's own work": every object of a view's payload
        that names a node, a file or a hand-drawn link carries
        `officialness` (`researchos.officialness`) -- official, the
        member's own unsubmitted change, or in a pending proposal; nothing
        is dropped. Contained: a view is never refused over this. The
        labels in the page's language on every page (13.7: officialness
        states are shared words)."""
        try:
            seen = officialness.view(self._project_root())
            lang = self._lang()
            return officialness.annotate_cards(seen, payload, lang) if cards else officialness.annotate(seen, payload, lang)
        except Exception:  # noqa: BLE001
            logger.exception("officialness not added to a payload")
            return payload

    @staticmethod
    def _node_api(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except node_api.NodeApiRefused as exc:
            raise NodeRefusedApiError(exc) from exc

    def _workspace_api(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except (workspace.WorkspaceError, newproject.NewProjectError) as exc:
            raise WorkspaceApiError(exc, self._lang()) from exc

    def _workspace_target(self, body: dict[str, Any]) -> ServedProject:
        served = self._require_unblocked()
        if body.get("project") != str(served.root):
            error = ApiError("the open project changed; reopen this document before saving")
            error.status = 409
            error.state = "workspace_project_changed"
            error.extra = {"message": "项目已切换，请重新打开文档再保存" if self._lang() == "zh" else str(error)}
            raise error
        return served

    def _agent_api(self, fn: Callable[[], Any]) -> Any:
        """The agent endpoints' refusals (the run manager's, and the few of
        this module) as `agent_<code>` errors in the request's language."""
        try:
            return fn()
        except (agent_manager.AgentError, _AgentRefusal) as exc:
            raise AgentApiError(exc, self._lang()) from exc
        except records_lock.ProjectLockError as exc:
            # The manager takes the project lock itself where it writes; a
            # refusal it did not map still keeps the `agent_<code>` contract.
            raise AgentApiError(agent_manager.AgentError(
                "busy", f"The project is being written by something else: {exc}"), self._lang()) from exc
        except records_situation.NeedsMigrationError as exc:
            raise AgentApiError(_needs_migration_refusal(str(exc)), self._lang()) from exc
        except records_situation.WriteRefused as exc:
            raise AgentApiError(agent_manager.AgentError("read_only", str(exc)), self._lang()) from exc

    def _agent_target(self, body: dict[str, Any]) -> ServedProject:
        """Like `_workspace_target`: an unblocked served project that is the
        one the page named (a POST of an older window must not act on the
        project served now) and has an identity to keep runs under."""
        served = self._require_unblocked()
        if body.get("project") != str(served.root):
            raise AgentApiError(_AgentRefusal(
                "project_changed", 409, "the open project changed; reload the page before running an agent",
                "项目已切换，请刷新页面后再操作"), self._lang())
        if served.project_id is None:
            raise AgentApiError(_needs_migration_refusal("this project has no identity yet; nothing is run"),
                                self._lang())
        return served

    def _agent_writable(self, served: ServedProject) -> None:
        """What `RceHTTPServer.write_guard` would refuse for the served
        project (blocked -- `_agent_target` did that --, read-only, frozen
        until migrated), as `agent_<code>` errors in the request's language
        and WITHOUT the project lock: holding it around the run manager's
        slow work (`<program> --version`, a recovery snapshot) stalled every
        other save and the watcher (17.8). The manager takes the lock itself
        where it writes (undo, clearing a done mark, the agent's tools)."""
        if served.read_only:
            raise AgentApiError(_AgentRefusal(
                "read_only", 409, "this project was opened read-only; nothing is written",
                "这个项目是以只读方式打开的，不会写入任何内容"), self._lang())
        if served.needs_migration:
            raise AgentApiError(_needs_migration_refusal(
                "this project is frozen until it is migrated (researchos migrate); nothing is written"), self._lang())

    def _agent_person(self, *, strict: bool = True) -> tuple[str | None, str | None]:
        """Who presses 运行: the member id and name from `me.toml`. None for
        both only when there is no `me.toml` (the run then names nobody). A
        `me.toml` that exists but cannot be read is a refusal
        (`agent_person_unreadable`, 17.7: the agent is named with the person,
        and Research OS writes no entry without saying who made it) -- with
        `strict=False` (stop, undo: they start nothing) it is only "nobody"."""
        try:
            by = people.current_member_id()
        except people.PeopleError as exc:
            if not strict:
                return None, None
            raise AgentApiError(_AgentRefusal(
                "person_unreadable", 409, str(exc),
                "这台电脑上的个人信息文件（me.toml）读不出来，请先修好它，或在设置里重新填写你的名字"), self._lang()) from exc
        try:
            me = people.read_me().me
        except Exception:  # noqa: BLE001 -- a name is a courtesy, never a reason to refuse
            me = None
        return by, (me.name if me is not None else None)

    def _agent_runs_post(self, path: str) -> Any:
        """`POST /api/agents/runs` (start) and `/api/agents/runs/<id>/reply|stop|revert`
        (17.8). Start, reply and revert are refused up front as the write guard
        would refuse them (blocked, read-only, frozen) but do NOT hold the
        project lock -- the run manager takes it itself where it writes; stop
        is not refused for any of that -- it only ends processes and may take
        seconds."""
        rest = path[len("/api/agents/runs"):].strip("/")
        parts = rest.split("/") if rest else []
        if len(parts) > 2 or (len(parts) == 2 and parts[1] not in ("reply", "stop", "revert")) or len(parts) == 1:
            raise NotFoundError(f"no such endpoint: {path}")
        body = self._read_json_object()
        served = self._agent_target(body)
        agents, lang = self.server.agents, self._lang()
        if not parts:
            task = body.get("task")
            if not isinstance(task, str) or not task:
                raise AgentApiError(_AgentRefusal(
                    "invalid", 400, "the request must name a task", "请求里没有指明任务"), lang)
            self._agent_writable(served)
            by, by_name = self._agent_person()
            return self._agent_api(lambda: agents.start(
                served.root, served.project_id, task, by=by, by_name=by_name, lang=lang))
        run_id, action = parts
        if action == "stop":
            return self._agent_api(lambda: agents.stop(served.project_id, run_id))
        if action == "reply":
            self._agent_writable(served)
            by, by_name = self._agent_person()
            payload = self._agent_api(lambda: agents.reply(
                served.root, served.project_id, run_id, body.get("message"), by=by, by_name=by_name, lang=lang))
            if payload.get("kind") == "send_back":  # the task's done mark may have been cleared
                payload["generation"] = self.server.watcher.record_write(set())
            return payload
        self._agent_writable(served)
        by, _name = self._agent_person(strict=False)
        payload = self._agent_api(lambda: agents.revert(served.root, served.project_id, run_id, by=by,
                                                        files=body.get("files")))
        payload["generation"] = self.server.watcher.record_external_change(None)
        try:
            self.server.start_scan(served)
        except ApiError as exc:
            logger.info("no scan after undoing the agent run %s: %s", run_id, exc)
        return payload

    def _check_local_origin(self) -> None:
        """Reject a request whose `Host` does not name this exact loopback
        server, and one whose `Origin` (when a browser sends one at all)
        names anything else -- see module docstring's "Cross-origin
        defense". Called first thing in both `do_GET` and `do_POST`, before
        any routing or body parsing, so a rejected request never reaches
        `open_payload` or any other handler."""
        port = self.server.server_address[1]
        expected_host = f"127.0.0.1:{port}"
        host = self.headers.get("Host")
        if host != expected_host:
            raise ForbiddenOriginError(
                f"request Host {host!r} does not match this server ({expected_host!r}); refusing"
            )
        origin = self.headers.get("Origin")
        # Safari serializes the Origin of a same-origin POST to a
        # non-default port WITHOUT the port ("http://127.0.0.1", observed
        # live 2026-08-30 from the Research OS.app -> default-browser flow), so the
        # portless loopback form must be accepted alongside the exact one.
        # This does not widen the drive-by/rebinding surface: a foreign
        # page's Origin always carries its own hostname, and the only way a
        # browser produces a bare "http://127.0.0.1" is a page actually
        # served from loopback port 80 on this machine -- a local process,
        # outside this threat model (see module docstring).
        if origin is not None and origin not in (
            f"http://{expected_host}",
            "http://127.0.0.1",
        ):
            raise ForbiddenOriginError(
                f"request Origin {origin!r} does not match this server; refusing"
            )

    def _has_session(self) -> bool:
        """18.1: whether this request carries the engine's session -- the cookie
        `ro_session_<port>` or the header `X-ResearchOS-Session` -- compared in constant
        time. A server built without a session (the tests') asks for none: every request
        that passed the origin check has it."""
        expected = self.server.session
        if expected is None:
            return True
        wanted = expected.encode("utf-8")
        cookie = f"{SESSION_COOKIE_PREFIX}{self.server.server_address[1]}"
        offered = [self.headers.get(SESSION_HEADER) or ""]
        for line in self.headers.get_all("Cookie") or []:
            for part in line.split(";"):
                name, sep, value = part.strip().partition("=")
                if sep and name == cookie:
                    offered.append(value.strip().strip('"'))
        return any(hmac.compare_digest(value.strip().encode("utf-8"), wanted) for value in offered if value.strip())

    def _check_session(self) -> None:
        """18.1: refuse a request without the session (403 `session_required`, the
        sentence in the request's language). Called by `do_POST` right after the origin
        check and before routing or reading the body, so a refused write changes nothing."""
        if not self._has_session():
            raise SessionRequiredError(self.server.server_address[1], self._lang())

    def _boot(self, query: dict[str, list[str]]) -> None:
        """`GET /boot?session=<secret>` (18.1): the right secret sets the session cookie
        (HttpOnly, SameSite=Strict, Path=/), a wrong or missing one does not; either way
        the answer is a 303 to `/`, which also takes the secret out of the address bar."""
        expected = self.server.session
        offered = (query.get("session") or [""])[0]
        self.send_response(303)
        if expected is not None and hmac.compare_digest(offered.encode("utf-8"), expected.encode("utf-8")):
            cookie = f"{SESSION_COOKIE_PREFIX}{self.server.server_address[1]}"
            self.send_header("Set-Cookie", f"{cookie}={expected}; HttpOnly; SameSite=Strict; Path=/")
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _lang(self) -> str:
        """The language this request asks for (13.2): "en" or "zh" from
        its `X-ResearchOS-Lang` header, Chinese without one. For the
        sentences the engine composes for the page (`settings.say`)."""
        return settings_mod.request_language(self.headers.get(settings_mod.LANG_HEADER))

    def _refuse_without_project(self, path: str, allowed: frozenset[str]) -> None:
        """10.1: with no project served, every project endpoint answers 409
        `no_project` -- before routing, so no handler ever sees the
        placeholder root."""
        if path in allowed:
            return
        served = self._served()
        if served.no_project:
            raise ProjectBlockedError(served.blocked or {"situation": NO_PROJECT_SITUATION})

    def do_GET(self) -> None:  # noqa: N802 (stdlib's own method name)
        # 18.1 audit (the table is tests/test_engine_session.py's GET_ROUTES, which fails for a route
        # it does not list): no GET here writes a record, a setting or the project, opens an
        # application or starts a scan, so none asks for the session -- a window without it
        # still reads. The reads that run another program (read-only `git`, `<runner> --version`,
        # `opencode models`, `mdfind`) change nothing; the one GET that writes, the run list's
        # recovery of runs a gone engine left active, does so only with the session.
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            self._check_local_origin()
            if path == "/boot":  # 18.1: answers with no project too, before anything else is looked at
                try:
                    self._boot(query)
                except OSError:  # the window went away while it was answered: nothing to report, and nothing to log
                    pass
                return
            self._refuse_without_project(path, _NO_PROJECT_GET)
            if path == "/":
                self._send_html(200, _app_html())
            elif path == "/canvas.js":
                self._send_text(200, _canvas_js(), "text/javascript; charset=utf-8")
            elif path == "/i18n.js":
                self._send_text(200, _i18n_js(), "text/javascript; charset=utf-8", no_store=True)
            elif path == "/desk.js":
                self._send_text(200, _desk_js(), "text/javascript; charset=utf-8")
            elif path == "/node.js":
                self._send_text(200, _node_js(), "text/javascript; charset=utf-8")
            elif path == "/team.js":
                self._send_text(200, _team_js(), "text/javascript; charset=utf-8")
            elif path == "/routemap.js":
                self._send_text(200, _routemap_js(), "text/javascript; charset=utf-8")
            elif path == "/workspace.js":
                self._send_text(200, _WORKSPACE_JS_PATH.read_text(encoding="utf-8"), "text/javascript; charset=utf-8")
            elif path == "/workspace.css":
                self._send_text(200, _WORKSPACE_CSS_PATH.read_text(encoding="utf-8"), "text/css; charset=utf-8")
            elif path == "/api/workspace":
                served = self._require_unblocked()
                payload = self._workspace_api(lambda: workspace.overview(served.root, self._lang()))
                payload.update(project=str(served.root), id=served.project_id,
                               read_only=served.read_only or served.needs_migration or not payload.get("can_write", payload.get("mode") in {"single", "pi", "member"}))
                self._send_json(200, payload)
            elif path == "/api/workspace/document":
                served = self._require_unblocked()
                if "project" in query:
                    self._workspace_target({"project": query["project"][0]})
                rel = (query.get("path") or ["research/overview.md"])[0]
                payload = self._workspace_api(lambda: workspace.document(served.root, rel, self._lang()))
                marks = workspace.local_annotations(served.root, {rel: payload["revision"]}, self._lang())
                payload["officialness"] = marks["files"][rel]
                self._send_json(200, {**payload, "project": str(served.root), "id": served.project_id})
            elif path == "/api/workspace/exists":
                # Which of the paths a document names are files it may link to: the page's list of files stops at
                # `workspace.FILE_LIMIT`, and a link to an existing file beyond it is still a link (DESIGN.md 18.3).
                served = self._require_unblocked()
                if "project" in query:
                    self._workspace_target({"project": query["project"][0]})
                self._send_json(200, {"exists": workspace.existing_files(served.root, query.get("path", []))})
            elif path == "/api/workspace/asset":
                served = self._require_unblocked()
                rel = (query.get("path") or [""])[0]
                target = _resolve_within_root(served.root, rel)
                media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                         ".gif": "image/gif", ".webp": "image/webp"}
                content_type = media.get(target.suffix.lower())
                if not content_type:
                    raise BinaryFileError("only PNG, JPEG, GIF and WebP images can be displayed here")
                if not target.is_file():
                    raise NotFoundError("the image does not exist")
                if target.stat().st_size > 8 * 1024 * 1024:
                    raise BinaryFileError("the image exceeds the 8 MB preview limit; open it externally")
                try:
                    raw = cloud.read_bytes(target)
                except cloud.CloudOnlyError as exc:
                    raise CloudOnlyApiError(exc) from exc
                if len(raw) > 8 * 1024 * 1024:
                    raise BinaryFileError("the image exceeds the 8 MB preview limit")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
            elif path == "/api/summary":
                served = self._served()
                self._json_from_conn(lambda conn: summary_payload(conn, served.root, served))
            elif path == "/api/settings/llm":
                self._send_json(200, llm_setting_payload())
            elif path == "/api/attempts":
                self._json_from_conn(lambda conn: self._official(attempts_payload(conn, self._project_root())))
            elif path == "/api/tree":
                self._json_from_conn(lambda conn: self._official(tree_payload(conn, self._project_root())))
            elif path == "/api/lineage":
                self._json_from_conn(lambda conn: self._official(lineage_payload(conn, self._project_root())))
            elif path == "/api/projects":
                # Registry + current root only -- deliberately not routed
                # through _open_conn/_json_from_conn, since listing which
                # projects exist must keep working even when the *current*
                # project's own graph.db has gone missing mid-serve.
                self._send_json(200, projects_payload(self._served()))
            elif path == "/api/engine":
                # Which project this engine serves (9.12, `researchos migrate`'s
                # retire guard). Like /api/projects, not routed through
                # _open_conn: it must answer whatever state the project is in.
                self._send_json(200, engine_payload(self._served()))
            elif path == "/api/generation":
                # Watcher status only (task V3 phase 2) -- like
                # /api/projects, deliberately not routed through
                # _open_conn: the frontend's refresh poll must keep
                # answering even when the current project's own graph.db
                # has gone missing mid-serve (that failure surfaces as the
                # watcher's last_error, not as this endpoint erroring).
                # 10.2/10.3: plus the full scan's progress and last result.
                self._send_json(200, {**self.server.watcher.status_payload(), **self.server.scan_status()})
            elif path == "/api/canvas":
                scope = (query.get("scope") or [None])[0]
                served = self._served()
                # 9.12 (acceptance, 2026-10-05): the page saves no viewport
                # or layout for a project frozen until it is migrated (nor
                # for one opened read-only) -- it would only be refused.
                # A node's neighbourhood (U3, 「关系图」 on a node page) keeps
                # no arrangement of its own: laid out fresh, never saved.
                frozen = served.needs_migration or served.read_only or str(scope or "").startswith(canvas.NEAR_PREFIX)
                self._json_from_conn(lambda conn: self._official(
                    {**canvas_payload(conn, self._project_root(), scope), "frozen": frozen}))
            elif path == "/api/review":
                root = self._project_root()
                self._json_from_conn(lambda conn: review_payload(conn, root, self._lang()))
            elif path == "/api/github":
                # 11.2: the linked repository's state, from local refs only.
                self._send_json(200, github_state_payload(self._served(), lang=self._lang()))
            elif path == "/api/github/push-plan":
                # 11.3: what a push would do -- reads only, no network.
                self._send_json(200, github_push_plan_payload(self._served()))
            elif path == "/api/citations":
                # 11.4: the 「文献」 view -- reads only, no network.
                root = self._project_root()
                self._json_from_conn(lambda conn: self._official(literature_api.citations_payload(conn, root)))
            elif path == "/api/variables" or path.startswith("/api/variables/"):
                # The 「变量」 view (9.11): the cards are read from their files
                # (the index adds the trust decision); a blocked or moved
                # project answers 409 with what to do (`_variables_served`).
                self._send_json(200, self._official(variables_get(self._served(), path, query, self._lang()), cards=True))
            elif path == "/api/migration":
                self._send_json(200, migration_payload(self._served()))
            elif path == "/api/history":
                served = self._require_unblocked()
                if "node" in query:
                    # 12.6: a node's versions; without `node`, a link's
                    # judgment history (9.3) as before.
                    self._send_json(200, self._official(self._node_api(
                        lambda: node_api.history_payload(served.root, query, self._lang()))))
                else:
                    self._send_json(200, history_payload(served, query))
            elif path == "/api/history/version":
                # U4: one kept version as it was (「当时的依据」).
                served = self._require_unblocked()
                self._send_json(200, self._node_api(lambda: node_api.version_payload(served.root, query)))
            elif path == "/api/history/diff":
                served = self._require_unblocked()
                self._send_json(200, self._node_api(lambda: node_api.diff_payload(served.root, query, self._lang())))
            elif path == "/api/notes":
                served = self._require_unblocked()
                self._json_from_conn(lambda conn: self._node_api(
                    lambda: node_api.notes_payload(conn, served.root, query, self._lang())))
            elif path == "/api/trace":
                served = self._require_unblocked()
                self._json_from_conn(lambda conn: self._official(
                    self._node_api(lambda: node_api.trace_payload(conn, served.root, query))))
            elif path == "/api/node":
                # 12.6, 12.7, 12.9: one object's page -- reads only.
                served = self._require_unblocked()
                self._json_from_conn(lambda conn: self._official(
                    self._node_api(lambda: node_api.node_payload(conn, served.root, query, self._lang()))))
            elif path == "/api/results":
                served = self._require_unblocked()
                self._json_from_conn(lambda conn: self._official(
                    self._node_api(lambda: node_api.results_payload(conn, served.root, query))))
            elif path == "/api/routes":
                # U4: 研究路线 -- the attempts grouped by status, reads only.
                served = self._require_unblocked()
                self._json_from_conn(lambda conn: self._official(node_api.routes_payload(conn, served.root, self._lang())))
            elif path == "/api/evidence":
                # U4: an abandoned route's frozen basis (12.4), reads only.
                served = self._require_unblocked()
                self._json_from_conn(lambda conn: self._official(
                    self._node_api(lambda: node_api.evidence_payload(conn, served.root, query, self._lang()))))
            elif path == "/api/data-code":
                served = self._require_unblocked()
                self._json_from_conn(lambda conn: self._official(node_api.data_code_payload(conn, served.root)))
            elif path == "/api/stata":
                # 15.3: the project folders Research OS proposes for Stata
                # globals, the answers given and the paths 展开不了 -- reads only.
                served = self._require_unblocked()
                self._send_json(200, node_api.stata_payload(served.root, self._lang()))
            elif path == "/api/proposals" or path.startswith("/api/proposals/"):
                # 12.3: the PI's inbox or a member's own proposals; one
                # proposal with what approving would do. Never in any
                # reader of the index (12.4, "not a node until approved").
                served = self._require_unblocked()
                rest = path[len("/api/proposals"):].strip("/")
                if not rest:
                    self._send_json(200, self._node_api(lambda: node_api.proposals_payload(served.root)))
                elif "/" not in rest:
                    member = (query.get("from") or [None])[0]  # whose, when the id is in two areas
                    self._send_json(200, self._node_api(lambda: node_api.proposal_payload(
                        served.root, rest, self._lang(), member)))
                else:
                    raise NotFoundError(f"no such endpoint: {path}")
            elif path == "/api/desk":
                # 12.0 / 14: the desk and its route map -- reads only. 12.12:
                # a member's own work is marked (`desk.payload` bounds the
                # shared folder's read itself). 13.7: its sentences in the
                # page's language.
                served = self._require_unblocked()
                self._json_from_conn(lambda conn: desk_payload(conn, served, self._lang()))
            elif path == "/api/share":
                # 12.2: the shared folder's state (role, folder, last
                # snapshot, verification, what is pending) -- reads only.
                served = self._require_unblocked()
                self._send_json(200, self._node_api(lambda: node_api.share_status_payload(served.root)))
            elif path == "/api/me":
                self._send_json(200, node_api.me_payload())
            elif path == "/api/me/key":
                # U4: this machine's public key, if it has one; never made here.
                self._send_json(200, node_api.me_key_payload())
            elif path == "/api/share/sync-preview":
                # U4: what 「同步」 would write -- reads only.
                served = self._require_unblocked()
                self._send_json(200, self._node_api(lambda: node_api.sync_preview_payload(served.root, self._lang())))
            elif path == "/api/agents":
                self._send_json(200, agents_payload())
            elif path == "/api/agents/models":
                self._send_json(200, self._agent_api(lambda: agents_models_payload(query)))
            elif path == "/api/agents/runs":
                served = self._require_unblocked()
                if served.project_id is None:  # no identity yet: nothing could have run
                    self._send_json(200, {"runs": [], "active": None})
                else:
                    # Listing recovers the runs a gone engine left active (a write, and it may end their
                    # program): only for a request that carries the session (18.1); without it, the list is read as stored.
                    recover = self._has_session()
                    self._send_json(200, self._agent_api(lambda: self.server.agents.runs(served.project_id, recover=recover)))
            elif path == "/api/agents/large-files":
                # 18.1 item 5: what the confirmation before a first run names (a stat-only walk)
                served = self._require_unblocked()
                self._send_json(200, agent_snapshot.large_files(served.root))
            elif path.startswith("/api/agents/runs/") and path.endswith("/revert-plan") and path.count("/") == 5:
                # 18.1 item 5: the undo's list, which only reads
                served = self._require_unblocked()
                run_id = path[len("/api/agents/runs/"):-len("/revert-plan")]
                if served.project_id is None:
                    raise AgentApiError(agent_manager.AgentError("run_missing"), self._lang())
                self._send_json(200, self._agent_api(lambda: self.server.agents.revert_plan(
                    served.root, served.project_id, run_id)))
            elif path.startswith("/api/agents/runs/") and "/" not in path[len("/api/agents/runs/"):]:
                served = self._require_unblocked()
                run_id = path[len("/api/agents/runs/"):]
                if served.project_id is None:
                    raise AgentApiError(agent_manager.AgentError("run_missing"), self._lang())
                self._send_json(200, self._agent_api(lambda: self.server.agents.detail(served.project_id, run_id)))
            elif path == "/api/records":
                # The 「记录」 panel (9.2): read even for a blocked project --
                # it lists the folder's own files and writes nothing.
                self._send_json(200, records_payload(self._served()))
            elif path == "/api/file":
                values = query.get("path")
                if not values:
                    raise MissingParamError("missing required query parameter 'path'")
                self._send_json(200, self._official(file_payload(self._project_root(), values[0], self._lang())))
            elif path.startswith("/api/"):
                raise NotFoundError(f"no such endpoint: {path}")
            else:
                raise NotFoundError(f"not found: {path}")
        except ApiError as exc:
            self._send_api_error(exc)
        except Exception:
            logger.exception("unhandled error handling GET %s", _without_secret(self.path))
            self._send_json(500, {"error": "internal server error"})

    def _read_json_object(self) -> dict[str, Any]:
        """Every POST endpoint's body is one JSON object -- one shared
        parse+shape check, identical 400s for the same malformed input.
        Per-endpoint key requirements layer on top (`_read_json_body_with_
        path` for the path-shaped endpoints, `_parse_attempt_edit_body`
        for the attempt-edit ones)."""
        length = int(self.headers.get("Content-Length") or "0")
        raw_body = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError as exc:
            raise MissingParamError(f"invalid JSON request body: {exc}") from exc
        if not isinstance(body, dict):
            raise MissingParamError("request body must be a JSON object")
        return body

    def _read_json_body_with_path(self) -> dict[str, Any]:
        """The two path-shaped POST endpoints (`/api/open`,
        `/api/projects/switch`) additionally require a string `"path"` key."""
        body = self._read_json_object()
        if not isinstance(body.get("path"), str):
            raise MissingParamError("request body must be a JSON object with a string 'path' key")
        return body

    def do_POST(self) -> None:  # noqa: N802 (stdlib's own method name)
        parsed = urllib.parse.urlsplit(self.path)
        try:
            # Same first-thing origin check as do_GET, before any routing or
            # body parsing -- POST endpoints have side effects (open shells
            # out; switch repoints the whole server), so this line is what
            # stands between them and a drive-by page's cross-origin fetch.
            self._check_local_origin()
            # 18.1: and the session, which the origin check cannot prove -- a
            # program on this Mac can send the right Host and no Origin at all.
            self._check_session()
            self._refuse_without_project(parsed.path, _NO_PROJECT_POST)
            if parsed.path == "/api/open":
                body = self._read_json_body_with_path()
                payload = open_payload(self._project_root(), body["path"], bool(body.get("reveal", False)))
                self._send_json(200, payload)
            elif parsed.path == "/api/projects/create":
                body = self._read_json_object()
                payload = self._workspace_api(lambda: newproject.create(body))
                served = served_for(Path(payload["path"]))
                self._switch_to(served)
                payload["opened"] = True
                try:
                    self.server.start_scan(served)
                    payload["scanning"] = True
                except ApiError as exc:
                    payload["scanning"] = False
                    payload["scan_error"] = str(exc)
                self._send_json(200, payload)
            elif parsed.path in ("/api/workspace/document", "/api/workspace/tasks"):
                body = self._read_json_object()
                served = self._workspace_target(body)
                with self.server.write_guard(human=True):
                    self._workspace_target(body)  # a concurrent window may have switched projects
                    task_save = parsed.path.endswith("/tasks")
                    saved = {k: v for k, v in body.items() if k != "project"}
                    payload = self._workspace_api(lambda: workspace.save_tasks(served.root, saved) if task_save
                                                  else workspace.save_document(served.root, saved, lang=self._lang()))
                    rel = workspace.TASKS_PATH if task_save else payload["path"]
                    revision = payload["tasks_revision"] if task_save else payload["revision"]
                # The file is written and the lock released; the marks only decorate the answer, and a member's
                # reads the shared folder (bounded in time) -- never under the project lock (DESIGN.md 18.3).
                marks = workspace.local_annotations(served.root, {rel: revision}, self._lang())
                payload["tasks_officialness" if task_save else "officialness"] = marks["files"][rel]
                payload["generation"] = self.server.watcher.record_write(set())
                if parsed.path.endswith("/document"):
                    try:
                        self.server.start_scan(served)
                    except ApiError as exc:
                        logger.info("no immediate scan after writing a research document: %s", exc)
                self._send_json(200, payload)
            elif parsed.path == "/api/projects/switch":
                body = self._read_json_body_with_path()
                requested_id = body.get("id") if isinstance(body.get("id"), str) else None
                served, payload = switch_project_payload(body["path"], requested_id)
                self._switch_to(served)
                self._send_json(200, payload)
            elif parsed.path == "/api/projects/locate":
                # 「选择新位置…」 (9.4): adopts the chosen folder only if it
                # carries the registry entry's id (see locate_payload).
                served, payload = locate_payload(self._read_json_object())
                self._switch_to(served)
                self._send_json(200, payload)
            elif parsed.path == "/api/project/resolve":
                # The answer to a blocked project's question (9.4).
                served, payload = resolve_payload(self._served(), self._read_json_object())
                self._switch_to(served)
                self._send_json(200, payload)
            elif parsed.path == "/api/project/reopen":
                # 「重新打开」: the identity check again on the served folder
                # (reopen_payload); no path is taken from the page.
                self._read_json_object()
                served, payload = reopen_payload(self._served())
                self._switch_to(served)
                self._send_json(200, payload)
            elif parsed.path == "/api/projects/remove":
                # Section 8.10 rule 3. POST, and origin-checked above like
                # every other side effect: this writes ~/.researchos/projects.json,
                # which is the allow-list /api/projects/switch validates
                # against, so a drive-by page must never be able to edit it.
                body = self._read_json_body_with_path()
                served = self._served()
                entry = remove_project_entry(body["path"])
                payload = {"removed": body["path"]}
                if is_served_entry(served, entry):
                    # 10.4: removing the open project opens the next one,
                    # or the no-project state.
                    following = next_served_after_removal()
                    self._switch_to(following)
                    payload.update(
                        current=None if following.no_project else str(following.root),
                        project_id=following.project_id, blocked=following.blocked,
                        no_project=following.no_project,
                    )
                self._send_json(200, payload)
            elif parsed.path == "/api/projects/inspect":
                # 10.2 step 2: look before writing (origin-checked above,
                # like every endpoint that takes a path from the page).
                self._send_json(200, inspect_payload(self._read_json_object()))
            elif parsed.path == "/api/projects/add":
                # 10.2 step 3: write, then open it as a switch does, then
                # (a new folder) its first full scan in the background.
                served, payload, scan = add_payload(self._read_json_object())
                if served is not None:
                    self._switch_to(served)
                    if scan:
                        try:
                            self.server.start_scan(served)
                        except ApiError as exc:  # the addition stands; the scan says why it did not start
                            payload["scanning"] = False
                            payload["scan_error"] = {"state": exc.state, "error": str(exc)}
                self._send_json(200, payload)
            elif parsed.path == "/api/projects/rescan":
                # 10.3: the full scan of the served project, in the background.
                self._read_json_object()
                self.server.start_scan(self._served())
                self._send_json(200, {"ok": True, "scanning": True, **self.server.scan_status()})
            elif parsed.path == "/api/github/fetch":
                # 11.2: 「从 GitHub 获取最新状态」 -- `git fetch` for the linked
                # remote, the only network Research OS does here, only on this click.
                self._read_json_object()
                self._send_json(200, github_fetch_payload(self._served(), lang=self._lang()))
            elif parsed.path == "/api/github/commit-records":
                # 11.3 「先把人工记录提交一次」: the `.researchos/` files only.
                self._send_json(200, github_commit_records_payload(self._served(), self._read_json_object()))
            elif parsed.path == "/api/github/create-repo":
                # 11.3: a PRIVATE repository via gh, only on this click.
                self._send_json(200, github_create_repo_payload(self._served(), self._read_json_object()))
            elif parsed.path == "/api/github/push":
                # 11.3 「推送」: the only request that pushes; never forced.
                self._send_json(200, github_push_payload(self._served(), self._read_json_object(), lang=self._lang()))
            elif parsed.path in ("/api/citations/lookup-setting", "/api/zotero/open-attachment", "/api/zotero/open-item"):
                # 11.4: the 「文献」 view's setting and its two openers; a
                # judgment on a candidate is POST /api/judgements.
                self._send_json(200, literature_post(parsed.path, self._read_json_object()))
            elif parsed.path == "/api/settings/language":
                # 13.3: the language switch -- this Mac's setting, no project needed.
                self._send_json(200, language_setting_payload(self._read_json_object()))
            elif parsed.path == "/api/settings/llm":
                # 16: the model 「生成草稿」 uses -- this Mac's setting.
                self._send_json(200, llm_setting_set_payload(self._read_json_object()))
            elif parsed.path == "/api/attempts/draft":
                # 16: reads the chosen files and asks the model; writes nothing, and
                # not inside write_guard: the model may take minutes (16.2).
                body = self._read_json_object()
                self._require_unblocked()
                self._send_json(200, attempt_draft_payload(self._project_root(), body))
            elif parsed.path == "/api/projects/rename":
                # 10.4: the registry label only, never the folder.
                self._send_json(200, rename_payload(self._read_json_object()))
            elif parsed.path == "/api/attempts/initialize":
                body = self._read_json_object()
                served = self._workspace_target(body)
                with self.server.write_guard(human=True):
                    payload = self._workspace_api(lambda: workspace.initialize_attempts(served.root, self._lang()))
                if payload["created"]:
                    payload["generation"] = self.server.watcher.record_write({
                        str(attempts_ingest.config_path(served.root)), str(served.root / payload["file"]),
                    })
                self._send_json(200, payload)
            elif parsed.path == "/api/attempts/preview":
                # Pure dry run (task V3 phase 3) -- but origin-checked like
                # a write anyway (above), since its twin below mutates and
                # the two must never drift apart in what reaches them.
                body = self._read_json_object()
                self._require_unblocked()
                self._send_json(200, attempts_preview_payload(self._project_root(), body))
            elif parsed.path == "/api/attempts/write":
                # The one endpoint that writes project content: the user's
                # own map file, via researchos.webapp.mapedit (backup + atomic
                # write + re-ingest under the watcher's ingest lock) --
                # see module docstring's "Write-path defense".
                body = self._read_json_object()
                with self.server.write_guard(human=True):
                    payload = attempts_write_payload(self._project_root(), body, self.server.watcher)
                self._send_json(200, payload)
            elif parsed.path in ("/api/mappings/add", "/api/mappings/delete"):
                # The canvas's one write dialog (section 8.3/8.5): writes
                # .researchos/mappings.toml, never a request-named path -- see
                # module docstring's "Canvas write defense".
                body = self._read_json_object()
                fn = mappings_add_payload if parsed.path.endswith("/add") else mappings_delete_payload
                with self.server.write_guard(human=True):
                    payload = fn(self._project_root(), body, self.server.watcher)
                self._send_json(200, payload)
            elif parsed.path in ("/api/edges/reject", "/api/edges/restore", "/api/judgements"):
                # A human act on a machine link (9.1, 9.3): the ledger first,
                # through `researchos.records.judgements.judge`, then the index.
                body = self._read_json_object()
                served = self._served()
                with self.server.write_guard(human=True):
                    conn = self._open_conn()
                    try:
                        if parsed.path == "/api/judgements":
                            payload = judgement_payload(conn, served, body)
                        else:
                            action = "reject" if parsed.path.endswith("/reject") else "restore"
                            payload = edge_status_payload(conn, served, body, action)
                    finally:
                        conn.close()
                # The ledger was written and applied here: absorb it so the
                # watcher does not apply it a second time; pages re-fetch.
                payload["generation"] = self.server.watcher.record_write(
                    {str(ledger_mod.judgements_path(served.root))},
                )
                self._send_json(200, payload)
            elif parsed.path == "/api/records/answer":
                body = self._read_json_object()
                served = self._served()
                with self.server.write_guard(human=True):
                    payload = records_answer_payload(served, body)
                payload["generation"] = self.server.watcher.record_write(
                    {str(ledger_mod.judgements_path(served.root)), str(canvas.canvas_record_path(served.root)),
                     str(notes_mod.notes_path(served.root))},
                )
                self._send_json(200, payload)
            elif parsed.path == "/api/notes":
                # 12.6: a note added or withdrawn -- the notes ledger first
                # (`researchos.records.notes`, which takes the project lock and
                # re-checks identity), then the index's copy.
                body = self._read_json_object()
                served = self._served()
                with self.server.write_guard(human=True):
                    payload = self._node_api(lambda: node_api.notes_post_payload(
                        served.root, served.project_id, body, timeout=WRITE_LOCK_TIMEOUT_S,
                    ))
                payload["generation"] = self.server.watcher.record_write({str(notes_mod.notes_path(served.root))})
                self._send_json(200, payload)
            elif parsed.path == "/api/stata/answer":
                # 15.3: 「是」/「不是」 for the project folder behind a Stata
                # global -- one entry appended to .researchos/stata.toml
                # (`researchos.records.stata.answer`: the PI or a single
                # researcher, under the project lock), then the do-files
                # rescanned here, not left to the watcher.
                from researchos.records import stata as stata_record  # noqa: PLC0415

                body = self._read_json_object()
                served = self._require_unblocked()
                with self.server.write_guard(human=True):
                    payload = self._node_api(lambda: node_api.stata_answer_payload(
                        served.root, served.project_id, body, self._lang(), timeout=WRITE_LOCK_TIMEOUT_S,
                    ))
                    if payload.get("changed"):
                        with self.server.watcher.ingest_lock:
                            try:
                                self.server.watcher.reingest_dataflow(served.root)
                            except Exception as exc:  # noqa: BLE001 -- the answer is written whatever this says
                                logger.exception("rescanning the do-files of %s after a Stata answer failed", served.root)
                                payload["ingest_error"] = str(exc)
                payload["generation"] = self.server.watcher.record_write({str(stata_record.stata_path(served.root))})
                self._send_json(200, payload)
            elif parsed.path == "/api/papers":
                # 12.13: 「设为论文正文」 -- one entry appended to
                # .researchos/papers.toml (`researchos.records.papers`, which
                # takes the project lock and re-checks identity).
                from researchos.records import papers as papers_mod  # noqa: PLC0415

                body = self._read_json_object()
                served = self._served()
                with self.server.write_guard(human=True):
                    payload = self._node_api(lambda: node_api.papers_post_payload(
                        served.root, served.project_id, body, timeout=WRITE_LOCK_TIMEOUT_S,
                    ))
                payload["generation"] = self.server.watcher.record_write({str(papers_mod.papers_path(served.root))})
                self._send_json(200, payload)
            elif parsed.path == "/api/proposals":
                # 12.3: a proposal from this working copy -- the dry run, or
                # (with `write`) written once to the exchange. Writes nothing
                # in the project.
                body = self._read_json_object()
                served = self._require_unblocked()
                self._send_json(200, self._node_api(lambda: node_api.proposal_create_payload(served.root, body, self._lang())))
            elif parsed.path.startswith("/api/proposals/"):
                # 12.3: 「批准」「退回并说明」「讨论」 -- the PI only. Approving
                # writes the official version (files, the attempt table, a
                # card, decisions.toml) under the project lock.
                body = self._read_json_object()
                served = self._served()
                parts = parsed.path[len("/api/proposals/"):].split("/")
                if len(parts) != 2 or parts[1] not in ("approve", "return", "discuss"):
                    raise NotFoundError(f"no such endpoint: {parsed.path}")
                with self.server.write_guard(human=True):
                    payload = self._node_api(lambda: node_api.proposal_act_payload(
                        served.root, served.project_id, parts[0], parts[1], body, timeout=WRITE_LOCK_TIMEOUT_S,
                        ingest_lock=self.server.watcher.ingest_lock,
                    ))
                if parts[1] == "approve":
                    payload["generation"] = self.server.watcher.record_external_change(None)
                    try:
                        # The new files become nodes; the approval is done
                        # whatever this says (a scan already running, ...).
                        self.server.start_scan(served)
                    except ApiError as exc:
                        logger.info("no scan after approving into %s: %s", served.root, exc)
                self._send_json(200, payload)
            elif parsed.path == "/api/share/publish":
                # 12.3 「发布」: the PI writes a new signed snapshot to the
                # shared folder; nothing in the project is written.
                body = self._read_json_object()
                served = self._require_unblocked()
                self._send_json(200, self._node_api(lambda: node_api.share_publish_payload(
                    served.root, served.project_id, body, timeout=WRITE_LOCK_TIMEOUT_S)))
            elif parsed.path == "/api/share/sync":
                # 12.3 「同步」: this working copy to the latest verified
                # snapshot, under the project lock; then rescanned.
                self._read_json_object()
                served = self._require_unblocked()
                with self.server.write_guard(human=True):
                    payload = self._node_api(lambda: node_api.share_sync_payload(
                        served.root, served.project_id, timeout=WRITE_LOCK_TIMEOUT_S, lang=self._lang()))
                payload["generation"] = self.server.watcher.record_external_change(None)
                if payload.get("updated") or payload.get("removed"):
                    try:
                        self.server.start_scan(served)
                    except ApiError as exc:
                        logger.info("no scan after syncing %s: %s", served.root, exc)
                self._send_json(200, payload)
            elif parsed.path == "/api/team/add":
                # 12.12: the PI adds a member (team.toml is appended to under
                # the project lock) and their area is made on the shared folder.
                body = self._read_json_object()
                served = self._require_unblocked()
                with self.server.write_guard(human=True):
                    payload = self._node_api(lambda: node_api.team_add_payload(
                        served.root, served.project_id, body, timeout=WRITE_LOCK_TIMEOUT_S))
                payload["generation"] = self.server.watcher.record_external_change(None)
                self._send_json(200, payload)
            elif parsed.path == "/api/share/join-preview":
                # U4: what joining would write and whose key it would trust
                # -- nothing written; answered with no project open too.
                body = self._read_json_object()
                self._send_json(200, self._node_api(lambda: node_api.join_preview_payload(body)))
            elif parsed.path == "/api/share/join":
                # 12.3: a working copy in a NEW folder the page names --
                # origin-checked above like every endpoint; the folder is
                # refused at a top-level folder, in the home, inside a
                # project, or when not empty (`researchos.share.join`).
                # U4: then opened, as an added project is, and scanned.
                body = self._read_json_object()
                payload = self._node_api(lambda: node_api.share_join_payload(body))
                try:
                    joined = served_for(Path(payload["working_copy"]))
                    self._switch_to(joined)
                    payload["opened"] = True
                    try:
                        self.server.start_scan(joined)
                        payload["scanning"] = True
                    except ApiError as exc:
                        payload["scanning"] = False
                        payload["scan_error"] = {"state": exc.state, "error": str(exc)}
                except Exception as exc:  # noqa: BLE001 -- the copy stands; the page says it was not opened
                    logger.exception("the joined copy %s was not opened", payload.get("working_copy"))
                    payload["opened"] = False
                    payload["open_error"] = str(exc)
                self._send_json(200, payload)
            elif parsed.path == "/api/share/set":
                # U4: the shared folder of this project, on this machine
                # (projects.json); nothing in the project or the folder.
                body = self._read_json_object()
                served = self._require_unblocked()
                self._send_json(200, self._node_api(lambda: node_api.share_set_payload(
                    served.root, served.project_id, body)))
            elif parsed.path == "/api/team/init":
                # U4: the PI's team file (team.toml, created once).
                self._read_json_object()
                served = self._require_unblocked()
                with self.server.write_guard(human=True):
                    payload = self._node_api(lambda: node_api.team_init_payload(
                        served.root, served.project_id, timeout=WRITE_LOCK_TIMEOUT_S))
                payload["generation"] = self.server.watcher.record_external_change(None)
                self._send_json(200, payload)
            elif parsed.path == "/api/me/key":
                # U4: this machine's public key, made on first need.
                self._read_json_object()
                self._send_json(200, self._node_api(node_api.me_key_create_payload))
            elif parsed.path == "/api/me":
                # 12.1: who I am on this machine (~/.researchos/me.toml).
                self._send_json(200, self._node_api(lambda: node_api.me_set_payload(self._read_json_object())))
            elif parsed.path.startswith("/api/variables/"):
                # What Research OS authors on a variable card (9.11 "In the app"):
                # the card's log first, through `researchos.records.cards`, then
                # the index. 「完整比对」 writes the index only.
                body = self._read_json_object()
                served = self._served()
                action = parsed.path[len("/api/variables/"):]
                try:
                    with self.server.write_guard(human=action != "full-compare"):
                        payload = variables_api.act(served.root, served.project_id, action, body)
                except variables_api.CardActionRefused as exc:
                    raise CardRefusedApiError(exc) from exc
                payload["generation"] = self.server.watcher.record_write(
                    {str(p) for p, _subdir in inventory.card_file_paths(served.root)},
                )
                self._send_json(200, payload)
            elif parsed.path == "/api/migration/run":
                # 9.5's explicit act. Not inside `write_guard`: the migration
                # takes the project lock itself, from first step to last,
                # and is the one writer allowed while `migrating_from` is set.
                body = self._read_json_object()
                served = self._served()
                payload = migration_run_payload(served, body)
                # Who this folder is may have changed (a pre-V5 folder now has
                # an id): reopen it, and let every page re-fetch.
                self._switch_to(served_for(served.root, label=served.label))
                self._send_json(200, payload)
            elif parsed.path == "/api/canvas/layout":
                # UI state beside the graph (8.6) -- origin-checked like
                # every POST: a drive-by page must not scramble the canvas.
                body = self._read_json_object()
                with self.server.write_guard(human=True):
                    conn = self._open_conn()
                    try:
                        payload = canvas_layout_payload(conn, self._project_root(), body)
                    finally:
                        conn.close()
                # A record now (9.2, `.researchos/canvas.json`): absorbed so the
                # watcher does not take this page's own drag for a change.
                self.server.watcher.record_write({str(canvas.canvas_record_path(self._project_root()))}, bump=False)
                self._send_json(200, payload)
            elif parsed.path == "/api/settings/agents":
                # 17.1: this Mac's agents, program locations and limits
                # (settings.json); no project involved.
                body = self._read_json_object()
                self._send_json(200, self._agent_api(lambda: agents_settings_payload(body)))
            elif parsed.path == "/api/agents/runs" or parsed.path.startswith("/api/agents/runs/"):
                self._send_json(200, self._agent_runs_post(parsed.path))
            elif parsed.path == "/api/shutdown":
                # Stops this whole server (task V3 phase 4). Respond first,
                # then stop the serve loop from a separate thread -- see
                # module docstring's "Shutdown defense" for why shutdown()
                # must never be awaited from a handler, and why socket/
                # watcher cleanup deliberately stays in serve()'s own
                # finally-block server_close.
                _check_shutdown_target(self._read_json_object())
                self._send_json(200, {"ok": True})
                threading.Thread(
                    target=self.server.shutdown, name="researchos-shutdown", daemon=True
                ).start()
            else:
                raise NotFoundError(f"no such endpoint: {parsed.path}")
        except ApiError as exc:
            self._send_api_error(exc)
        except Exception:
            logger.exception("unhandled error handling POST %s", _without_secret(self.path))
            self._send_json(500, {"error": "internal server error"})


# What answers with no project served (10.1): the page, the registry and the
# engine's own state, and what changes the served project.
_NO_PROJECT_GET = frozenset({"/", "/i18n.js", "/canvas.js", "/desk.js", "/node.js", "/team.js", "/routemap.js", "/workspace.js", "/workspace.css",
                             "/api/projects",
                             "/api/engine", "/api/generation", "/api/me", "/api/me/key", "/api/settings/llm",
                             "/api/agents", "/api/agents/models"})
_NO_PROJECT_POST = frozenset({
    "/api/projects/switch", "/api/projects/locate", "/api/projects/remove", "/api/projects/inspect",
    "/api/projects/add", "/api/projects/create", "/api/projects/rename", "/api/shutdown", "/api/me",
    "/api/me/key", "/api/share/join-preview", "/api/share/join", "/api/settings/language",
    "/api/settings/llm", "/api/settings/agents",
})


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_server(
    project_root: Path | None,
    port: int,
    watch_interval: float = project_watcher.DEFAULT_INTERVAL_SECONDS,
    served: ServedProject | None = None,
    session: str | None = None,
) -> RceHTTPServer:
    """Bound to 127.0.0.1 only -- see module docstring. `port=0` (used by
    the test suite) asks the OS for a free ephemeral port; the caller reads
    the actual bound port back from `server_address[1]`. `watch_interval`
    is the auto-refresh watcher's polling period, injectable so tests can
    run a fast real-thread loop -- the watcher itself is created either
    way but only serve() starts its thread. `project_root=None` with no
    `served` starts in the no-project state (DESIGN.md 10.1). `session` is
    the secret every write must carry (18.1); None, the default, asks for
    none -- so the tests build servers that need no session, and `serve`
    never does."""
    return RceHTTPServer(
        ("127.0.0.1", port), RceRequestHandler, project_root, watch_interval=watch_interval, served=served,
        session=session,
    )


def _install_sigterm_handler(httpd: RceHTTPServer | None = None) -> Callable[[], None]:
    """17.3: a SIGTERM to the engine takes the Ctrl+C path -- `serve_forever`
    ends and `serve`'s `finally: server_close()` stops every running agent
    (state `interrupted`) instead of the process dying with them orphaned.
    Only the first SIGTERM acts, and none once the shutdown has begun
    (`httpd` is closing -- say a POST /api/shutdown ended the loop and the
    wrapper's SIGTERM comes while `server_close` waits for the agents): a
    KeyboardInterrupt there would cut the agents' kill threads off and leave
    their processes running. Installed from the main thread only
    (`signal.signal` refuses any other, and tests run `serve` in threads);
    returns the function that puts the previous handler back (a no-op when
    nothing was installed)."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    fired = threading.Event()

    def on_sigterm(signum: int, frame: Any) -> None:
        if fired.is_set() or (httpd is not None and httpd._closing):  # noqa: SLF001 -- the server's own flag
            return
        fired.set()
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGTERM, on_sigterm)
    except (ValueError, OSError):  # not the main interpreter thread after all, or no signals here
        return lambda: None

    def restore() -> None:
        with contextlib.suppress(ValueError, OSError, TypeError):
            signal.signal(signal.SIGTERM, previous if previous is not None else signal.SIG_DFL)

    return restore


class SessionFileError(ApiError):
    """The engine could not write its session file (18.1): it refuses to start, because the
    Mac app could never learn the session and every window would be read-only."""


def new_session() -> str:
    """18.1: a fresh engine session -- 32 random bytes, as 64 hex characters."""
    return secrets.token_hex(32)


def write_session_file(port: int, session: str) -> Path:
    """18.1: `<home>/engine/<port>.session` -- the secret and a newline, mode 0600 from the first
    byte, replaced atomically (written beside it, then renamed), so a reader sees the old file
    or the whole new one and no other user ever sees the secret. The caller removes it at shutdown
    (`remove_session_file`)."""
    target = paths.engine_session_path(port)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(session + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    return target


def remove_session_file(path: Path, session: str) -> None:
    """Remove the session file written for `session` -- not one a newer engine of the same port
    wrote since (it holds another secret). Never raises: a shutdown is not the place for it."""
    try:
        if path.read_text(encoding="utf-8").strip() == session:
            path.unlink()
    except OSError:
        pass


def _recover_agent_runs(httpd: RceHTTPServer) -> None:
    """The body of `serve`'s recovery thread (18.1 item 6): `AgentManager.recover_all`, whose trouble is
    logged and never ends the engine."""
    agents = getattr(httpd, "agents", None)
    if agents is None:
        return
    try:
        recovered = agents.recover_all()
    except Exception:  # noqa: BLE001 -- documented: recovery is a courtesy to the next page load
        logger.exception("could not recover the agent runs an earlier engine left going")
        return
    if recovered:
        logger.info("recovered %d agent run(s) left going by an earlier engine: %s", len(recovered), ", ".join(recovered))


def serve(project_root: Path | None, port: int, open_browser: bool = True, served: ServedProject | None = None) -> None:
    """`researchos serve`'s entry point: validate the project, print the one
    startup line the task spec requires verbatim, optionally open a browser
    tab, then block serving requests until Ctrl+C. `_require_db` runs before
    `build_server` so a project that was never `researchos init`ed fails with the
    same clear message every other subcommand gives, before a socket is even
    opened.

    This is also the one place the auto-refresh watcher's polling thread is
    started (task V3 phase 2) -- a served app is the only consumer of live
    re-ingestion, so build_server callers that never serve (the test
    suite's routing fixtures) never pay for a background thread. The
    `finally` block's `server_close` stops it again.

    V5: `served` is the identity check's result (`served_for`; computed
    here when not given). A blocked project -- or a registry entry whose
    folder is gone -- does not stop the server: it serves the question
    (module docstring, "Project identity") and writes nothing.

    17.3: from the main thread a SIGTERM ends the serve loop like Ctrl+C, so
    the `finally` below stops every running agent before the process exits.

    18.1: `serve` always makes an engine session (`new_session`): the server
    asks every write for it, `<home>/engine/<port>.session` holds it (mode
    0600) until shutdown, and a browser it opens is sent to
    `/boot?session=...` first. The secret is never printed, whether or not a
    browser is opened -- the startup line is the plain address.

    18.1 item 6: a thread recovers the agent runs an earlier engine left
    preparing or running, of every project that has runs on this Mac."""
    if served is None:
        served = no_project_served() if project_root is None else served_for(Path(project_root))
    try:
        if served.no_project:
            # 10.1, 10.5: no project yet -- the page offers 「添加项目…」.
            print("Research OS: no project yet -- the app offers to add one", file=sys.stderr)
        elif served.blocked is None:
            _served_db(served)
        else:
            print(
                f"Research OS: {project_root}: {served.blocked.get('situation')} -- the app shows what to do; "
                f"nothing is written until it is answered",
                file=sys.stderr,
            )
    except GraphDownloadingError as exc:
        # Not fatal at startup (section 8.10 rule 1): the graph exists, it is
        # just in iCloud right now and its download has been requested.
        # Refusing to start would turn a transient state into Research OS.app's
        # 「引擎没有在 10 秒内启动」; serving lets the page show the honest
        # header state and pick the graph up by itself once it is local.
        print(f"Research OS: {exc}", file=sys.stderr)
    session = new_session()
    httpd = build_server(served.root, port, served=served, session=session)
    bound_port = httpd.server_address[1]
    try:
        session_file = write_session_file(bound_port, session)
    except OSError as exc:
        httpd.server_close()
        raise SessionFileError(
            f"cannot write the engine session file {paths.engine_session_path(bound_port)}: {exc}; "
            f"not starting without it"
        ) from exc
    url = f"http://127.0.0.1:{bound_port}"
    print(f"Research OS app: {url}  (Ctrl+C to stop)")
    httpd.watcher.start()
    # 18.1 item 6: the runs an earlier engine left going are recovered now, not when their project's runs
    # are next listed; a thread, so that a big project's after-picture does not hold up the first request.
    threading.Thread(target=_recover_agent_runs, args=(httpd,), name="researchos-agent-recover",
                     daemon=True).start()
    if open_browser:
        webbrowser.open(f"{url}/boot?session={session}")
    restore = _install_sigterm_handler(httpd)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd._closing = True  # noqa: SLF001 -- from here on a SIGTERM must not interrupt the shutdown
        # first, so the file is gone before the slow part of the shutdown (the agents stopping)
        # and a newer engine of this port is never left without its own
        remove_session_file(session_file, session)
        try:
            httpd.server_close()
        finally:
            restore()
