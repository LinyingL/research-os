# Design

Research OS builds a provenance graph over a research project by reading the tools
researchers already use. It never asks you to change how you work, and it
never guesses.

> Section numbers below are the ones cited throughout the source code
> (`see DESIGN.md section 5`, etc.). They are inherited from the internal
> specification this document is derived from, so the numbering has gaps:
> the missing sections covered project scope, budgets, and working
> discipline, which are not part of the public design.

## Section 0 — Principles

**Deterministic first.** Most of the graph comes from parsing, not inference:
filenames in `\includegraphics`, cite keys, git SHAs recorded by experiment
trackers. Code beats models wherever code suffices. A language model is an
optional enhancement layer, never a prerequisite.

**Evidence or nothing.** Every edge carries the extractor that produced it, a
pointer to the evidence (`file:line`, run id, commit SHA), a confidence value,
and a status. An edge without evidence cannot be stored — the database
rejects it. `backed_by` is the one exception to `file:line` being a stored
literal: its pointer is `file` plus the claim's *current* line, and the line
half is resolved at query time rather than persisted, precisely because an
unrelated edit elsewhere in the file shifts it (section 4, connector 7).

**Never guess.** When a path cannot be resolved, a SHA is not in the graph, or
a filename matches more than one candidate, the extractor skips and logs. A
missing edge is a normal outcome. A fabricated one is a defect.

**Humans own judgment.** Machine ingestion can write `auto` and `pending`
edges only. Confirming or rejecting an edge goes through a separate write
path, and re-ingestion never overwrites a human decision. (This is about
protecting the one place a decision is actually recorded, not a blanket
"never touch human_fields" rule -- an `attempt` node's `human_fields` is
the one documented exception, and section 4 explains why it is not really
an exception at all.)

*Section 17 (2026-10-21) lets an AI agent a person assigns do work —
files, code, runs — with attribution and undo instead of approval;
judgments stay human.*

**Simplest thing that works.** Prefer an existing library over new code,
deterministic code over a model, a smaller model over a larger one, a single
file over a service. Abstract on the third repetition, not the first.

**Kept material is not a relation.** An old copy of a script, the hash of a
file, a confirmation the researcher made: each is material that can be
checked. That two pieces of material sit side by side — the same folder,
the same day, the same name — says nothing about how they are related. A
relation between them is recorded only when it has a basis of its own:
something the machine read, or something the researcher stated. "This
file existed when the definition was confirmed" and "this file was built
by that definition" are different sentences, and Research OS never writes the
second because the first is true. (Adopted 2026-10-05 from the external
review of Section 9; it is the rule the result-provenance phase starts
from.)

## Section 2 — Architecture decisions

Three layers, only the first of which is mandatory:

```
  deterministic parsing   always on, no model, no network
           |
  semantic enhancement    optional, local model, off by default (implemented: annotation-only)
           |
  human confirmation      always available, never required
```

The engine is fully usable with the first layer alone. That is the default
install and the intended baseline, not a degraded mode.

The semantic layer is implemented as `researchos judge` (S2): it reviews pending
`backed_by` candidates and writes a model's opinion into each edge's
`evidence.semantic_review`, but it never writes `status` — see "Machine
annotation vs. human judgement" below for why that split is load-bearing,
not incidental.

Storage is a single SQLite file at `.rce/graph.db` inside the project,
alongside `.git` rather than inside it. Two tables — `nodes` and `edges` —
plus a migrations table. No database server, no message queue, no daemon.

*Superseded in location only (Section 8.10, rule 1): the graph is still a
single SQLite file per project, but it now lives at
`~/.rce/graphs/<id>/graph.db`, outside the project, because a project
folder may be cloud-synced. Everything else in this paragraph stands.*

The confirmation queue is not a third table; it is `edges` filtered by
`status = 'pending'`.

Evidence accumulates rather than overwrites: when the same figure is included
twice in one section, both call sites are kept as occurrences on one edge.
This "one occurrence per call site" framing belongs to `includes`/`cites`/
`generates`; `backed_by` occurrences are identified differently — (file,
metric, metric_value, claim_raw, claim_value), deliberately never the
claim's line — so they no longer distinguish call sites at all. Two
occurrences on one `backed_by` edge mean the same claim matched two
different (metric, value) pairs on the same experiment, not the same match
observed at two source locations (section 4's `backed_by` row and "Machine
annotation vs. human judgement" below).

An edge's evidence can also carry sibling keys alongside `occurrences` —
`semantic_review` (below) and `candidate_count` (a `backed_by`-only,
edge-level fact: how many (experiment, metric) pairs the claim matches in
total) are both stored this way, overwritten wholesale to their latest
value on every write, never accumulated like `occurrences` is. Folding
either into an occurrence's own identity instead used to be a real bug —
see connector 7's confidence discussion below for `candidate_count`'s
history.

`nodes.human_fields` and `edges.status` are the human-owned columns. The
machine write path (`upsert_node` / `upsert_edge`) structurally cannot set
them to a human value; a separate function does, and re-ingestion leaves
confirmed and rejected edges alone.

## Section 4 — Object model

Nine node types: `project`, `experiment`, `commit`, `figure`, `section`,
`claim`, `reference`, `contributor`, `attempt`.

Node ids are deterministic, so repeated ingestion converges instead of
duplicating: `commit:<sha>`, `figure:<repo-relative-path>`,
`section:<tex-file>#<slug>`, `reference:<lowercased-bib-key>`,
`experiment:<run-id>`, `contributor:<lowercased-email>`.

`claim:<tex-file>#<content-hash>` is content-addressed rather than
position-addressed: the hash covers the owning section's slug, the claim's
sentence (whitespace/case-normalized), the number's own printed literal, and
its position among any other numbers in the same sentence -- never the line
number. Inserting, deleting, or reordering unrelated lines therefore leaves
every claim's id unchanged; only editing the claim's own text or its printed
number produces a new id, which is correct -- at that point a human's
earlier confirm/reject on the old claim genuinely no longer applies. A
line-anchored id would instead let an unrelated edit shift a claim onto
another claim's former id, silently inheriting that claim's human verdict.

**`attempt` (migration 0002).** A researcher's project often has a
hand-maintained Markdown table logging every research path tried -- an
attempt timeline, one row per attempt, with a stable `#` column, a
date, a path/variable description, and a human-written result and verdict
(e.g. "confirmed", "dead end", "direction rejected"). Research OS never asks the
researcher to change this habit; an `attempt` node is a machine-parsed
mirror of one such row, kept queryable and linkable to the rest of the
graph rather than left as inert prose.

Id convention: `attempt:<source-file-relative-path>#<# column value>` --
e.g. `attempt:00-项目地图_唯一真相.md#16`, or `#14a` for a row the author
split into sub-attempts (`14a`/`14b`) without renumbering the rows after
it. This assumes the `#` column is a stable manually-assigned label rather
than a position: checked against a real 23-row attempt timeline (row 14
was split into `14a`/`14b` without renumbering the rows after it, so the
column runs 1-13, `14a`, `14b`, 15-22 -- 23 physical rows, 22 distinct
labels, no label reused), several rows are *not* in chronological order
relative to their neighbors (e.g. rows dated 2026-07-09 appear after rows
dated 2026-07-22 because they were folded in from a separate parallel
review) -- exactly the pattern of a label the author assigns once and
keeps, not a row index that shifts when the table is resorted or edited.
This id convention holds only under that assumption: a project whose own
timeline reuses a `#` value on a *different* row without renumbering would
have re-ingestion quietly merge two different attempts onto one node, so
an ingest extractor for this format must inherit the same "skip and log,
never guess" discipline as every other connector (section 5) when a
row's `#` value repeats a different row's `#` value within the same parse
of the table -- editing a row's own description/date/variables text is
not this case; those are ordinary machine-parsed `attrs` and are expected
to change on re-parse like any other node's `attrs`.

**`rce.ingest.attempts` (task A2)** is this extractor: config-driven only,
via `.rce/attempts.toml` (file/heading/`[columns]` name mapping, optional
`steps_dir`) -- with no config present, `researchos attempts` prints a
copy-pasteable template and exits 1 rather than guessing which table in the
project is the attempt timeline. The same clean-message-and-exit-1 handling
also covers a configured `[columns]` name no longer present in the table's
actual header (e.g. a renamed column) and a heading/table that can no
longer be located at all (see "Attempt orphans" below); both raise
`AttemptsConfigError` -- the latter via its `AttemptsTableNotFoundError`
subclass -- from inside `ingest_attempts_repo`, not only from `load_config`,
and `rce.cli.cmd_attempts` catches both around the same call so neither
surfaces as a raw traceback. `verdict`/`result` go to `human_fields` via
`set_human_fields` on *every* parse, resynced to whatever the row currently
says in the source file -- see "Attempt human_fields: resync from source,
not write-once" below for why this is not an exception to "humans own
judgement" (section 0) but the same rule pointed at a different authority.
A description referencing a step-number range (e.g. `(16-18)`) resolves,
when `steps_dir` is configured, by exact numeric filename prefix into
`attrs.step_files`; a number with no matching file is recorded in
`attrs.step_files_broken` for a future consistency check (A3), never fuzzy-matched.

Only `#`/date/variable-description/referenced-step-number/source-file-
and-line are machine-parsed and live in `attrs` -- they are facts about
the row's text, and a re-parse may refresh them like any other node's
`attrs`. `verdict` and `result` are the human's judgement call recorded in
prose next to the row (what the attempt showed, whether it stands) and
must be written to `human_fields` only, through `set_human_fields`, never
through the machine `upsert_node` path -- not because the value is frozen
once written (it is not; see below), but because `db.upsert_node` enforces
this structurally: its `UPDATE` column list has no `human_fields` entry, so
even a future extractor that carelessly puts `verdict`/`result` in its
`attrs` dict cannot make them land there instead.

### Attempt human_fields: resync from source, not write-once

The first implementation of task A2 wrote `verdict`/`result` to
`human_fields` once, on first sight of an id, and never again -- modeled
directly on `edges.status`, where re-ingestion must never touch a
confirmed or rejected edge. Testing against a real 23-row attempt timeline
exposed this as a mistake, not a stricter safeguard: a row's verdict was
changed from `☠️ 伪协整` to `🕒 决定复活重做` in the source file, the
project was re-ingested, and the graph did not move -- `researchos attempts
--check` kept printing `[revived_dead_variables] OK: no issues found
(23/23 attempts checked)`, while the same map ingested fresh into an empty
database correctly reported the revival. `rce.consistency.
check_revived_dead_variables`'s logic was never wrong; the input it read
had been frozen.

The mistake was applying "humans own judgement" to the wrong authority.
That rule protects the one place a human decision is actually recorded
from being silently overwritten by a machine re-ingest. For
`edges.status`, that place is the graph itself: a `backed_by` candidate is
machine-generated, and `researchos confirm` writing `status` in the graph is the
*only* place a human ever records confirm/reject -- so re-ingestion
touching `status` would erase the one record of that decision that exists
anywhere. For an attempt's `verdict`/`result`, the decision is instead
recorded by the researcher in their *own source Markdown file*, in prose
next to the row -- the timeline is declared the project's single source of
truth, and the graph is a queryable mirror of it, not a second copy that
competes with it. Freezing `human_fields` on first sight did not defend
that decision; it defended a stale copy against the original, which is
backwards -- the researcher editing their own map file *is* them making
the judgement, and an engine that keeps mirroring the file faithfully is
not overriding anything. ("Keeps mirroring" describes what happens *when
this extractor runs*, not a standing live guarantee -- see "Staleness
window" below.)

The fix: `verdict`/`result` are resynced from the source file on every
parse (`rce.ingest.attempts.ingest_attempts_repo`), written via
`set_human_fields` whenever the freshly parsed value differs from what is
stored, and skipped otherwise so an unchanged row produces neither a
gratuitous write nor log noise. This is not an exception to "machine
ingestion never overwrites a human decision" -- it is that same rule,
pointed at the place the decision actually lives. `edges.status` and
attempt `human_fields` therefore behave oppositely on purpose:
`edges.status` must never be touched by re-ingestion because the graph is
the sole record of that decision (superseded by 9.1: the judgment ledger
`.rce/judgements.toml` is now that record, and the index derives
`edges.status` from it); attempt `human_fields` must always be
resynced by re-ingestion because the source file is the sole record and
the graph must not be allowed to go stale and start lying about what that
file currently says.

**Precondition this rests on.** There is currently exactly one place an
attempt's verdict is ever recorded: the source Markdown file --
`db.set_human_fields` has exactly one caller in the whole `src` tree, this
extractor. If a future feature lets someone edit an attempt's verdict
directly in the graph (mirroring how `researchos confirm` edits `edges.status`
directly, rather than editing a source file), that edit and the source
file become two authorities that can disagree, and this resync would
silently discard the in-graph edit on the very next `researchos attempts` run.
That case must be resolved explicitly when such a path is added -- e.g.
detect divergence and refuse to overwrite, or have the in-graph edit
itself rewrite the source file -- rather than left for whoever adds it to
discover by losing an edit. This precondition used to be enforced by
nothing but this paragraph; `tests/test_ingest_attempts.py::
test_set_human_fields_has_exactly_one_caller_in_src` now asserts the
call-site count directly via `ast` -- a mention of the name in a docstring
or comment is not mistaken for a call, and (fixed after a reviewer probe
found the gap) neither is an aliased import evading detection: `from
rce.db import set_human_fields as _write_hf` followed by a real call
through `_write_hf(...)` used to pass this test undetected, since that
call's own `func.id` is the alias, never the literal string
`"set_human_fields"`. The scan now also flags any `ImportFrom` of the name
as a site in its own right, aliased or not, regardless of whether the
alias is ever subsequently called. This makes the test a best-effort
static tripwire for the common ways a second caller gets added -- an
ordinary call, an attribute access, or an aliased import -- not a proof
that no second caller can ever exist undetected: a sufficiently indirect
reference (`getattr(db, "set_human_fields")`, constructing the call from a
string) is still outside what a syntactic `ast` scan can see. It closes the
concrete gap a reviewer found, not every gap that could theoretically be
constructed.

**Staleness window: only `researchos attempts` refreshes these nodes.** `attempt`
nodes are written and resynced exclusively by `rce.ingest.attempts`
(above), which runs when the `researchos attempts` subcommand runs -- `researchos
ingest` (the deterministic pass over commits/figures/claims/etc., section
2) never touches them. So between one `researchos attempts` run and the next, an
attempt's `human_fields`/`attrs` read via `researchos query` or the MCP server can
be stale relative to whatever the researcher has since edited into the
source file -- the mirror is refreshed on demand, not continuously, and
nothing currently re-runs `researchos attempts` as a side effect of `researchos ingest`
or a query. This is a reasonable division of labor (a hand-maintained
Markdown table is not a thing to re-parse on every unrelated `researchos ingest`
of commits and figures), not an oversight, but it means "the graph
reflects the file" is only true as of the last `researchos attempts` invocation,
and a project relying on fresh attempt data before a query should run `researchos
attempts` first, the same way `cmd_attempts` itself always re-ingests
before listing or `--check`ing (above).

### Attempt orphans: deletion resyncs from source too, not just verdicts

**Observation failure is not evidence of absence.** Everything else in this
section governs *which* previously-ingested nodes `_cleanup_orphans`
deletes once it runs -- but it must first be established that this run
actually observed the timeline at all, and "parsed zero rows" is not one
condition. `parse_attempts_table` can come back with either (a) the
heading and its table were both located, and the table's data section
genuinely has no rows (every row really was deleted, or this is a fresh,
empty timeline) -- a real, legitimate observation -- or (b) the configured
`heading`, or a table underneath it, could not be *located* in the source
file at all: an unrelated Markdown edit inserted a heading between the
tracked one and its table, the heading text drifted out of sync with
`.rce/attempts.toml`, or the table lost its separator row. These two used
to come back identically, as a bare `[]` with only a logged warning
(Blocker 1, found against the real 23-row project map this extractor was
built against): inserting one ordinary prose sub-heading (`### 说明:...`)
between `## 二、尝试途径总年表` and its table made `parse_attempts_table`
return `[]`; `ingest_attempts_repo`'s `seen_ids` therefore came back empty,
and `_cleanup_orphans` read that exactly the way it reads a genuinely
emptied table -- "none of these 23 attempts exist anymore." `researchos attempts`
printed `attempts=0 created=0 updated=0 orphans_removed=23` and deleted
every node; `researchos attempts --check` then printed `OK: no attempts to check`
and exited 0 -- an ordinary heading edit silently erased the graph's entire
attempt history and reported success on the way out. This is Section 0's
"never guess" principle again, pointed at a failure mode the OSError
read-failure path just above already defends against (that section's own
docstring: "not evidence the attempts are gone") but which had not yet been
extended to a *located-file, unlocatable-table* failure: not having
observed the table's rows is not evidence they don't exist, and reading
"not found" as "empty" is exactly as much a guess as fabricating a number
would be.

The fix keeps the two outcomes from ever being conflated in the first
place, structurally rather than by convention: `parse_attempts_table` now
raises `AttemptsTableNotFoundError` (a subclass of `AttemptsConfigError`)
when the heading or its table cannot be located, and returns a plain `[]`
-- unambiguous now -- only when the table *was* located and its data
section is genuinely empty. `ingest_attempts_repo` lets that exception
propagate rather than swallowing it (after logging which file/heading it
was looking for and noting explicitly that every existing node for this
file is left untouched): the row loop that would build `seen_ids` never
runs, so `_cleanup_orphans` is skipped structurally, not by a conditional
that could itself have the same bug. `rce.cli.cmd_attempts` catches it
alongside every other `AttemptsConfigError` (above) and exits 1 with a
clean message -- deliberately not the OSError path's silent all-zero-counts
return, because unlike a transient read failure this means the run's own
request (this heading, in this file) could not be satisfied, and the
operator should be told, not shown a quiet success line.

The resync above covers a row that still exists but whose verdict changed.
A row *deleted* (or renumbered) out of the table entirely -- once the
table has actually been located and parsed, per the above -- used to be
handled differently, and inconsistently with the principle that same
section just established: `rce.ingest.attempts._cleanup_orphans` preserved
any orphaned `attempt` node that still carried `human_fields`, deleting
only one with none -- modeled on `rce.ingest.claims`'s own orphan cleanup,
which preserves a claim node whose `backed_by` edge carries a
confirmed/rejected `edges.status`.

That model does not transfer as a `human_fields` check. Testing against a
real 23-row map confirmed it concretely: deleting row 15 from the source
file and re-ingesting left `researchos attempts`' listing still printing 23 rows
including `#15`, `researchos attempts --check` still reporting a finding about a
row that no longer existed anywhere in the project, and exit code 1 -- with
no way to clear either short of dropping the whole database. Worse, the
guard behind this was never actually reachable in practice: this same
extractor's resync (above) writes `human_fields` on *every* node's first
parse, even a row whose verdict/result are both blank, since
`{"verdict": "", "result": ""}` is still a truthy dict -- so
`if node["human_fields"]:` was true for every attempt node this extractor
has ever produced, and the delete branch beneath it was dead code outside a
hand-built test. `human_fields` looking truthy is not evidence of a
decision *in the graph* the way a confirmed/rejected `edges.status` is --
it is simply the last mirrored copy of whatever the row's verdict cell
said, and the row no longer exists to mirror. Checking it, as that version
of `_cleanup_orphans` did, was applying claims's guard to the wrong
authority: an attempt's verdict/result decision is recorded in the
researcher's own source file, never in an edge, and the graph only mirrors
it (see "resync from source" above); `human_fields` is simply not where a
human decision about *this node* lives.

Removing that wrong check does not mean no check is needed at all, though
-- and the fix that first replaced it went one step too far by concluding
exactly that. That version deleted an orphaned node -- and every edge
touching it, in either direction, from any extractor -- unconditionally,
reasoning that an attempt node's only edge type, `uses`
(`attempt --uses--> commit`, written by `rce.consistency` below, tagged
`extractor="attempts_consistency"`), is "a deterministic, always-`auto`
fact that no human ever confirms or rejects." That assumption was never
actually *enforced* anywhere in the codebase, and a real end-to-end run
against the project map disproved it directly (Blocker 2): `researchos confirm`
carries no allowlist restricting which edge types a human may judge --
`researchos confirm --status confirmed <attempt-node> <commit> uses
attempts_consistency` succeeds today exactly like confirming a `backed_by`
edge would. Once that `uses` edge was confirmed, deleting the row it
belonged to and re-ingesting silently destroyed both the edge and the node
carrying the confirmed decision -- no log line, no count, nothing to
distinguish it from an ordinary, harmless cleanup.

The corrected fix restores a preserve check, keyed on the same signal
claims already uses -- `edges.status` in `('confirmed', 'rejected')` -- not
on `human_fields`, which was the wrong signal for the reasons just given.
Before deleting an orphaned attempt node, `_cleanup_orphans` now inspects
every edge touching it, `src` or `dst`, from any extractor; if any carries
that status, the node and all of its edges are left exactly as they are,
logged (which node, which file, how many edges carry a decision), and
counted under a new `orphans_preserved_with_human_decision` key, visible in
`researchos attempts`'s own summary line rather than only in the log. Only when
none of a node's edges are confirmed or rejected does the delete proceed as
before: every edge touching the node is removed first (counted under a new
`orphan_edges_removed` key, mirroring claims's `backed_by_edges_removed`),
then the node itself.

`edges.status` (claims) and `attempt` orphans (this section) now agree on
*when* to preserve a node: both refuse to delete once a confirmed/rejected
edge is present, because in both cases the graph's own `edges.status` is
somewhere a human decision can genuinely be recorded, and `researchos confirm`
does not care which node type or extractor an edge belongs to. What still
differs, deliberately, is *scope*: claims's own check and delete are both
scoped to edges its own extractor produced (`extractor="claims"`), so a
hypothetical second claims-like extractor's candidates would not interfere
with each other's bookkeeping; attempts' check and delete both span every
extractor touching the node, because `nodes.id` is a foreign key
`edges.src`/`dst` reference with no cascade (`db.delete_node`'s own
docstring) -- an unconfirmed `uses` edge left in place by an earlier
`--check` run would otherwise make the node's own delete raise
`sqlite3.IntegrityError`, and there is no reason to scope the
*human-decision protection* narrowly just because claims happens to scope
its own extractor-local bookkeeping that way. `human_fields` resync
(above) still behaves oppositely from `edges.status` on purpose, unaffected
by either fix: an attempt's `human_fields` must always be resynced from the
source file, because the file, not the graph, is the sole record of a
verdict; an edge's `status`, and now an attempt node carrying a
confirmed/rejected edge, must never be overwritten or deleted by
re-ingestion, because the graph is the sole record of *that* decision,
wherever in the graph it happens to be attached. (Superseded by 9.1: the
graph is no longer that record -- the judgment ledger is -- so claims and
attempts cleanup now delete such orphans like any other; 9.6 says how the
judgment is shown.)

Edge types, grouped by the layer that produces them:

| Edge | Meaning | Layer |
| --- | --- | --- |
| `commit --implements--> experiment` | a run recorded this commit's SHA | deterministic |
| `experiment --produces--> figure` | a run artifact matches a tracked image | deterministic |
| `commit --generates--> figure` | a `savefig()` call writes this image | deterministic |
| `section --includes--> figure` | `\includegraphics` in that section | deterministic |
| `section --cites--> reference` | `\cite` and its natbib/biblatex variants | deterministic |
| `* --authored_by--> contributor` | git author, run owner | deterministic |
| `claim --backed_by--> experiment` | a number in the prose matches a run metric | deterministic candidate, pending judgement |
| `attempt --uses--> commit` | the last commit to touch a script file the attempt depends on | deterministic |
| `figure --supports--> section` | a figure substantiates an argument | semantic, planned |

`backed_by` candidates are generated deterministically by `rce.ingest.claims`
(no model involved): a claim's printed number is matched against experiment
metrics by rounding both to the precision the claim itself was printed with,
never a tuned tolerance. Every candidate is written `status=pending` — the
extractor never confirms or rejects one; that judgement is left to the
semantic layer or a human via the confirmation queue.

Confidence on a `backed_by` candidate is always `1.0`, whether a claim
matches one experiment or several. Precision-matching is deterministic and
exact — the rounding rule either finds a candidate or it doesn't, with no
tunable tolerance — so confidence expresses the reliability of the match
*rule*, not which candidate is the correct one. That second question is a
judgement call, and judgement belongs to the semantic layer or a human, never
the extractor; diluting confidence to `1/N` across `N` candidates used to
smuggle a guess about "which one" into a decimal that looked precise but
wasn't. Instead, ambiguity is recorded plainly: every candidate edge's
evidence carries `candidate_count`, the count of (experiment, metric) pairs
that claim matched — not the count of distinct experiments, since one
experiment can contribute more than one matching metric to the same claim
— so a reviewer sees "3 candidates" rather than inferring it from a
confidence of `0.33`. `candidate_count` is a sibling of `occurrences`
(`evidence.candidate_count`, not nested inside any one occurrence) and is
overwritten to the latest total on every re-ingest, never accumulated: it
describes the whole claim across every experiment, not the one occurrence
just written, and folding it into an occurrence's own identity instead
used to be a real bug — incrementally adding new matching experiments
changed the count on every re-ingest, which made an *already-existing*
edge's unchanged occurrence look "new" each time and grew a single edge's
occurrence list without bound.

`supports` still has no extractor and exists in the schema only; it belongs
to a future extension of the semantic layer described in section 7.
`backed_by` candidates, meanwhile, are reviewable today: `researchos judge` (S2)
attaches a model's opinion to each pending candidate as
`evidence.semantic_review` — see "Machine annotation vs. human judgement"
below. This is annotation on top of the existing `backed_by` edge, not a
new edge type, so it needed no change to the table above.

### Machine annotation vs. human judgement

`researchos judge` (`rce.semantic.judge`) is a second machine that looks at the
graph, not a shortcut around "humans own judgement." Section 0's rule does
not carve out an exception for a model just because its guess is often
better than the deterministic rounding-coincidence match it is reviewing —
a wrong guess dressed up as fluent prose is exactly the failure mode "never
guess" exists to prevent. Concretely, this is enforced two ways:

1. **A narrow write path.** `db.set_edge_semantic_review` is the only
   function `rce.semantic.judge` calls to persist anything. It writes
   `evidence.semantic_review` (`related`, `reason`, `better_match`, the
   `metric`/`metric_value` of the one occurrence actually reviewed, plus
   `model`/`reviewed_at`/`run_id` for traceability) and nothing else on the
   row — not `status`, not `confidence`. It does not call `upsert_edge` or
   `set_edge_status` itself, so there is no code path by which a judge run
   could move an edge to `confirmed` or `rejected`, however confident the
   model's own `related: true` sounds. A `pending` candidate reviewed by
   the judge is still `pending` afterward; a human decides via `researchos
   confirm`, same as any other candidate.
2. **A verifier the model cannot talk its way past.** `better_match` names a
   param or metric the model claims exists on the *same* experiment run —
   information only available at review time, so no static JSON-Schema can
   check it. `rce.semantic.judge`'s own verifier checks `better_match`
   against that run's actual param/metric names before anything is stored;
   a name that is not literally present is discarded as a hallucination,
   logged, and never written (`related`/`reason` are kept regardless — one
   field failing verification does not discard the whole review).
3. **Trim the response, never the judgement.** `reason` is meant to be one
   sentence; a model that instead writes several is not treated as a
   validation failure. `reason` is truncated at 300 characters (plus a 4-character marker, so a stored value can be 304) — truncated to
   that length with a trailing `" ..."` marker and the truncation logged
   (never silent) — while `related` and a verified `better_match` are still
   validated and stored normally. Discarding the whole response over a
   wordier-than-asked-for `reason` would throw away the one thing a human
   reviewer actually wants (the model's `related`/`better_match` verdict)
   over nothing worse than a run-on sentence.

The result reads like a second opinion, not a verdict: "this candidate is
probably a numeric coincidence, and `quantization` on this same run looks
like a better fit" is exactly the kind of note a human reviewer wants
sitting next to a `pending` edge before they decide — and exactly the kind
of note that must never quietly become the decision itself.

**Known limitation.** A `backed_by` edge carries one occurrence per distinct
metric of *that* experiment matching the claim, so an edge has more than one
occurrence only when a single experiment logs several metrics that all round
to the claim's printed value. `researchos judge` reviews exactly one occurrence per
edge (the most recently written one) and records which one it saw in
`semantic_review.metric`/`.metric_value`; any other occurrence on that edge
is logged as skipped, never itself sent to the model.

Note that `evidence.candidate_count` is *not* what triggers this. It is an
edge-level, claim-global figure — how many (experiment, metric) pairs the
claim matched across the whole graph — so the ordinary case of a claim
matching twenty different experiments gives every one of those edges
`candidate_count = 20` and exactly one occurrence, and each is reviewed in
full. Only the several-metrics-on-one-experiment case is partially reviewed.

### Attempt consistency checks (task A3)

The attempt timeline (above) is not a place Research OS hands a judgement back to
the researcher. The researcher already wrote the judgement — `verdict` and
`result`, in their own words, next to the row. What the researcher cannot
easily do by hand is notice that a row's step reference no longer resolves
to a file, that a script a row depends on was edited after the verdict was
recorded, or that a dead variable's name has quietly resurfaced in a row
that is still marked alive. `rce.consistency` (`researchos attempts --check`) is
three narrow, fully deterministic checks for exactly that — no model
anywhere in this module, same "code beats models wherever code suffices"
rule as every other extractor. Each reads `attempt` nodes already written by
`rce.ingest.attempts` rather than re-parsing the source Markdown itself, so
`researchos attempts --check` always re-ingests first.

1. **Broken reference.** An attempt's step reference (`attrs.step_refs`,
   resolved into `attrs.step_files`/`attrs.step_files_broken` at ingest
   time — see the `attempt` node description above) that matched no file
   under `steps_dir`. The finding also reports the nearest step numbers
   that DO exist in the directory, on either side of the missing one, so a
   human can tell a rename from an actual deletion at a glance instead of
   opening the folder.
2. **Stale verdict.** An attempt whose recorded date is earlier than the
   last time one of its resolved dependency scripts was touched — the
   verdict was written, then the code changed again. The script's
   last-touch time comes from `rce.ingest.git.read_commits` (reused, not
   reimplemented): the most recent commit whose changed-file list includes
   that script. A script the check cannot find in git history at all —
   untracked, or the project has no git repository — falls back to the
   file's own mtime, and the finding says so explicitly (`basis="git"` vs.
   `basis="mtime"`) rather than presenting both as equally reliable. Every
   script resolved via git also gets an `attempt --uses--> commit` edge
   (the edge type migration 0002 added), evidence `{"script",
   "commit_time"}` — a deterministic fact worth keeping regardless of
   whether that particular script triggered a finding. A mtime fallback has
   no commit to point at, so no edge is written for that case.

   **Date parsing.** The comparison needs the attempt's own date column
   parsed into a definite date. A real hand-written timeline's dates are
   rarely a plain `YYYY-MM-DD` — a range ("07-08~09"), an upper bound
   ("≤07-07"), a bare month-day with no year ("07-26"), a trailing
   annotation ("07-10 冻结"). Inferring the missing year or picking a bound
   of a range would be exactly the fabrication section 0 forbids, so none
   of these parse *unless* the human declares `date_year` in
   `.rce/attempts.toml` (below) — a human stating "this table's dates are
   all in year Y", never rce inferring it. Once `date_year` is declared,
   `rce.consistency._parse_attempt_date` accepts, in addition to a full
   `YYYY-MM-DD`:
     - `MM-DD` (e.g. `"07-26"`) — combined with `date_year`.
     - `<=MM-DD` / `≤MM-DD` (e.g. `"≤07-07"`) — the prefix is stripped and
       the date after it used as-is; it marks the true date as possibly
       earlier, but does not change which date this check takes.
     - `MM-DD~DD` or `MM-DD~MM-DD` (e.g. `"07-08~09"`, a range spanning a
       month boundary) — the LATER end is taken, because a verdict is only
       safely dated once the attempt is actually finished, i.e. the end of
       the range, never its start.
     - Trailing free text after the date (e.g. `"07-10 冻结"`) is ignored.

   An attempt whose date still does not parse (bare `MM-DD` with no
   `date_year` configured, or genuinely unparseable text) is skipped from
   this one check individually, logged, and counted in the check's own
   coverage figures — never guessed at, and never silently folded into "0
   findings" (see "Honest coverage reporting" below).
3. **Revived dead variable.** A *living* attempt (its verdict contains one
   of the configured `active_verdicts` markers) whose description or
   variable list mentions one of the configured `dead_variables` entries.
   Matching is a case-insensitive substring test, deliberately
   conservative, and every finding carries the actual matched field text
   verbatim so a human judges it themselves — this check only ever reports
   a hit, it never files, dismisses, or otherwise disposes of one.

Each check is independently gated on its own config prerequisite, and a
missing prerequisite is reported as *skipped*, never silently folded into
"no findings" — the two look identical in a bug but must never look
identical in this tool's output. Checks 1 and 2 need `steps_dir`; check 2
also optionally reads `date_year` (above); check 3 needs both
`dead_variables` and `active_verdicts` declared. All of these are optional
top-level keys in the same `.rce/attempts.toml` task A2 already introduced
— no second config file. They **must** appear before the `[columns]` table
in the file: TOML nests any bare key written after a `[table]` header into
that table, so a list or int written after `[columns]` is silently read as
e.g. `columns.dead_variables` instead and never seen by `load_config` at
all — a real bug this project's own `SAMPLE_CONFIG` template shipped with
until it was caught against a real 23-row timeline (see `rce.ingest.
attempts` for the fix and its regression test).

```toml
steps_dir = "复现包_分步"                    # optional; gates checks 1 and 2
dead_variables = ["信息熵", "lnRate 配置比例"]  # optional; gates check 3, paired with:
active_verdicts = ["✅", "🕒"]                # which verdict markers count as "alive"
date_year = 2026                             # optional; gates check 2's looser date forms above
```

`dead_variables`/`active_verdicts` default to unset (not `[]`) when absent,
so an explicitly declared empty list (a valid, if pointless, configuration)
is never confused with "never declared, skip and say why." `date_year`
similarly defaults to unset, in which case check 2 only ever accepts a
plain `YYYY-MM-DD`, exactly as before this key existed.

**Honest coverage reporting.** `CheckResult` records not just findings but
`total`/`checked`: how many attempts the check considered, and how many it
actually evaluated (the rest skipped per-item, per above — currently only
check 2 has a per-item skip; checks 1 and 3 always have `checked == total`
since neither has a comparable "this one item is unparseable" case). A
check where `checked` is far below `total` — in the extreme, `checked ==
0` — must never render as "OK, no issues found": a real 23-row timeline
with no `date_year` configured has every single date fail to parse, and
printing that as a clean bill of health hides that the check ran and
verified nothing at all. `researchos attempts --check`'s report always states the
coverage figure (`rce.cli._print_consistency_report`), and explicitly flags
zero coverage as not a clean result, rather than only ever printing
`findings`.

`researchos attempts` with no `--check` lists what is registered — `#`, date,
verdict, and how many step files a row resolved to — the same non-judging
posture as the rest of this section. `--check` runs all three and exits
non-zero if any of them reports a finding; a skipped check never affects
the exit code, since a missing config declaration is a configuration gap,
not itself "a problem found" in the project.

## Section 5 — Connection keys

The deterministic layer joins objects on evidence that already exists in the
project. Nothing here requires metadata entry:

1. `\includegraphics{path}` against tracked image files. Extensionless
   includes are resolved by trying known image extensions in pdflatex's
   search order.
2. `\cite{key}` — and `\citep`, `\citet`, `\citealp`, `\parencite`,
   `\textcite`, `\autocite` — against `.bib` entries, matched
   case-insensitively.
3. The git commit SHA that MLflow and W&B record with each run.
4. Run artifact filenames against tracked images, matched by basename and
   only when the match is unique.
5. String literals in `savefig()` calls, including same-file module-level
   string constants recovered by constant folding. A name that is bound more
   than once anywhere in the file is never folded, and a file containing
   `from x import *` gives up folding entirely — the set of names such an
   import binds is not statically knowable.
6. `\label` and `\ref` within a document.
7. Numbers in prose — percent/fraction/plain forms with a decimal point,
   `\SI{}{\percent}`, and bare `$...$` math-mode numbers — against experiment
   metrics. A candidate `backed_by` edge is written (`status=pending`) when
   both round to the same value at the precision the claim itself was
   printed with; the final confirm/reject judgement is deferred to the
   semantic layer or a human.

Every one of these skips and logs rather than guessing when the key does not
resolve cleanly.

**Known limitation (connector 7).** A number immediately followed by a
hyphen and a letter (`1.58-bit`, `3-fold`) is always treated as a compound
modifier and skipped, never scanned as a claim — a deterministic syntax
rule, not a tuned threshold. It has no false-claim direction, only a
false-skip one: a genuine claim shaped the same way (`a 2.3-point
improvement`) is skipped too. Section 0's "never guess" accepts that cost.
For the same reason, a `\begin{env}`'s own argument groups (e.g. the layout
length after `\begin{subfigure}`, or a `\begin{tabular}` column spec), the
URL argument of `\url`/`\href`, and a bare `https://...` typed directly in
prose are all blanked before number-scanning — a DOI or arXiv id's digits
are not a claim about the paper's own results.

**Known limitation (connector 7, no local run store).** A paper repository
that never has a local MLflow/W&B run history — the normal case for a
`showyourwork`-style repository, and for most already-published LaTeX
repositories in general — gets zero `experiment:` nodes. With no experiment
metrics to compare against, every quantitative claim's `backed_by` candidate
set is structurally empty, not merely small: the claim node is still
created, but no candidate edge of any kind follows. This is not a bug. The
deterministic layer only connects evidence that already exists in the
project; a repository with no recorded runs has no run evidence to connect,
and Research OS does not invent a placeholder experiment to compare against. A user
comparing Research OS's output across repositories should expect an all-claims,
no-`backed_by` result from this class of repo rather than mistake it for a
broken extractor.

## Section 7 — Roadmap

**Now.** The deterministic layer across git, LaTeX/BibTeX, `savefig()` static
analysis, MLflow's local store, and W&B's public API; deterministic
`claim --backed_by--> experiment` candidate generation from numbers in prose
(pending status, no model); a command line that ingests and traces
provenance; an optional MCP server. The semantic layer's first slice
(`researchos judge`, S2): a local, vendor-neutral OpenAI-compatible client
(`rce.semantic.backend`, off by default, no third-party dependency to
install) reviews pending `backed_by` candidates and writes its opinion into
`evidence.semantic_review` — related or not, a one-sentence reason, and an
optional `better_match` naming a param or metric on that *same* experiment
run the deterministic matcher never considered. Every `better_match` is
verified against the run's actual param/metric names before being stored; a
name that doesn't exist there is discarded as a hallucination, logged, and
never written. `status` is untouched either way — see "Machine annotation
vs. human judgement" below.

Also now, though it predates this roadmap section's own last update: the W2
data-lineage extractor (`rce.ingest.dataflow`) statically scans `.py`/`.R`/
`.Rmd` sources for recognized read/write calls (`pd.read_csv`/`to_csv`,
`open()`, `read.csv`/`write.csv`, `ggsave`, ...) into
`script --reads/writes--> dataset|figure` edges — no model, no git required
(unlike `rce.ingest.pyfig`'s `generates` edge, whose src is resolved via
`git blame`). `researchos lineage` (W4) is the read-only report surface over that
graph, and the one new user-facing exit this round: it writes nothing and
re-parses no source file, only querying edges `researchos ingest` already wrote.
Four blocks, each scoped precisely rather than uniformly: **orphan inputs**
(a `dataset` read by a script but written by none — "where did this input
data actually come from", scoped to `dataset` only, since a `figure` is
normally an output, never an input a script reads back) and **duplicate
copies** (a read `dataset` whose basename also exists at other paths in the
project — "which of the N copies did the script actually read", again
`dataset`-only for the same reason) sit next to **lineage chains** (a
`dataset` *or* `figure` with both a writer and a reader — a produced-and-
consumed plot is exactly as traceable a chain link as a produced-and-
consumed dataset, so `figure` is included here) and **broken links** (any
`reads`/`writes` occurrence whose evidence carries `missing: true`,
regardless of node type — a script pointing at a file that genuinely isn't
on disk is the same finding whether that file would have been a dataset or
a figure). `--orphans` narrows the report to the first block alone; `--json`
gives the same four blocks as structured output. The command exits 1 if
either orphan inputs or broken links were found (the two blocks that
represent an actual gap) and 0 otherwise — chains and duplicates are
informational and never affect the exit code. An empty block is simply
omitted; only when every block is genuinely empty does the report say so
explicitly, stating what was scanned (script/edge/target counts) and that
none of it matched any of the four patterns — never a bare success line
with no content behind it, the same "a missing finding is a normal outcome,
but it must be stated, not indistinguishable from nothing having run"
posture Section 0 already asks of every other check in this document (see
e.g. `researchos attempts --check`'s own coverage reporting above).

**Next.** Proposing `supports` edges (a figure substantiates an argument)
with confidence scores, every proposal verified against the graph before it
is stored and queued for human confirmation when uncertain. Still optional
and **local by default, not local by construction**: `rce.semantic.backend`
talks to whatever OpenAI-compatible server `RCE_LLM_BASE_URL` (or the
`base_url` constructor argument) names, and that is a plain configuration
value, not something the code structurally confines to this machine. The
default points at a local server, and every `researchos judge` run whose base URL's
hostname is not `localhost`/`127.0.0.1`/`::1`/`*.local` prints a prominent
warning before sending that run's experiment params and metric names
anywhere, so pointing the semantic layer at a remote endpoint is possible
but never silent. This is a hostname-*shape* check (string comparison
against a short allow-list), not DNS resolution or a network reachability
probe — Research OS never resolves or contacts the address to decide whether to
warn. **Known limitation:** the `*.local` exemption trusts the suffix by
name only; a hostname that merely ends in `.local` (whether or not it is
actually mDNS/LAN-only) is treated as local and, like `localhost`, produces no warning at all, since a hostname-shape check has no way to verify what a name
actually resolves to or where it's reachable from.

**Later.** A local read-only web view over the same graph, and periodic
digests of what changed, what went stale, and what is waiting for review.

## Interfaces

The command line is complete on its own: ingest, inspect, and trace
provenance with no assistant involved. `researchos judge` is the one subcommand
that talks to a model, and only when invoked — every other subcommand
(`init`/`ingest`/`status`/`query`/`trace`/`confirm`) works identically
whether or not a local model server exists.

An optional MCP server exposes the same graph to any MCP-capable client,
including open-source clients and ones backed by local models. MCP is a
protocol, not a vendor — the engine depends on no particular AI product, and
the default install pulls none in.

## Section 8 — The app: node canvas and native shell (task V4)

Section 7's "later" web view exists (tasks V1–V3: decision tree, lineage,
multi-project, auto-refresh, writing attempt rows back into the map file).
The researcher's verdict on it was exact: *"it still isn't an app"* — and,
asked what an app would be, two answers: **not a browser tab**, and **draw
the graph in the app, ComfyUI-style, and let me add the mappings between
figures, code and datasets myself.** This section is the design for both.
Every UI decision below is binding for whoever implements it; the visual
system is the one already carried by `src/rce/webapp/app.html` (paper /
ink / clay / ochre / olive / gray tokens, Songti for the brand mark and
headings, PingFang for UI, a mono face for paths). Nothing here introduces
a new palette, a new font, or a third-party dependency.

### 8.0 Why a canvas, and why ComfyUI is the right reference

The decision tree answers "what did I try and what did I decide"; the
lineage report answers "who wrote this file". Neither answers the question
a researcher asks while *building*: **which dataset feeds which script
produces which figure — and where is the link I know exists but the
machine could not see?** That is a graph question, and the honest UI for a
graph the user is expected to *edit* is a node editor: boxes with typed
sockets, links drawn between sockets, an infinite canvas you pan and zoom.
ComfyUI is the reference because its conventions are already muscle memory
for anyone who has used a node editor: drag from an output socket to an
input socket to connect; drag a node's title to move it; drag empty canvas
to pan; wheel to zoom; only *compatible* sockets accept a link. We adopt
those conventions wholesale and change only what the domain demands.

What we deliberately do **not** copy: ComfyUI's dark theme (this is a paper
tool), node creation from a blank search box (Research OS never invents research
objects — every node is a file that exists in the project), and executing
the graph (Research OS observes a pipeline; it does not run one).

### 8.1 Nodes: the object model on the canvas

Only three node types appear in v1 — exactly the three the researcher
named — plus attempts as *frames*, not nodes:

| Canvas node | Graph node type | Left sockets (inputs) | Right sockets (outputs) |
|---|---|---|---|
| 数据集 | `dataset` | 来源 (a script that `writes` it) | 数据 (feeds a script's 读取) |
| 脚本 | `script` | 读取 (datasets it `reads`) | 写出 (datasets it `writes`) · 生成 (figures it `generates`/`writes`) |
| 图表 | `figure` | 生成自 (the script that produced it) | — |

Socket compatibility is the whole grammar of what a human may draw:
`数据集.数据 → 脚本.读取` creates a `reads` edge; `脚本.写出 → 数据集.来源`
creates `writes`; `脚本.生成 → 图表.生成自` creates `generates`. Any other
pairing is refused at drop time with a one-line explanation in product
language ("只能把数据集接到脚本的「读取」插口"). This is how the canvas stays
honest: the user can only assert relationships the object model already
knows how to store, and every link on screen is a real edge type.

**Attempts are frames, not nodes.** An attempt row (`attempt` node) owns
step scripts (`attrs.step_files`). On the canvas an attempt is drawn as a
ComfyUI-style *group frame*: a dashed, lightly tinted rectangle behind its
scripts, titled `#16 · TopicShift→波动+采用` in the attempt's verdict color
(the same badge colors the decision tree already uses). Frames are
scope, not objects — you cannot connect a socket to a frame.

**Ghost nodes.** An attempt's `step_files` routinely includes outputs no
extractor produced a node for (a knitted `.pdf`, a `.docx`) — in the
researcher's real project every `17-….pdf`/`18-….pdf` is such a file. Those
appear on the canvas as *ghost nodes*: dashed border, muted title, tag
「尚未入图」, typed by extension (image/pdf → 图表; csv/dta/parquet/xlsx/rds
/feather → 数据集; py/R/Rmd/jl → 脚本 — the same extension classification
`rce.ingest.dataflow` already applies, a deterministic classification, not
an inference about relationships). The moment the user connects a ghost, it
becomes a real node (the mapping ingest upserts it, see 8.5). This is the
only way a node comes into existence from the canvas, and it is exactly the
case the researcher asked for: "my figure is not linked to my code — let me
link it."

### 8.2 Visual language

*Node anatomy.* A rounded card (radius 6px) on `--paper-alt`, 1px
`--line-strong` border, soft shadow only while dragging. A 26px title bar
carries a 3px left rule in the type color and a small mono type label
(`数据集` / `脚本` / `图表`); the body shows the file's basename in the UI
face at 13px and its directory in the mono face at 11px `--ink-soft`,
truncated from the left so the meaningful tail survives. Sockets are 10px
circles sitting *on* the card edge, labeled inside the card in 11px mono.
Type colors: 数据集 `--ochre` (raw material), 脚本 `--olive` (the work),
图表 `--ink` (the product). Clay is **not** a type color — in this app clay
means "a human did this" or "something is wrong", and we keep it that way.

*Node states.* Selected: 2px `--clay` border. Hovered: border to
`--line-strong` at full opacity. Missing on disk (the graph knows it, the
filesystem does not): dashed border + tag 「文件不存在」 in clay. Orphan
input (a dataset no script writes — the lineage report's own definition):
a clay dot on its 来源 socket, the same red dot the decision tree uses.
Ghost: see 8.1.

*Links.* Cubic Béziers with horizontal tangents (ComfyUI's curve), drawn
under nodes. **Machine-extracted edges** (any extractor other than
`mapping`): 1.5px `--ink` at 45% opacity, solid; `pending` status → dashed
(a candidate awaiting `researchos confirm`); `rejected` → not drawn. **Human
mappings** (extractor `mapping`, 8.5): 2.25px `--clay`, solid, with a 6px
clay dot at the curve's midpoint. The two must never be confusable at a
glance — that is Section 4's "machine annotation vs. human judgement" rule
made visible. Hover shows a small card: for a machine link 「dataflow 提取 ·
第 15 行」 (extractor + evidence line when present), for a human link 「你于
2026-09-06 标注 · <note>」. A link whose endpoints are both selected/hovered
brightens to full opacity; while dragging a new link, compatible target
sockets pulse with a `--clay-soft` halo and incompatible ones dim.

*Canvas.* `--paper` background with a 24px dot grid in `--line` (dots, not
lines — lines fight the Béziers). A quiet toolbar floats top-left inside
the canvas: scope selector (8.7), search field 「查找节点…」, and buttons
适应全部 · 100% · ＋ · －. Bottom-left: a zoom readout in mono (`72%`).
No minimap in v1.

### 8.3 Interaction

| Gesture | Effect |
|---|---|
| Drag empty canvas · two-finger scroll · Space+drag | Pan |
| ⌘/Ctrl + wheel · pinch | Zoom 25%–250% about the cursor |
| Drag node title or body | Move (8px grid snap while ⇧ held); position persisted, debounced 400ms |
| Drag from an output socket | Start a link; compatible inputs highlight; drop on one → confirm popover (below); drop elsewhere → cancel, nothing happens |
| Click node | Select; opens the existing slide-out panel (file preview, Open, Reveal in Finder) |
| Double-click node | Open with default application (existing `POST /api/open`) |
| Click link | Select it; the hover card stays pinned |
| Right-click human link · ⌫ with a human link selected | 「删除标注」 with confirm |
| Right-click machine link | 「标记为错误提取」→ `set_edge_status(..., "rejected")` — the one human-only status path Section 4 already allows, reused rather than a second mechanism |
| Esc | Clear selection / cancel a link drag |
| F | Fit all visible nodes |

The **confirm popover** for a new link is the canvas's only write dialog:
one line stating the assertion in product language (`17-叙事更替与汇率波动.Rmd
生成 → 17-叙事更替与汇率波动.pdf`), an optional 备注 field, buttons 确认标注 /
取消. Confirm writes the mapping (8.5); the link appears as a human link
once the re-ingest lands (the existing generation poll does the
re-render; the UI draws it optimistically in the meantime and reverts with
an error chip if the write fails). Exactly the same "preview → explicit
confirm → file write → graph follows" shape as writing an attempt row.

### 8.4 Layout

**Auto-layout is a suggestion; an arrangement is a memory, and each view
keeps its own.** Until the researcher moves something, a view (a scope,
8.7) is laid out fresh each time it is drawn, among the cards visible in
that view only. The first time they move a card in a view, the whole view
is *pinned*: every visible card's position at that moment is saved for
that view, and from then on nothing in it moves unless they move it — the
ComfyUI contract. A card that appears later in a pinned view (a new script
after a re-ingest, a ghost) is placed by the steps below, clear of the
pinned cards, and joins the arrangement the next time anything is moved.
The same card may sit in different places in two views — an attempt's view
and 全部 are two pictures of one project, like two diagrams of one model.

(Two earlier rules failed on the researcher's real project and are
recorded so they are not tried again. *Global positions with a global
layout* scattered the ten cards of the current attempt across 4,000px, and
the default view opened as dust at 25% zoom. *Global positions with a
per-view layout, only moved cards saved* meant that nudging one card pulled
it out of its pipeline: on the next layout its neighbours re-packed without
it and the pipeline came apart.)

The layout of unpinned cards, client-side and with no library:

1. **Islands.** Split the cards into connected components (links taken as
   undirected). A research project is many small pipelines, not one deep
   graph; each pipeline is laid out on its own. For this purpose only, a
   card with no links counts as linked to the step script of its own
   attempt that shares its numeric step prefix (`17-….pdf` with
   `17-….Rmd`), so an output waiting to be connected sits beside the
   script that most plausibly made it — placement, never an asserted edge.
2. **Layer** within an island = longest path from a source. A dataset
   nobody writes is layer 0; a script is 1 + max(layer of the datasets it
   reads); a dataset or figure some script writes is that script's layer +
   1; a step-prefix companion sits one layer right of its script. (This is
   what makes the researcher's own pipeline read correctly: `16.py` writes
   `topicshift_monthly.csv`, which `17.Rmd` and `18.Rmd` read, so 17/18
   sit two columns right of 16 rather than beside it.) Cycles, should a
   pipeline contain one, are broken at the edge that closes them and that
   edge is drawn dashed in clay with a hover note 「检测到循环」.
3. **Order within a layer** by one barycenter pass (mean y of already
   placed neighbors), ties broken by the numeric step prefix of the path
   so step order survives. Columns 320px apart, rows packed with 24px
   gaps. A layer taller than 12 cards wraps into balanced side-by-side
   sub-columns of at most 12 (a 220px gap between them, inside the same
   layer band, which widens to hold them) so no island becomes a tower.
4. **Loose cards** — cards with no links and no step-prefix script — are
   not islands of one. They are gathered into a single grid block titled
   「未连线」 (quiet mono caption, `--ink-soft`), ordered by type then path,
   and shaped like the page rather than a strip: at least 4 columns, more
   when needed to bring the block toward 1.6:1.
5. **Packing.** Islands are placed in rows, left to right, 96px apart,
   wrapping to a new row at a target width. The target is at least W =
   max(widest island, √(1.6 × total island area)); the packer tries wider
   targets up to 2.5 × W and keeps the page closest to 1.6:1 (W alone
   leaves ragged rows and a page taller than it is wide). Order: the
   island holding the current attempt's scripts first, then by card count
   descending, ties by the smallest step prefix; 「未连线」 last. Blocks
   are packed clear of any pinned card.

「重新排列」 in the toolbar overflow forgets *this view's* arrangement and
therefore asks first (「将丢弃你在这个视图里摆放的位置」). The camera: on
entering a view with no saved viewport it fits all — the scope the
researcher opens on must be readable without touching anything — and until
they pan or zoom, it re-fits when the window is resized. Fit-all never
moves cards. When an unpinned view re-lays itself out because its links
changed (a link was just confirmed), the camera holds the card the
researcher was working on at the same point on screen, so the canvas moves
under their hand rather than away from it.

### 8.5 Human mappings are a file: `.rce/mappings.toml`

Section 4's doctrine — the researcher's own file is the truth and the graph
resyncs from it — applies to hand-drawn links exactly as it applies to
attempt verdicts. A link the user draws is therefore **appended to
`.rce/mappings.toml`**, a file they can read, diff, and commit; the graph
edge is derived from it by an ingest, never written directly by the UI.

```toml
# 手工标注的映射。Research OS 只记录它能读到的；它读不到的，由你在这里补上。
# 此文件是唯一真相：图谱里的人工连线从它派生，删掉 graph.db 也不会丢。
[[mapping]]
from = "复现包_分步/17-叙事更替与汇率波动.Rmd"
to   = "复现包_分步/17-叙事更替与汇率波动.pdf"
type = "generates"                 # reads | writes | generates
note = "knitr 渲染产出"             # 可选
date = "2026-09-06"                # 标注日期（本地日期，写入时填）
```

Rules:

- `from`/`to` are project-relative paths, confined to the project root by
  the same resolve-then-`relative_to` check every other path takes (the
  write path is never less confined than the read path — a lesson already
  paid for in the V3 review). `type` must be one of the three; the
  (`from` type, `to` type, `type`) triple must satisfy the socket grammar
  in 8.1 or the ingest refuses that entry with a line number and keeps the
  others.
- The ingest (`researchos mappings`, also run by the watcher — the file joins the
  watch set) upserts missing endpoint nodes (the ghost → real transition,
  typed by extension) and upserts each edge with `extractor = "mapping"`
  and status `confirmed` — the human wrote the file; the ingest is their
  hand, the same way `researchos attempts` writes `human_fields.verdict` from the
  human's own table. No machine extractor may write `extractor =
  "mapping"`, and no machine re-ingest may remove or downgrade a `mapping`
  edge; the only thing that removes one is its entry disappearing from the
  file (resync-from-source, exactly as attempt orphans in Section 4).
- Writes from the canvas (add, delete) go through the same discipline as
  the map file: backup to `.rce/backups/`, atomic replace with fsync,
  re-ingest under the watcher's ingest lock, generation bump. The
  TOML is emitted by a small fixed-schema writer (stdlib has no TOML
  writer; the schema above is all it ever needs to emit) that preserves
  the header comment and entry order and appends new entries at the end.
- Duplicate assertions (same `from`, `to`, `type`) are refused at write
  time with 「这条映射已存在」; a mapping that contradicts a machine edge is
  *allowed* — the human is asserting the machine missed nothing/was wrong,
  and both lines will show, distinguishable by style.

### 8.6 Layout state is not truth: `canvas.json`

Arrangements and viewports are UI state — machine-managed JSON kept beside
the graph (8.10), safe to delete (everything re-lays out), not something
the researcher is expected to read. One entry per view:
`{"views": {"all": {"positions": {"script:复现包_分步/16-….py": [x, y], …},
"viewport": {"x": …, "y": …, "zoom": …}}, "attempt:…#16": {…}}}`. Written
atomically by a debounced `POST /api/canvas/layout` that names its scope;
a scope the project does not have is refused, so the file cannot grow keys
a page invents. Never backed up (there is nothing irreplaceable in it). A
missing, corrupt, or older-format file degrades to "nothing saved", never
to an error the user sees.

### 8.7 Scope and search

The researcher's project has dozens of scripts and datasets; the whole
graph at once is a hairball. The toolbar's scope selector defaults to
**the current attempt** (the ✅ row when there is exactly one; else the
most recent) and offers 全部 plus every attempt. Scoping to an attempt
shows its step scripts, every dataset/figure they touch, and one hop
further along `writes → reads` chains so upstream generators stay visible;
its frame is drawn; other attempts' nodes are simply absent, not dimmed.
Each view keeps its own arrangement and viewport (8.4, 8.6). Frames are
drawn in attempt views only: in 全部 an attempt's scripts are spread
through a larger pipeline and a bounding frame would swallow cards that
are not its own. Switching scope clears the selection and any pinned link
card — they belong to the view that was left. Search
(「查找节点…」) highlights matching nodes and dims the rest without
changing scope; Enter fits the camera to the matches.

### 8.8 Product language

All UI copy is Chinese product language — not only what V4 adds. (English is the second interface language since V9; see Section 13.) The
three tabs read 「决策树」「血缘」「画布」, and the English left over from
V1–V3 is translated in the same pass. The brand mark stays `Research OS`; its
subtitle becomes 「研究脉络」 (the old "decision tree & lineage" named two
of three views, in the wrong language). What stays as it is: file names
and paths, the researcher's own text (attempt titles, verdicts, column
names), and raw engine error strings — which remain English and live on
hover titles, behind a Chinese framing (「无法标注：…」). Nothing on the
canvas says "node", "edge", or "socket" to the user — it says 数据集, 脚本,
图表, 连线, 插口, 标注.

Binding glossary (one term per concept, everywhere it appears):

| Was | Is |
|---|---|
| Reads / READS | 读取 |
| Writes / WRITES | 写出 |
| No recorded reads or writes. | 没有读到读写记录。 |
| Orphan inputs | 无来源输入 |
| READ BY | 被这些脚本读取 |
| WRITTEN BY | 由这些脚本写出 |
| Lineage chains | 血缘链 |
| Broken links | 断链 |
| (reads, not found) / (writes, not found) | （读取，文件不存在）/（写出，文件不存在） |
| Duplicate copies | 同名拷贝 |
| OTHER COPIES | 其它拷贝 |
| Open | 打开 |
| Reveal in Finder | 在 Finder 中显示 |
| Close | 关闭 |
| Loading… | 载入中… |

Anything not in the table is translated in the same register — short,
concrete, no jargon — and the same English source always gets the same
Chinese.

**Errors.** A hover title is acceptable for a passive chip; it is not
acceptable as the only place an error lives when that error has just
blocked something the researcher tried to do (saving an attempt row,
confirming a link, switching project, opening a file, stopping the
service). Those show, in order: a specific Chinese sentence when the
engine names the cause with a machine-readable code (the attempt form's
own cases — a number that already exists, a number that is not in the
table, an invisible line-break character in a cell, a table that can no
longer be found — each get one); otherwise the Chinese framing; and in
both cases a 「详情」 toggle that reveals the engine's raw text inline, in
small mono `--ink-soft`. One click, never a hover. The command line stays English (it is a developer surface, and
its messages are quoted in docs and tests).

### 8.9 The native shell: `Research OS.app`

"Not a browser tab" means: a real macOS application window — Dock icon,
menu bar, ⌘-shortcuts, no address bar, no browser chrome. The engine stays
Python and the interface stays `app.html`; the shell is a native window
that shows it, the same architecture Obsidian, Notion and VS Code use, and
the one that keeps every feature written once. The researcher chose this
over a SwiftUI rewrite knowingly.

- **Build.** A single Swift source file (AppKit + WebKit) shipped inside
  the package (`src/rce/webapp/shell/RCEShell.swift`) and compiled by `researchos
  app` with the system `swiftc` — verified present on the researcher's
  machine with Xcode 16 — into `~/Applications/Research OS.app/Contents/MacOS/RCE`.
  Zero third-party dependencies, no Xcode project, no signing beyond
  ad-hoc. If `swiftc` is absent, `researchos app` falls back to the existing
  launcher-script bundle and says so in one line.
- **Lifecycle.** On launch the shell probes `http://127.0.0.1:7357/api/
  projects`; if nothing answers it spawns `researchos serve --port 7357
  --no-browser` (the absolute venv path baked in at build time, exactly
  as the launcher script does) as a child `Process` and shows a placeholder
  page — paper background, the serif brand mark, 「正在启动引擎…」 — until the
  probe succeeds (≤10s; on timeout the placeholder shows the last lines of
  `~/.rce/serve.log`). On quit it POSTs `/api/shutdown` and terminates the
  child *only if it spawned it*; an engine the user started from a terminal
  is left alone.
- **Window.** 1280×840 default, 900×600 minimum, frame autosaved under the
  name `RCEMain`, title `Research OS` (`Research OS — <project label>` once the bridge
  reports one). Standard traffic lights, full-size content view off (the
  page has its own header). Closing the window quits the app.
- **Menus.** Research OS (关于 Research OS · 退出 ⌘Q) · 文件 (新增尝试 ⌘N · 在 Finder 中显示项目
  ⌘⇧R · 关闭 ⌘W) · 编辑 (the standard undo/cut/copy/paste/select-all set —
  without it no form field pastes) · 视图 (决策树 ⌘1 · 血缘 ⌘2 · 画布 ⌘3 ·
  重新载入 ⌘R · 放大 ⌘+ · 缩小 ⌘- · 实际大小 ⌘0 · 适应全部 F) · 窗口 (最小化 ⌘M ·
  缩放) · 帮助 (打开项目地图).
- **Bridge.** Native → page: `evaluateJavaScript("window.RCE && RCE.command('canvas')")`
  for every menu item; the page exposes one `window.RCE.command(name)`
  dispatcher and works unchanged in a plain browser (no bridge, no menus,
  nothing else lost). Page → native: a single `webkit.messageHandlers.rce`
  channel carrying `{type: "title", text}` on project switch. Links to any
  origin other than `127.0.0.1` open in the default browser. Developer
  extras off; JavaScript on; no other WebKit preferences touched.
- **Icon.** Generated at build time by the same toolchain (a tiny Swift
  program draws PNGs at 16–1024px; `iconutil` folds them into `.icns`):
  paper (`#F7F2E9`) squircle, and — echoing the canvas — three sockets in a
  left-to-right flow joined by two ink Béziers: an ochre dot (数据集), an
  olive rounded square (脚本), a clay dot (图表). At 16–32px the links drop
  and only the three marks remain. No text on the icon. The bundle is
  `dev.researchos.rce`, `LSUIElement` **false** (it is a windowed app now),
  replacing the V3 launcher-only bundle in place.
- **Origin.** WebKit sends the portless `Origin: http://127.0.0.1` on
  same-origin POSTs — the Safari behaviour the V3 fix already accepts; the
  shell adds no new origin shape.

### 8.10 Resilience rules surfaced by V3 in use

Three failures seen in the first week of real use become rules:

1. **The derived graph never lives inside a cloud-synced folder.**
   Observed: the researcher's project sits in `~/Documents`, which this Mac
   syncs to iCloud Drive ("Desktop & Documents Folders"). `sqlite3.connect`
   on `.rce/graph.db` blocked for over a minute — a file the sync provider
   has evicted or is mid-transfer is materialized on `open()`, and there is
   no non-blocking way to open it — so every DB-backed endpoint hung while
   `/api/generation` and `/api/projects` kept answering. SQLite under a
   file-provider sync is also a documented corruption path. The rule:
   `graph.db` moves out of the project to `~/.rce/graphs/<id>/graph.db`,
   `<id>` a stable hash of the project's *resolved* path, resolved by one
   helper (`rce.paths.graph_db_path(project_root)`) that every `_require_db`
   copy (cli, mcp_server, webapp) calls — no module computes the path
   itself. `.rce/` inside the project keeps only what the researcher owns
   and may want under git: `attempts.toml`, `mappings.toml` (8.5),
   `backups/`. `canvas.json` (8.6) lives beside the graph, not in the
   project — it is derived too. A legacy in-project `graph.db` is migrated
   on first touch by any subcommand: copied, checked with `PRAGMA
   integrity_check`, then removed, one log line; the migration and `researchos
   init` both leave a one-line `.rce/README` saying where the graph went
   — the researcher who opens `.rce/` and finds no `graph.db` must find
   the answer in the same folder. `researchos status` and
   `/api/summary` report the graph's actual location so nothing is hidden.
   Before opening, the server checks macOS's dataless flag on the file
   (`st_flags & SF_DATALESS`) and, if set, answers with a header state
   「图谱文件正在从云端下载…」 instead of blocking a handler thread.
2. **A project whose graph vanishes mid-serve must degrade, not deadlock.**
   Observed: the watcher raised the same "graph database disappeared" error
   once per 2-second poll, forever, into the log. The rule: a read opens
   its own connection and surfaces `ProjectNotInitializedError` as a header
   state (「项目不可用 — 图谱文件已不存在」) that leaves the project switcher
   usable; the watcher, on a missing graph, logs once, sets `last_error`,
   and stops re-ingesting that root until the file reappears. While a
   project state (「项目不可用…」/「图谱文件正在从云端下载…」) is showing, the
   refresh chip (「重扫失败…」) is suppressed — both are true, but one
   cause deserves one message, and the project state is the cause.
3. **A registry entry whose directory is gone is shown as such and can be
   removed from the switcher** (「移除失效项目」 next to a disabled entry), so
   the researcher never has to hand-edit `~/.rce/projects.json`.

### 8.11 Deliberately later

Minimap; multi-select and box-select; adding an arbitrary project file as a
node from a picker (ghosts from `step_files` cover the researcher's actual
case); frames for anything other than attempts; drawing `cites`/`supports`
links (claims and references are not on this canvas); a Windows/Linux
shell. A project folder that is *moved* gets a new graph id and so an
empty graph (re-ingest is cheap and deterministic); noticing the orphaned
graph directory and offering to re-attach it is later work.

### 8.12 Rulings made during implementation

Decisions the implementers took where this section was silent, reviewed
and adopted as design:

- **A `reads` mapping is written the way the canvas is drawn.** In
  `mappings.toml`, `type = "reads"` has `from` = the dataset and `to` = the
  script (the link runs 数据 → 读取); the graph still stores `script
  --reads--> dataset`, as the dataflow extractor does. A reversed entry is
  refused with a "swap from and to" hint, never silently flipped.
- **A missing `mappings.toml` is not a deletion; an empty one is.** Failing
  to find the source is not evidence that the researcher retracted
  anything (the attempt-orphans doctrine of Section 4). To retract every
  mapping, empty the file.
- **The file outranks `researchos confirm`.** A mapping edge is re-confirmed on
  every ingest; the way to retract one is to delete its entry.
- **Undo restores what was there.** 「撤销」 after 「标记为错误提取」 returns the
  link to the status it had before (a link the researcher had confirmed
  comes back confirmed), not to a default.
- **Rejected links are wrong everywhere.** The 血缘 report skips rejected
  edges just as the canvas does, so the two views never disagree about
  whether a dataset has a source.
- **Mappings are read at startup.** The first poll of a project ingests
  `mappings.toml` once, so edits made while the app was closed are not
  lost; attempts are still only re-read when their file changes.
- **The app only stops what it started.** On quit the shell names its
  child's pid to `/api/shutdown`; an engine started from a terminal
  refuses and keeps serving.
- **The last tab is remembered; the scope is not.** Reopening the app
  returns to the view last used, always on the current attempt.
- **A frame holds its own.** A frame is drawn around its members outside
  the 「未连线」 block, so one stray step file cannot stretch it across the
  page.

## Section 9 — Human records outlive the index (task V5)

*Status: APPROVED FOR IMPLEMENTATION, 2026-10-05. Drafted from a
four-reader inventory of the code, revised against five adversarial
design reviews (73 findings, 8 of them blocking), then reviewed by the
researcher's external reviewer at commit 976059a: "the main design can
pass; five conventions must change first". All five are adopted below
(9.3 ordering; 9.11 what a fingerprint proves, references, coverage of a
comparison, crash order). The researcher then delegated the remaining
decisions to the design lead ("你自行决定") and asked for a complete,
working app to try; 9.10 records what was decided.*

**The goal, in the researcher's words: a judgment the user has made is kept
for the long term, independently of the machine index, which can always be
rebuilt.** A stable project identity answers "after the folder moves, find
the same data again". It does not answer "after the database is rebuilt,
get the human records back". This section answers both.

### 9.0 What was found (2026-10-04, read from the code and by experiment)

Section 4 already says the researcher's own file is the truth and the graph
follows it. Two kinds of human labor obey that rule today (attempt verdicts
in the Markdown table; hand-drawn links in `.rce/mappings.toml`). The rest
does not:

- A confirm or a reject on a machine-extracted link exists only as
  `edges.status` in `~/.rce/graphs/<path hash>/graph.db`. No time, no note,
  no history, and no record of the evidence the researcher was looking at.
  `updated_at` is overwritten by every later scan. Undo deletes its own
  memory.
- Rebuilding the database loses every confirm and reject.
- Moving or renaming the project folder strands the database and the
  canvas arrangement under the old path's hash. Worse: a *different*
  project later created at the old path silently inherits the old one's
  judgments, and the moved project's stale registry entry still serves the
  stranded database from a path that no longer exists.
- When the evidence under a judgment changes, the judgment is silently
  carried over. (Experiment: a confirmed read stayed confirmed after the
  call was deleted from the script; a confirmed claim–metric link stayed
  confirmed, still quoting 0.873, after the metric became 0.95.) When the
  *identity* of the judged thing changes (file renamed, sentence reworded),
  the judgment stays on the old id with nothing pointing to the new one.
- Canvas arrangements were classified "safe to delete" (8.6) and sit beside
  the database. The researcher counts them as labor; so they are.
- Two processes writing the same record file lose updates (600 concurrent
  position writes → 201 survive, 279 raise), because every lock is
  in-process and the temp-file name is fixed.

### 9.1 The rule

Two stores of opposite nature, and one direction between them.

- **The record** (人工记录) lives inside the project, in `.rce/`, as plain
  files the researcher can read, diff, commit, and back up with the
  project. It is never derived from anything.
- **The index** (机器索引) is `graph.db` under `~/.rce/graphs/<project
  id>/`. It is derived from the project's sources plus the record. Deleting
  it loses nothing.

**The index may forget; the record may not. Whatever the index knows about
a human judgment, it learned from the record.** Every human action is
written to the record first and only then reflected in the index — through
one code path, whichever surface it came from (canvas, CLI, MCP). No
surface may write a human status to the index directly. The record files
join the watch set and are applied on first sight of a project, as
`mappings.toml` already is (8.12), so an index can lag the record only
until the next poll.

One consequence for Section 4: orphan claim and attempt nodes were kept
forever when a link on them had been judged, "because the graph is the
sole record of that decision". It no longer is. Cleanup removes such
orphans like any other; the ledger keeps the judgment (9.6 says how it is
shown).

### 9.2 Inventory: what counts as human labor, and where each lives

| Human labor | Today | Source of truth after V5 | Backup |
|---|---|---|---|
| Attempt verdict and result (尝试结论) | the researcher's Markdown table | unchanged | before each app write (as today), **and** a snapshot the first time Research OS sees the file changed each day — which is what covers hand edits |
| Hand-drawn link and its note (手工连线、备注) | `.rce/mappings.toml` | unchanged | same |
| Confirm / reject of a machine link (确认、拒绝) | `edges.status` in the index only | **`.rce/judgements.toml`** (9.3) | daily snapshot; and the file is append-only, so it is its own history |
| Undo, withdraw (撤销、撤回) | an evidence key, erased on use | entries in `judgements.toml` | same |
| Note on a judgment (备注) | does not exist | optional `note` on the entry | same |
| Canvas arrangement, per view (画布位置) | `canvas.json` beside the index | **`.rce/canvas.json`** | daily snapshot, and before 「重新排列」 discards an arrangement |
| Variable definition, construction and the reason for it (变量定义卡) | nowhere — a shorthand name in the attempt table | **`.rce/variables/<id>/`** (9.11) | daily snapshot; each confirmed version is also kept as a frozen byte copy that is never rotated |
| Attempt-table configuration | `.rce/attempts.toml`, hand-written | unchanged | daily snapshot when changed; Research OS never writes it |

Snapshots go to `.rce/backups/`, newest 20 per file. A snapshot per day
rather than per write, because a review session of thirty clicks must not
rotate the last good copy away.

Not human labor, and said so: the last-used tab; the project registry
(`~/.rce/projects.json`); a model's second opinion from `researchos judge`
(`semantic_review` — a machine annotation; it can be recomputed, at a
cost, and its persistence is out of scope here).

### 9.3 The judgment ledger: `.rce/judgements.toml`

Append-only. An Research OS write is the file's existing bytes plus the new
entry's bytes; existing text, comments and unknown keys are never
re-emitted. History is therefore not a feature to build — it is the file.

```toml
# 你对机器提取结果的判断。Research OS 只追加、不改写；图谱里的确认/否决从这里派生。
[[judgement]]
id       = "j-0b6f3c1e9a4d4f7ea2c5d8e1f0a3b6c9"   # random, unique
seq      = 41                            # assigned under the lock: the order of appending
at       = "2026-10-04T21:15:03+02:00"   # for display only — never decides anything
verdict  = "rejected"       # confirmed | rejected | withdrawn | undone
src      = "script:复现包_分步/17-叙事更替与汇率波动.Rmd"
dst      = "dataset:复现包_分步/Data/panel_pricing.csv"
type     = "reads"
extractor = "dataflow"
via      = "canvas"         # canvas | cli | mcp | migrated | recovered
note     = "这里读的是旧版面板"   # optional
[judgement.basis]           # what the researcher was looking at (9.6)
calls = ["read.csv"]
```

- A link is identified as it is in the index: (`src`, `dst`, `type`,
  `extractor`). Node ids are project-relative (verified: none contains the
  project root), so the ledger is valid wherever the folder goes. The
  ledger never holds `extractor = "mapping"`: a hand-drawn link has one
  authority, `mappings.toml`, and every write path refuses a second.
- **Two different acts, two verdicts.** 「撤销」 takes back the researcher's
  *last act* on a link: an `undone` entry naming the entry it cancels
  (`undoes = "<id>"`), after which the link is whatever it was before — a
  link confirmed, then rejected by a mis-click, then undone, is confirmed
  again (8.12's rule, kept). 「撤回」 takes back the *judgment*: a
  `withdrawn` entry, after which the link is whatever the machine says.
- **The state of a link** is its last entry *in the order of appending*,
  among entries not cancelled by an `undone`. That order is the file's
  order, and each entry Research OS writes carries `seq`, one more than the
  highest in the file, assigned while the project lock is held. **A
  clock never decides.** `at` is there to be read by a person: a clock
  that is corrected, or another machine's clock running behind, must not
  be able to put a withdrawal before the confirmation it withdrew. (An
  earlier draft ordered by `at`; the external review caught it.)
- **Two histories are a conflict, not a race.** If `seq` repeats or runs
  backwards, two machines appended to different copies of the file and
  something merged them. Research OS does not pick a winner — not by time, not by
  position. Every link that has an entry at or after the first anomaly is
  shown as 「记录冲突，待处理」 at the machine's status, with both
  histories, until the researcher writes a new entry for it; that entry,
  appended last and by them, settles it. A hand-written entry without
  `seq` takes its place by position and is not an anomaly.
- **`basis`** is the heart of 9.6: the substantive facts the link rested on
  when the researcher judged it. It is stored readable, so that in a year
  the researcher can see *what* they confirmed, not merely *that* they did.
- Hand edits are legal; it is the researcher's file. An entry that fails
  validation makes the whole ledger unreadable, and Research OS names the line.

**A ledger Research OS cannot trust is never built upon.** While the ledger is
missing though the project has one (`.rce/project.toml` records that it
does, from the first entry on), still in the cloud, or unreadable:

- the index keeps the human state it had (Section 4: failing to read the
  source is not evidence of deletion); a brand-new index shows none and
  says why;
- **every write to the ledger is refused** (「判断记录文件当前无法读取，请先
  恢复它」). Research OS never replaces a file it could not parse and never
  recreates one that should exist — otherwise one click on a morning when
  the sync service is mid-transfer would found a new one-entry ledger and
  the next read would retract everything else.

**A ledger that has shrunk is not obeyed silently.** The index keeps a
copy of every entry it has applied. That copy is a safety net, never a
second authority: if a readable ledger lacks entries the index applied (a
sync service kept the other machine's file; a truncation that still
parses; a zero-byte download; a restore from backup), Research OS changes nothing
and asks — 「记录文件比图谱少了 N 条判断」 with two answers, 「以文件为准」
(this is what a deliberate restore wants) and 「把缺少的补回文件」 (appended
with `via = "recovered"`). The same question, mirrored, when the file has
entries the index never saw is not a question: they are simply applied.

`mappings.toml` keeps its format and its 8.12 rules.

### 9.4 Project identity

A project is identified by a file it carries, not by where it sits.

```toml
# .rce/project.toml
id      = "p-3f9c2a7e5b1d4c68a0e7b2d94c1f6a35"   # random, assigned once
created = "2026-10-04"
ledger  = true                  # set when judgements.toml gets its first entry
# forked_from = "p-…"           # only on a folder declared an independent branch
```

The index lives at `~/.rce/graphs/<id>/` and remembers its home in
`home.json` beside `graph.db`: the folder's canonical path (as the file
system stores it) and its device and inode numbers. "This folder is the
home" means *the same directory*, whatever it is spelled as — a change of
letter case, a Unicode-normalization variant or a symlink is not a move,
and only rewrites the spelling.

**The identity check is the first thing every entry point does** — every
CLI subcommand, `researchos serve` with or without a path, a project switch in
the app, the MCP server — before anything is written: no registry entry or
recency bump, no README, no scan, no project node.

| Situation | What it is | What happens |
|---|---|---|
| id present, an index for it exists, its home is this directory | the normal case | open |
| …its home is elsewhere, and the old home is **confirmed** gone (its volume is mounted, its parent is readable, the folder is not there) or confirmed to carry a different id | **moved or renamed** | adopt: update `home.json` and the registry entry (path and label); one log line |
| …its home is elsewhere, and the old home still carries this id | **a copy** — two live folders, one identity | stop and ask (below) |
| …its home is elsewhere, and the old home **cannot be checked** (volume not mounted, unreadable, its identity file still in the cloud) | cannot tell a move from a copy | stop and ask, with a third answer 「原位置暂时不可用，先只读打开」 |
| id present, no index for it on this machine | restored, cloned, synced from another Mac, or the index was deleted | build the index from sources and the record |
| no `project.toml`, but `.rce/` holds record files | the identity file was lost, or `.rce/` was copied in from elsewhere | stop and ask; never mint an id silently over existing records |
| `project.toml` exists but cannot be read (in the cloud, unparseable, or a sync conflict copy sits beside it) | cannot tell who this is | stop: 「项目身份文件无法读取」; nothing is written |
| no `project.toml`, no record files | not an Research OS project, or one from before V5 | `researchos init`; and see 9.5 for what is checked first |

"Gone" is never inferred from failing to look. That is the Section 4
error in another coat: an unmounted disk is not a deleted folder.

**A copy is never guessed at.** Until the researcher answers, nothing is
written — not the index, not the record, not the registry. The answers:

- 「作为独立分支继续」 (`researchos project fork`): this folder gets a new id and
  `forked_from`. Its record files came with the copy, so its judgments
  start equal to the original's and diverge from here; it gets its own
  index. (If `project.toml` is tracked by git, Research OS says that committing it
  will carry the new identity into whatever branch it is merged to.)
- 「这里才是原项目」 (`researchos project claim`): the index's home becomes this
  folder **and the index is rebuilt from this folder** (9.8), so nothing
  from the other folder's scans or record survives in it. The other folder
  is asked the same question when it is next opened.
- 「这是另一个项目」: for a folder that merely received a copy of someone
  else's `.rce/`. A new id with no `forked_from`; the copied ledger,
  arrangement and variable cards are moved into `.rce/backups/`, since
  they speak about another project's files. (A variable reference such
  as `topicshift@v2` resolves within one project id: versions confirmed
  before a fork are the same in both folders, later ones are not.)

**Identity is re-checked at every write, not only at open.** A running
engine holds a folder open for hours; the folder can be moved in Finder
under it, or claimed elsewhere. Under the project lock (9.7), each write
to a record file or to the index first confirms that the served folder
still exists, still carries the id, and is still that id's home. If not,
it writes nothing and the page says 「项目已移动或已在别处认领，请重新打开」.
Record writers create `.rce/` only inside a folder that exists; they never
re-create a folder that has gone.

Consequences worth stating. A new, unrelated project created at a path an
old project used to occupy has a different id (or none) and inherits
nothing. A registry entry is `{id, path, label}`, keyed by id; a moved
project updates its entry instead of adding one, and an entry whose folder
is gone is shown as 「找不到项目文件夹（可能已移动）」 with 「选择新位置…」 —
the folder chosen goes through the same identity check, so a moved project
can be re-attached from the app, which is how the app (started with no
path) finds it again; Research OS never searches for a folder by itself. The
`project` node in the index is `project:<id>`. The same project synced to
two Macs is one identity with one local index per machine.

### 9.5 Migrating what exists

Before V5 the judgments sit in indexes keyed by a path hash — and a path
hash does not prove whose they are. So migration is **an explicit act,
never a side effect of opening a folder**, and it accounts for every old
index on the machine, not just the obvious one.

**What is looked for.** On every open, whether or not the folder already
has an id, Research OS checks for un-retired pre-V5 databases that may hold this
project's judgments: the index at this path's hash; a legacy
`.rce/graph.db` inside the folder (pre-8.10); and — listed by `researchos migrate
--list` for the researcher to recognise — every other un-retired index
under `~/.rce/graphs/`, each with the project path it recorded, whether
that path still exists, and how many judgments it holds. Indexes stranded
by a move before V5 are found this way, and so is the second Mac's own
index when the identity file arrived by sync.

**What the researcher is shown before anything is exported**: where the
index says it came from, how many confirms and rejects and arranged views
it holds, and how many of the judged links' endpoints a scan of *this*
folder actually produces. A folder that merely reuses an old project's
path will show a low match; 「这不是这个项目的」 leaves that index alone.
Until a pre-V5 project is migrated it opens for reading; writing a human
record waits for the migration, so that nothing new lands in the old
store.

**The steps**, under the project lock from the first to the last:

1. **Export.** Every confirmed or rejected machine link in the old index
   becomes ledger entries (`via = "migrated"`; a reject that remembered a
   prior confirmation becomes two entries, in order; `mapping` links are
   skipped — their truth is already a file). An arrangement is copied to
   `.rce/canvas.json` only if none exists there. Exporting twice adds
   nothing. Exporting a second old index merges by content; a migrated
   entry that contradicts the state the ledger already has for that link
   is recorded and the link is put under review (9.6) — two machines
   disagreed, and a migration date must not decide between them.
2. **Basis.** The evidence at the moment of the original judgment was
   never kept. The old index's evidence is an accumulation of everything
   ever seen. So: if those accumulated occurrences yield exactly one basis
   and it equals what a fresh scan yields, that basis is recorded
   (`basis_recorded = "at-migration"`). Otherwise the entry records what
   the old index held and the link comes up **under review** — a judgment
   that was already silently carried over to changed evidence is not
   re-certified on the new evidence by the act of migrating it.
3. **Identity.** `.rce/project.toml` is created exclusively and never
   overwritten; it carries `migrating_from` until step 5, and any open of
   such a folder resumes here rather than treating it as migrated.
4. **Rebuild and verify.** Build a new index for the new id from scratch —
   sources, then the record — and reconcile it against the *old index's own
   count*, not against what the exporter says it exported: **M** judged
   links in the old index, each matched by key to the ledger and found in
   the new index as exactly one of *applied*, *under review*, or *under
   review because no scan produces the link any more*; unmatched must be
   zero. A source file that could not be read during this scan is a fourth
   count, 「来源文件暂不可读」, and any number other than zero there stops
   the migration — an evicted file must not be mistaken for a vanished
   link. Attempt verdicts and hand-drawn links in the new index must equal
   *their source files*; where the old index differed it is listed as a
   stale mirror, not a failure. The tally is printed.
5. **Only then retire the old index**, and only if no other process holds
   it open (an engine or MCP server still running old code would go on
   writing into a retired file: 「请先退出 Research OS 与 MCP 服务」). It is renamed
   into `~/.rce/graphs/.retired/<hash>-<date>/`, not deleted, and the
   command says where it went.

If step 4 does not balance, nothing is retired: the half-built index is
removed, the old one keeps serving, what did not match is shown, and a
retry duplicates nothing. The researcher's own project is migrated by hand
at acceptance, after a backup, as the graph move was.

### 9.6 When the evidence changes

A judgment is a statement about particular evidence. It is applied only
while that evidence stands; otherwise it is kept, shown, and waits.

**Basis.** Each extractor defines, for its links, a small canonical record
of the facts a link rests on, and nothing positional. Line numbers,
timestamps, run counters, the name of a receiving variable and the
spelling of an expression are not facts.

| Extractor | Basis |
|---|---|
| `dataflow` reads / writes | the set of bare call names that produced the link (`read_csv`, `open`; in R the function without its package prefix: `read.csv`, `read_dta`). The path is not repeated: the resolved path *is* the link's `dst` |
| `claims` backed_by | the claim's normalized sentence and printed number; the names of the metrics of that experiment that match it, each value rounded to the claim's printed precision |
| `pyfig` generates | the bare call name |
| `mlflow` / `wandb` produces | the artifact path |
| `latex` / `mdpaper` includes, cites; `git`; `attempts_consistency` | the link's identity alone |

**What a scan must report**, which it does not today. The basis compared
is the one produced by *this scan*, not the index's accumulated evidence.
And a link can only be said to be "no longer produced" by a scan that
actually read its source. So every extractor reports, per source, one of
*read and parsed* / *unreadable* / *unparseable* (today an unparseable
script and a script with no calls look the same). A link's source is the
file named in its evidence, or for experiment links the tracking store
read in that run; a file absent from a successfully read inventory is an
observation too. A scan speaks only for the extractors it ran and the
sources it read; everything else keeps its previous state.

**On every scan, for each link whose ledger state is a verdict:**

- **Same basis** → the judgment applies. A line inserted above, a renamed
  variable, a rescan: nothing happens, and nothing is written.
- **Anything else** → the judgment is **not applied**. The link is shown
  at the machine's own status and marked 「待复核」, with the old verdict,
  its date, its note, its basis, and the reason, which is one of:
  - 「依据已变化」 — the link is still produced, on a different basis;
  - 「机器不再得出这条关联」 — both ends are still in the scan, the link is
    not (the call was removed; the metric no longer rounds to the printed
    number);
  - 「关联的一端不在本次扫描结果里」 — a file was renamed or removed, a
    sentence was reworded so that the claim has a new id.

  The wording says what the scan did, never that something "no longer
  exists": a path the extractor could no longer resolve is not a deleted
  file. A source that could not be read is none of these — it is 「来源文件
  暂不可读」, and the judgment stays as it was.
- The ledger is not touched by any of this. The researcher settles a
  review with a new entry: 「仍然成立」 (the same verdict, recorded on the
  basis as it is now), the opposite verdict, or 「撤回」. If the same link
  comes back with the same basis, the judgment applies again by itself,
  because the facts it was made on are back.

**No transfer, but a prompt.** Research OS never moves a judgment onto another
link by itself. But when a judged link stops being produced, the review
item lists the links that *appeared in the same scan from the same source
with the same type and the same basis* — the renamed script's read of the
same file — and each of those links carries the hint 「可能对应一条待复核的
旧判断」. Applying the old verdict to one of them is the researcher's click
and a new ledger entry. This is how "the new state has a prompt" is met
when an edit changes an id.

**Where it shows.** A link under review is marked as such in every place
that shows links — canvas, 血缘, 决策树, `researchos lineage`/`trace`, MCP —
because a link the researcher had rejected reappears at the machine's
status, and must not pass for an ordinary one. The header counts
「待复核：N」; a link under review is counted there and not in 「待确认」.
Clicking the count lists the items in the existing side panel with the
three actions; `researchos review` prints the same list. The canvas gets nothing
else in V5.

What V5 does **not** change: machine links nobody judged, which a scan no
longer produces, stay in the index and in the views as they do today.
Cleaning those up is a separate decision (9.10).

### 9.7 One writer at a time

Every write to a record file, and every scan that writes the index, takes
a cross-process lock on the project: `flock` on
`~/.rce/locks/<project id>.lock` — local, never synced, never evicted, and
the same file for every spelling of the folder (before a project has an
id, the lock is keyed by its canonical path). Temp-file names are unique
per write. Two engines, or an engine and a CLI command, may run against
one project; they take turns.

Record files sit in a folder that may be cloud-synced. Before reading
one, Research OS checks whether the sync service has evicted it (8.10's dataless
flag), asks for the download, and says 「记录文件正在从云端下载…」 rather
than blocking. If the sync service leaves a conflict copy of a record file
(`judgements 2.toml`), Research OS says so and merges nothing by itself; entry ids
make a merge tool possible, and that tool is not V5.

### 9.8 Commands

- `researchos records` — the inventory of 9.2 for this project: where each kind
  lives, how many, the newest snapshot. `researchos records --verify` checks, per
  link, that the index's human state is what the record implies, and exits
  non-zero if not.
- `researchos rebuild` — build a fresh index beside the current one, apply the
  record, and compare human state per link before and after: on unchanged
  sources, a judgment applied before and not applied after is a failure,
  and any unreadable source blocks the swap. Then swap; the previous index
  is kept one generation.
- `researchos review` — the list of 9.6. `researchos confirm` gains `--note` and the
  `withdrawn` and `undone` verdicts and writes the ledger like every other
  surface; MCP's `confirm_edge` is narrowed to the same verdicts and, like
  the canvas, refuses hand-drawn links.
- `researchos migrate`, `researchos migrate --list`, `researchos migrate --from <dir>` — 9.5.
- `researchos project fork` / `researchos project claim` — 9.4.

### 9.9 Acceptance

Each scenario runs on a fixture that holds, at the start: one confirmed
machine link, one rejected, one confirmed-then-rejected-then-undone (so:
confirmed), one hand-drawn link with a note, an attempt table with
verdicts, and a canvas view arranged by hand. "All records present" means
all six, each readable through the app and through `researchos records`.

1. **Move.** Move the folder elsewhere and open it there — from the CLI,
   and from the app through 「选择新位置…」: all records present; one
   registry entry, at the new path; the old path is not servable; the
   index directory did not change. Move it *while an engine is serving
   it* and click a judgment: nothing is written at the old path, and the
   page says to reopen.
2. **Rename**, including a change of letter case only: as 1, with no copy
   question.
3. **Copy.** Open a copy: blocked, with the answers, and nothing written
   (registry included) until one is taken. *Fork*: new id, `forked_from`,
   all records present in both; a later judgment in the copy does not
   appear in the original. *Claim*: the index is rebuilt from the
   claiming folder, `researchos records --verify` passes there, and the original
   is asked on its next open. With the original on a volume that is not
   mounted: the third answer, read-only, and no adoption.
4. **Path reuse.** After V5: move the project away, create an unrelated
   project at the old path — it has no judgments, no arrangement, its own
   id. Before V5: the same, then upgrade — the new folder is shown the
   old index with its low match, declines, and inherits nothing.
5. **Rebuild.** Delete `~/.rce/graphs/<id>/` and open the project: all
   records present; `researchos records --verify` passes. `researchos rebuild` on a
   healthy project: the same, and the per-link comparison is clean.
6. **Repeated scans.** Run every scan three times: the ledger is
   byte-identical, the index's human state is identical, nothing comes
   under review.
7. **Backup and restore.** Copy `.rce/` aside; make further judgments;
   restore the copy over `.rce/`: Research OS reports that the file has N fewer
   judgments than the index and asks; 「以文件为准」 gives exactly the
   copy's records. Restore the whole project folder from an earlier copy
   on a machine with no index for it: the same records, no question.
8. **Evidence changes.** (a) Insert lines above a judged call; rename its
   receiving variable or the import alias: the judgment applies, nothing
   is flagged. (b) Change which function is called: 「待复核 · 依据已变化」,
   the old verdict and basis visible, the link at the machine's status and
   marked in every view; 「仍然成立」 clears it and the ledger shows both
   entries. (c) Delete the call; change a metric so it no longer rounds to
   the claim's number: 「待复核 · 机器不再得出这条关联」; put it back: the
   judgment applies again with no new entry. (d) Rename the script: the old
   judgment is under review with its reason, the new read is listed beside
   it as a candidate and carries the hint; nothing is carried across until
   the researcher clicks. (e) Make the script unparseable, and separately
   unreadable: nothing comes under review; the judgment is unchanged and
   the source is reported.
9. **Migration.** A pre-V5 index holding: a confirmed link; a rejected one
   with and one without a remembered prior status; a judged link whose
   stored evidence shows two different bases; a judged link the script no
   longer produces; a preserved orphan claim and a preserved orphan
   attempt, each with a judgment; a judged hand-drawn link; arranged
   views. Migrate: the tally starts from the old index's own count and
   balances with nothing unmatched; the two-bases link is under review,
   not re-certified; the old index is retired only afterwards, and not
   while another process holds it. Make verification fail on purpose, and
   kill the process between each pair of steps: nothing is retired, the
   old index still serves, and a retry resumes and duplicates nothing. A
   second old index for the same project (another machine's) merges, and
   its one contradicting verdict comes under review.
10. **Two writers.** Two processes each make 300 judgments and 300
    position changes on one project at once: every one is in the record
    afterwards, and nothing raises.
11. **A record Research OS cannot trust.** Make the ledger unparseable; remove
    it; make it zero bytes; truncate it at an entry boundary: in each
    case the index keeps what it had, the app says what is wrong, **a
    click on confirm or reject writes nothing**, and repairing the file
    restores normal operation. Truncated and zero-byte ask the 9.3
    question.
12. **History.** Confirm; reject; undo — the link is confirmed; confirm
    again with a note; withdraw — the link is the machine's. The ledger
    holds five entries in order, the app shows the last state, and the
    others as its history. **Set the system clock back an hour between two
    of them: the later act still wins.** Merge two copies of the ledger
    that each appended after the same entry: the links they touch show
    「记录冲突，待处理」, nothing is decided by time, and a new entry by
    the researcher settles each.

### 9.10 Not in V5, said plainly

The result-review flow (entering from a figure, a PDF or a claim) — the
next phase; it will read the review list this phase creates. Folding the
attempt table's stale-verdict check into that list. Removing from the
views machine links nobody judged that a scan no longer produces. Merging
two diverged copies of a record file. Keeping `researchos judge` annotations
across a rebuild. Any new canvas feature.

One limit the researcher should approve with open eyes: **a judgment on a
claim loses its link whenever the claim's id changes, and that id changes
often.** Verified causes: rewording the sentence; changing the printed
number; renaming the section heading (every claim in the section);
renaming or moving the file (every claim in it); and, in Chinese prose
written one paragraph per line, *any* edit in the paragraph, because 。！？
are not yet treated as sentence ends. V5 guarantees such a judgment is
kept, shown under review with its original sentence, and offered against
the candidates that replaced it — not that it stays attached. **Decided
2026-10-05:** the sentence splitter is fixed in this phase (。！？ become
sentence ends), because the cheapest moment to change how a claim is
identified is now, while the researcher's project holds no judgment on any
claim. Reviews on claims are computed from the start; the external
reviewer's condition — no reminders at volume while unrelated edits still
raise them — is met by fixing the cause first.

**Decided 2026-10-05, under the researcher's delegation.**

- A project from before V5 is read-only for human records until it is
  migrated; Research OS writes a human record only after the migration's tally
  has balanced.
- V5 is built in stages, each with its own acceptance run, and delivered
  to the researcher as one working app: the record and the lock; project
  identity; scan reports and per-scan basis; the ledger driving the
  index, with review states; migration; `records` / `rebuild` / `verify`;
  the app's review list, history and blocking states; variable cards.
  The order of entries, immutable references and the snapshot-before-entry
  rule are fixed from the first stage — a format written to the
  researcher's disk is the one thing that cannot be changed cheaply later.
- Cards are written in their files; the app shows them and offers the
  actions Research OS records. The cost of filling a card is measured on a real
  one before a form is considered.
- The first card is one variable taken end to end — `TopicShift` — before
  `RV` and `NetBuyRate`. Research OS's maintainers may draft a card from the
  source with the evidence attached; it stays a draft until the researcher
  has read the meaning and written the reason.

### 9.11 Variable definition cards

*Added at the researcher's direction after draft 2, and revised after two
adversarial design reviews (27 findings, 3 blocking): "protecting human
labor" must protect not only the judgments but the variable definitions
those judgments were made on.*

**The gap.** In the researcher's project a variable exists only as a
shorthand in the attempt table — `TopicShift→RV/NetBuyRate(月)`, `私有
Shannon 熵→lnRate(月,ECM)` — and as a substring in the dead-variable list.
Nothing records what `RV` means, which returns it uses, how it is
aggregated, what happens to missing trading days, or why that definition
was chosen over another. A script path does not answer "what is this
indicator and why is it built this way"; a name does not answer "how was
it computed". The card keeps both, and keeps them apart: **the research
definition is the researcher's statement; the implementation is where Research OS
can check that statement against the files.**

**Layout: what the researcher writes and what Research OS records never share a
file.** One directory per variable; its name is the variable's id.

```
.rce/variables/topicshift/
    v1.toml         the researcher's text for version 1 — Research OS never writes into it
    v2.toml
    log.toml        appended by Research OS when the researcher acts: confirmed, reaffirmed,
                    corrected, abandoned, revived — an append-only ledger, exactly 9.3
    frozen/<hash>.toml    a byte copy of each version as confirmed (and as corrected)
.rce/variables/_code/<hash>.<ext>   a copy of each implementing script as confirmed
```

(The first draft put status and Research OS's checks inside the researcher's
blocks of one file. That cannot be done by appending — a table appended at
the end of a TOML file attaches to the *last* version, not the one being
confirmed, verified with the parser — so it would have had Research OS rewriting
the researcher's text. It is recorded here so it is not tried again.)

A version file, written by the researcher:

```toml
name    = "TopicShift（叙事更替）"
aliases = ["TopicShift"]        # the spellings used in the attempt table

# 含义
meaning     = '''相邻两月新闻主题分布的差异，衡量叙事更替的幅度'''
unit        = "无量纲，0–1"
granularity = "月"

# 输入 — a dataset, or another variable at a pinned version
[[input]]
dataset      = "复现包_分步/Data/theme_counts_2017_2024.csv"
fields       = ["month", "theme", "count"]
data_version = '''2017-01 至 2024-12，2026-07 下载'''

# 构建口径
[construction]
formula     = '''TS_t = 1 − cos(p_t, p_{t−1})，p_t 为当月各主题占比向量'''
filter      = '''剔除当月文章数 < 30 的月份'''
aggregation = '''日度主题计数先按月求和，再归一'''
missing     = '''缺月不插值，记为缺失'''
transform   = '''不标准化'''
params      = '''主题数 K=12'''

# 实现依据
[implementation]
script       = "复现包_分步/16-构造叙事更替指标.py"
output       = "复现包_分步/Data/topicshift_monthly.csv"
field        = "topicshift"
code_version = '''复现包 2026-07-26 版'''    # optional, in the researcher's words

# 人工决策
[decision]
why        = '''余弦距离对主题总量不敏感；试过 JS 散度，对稀疏主题过于敏感'''
decided_by = "LL"
adopted_on = "2026-07-26"      # when the definition was adopted — may predate the card
```

The five blocks are the researcher's list. Free text is written in
literal strings (`'''…'''`) so that a formula with backslashes or quotes
is kept exactly; the template says so. A version file is rejected on
read, with the line named, if it has a key the schema does not know —
which is what catches a line that slid under the wrong heading. A draft
may be incomplete; to be confirmed a version needs a name, meaning, unit,
granularity, at least one input, a formula and a reason.

**Who writes what.** The version file is the researcher's, all of it. Research OS
never fills in a formula, a filter or a reason — reading code and guessing
what it computes is what Section 0 forbids. Everything Research OS knows goes into
`log.toml`.

**Confirming** is the act that turns a draft into a definition results
may rely on. Research OS appends one entry:

```toml
[[entry]]
id      = "v-5e0c…"                         # this entry is the version's immutable identity
seq     = 7                                 # order of appending (9.3) — the clock decides nothing
at      = "2026-10-05T10:12:00+02:00"      # Research OS's clock, for display — never typed
act     = "confirmed"
version = 1
attested = "unknown"   # the researcher's own statement, asked at confirmation:
                       # was the output file as it stands built with THIS definition? yes | no | unknown
content = "sha256:9c1f…"       # of the version file's parsed content; comments and layout are free
frozen  = "frozen/9c1f….toml"
[entry.checked]                 # read from disk now; each item is 已核对, 不符, or 未核对 with its reason
script  = { result = "已核对", sha256 = "71ab…", copy = "_code/71ab….py" }
writes  = { result = "已核对", call = "to_csv" }     # this parse of the script writes the output
reads   = [{ dataset = "…theme_counts_2017_2024.csv", result = "已核对", call = "read_csv" }]
field   = { result = "已核对" }                      # found in the CSV header
[entry.observed]                # what was on disk at that moment — an observation, nothing more
output  = { sha256 = "0d4e…", size = 18230 }
inputs  = [{ dataset = "…theme_counts_2017_2024.csv", sha256 = "44ab…", size = 912004 }]
```

- The checks are made **by parsing the script now**, not by asking the
  index (which keeps links a script stopped producing): does this parse
  yield a write to the output and reads of the inputs. `不符` — the
  script parsed and does no such thing — is a finding and is shown as
  one; `未核对` says why (the script could not be parsed; the output is
  not a CSV; the file is still in the cloud; the link exists only as the
  researcher's own hand-drawn mapping, which is their statement and not a
  check). No outcome blocks confirmation: a variable computed in an Rmd
  chunk and never written to a file is still a variable.
- **What each thing proves, and no more** (Section 0, "kept material is
  not a relation"). *Confirming* means the researcher endorses this
  definition. `observed` means a file with this hash was on disk when
  they did. Neither says the file was built by the definition: the script
  may already compute the new definition while the output on disk is last
  month's — and the design lets a version be confirmed when the checks
  say `不符`. So Research OS never infers "this output belongs to this version"
  from a matching hash. What can carry that weight is a statement or
  evidence of its own: at confirmation the researcher is asked, once,
  whether the output as it stands was built with this definition — 是 /
  否 / 不确定 — and the answer is recorded as theirs (`attested`). They
  know it at that moment and will not in two months. Research OS does not run
  scripts, so it has no run evidence to offer; if it ever records runs,
  that will be a second kind of basis. A `yes` standing beside a `不符` is
  shown as exactly that, not resolved. `adopted_on` is the researcher's
  statement of when the definition was adopted and may predate the card;
  `at` is when the statement was made. The next phase attributes results
  from these items one by one.
- **The implementing script is copied** to `_code/`, named by its hash.
  The researcher's project is not a git repository; a hash can be compared
  in a year but not read. With the copy, the card can show 「查看当时的代码」.
  Scripts are small; a copy is written once and never rotated.

**Versions, and the rule that protects history.**

- A card has at most one draft, and it is always the highest number. A
  draft is edited freely; the view marks it 「草稿 · 改动不留版本」.
- **Confirming freezes the whole version.** After that, any change to the
  content of `v<n>.toml` is detected by comparing it with the hash in its
  own log entry — **without needing the index**, so the check still works
  after a rebuild, on another Mac, after a restore. Research OS applies nothing
  and asks: 「v2 的定义在确认后被改动了」 — 「另存为新版本」 (the edited text
  becomes the draft `v3`; `v2` is restored from its frozen copy, byte for
  byte), or 「这是更正」 (a typo, a clearer wording, a script that moved: a
  `corrected` entry with both hashes and a new frozen copy, so the wording
  before and after can both be read).
- To change how a variable is built is to write the next version: `researchos
  variable revise` copies the current version file, byte for byte, to the
  next number as a draft, and refuses while a draft is open. When the new
  one is confirmed, the old one is *shown* as superseded from that
  entry's time; its file is not touched.
- **A result points at one confirmed text, not at a name and not at a
  number.** `v2` is the label a person reads. What a reference stores is
  the variable's id, that label, the id of the log entry it relies on (the
  `confirmed` entry, or the `corrected` entry current when the reference
  was made) and the content hash that entry carries; it is written
  `topicshift@v2·9c1f2a3b`. A reference resolves only when the log holds
  that entry with that hash. If it does not — the project was restored
  from a backup older than the version; the card was rebuilt — it shows
  「引用暂不可解析」 and stays that way: it is **never** re-pointed at
  whatever is called `v2` now. This is why a correction does not rewrite
  history (the result keeps the hash it was made against, and both
  wordings can be read), and why "numbers are not reused" — which Research OS
  still does its best to keep, leaving a `removed` entry behind a version
  that a restore took away — no longer has to be true for a reference to
  be safe. Only a confirmed version can be referred to.
- In use = the highest confirmed version. `abandoned` and `revived` are
  entries about the whole variable, each with the researcher's reason;
  while abandoned no version is in use, and every version still resolves.

**Variables built from variables.** An input is either a dataset or
`variable = "returns@v1"` — pinned. When the upstream variable gains a
newer version the card says 「上游 returns 已有 v2」 and nothing else
happens: this version was built on that one. `transform` describes what
the output file contains; a lag or a standardization applied inside one
model belongs to that result (next phase). The same construct at two
frequencies is two cards — granularity is part of the definition.

**Dead variables.** Abandoning a card records, in the researcher's words
and with a date, why a variable died — which the substring list in
`attempts.toml` cannot. In V5 that list remains the only input to the
revived-dead-variable check; the 「变量」 view shows any disagreement in
either direction (「卡片已弃用，attempts.toml 未列入」 and the reverse), using
`aliases`. Unifying the two is later. `aliases` also gives the next phase
something deterministic to map the attempt table's shorthand onto.

**When the implementation moves under a confirmed version** (stage (b)).
Research OS compares the script — its code with comments and blank lines removed;
for an Rmd, its code chunks — and the input files with what the
`confirmed` entry recorded. The researcher may narrow the comparison by
naming `chunk` (an Rmd chunk label) or `function` in `[implementation]`;
the region is found exactly or the version is under review for that
reason. A difference puts the version under review — 「实现脚本在确认后有
改动」 or 「输入数据在确认后有变化」 — and Research OS says plainly that it cannot
tell whether the definition changed. Reviews raised by one changed script
are one item, 「此脚本的改动涉及 N 个变量」, answerable together (「全部口径
未变」) or one by one: 「口径未变」 appends `reaffirmed` with the new
fingerprints and, after an input change, the researcher's new
`data_version` note; 「口径已变」 opens the next draft. An input that
cannot be read (still in the cloud) raises nothing.

**"Nothing changed" always says how far it looked.** Narrowing to a chunk
or a function does not see a parameter defined outside it or a function it
calls; a file above 50 MB is compared by size, and content can change at
the same size. Both are legitimate ways to keep a scan cheap, and both are
blind spots, so every comparison carries its coverage and the view words
it: 「全文未变」; 「指定范围未变（范围外未比对）」; 「大小未变（内容未比对）」;
「修改时间变化，内容未比对」. The bare phrase "实现及数据未变化" is never
shown. A `reaffirmed` entry records the coverage it was made under, and
「完整比对」 hashes the large files on request.

**A confirmation is written snapshot first, entry last.** Confirming (and
correcting) touches several files; the log entry is the commit point. In
order: the code copy and the frozen copy are written and synced to disk;
both are read back and their hashes checked; only then is the entry that
names them appended. A crash at any point leaves at worst copies no entry
refers to — harmless, and `researchos records --clean` removes them — and never
an entry that refers to a copy which is not there. On reading, an entry
whose copy is missing anyway (a sync that has not delivered it) is shown
as 「确认记录引用的副本缺失」: the hash in the entry still detects an edit,
but the version cannot be restored from it until the copy returns.

**Safety, as for the ledger.** `log.toml` is a 9.3 ledger and gets 9.3's
protections: while it is missing, in the cloud or unreadable, the card is
frozen and writes to *that card* are refused; a log that has lost entries
the index applied — an `abandoned` as much as a `confirmed` — is asked
about, never obeyed. Two directories with the same id up to letter case,
or a sync conflict copy beside any file of a card, make that card
unreadable until one is removed. `researchos variable new` creates the directory
exclusively and refuses an id already present in the directory, the
index, or the snapshots. Snapshots go to `.rce/backups/variables/<id>/`.
What V5 cannot detect, and says so: a whole card directory that never
arrived on a machine with no index for the project.

**In the app.** One plain view, 「变量」: the cards, the version in use,
the history from the log, each check with its result, what is under
review, 「查看当时的代码」. The app may write what Research OS authors — confirm,
reaffirm, open the next draft, abandon or revive with a note — and never
the researcher's text: a card is written in its file, from a commented
template that `researchos variable new` creates. In the index, V5 keeps a copy
of each card for this view and for the shrink check, and promises no node
type to later phases.

**What V5 covers and does not.** Cards are written by the researcher for
the variables that actually enter a model, a figure or a claim; Research OS does
not go looking for variables and does not create cards. Not in V5: links
from attempts, figures and claims to variable versions; variables on the
canvas; a form for writing a card in the app; discovering variables from
data columns; replacing the dead-variable list; renaming a variable's id.

**Acceptance (continues 9.9).**

13. **Life of a card.** `researchos variable new` → a draft from the template;
    edit it freely; confirm with one input deliberately absent and the
    output not a CSV: confirmed, with those checks recorded as 「未核对」
    and their reasons, the script copied to `_code/`, the output
    fingerprinted. Revise and confirm: `v2` in use, `v1` shown superseded
    from the entry's time, `v1.toml` byte-identical. `revise` with a draft
    open is refused. **Kill the process between each pair of writes of a
    confirmation and start again**: there is never an entry whose frozen
    or code copy is missing; leftover copies are removed by `researchos records
    --clean`. The confirmation asks the attestation question, and a
    matching output hash alone never makes the view say the output was
    built with the version.
14. **History is not overwritten — with or without an index.** Edit a
    confirmed version's formula by hand: nothing is applied and the
    question is asked. 「另存为新版本」: the edit becomes draft `v3` and
    `v2.toml` is byte-identical to its frozen copy. 「这是更正」: a
    `corrected` entry, and both wordings can be read. **Repeat with
    `~/.rce/graphs/<id>/` deleted before the edit is seen: the question is
    still asked.** Change only a comment: no question. **A reference
    made before a correction still shows the wording it was made
    against; with the card restored from a backup that predates the
    version, the reference shows 「引用暂不可解析」 and a newly written
    `v2` does not capture it.**
15. **Survival.** Scenarios 1–7 and 10–11 of 9.9 with two cards added to
    the fixture (one with two versions, a correction and an `abandoned`
    entry): after a move, a rename, a copy with each answer, a rebuild, a
    restore and two concurrent writers, every version, entry, frozen copy
    and code copy is present and `researchos records --verify` passes. A log made
    unparseable, removed or truncated — including one that lost only its
    `abandoned` entry — freezes that card, refuses writes to it and asks;
    the other cards keep working. A sync conflict copy beside `v2.toml`
    makes that card unreadable and says why. A version removed by
    「以文件为准」 does not give its number to the next revision.
16. **Dead and back.** Abandon a card with a reason; revive it: the view
    shows the state and both entries in order, and flags the disagreement
    with `attempts.toml` in whichever direction it exists.
17. **The implementation moves** (stage (b)). Edit a comment in the
    script: nothing. Change its code: one review item for the script,
    naming every card that points at it; 「全部口径未变」 appends one
    `reaffirmed` per card; 「口径已变」 on one of them opens its next draft.
    With `chunk` named, a change outside the chunk raises nothing and a
    removed chunk raises 「找不到该代码块」. Make the script unreadable:
    nothing comes under review. The view says 「指定范围未变（范围外未比对）」
    for the narrowed card and 「大小未变（内容未比对）」 for an input above
    50 MB whose bytes changed at the same size — never an unqualified
    "unchanged" — and 「完整比对」 then finds the change.

### 9.12 Rulings made while building V5

Decisions the implementers took where this section was silent, or
questions they sent back rather than guess; ruled on by the design lead
and binding like the rest.

*The ledger.*
- **`settles`.** A new entry cannot be told from the other copy's later
  entries by position alone. An entry Research OS appends to a file that already
  holds an anomaly names the anomalous entries it settles (`settles =
  [ids]`); that, not its position, is what settles its link. The conflict
  region begins where the two copies diverged, not at the first entry that
  looks out of order — it can only mark more links as conflicted, never
  decide more.
- **An emptied ledger is not "no judgments".** Once a ledger has held an
  entry, a file with none is a file that shrank (9.3) and is asked about.
  The way to have no judgment in force is to withdraw each one: the record
  is append-only in spirit as well as in bytes. 「以文件为准」 is refused
  when the file as it stands could not be applied afterwards.
- **An answer belongs to the question that was shown.** The shrink answer
  names the entries the researcher saw; if the file has changed since, the
  question is asked again.
- **A judgment Research OS cannot place** — its link is not in the index and no
  scan speaks for it — is 「图谱里还没有这条关联」: kept, listed, not
  counted as under review. One made while its source cannot be read is
  held at the machine's status until a scan reads the source.

*Identity.*
- **A lost identity file has three answers**: restore `project.toml` from
  its snapshot when one exists; 「沿用这些记录，建立新身份」 (`researchos project
  adopt`: a new id, every record kept, a fresh index built from them); or
  「这是另一个项目」.
- **A limit, stated.** When a copy is put at the home's recorded path and
  the original is renamed aside, Research OS can ask only the original (it
  recognises it by inode); it has no way to go looking for the other
  folder. Each folder's records are its own files either way.
- 8.10's "a stable hash of the project's resolved path" and 8.11's "a
  project that is moved gets a new graph id" are superseded by 9.4.

*Evidence.*
- **Candidates** (9.6) are links that first appeared in the scan in which
  the judged link stopped being produced, with the same type and
  extractor and the same basis, from the same source *or sharing an end
  with it* — the second clause is what lets a renamed script's read be
  offered at all. **For claims the basis clause is dropped**: a reworded
  claim has, by construction, a different basis, so its candidates are
  the new `backed_by` links from the same file to the same experiment.
  A candidate is a prompt and nothing else.
- **`pyfig` cannot meet 9.9 #8(a).** Its link runs from the *commit* that
  last touched the `savefig` line, so committing any edit to that line
  makes a different link. The judged one goes under review with the new
  one as its candidate. Re-rooting that link on the script is extractor
  work for later; the researcher's project has no such links today.
- In a git project the inventory is what git tracks: a script renamed in
  Finder is scanned again once it is tracked.

*Migration.*
- **On disk, identity comes first.** `project.toml` is created with
  `migrating_from` before anything is exported, so a crash can never leave
  records in a folder with no identity. Then the scan, the export on that
  scan's bases, the verification, the retirement, and the flag cleared. A
  migration that stopped resumes on the next `researchos migrate`, not on every
  open.
- **"Exactly one basis"** means: the *set* of call names over the old
  index's stored occurrences equals the fresh scan's basis. A link one
  scan legitimately produces through two calls is not sent to review for
  that.
- The unreadable count stops the migration if either a judged link or any
  scanned source is unreadable.
- A link whose status changed in the old index after it was exported is
  exported again as a later entry. There is no "abort": a migration that
  cannot retire names the process holding the old index and waits.
- **Only a process that actually holds the old index blocks its
  retirement** — an engine serving *this* project, or any process with the
  database file open. Another Research OS window on another project does not.
- A migrated entry's time is the migration's, and is shown as 「迁移自旧
  索引（原判断时间未知）」, never as the time the judgment was made.

*The command line.* Every subcommand takes the project either as a
positional path or as `--path`.

*Found in acceptance on the researcher's project, 2026-10-05.*
- **A pre-V5 project is frozen until it is migrated, scans included.** The
  old index is at an older schema and nothing new may land in it before
  the migration reads its own count: the watcher does not run for such a
  project, `researchos ingest` / `attempts` / `mappings` refuse with "migrate
  first", and the page does not try to save a viewport. Reading works.
- **A lost identity file is recognised by its snapshot too.** A folder
  with no `project.toml` and no record files, but with a snapshot of
  `project.toml` under `.rce/backups/`, is asked (restore / new identity)
  rather than silently given a fresh id by `researchos init`.
- **A confirmed version file that has vanished or cannot be read** has no
  edited text to keep, so its question has other answers: a vanished file
  offers 「按冻结副本放回」; an unreadable one offers only "keep these bytes
  as the next draft and put the frozen text back".
- **Two histories in a card's log are settled by naming what stands.**
  `researchos variable settle <id> --keep <entry id>…` (and the same from the
  card's page, 「以这一条为准」 beside each disputed entry) appends a
  `settled` entry that names the anomalous entries it settles and, for
  each version number in dispute, the one confirmation that stands. The
  others stay in the history, not in force. Nothing is chosen by position
  or by time.
- **`.rce/README` says where things are now.** It is rewritten whenever
  the identity check adopts, migrates or builds an index: the index's
  current location and the record files this folder holds.
- **The first launch after an install asks for the Documents folder.**
  macOS treats a rebuilt, ad-hoc-signed `Research OS.app` as a new application
  and suspends the engine's first read under `~/Documents` until the
  researcher answers its prompt — observed as an engine that starts and
  never listens while the screen is locked. The shell's waiting page says
  so (「如果系统询问是否允许 Research OS 访问"文稿"文件夹，请点"允许"」) instead of
  showing a log tail that has nothing in it, and the engine's output is
  unbuffered so that its startup line reaches `serve.log` at once. `researchos
  app` does not rebuild a bundle whose sources have not changed, so the
  question is asked once per real change of the shell, not per install.
- **Research OS runs on Python 3.11 and on Linux as it says it does.** The path
  convention must not depend on a later argparse; naming the process that
  holds an index must work from `/proc` where there is no `lsof`.

## Section 10 — Adding a project from the app (task V6)

*Status: APPROVED FOR IMPLEMENTATION, 2026-10-07, at the researcher's
request ("我需要一个手动增加新项目的功能"). This is the first piece of the
fourth-phase plan's step 3, lowering the cost of getting a project into
RCE. Sections 8.8 (product language and errors), 9.4 (identity) and 9.12
apply throughout.*

### 10.0 What is missing

A folder becomes an Research OS project only through the command line (`researchos init`,
then `researchos ingest`, then `researchos serve <path>`). The app can switch only among
folders already registered, the switcher appears only once there are two,
and the app never scans a project in full by itself — the watcher re-reads
the attempt table, the record files and the step scripts when they change,
nothing more. With an empty registry the engine refuses to start at all,
so on a new machine `Research OS.app` opens onto an error.

### 10.1 Where it lives

- **The project menu.** The project line in the header becomes one button,
  「项目：<名称> ▾」, shown always, even with a single project. It opens a
  small menu: the registered projects (the open one marked; one whose
  folder cannot be found shown greyed as 「找不到文件夹」 with its own
  「选择新位置…」); a rule; 「添加项目…」; 「重新扫描这个项目」;
  「重命名显示名称…」; 「从列表中移除…」. It replaces the `<select>` and the
  separate 「移除失效项目」 button.
- **The native menu.** 文件 → 「添加项目… ⌘O」 (a whitelisted command,
  `add-project`).
- **No project yet.** With an empty registry the engine starts anyway, in a
  no-project state, and the page shows only this: two sentences on what Research OS
  does (it reads a research folder's scripts, data and drafts, and keeps
  the researcher's judgments about them), 「添加项目…」, and one line saying
  that a project is simply the folder that holds a piece of research.

### 10.2 Adding: choose, look, then write

**1. Choose.** In the native shell, a folder panel (directories only,
message 「选择要加入 Research OS 的项目文件夹」) through the existing bridge, which
gains a purpose (`add` | `locate`) that changes only the panel's message.
In a plain browser, a path field (`~` is expanded).

**2. Look before writing.** The page asks the engine what it sees
(`POST /api/projects/inspect {path}`). Inspecting writes nothing anywhere,
reads no file's contents and never makes a cloud file download: it lists
the folder (or, for a git repository, asks git which files it tracks) and
stats what it lists. The answer is exactly one of:

| What the folder is | What the researcher is shown |
|---|---|
| already in the list | 「这个项目已经在列表里」 — 「切换过去」 |
| an Research OS project (`.rce/project.toml`) | its name and, from the identity check (9.4), what opening it means. Normal, moved, or no index on this machine: 「加入列表并打开」. A copy, a lost or unreadable identity, an original that cannot be checked: the 9.4 question and its answers, here; nothing — the registry included — is written until one is chosen |
| an Research OS project from before V5 | 「这是旧版 Research OS 项目：加入后需要先迁移，才能记录判断」 — 「加入列表并打开」; the migration banner (9.5) follows |
| a folder Research OS has not seen | the preview below — 「加入并扫描」 / 「取消」 |
| something that cannot be a project | why, in one sentence (below), and nothing to press but 「重新选择」 |

**The preview** says, in numbers, what Research OS would read — and these are the
numbers the first scan will in fact read, computed by the same inventory
the scan uses: scripts (`.py`, `.R`, `.Rmd`), data files, drafts (`.md`,
`.tex`), images. For a git repository it says that Research OS reads the files git
tracks, how many those are, and how many more files sit in the folder
untracked and will not be read (「另有 N 个未被 git 跟踪的文件不会被扫描」) —
a project whose new scripts are not yet committed must not look emptier
than it is without saying why. Files still in the cloud are counted and
said to be read once downloaded. Above 5,000 files to scan the preview
says so and asks a second time; size alone refuses nothing. It says what
adding writes: a `.rce/` folder inside the project holding its identity
file and a short README, and an index under `~/.rce` — nothing else in the
folder is created or changed. The display name defaults to the folder's
name and can be edited here.

**Refusals**, each one sentence and no write:
- not a folder, does not exist, cannot be read;
- a top-level folder rather than a project: `/`, the home folder itself,
  `~/Documents`, `~/Desktop`, `~/Downloads`, `~/Library`, the iCloud Drive
  root, a disk's root under `/Volumes`, and anything inside `/System`,
  `/Library`, `/Applications`, `/usr`, `/bin`, `/sbin`, `/etc` — 「请选择
  具体的项目文件夹，而不是「文稿」这样的总文件夹」 (matched exactly for the
  top-level folders, by containment for the system ones);
- the Research OS home (`~/.rce`) or anything inside it;
- inside a registered project, or containing one: 「这个文件夹在项目「X」
  里面」 / 「这个文件夹里已经有项目「X」」 — the same files may belong to only
  one project, otherwise two ledgers would judge the same links.

**Waiting for the system.** Listing a folder under `~/Desktop`,
`~/Downloads` or another protected place can be held by macOS until the
researcher answers its permission prompt. Inspecting therefore has a
deadline (5 seconds); past it the answer is 「正在等待系统授权访问这个
文件夹……如果系统询问，请点“允许”」 and the page asks again until the listing
returns. Nothing hangs.

**3. Write** (`POST /api/projects/add {path, label, inspected}`). The
request carries the token the inspection returned, which names what was
seen (the kind, the identity if any, the counts); if the folder is no
longer what was inspected, adding is refused and the page inspects again.
For a new folder, in order: create `.rce/` and `.rce/project.toml`
exclusively, the index under the new id, the README; register the folder
with its display name; open it; then start the first full scan in the
background. The page is usable at once and shows the scan's progress in
the header (「正在扫描：<步骤>（<n>/<m>）」); the views fill when it ends.
Files that cannot be read are reported as such (9.6) — a failed read is
not a failed addition.

### 10.3 Rescanning

「重新扫描这个项目」 runs that same full scan — every source extractor, the
attempt table and its check, the hand-drawn links, then the record
applied — under the project lock, in the background, with the same
progress line; one scan at a time per project (a second request says one is
running). A project from before V5 is not rescanned until it is migrated
(9.12). The command line already has this as `researchos ingest`.

### 10.4 The list

- 「重命名显示名称…」 changes the registry label only — never the folder.
- 「从列表中移除…」 removes the registry entry only. The confirmation says
  that the folder, its `.rce/` records and its index stay where they are,
  and that adding the folder again brings everything back. Removing the
  open project opens the next one, or the no-project state.

### 10.5 Command line

`researchos projects add <path> [--label L] [--yes]` (without `--yes`, prints the
preview and writes nothing), `researchos projects rename <path|id> <label>`, and
the existing `researchos projects list | remove`. `researchos serve` with an empty
registry starts in the no-project state instead of exiting.

### 10.6 Safety

These are the first endpoints that let the page name an arbitrary folder
to write into. Every one of them (`inspect`, `add`, `rename`, `rescan`)
runs `_check_local_origin` first. The chosen path is resolved, symlinks
followed, and the resolved folder is the one checked against the refusals
and the one written into. Adding writes only `.rce/` inside a folder that
exists, and the index and registry under the Research OS home. Inspecting reads
directory listings and file metadata only, stops counting at 100,000
entries and says so, and never follows a symlink out of the folder.

### 10.7 The native shell

The bridge's `choose-folder` message gains `purpose`; the panel's message
is 「选择要加入 Research OS 的项目文件夹」 for `add` and 「选择项目文件夹的新位置」 for
`locate`; any other value is ignored. The 文件 menu gains 「添加项目… ⌘O」.
This rebuilds the shell, and macOS will ask once more for access to the
Documents folder (9.12).

### 10.8 Acceptance

1. **First launch.** With an empty registry the app opens on the no-project
   page; 「添加项目…」 adds a folder; it scans with progress; the views fill.
2. **A new folder, not a git repository.** The preview's counts equal what
   the first scan reads. Afterwards the folder holds `.rce/project.toml`,
   `.rce/README` and the identity's snapshot under `.rce/backups/` and is
   otherwise unchanged — every other file's bytes and
   modification time compared before and after; the registry has the
   chosen display name; nothing is under review.
3. **A git repository with untracked files.** The preview names the tracked
   count and the untracked count, and the scan reads exactly the tracked.
4. **Already in the list.** Offered 「切换过去」; nothing written.
5. **A copy of a V5 project.** The 9.4 question in the add dialog; nothing
   written, the registry included, until it is answered; each answer then
   behaves as 9.4 says.
6. **A project from before V5.** Registered and opened; the migration banner
   shows; no scan writes into its old index.
7. **Refusals.** `/`, the home folder, `~/Documents`, a file, a missing
   path, `~/.rce`, a folder inside a registered project, a folder containing
   one: each refused with its sentence; nothing written anywhere.
8. **The folder changed between look and write.** A `project.toml` appears
   after inspecting: adding is refused and the page inspects again.
9. **The list.** Rename; remove another project; remove the open one: the
   next opens, or the no-project page; the folders and their records are
   untouched.
10. **Rescan.** From the menu, with progress; a second request while one
    runs is refused; refused on a project from before V5.
11. **Origin.** Cross-origin and wrong-Host requests to every new endpoint:
    403, and nothing written.
12. **Waiting for the system.** An inspection whose listing blocks (made to
    block in the test) answers with the waiting state within the deadline
    and completes when the listing returns.

### 10.9 Rulings made while building V6

- **What adding writes.** Besides `.rce/project.toml` and `.rce/README`,
  adding writes the identity's snapshot under `.rce/backups/` (9.12 needs
  it to recognise a lost identity). Acceptance 2 reads accordingly, and the
  add dialog lists all three.
- **What inspecting reads.** No file's contents except Research OS's own identity
  files — the folder's `.rce/project.toml`, its snapshots, and for a moved
  project the old home's `project.toml` — and none of those while they are
  in the cloud: a cloud identity file is reported as such and its download
  is not requested.
- **A folder from before 8.10.** Opening a folder that still holds
  `.rce/graph.db` moves that database out of it (8.10, rule 1: copied,
  checked, then removed). Adding from the app does the same; the dialog
  says so before anything is written.
- **Git in a folder that is not yet trusted.** Every git command Research OS runs
  disables the repository's own `core.fsmonitor` and takes no optional
  locks, so looking at a folder cannot run a program the folder names.
  Still to do: `git blame` without textconv and `git log` without signature
  checking, for the same reason, in the scans that follow consent.
- **A project's display name survives a move.** Re-attaching a moved
  project keeps the name the researcher gave it.

## Section 11 — Connections: OneDrive, GitHub, Zotero (task V7)

*Status: APPROVED FOR IMPLEMENTATION, 2026-10-09. The researcher asked for
connections to OneDrive, GitHub and Zotero and, shown what each could mean
on their own machine, chose: OneDrive folders used as projects; a project
linked to its GitHub repository, and pushed there as a backup; citations in
the drafts tied to the literature they cite. Sections 0, 8.8, 9 and 10
apply throughout. No connection stores a password or a token; RCE uses the
logins the researcher already has (the OneDrive client, `gh`, git's own
credential helper) or none.*

### 11.0 What the researcher's machine looks like (2026-10-09)

- `~/Library/CloudStorage/OneDrive-个人` exists (376 files, mostly books and
  papers), but the OneDrive client is no longer installed: files that are
  only in the cloud cannot be downloaded.
- A Zotero data directory exists (`~/Zotero`, 18 items, last changed June
  2023); the Zotero program is not installed.
- The drafts cite in prose: about 950 author–year citations in half- and
  full-width brackets (`Simon（1955）`, `（Kahneman & Tversky, 1979）`,
  `Chen & Peng (2010)`), 94 distinct DOIs, 8 drafts with a reference list,
  no citation keys and no `.bib` file.
- `gh` is logged in with repo scope. `rmb-hysteresis` is a git repository
  with no remote. The thesis project is not a git repository, is 2.2 GB, has
  six data files of 146–172 MB, and lives in the iCloud-synced Documents
  folder.

### 11.1 OneDrive (and other synced folders) as project locations

A folder inside `~/Library/CloudStorage/<provider>-<account>` is added like
any other (Section 10). What changes:

- The provider's root (`OneDrive-个人` itself) and `~/Library/CloudStorage`
  itself are top-level folders and are refused with 10.2's sentence.
- Research OS knows which provider holds a folder (from the directory name under
  `CloudStorage`) and whether its client is running. When files are only in
  the cloud and the client is not running, Research OS does not try to read them —
  a read would wait forever — and says so wherever those files are counted:
  in the add preview and in 「来源文件暂不可读」 (「这些文件只在 OneDrive 云端；
  OneDrive 客户端没有运行，打开它并登录后 Research OS 才能读到」). With the client
  running, a cloud file is read the same way an iCloud one is (8.10).
- Nothing else: the index is already outside the project (9.1), the lock is
  in `~/.rce/locks` (9.7), records are plain files the provider syncs.

### 11.2 GitHub: a project linked to its repository

A project whose git remote is on `github.com` (https, `git@` or `ssh://`
forms; the owner and name checked against GitHub's character set) is
*linked*. Nothing is configured; nothing is stored.

- **Where it shows.** The project menu gains one line, 「GitHub：owner/repo」,
  and under it the state as of the last fetch: 「本地领先 N 个提交，落后 M
  个（<日期> 获取）」, or 「与 GitHub 一致」, or 「还没有从 GitHub 获取过」.
  「从 GitHub 获取最新状态」 runs `git fetch` for that remote — the only
  network Research OS does here, only on that click, never `pull`, never a merge.
- **Links.** A script, data file or draft that git tracks gets 「在 GitHub
  上查看」, pinned to a commit so the link never changes under the reader:
  the newest commit that GitHub already has (the upstream branch's tip, or
  the remote's default branch). If that commit does not contain the file,
  there is no link and the reason is said: 「这个文件还没有推送到 GitHub」. If
  the local file differs from that version: 「本地有未推送或未提交的改动」.
  A commit gets a link only when GitHub has it.
- **Every git command Research OS runs** has the repository's `core.fsmonitor`
  disabled and takes no optional locks (10.9), so linking a folder never
  runs a program the folder names.

### 11.3 GitHub: pushing a project as a backup

「推送到 GitHub…」 in the project menu. Pushing is the first thing Research OS does
that writes somewhere other than this machine; every push is shown before
it happens and needs its own confirmation — nothing is remembered as "always
push".

**The dialog shows**: the branch; the commits GitHub does not have yet (count
and their first lines); uncommitted changes, which a push does not carry
(「另有 N 个文件的改动还没有提交，推送不包括它们」); and whether the
project's human records (`.rce/`) are committed. One tick, 「先把人工记录提交
一次」, commits exactly the record files under `.rce/` — never any other file,
never the researcher's own staged changes — with the message 「Research OS：人工记录」.

**Without a GitHub remote**, the dialog first creates a **private**
repository under the account `gh` is logged in to (name defaults to the
folder name, ASCII-only, editable), adds it as `origin`, then pushes. Research OS
never creates a public repository.

**Refused, with the reason and no push:**
- `gh` is missing or not logged in (for creating a repository) — the exact
  command to run is shown; Research OS never asks for a password or token;
- detached HEAD, a merge or rebase in progress, no branch;
- the push is not a fast-forward (「GitHub 上有你本地没有的提交，请先在终端里
  处理」) — Research OS never force-pushes;
- a file over 100 MB in the commits to be pushed (GitHub rejects it): the
  files are named;
- the remote is not on GitHub.

**A folder that is not a git repository** is not turned into one in V7. The
dialog says so and, when the folder is inside an iCloud, OneDrive or other
synced folder, why: sync services and git's internal files do not mix, and
a repository there can be damaged by the sync. The way forward it names: move
the project to a folder that is not synced (9.4 recognises the move), then
`git init` there. For the thesis project it also names the six data files
over 100 MB that GitHub would refuse.

### 11.4 Citations: the drafts tied to the literature

A new extractor, `citations`, reads every draft (`.md`, `.tex`).

**What it finds.**
- **DOIs**, normalised: lower case; trailing punctuation removed, half- and
  full-width (`.,;:)）。，；`, backticks, markdown brackets).
- **Author–year citations** in Latin script: narrative (`Simon (1955)`,
  `Simon（1955）`, `Chen & Peng (2010)`, `Chen and Peng (2010)`, `Hassan et al.
  (2019)`) and parenthetical (`(Hansen, 2000)`, `（Samuelson & Zeckhauser,
  1988）`, several separated by `;` or `；`), years 1900–2099 with an
  optional letter. Each occurrence keeps its file, line, the exact text, the
  section, and — when a claim (Section 4) covers that sentence — the claim.
  Chinese-script names (`张川川（2020）`) are not read in V7: the bracketed-
  year pattern alone cannot tell a name from an ordinary phrase, and V7
  does not guess.
- **The draft's own reference list** — the entries under a heading 「参考文献」,
  `References` or `Bibliography` — each entry with its first author's
  surname, year (and letter), and DOI if it has one.

**How a citation is resolved**, in this order, each step deterministic:
1. **Within the draft.** An author–year citation that matches exactly one
   entry of the same draft's reference list by first surname and year (and
   letter) is that entry — the convention the citation itself relies on.
   Two entries matching is ambiguous, and both are offered as candidates.
2. **By DOI** — written in the text, or carried by the matched entry — to the
   item in the researcher's Zotero library with that DOI. The library is
   read from Zotero's data directory (from Zotero's preferences, else
   `~/Zotero`), opened read-only and never written; when Zotero is running
   and holds its lock, a copy is read.
3. **By DOI, online — only when the researcher turns it on.** A setting,
   off by default, 「用 DOI 联网查文献信息」, with the sentence 「只发送 DOI，
   不发送文稿内容」; lookups go to doi.org / Crossref, are cached under the
   Research OS home with their date, and a failure is retried later, never recorded
   as "not found".
4. **Author–year without a DOI path** — Zotero items whose first creator's
   surname and year match are *candidates* (`pending`, as claims' are):
   Research OS does not decide that "Simon 1955" is a particular item; the
   researcher confirms one, through the ledger (9.3).

**In the graph.** References become nodes — `ref:doi:<doi>` when a DOI is
known, `ref:zotero:<item key>` otherwise, `ref:entry:<draft>#<n>` for a
reference-list entry with neither — and each citation an edge `cites` from
its section and, when there is one, its claim, with extractor `citations`.
Resolved by an identifier (steps 1–3): `auto`. Candidates (step 1 when
ambiguous, step 4): `pending`. The basis (9.6) of a `cites` link is the
citation's normalised surname and year or its DOI, and the matched entry's
DOI — so a citation whose reference-list entry changes its DOI comes under
review.

**The app gets one view, 「文献」.** Per draft, its citations: the text as
written, what it was resolved to (authors, year, title, journal) and how —
「Zotero」, 「文末条目」, 「DOI 联网」 — or 「未找到」 or 「N 个候选，待确认」 with
确认 / 否决 per candidate (ledger entries). A summary at the top: 「N 处
引用：已对上 X，待确认 Y，未找到 Z」, with the drafts that have the most
unresolved first. Actions: open the DOI; 「在 Zotero 中打开」 only when the
Zotero program is installed; 「打开 PDF」 for a Zotero attachment — the one
place Research OS opens a file outside the project, so it opens only a file that
the Zotero database names for that item, inside Zotero's storage
directory, never a path the page supplies. The setting for online lookups
lives at the top of this view. Showing which claims cite what — from the
claim's side — belongs to the result-review phase.

### 11.5 Acceptance

1. **OneDrive.** A folder in a CloudStorage fixture with cloud-only files
   and no client running: added; the preview and the scan say why those
   files are not read; nothing waits. The provider root is refused.
2. **GitHub link.** A repository whose remote is a local stand-in for
   GitHub: the menu line, the state after 「获取最新状态」, a link pinned to
   the pushed commit, no link for a file not yet pushed, the local-changes
   note. A remote URL with an owner or name outside GitHub's character set
   is not linked.
3. **Push.** Into a local bare repository standing in for GitHub, with `gh`
   replaced by a test double: the dialog lists exactly the unpushed commits
   and the uncommitted files; 「先把人工记录提交一次」 commits only `.rce/`
   files even with other changes staged; no remote → a private repository is
   requested and then pushed; each refusal of 11.3 triggered and nothing
   pushed; never a force push. No test or check pushes to github.com.
4. **Not a git repository.** The dialog explains; inside a synced folder it
   says why; the large files are named; nothing is written.
5. **Citations, on a copy of the researcher's drafts.** Every DOI and every
   Latin author–year citation found (checked against a hand count on two
   drafts); in-draft resolution against the reference list; DOI → Zotero on
   a copy of their library; ambiguity offered as candidates; nothing
   resolved by surname alone without being `pending`; Chinese-script names
   not read; the online lookup does nothing until turned on and sends only
   DOIs (checked with a recording stand-in for the network).
6. **文献 view.** Summary counts equal the graph; confirm and reject a
   candidate (a ledger entry each); 「打开 PDF」 refuses any path that is not
   the item's attachment inside Zotero's storage directory.
7. **Origin and secrets.** Every new endpoint answers 403 cross-origin; no
   token, password or credential is written anywhere Research OS writes.

### 11.6 Rulings made while building V7

- **A full reference is an entry wherever it is written.** A line, list
  item or table cell that begins with a full reference in author–initials
  form — `Simon, H. A. (1976). From substantive…`, `Gigerenzer, G., &
  Goldstein, D. G. (1996). Reasoning…` — is a reference-list entry of that
  draft, whether or not it sits under a 参考文献 heading, and is never
  counted as an in-text citation. (On the researcher's drafts, 93 such
  references in 7 drafts sat outside a recognised heading, several in
  tables, and were being reported as citations 「未找到」; recognising them
  raised the resolved citations from 464 to 569.) The format itself is the
  evidence; the heading was only a shortcut to it. A list item's trailing
  commentary is part of its entry's text in V7.
- **A citation next to a DOI for the same work.** When one section cites a
  work by DOI and also by author–year, the graph holds one link (section →
  reference), and the author–year occurrence cannot be judged separately.
  The view says so (「同一节里另一处引用已经凭 DOI 或文末条目对上了这篇文献」)
  and counts it with 「待确认」. Per-occurrence judgments belong to the
  result-review phase.
- **Chinese name joined to a Latin one.** 「张川川与 Simon（2020）」 cannot be
  told from prose such as 「增加与 Fatum et al. (2017)」 (14 real citations on
  the drafts, the hazard 0 times). Such citations are read but never
  resolved by that name alone: even a unique match is only a candidate.
- **Hyphen-joined author lists** (`Pesaran-Shin-Smith (2001)`) are matched
  as lists when the whole name matches no entry as written.
- **A reference-list entry without a DOI** is identified by its position
  (`ref:entry:<draft>#<n>`), so the link's basis carries the entry's text:
  inserting an entry above it puts a judgment on it under review instead of
  carrying the judgment to another paper.
- **GitHub.** The repository's own git configuration — `.git/config`, its
  includes and `config.worktree` — may not name a program Research OS would run
  (ssh command, upload-pack, credential helper, filter drivers): fetch and
  push are refused with a sentence telling the researcher to run them in a
  terminal, and status reads switch the repository's own filters off.
  Creating a repository and pushing are two clicks (「创建私有仓库」, then
  「推送」), so the only control that pushes is 「推送」. Whether `gh` is
  logged in is checked only when 「创建私有仓库」 is pressed, because checking
  it touches the network. A cherry-pick, revert or bisect in progress
  refuses a push like a merge does.
- **Zotero.** 「在 Zotero 中打开」 goes through the engine (`open`), because
  the shell sends only web links out; it is shown only when the Zotero
  program is installed. Group libraries are not addressed in V7.
- **Drafts are not watched.** Citations are read by the full scan
  (「重新扫描这个项目」, `researchos ingest`), as the other draft extractors are
  (10.0).

## Section 12 — Research OS: one person, many projects; a team, one record (task V8)

*Status: APPROVED FOR IMPLEMENTATION, 2026-10-11. The researcher renamed the
product and widened it: "Research OS — single-person multi-project management
and multi-person collaboration; every node shows what changed and its notes;
a proposal or an attempt does not become a node until the PI approves it; a
figure in the paper traces to the script that made it, its input data and
the attempt it belongs to; an abandoned route keeps the basis it was
abandoned on; as a node moves forward, you can see where things changed."
Their decisions: the shared drive (e.g. OneDrive) stores the material and
the PI's computer is the main server; changes start at either end; new
attempts, verdict changes and new variable definitions need the PI's
approval; rename everything, the command included, and the hidden folders
with a safe migration; build collaboration now, tested with a real second
person. Sections 0, 4, 8.8, 9, 10 and 11 apply throughout.*

### 12.0 What Research OS is — and is not

The researcher asked whether this is "an ERP with a dashboard plus shared-
drive management". Half of that is right and kept: roles, an approval
workflow, an audit trail of who changed what and when, a home page that
shows the state of the work, the shared drive as storage. Half is rejected,
deliberately:

- **No data entry.** An ERP runs on people filling forms for a process that
  is already known. Research is the process being discovered. The moment
  researchers must type records by hand, the records go stale and every
  trace leads to a stale answer. Research OS keeps Section 0's first
  principle: it reads what researchers already produce — scripts, data,
  drafts, the attempt table — and people do only three things by hand:
  write notes, make proposals, approve.
- **No statistics dashboard.** "24 attempts, 38 % of citations resolved"
  helps nobody decide anything. The home page is a desk: what waits for
  *my* decision, what changed since *I* last looked and exactly where,
  which results lost their evidence, which routes were abandoned and why. (Since V10 the desk is
  the route map with what waits for me beside it: Section 14.)
- **Not a file manager.** OneDrive already manages files. Research OS
  manages what the files mean, how they relate, and which changes the
  team has accepted.

In one line: **a lab notebook that writes itself, with GitHub-style review
for decisions, stored on the shared drive.**

### 12.1 People, roles and the single-person case

- **Who I am.** Set once per machine (name and the e-mail of the shared-
  drive account) in `~/.researchos/me.toml`. Every entry RCE writes from now
  on carries `by = "<member id>"`. Entries without `by`, written before V8,
  belong to the project's first researcher.
- **Who is in a project.** `.researchos/team.toml` in the official version:
  members `{id, name, role = "pi" | "member", key}` (`key`: 12.5). Written
  only by the PI's Research OS.
- **One person.** A project without a team file — or whose only member is
  its PI — has no approval step at all: you are your own PI, your changes
  are official when you make them. Everything in 12.3 applies only to
  projects with members.

### 12.2 Three places, one writer

1. **The official version** (正式版本) is the project folder on the PI's
   computer. Only the PI's Research OS writes it. The PI's computer is
   never contacted over the network by anyone; it is the place where
   approvals are applied, nothing more. ("Main server" means exactly this.)
2. **The shared drive** (公盘) is a folder on OneDrive — or any synced
   folder — shared with the team. It has two areas:
   - `已发布/` — written only by the PI's Research OS. Members get **view
     only** permission in OneDrive's own sharing settings; that permission
     is what makes "only the PI can approve" a rule rather than a request.
     It holds the published snapshots of the official version and the
     outcomes of proposals.
   - `提议/<member id>/` — each member's own area, writable by that member
     and readable by the PI.
3. **A member's working copy** is a project folder on the member's
   computer, made from the latest published snapshot when they join.

**Everything on the shared drive is written once, under a name used once,
and never edited in place.** A snapshot, a proposal, an outcome: each is a
new directory, written under a temporary name and completed by a final
`ready.toml` written last; a reader ignores anything without it. No live
database, no git repository and no record ledger is ever kept on the shared
drive — sync services and files edited from two places do not mix (8.10,
11.3). Two people never edit the same file there, so the shared drive never
produces a conflict copy of anything Research OS needs.

**Snapshots.** `已发布/snapshots/<seq>/` holds a manifest (every file of the
official version: path, size, sha256; seq increasing by one; the previous
snapshot's manifest hash, so snapshots form a chain), the text files that
changed since the previous snapshot, and the record files. Large files are
stored once by content under `已发布/blobs/<sha256>` and named in the
manifest — a 170 MB data file uploaded once is never copied again.
Snapshots are kept; the PI may prune old blobs no snapshot after a chosen
date references.

### 12.3 Changes from either end

**A member proposes.** In their working copy a member works as usual —
edits scripts, adds data, writes a new row in the attempt table, drafts a
variable card. 「提交提议」 gathers what differs from the official version
they last synced, shows it to them first, and writes one proposal to their
area: what it is (12.4), a sentence of their own, the files added or
changed (text files with their diff against the official version; large
files once as blobs), and the official snapshot it was made against.

**The PI decides.** The PI's Research OS watches the members' areas. A new
proposal appears in the PI's inbox with exactly what approving would do to
the official version: the attempt-table rows added or changed (as a diff),
each file added or changed (line diffs for text; size, fingerprint and, for
tables, rows and columns for data), the variable versions it would confirm.
If the official version has moved since the proposal's snapshot and the
same files changed on both sides, the PI is told which, and the proposal
cannot be approved until the member re-proposes against the newer snapshot
— Research OS never merges two people's edits of one file by itself.
Answers: 「批准」 (applied to the official version, every entry recorded
with `by` = the member and `approved_by` = the PI and the PI's note),
「退回并说明」, 「讨论」 (a note to the member, the proposal stays open).
Approving publishes a new snapshot; returning publishes an outcome with the
reason. Both are visible to the member.

**The PI changes things directly** in the official version — the PI's own
acts are approved by definition — and 「发布」 publishes a snapshot.

**A member syncs.** 「同步」 brings the working copy to the latest snapshot:
files the member has not touched are updated; a file the member changed
locally that the official version also changed is left as the member's and
the official one is put beside it (`<name>.official.<ext>`) with the
difference shown — again, never a merge. A member's pending proposals stay
pending until their outcome arrives.

### 12.4 What needs approval

From the researcher's decisions:

| Change | In a project with members |
|---|---|
| A new attempt (a route proposed) | proposal; becomes a node only when approved |
| An attempt's verdict (adopted ✅, abandoned ☠️, …) | proposal; the result and reason are required when abandoning |
| A variable's new definition version | proposal; confirmation happens when approved |
| A file of the official version added or changed | proposal (it is how the member's work reaches the official version) |
| A note on a node | no approval; travels with the next sync of the member's area, appended with `by` |
| Confirming / rejecting a machine link | no approval; same path as notes |
| Drawing or removing a 「源自」 line between attempts | proposal (`route_link`, Section 14.2) |

**"Does not become a node until approved"** is literal: a pending proposal
is not in the official index, not in the decision tree, not in any trace.
Each person sees it as a proposal — the member in 「我的提议」, the PI in the
inbox, everyone else not at all. A returned proposal never becomes a node;
its reason stays in the proposal's history.

**An abandoned route keeps its basis.** Abandoning (☠️) through a proposal
requires the result and the reason; approving records them in the attempt
table (as the researcher's table already does by hand) and freezes the
attempt's evidence at that moment: the files of its step scripts and the
outputs they wrote, by fingerprint (and, for text, by content in the
history store, 12.6). A year later the route can be opened and shows what
was tried, what came out, and why it was dropped — even if the scripts have
since changed or gone.

### 12.5 Is a snapshot really the PI's?

OneDrive's sharing permissions are the first guard. The second is a
signature: the PI's Research OS creates an SSH signing key on first use
(`ssh-keygen -t ed25519`, part of macOS and every Linux — no new
dependency) and signs each snapshot manifest and outcome (`ssh-keygen -Y
sign`); the public key is in `team.toml`, which members receive when they
join. A member's Research OS verifies every snapshot (`ssh-keygen -Y
verify`) and refuses one that is not signed by the PI's key, says so, and
keeps the last good one. A member's proposals are signed with the member's
key the same way, so the PI knows who sent what. Where `ssh-keygen` is
missing, Research OS says that snapshots cannot be verified and relies on
the sharing permissions alone.

### 12.6 Every node: what changed, and the notes

- **History.** Each node that is a file (script, data, draft, figure) has
  versions: every content Research OS has seen, with when, who (when known:
  the member a proposal came from, the PI who approved it, or "本机"), and
  the note that came with it. In a git project the versions are git's own
  commits. Otherwise Research OS keeps them: text files up to 5 MB by
  content in a version store beside the index (`history/`, content-
  addressed, never pruned automatically); larger and binary files by
  fingerprint, size and — for CSV/TSV/Excel tables — row count and column
  names. A collaborative project's history is also recoverable from the
  published snapshots.
- **What changed** is shown as a line diff for text, and for data as
  「行数 96 → 97，列不变」 / 「新增列 log_ret」; for an attempt, its verdict
  and result as they moved (🕒 → ✅, with who approved and when); for a
  variable, its versions (9.11).
- **Notes** on any node: `.researchos/notes.toml`, an append-only ledger
  (9.3) of `{node, by, at, text}`; a note can be withdrawn, not edited. On a
  node page notes and changes are one timeline.

### 12.7 Tracing a result

The question "where does this figure come from" is answered on the figure's
page as one chain: the data it was built from → the scripts that read and
wrote it → the figure → the paper section that cites it, with the attempt
it belongs to and the variable versions it used. Where the chain is broken,
the break is shown and offers its repair. Two repairs are new:

- **An attempt's files.** Today an attempt knows its scripts only from step
  numbers in its description (2 of the researcher's 24 attempts). On an
  attempt's page, 「关联文件」 attaches scripts, data and figures to it — a
  hand-drawn mapping (8.5) of type `belongs_to`, stored in
  `mappings.toml`.
- **A figure without a source** gets 「补上来源」: choose the script that
  made it (a `generates` mapping) — the researcher's own figure 1 needs
  exactly this.

### 12.8 The name

Research OS everywhere: the app (`Research OS.app`), the command
(`researchos`; `rce` stays as an alias that prints the new name, for one
release), the Python package (`researchos`), the environment variable
(`RESEARCHOS_HOME`; `RCE_HOME` still honoured), the MCP server, the
documentation, and the GitHub repository (`research-os`; GitHub redirects
the old address). The hidden folders become `.researchos/` in each project
and `~/.researchos/` on each machine. **Migration** is automatic on first
touch and safe in the 8.10 way: under the project lock, `.rce/` is renamed
to `.researchos/` in one step (same volume, atomic), the result is read back
and verified (identity, ledgers, cards parse; every file present with the
same bytes); `~/.rce` is renamed the same way under the machine lock. Any
failure leaves the old folders exactly as they were and says why. A folder
that has both is not touched and is reported.

### 12.9 The interface

The tabs 决策树 / 血缘 / 画布 / 变量 / 文献 are replaced by a sidebar:
projects (switch, add); and, for the open project, **工作台** (the desk of
12.0), **研究路线** (the attempts as a timeline/tree with status, proposals
shown distinctly, abandoned routes with their reasons), **成果** (figures,
tables and claims of the drafts, each opening its trace), **变量**, **文献**,
**数据与代码** (scripts and data, with the lineage report's orphans, chains
and broken links as filters), **提议** (inbox for the PI; 我的提议 for a
member), **成员**. Every object opens the same **node page**: its trace,
its changes and notes as one timeline, its judgments, and its actions. The
canvas becomes 「关系图」, a view inside a node page and inside 数据与代码 —
not a destination of its own. (Except the route map, which is the desk since V10: Section 14.) The visual language stays the paper palette
of 8.2. Desk, node page and proposal review were shown to the researcher
as a concept on 2026-10-11.

### 12.10 Acceptance

1. **Rename and migration** on a copy of the researcher's project and of
   `~/.rce`: everything readable afterwards, ledgers and cards byte-
   identical, `researchos` and `rce` both work; a failure injected at each
   step leaves the old folders untouched.
2. **One person**: no approval step anywhere; history and notes on a
   script, a data file and an attempt; a figure traced to data, scripts and
   attempt; 「关联文件」 and 「补上来源」 repair a broken trace.
3. **Two people, simulated** (two `RESEARCHOS_HOME`s, a local folder as the
   shared drive): join; a member proposes a new attempt with a script and a
   200 MB data file; it is not a node anywhere until approved; approved —
   applied, published, synced, recorded with `by` and `approved_by`; a
   returned proposal shows its reason to the member and never becomes a
   node; a verdict change to ☠️ without a reason is refused; both sides
   changing one file — the PI is told, no merge happens; a snapshot
   written by someone other than the PI (forged manifest) is refused by the
   member's Research OS; a half-written proposal (no `ready.toml`) is
   ignored; a OneDrive conflict copy in the shared drive is reported and
   never read.
4. **Two people, real**: the researcher and their collaborator on two Macs
   and a real shared OneDrive folder go through 3's first five steps.
5. **Nothing is written to the shared drive in place**: after every test
   above, every file there was created once and never modified (mtimes and
   inodes checked).

### 12.11 Visual vocabulary: nothing that looks generated

The researcher, on the 2026-10-11 concept: "the -> kind of symbols, emoji
and other marks of vibe coding must go". Binding for every surface of the
app, the shell and the documentation's screenshots:

- **No emoji** in anything Research OS itself writes on screen. The
  researcher's own attempt table uses emoji as verdict markers (✅ ☠️ 🕒 ⚠️
  ⛔ 📋); the interface shows a verdict as a text status — 现行, 已放弃,
  待定, 不利证据, 被否, 归档 — in a quiet tinted label, followed by the
  researcher's own words. The mapping from marker to status is read from
  `attempts.toml` (`[verdict_labels]`), with these defaults; an unknown
  marker shows as 「其它」 plus the researcher's words. English words for these statuses: 13.4. The raw marker
  appears only where the source file itself is shown (history, diff).
- **No arrow characters as connectors or decoration** — →, ←, ↔, ->, =>,
  ▸, ▾, ＋ and the like. A relation is said in words (读取, 写出, 生成,
  属于, 引用) or drawn: a trace is a set of columns joined by real lines,
  not a sentence of arrows. Disclosure and menus use the platform's own
  controls. Arrows inside the researcher's own text (an attempt titled
  "TopicShift→波动") are their words and stay.
- **Icons sparingly**: monochrome line icons only where they help
  recognition (sidebar sections, file types), never as decoration, never
  colored, never in running text.
- **Typography carries hierarchy**: the serif for the brand and page
  titles, the sans for everything else, the mono only for paths, code and
  identifiers; weight and space instead of boxes, badges and dividers
  where possible.

### 12.12 Rulings made while building the V8 engine

*Names.* Sections before 12 still write `.rce/`, `~/.rce`, `RCE_HOME` and
`rce`: read them as `.researchos/`, `~/.researchos`, `RESEARCHOS_HOME` and
`researchos`. A folder with both names stops every command on it, because
records an old engine wrote into `.rce/` must not be passed over; a home
named by the environment is never renamed; the machine lock for the home is
on the folder that holds it; identifiers the researcher never sees (the
page bridge, the window's saved frame, the remembered tab) keep their old
names so nothing saved is lost.

*History.* The version store of a non-git project is kept material, not a
record: rebuilding the index keeps it, deleting the index's whole folder
loses it, and `researchos records` lists it with its size so that is never
a surprise. A collaborative project's history is also in its snapshots. A
missing `by` is shown as 「最初的研究者」.

*Approvals.* Every approval — not only an abandonment — is an entry in
`decisions.toml`, because an attempt-table row has nowhere to carry `by`
and `approved_by`. An abandonment's reason is written into the row's result
as 「（放弃理由：…）」 as well. `attempts.toml` may list `abandoned_verdicts`;
☠️ always counts. **The attempt table conflicts by row, not by file**: a
proposal that adds or changes a row conflicts only when that row (or the
number it takes) changed in the official version since the proposal's base;
the PI editing other rows does not block it. Other files conflict as wholes,
as 12.3 says.

*Signatures.* A proposal must be signed whenever the PI's Research OS knows
the author's key and has `ssh-keygen`; otherwise it is approvable but shown
as unverified, with the reason. `team.toml` travels inside each snapshot. A
snapshot number skipped by an interrupted publish is never reused. There is
no recovery of a lost PI key in V8: the README says to keep a copy of
`~/.researchos/keys`; a new key starts a new chain, and members are shown
the new fingerprint to compare, as on first joining.

*The shared drive.* Adding a member, the PI's Research OS creates that
member's empty area `提议/<member id>/` once — a write-once act like any
other — and tells the PI to give that member edit rights to it in OneDrive.
Members' notes, link judgments **and hand-drawn mappings** (including
「关联文件」 and 「补上来源」) travel as signed, write-once packets and are
appended to the official ledgers at the next publish, without approval.
Canvas arrangements do not travel: each person keeps their own. A blob found
damaged may be removed by the PI and is rewritten by the next publish.

*A member's own work.* In a member's working copy, what differs from the
official snapshot is that member's own work, not part of the official
record: every view marks it 「我的改动，尚未提交」 or 「提议中」, and it
never appears as official. A member not yet in the team is told so —
「你还不是这个项目的成员：请 PI 把你加入团队，然后同步」 — rather than being
treated as a single-person project.

### 12.13 Rulings from building and accepting the V8 interface

*Words, not arrows.* Where 12.6 writes 「行数 96 → 97」 or 「🕒 → ✅」, the
screen says 「行数从 96 增至 97」 and 「判决从「待定」改为「现行」」 (12.11).
The command line, a developer surface, may keep its arrows.

*The shell's menus* replace 8.9's list: 文件 — 添加项目… ⌘O, 提交提议…
(member) or 发布 (PI) when the project is collaborative, 关闭 ⌘W; 视图 — the
eight sections on ⌘1–⌘8, 重新载入 ⌘R, 放大, 缩小, 实际大小. The bridge carries
title, mode and choose-folder / choose-file messages, nothing else. (Amended by 13.3: also `{type: "language"}`.)

*Who.* A version installed by 同步 is attributed to the publish it came
from — the PI and the snapshot — not to 「本机」. In a member's working copy a
version with no other attribution is 「本机」; elsewhere a pre-V8 entry
without `by` is 「最初的研究者」. Everywhere a person is named, their name
from the team (or me.toml) is shown, never their raw id.

*A member's own work* has a third state besides 「我的改动，尚未提交」 and
「提议中」: 「已批准，待同步」, between the PI's approval and the member's next
sync.

*What a proposal carries.* By kind: a verdict change carries its row only; a
new attempt carries its row and the files linked to that attempt; a files
proposal carries the files the member chose. The member can add or untick
files before sending, and a file that was part of a returned proposal is
flagged. Nothing else travels unasked.

*Tables* (CSV, TSV) always show the rows-and-columns summary first; a small
one shows its line diff below it.

*Notes on links.* The note written with 「关联文件」 or 「补上来源」 appears in
the node's change timeline with the link.

*成果 lists claims only from the paper.* A claim is any number in a
sentence, and the researcher's 90 drafts hold 3,688, mostly in revision logs
and review notes. 成果 shows figures and tables, and claims only from drafts
the researcher marks 「设为论文正文」 — a list kept in
`.researchos/papers.toml`, snapshotted like the other record files. With
none marked, 成果 says how to mark one. Disclosure of a draft's claims uses a
quiet line chevron, not the browser's triangle.

*Two folders for a member* (personal OneDrive). Sharing a subfolder with
other rights than its parent makes it a separate shortcut on the member's
Mac. A member therefore sets the published folder and their own proposals
folder separately — `researchos share set --published <folder> --mine
<folder>` and the same in 成员 — and Research OS checks that the published
folder holds this project's signed snapshots and that the member can write
into their own folder before using it.

*Installing for a collaborator.* `docs/安装与加入团队.md`, in Chinese, for a
member who does not program: install OneDrive and sign in; install
Research OS with uv in one command (uv brings its own Python — macOS's own
is too old); `researchos app` to put the app in Applications; first launch and
the Documents prompt; set name and e-mail; send the PI their public key;
accept the PI's sharing invitations and add both folders to their OneDrive;
choose them in 成员; compare the PI's key fingerprint; 同步. And for the PI:
which folder to share with which rights, where to click in OneDrive's
personal web interface. It is tested step by step on a clean account-like
setup before it is handed over. Its English counterpart (13.7),
`docs/install-and-join.en.md`, takes the same steps in the same order with
the same commands, and names each label of a screen still in Chinese as the
screen shows it, with its meaning in brackets.

## Section 13 — Two interface languages (task V9)

The researcher, 2026-10-12: "现在给 APP 添加英文切换功能". A collaborator or
a PI who does not read Chinese must be able to use Research OS. The same
day the researcher chose the scope of the first increment (13.7: the frame
only), approved the glossary of 13.4, chose that the first launch follows
the Mac's language (13.2), and asked for an English install guide.

### 13.1 What switches and what never does

The language is a property of **the screen, not of the record**. It changes
only the words Research OS itself writes on screen — in the page and in the
shell. It never changes:

- **The researcher's text**: attempt titles, verdict cells, results, notes,
  reasons, card fields, proposal sentences, project labels, people's names,
  file and folder names. Shown as written, in whatever language it is in.
- **Anything written to disk or to the shared drive**: the folder names
  `已发布` and `提议`, record file headers and comments, `decisions.toml`,
  「（放弃理由：…）」 in the attempt table, the commit message
  「Research OS：人工记录」, stored values such as `已核对`. Two people whose
  screens are in different languages share one folder and one record; a
  file written by one Mac is byte-identical whichever language it shows.
- **The vocabulary that reads the researcher's files**: `[verdict_labels]`,
  headings, column names, the default verdict words used as parse keys.
- **The command line** (8.8: a developer surface, English).

Where an English screen has to name a fixed on-disk name, it shows the real
name, with the meaning in brackets the first time on that screen:
"the 提议 folder (proposals)".

### 13.2 Where the choice lives

Per person, per Mac: the key `interface_language` (`"zh"` or `"en"`) in
`~/.researchos/settings.json`, beside `doi_online_lookup`. It is written only
when the person chooses, never by default. The read-modify-write helpers
move from `literature.py` to a small `researchos/settings.py` used by both
settings.

With no saved choice, Research OS follows the Mac: if the first preferred
language starts with `zh`, Chinese; anything else, English. The page reads
`navigator.languages`, the shell reads `Locale.preferredLanguages`; both see
the same macOS setting. (The researcher's Mac is `zh-Hans`, so nothing
changes for them until they choose.)

Inside the shell the page does not trust `navigator.languages`: a web view
in an app that declares no localizations may report English on a Chinese
Mac. The shell injects its own answer from `Locale.preferredLanguages` at
document start, and the page uses it before `navigator.languages`. Order:
the saved choice, then the shell's answer, then the browser's.

The engine never guesses. A sentence it composes for the page is in the
language the page asks for on that request (the header
`X-ResearchOS-Lang: en|zh`, set in one place, `apiGet`/`apiPost`); without
the header it is Chinese, so the command line, MCP and an older page are
unaffected.

### 13.3 The switch

A quiet text button in the sidebar's tool row beside 记录 and 停止服务,
named in the language it switches to: 「English」 on a Chinese screen,
「中文」 on an English one. No flag, no globe, no icon. One click saves the
choice, tells the shell, and reloads the page on the same section (the
remembered view). `<html lang>` is `zh-CN` or `en` accordingly. The fonts do not
change (Section 8): Songti and PingFang carry Latin letters, and English text
keeps the same serif/sans/mono roles. Labels must fit at the sidebar's
fixed width in both languages; a longer English word wraps, it is never cut
with an ellipsis.

The shell follows the page: the bridge accepts one more message,
`{type: "language"}`, on which the shell re-reads `settings.json` (it never
trusts the message's content), rebuilds its menus and re-applies the team
mode. This amends 12.13's "nothing else". At launch the shell builds its
menus, waiting page, alerts and panel prompts in the saved language, or the
Mac's when none is saved.

### 13.4 Glossary (binding; approved 2026-10-12)

One English word per concept, everywhere it appears; sentence case (the
shell's menus alone follow macOS's title case and its standard names: Undo,
Select All, Zoom In, Minimize); the same register as the Chinese — short, concrete, no jargon. 8.8's spirit
holds: the user is never told "node", "edge" or "socket". English strings
obey 12.11: no arrows, no emoji; quotation marks are “ ” where the Chinese
has 「 」.

| 中文 | English |
|---|---|
| 工作台 | Desk |
| 研究路线 | Research routes |
| 成果 | Outputs |
| 变量 | Variables |
| 文献 | Literature |
| 数据与代码 | Data & code |
| 提议 | Proposals |
| 成员 | Members |
| 现行 | Current |
| 已放弃 | Abandoned |
| 待定 | Pending |
| 不利证据 | Adverse evidence |
| 被否 | Rejected |
| 归档 | Archived |
| 其它 | Other |
| 来龙去脉 | Provenance |
| 改动与备注 | Changes & notes |
| 判断 | Judgments |
| 关系图 | Relation map |
| 尝试 / 判决 / 结果 | Attempt / Verdict / Result |
| 待复核 | Needs review |
| 缺来源 / 补上来源 | Missing source / Add source |
| 缺尝试 / 关联文件 | No attempt / Link files |
| 公盘 | Shared folder |
| 官方版本 | Official version |
| 工作副本 | Working copy |
| 批准并加入 | Approve |
| 退回并说明 | Return with reason |
| 我的改动，尚未提交 | My change, not yet proposed |
| 提议中 | Proposed |
| 已批准，待同步 | Approved, awaiting sync |
| 本机 | This Mac |
| 最初的研究者 | Original researcher |
| 含义 / 输入 / 构建口径 | Meaning / Inputs / Construction |
| 实现依据 / 人工决策 | Implementation / Human decision |
| 草稿 / 确认这一版 | Draft / Confirm this version |

Verdicts: the six default statuses and 「其它」 take the words above. A
status the researcher named in `[verdict_labels]` shows their word as
written (status `labelled`), in either language.

Fixed for the desk and the frame (first increment): 等你批准 "Awaiting your
approval"; 我的提议 "My proposals"; 你上次查看以来的改动 "Changes since you
last looked"; 依据变了的成果 "Outputs whose basis changed"; 已放弃的路线
"Abandoned routes"; 记录 "Records"; 停止服务 "Stop service"; 研究脉络 (the
brand's subtitle) "Research provenance"; PI stays "PI"; 成员 as a role
"Member"; 同步 "Sync"; 发布 "Publish".

### 13.5 Dates, numbers and plurals

Chinese stays as it is. English uses explicit formats, not `Intl`, so tests
are deterministic: "Today 09:24", "Yesterday 18:02", "4 Oct", "4 Oct 2025"
outside the current year; 24-hour time as in Chinese; thousands separated by
commas ("1,497"). Every counted noun has a singular and a plural ("1
proposal", "2 proposals"); Chinese has one form.

### 13.6 How text is translated

- **The Chinese stays in the source as the key.** `t("工作台")` returns the
  English in English mode and the Chinese otherwise; values are filled by
  name (`{n}`, `{name}`); a plural entry holds a singular and a plural form
  chosen by `n`. Sentences built by concatenation become one template each,
  because English word order differs.
- **One served file, `/i18n.js`, loaded first**, defines `window.ROI18N`
  (the language and the English table). Every module looks it up at call
  time and falls back to the Chinese when it is absent, so a module loaded
  alone — as the node-run tests do — behaves exactly as before.
- **The page never compares displayed text.** Where it compared Chinese the
  server sent (the desk's `what`, and the others found in the 2026-10-12
  survey), the payload gains a code and the page compares codes.
- **The engine** looks up the sentences it composes for the page through
  one helper, by the request's language, Chinese by default.
- **A missing English entry shows the Chinese**, never a key or a blank, and
  a test lists every one for the surfaces declared converted.

### 13.7 Increments

**First increment — the frame** (this task): the setting, the header and
`/i18n.js`; the switch; the top bar's action button (新增尝试 / 提议新的尝试;
the form it opens is later); the sidebar (section names, the project switcher and
its menu, the who-am-I and published/synced lines, 记录, 停止服务); page
titles; the welcome shown without a project; the stopped-service page; the
desk entirely (its own words, the sentences the engine composes for it, the
verdict words it shows); the shell (menus, waiting page, alerts, panel
prompts); the English install guide `docs/install-and-join.en.md`.

**Later increments**, one section at a time: 研究路线, 成果, 变量, 文献,
数据与代码 with 关系图, 提议, 成员, the node page, forms and dialogs, and the
error sentences (`BLOCKING_ERROR_TEXT`, the engine's refusals). Until a
section's increment lands, that section shows Chinese in English mode, and
so do error sentences everywhere. **Shared words are the exception**: words several
sections take from one helper — dates and times, verdict status words,
people words, officialness states, the one-line description of a proposal —
follow the interface language on every page, converted or not, so a Chinese
sentence on a section not yet converted may carry an English date or status
word until its increment lands. One copy of each helper is worth that. Within a converted surface, every word
Research OS writes is English.

### 13.8 Acceptance of the first increment

1. No setting, Mac in Chinese: every screen reads exactly as before.
2. English (by choice, or a browser whose first language is not Chinese and
   no setting): sidebar, page titles, welcome and desk carry no Chinese
   except the researcher's text and fixed on-disk names; verdict words as
   in 13.4; the researcher's own `[verdict_labels]` words unchanged.
3. The switch round-trips: 「English」, reload, English; `settings.json`
   gains exactly `interface_language`, `doi_online_lookup` untouched;
   「中文」 brings it back.
4. Nothing on disk depends on the language: the two-person flow of 12.10.3
   run with an English screen writes the same folder names, record files,
   decisions and commit message as with a Chinese one.
5. The shell's menus follow the switch without a restart; with no setting
   on an English Mac its waiting page is English.
6. Command-line output is unchanged; every existing test passes; new tests
   cover the setting, the header, the desk in both languages, the shell's
   two tables, and the completeness and 12.11 rules for English strings.
7. Screenshots of every converted surface in both languages, taken by the
   reviewer in a browser, before the merge.

## Section 14 — The desk is the route map (task V10)

The researcher, 2026-10-12: "工作台的逻辑应该改掉，应该将之前研究路线的流程图/
决策树画布放到这里，点击并编辑每个节点，且能看到并处理最核心最重要的事". This
reopens canvas work, which had been paused since the external review (the
researcher's own decision, made knowingly). Decided by the researcher the same
day:

1. Lines between attempts — 「源自」, *grew out of* — are drawn by hand on the
   map and kept in the project's records, never in the Markdown map file;
   attempts with no such line wait in a row by date. Nothing infers them:
   the map file records no lineage, and a machine guess at which route grew
   out of which would be worse than no line (Section 0).
2. The urgent work on the desk is of two kinds only: proposals awaiting
   approval, and judgments needing review (待复核).
3. The top level shows attempts only; an attempt's scripts and data are seen
   by opening it — in the side panel, not by unfolding the map.
4. In a project with members, a member may draw or remove a line, but as a
   proposal the PI approves.
5. The old desk blocks are folded into the map (14.5).
6. 源自 is "Grew out of" in English (13.4).

This supersedes, for the desk: 12.0's list of what the desk shows; 12.9's
"the canvas … not a destination of its own" (the route map is the desk's
main view; 关系图 stays where it is); 8.1's "attempts are frames, not nodes"
(on the route map, attempts are the cards; in 关系图 they stay frames).

### 14.1 The route map

The desk is the route map with a column beside it (14.4). The map's cards
are the attempts of the official table — in a member's working copy also
the member's own rows, marked as 12.12 says. A pending new attempt
proposed by someone else is never a card (12.4); it is listed in the column.

A card: 「#16」 and the researcher's title; the date as written; the verdict
as a text status from `route_status` (so `[abandoned_verdicts]` and the
researcher's own labels count, 12.11), in its tone; abandoned cards quieter.
Marks on a card are words in a quiet label, never icons or counts in
circles: 「等你批准」 or 「提议中」 when a proposal touches it, 「待复核 2」,
「有改动」 (14.5).

Lines are 「源自」 lines only, drawn as a human's lines (clay, 8.2). The map
reads left to right in time: a parent stands left of what grew out of it;
no arrowheads, no arrow characters (12.11). Layout: a forest by generation
(longest path from a root), siblings by date; an attempt with neither a
parent nor a child stands in a row below the forest, 「还没标来处」, by date.
Dates in the table are free text (`07-26`, `≤07-07`, `07-08~09`): order by
the date where `attempts.toml`'s `date_year` makes it readable, then by the
table's own number (`attempt_sort_key`).

Positions are per person, in `canvas.json` under the view `routes`
(they never travel, 12.12). 8.4 holds: the whole view is pinned on the
first move; afterwards a new attempt joins the end of the 「还没标来处」 row
and nothing pinned moves; 「重新排列」 asks, then lays the view out afresh.
The route map is its own renderer, not a second use of the 关系图 canvas
object, so the desk, 数据与代码 and the node page never share state.

### 14.2 Drawing a line

Drag from the small handle on the right edge of the attempt something grew
out of, onto the attempt that grew out of it — left to right, as the map
reads: dragging from #16 onto #17 offers 「#17 源自 #16」, with an optional
note; confirm saves. The popover always says the sentence before anything is
written. A click
without dragging opens the panel (14.3). A click on a line selects it;
「删除这条连线」 asks first.

The record: `mappings.toml` gains the type `derives_from`, `from` the child
attempt, `to` the parent (`attempt:<table>#<n>` both), with `note`, `by`
and the date like every hand-drawn mapping (8.5). It is not a file link and
is kept out of the file grammar (as `belongs_to` is). Rules, each refused
with its own sentence (8.8): both ends must exist when drawn from the app;
no line from an attempt to itself; no duplicate; no line that would close a
circle. An attempt may grow out of several. Read back, an entry that would
close a circle (a hand edit, two packets) is ignored and reported as a
mapping problem; an entry whose end no longer exists (a renumbered row)
stays in the file, is listed in the column as 「连线的一端找不到了」, and is
not a records-verification failure.

**Who draws.** The PI — and the researcher in a single-person project —
draws and removes lines directly. A member's drawing or removal is a
proposal of the new kind `route_link` (`add` or `remove`, child, parent,
note), shown on the member's map dashed and marked 「提议中」 until decided;
returned, it disappears and its reason stays in 我的提议. A member's
new-attempt proposal may name what it grew out of; approving the attempt
adds those lines. A member's `derives_from` entries never travel in the
no-approval mapping packets of 12.12. Every approved `route_link` is an
entry in `decisions.toml` (12.12).

### 14.3 The panel

Clicking a card opens the side panel on the desk, without leaving it:

- **The attempt**: number, title, date, verdict; edit the verdict and the
  result. Saving goes through the existing preview-then-confirm write of
  the map file, sending only what changed. A member's edit becomes a
  verdict-change proposal (12.4). **Abandoning asks for the reason and
  freezes the basis for everyone** — the PI and a single researcher too,
  not only a proposal — because an abandoned route keeps its basis (12.4).
  Description, date and variables are edited in 研究路线's form, not here.
- **写备注**: a note on the attempt (the notes ledger, 12.6).
- **来龙去脉**: the attempt's data, scripts and outputs in columns joined by
  real lines — the node page's trace, drawn in the panel. One membership
  rule (its step files plus 「关联文件」) serves the panel, the node page and
  关系图's attempt view, so they never disagree.
- **源自 / 由它而来**: its parents and children, each removable (a proposal
  for a member).
- **Its urgent work**: proposals touching it, decided in place with the full
  「批准以后会怎样」 (approve, return with reason, discuss); its 待复核
  judgments, confirmed again or rejected in place.
- **打开详情页**: the node page.

### 14.4 The column

Beside the map, newest first:

- **等你批准** (the PI): every pending proposal — new attempts, verdict
  changes, variable versions, files, lines. A click opens it in the panel
  and, when it touches an attempt, highlights that card.
- **我的提议** (a member): their proposals with their state — 提议中,
  已退回 with the reason, 已批准，待同步.
- **待复核**: judgments whose basis changed, under the attempt they belong
  to (through the membership rule of 14.3), else under 「不属于任何尝试」
  (claims, variable cards, files no attempt holds).
- **官方版本有更新** and 同步 (a member), as the desk had it.
- Lines with a missing end (14.2).

With nothing to do the column says so in one quiet line.

### 14.5 Where the old desk went (12.0 amended)

- *Abandoned routes*: the quieter cards; the reason is in the panel.
- *What changed since I last looked*: 「有改动」 on every attempt whose row
  changed, or one of whose files changed, since the last visit to the desk
  (the visit time is kept as before); the panel's timeline says what.
- *Results that lost their evidence*: on 成果 (12.13), not on the desk.

The desk is still not a statistics dashboard: no counts for their own sake.

### 14.6 Language

The desk is a converted surface (13.7): the map, the column and the panel
— including the edit form, the proposal decision and the review actions
when shown there — carry no Chinese in English mode except the
researcher's text. 13.4 gains: 源自 "Grew out of"; 由它而来 "What grew out
of it"; 路线图 "Route map"; 还没标来处 "Origin not marked yet"; 有改动
"Changed"; 待办 "To handle". The words 节点 and 画布 stay off the screen
(8.8), in both languages.

### 14.7 Increments (branch v10-desk-map, merged after acceptance)

A. The engine: the `derives_from` type with its rules and index migration;
the `route_link` proposal and parents on a new attempt; abandonment asks a
reason and freezes the basis on a direct write; the shared membership rule;
the desk payload for the map, the marks and the column, in the request's
language. B. The route map renderer and the `routes` view in
`canvas.json`. C. The column and the panel: editing, notes, trace, lines,
proposals and reviews in place, all in both languages. Then review,
re-baselining the desk tests deliberately (13.8's pin of the Chinese desk
describes the old desk), and acceptance.

### 14.8 Acceptance

1. On a copy of the researcher's project: 24 cards, all in 「还没标来处」 by
   date; drawing 「#17 源自 #16」 forms a two-card forest; a reload keeps
   it; `mappings.toml` gains exactly one entry; the map file is unchanged
   byte for byte.
2. A circle, a self-line and a duplicate are each refused with their own
   sentence.
3. Clicking #16: verdict, result, note form, and a trace with data, scripts
   and outputs. Changing the result previews and then writes only that
   cell. Setting an abandoning verdict asks for a reason and freezes the
   basis.
4. Two people (12.10.3's setup): a member's verdict change shows on its
   card and in the PI's column and is approved in the panel; a member's
   line is dashed 「提议中」 until approved, then official; a returned one
   disappears with its reason.
5. A judgment whose script changed puts 「待复核 1」 on its attempt;
   confirming it again in the panel clears it.
6. 「有改动」 appears after a change and clears after the next visit.
7. English mode: no Chinese on the map, column or panel except the
   researcher's text. No emoji, no arrows, no 节点 or 画布 anywhere.
8. Every existing test passes, with the desk pins re-baselined on purpose.

## Section 15 — Stata (task V11)

The researcher, 2026-10-17: "添加对 stata 的支持". Decided the same day: this
increment covers **do-file lineage** and **.dta files themselves**; Stata's
tables and logs as outputs, and variable cards reading gen/egen, come later.
Acceptance runs on three public replication packages (reifjulian/my-project,
simonmongey/Mongey_Pilossoph_Weinberg, worldbank/rio-safe-space), read only,
never run. Stata comes before the evidence work. **A global that stands for a
project folder is proposed by Research OS and confirmed once by a person**
(15.3).

A survey of the three packages (107 do-files, 21 .dta files) found: paths are
almost always built from globals; the root global is either left for each
user to set (my-project comments it out) or hard-coded as one author's
Windows path (rio); 21 of 77 Mongey paths omit `.dta`; tempfiles are common;
nothing in Stata's syntax is safe to match with a word-anywhere pattern.

### 15.1 What Stata files are

- `.do` is a **script**: scanned for lineage, versioned, an endpoint for
  hand-drawn links, a step file when numbered (attempts' `steps_dir`). `.ado`
  is a script too, versioned and shown, but not scanned for files in this
  increment; the program names it defines are registered so a call to a
  project program is never read as a Stata command. Vendored libraries
  (folders holding `stata.trk`, `ado/plus`, `ado/personal`,
  `libraries/stata`) are listed as skipped, never scanned.
- `.dta` stays a **dataset** (it already is) and gains its own reader (15.4).
- The word on screen stays 「脚本」 / "Scripts"; lists of script kinds name
  `.do` (adding a project, the command line, the README).
- A human record keyed `file:x.do` before this increment is not carried over
  to `script:x.do`; no researcher's project held do-files then.

### 15.2 Reading a do-file

- **A lexer before any matching**: `*` comments at a command's start, `//`
  after whitespace, `///` continuation, nested `/* */`, `#delimit ;`, double
  and compound quotes; evidence keeps the file's own line numbers.
- **Commands only at a command's start**, after the prefixes `capture`,
  `quietly`, `noisily` (and their abbreviations), `by … :`, `bysort … :`,
  `version # :`, and inside the `{ }` blocks of `if`, `else`, `foreach`,
  `forvalues`, `while`. Abbreviations are normalised to one name taken from
  Stata's manual (never guessed), so a re-spelling does not put judgments
  under 「待复核」 (9.6).
- **Reads**: `use` (also `use varlist using`), `merge`, `joinby`, `cross`
  `… using`, `append using` (several files), `import delimited`, `import
  excel`, `insheet using`, `infile`/`infix … using`. **Writes**: `save`,
  `saveold`, `graph export`, `export delimited`, `export excel`, `outsheet`.
  Tables written by `esttab`, `texsave`, `outreg2` and the like are skipped
  and logged as belonging to the outputs layer, which has no table node yet.
- **Default extension**, as Stata's help pages state (read 2026-10-17):
  `.dta` for `use`, `save`, `saveold`, `merge`, `append`, `joinby`, `cross`;
  `.csv` for `import delimited` and `export delimited`; `.xlsx` for `export
  excel`; `import excel` looks for `.xls`, then `.xlsx` (the first that
  exists; neither, no link); `.raw` for `insheet` and for the data of
  `infile` and `infix`, `.dct` for their dictionaries; `.out` for
  `outsheet`. Extensions that are not data files (`.raw`, `.out`, `.dct`)
  are skipped and logged, as any unknown file type is.
- **Which do-file runs which** (`do`, `run`, `include`) is read to carry
  globals and the working folder along a run; it is not stored as a link in
  this increment.
- **Skipped and logged, never guessed**: tempfiles, names built in loops,
  any path with a macro that does not resolve, `sysuse`/`webuse`, anything
  outside the project.

### 15.3 Paths, macros and the project folder

- `$name`, `${name}` and `` `name' `` are expanded. A **global** is used only
  when every assignment to it in the project gives the same literal; a
  second, different value leaves it unresolved. A **local** is the file's
  own and used only when assigned once. An undefined macro is unresolved —
  never the empty string Stata would silently use.
- In a do-file a backslash is a path separator (packages written on
  Windows), except where Stata reads it as an escape (before `$` and `` ` ``).
- **Windows paths are absolute** — `C:\…`, `C:/…`, `\\server\…` — for every
  language: never mapped into the project, skipped and logged. (Python and R
  shared this latent bug.)
- **The working folder**: a relative path resolves against the folder of the
  do-file that started the run, followed along `do`/`run` chains and changed
  by a literal `cd`; a do-file nobody runs keeps today's rule (the project
  root, then its own folder).
- **The project folder behind a global** (the researcher's decision): when a
  global used at the head of paths is undefined, or holds an absolute path
  from someone's computer, Research OS **proposes** which folder of the
  project it stands for, with its evidence — 「$MyProject 很可能指
  analysis/：以它开头的 23 条路径里，21 条在 analysis/ 下存在」, or an
  absolute path whose last folders name a project folder. The proposal is
  shown in 数据与代码 with 「是」 and 「不是」. A confirmed answer is a human
  record, `.researchos/stata.toml` (one entry per answer: global, folder,
  `by`, date; the newest answer for a global holds), written by Research OS,
  snapshotted and listed like the other records; in a project with members
  only the PI (or a single researcher) answers, members see the proposal.
  Until answered, those paths are listed as 「展开不了」 with the reason, and
  no link is made from them. A link made through a confirmed answer carries
  it in its evidence.

### 15.4 .dta files

- **A header reader**, standard library only, releases 113–121 in both byte
  orders: rows, variables (name, storage type, display format, label,
  value-label name), the dataset label and its timestamp. It never reads the
  data section, so its cost does not grow with rows; a truncated or corrupt
  file is refused with a sentence saying what is wrong, never a hang. Names
  and labels are untrusted text.
- **Shown**: the file panel shows rows × variables and the variable table
  (name, label, type) instead of refusing a binary file; a version change
  reads like a table's (12.13): rows from X to Y, variables added or
  removed.
- **Python and R** learn `.dta` writers and readers they lacked
  (`to_stata`, `write_dta`, `pyreadstat`'s `read_dta`), so a pipeline mixing
  languages connects.

### 15.5 Language

New words in both languages (13.6); 13.4 gains: 全局变量 "global";
展开不了 "Not resolved"; 项目里的文件夹 "project folder".

### 15.6 Acceptance

1. **my-project**: after confirming `$MyProject` = `analysis/`, the links
   equal the hand-made gold standard of the survey — every resolvable path,
   and not one link that is not in it. Tempfiles and the timestamped log are
   skipped and logged.
2. **Mongey**: the paths relative to each task's `src/` resolve, the 20
   without `.dta` included; tempfiles skipped; no invented link; its 17 .dta
   files show rows and variables equal to pandas' reading of their headers.
3. **rio**: MASTER's globals fold; the doubly assigned global is left
   unresolved; the hard-coded Windows root is proposed as its project folder
   and, once confirmed, the paths built on it resolve; uses of undefined
   globals are listed as 「展开不了」; no node anywhere names a drive letter.
4. Every skip is logged with its reason; the counts are reported.
5. Corrupt and truncated .dta files are refused clearly.
6. The researcher's own project (Python, R, Rmd) yields the same links as
   before.
7. English mode shows no Chinese on the new surfaces except the researcher's
   text; no dependency is added; every test passes.

### 15.7 Rulings made while building V11

- **A global holding a relative value** (`global data "data"`) is relative
  to the run's working folder, as Stata reads it; only globals holding
  absolute paths, or a confirmed project folder, anchor a path.
- **The run.** A do-file that no other do-file runs starts a run; its
  folder is the run's working folder, followed through `do`, `run` and
  `include` and changed by a literal `cd`. A `cd` inside a program's body
  holds only in that body; after a call to a project program whose body
  changes folder, the working folder is unknown and relative paths are
  skipped. A target that cannot be followed (not a project do-file, a
  circle, a chain deeper than 100) is logged, not linked.
- **A proposal for a project folder** is shown only when the evidence is
  unambiguous. An undefined global: the folder holding the most of its
  distinct paths, at least 2 of them and at least half, with no other folder
  holding as many. A global holding someone's absolute path: the project
  folder named by the longest run of its last folders, with no other folder
  named as long and none holding more of its paths. A global assigned two
  different values is never proposed. (The counts are the researcher's to
  change.)
- **Default extensions** follow Stata's help pages command by command
  (15.2), cited in the code.

## Section 16 — Drafting a new attempt with a local model (task V12)

The researcher, 2026-10-17: connect LM Studio and help fill in forms. Decided
the same day: **the smallest version** — a button on 新增尝试 / 提议新的尝试
that drafts two cells from files the researcher picks; the semantic layer's
client (`researchos.semantic.backend`, already speaking LM Studio's
OpenAI-compatible surface) changed only to tell a context overflow (or a
reply cut off at the length limit) apart, keep the server's error text,
remember which model answered, count a connection the server breaks off as
a failure like any other, send a request to this Mac past any proxy, and
leave out the judge's remote warning on request; no change to approval.

### 16.1 What the model may draft

- **Only the description and the result** of a new row. Never the verdict,
  never a reason for abandoning a route, never a proposal's one-sentence
  explanation, never 源自: those are judgements, and Section 0's "Humans
  own judgment" holds for a fluent draft as for an edge.
- **Only from the chosen files.** A cell the files do not support stays
  empty: an empty cell is a normal outcome, a filled guess a defect. What
  the engine checks, deterministically, after the model answers (the
  `better_match` rule of "Machine annotation vs. human judgement", applied
  to paths and numbers): for each cell the model names the files it rests
  on; a cell with text and no named file is dropped (`no_source`), a cell
  naming any file it was not given is dropped (`unknown_source`), and a
  cell with a number that occurs nowhere in the text of its named files as
  sent to the model, nor, as written, in their paths (which the model is
  shown: "(16-17)" for the cited steps is no guess), is dropped
  (`unsupported_number`; numbers compared as written, thousands
  separators, U+2212 and % aside, and as values, so "0.1" is "0.10"; an
  exponent belongs to its number, so 1.23e-05 is never 1.23; digits inside
  a name such as `log10` are no number; full-width digits read as digits;
  a comma in the files is read both as a thousands separator and as a
  field separator, so a CSV row "120,450,10" holds 120, 450 and 10). An
  empty answer is `empty`. A dropped cell is left empty, its reason goes
  back to the form and is logged, never silently. What is only asked of
  the model in the prompt: no judgement of the attempt, no rounded or
  estimated number, every number in digits as the files show it, no guess.
- **Only into an empty cell.** A cell the researcher already typed in is
  never overwritten; the form says so. After a draft the form says what
  became of each cell, one whole sentence each, by the form's own column
  label: filled, kept (it already had text), or left empty with the
  engine's reason; and that longer files were read only in part.
- **Never into a frozen form.** While the form's preview is being made or
  stands, the draft controls (生成草稿, the file path, 加入, the chosen
  files, 「模型服务」) are frozen with the rest of the form; a draft that
  arrives then is not filled in, and the form says so. Once a member's row
  is written to the working copy they stay frozen, and a draft that
  arrives then says the row is written and the draft was not used.
- **A draft is not a record.** It goes back to the form and nowhere else.
  The row reaches the attempt table only through the form's own preview
  and write (or, for a member, the proposal the PI approves), exactly as a
  row typed by hand; nothing in the table marks that a model drafted it.
  Beside each drafted cell the form names the model and the files the
  model says it used, as the model's claim, until the researcher edits the
  cell. A drafted cell counts as typed for the panel's leave guard (14.3).

### 16.2 Files and the model server

- A file is chosen by its path in the project (suggested from the files
  the canvas knows that a draft can read: no figure, no missing file, no
  binary data file such as a .dta; a path left in the field without 加入
  counts as chosen), resolved inside the project and named, to the model
  and in the answer, by its path from the project folder (`./a.py`, `a.py`
  and `x/../a.py` are one file, and so are two spellings of one file on a
  disk that ignores case). It is read as `GET /api/file` reads it: inside
  the project only, text only, a cloud-only file refused. One file that
  cannot be read refuses the whole draft, naming the file as typed:
  `draft_file_missing` (a path no file can have, too),
  `draft_file_not_text` (a Stata .dta too: its header is not text),
  `draft_file_unreadable` (no permission, a read error),
  `draft_file_cloud_only`, `draft_file_outside`; no partial draft.
- The response schema limits source paths to the selected files and
  allows only the two expected cells and their `text`/`sources` fields.
  This lets constrained decoding reproduce long or Chinese paths exactly.
  A server that ignores these constraints gets the ordinary schema repair
  request; a second invalid answer is `draft_model_failed`. The per-cell
  source check remains a safeguard after schema validation.
- At most 5 files, sharing 12,000 characters: equal shares, a short file's
  unused share going to the others; a file cut is cut between words, never
  inside a number, marked to the model and listed for the screen. 12,000
  characters fit LM Studio's usual 4,096-token context for English or
  code; Chinese text takes about a token a character, so a long Chinese
  file needs the model loaded with a larger context. (These numbers are
  the researcher's to change.)
- Nothing else from the table is sent: no example rows (a small model
  copied an example's wording into the draft, 2026-10-17). The form says
  which language to write in, read from the table: of its last 5 rows,
  those with a description or a result; at least half holding Chinese is
  `zh`, none is `en`; otherwise it says nothing and the model writes in the
  language of the files' own comments and text.
- The server's address and the model are this Mac's setting
  (`settings.json`: `llm_base_url`, `llm_model`), under 「模型服务」 in the
  form: each field shows the value saved on this Mac (empty when none),
  with the value in effect as its placeholder; 保存 (or Enter in either
  field) sends both. An empty value returns to the `RCE_LLM_*` environment
  variables, then to the defaults (LM Studio's `http://localhost:1234/v1`).
  Only the keys sent change. An address must be http or https, name a
  host and carry no user:password (urllib cannot use one; a key is
  `RCE_LLM_API_KEY`) and no control character; a saved one that is not
  reads as not saved. A server root such as `http://127.0.0.1:1234` is
  treated as `http://127.0.0.1:1234/v1`, including an existing saved value
  or an environment variable. Explicit API paths are preserved.
  `RCE_LLM_API_KEY` goes only to the address it was
  set beside (`RCE_LLM_BASE_URL`, else the default), never to a different
  address saved here. "On this Mac" is
  `localhost`, 127.0.0.0/8 and `::1` only: a `*.local` name is another
  machine on the network. The draft block always says, above 生成草稿,
  where a draft goes: the model on this Mac, or, warning-styled, the host
  the chosen files' contents are sent to (Section 7: local by default, not
  local by construction); it is read when the form opens and again after
  保存, and until it has been read 生成草稿 stays off. A request to this
  Mac goes past any proxy (`http_proxy`, the macOS system proxy), so a
  draft the form calls local stays on the Mac; each draft sent off it is
  logged with its host. The address is shown, and logged, without any
  user:password.
- The model may take minutes: each request to it waits up to 300 seconds
  for the server to answer (`RCE_LLM_TIMEOUT` when set); a reply in the
  wrong shape gets one repair request, and a server that refuses
  `response_format` one plain request, so a draft can take a few times
  that. It holds no project lock while it waits.
- Refusals, never a partial guess: input longer than the model's loaded
  context (LM Studio's own sentence, told apart from every other error
  before the `response_format` fallback) is `draft_too_long`, with the
  remedy in words (reload the model with a larger context, or choose fewer
  files), and so is a reply the server cut off at its length limit; a
  model that cannot be reached, that breaks off its answer (unloaded, the
  server stopped), or that does not answer in the schema after one
  repair, is `draft_model_failed` with LM Studio's remedy in words. The
  server's own words are the detail behind 「详情」.
- LM Studio sometimes returns constrained JSON in `reasoning_content`
  with an empty `content`. Only a reply finished with `stop`, whose whole
  alternate field parses and passes the requested schema, is accepted.
  Prose and truncated answers are refused; a nonempty `content` takes
  precedence. The draft's source and number checks still apply. The prompt
  repeats the selected source paths after the file contents, so a path
  mentioned inside a script is not mistaken for a supplied source. It
  distinguishes reading code from running it: print templates, decision
  thresholds and conditional messages do not establish observed results.
  Structured requests use temperature 0 to reduce sampling variation.
- The form names the model the server says answered (its reply's `model`),
  else the one asked for.

### 16.3 Endpoints

`GET/POST /api/settings/llm`, `POST /api/attempts/draft` (server module
docstring). Both POSTs are origin-checked like every POST; the setting
answers with no project served (it is this Mac's, as the language switch
is); the draft is refused for a blocked project or with none, and writes
nothing.

## Section 17 — AI agents carry out tasks (task V13)

The researcher, 2026-10-21: a task's owner may be an AI agent installed on
this Mac — a Claude Code model, or a model reached through OpenCode — and the
agent **does the task**, not a draft of it: it reads the project, writes
code, runs Python, R or Stata, installs packages, downloads data, writes
notes and a result, and marks the task done. Decided the same day:

- The agent is an errand-runner acting on a person's instruction. It
  never makes a judgment that Section 0 reserves for people, and in a team
  it never proposes on anyone's behalf (17.7).
- What an agent may never do is a short list enforced by Research OS, not
  asked of the model (17.5). The verdict of an attempt is on it.
- Programs, package installs and network access are allowed.
- Local models are reached through OpenCode; Research OS runs no agent loop
  of its own.

### 17.0 What this amends

- **Section 0, "Humans own judgment"**, stands for judgments: confirming or
  rejecting a machine link, an attempt's verdict, approving or returning a
  proposal, publishing. What changes is *work*. Files, code, runs, notes and
  results may now be produced by an agent a person assigned, without
  approval beforehand; the safeguard moves from approval before to
  **attribution and undo after**: every change an agent makes is recorded
  as its own, with the person who started it, and a run can be undone
  (17.6).
- **8.0, "Research OS observes a pipeline; it does not run one"**, and 9.11's
  "Research OS does not run scripts": Research OS itself still runs
  nothing. It starts an agent the person chose, in the project folder, and
  the agent may run scripts. What the agent ran is in its run log (17.4);
  it is not yet a basis for provenance (9.11's "second kind of basis"
  remains future work).
- **16.1, "A draft is not a record"**, is about the attempt form's model
  draft and stays. An agent's output is a record: it is written to the
  project, attributed, and kept in the change history.

### 17.1 Agents

- **Two runners**, each an external command-line program Research OS finds
  but never installs: `claude-code` (Claude Code, `claude`) and `opencode`
  (OpenCode, `opencode`). An **agent** is a runner plus a model:
  `claude-code` with `sonnet`, `opus`, `haiku` or a full model name;
  `opencode` with `provider/model` as `opencode models` lists it (an LM
  Studio or Ollama model configured in OpenCode is how a local model is
  used).
- **This Mac's list** is `settings.json` `agents`: `[{runner, model}]`, at
  most 20, written only when the person adds or removes one. A model name is
  1–120 characters of letters, digits and `. _ : / @ [ ] + -`, starting with
  a letter or digit (never `-`: it is passed as a command-line value).
  `agent_commands` maps a runner to an absolute path when the person sets
  one; otherwise the program is looked up on `PATH` widened with
  `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`,
  `~/.claude/local`, `~/.opencode/bin`, `~/.bun/bin` and `~/.npm-global/bin`
  — an app opened from the Finder has none of the shell's `PATH`. Found
  means an executable file that answers `--version` within 10 seconds; the
  version is shown.
- **The tools need the MCP extra.** An agent reports through Research OS's
  own MCP tools (17.4), so running one needs the optional `mcp` package in
  the engine's Python; without it the settings and every 运行 say so and
  name the install command. Nothing else in the app changes.
- **Where the project's content goes.** Claude Code sends what it reads to
  Anthropic; OpenCode sends it to the model provider configured in
  OpenCode, which may be a local server. The settings and the first 运行 of
  each agent on a page say this in words before anything is sent.

### 17.2 Tasks

- `owner` may be an agent: `agent:<runner>:<model>`, e.g.
  `agent:claude-code:sonnet`. A value starting `agent:` that does not name a
  runner and a valid model is refused. The file says *which* agent, never a
  path or an address: `research/tasks.json` travels to the team, the
  program lives on one Mac.
- A task gains an optional `report`: the path of the result record the
  agent wrote (`research/results/<32 hex>.md`). It is written only when
  set; a task without one is unchanged on disk.
- The owner field offers the people of the project (me, and the team's
  members in a team) and this Mac's agents, shown as 「Claude Code ·
  sonnet」 / 「OpenCode · lmstudio/qwen3-coder」, plus a name typed by hand.
- Whoever may save tasks may run an agent task (single, PI, or a member in
  their own working copy). Another Mac sees the owner, the outcome and the
  history, not the run log, which stays on the Mac that ran it.

### 17.3 A run

- **One run at a time per project on this Mac.** 运行 starts it; the page
  returns at once and follows the run.
- **States**, each a code the page maps to words (13.6): `preparing` (taking
  the before-picture, 17.5), `running`, `needs_input` (the agent asked a
  question and stopped), `done` (the agent reported the task complete),
  `ended` (the program finished without a report or a question: the person
  should look), `failed` (could not start, ended in error, or ran past the
  time limit), `stopped` (the person stopped it), `interrupted` (the app
  quit or crashed while it ran). A run that was undone also carries
  `reverted`. A run that ends with an out-of-bounds change (17.5, 18.1
  item 4) is `out_of_bounds`, whichever of these it would otherwise have
  been; that state is kept as its `outcome`, and it is the outcome that
  decides whether the run can be answered or sent back.
- **Answering and sending back.** A run waiting for an answer, or one that
  finished, can be continued: 回答 (to a question) or 打回 (with what to
  change) starts a new run that resumes the same agent conversation with
  the person's words. 打回 on a done task clears its done mark first. Only
  the latest run of a task can be continued, and not once undone.
- **Stopping** notes every process the program started (OpenCode starts
  each shell command in a session of its own, so its process group is not
  enough), asks the program to end (SIGTERM; Claude Code passes it on, an
  OpenCode killed this way leaves its commands running), waits 10 seconds,
  then kills whatever of that tree is still alive. A program that ends on
  its own may leave processes running (a download started with `nohup`):
  the tree is sampled while the run goes, and whatever of it outlives the
  program is ended before the after-run check, which the run log notes.
  A run has a time limit, `agent_timeout_minutes` in
  `settings.json` (default 120, 5–1440); Claude Code also takes
  `agent_max_budget_usd` when the person sets one.
- **When the app quits** every running agent is stopped (`interrupted`); a
  SIGTERM to the engine takes the same path (a second SIGTERM during that
  shutdown is ignored). A run found `running` with no engine behind it is
  marked `interrupted` the next time runs are listed, and its program, if
  still alive and still that program, is stopped. "Still that process" is
  the pid together with its start time, recorded at launch for the engine
  and the program, so a reused pid is never taken for either.

### 17.4 What the agent is given

- **The working folder** is the project folder.
- **The instruction** (in the language of the person who pressed 运行): the
  task and its date; the project's research question and plan
  (`research/overview.md`, at most 8,000 characters, cut marked); the
  titles and paths of the notes and result records; the rules of 17.5 in
  plain words; and how to report. It is shown in the run log as sent.
- **Research OS's tools**, through `researchos mcp --agent-run <run>`, a mode
  of the existing MCP server that offers exactly these:
  - `researchos_complete_task(report, title?)` — writes the result record
    (a new `research/results/` document, or the one this task's runs
    already wrote), marks the task done and links it as `report`.
  - `researchos_ask(question)` — records a question; the agent stops and
    the task shows 等你回答.
  - `researchos_save_note(title, content)` — a new note.
  - `researchos_find`, `researchos_trace`, `researchos_status` — read only.
  `researchos_confirm_edge` is not offered. The tools write through the
  same paths as the page (lock, backup, history) and attribute the write
  to the agent.
- **Claude Code** runs as `claude -p --output-format stream-json
  --verbose --model <model> --session-id <new uuid>` (or `--resume <id>`),
  `--permission-mode dontAsk` with the tools it may use listed in
  `--allowedTools` (file tools, Bash, web fetch and search, and
  `mcp__researchos`), the denials of 17.5 in `--disallowedTools`, a
  `PreToolUse` hook and `--mcp-config … --strict-mcp-config` from files in
  the run's folder, the rules in `--append-system-prompt`, and the
  instruction on standard input (a prompt after `--allowedTools` would be
  taken as a tool name). Inherited Claude Code session variables are
  removed from its environment so a Claude Code that started Research OS
  is never joined. The hook is `sh -c '<python> -I -m
  researchos.agents.hook <run>'` and only its exit 0 allows a call: a
  project file that shadows a standard module (`random.py`), a missing
  interpreter, any failure refuses (Claude Code itself lets a call through
  on any other non-zero exit). `disableAllHooks` is set to false, and
  before the program starts a canary call the hook must refuse is made: if
  it is not refused, the run does not start (`guard_failed`). The MCP
  server runs isolated the same way (`-I`).
- **OpenCode** runs as `opencode run --format json --model <model> --dir
  <project>` (`--session <id>` to continue) with its configuration —
  permissions and the MCP server — in `OPENCODE_CONFIG_CONTENT`, a project's
  own `opencode.json` switched off (`OPENCODE_DISABLE_PROJECT_CONFIG=1`: it
  could otherwise allow what Research OS denies), and the rules and the
  instruction on standard input (a message given as an argument reaches the
  model re-quoted). Every permission is `allow` or `deny`, never `ask`: an
  unattended run rejects what would ask and stops. So `doom_loop` is
  `deny` and reading `.env` files is denied, where OpenCode's defaults
  would ask.
- **The run log** is the program's own event stream, kept in the run's
  folder, read back as steps: what the agent said, each tool it used with
  its command or file, what was refused, and how it ended (with cost and
  model when the program reports them).

### 17.5 What an agent may never do, and how that is held

**The list** (binding):

1. Make a human judgment: an attempt's verdict or result in the attempt
   table, confirming or rejecting a link, a variable definition's
   confirmation, anything in `.researchos/`.
2. Act for the team: approve or return a proposal, propose, publish, sync,
   push to GitHub, change the team.
3. Change or delete data that existed before the run (`.csv`, `.tsv`,
   `.xlsx`, `.xls`, `.parquet`, `.feather`, `.rds`, `.rdata`, `.dta`,
   `.sav`, `.json`, `.h5`, `.pkl`, `.sqlite`, `.db`, `.npy`, `.npz`, `.mat`,
   `.sas7bdat`, `.jsonl`, `.pickle`; `package.json` and `package-lock.json`,
   in any folder, are not data); new data goes to new files.
4. Change git's state: commit, push, reset, checkout, rebase, merge,
   stash, clean, tag.
5. Write `research/tasks.json` except through its tools.

**How it is held**, in layers, none of them the model's good will:

- **Claude Code's hook** (`PreToolUse` on file edits and Bash) asks
  Research OS before each call and refuses: an edit outside the project
  folder, inside `.researchos/` or `.git/`, of the attempt table (its path
  read from `attempts.toml` at the start), of `research/tasks.json`, or of
  a data file that existed before the run; a command that names the
  `researchos` or `rce` program, a git command of rule 4 (git's own
  options in front included), the engine's address or its API on this
  Mac, or `.researchos`; and a command that would remove, move or
  overwrite a protected file or a folder holding one -- `rm`, `mv`, `cp`,
  `rsync`, `ln`, `find -delete`, `git rm`/`git mv`, an archive extracted
  over it, a `curl -o`/`wget -O` download onto it, a redirect into it,
  `sed -i`, `truncate`, `tee`, `dd` -- or copy, move, extract or download
  into a folder that holds a protected file of the same name. Paths are
  resolved against the command's working folder, `~` and links followed.
  Reading a protected file stays allowed. A hook that cannot decide
  refuses. The command check reads text; it is a barrier, not a sandbox.
- **Permissions**: the git and `researchos` commands are also denied in
  Claude Code's `--disallowedTools` and OpenCode's `permission.bash`.
  OpenCode has no per-call hook here: for it the after-run check below is
  the guard, and its settings and the instruction it is sent say so.
- **Research OS refuses itself.** The agent's processes carry
  `RESEARCHOS_AGENT_RUN`. With it set, the `researchos` command refuses
  everything but its own agent tools, and every write under the project
  lock (`write_guard`: a person's record, a publication, an index write)
  refuses unless the agent tools opened it. A
  script that imports Research OS to approve a proposal gets a refusal, not
  an approval.
- **The engine's address is not handed to the agent**, and the hook refuses
  commands that name it. The local web API's origin check refuses other
  web pages, not other programs of the same user, so this is stated as a limit: an agent
  that set out to bypass all of the above with Bash could; the after-run
  check would show what it touched.
- **The before-picture and the after-run check.** Before the program
  starts, Research OS records the files of the project (size, modification
  time, and a SHA-256 for files up to 64 MB), skipping `.git/`,
  `.researchos/backups/`, `__pycache__/`, `node_modules/`, `.venv/`,
  `venv/`, `.ipynb_checkpoints/` and the like and the files the system and
  editors leave (`.DS_Store`, `~$` lock files, `.Rhistory`), never reading
  a file that is only in the cloud, and copies every file up to 16 MB
  (1 GB per run at most) into the run's folder. The picture stops at
  50,000 files; the run log then says that later files are neither checked
  nor undoable, and in such a picture a file counts as created only if it
  was written after the run began. Data files (rule 3) are found by a
  separate walk that only reads their size and time, is not limited by
  that number, and goes into every folder but `.git`, `node_modules`,
  `.venv`, `venv` and `__pycache__`. Git's `HEAD`, branch and a fingerprint
  of all its references and the stash are noted (so a push, a tag, a branch
  or a stash shows; the index does not). After the run the folder is
  compared: files created, changed and deleted (a touched file with the
  same content is not changed). A change to a protected file (rules 1, 3,
  5) or to git's state is an **out-of-bounds change**: the run shows it
  first, in the warning colour, with 撤回本次运行 beside it. Files the person
  saved through Research OS while the run went on (until the after-picture
  is complete) are not counted as the agent's; a protected file the person
  saved is still out of bounds if it does not end as the person left it.
- **Attribution.** Every file the run created or changed is recorded in the
  change history with `source = "agent"`, `who =
  "agent:<runner>:<model>#<person id>"` (the person who started the run;
  without `#…` when unknown) and the run id as the note; the history shows
  「Claude Code · sonnet（张三 指派）」 and 「AI 执行」. A git-tracked file is
  not recorded: its history is git's, and the run log is where its agent
  change is named. The result record
  the agent writes opens with a line saying which agent wrote it, when, and
  for whom.

### 17.6 Undoing a run

撤回本次运行 puts back what the run changed, as far as the before-picture
allows, and never over a later change:

- a file the run created is moved into the run's folder (`reverted/`), not
  deleted -- unless it is a file the run moved or renamed that had no copy
  (its content is that of a deleted file), which goes back to where it was;
  folders the moves emptied, that did not exist before the run, are removed
  (18.1 item 5: the before-picture records folders);
- a file it changed or deleted is restored from the copy taken before;
- a file that changed again after the run (by the person or a later run),
  or that had no copy (over 16 MB, or past the 1 GB budget, or only in the
  cloud) is left as it is and listed with the reason;
- the task's done mark and `report` are taken back if the run set them
  (not by restoring `tasks.json`, which the person may have changed since);
- a run cannot be undone while a later run of the same project that is not
  undone touched the same files, or while another Research OS on this Mac
  runs an agent in the project: undo the later one first;
- files the person saved during the run, and files only in the cloud, are
  left as they are.

Each restore is a write like any other, backed up and in the history,
under the person's name. When something was left as it was, the run reads
部分撤回 and its log lists those files with the reason. The copies of the
newest 20 runs of a project are kept; the logs of the newest 200; older
runs can no longer be undone, and runs beyond the 200 are removed.

What the undo will do is listed first, and the person chooses (18.1 item 5).

### 17.7 In a team

- An agent works where the person who started it works: on the PI's Mac in
  the official version, on a member's Mac in their working copy. What it
  wrote is that person's local change; nothing is proposed for them, and
  the person decides what to propose (12.3), as for their own edits.
- In the history and in the result record the agent is named, with the
  person; approval later records the approver as for any change.

### 17.8 Endpoints

All `agent_<code>` errors carry `message` in the request's language and
`detail`. Every POST is origin-checked and carries `project` (as the
workspace's do); runs are the served project's. Starting, answering and
undoing refuse a blocked, read-only or unmigrated project up front
(`agent_read_only`, `agent_needs_migration`) without holding the project
lock while the program is looked for; the manager takes the lock only for
its own writes. A `me.toml` that exists but cannot be read refuses
starting and answering (`agent_person_unreadable`: a run is never nobody's).
A project without an identity lists no runs. A run that ends while another
project is served is scanned when that project is opened again.

- `GET /api/agents` — runners found (path, version, or why not), this Mac's
  agents, whether the MCP extra is installed, suggested Claude Code models.
  Works with no project.
- `GET /api/agents/models?runner=opencode` — `opencode models`.
- `POST /api/settings/agents` — `{agents?, commands?, timeout_minutes?,
  max_budget_usd?}`; keys not sent are kept.
- `GET /api/agents/runs` — the project's runs, newest first, and which is
  active.
- `GET /api/agents/runs/<id>` — one run with its steps, changes,
  out-of-bounds changes, question, report, error and the instruction sent.
- `POST /api/agents/runs` `{project, task}` — start.
- `POST /api/agents/runs/<id>/reply` `{project, message}` — answer or send
  back.
- `GET /api/agents/runs/<id>/revert-plan` — what 撤回本次运行 would do now
  (18.1 item 5), without doing it.
- `GET /api/agents/large-files` — the project's files over 16 MB (18.1 item 5).
- `POST /api/agents/runs/<id>/stop`, `POST /api/agents/runs/<id>/revert`
  (which takes `files`, the paths left checked in the plan; absent = all).

### 17.9 Language

Added to 13.4: AI 执行者 "AI agent"; 运行 (a run, and the button) "Run";
运行记录 "Run log"; 等你回答 "Waiting for your answer"; 回答 "Answer";
打回 "Send back"; 撤回本次运行 "Undo this run"; 越界改动 "Out-of-bounds
change"; AI 执行 (history source) "By an agent". States: 准备中
"Preparing", 运行中 "Running", 已完成 "Done", 未报告 "Ended without a
report", 失败 "Failed", 已停止 "Stopped", 已中断 "Interrupted", 部分撤回 "Partly undone", 已撤回本次运行
"Undone" (not 已撤回, which is a withdrawn proposal's word), 越过了限制
"Went out of bounds" (a run that ended with an out-of-bounds change, in the
warning colour; never 已完成).

### 17.10 Acceptance

1. A task assigned to 「Claude Code · sonnet」 runs from the task row; the
   agent writes a script, runs it, writes a result record through
   `researchos_complete_task`; the task shows done with 打开结果, and the
   new files show 「Claude Code · sonnet」 in their history.
2. The agent's attempt to edit an existing `.csv`, `.researchos/team.toml`
   or the attempt table, to `git push`, or to run `researchos`, is refused
   and appears in the run log as refused.
3. A protected file changed behind the hook's back (a script overwriting a
   data file) appears as an out-of-bounds change; 撤回本次运行 restores it.
4. 撤回本次运行 moves the run's new files away, restores changed ones,
   clears the task's done mark, and leaves a file the person changed after
   the run as it is, saying so.
5. A question from the agent shows 等你回答; 回答 resumes the same
   conversation; 打回 on a done task reopens it and resumes.
6. 停止 ends the run and every process it started; quitting the app does
   the same; a run left `running` by a crash reads `interrupted`.
7. In a member's working copy, the agent's changes are local changes and no
   proposal is made.
8. With `RESEARCHOS_AGENT_RUN` set, `researchos` commands and every write
   under the project lock refuse.

## Section 18 — An optimization round: agent safety, terms, the sidebar, speed (task V14)

The researcher, 2026-10-22: "现在由你来优化". Four read-only reviews of
`ca00a1e` and a timing on a copy of the researcher's thesis project (every
endpoint under 0.07 s except `/api/citations`, 0.45 s and 1.2 MB) set the
order. Decided the same day: all four groups below; 研究判断 stays and a
person's verdict on a machine link becomes 连线确认; 成果 becomes 图表与论断
and keeps its place in the sidebar; changing an existing row's 研究判断 or
结果 saves at once with 撤销, while abandoning a route and adding a row still
confirm first. Two rule changes suggested the same day (a variable card's
fields marked 不适用; number matching only on demand) wait for a later round.

### 18.1 Agents: the side doors (amends 17.4–17.6)

17.5 says an agent that bypassed the hook "would show" in the after-run
check. It did not: a program the agent writes could POST to the engine
(approve, publish, write the attempt table, change `agent_commands`), the
engine wrote under the person's name, and those writes were excluded from
the check. This round closes that door and the cheaper ones beside it.

1. **The engine requires its session for every write.**
   - At start, `serve` makes a random 32-byte session secret, kept in memory
     and written to `<home>/engine/<port>.session` (mode 0600, replaced
     atomically, removed at shutdown).
   - Every request other than GET and HEAD must carry it: the cookie
     `ro_session_<port>` (HttpOnly, SameSite=Strict, Path=/) or the header
     `X-ResearchOS-Session`. Otherwise the answer is 403 `session_required`,
     with a sentence in the request's language, after the origin check and
     before routing or reading the body. A GET that writes or starts
     something is moved under the same rule (each is listed in the commit).
   - `GET /boot?session=<secret>` sets the cookie and redirects to `/`; a
     wrong or missing secret redirects without it. The Mac app reads the
     session file once the probe answers, loads `/boot?…`, and sends the
     header on its own `POST /api/shutdown`. An engine without a session
     file (an older one) is loaded as before.
   - `researchos serve` opens the browser at `/boot?…`. With `--no-browser`
     it prints only the plain address: the secret is never printed, because
     the app's `serve.log` is readable by any program of the user.
   - A page without the session still reads. Its first refused write says
     so in place: 「这个窗口没有连上 Research OS 的写入权限：请从 Research OS
     应用打开它。如果你已经在应用里，说明应用是旧版本：请运行 researchos app
     重新生成。」 / "This window cannot save: open it from the Research OS
     app. If you already did, the app is out of date: run researchos app to
     rebuild it." The second sentence is for an app built before the session
     existed: it loads the plain address, cannot be given a cookie without
     defeating the check, and only `researchos app` replaces it (the update
     steps already end with that command; the changelog says so too).
   - The launcher-script bundle that `researchos app` builds when `swiftc` is
     missing reads the same file once the probe answers and opens
     `/boot?<secret>` (hex, so no encoding), in the already-running case too;
     for an engine without a file it opens the plain address.
   - The log never holds the secret: the request line, and the path the
     catch-all of a failed request reports, are logged with the value of
     `session=` hidden, and a window that goes away while `/boot` answers is
     not an error to report.
   - Tests may build a server that requires no session; `serve` never does.
2. **What the agent can read and is given.**
   - The Claude Code hook also runs on `Read`, `Glob`, `Grep` and `LS`, and
     refuses paths inside Research OS's home and `~/.ssh`. The Bash check
     refuses the home's absolute path as it refuses `.researchos`.
   - `RESEARCHOS_HOME` is no longer in the agent program's environment;
     only the MCP server gets it (17.4's MCP configuration already sets it).
   - Variables that look like credentials are removed from the program's
     environment. A name matches if it contains `TOKEN`, `SECRET`,
     `PASSWORD`, `PASSWD`, `CREDENTIAL`, `PRIVATE`, `API_KEY`, `ACCESS_KEY`,
     `AUTH`, `SESSION` or `COOKIE`, or is `SSH_AUTH_SOCK`, `GPG_AGENT_INFO`,
     `RCE_LLM_API_KEY`, or starts with `AWS_`, `GITHUB_` or `GH_`.
   - The runner keeps what it needs to reach its model:
     - Claude Code: `ANTHROPIC_*` and `CLAUDE_CODE_OAUTH_TOKEN`; `AWS_*` only
       when `CLAUDE_CODE_USE_BEDROCK` is set, and `GOOGLE_*` only when
       `CLAUDE_CODE_USE_VERTEX` is.
     - OpenCode: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
       `GEMINI_API_KEY`, `GOOGLE_*`, `GROQ_API_KEY`, `MISTRAL_API_KEY`,
       `DEEPSEEK_API_KEY`.
   - The names removed, never their values, are listed once in the run log.
   - Claude Code starts with `--setting-sources user`, so a project's own
     `.claude/settings*.json` is not loaded; OpenCode's project
     configuration is already off for the same reason. `.claude/`,
     `opencode.json` and `.opencode/` in the project are protected: an edit
     there is refused, and a change there is out of bounds.

   How it is read:
   - *The folders.* `guard.json` lists them as `off_limits`: the home in
     use, the default `~/.researchos` and the legacy `~/.rce` whether or not
     the environment names another, and `~/.ssh`, each as spelled and with
     symbolic links resolved. A path is compared resolved, by NFC-normalised,
     case-folded names, so a link inside the project and a differently
     cased spelling lead nowhere. `Glob` also reads the literal folder in
     front of the first wildcard of an absolute pattern. A `Grep` that starts
     above one of the folders (or starts nowhere in particular, in a call
     whose folder is above one) is refused, since ripgrep would read into
     it; `Glob` and `LS` above them only list names and are not.
   - *Bash.* The command text is refused when it spells the absolute path
     of one of those folders (as a whole name: `rh` is not `rhx`), names
     `$RESEARCHOS_HOME` or `$RCE_HOME`, or names a folder `.ssh`. As before,
     this is a text check: a program the agent writes can still look for the
     home by itself, which 17.5's limit restates below.
   - *The environment.* Besides the variables of 17.4, `RESEARCHOS_HOME`
     and `RCE_HOME` are not passed on; the MCP server's entry names the home
     for the server alone. Credential-like names are judged in upper case,
     so `github_token` goes too. `AUTH` and `SESSION` also catch names that
     are no credential (`GIT_AUTHOR_NAME`, `TERM_SESSION_ID`); the accepted
     cost is a missing variable, never a leaked one. The names removed are
     stored with the run (`env_removed`) and shown in its log under 「没有交给
     它的环境变量」 "Environment variables not given to it".
   - *Settings.* The folders `.claude` and `.opencode` are refused at any
     depth by the file tools and, with a writing operation or a copy, by the
     Bash check; so are `opencode.json` and `opencode.jsonc` anywhere.
     The after-run check reports a change there as `agent_settings_changed`
     (the page: 「改动了 Claude Code 或 OpenCode 在项目里的设置」 "Changed
     Claude Code or OpenCode settings in the project"); a pre-existing
     `opencode.json` is that, not also a changed data file. Undo puts a
     changed one back and moves a created one away, as for any file.
3. **What the engine sees during a run is the run's.** While a run of the
   project is active, from its before-picture to its after-picture, every
   version that a watcher scan adds to the change history is attributed as
   in 17.5. It gets `source = "agent"`, the run's `who`, and the note "seen
   during run <id>". Writes the person makes through Research OS keep their
   name. After the run, attribution does not add a second version of the
   same content.
   - *How.* The run manager, in the engine that started the run, tells the
     history so from the before-picture until the run's attribution is
     written (`history.watch_run`); the watcher needs to know nothing of
     runs. A scan in that engine then credits to the run a file that was
     not in the before-picture or ends with other bytes. A file the run did
     not change (one the person dropped in the folder before it, never
     scanned) and a path the person saved through Research OS since the run
     began, as long as it still has the bytes Research OS wrote, keep the
     scan's attribution. The attempts a scan reads follow the attempt
     table, and the judgment ledger is one of the record files: each is the
     run's or it is not. The pre-image of a person's save (12.6) is read
     the same way, so that the run's content is not recorded under their
     name. The stored note is English; the page shows it in its language.
   - *Limit.* Only scans in the engine that carries the run out know of
     it: a `researchos` command the person runs in a terminal meanwhile is
     an ordinary scan.
4. **Out of bounds is a state.** A run that ends with an out-of-bounds
   change reads 越过了限制 ("Went out of bounds") in the warning colour
   wherever its state shows, with 撤回本次运行 beside it, never 已完成. The
   task's done mark stays as the agent set it; the run is what warns. The
   check also covers:
   - `.researchos/` at any depth, `.researchos/backups/` included (a
     deleted backup is out of bounds);
   - `.git/hooks/` and `.git/config` (a planted hook is out of bounds).

   How it is read:
   - *The state.* `out_of_bounds` replaces whatever state the run would have
     had, `done`, `ended`, `needs_input`, `failed`, `stopped` and
     `interrupted` alike, and `outcome` keeps that state (17.3). A run
     waiting for an answer that went out of bounds still shows its question
     and can be answered.
   - *Depth.* A folder named `.researchos` or `.rce` protects what is below
     it wherever it lies in the project, as the hook already reads it.
   - *Git.* The before-picture records `.git/config` and the files in
     `.git/hooks/`, copies included (the `*.sample` templates, which git
     never runs, are left out), though `.git/` is otherwise not walked.
     A change there is out of bounds, and undo restores it or moves a
     planted hook away; the `config` of a repository the run itself
     started stays.
   - *Backups.* The picture lists `.researchos/backups/` by name and size.
     A new backup is nothing, since Research OS makes one with every save.
     One that is gone is out of bounds, the run lists it among its deleted
     files, and undo says it has no copy. One exception: Research OS keeps
     the newest 20 of each series of backups, and a save, by the person or
     by a run's own tool, prunes the oldest. As many of the oldest of a
     series as such pruning can have removed since the picture (what the
     series held, plus what it gained, less 20) are that rotation, not the
     agent; a series that never held more than 20 is never pruned, so there
     one planted newer name excuses nothing. Planting twenty names would
     still pass: this is a barrier, not a sandbox.
5. **Undo says what it will do, and asks.**
   - 撤回本次运行 first lists the files it will put back (changed or
     deleted) and the files it will move away (created), each with its
     time, each with a checkbox, all checked. It also lists the files it
     cannot undo, each with the reason. Unchecked files are left as they
     are and listed as 你选择保留 ("You kept this").
   - The confirmation before a person's first run no longer says that
     everything can be undone. It says that these cannot be: installs,
     downloads, git, and anything outside the project folder. It also names
     the project's files over 16 MB (up to ten, then a count).
   - Undo keeps folders that existed before the run. The before-picture
     records folders as well as files.

   How it is read:
   - *The list* is `GET /api/agents/runs/<id>/revert-plan`, which runs the
     very code of the undo without writing: the same checks in the same
     order, so what is listed is what the undo would do at that moment, and
     it refuses as the undo does (undone already, a later run touched the
     same files, nothing to undo, a run going). It only reads, so a window
     without the session may ask. Each file carries its `time`: its
     modification time on this Mac, in local time, where it is there; for a
     file that is gone, and for the task's done mark, when the run ended.
     The cannot-undo files carry the reason codes of 17.6 (`changed_since`,
     `no_copy`, `in_cloud`, `protected`). The last is a created file under
     `.researchos/` and a created `.git/config`; the page words it 「属于
     Research OS 的记录文件夹或 git，撤回不会动它」 "Belongs to Research OS’s
     records folder or to git; undo does not touch it".
   - *The choice.* `POST …/revert` takes `files`, the paths left checked
     (absent = all that can be undone; an empty list is refused). A path
     that could have been undone and is not named is left as it is and goes
     in the run's `revert.skipped` with the reason `kept`, which the page
     words 你选择保留; the run then reads 部分撤回 like any other partial
     undo. A path the undo could not have put back (no usable copy, or its
     folder cannot be made) keeps that reason whether it is named or not: the
     check for it comes before the choice is read, in the loops of changed
     and of deleted files alike, so that the note and the log never say a
     person kept what the list told them could not be undone. A path the list did not offer is never acted on, whatever
     `files` says, and a file that changed between the list and the
     confirmation is still left as it is (17.6).
   - *What is one choice.* A created file that is a deleted file renamed is
     offered once, under the old name, and put back by moving the new file
     (unchecked, it keeps its new name). The task's done mark and result
     link are one choice, listed as `research/tasks.json`: a person who
     keeps the result record may keep the task done.
   - *The history.* Each file the undo puts back is recorded in the change
     history under the person's name (17.6), except those below `.git/`,
     `.researchos/` and `.rce/`: those are put back all the same, and kept
     out of the history as attribution (item 3) keeps them out (a
     repository's `config` can hold a remote URL with a token).
   - *Folders.* The picture lists every folder its walk went into, empty
     ones and ones that hold only noise it does not record included. A
     picture cut at its limit cannot say which folders existed, and undo
     then removes none; one taken before folders were recorded falls back
     on the folders its files stand in.
   - *The first run's confirmation* names the files over 16 MB from
     `GET /api/agents/large-files` (the ten biggest, then a count): a
     stat-only walk by the rules of the before-picture. If the engine cannot
     say, the confirmation is still shown, without the names.
6. **Smaller.**
   - Rule 3's suffixes add `.sqlite`, `.db`, `.npy`, `.npz`, `.mat`,
     `.sas7bdat`, `.jsonl` and `.pickle`. `package.json` and
     `package-lock.json` are not data.
     - *How.* One list, `snapshot.DATA_SUFFIXES`: the data walk and
       `protected` read it, and the hook gets its files from `protected`
       through `guard.json`. The rule the agent is sent (`brief.rules`)
       spells it in words, and a test keeps the two together. The two names
       are `snapshot.NOT_DATA_NAMES`, compared by file name in any folder, case
       folded (a Mac's names are): `package.json.bak` and `my-package.json`
       are not those names, and `package.jsonl` is data.
   - OpenCode's git denials match any command containing `git` and a rule-4
     verb as separate words, not only commands that start with `git`. The
     accepted cost is an occasional refusal of a harmless command.
     - *How.* Each verb of `runners.GIT_DENIED` has the patterns of
       `runners.opencode_git_patterns` in `permission.bash`: `*git <verb>*`
       (the verb right after `git`, as before) and `*git * <verb><end>` (git's
       own options between), where `<end>` is the end of the command, a space,
       or one of tab, newline, `;`, `&`, `>`, `<`, a backtick, `'` and `"`
       (`>` and `<`: OpenCode matches a redirected statement as a whole, so
       `git -C . push>/dev/null` has to end its verb there). The verb must be a
       word of its own: `git add clean_data.py` and `git describe --tags` pass,
       `/usr/bin/git push`, `env git push`, `cd x && git commit`, `git -C . push` and
       `bash -c 'git -C . push'` do not. Only `*` is used: `|` and `)` are left
       out, because a matcher that does not escape them reads `|` as an
       alternative and the pattern would match every command. The installed
       OpenCode splits a command into its parts and matches each, so
       `git -C . push|cat` and `(git -C . push)` are caught through the part
       `git -C . push`.
     - *One list.* `runners.GIT_DENIED` is `guard.GIT_MUTATING`, sorted: the
       hook (rule 4's nine verbs, and `cherry-pick`, `revert`, `switch`,
       `restore`, `pull`, `am`, `filter-branch`, `update-ref` and `replace`),
       Claude Code's `--disallowedTools` and OpenCode's patterns (which are
       all OpenCode has: it has no hook) cannot drift apart, and a test says
       so. Before this, OpenCode could `git pull` or `git update-ref`.
   - Hook refusals and run errors show in the person's language.
     - *How.* Every refusal of the guard is a `guard.Refusal`, a `str` (the
       English sentence addressed to the agent, as before) that also carries a
       code of `guard.REFUSAL_CODES`: `unchecked`, `no_file`, `outside_project`,
       `records_folder`, `git_files`, `agent_settings`, `protected_file`,
       `off_limits`, `research_os_command`, `git_state`, `engine_api`. The hook
       logs the code in `refusals.jsonl` and ends what it says to the agent
       with `[refusal: <code>]`, because the run log is read from the program's
       own event stream and that is what travels in it. The streams give
       a step `code` (only when there is one): the hook's, `permission_rule`
       (Claude Code's deny list, OpenCode's rule or rejection),
       `tool_unavailable` and `mcp_failed`. The end of a run that failed or was
       interrupted gets the code of its error (`run.error.code`), also when
       the engine writes that end itself (an interrupted run has no result
       event) and then the error's detail is its text, never a sentence in the
       language the run began in; the engine-written end of a stopped run has
       the code `stopped` and no text. A step's `text` stays the program's or
       the engine's own words. Claude Code's end text is the result text or
       its errors; the result's `subtype` (`success`, `error_max_turns`) is a
       code, never the text, so a run that stops with no closing sentence ends
       with no text.
     - The page tells a refused step, an error step and a failed end by their
       code in the person's language (`t()`), and puts `text` beneath it as
       the detail (「详情」 "Details"). A code the page does not know, a refusal
       logged before codes, and a program's own error say it generally (「这一步被
       拒绝了」, 「程序报告了一个错误」, 「这次运行没有成功」); the same holds for
       a run state the page does not know (「状态未知」). A raw code is never
       shown. `error.detail` stays under 「详情」 in the run's error, as before;
       the engine no longer puts the code in the place of a missing detail.
   - `stderr.log` is capped as the event log is.
     - *How.* The program's standard error reaches the engine through a pipe
       and a thread copies it into `stderr.log` while the file stays within
       `STDERR_LIMIT`, which is `EVENTS_LIMIT` (20 MB). What does not fit is
       dropped, the run is marked `stderr_truncated` (and its detail says so),
       as `events_truncated` marks the events, and the pipe is read to its end
       all the same, so that a program that writes a great deal is never held
       up. The page shows neither mark.
     - *The end is kept.* The cap keeps the beginning, but what the person
       looks for in 「程序的错误输出」 is how the program died. The thread
       also holds the last `STDERR_TAIL_BYTES` (4096) the program wrote and,
       when the cap was hit, leaves them in `stderr.tail` when standard error
       ends. The detail's `stderr_tail` is that file for a run marked
       `stderr_truncated` (else the end of `stderr.log`, which is also what
       it falls back on if the file is not there, say after an engine that
       died first). `stderr.log` itself stays at the cap.
   - `internal`, the code of any crash of the run's thread (writing the run's
     files before the program starts, taking the pictures, reading the
     output), is worded for all of them: 「Research OS 在运行中出了错。」
   - When the engine starts, runs of every registered project that were
     left preparing or running are recovered at once, not only when that
     project's runs are listed. A process still alive that is verifiably
     the run's is ended, and the state is `interrupted`.
     - *How.* `serve` starts a thread (not `build_server`, so a test server
       starts none) that goes through every project that has runs on this
       Mac, not only the ones the registry lists now: a project removed from
       the registry still has a program that may be alive. It is the code of
       listing the runs (17.3): marked `interrupted`, the program ended only
       when its pid has the start time recorded at launch, a run of another
       live engine left alone. A thread, because a project of 50,000 files
       takes seconds to compare, and the app waits for the engine's first
       answer.
     - *A folder that cannot be read.* A run whose project folder is not
       there at that moment (a drive that is not attached) still has its
       program ended and reads `interrupted`, but its changes cannot be
       compared. It is marked `check_pending`, and the comparison is made by
       the next recovery that finds the folder again (a listing, a start, the
       next engine start), unless a run of the project is going then. Until
       then it cannot be undone, and no out-of-bounds change is reported;
       afterwards it can and is. A folder that stays away keeps the mark.

**The cookie's limit.** The session cookie belongs to the host `127.0.0.1`,
not to the port: cookies are not scoped by port, and SameSite=Strict does not
separate the ports of one host. In the browser flow (`researchos serve`), a
server an agent starts on another port of `127.0.0.1` (to preview a figure,
say) receives the cookie if the person opens it in the same browser while it
runs, and could replay the secret as `X-ResearchOS-Session`. The app is not
affected: its window refuses to leave the engine's own address. This round
leaves it as it is: removing the ambient cookie means the page sending the
secret itself on every request, a change of every request the page makes,
which is for a round of its own. Until then: do not open an agent's local
server in the browser that runs Research OS, or use the app.

**17.5's limit, restated.** Research OS runs no operating-system sandbox. A
program the agent writes and runs can still read and change anything this
Mac's user can, the session file and the signing key included. Such a
program, written to dig out the session, could act through the engine
unseen by the after-run check. Everything short of that is now refused or
attributed to the run. Closing the rest needs a sandbox, which this round
does not add.

### 18.2 Terms (amends 13.4, 14.6, 17.9)

- **研究判断** stays as an attempt's verdict: "Research assessment". 13.4's
  row becomes 尝试 / 研究判断 / 结果, "Attempt / Research assessment /
  Result". Where 判断 means the attempt's (修改判断, 提议修改判断), it
  becomes 研究判断.
- **连线确认** is a person's confirming or rejecting of a machine link,
  formerly 判断; English stays "Link judgments". The node page's section,
  its filters and every sentence that says 判断 for a link say 连线确认
  (for example 待复核的连线确认).
- **正式版本** "Official version", 12.2's word. The English "Published
  version" is retired: 发布 "Publish" is only the act. 官方的一份 becomes
  正式的一份, "official copy".
- **备注 and 笔记.** 备注 is a comment on an object, on the node page and in
  the panel alike (添加备注, 写一条备注…): "Comment", "Comments". 笔记 is a
  document in 项目首页: "Note", "Notes". 改动与备注 becomes "Changes and
  comments". Nothing on disk changes; `notes.toml` keeps its name.
- **Page and output names.**
  - **图表与论断** "Figures and claims", formerly 成果 "Outputs".
  - **输出** "Outputs" is a script's outputs; 产出 becomes 输出.
  - **结果记录** "Result records" are documents in 项目首页. **结果**
    "Result" is the attempt's cell.
  - **研究路线** "Research routes" is the merged page (18.3); 工作台 "Desk"
    is retired.
- **Other single words.** 本机 is "This Mac" everywhere. The experiment
  node kind is 实验 "Experiment" everywhere; 实验记录 and "Run record" leave
  the interface (运行记录 is an agent run's log, 17.9).
- **The five English words** follow the page, not the 2026-10-12 table:
  - 改动与备注: "Changes and comments" (above);
  - 连线确认: "Link judgments";
  - 关系图: "Relationship map", and the engine's "relation map" follows;
  - 补上来源: "Add a source";
  - 缺尝试: "Missing attempt".
- **13.4 gains** 项目首页 "Project home", 负责人 "Owner" and 任务 "Task". It
  also gains the data page's filters 未识别生成脚本, 中间数据 and 同名文件,
  with the English the page already has.
- **Text rules.**
  - The sentences in `team.js` that, since `6a4fa83`, join translated and
    bare fragments become one template each (13.6).
  - English entries that no key uses are removed.
  - The glyph test also refuses », ⟶, ‹, ›, ⏵, ● and ○, and reads
    `workspace.css` and the page's style block.
  - `workspace.js` joins the converted surfaces.
  - 项目首页's two disclosures use 12.13's chevron.

### 18.3 The sidebar and saving (amends 12.9, 12.0, 14.3)

- **研究路线 is one page.**
  - It holds Section 14's route map with its 待办 column, and a switch 图 /
    列表. 列表 is the grouped list that 12.9 called 研究路线 (by status,
    proposals shown distinctly, abandoned routes with their reasons), with
    the same 待办 column.
  - An attempt opened from the list opens the same side panel as on the
    map.
  - The switch is remembered per project in the browser, a per-viewer
    convenience.
  - Anything that opened 工作台 or the old 研究路线 opens this page.
  - In a team project, 待办's count shows beside 研究路线 in the sidebar.
  - The Mac menu has one item for it.
- **The sidebar** reads 项目首页, 研究路线, 图表与论断, 变量, 文献, 数据与代码,
  and, in a team project, 提议 and 成员. 项目首页 stays the first page.
- **A one-person project** (no team file, 12.1):
  - The sidebar shows no 提议, no 成员 and no identity reminder, and the Mac
    menus show no 提议, 成员, 提交提议 or 发布.
  - The project menu gains 开始协作…, which opens the existing team setup.
  - Once a team file exists, all of these return.
  - A team project that this Mac is not part of shows them.
- **Changing an existing row's 研究判断 or 结果 saves at once.**
  - It is the same write as before (only what changed, backed up, in the
    history, conflict-checked), without the preview step.
  - The panel then shows 已保存 and 撤销. 撤销 writes the previous values
    back the same way: a second history entry, not an erasure. It is
    offered until the panel closes or the row changes again.
  - Choosing 已放弃 still asks for the reason and confirms first, because
    it freezes the basis. A new row keeps its preview. A member's change is
    still a proposal (12.4).
- **项目首页.**
  - **Order:** notes and result records by last change, newest first;
    attempts by number.
  - **Headings:** a document's heading is written only when the person
    typed a title different from the one the file shows, whatever the
    screen's language (13.1). Typing during a save keeps the title.
  - **More than 500 files:** the page says so where files are chosen. A
    reference to an existing file beyond the 500 is still known.
  - **A member's 提交 of tasks** takes along the result records that the
    changed tasks link.
  - **Read-only notice:** on a Mac that is not a member it says 这台 Mac
    不是这个项目的成员，只能查看.
  - **Officialness words:** 13.4's (我的改动，尚未提交 / 提议中 / 已批准，
    待同步), from the same module as everywhere else.
  - **An unreadable project identity** shows the page with that said, not
    an error.
  - **Other files:** Markdown files with other names in `research/notes/`
    and `research/results/` show too. `research/` is not read as a paper
    draft.
  - **A PI's 项目首页:** notes and result records say they go to the
    members with the next 发布.
  - `docs/project-workspace.md` and its Chinese twin use the labels on
    screen.

### 18.4 Speed (no change of behaviour)

Every item leaves payloads as they were, adds no dependency, and keeps
nothing new inside the project.

1. **图表与论断:** one shared trace context per request. Attempt rows, the
   configuration, link flags and variable cards are read once; file
   checks are remembered; a figure is traced once. 设为论文正文 keeps the
   generation it gets back.
2. **Agent runs:**
   - The after-picture reuses the before hash of a file whose size,
     modification time and inode are unchanged.
   - `GET /api/agents/runs/<id>` reads the event log from where it last
     stopped and lists runs once per request.
   - 项目首页's run poll stops while the window is hidden and polls once
     when it is shown again.
3. **The generation poll** pauses while the window is hidden and runs at
   once when it is shown.
4. **Teams:**
   - A published manifest or signature is parsed and verified once per
     content, keyed by its bytes' SHA-256, in memory. `ssh-keygen` runs once
     per data, signature and key.
   - The working-copy walk takes one `lstat` per file and writes its
     fingerprint file only when an entry changed.
   - Publishing and approving stop rebuilding the file map for each file.
   - A proposal's snapshot id is built on the fingerprints.
5. **File preview and drafting** read at most 200 KB and one byte, and take
   the size from the open file.
6. **The change history behind 研究路线** uses a time index and reads only
   the window it shows.
7. **Smaller:**
   - `/api/summary` counts in SQL.
   - `GET /api/agents` remembers a program's version while its path, size
     and modification time are unchanged.
   - 项目首页 takes `/api/me` and `/api/share` from the page's shared
     context.
   - `/api/stata` remembers each script's reading by path, size and
     modification time.

### 18.5 Acceptance

1. A Python script started by a run that POSTs to the engine, without the
   session and with `Host` and no `Origin`, gets 403 and changes nothing.
   The app and `researchos serve` (browser) still save everything they
   did. A page opened without the session reads, and refuses its first
   write with 18.1's sentence.
2. In a run:
   - `Read` of `~/.researchos/settings.json` is refused.
   - `env` shows no `RESEARCHOS_HOME` and none of the removed variables,
     and the log names them.
   - A project `.claude/settings.json` written by the agent is out of
     bounds, and is not loaded by the next run.
   - A watcher scan during the run records the agent's new file under the
     agent.
   - A planted `.git/hooks/pre-commit` makes the run read 越过了限制.
3. Undo lists its files with checkboxes. An unchecked file is left as it
   is and listed as kept. A folder that existed before the run survives
   undo.
4. Terms: no screen says 判断 for a link or 工作台; English shows no
   "Published version" and no "Outputs" for 图表与论断; node page and
   panel both say 备注. The language tests list no Chinese left on the
   converted surfaces, and the glossary test (13.4) matches the page.
5. The sidebar of a one-person project has six items and no identity
   reminder; 开始协作… is in the project menu; after a team file exists, 提议
   and 成员 appear. 研究路线 switches between 图 and 列表 and remembers it.
6. Changing a 研究判断 in the panel writes at once, and 撤销 puts it back;
   both are in the history. Choosing 已放弃 still asks for the reason.
7. Speed:
   - On a fixture of 400 figures and 10 variable cards, 图表与论断 answers
     in under 0.5 s.
   - A member's page with 30 published snapshots answers in under 1 s.
   - Previewing a 1 GB file raises memory by less than 5 MB.
   - The history window at 100,000 versions takes under 60 ms.
   - On the researcher's copy, no endpoint is slower than before.
8. The full suite passes on Python 3.11 and 3.13.
