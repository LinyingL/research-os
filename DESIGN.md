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
| (reads, not found) / (writes, not found) | 断链：读取 X，文件不存在 / 断链：写出 X，文件不存在（21.7；曾是（读取，文件不存在）/（写出，文件不存在）） |
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
quiet line chevron, not the browser's triangle. (*Amended by 21.5, B3-2*: the
mark is one control with a picker, not a button on each draft; a figure or table
can be put out of the paper in the same file.)

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

A quiet text button in the sidebar's tool row beside 备份 (21.6; it was 记录) and 停止服务,
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
| 项目首页 | Project home |
| 研究路线 | Research routes |
| 路线图 / 列表 | Route map / List |
| 图表与论断 | Figures and claims |
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
| 改动与备注 | Changes and comments |
| 连线确认 | Link judgments |
| 关系图 | Relationship map |
| 尝试 / 研究判断 / 结果 | Attempt / Research assessment / Result |
| 待复核 | Needs review |
| 缺来源 / 补上来源 | Missing source / Add a source |
| 缺尝试 / 归入尝试 | Missing attempt / File under an attempt |
| 公盘 | Shared folder |
| 正式版本 | Official version |
| 工作副本 | Working copy |
| 批准并加入 | Approve |
| 退回并说明 | Return with reason |
| 我的改动，尚未提交 | My change, not yet proposed |
| 提议中 | Proposed |
| 已批准，待同步 | Approved, awaiting sync |
| 本机 | This Mac |
| 最初的研究者 | Original researcher |
| 负责人 | Owner |
| 任务 | Task |
| 笔记 | Note |
| 备注 | Comment |
| 报告 | Report |
| 实验 | Experiment |
| 输出 | Outputs |
| 缺生成脚本 | No generating script |
| 中间数据 | Intermediate data |
| 同名文件 | Same-name files |
| 含义 / 输入 / 构建口径 | Meaning / Inputs / Construction |
| 实现依据 / 人工决策 | Implementation / Human decision |
| 草稿 / 确认这一版 | Draft / Confirm this version |

This table is kept as the page speaks (amended by 18.2, task V14, and by 21.6, task V16: 报告 for 结果记录, 缺生成脚本 for
未识别生成脚本, 归入尝试 for 关联文件, and 备份 for the sidebar's 记录 in the list below). A row
written `A / B / C` gives three words in the order written. A counted word
(13.5) is given in one form, and the page's entry holds both.
`tests/test_glossary.py` reads this table and the lists of words below it
and in 14.6, 15.5 and 17.9, and holds the English table in `i18n.js` to
them in two ways. A Chinese word that is a key there carries exactly the
English written here (a counted word, either of its two forms). And the names
that sentences carry (研究判断, 连线确认, 正式版本, 关系图 and the others the
test lists) read the same inside every sentence that has them. A word that is
no key of its own (it lives only inside sentences, or belongs to a page not
yet converted, as 含义 to 确认这一版 do, 13.7) is not compared. When a row
changes here, the test shows each place the page still has the old word.

Research assessments: the six default statuses and 「其它」 take the words
above. A status the researcher named in `[verdict_labels]` shows their word
as written (status `labelled`), in either language.

Fixed for the desk and the frame (first increment): 等你批准 "Awaiting your
approval"; 我的提议 "My proposals"; 你上次查看以来的改动 "Changes since you
last looked"; 依据变了的图表与论断 "Figures and claims whose basis changed"; 已放弃的路线
"Abandoned routes"; 备份 "Backups"; 停止服务 "Stop service"; 研究脉络 (the
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
- **正式版本有更新** and 同步 (a member), as the desk had it.
- Lines with a missing end (14.2).

With nothing to do the column says so in one quiet line. *(21.4 amends this
section: the column is 等你处理, one list with the person's handbacks and
runs, its blocks are the chips of its rows, and with nothing to do it folds
to a strip.)*

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
  form (since B4-3 on the 设置 page, 21.7): each field shows the value saved on this Mac (empty when none),
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

### 16.4 Word documents (amends 16.2)

The researcher, 2026-10-26: a Word document chosen under 辅助填写 was
refused as 「不是文本文件」. 16.2 reads a chosen file as `GET /api/file`
reads it, and since 0.3.0 that read gives a `.docx` as the panel's Word
preview (paragraphs and tables), not as text, so the draft took it for a
file that is not text. Decided the same day:

- A `.docx` is read for a draft by the panel's Word preview
  (`researchos.docx_preview`): `word/document.xml` only, its body
  paragraphs and tables in document order, a tracked deletion left out; no
  header, footer, footnote, comment, image or embedded object. It is sent
  as plain text: a paragraph a line; a table a row a line, its cells
  joined by " | ", a line break inside a cell read as a space; an equation
  as `[Equation]`, an image or object as `[Image or object]`. That text is
  what the number check (16.1) compares against.
- It shares the 12,000 characters and the 5 files with every other chosen
  file, and a long one is cut the same way, from its beginning: for a
  paper, usually the title, the abstract and the start of the
  introduction; a results table further on is not sent. The form's
  「较长的文件只读了开头」 says so. (The researcher chose this over sending
  the tables first.)
- A `.docx` the preview cannot read (not a Word body, damaged, or past its
  64 MiB archive or 8 MiB body limit) is `draft_file_unreadable`,
  「没能读取 {file}」, the preview's reason behind 「详情」. Every other
  format the draft still cannot read keeps `draft_file_not_text` and its
  words: a PDF, an older `.doc`, a spreadsheet, a .dta.
- A path typed whole (`/Users/…/project/paper.docx`) inside the project is
  the same file as its path from the project folder, as 16.2 says: the
  model sees it by that path, and a refusal names it as typed. Outside the
  project it is `draft_file_outside`.

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

- **Three runners**, each an external command-line program Research OS finds
  but never installs: `claude-code` (Claude Code, `claude`), `opencode`
  (OpenCode, `opencode`), and `codex` (Codex, `codex`). An **agent** is a runner plus a model:
  `claude-code` with `sonnet`, `opus`, `haiku` or a full model name;
  `opencode` with `provider/model` as `opencode models` lists it (an LM
  Studio or Ollama model configured in OpenCode is how a local model is
  used). Codex uses full model IDs from its catalog; see the Codex runner
  amendment below.
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

Added by 21.2 (the handback; the table of 21.6 has them too): 交回 "Handback"; 代理建议 "Suggested by the agent";
确认这个判断 "Confirm this assessment"; 报告 "Report"; 运行日志 "Run log" (21.6 renames 运行记录 to it; the page changes the
word where it converts that screen).

Added by 21.3 (the assessment as a choice): 用本地模型辅助填写 "Draft with a local model" (the folded block of the new-attempt
form; 辅助填写 stays the name of the field inside it).

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
   - Why `--add-dir` and a variable go with it. `--setting-sources user` also
     stops the project's own `CLAUDE.md` from loading, and the agent would
     lose the project's own instructions. So Claude Code is also given the
     project root as `--add-dir <project root>`, and its environment carries
     `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`, which is what makes
     Claude Code read the `CLAUDE.md` of an added folder. Checked with
     Claude Code 2.1.87: with `--setting-sources user` alone the project's
     `CLAUDE.md` is not read; with the two added it is, and a
     `.claude/settings.json` planted in the project still has no effect, so
     the settings door stays shut.
     - The folder added is the folder the program already starts in, so the
       agent is given no place it did not have. `--add-dir` takes any number
       of values, so a flag always follows it in the argument list; the tool
       lists stay last.
     - The variable is set after the scrub, to `1` whatever the engine's own
       environment says; it matches neither the session-identity pattern nor
       a credential word, and the names removed (`env_removed`) never include it.
       OpenCode is given neither.
     - `CLAUDE.md` is instructions to the model, not configuration: it grants
       no permission and does not touch the hook, and the rules and the hook
       hold whatever it says. A run that edits it shows in the change list
       like any other file.

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
       The words the agent is sent for rule 4 (`brief.rules`) are made from
       the same list, sorted, so that they name every command that is refused
       (before, they named the nine of 17.5 and left the rest out), and they
       add that deleting or renaming a branch counts too, which the hook also
       refuses; a test reads the verbs out of the words and compares them with
       the list, in both languages.
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
       the engine no longer puts the code in the place of a missing detail
       (a Claude Code result with `is_error` and neither errors nor text fails
       with its code and an empty detail, and the page shows no 「详情」).
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
  becomes 研究判断. No other word names that cell: 结论 and 判决 (and "verdict") are
  not used for it in the 记录 panel, the attempt form, a proposal's conflict line
  (第 {n} 行的研究判断或结果在正式版本里也改了), the instruction an agent is sent, or
  `researchos history`.
- **连线确认** is a person's confirming or rejecting of a machine link,
  formerly 判断; English stays "Link judgments". The node page's section,
  its filters and every sentence that says 判断 for a link say 连线确认
  (for example 待复核的连线确认).
  Where a count or a list may also hold a variable card (a script changed under
  it), the word is 项: the top bar's 「N 项待复核」, the desk's 「N 项的依据变了，等你复核。」
  and the panel's subtitle 「依据变了的旧连线确认和变量卡，等你再看一眼」. 连线确认 stays
  where every item is a link: the empty note, the ledger notices and the node
  page's tag.
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
    A result document with no heading is titled 结果记录 ("Result record"), as
    its group is.
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

  How they are read:
  - *Joined sentences.* A date between two bare halves (放弃于 …，记录了 n
    个文件的状态) is `放弃于 {when}，…`, one template for each ending, the date
    lower case inside an English line (13.5); a verdict between two words
    (研究判断 … 改为 …) is `研究判断 {old} 改为 {new}`, its two verdicts put in
    by `phrase`. The proposal kinds a member chooses between are
    `kindChoices()`, looked up when the form is drawn, and 提交提议 is
    translated whole, since its labels sit beside labels that already were
    (源自（可选）). The rest of `team.js` waits for its own increment (13.7), so
    the language test holds these stretches only (`CONVERTED`).
  - *Entries that no key uses.* An entry is kept while its Chinese is the
    whole first argument of a `t()`, the text of a `data-i18n` element or
    attribute, or a whole string literal where a table or the engine hands
    it to `t()` by a variable (`t(VIA_WORDS[x])`, a label `nodepage.py`
    sends). The test reads the same three places and lists any entry left
    over; a sentence reworded by a rename takes its old entry with it.
  - *The glyph test.* The list gains », ⟶, ‹, ›, ⏵, ● and ○ (a mark that
    stands in for a word). The stylesheets are read as the page loads them:
    `workspace.css` and each `<style>` block. Only what reaches the screen
    counts, the `content:` strings with their escapes (`"\25B8"`) read as the
    characters they spell; a comment or a selector is not text.
  - *项目首页.* `workspace.js` is converted whole: its helpers (`button`,
    `field`, `input`, `errorWords`, `named`, `quiet`) take words that are
    already for the screen, so every call passes `t("…")`, and a table that
    maps a code to a sentence (run states, refusals, errors, why a file was
    left alone) holds functions of `t()`, so that each sentence is looked up
    when it is shown. A code the table does not know reads as a generic
    sentence, never as the code.
  - *Disclosures.* The summary of 引用文件或尝试 and of 详情 is
    `res-draft-summary` with the chevron `chevronIcon()` draws; the browser's
    triangle is hidden by the class and `workspace.css` turns the chevron when
    the disclosure is open, as `.res-draft` does elsewhere.

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
  - The switch's buttons read 路线图 and 列表, "Route map" and "List": 13.4's
    own word for the map. A bare 图 would be a second English for a key the
    table already holds (图, "Figure": the kind label of a figure in 图表与
    论断), and a Chinese word has one English (13.4). 图 above and in 18.5
    means that button.
  - Anything that opened 工作台 or the old 研究路线 opens this page (in the
    mode kept for the project; the Mac menu's two commands, `desk` and
    `routes`, stay valid names and open the same page).
  - In a team project, 待办's count (the rows the column lists) shows beside
    研究路线 in the sidebar when it is not zero. A one-person project shows
    none. *(Replaced by 21.4: the column is 等你处理, and the one count of its
    rows shows beside 项目首页 in every project, a project of one person
    included; there is none beside 研究路线.)*
  - The Mac menu has one item for it, ⌘2; 项目首页 is ⌘1, and the shortcuts
    follow the sidebar's order (⌘1 to ⌘8).
- **The sidebar** reads 项目首页, 研究路线, 图表与论断, 变量, 文献, 数据与代码,
  and, in a team project, 提议 and 成员. 项目首页 stays the first page.
- **A one-person project** (no team file, 12.1):
  - The sidebar shows no 提议, no 成员 and no identity reminder, and the Mac
    menus show no 提议, 成员, 提交提议 or 发布.
  - The project menu gains 开始协作…, which opens the existing team setup.
  - Once a team file exists, all of these return.
  - A team project that this Mac is not part of shows them.
  - **How it is read** (task V14, G3-2):
    - *Which project.* The engine's role for the open project is `single`
      (`/api/proposals`; 12.1: no team file, an empty one, or only its PI)
      and its answer says `team`, true when a team file lists anyone. A
      project of one person is `single` with no `team`. Everything else shows
      the items: a PI or a member, `none` (a team project this Mac is not
      part of), `unknown` (a team file that cannot be read), a role the page
      could not read at all (never taken for one person), and a PI who has
      made the member list and added nobody -- the engine's word for them is
      still `single`, but a team file exists, and 成员 is where they go on
      to set the shared folder and add the first member.
      With no project open the sidebar is hidden whole, as before.
    - *The sidebar.* The nav starts marked one person's and the page flips
      the mark each time it reads the role (the sidebar's other live parts
      read at the same moments: after the page's own acts, on the desk's
      reload when the generation moves, on a project switch), so the items
      come back with no reload, and a one-person project never shows them
      for a moment. The stylesheet hides exactly those two items under the
      mark; the markup keeps 12.9's eight. The line at the foot keeps the
      name once it is set and loses only the reminder 「还没有设置你是谁」
      with its hover text; the form is on the page 开始协作… opens.
    - *The Mac.* The page tells the shell `single` for such a project (and
      with none open), and `none` for a role it could not read, so
      the menus show 提议 and 成员 and not 提交提议 or 发布 (which need a
      role). The shell hides 视图 > 提议 and 成员 for `single`; ⌘7 and ⌘8
      then do nothing, and ⌘1 to ⌘6 are the sidebar's six items.
    - *开始协作….* In the project menu, right after 推送到 GitHub…, in a
      project of one person only. It opens the 成员 page, read afresh as the
      sidebar's own click reads it: 我是谁 first, then the 开始协作 block
      with 建立成员名单. The page keeps its title 成员 and no sidebar item
      is marked, as the item is not there.
    - *What asks for 提议 in such a project* (a Mac app built before this
      round whose menu still has ⌘7, the compose command, a view kept from
      another project) is routed to 开始协作, not ignored: someone who asked
      for proposals is told how to have them. A page that asked for 提议
      before the role was known goes on to it once it is. 成员 itself is
      always a page of its own.
- **Changing an existing row's 研究判断 or 结果 saves at once.**
  - It is the same write as before (only what changed, backed up, in the
    history, conflict-checked), without the preview step.
  - The panel then shows 已保存 and 撤销. 撤销 writes the previous values
    back the same way: a second history entry, not an erasure. It is
    offered until the panel closes or the row changes again.
  - Choosing 已放弃 still asks for the reason and confirms first, because
    it freezes the basis. A new row keeps its preview. A member's change is
    still a proposal (12.4).
  - **How it is read** (task V14, G3-3):
    - *The preview is asked, not shown.* The PI's and a single researcher's
      save asks the engine's preview for the same body and, unless it says
      the write turns the route abandoned, sends that body to the write at
      once. For an update the preview also lists, per cell it would change,
      `cells`: the cell as the file holds it (`was`: its Markdown decoration,
      `<br>` and pipes as written, so that sending it back writes it as it
      was), as the parser reads it (`was_text`) and as it will read after the
      write (`now_text`).
    - *Where.* In the side panel and in the older edit form of the node page
      (编辑这一行). The button reads 保存. In the older form only a change of
      研究判断 and/or 结果 saves at once: one that also changes the date, the
      description or the variables is previewed and confirmed as before, and
      a new row keeps 预览改动. A member's 预览这一行 is unchanged.
    - *Conflict.* A row is written at once only while the cells the file
      holds are still the text the form showed (`was_text`). If the file
      changed meanwhile the row is shown instead, 原行 being the file's with a
      note saying so, and written on 保存修改, after which 已保存 and 撤销 are
      offered like after any save. A write the engine refuses says 写入失败，
      文件没有被修改 with the engine's own text behind 「详情」, and what was
      typed stays.
    - *撤销.* It writes the previous cells (`was`) back through the preview
      and the write: the row is what it was byte for byte, and the history
      and the backups hold both writes. The offer stays in the panel until it
      closes, drawn again after a refresh only while the row still reads as
      the save left it; a second save offers back its own step only. Before
      it writes, 撤销 asks the preview once more: a cell that no longer reads
      as the save left it (the file changed in between), or an old verdict
      that would abandon the route again (which needs its reason, 14.3), is
      not written, and the panel says why in a sentence.
    - *Abandoning* keeps its reason, its preview and 保存修改, and then says
      已写入 as before, with no 撤销: the basis frozen in `decisions.toml` is
      not written back by restoring the row.
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
  - **How it is read** (task V14, G3-4):
    - *Order.* `overview` sorts notes and result records together by the
      file's modification time, newest first, and only then cuts at 500, so
      it is the oldest that are left out. A save answers with `modified` and
      the page puts the saved document at the top; the page also sorts by
      `modified` itself, the engine's order standing for ties. Attempts sort
      by file, then by number as a number with `attempt_sort_key`'s rule (14a
      right after 14), then by id.
    - *Headings.* The engine decides, because it is the one place every writer
      goes through (the page, the agent tools, a script) and the screen's
      language is not always the one the file was read in. For an existing
      file, a title is typed when it differs from the heading the file has;
      for a file with no heading, when it is neither language's default for
      its kind (笔记 / Note, 结果记录 / Result record, 研究问题与计划 /
      Research question and plan). A new overview saved with its default title
      writes none. A new note or result record is always given the title
      typed, which the page requires. The cost is that someone who types
      exactly the other language's default as the title of a headingless file
      gets no heading; the file would show the same words anyway. `save_document`
      takes the request's language for the title it answers with.
    - *Typing during a save.* When the writing area differs from what was
      sent, the page applies to it the same rule the engine applied (the one
      `previewContent` already mirrors) if, and only if, that rule gives the
      text the engine answered with for what was sent. The words go into the
      writing area with the caret kept in place; while an input method is
      composing they wait for it to end.
    - *More than 500 files.* A sentence under the file chooser says that the
      list is cut. `GET /api/workspace/exists` answers which of the paths
      given (at most 200) are files `overview` would list if it were not cut:
      regular, inside the project, not hidden or in a folder the scan leaves
      out, no link on the way, not only in the cloud. The preview and 提交修改
      ask it only for the links the list does not hold, only when the list was
      cut, and remember the answers until the page reads the project again.
    - *Tasks and their records.* `overview` and the task save answer
      `changed_task_reports`: the `report` of each task that is new or differs
      from the official version the working copy was synced to (all of them if
      that cannot be read), for a member only. The page adds them to
      `research/tasks.json` in 提交修改; a path that is no change is left out
      by the proposal itself.
    - *Officialness.* `local_annotations` builds the officialness module's own
      view of the workspace files only (their hashes against the base) and
      asks the module for each file's state and words, so 提议中 and 已批准，
      待同步 show as they do on every page. It reads the member's own
      proposals from the shared folder, as the other pages do, waiting for it
      no longer than the desk does (`SHARED_TIMEOUT`, 14.4) and never under the
      project lock: a save writes the file, releases the lock, and only then
      builds the mark. A folder that does not answer in time leaves the words
      of the local comparison and `pending_known` false. It does not walk or
      hash the rest of the project. The mark has no `pending_known`;
      `officialness_mode` is the module's mode answer, which has it.
    - *Names.* Any visible `.md` name directly in the two folders is listed
      and can be opened and saved; one that does not exist cannot be created
      by naming it, and nothing is read from a deeper folder or from a link.
      `research/tasks.json`'s `report` still names a 32-hex result record.
    - *Backups of a hand-made name.* A save of an existing note or result
      record keeps the copy it replaces and the daily snapshot under
      `.researchos/backups/`, which name a series by the bare file name (the
      newest 20 copies, one snapshot a day). The engine's own names (32 hex
      digits), the overview and the task list stay in that folder; a name of
      the researcher's own is kept in `.researchos/backups/research/notes/` or
      `.../research/results/`, so `Summary.md` in both, or a note named like
      the attempt table, never share a series.
    - *Not a draft.* The files Research OS writes in `research/` at the project
      root -- `overview.md`, the files directly in `notes/` and `results/`, and
      the attempt table (`attempts.md`, or the file `attempts.toml` names in
      that folder) -- are skipped by `_is_paper_markdown`, which the citation
      step uses too, and count as skipped in the scan report. Any other
      Markdown file in `research/` is the researcher's own (a manuscript kept
      there) and is read as a draft, like one elsewhere. A skipped file is in
      the claims step's cleanup with no claims, so the claims (and their
      `backed_by` candidates) an earlier scan made of a note go on the next
      scan -- a judgment already made stays in the ledger, 9.1; the sections
      and figure links it made are left as they were.

### 18.4 Speed (no change of behaviour)

Every item leaves payloads as they were, adds no dependency, and keeps
nothing new inside the project.

1. **图表与论断:** one shared trace context per request. Attempt rows, the
   configuration, link flags and variable cards are read once; file
   checks are remembered; a figure is traced once. 设为论文正文 keeps the
   generation it gets back.

   How it is read:
   - *The context.* `trace.Shared(conn, root)` is made once per request by
     `nodepage.results` (the page, and a draft's claims) and handed to each
     `trace.chain(…, shared=)`. It is per request and per thread, and keeps
     nothing between requests. It reads nothing until a trace first needs
     it: the link flags; `attempts.toml` once, the verdict words taken from
     that read; the attempt rows, with an index from a step file's path to
     the attempts that name it (an attempt's `via` keeps its step files in
     the order its description gives them, then the files attached by hand,
     as before); the variable cards and a table's header; whether a path is
     on disk. A read that fails is not kept: an `attempts.toml` that cannot be
     read as text fails the first trace that needs it, with the same error,
     and building the context never fails. `chain` without a context builds
     its own, so the CLI, `/api/trace` and the node page read as they did.
   - *What the page asks.* The states need the breaks and the attempts only,
     so `chain(…, variables=False)` leaves the cards unread (`variables`
     comes back empty). A figure two sections include is traced once, and
     each item is given a copy of its state: the officialness marks are
     written into the payload in place.
   - *The mark.* 设为论文正文 stores the generation the POST returns in the
     page's counter and refreshes the page's views itself, as the page's other
     writes do, so the poll that follows finds nothing new.
2. **Agent runs:**
   - The after-picture reuses the before hash of a file whose size,
     modification time, inode and status-change time are unchanged.
   - `GET /api/agents/runs/<id>` reads the event log from where it last
     stopped and lists runs once per request.
   - 项目首页's run poll stops while the window is hidden and polls once
     when it is shown again.

   How it is read:
   - *The after-picture.* `snapshot.take(root, reuse=before)`. A file's entry
     in a picture gains `ino` (its inode) and `ctime_ns` (its status-change
     time) when the file was hashed and its
     modification time lay at least 3 s before the picture began: a stamp is
     as fine as the file system keeps it (2 s on FAT), and a file written
     again within that could carry the same stamp. The after-picture keeps a
     before hash for a file whose entry has `ino` and `ctime_ns` and whose
     size, modification time (ns), inode and status-change time (ns) are the
     same now; any other file is opened and hashed as before, and a file that
     is copied (the before-picture) is always read. A picture stored without
     `ino` and `ctime_ns` reuses nothing. The 64 MB and 50,000-file rules, the
     data walk and `fingerprint` are as they were, and the after-picture is
     the one a full read gives (`ino` and `ctime_ns` included). The
     status-change time moves with every write, every `chmod` and every
     `utime`, and a program cannot put it back, so a file rewritten in place
     with its modification time set back (a program's own `os.utime`, `touch
     -r`, `cp -p`, `rsync -t`) is hashed again and shows as changed, a
     protected file included (the check of 18.1 depends on it). A file whose
     status changed without its content (a `chmod`, an extended attribute a
     sync client sets) is hashed again too and found unchanged: it costs a
     read, never an answer. The data walk is as it was: it trusts size and
     time for the data files the main walk did not see. Not seen: a volume
     that keeps no status-change time apart from the modification time
     reports the set-back time for both, and there the case stays unseen.
   - *The log.* `detail` keeps, per run and in memory, a cursor into
     `events.jsonl`: the stream parser after the last complete line, the byte
     offset there, the file's device and inode and the 64 bytes before the
     offset. A poll reads what was appended, feeds the parser the complete
     lines, and reads a half-written last line on a throw-away copy of the
     parser, so the steps are the ones a read of the whole file gives (line
     splitting, the 20 MB limit and the closing step included). The cursor
     starts over when the inode differs, the file is shorter than the offset,
     or the 64 bytes differ, and also when a parser raised midway. It keeps
     the last 500 steps (plus up to 100 more, trimmed together) and the newest
     `say`, which the closing step is compared with, and counts the rest;
     the manager keeps the cursors of the 8 runs asked for last.
   - *The listing.* `GET /api/agents/runs` lists the project's runs once:
     `_recovered` returns the listing recovery worked from, with the runs it
     looked at (those recorded active, those owing a comparison) read again,
     and the page's answer is built from it; a run started since is found by
     listing again. `_summary_of` (the run's detail, the undo list) reads the
     runs newest first and stops at the newest run of the task.
   - *The window.* 项目首页 keeps no run-poll timer while
     `document.hidden`: a timer pending when the window is hidden is cleared,
     a poll that ends while hidden sets none, and when the window is shown
     again, if a poll was held back, one poll is made (it sets the next timer
     if a run still goes, and reloads the page if one ended). Nothing was held
     back, nothing is polled. 停止服务 ends it for good: the timer is cleared,
     a poll in flight sets none, and showing the window or returning to the
     home starts none.
3. **The generation poll** pauses while the window is hidden and runs at
   once when it is shown.

   How it is read: the page's interval is cleared when the window is
   hidden. When it is shown, `pollGeneration` runs at once (what moved
   meanwhile is read then) and the interval starts again; a page opened in a
   hidden window starts its poll when it is first shown. 停止服务 clears the
   interval and the run poll above, and nothing starts either again.
4. **Teams:**
   - A published manifest or signature is parsed and verified once per
     content, keyed by its bytes' SHA-256, in memory. `ssh-keygen` runs once
     per data, signature and key.
   - The working-copy walk takes one `lstat` per file and writes its
     fingerprint file only when an entry changed.
   - Publishing and approving stop rebuilding the file map for each file.
   - A proposal's snapshot id is built on the fingerprints.

   How it is read:
   - *The memories.* `researchos.memo.Memo` is a least-recently-used map in
     memory that the server's threads may share. It holds at most the
     entries (and, where entries are weighed, the total weight) named below,
     keeps nothing heavier than its whole budget, is lost when the engine
     stops, and is only asked with a key that holds everything the answer
     depends on: a content hash, or a file's path, size, modification time
     and inode. Losing an entry costs time, never an answer.
   - *A manifest.* `read_snapshots` still lists the folder and reads each
     manifest, its `ready.toml` and its signature on every call, so a
     snapshot that appears, goes or changes shows at once. What it works out
     of the manifest's bytes is kept by their SHA-256 and the number the
     folder gives the snapshot (1,024 entries): that the manifest is not
     valid, with the sentence saying why, or its `Head` (`seq`, `project`,
     `by`, `at`, `note`, `prev_seq`, `prev_manifest_sha256`, the count of its
     files, and its own entry for `.researchos/team.toml`). The listing, the
     chain check, the status and the previews read only the `Head`. The file
     list (`Snapshot.files` and `.data`) is parsed on first use, for the
     snapshot that is used, and kept for the last 4 manifests used and at
     most 60,000 file entries in all; a `Snapshot` builds its map once and
     hands it to every caller (none changes it), and the memory itself is
     never handed out. `payload()`, `files` and `data` give what they gave.
     A manifest with other bytes has another hash and is parsed, validated
     and verified again; so is the same text under another number.
   - *A signature.* `signing.verify` keeps `ssh-keygen -Y verify`'s answer by
     the program, the SHA-256 of the data, the SHA-256 of the signature and
     the key (1,024 answers). A "yes" is kept, and a "no" only when it is
     the program's judgement of the signature: the program exited with a
     failure, printed `Could not verify signature.`, and said either nothing
     (the key is not the one allowed) or `incorrect signature` or `Couldn't
     parse signature` on its standard error. `ssh-keygen` exits 255 for every
     failure it has, so a run that failed to judge (no user for the uid, no
     descriptor or memory, a file it could not read) is not an answer and is
     not kept: one such failure refuses that request's snapshot, not every
     later request until the engine stops. A run that did not end (no
     program, a timeout, a signal) is not an answer and is not kept;
     `check` reads it as "bad" as before. Proposals, outcomes and packets
     are verified through the same function.
   - *The chain.* `verify_chain` reads a snapshot's project, author and links
     from its `Head` and its signature from `signing.check`, and decides
     as it did on every call, in the same order, from parts remembered by
     content. `team.toml` is read from the folder on every call, its hash
     checked, so a file damaged since is seen at once and the snapshot
     refused as before; only what its bytes mean is kept, by their SHA-256
     (256 entries). The verdict as a whole is not kept: whether a snapshot
     links to the last good one depends on the snapshots before it, and the
     parts that cost something (the signature, the header, the team) are
     already kept.
   - *The walk.* `official_files` and `proposals.manifest` share one
     inventory (`share._inventory`): `os.scandir`, with the folders pruned
     as `ingest.files.iter_files` prunes them (noise and dot-prefixed
     folders, links not followed, hidden files and links left out, folders
     that cannot be listed skipped), the same files in the same order, and
     one `lstat` per file taken as its folder is listed (mode, size,
     modification time and status-change time in ns, inode and, on macOS, the
     flag that says it is only in the cloud: `share._is_dataless`, where the
     walk's tests put the cloud). A file that is about to be read (its
     fingerprint is not kept) is asked about the cloud once more, with a
     fresh `lstat` right before it is opened: the walk's flag is as old as
     the walk, and macOS may evict a file while the files before it are
     hashed. A file that has gone to the cloud since is reported as a
     cloud-only file (`official_files` lists it as `dataless`,
     `proposals.manifest` gives `{sha256: None, size, dataless: True}`) and
     is never opened (11.1, 12.2). A file whose fingerprint is kept costs no
     second `lstat`. The record files and the cards' version files are added
     as before. Only regular files are looked at. `official_files` keeps the
     files `snapshot_rels` keeps, sorted, the cloud-only ones apart;
     `proposals.manifest` keeps the files of `project_files` in that
     function's order, a cloud-only one as `{sha256: None, size, dataless:
     True}`; a folder with no readable project identity is read as before,
     every file opened.
   - *The fingerprints.* `<home>/graphs/<id>/share-fingerprints.json` keeps
     `{path: [stamp, sha256]}`, the stamp being
     `size:mtime_ns:inode:ctime_ns`. A file is read only when its stamp is
     not the one kept, or when it had not settled when the look began. The
     status-change time (`ctime_ns`) moves with every write, `chmod` and
     `utime` and cannot be put back, so a file rewritten in place with its
     modification time restored (`touch -r`, `cp -p`, `rsync -t`) is read
     again; a file made unreadable since it was fingerprinted (a `chmod`
     moves the status-change time) is refused when it is read again, as a
     full reading refuses it (`officialness.view` says so in its `problems`,
     as it does of a file unreadable from the start). The stamp had three
     parts before: a fingerprint file written by the version before has none
     that matches, and every file is read once after the upgrade.
     *Settled* is `memo.settled`'s rule (item 7, `memo.RACY_NS`): the file's
     modification time lay at least 3 s before the look began, taken before
     its first `lstat`. A stamp is as fine as the file system keeps it (2 s
     on FAT, 1 s on HFS+, a coarse network share), and a file written again
     within that after a look could carry the stamp the look saw. So the
     fingerprint of a file that has not settled is neither kept (in the file
     or in memory) nor trusted: the file is read by every look until its
     modification time is 3 s old, then read once more and kept (a file dated
     in the future never settles and is read by every look). Both looks
     leave the same map: every settled file seen (not in the cloud) whose
     fingerprint is known, computed now or kept and still at its stamp, so a
     deleted or changed file drops out and the two looks do not take turns
     rewriting the file. It is written, atomically, only when that map is
     not the one the file held; nothing is written when nothing changed. The
     map is also kept in memory by the file's own size, modification time and
     inode (8 files, 100,000 entries in all): a file replaced, deleted or
     rewritten by another process is read again. An entry is its own proof (a
     stamp is what a cache trusts, here as before).
   - *Publishing and approving.* `_publish_locked` builds the last
     snapshot's file map once; approving publishes through it. A snapshot
     builds its `files` once, so the other readers (`sync`, `join`, the
     preview, the status) ask a dictionary.
   - *The snapshot id.* `snapshot_id(manifest(root))`, which the PI's
     reading of a proposal (`what_changes`, hence `open_for` and `approve`)
     compares with the proposal's base, and the `manifest` a member's
     `make_proposal` and `start_working_copy` take, no longer open every file:
     they read the fingerprint file above and open the files whose stamp
     moved or that had not settled. The ids are the ones the old reading
     gave, `moved` and a proposal's `work` included: a file rewritten within
     the tick of the volume's clock, or with its time put back, is read, not
     taken from the file.
5. **File preview and drafting** read at most 200 KB and one byte, and take
   the size from the open file.

   How it is read:
   - *The read.* `cloud.read_head(path, limit)` makes the cloud check of
     `read_bytes` (11.1), opens the file once, unbuffered, takes its size
     from the open file (`os.fstat`) and reads up to `limit` bytes, however
     large the file is. `file_payload` asks for the 200 KB limit and one byte
     more: that byte is what says `truncated`, and `size` is the open file's.
     Everything else is as it was: the 8 KB window in which a NUL byte
     refuses the file, the decoding (a cut that falls inside a multi-byte
     character is dropped), the refusals, their bodies and the payload keys.
     A `.dta` returns before any read. A draft from chosen files reads each
     through `file_payload` (`_draft_file`), so it reads the same head.
   - *A cut read that is not UTF-8* (changed 2026-10-26). A file over the
     limit used to be decoded with its bad bytes dropped, so a PDF over
     200 KB with no NUL in its first 8 KB showed as garbled text and went to
     the model in a draft. Now only the last bytes of a character the cut
     splits (at most 3) are dropped; a bad byte anywhere else refuses the
     file as one under the limit is refused, and a draft answers
     `draft_file_not_text`.
   - *What it costs.* For a 64 MB file, and for a 1 GB file, whose first
     300 KB is text, Python's memory peaks at about 600 KB (the 200 KB read,
     its cut and its decoded text); the whole read held the file.
6. **The change history behind 研究路线** uses a time index and reads only
   the window it shows.

   How it is read:
   - *What the desk asks.* For each card, when its row or one of its files
     last changed (`changed_at`, `changed`, `changed_more`). That needs the
     newest version of each node the cards show, never the whole history.
     `desk._change_times(root, id, nodes)` is given those nodes (the attempts
     and the files they hold, 14.3's membership rule) and leaves the other
     nodes of the store unread; a node outside `nodes` may or may not be in
     its answer, and the desk never asks for one. The page sends no time of
     its own (it compares `changed_at` with its last visit), so what the
     window holds is bounded by each node's newest version, not by a date.
   - *The indexes.* `history.Store` makes, once for a store that lacks them,
     `idx_versions_at` on `julianday(at)` and `idx_versions_node_at` on
     `(node, julianday(at))`, in the version store beside the index
     (`<home>/graphs/<id>/history/`), nothing in the project. SQLite reads an
     `at` as an instant in UTC, so versions recorded either side of a change
     of clocks, or at different offsets, are ordered by the instant, not by
     their text. A store that cannot be written (read only, or held by a
     writer for more than 250 ms) is opened as it is, without them, and is
     read in full as before. SQLite refuses to index the word `now`: a store
     that holds one goes without the indexes, and none can be added; Research
     OS writes real times only.
   - *The window.* `Store.time_readable` says the indexes can answer: both
     exist, SQLite reads every `at` as a time (no `julianday` of NULL), and
     none lies before 1970 or after 2199 (so none is a number or a word taken
     for a time). `Store.earliest_times` gives the `at` of every version within
     2 days (`TIME_MARGIN_DAYS`) of the earliest of the attempts' rows and of
     everything else, for the baseline. `Store.node_windows(nodes)` gives, per
     node, its number of versions, its first version's time and source, the
     kind of its newest version, the latest `julianday` it has, and the
     versions within 2 days of that. These are plain `SELECT`s over the
     indexes (one grouped read of the node's index entries, a join to the
     window, and the first and last versions by key); nothing is `DISTINCT`.
   - *The judge.* SQLite only narrows. `desk._window_times` reads each time it
     keeps with `_parse`, as `_scan_times` (the full read, kept as it was)
     does, applies the same baseline rule (`_first_is_baseline`: the first
     version is no change when a scan or publish recorded it within 10
     minutes of the earliest of its kind; the kind is the node's newest
     version's), and takes the latest. The answer is the full read's, node for
     node. SQLite reads a time with no offset as UTC and Python as local
     time, up to 14 hours apart (`OFFSET_DAYS` = 0.6 day), which is why the
     window is 2 days wide: a version outside it cannot read later than one
     inside it. The newest taken from a window is kept only when the window
     holds all the node's versions, or the newest is no earlier than
     `TIME_MARGIN_DAYS - OFFSET_DAYS` before SQLite's newest; otherwise that
     node's versions are all read (`Store.stamps`).
   - *When it does not decide.* `_window_times` answers None, and the full read
     (`_scan_times`) answers instead, when the indexes are missing, a time is
     one SQLite cannot read or reads outside 1970-2199, a version in a window
     is not a time `_parse` reads, or the newest chosen reads a day or more
     later than SQLite's. Nothing is guessed: such a store costs what it cost.
   - *What it costs.* 100,000 versions of 3,000 nodes over a year (this
     machine): the full read 350 ms; the window for all 3,000 nodes 36 ms,
     for the 300 the cards show 6 ms. The first open of a store of that size
     builds the two indexes once (about 0.1 s).
7. **Smaller:**
   - `/api/summary` counts in SQL.
   - `GET /api/agents` remembers a program's version while its path, size
     and modification time are unchanged.
   - 项目首页 takes `/api/me` and `/api/share` from the page's shared
     context.
   - `/api/stata` remembers each script's reading by path, size and
     modification time.
   - The one `t()` call made while the page loads is made when it is drawn.

   How it is read:
   - *The counts.* `db.count_nodes_by_type` and `db.count_edges_by_type` are
     `SELECT type, COUNT(*) … GROUP BY type`; `summary_payload` takes every
     type of `NODE_TYPES` and `EDGE_TYPES` from them, in sorted order and
     with a zero for a type that has none, which is the payload the old
     count of loaded rows gave (the index's CHECK keeps a row to the known
     types). `pending` still reads the pending edges, `review` as before.
     On 12,000 nodes and 18,000 edges the counting took 143 ms and now
     takes 3 ms.
   - *The versions.* `find` still reads `agent_commands` and looks at the
     program (found on the widened `PATH`, or the saved path there and
     executable) on every call; only the `--version` answer is remembered.
     `profiles._VERSIONS` is a `Memo` of 32 entries, asked with the path as
     `find` resolved it, the file's device, inode, size and modification time
     in ns (`memo.stamp`, the key of the team memories, 4) and the `PATH` the
     program is started with. An answer is kept only when it is a version
     (a program that failed, said nothing or timed out gave no answer and is
     asked again at the next request), the modification time lay at least
     `memo.RACY_NS` (3 s, as in 2) before the program was started, and the
     stamp was the same after it ran. `set_commands` clears the memory, so
     saving locations asks afresh. The payload is the one every request
     starting the programs gave. Not seen: a program rewritten in place with
     its time set back, and a program whose answer changes with something
     other than its file and its `PATH` (an interpreter upgraded under it).
   - *The shared context.* `desk.js` already reads `/api/me`, `/api/proposals`
     and, for a team, `/api/share` once per project at a time
     (`ensureContext`, `loadContext`: one read in flight per data epoch).
     `workspace.js` asks `RODesk.context()` for who I am and the team and
     copies `me` and `share` out of it, but only when the context is already
     there: filled (`mode` set), made in this data epoch and for this
     project. It never waits for a load that is still going and never starts
     one: `/api/proposals` and `/api/share` read the shared folder with no
     time limit, and a folder that hangs must not hold who I am. Otherwise
     it reads them itself, one after the other as it always did (who I am
     first, so the owner list offers it at once; the team alone waits for
     `/api/share`): when there is no desk module or `context()` fails, while
     a load is in flight or after one failed, when the context is not this
     epoch's, and for `/api/share` alone when the project is a PI's or a
     member's and the context did not ask for the shared folder (its role
     read otherwise). A context made while `/api/me` is read serves the
     team. The cost is that a page that loads while the sidebar's context
     read is in flight reads `/api/me` and `/api/share` once more itself;
     once the context is made the page reads neither.
   - *The scripts.* `dataflow._stata_scans(…, remember=True)`, which only
     `/api/stata` asks for, keeps for each script the `stata.Reading` of its
     text: the lexer's commands and problem and the names `st_local()` and
     `st_global()` mention, which depend on the text alone and are never
     changed. `dataflow._READINGS` is a `Memo` of at most 2,000 scripts and
     120,000 commands, by the path and `memo.stamp` of the file. A request
     checks the cloud (11.1) for every script as before, looks at the file
     once, and takes the reading kept when the stamp is the same and the file
     can still be opened for reading; otherwise it reads and lexes the file,
     and keeps the reading if the file had settled (`memo.RACY_NS`) and its
     stamp was the same after the read. `_File` is made from the text or from
     the reading, with the same warnings; the project pass (macros, programs,
     runs, the calls) is made on every request, since it needs every file.
     The ingest that writes the index asks without `remember` and reads every
     script as before. On 120 scripts of 36,000 commands the request took
     797 ms with nothing remembered and 444 ms with it; one edited script
     adds its own reading.
   - *The text.* app.html's `NO_PROJECT_NAME` constant is the function
     `noProjectName()`: the name is looked up in the language in force when
     the project button is drawn (13.6), and no script makes a constant of a
     `t()` call while it loads.

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


## Codex runner and concrete model versions (2026-10-23)

Codex is a third runner under the existing task/run/report/undo contract.
It uses `codex exec --json --model <full ID>` and resumes with the recorded
thread ID, never the latest unrelated session. The selected model ID is
stored unchanged. Only a model reported by the program is recorded as the
answering model; token usage is not converted into an invented dollar cost.

Use workspace-write, no interactive escalation, ignore user configuration
and execpolicy rules, and supply a required MCP server limited to the six
agent tools. Only those tools are preapproved. MCP writes still pass through
the existing run-bound validation. No Research OS PreToolUse hook is claimed
for Codex: protected changes are identified by the before/after comparison.
The `.codex` folder joins the protected agent settings folders.

Explicit executable paths win. Otherwise prefer a working CLI bundled with
the macOS desktop app, then widened PATH. `codex debug models` supplies the
model catalog, with hidden entries removed; failures offer manual entry,
not a fabricated list. The UI keeps catalogs separate by runner, displays
complete IDs in a select, and permits full IDs and snapshots as text.
Availability is determined by the account and login method. GPT-5.5 is no
longer available with ChatGPT authentication; it remains an API option.

Verified with bundled Codex CLI 0.162.0-alpha.17.2: a fictional project
asked a question, resumed the same thread, wrote a file, completed through
MCP, and undid both the file and completion mark. Unsupported older CLI
flags fail visibly; no weaker execution fallback is used.

References: [noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[MCP configuration](https://learn.chatgpt.com/docs/extend/mcp),
[model availability](https://learn.chatgpt.com/docs/models).


### Claude model-version picker (2026-10-23)

Claude Code's suggestions now contain documented full version IDs rather
than rolling `sonnet` / `opus` / `haiku` / `fable` aliases. The picker labels
them with a readable family and version, while saving and executing the
unchanged full ID. Older 4.5 entries include their published snapshot date.
Existing tasks and saved agents using aliases remain valid; no migration or
automatic substitution occurs. Custom model IDs remain available. This is
a documented suggestion list, not a live account entitlement check, and
requires no API key or model inference to display.

Source: [Claude Code model configuration](https://support.claude.com/en/articles/11940350-claude-code-model-configuration).


## Section 19 — Where an agent's files go, and how the app knows them (task V15)

The researcher, 2026-10-25: "文件夹纪律：AI Agent 执行 Sessions 的文件，和输出结果的文件，
没有很好的管理", and "AI 输出的结果，也没有登记到 APP 对应的位置。看上去很乱没有条理".
This section amends 17.4–17.6 and the output routing that commit 17a1527 added without a
design entry (its `research/folders.json` and `research/results/<report>.outputs.json` are
described here for the first time and stay as they are unless said otherwise).

### 19.0 What was found (2026-10-25, three real projects, read only)

- **Process files had no place.** The brief named seven kinds of final output and said
  only "intermediate caches are not final outputs". Claude Code, Codex and DeepSeek Harness
  each invented the same answer: a folder `research/<topic>_<date>/` holding `scripts/`,
  `checks/`, `data/`, `figures/`, `sources/` and a report. Quant has 11 of them (326 MB,
  ~460 files) beside the app's own notes and result records; the code that fetches data and
  runs the backtests (~8,700 lines) lives in them, not in `src/`.
- **Three texts per task.** A result record (`research/results/<32 hex>.md`), the agent's own
  long report, and a note titled 「…要点与文件位置」 restating where the files are.
- **Almost nothing registered.** 25 of 26 runs predate the routing; Quant's agents created
  583 files, 15 are registered. Registration is the agent's own declaration and is never
  compared with what the run created; a person cannot register a file; registered files are
  split over 图表与论断 and 数据与代码 with nothing saying which task produced them; the
  agents' reports were read as paper drafts (~855 claims).
- **The run store is invisible and unbounded.** `~/.researchos/agent-runs` holds 4.3 GB:
  every run copies up to 1 GiB of the project without deduplication (93% of 论文重建's three
  copies are the same bytes), 20 copies are kept per project, an undone run keeps its copy,
  files moved away by an undo are kept with no limit and deleted silently at the 200-run
  rule, and nothing in the app shows any of it.
- Two defects: 「打开结果」 on 图表与论断 and 数据与代码 opens the record in the hidden
  project home, so nothing seems to happen; 14 of Quant's 15 agent tasks lost their done
  mark after completion. The only writer that does that is the row's checkbox (history shows
  page saves 50 s to 6 h after each completion); the researcher did not mean to.

### 19.1 Decided by the researcher (2026-10-25)

1. **Outputs stay sorted by kind** (17a1527's model), not one folder per task. A new
   location, the **work folder** (过程文件), takes everything else a run writes. The app, not
   the folder tree, ties a task's files together.
2. **Earlier outputs are registered where they are**, never moved, after the person has
   reviewed the proposed list.
3. **The run store is deduplicated, capped at 5 GB per project, and visible**, with the
   undo backups the only thing ever removed automatically. Files an undo moved away are
   removed only by the person.
4. The lost done marks are treated as a defect.
5. The work is done in increments (19.10), each verified and committed on its own; the
   size budget is waived for this task.

### 19.2 Places

- **Kinds.** `research/folders.json` gains an eighth role, `work`, default `work` (candidates:
  `work`, `agent-work`). A saved version-1 file without `work` reads as if it named the default; saving
  writes all eight. The 输出目录 panel shows the work folder last, with the words
  「过程文件：只为完成这次任务而写的脚本、自检输出、下载的源码和中间文件。不进索引，不算输出。」
- **The work location stands apart** (amended after step 2's review): it may not equal,
  contain or lie inside another role's location, and may not lie under `research/`. Saving
  such a choice is refused; an unsaved default that would clash, or a candidate that is a
  file or a link, moves on to the next candidate, and when none is usable the settings still
  read (the panel opens and says 「请为过程文件选一个目录。」 / "Choose a folder for process
  files." under the work field, until a saved choice clears it) while a run refuses to start
  with that message instead of failing inside. That refusal, and one for output settings that
  cannot be read, is an answer the person can act on (409), never a server error; the Chinese
  sentence of the second names `research/folders.json` and, where known, the reason (invalid
  JSON, a version nobody knows, a location that is a file, a work location that crowds
  another), as the English does.
- **A run's work folder** is `<work>/<YYYYMMDD>-<slug>/`: the date the task's first run
  started (local time) and the task's first line, at most 24 characters, every run of
  characters other than letters, digits, CJK, `-` and `_` turned into one `-`, repeated `-`
  made one, trimmed of `-`. Every later run of the same task (an answer, a sending back, a new start) uses the
  same folder, recorded in `run.json` as `work_folder`. A name taken by another task's folder
  gets `-2`, `-3`. Research OS does not create the folder; the agent does when it needs it.
- **The same slug names the task's subfolders** in the output folders, so one task reads
  as one name across kinds (`src/<slug>/`, `figures/<slug>/`, `data/derived/<slug>/`).
- **Outputs versus process.** An output is a file the person will read or use again: a
  figure, a table, a dataset meant for further work, a document, a reference export, a
  variable draft, and the code needed to reproduce any of these. Everything else the run
  writes — one-off probe and check scripts, their stdout, verification JSON, downloaded
  third-party sources, scratch data, caches — is process and goes to the work folder.
  **Table versus data:** a table is a result meant to be read (summary statistics, a
  regression, an attribution); data is a dataset meant as input to further code.
- **Nothing new under `research/`.** Only Research OS's tools write in `research/` (notes,
  result records, tasks, catalogs, variable drafts when `variable` points there).
- **Folder matching is by the file system's own case rule.** On a case-insensitive volume a
  declared `Data/x.csv` matches the location `data`; the check compares the resolved real
  paths, not the strings.
- **Work files are not protected data.** A data file inside any run's work folder that
  existed before a run is not on the protected list (17.5 rule 3) and is not named in the
  brief: a later run may overwrite its own task's process files.

### 19.3 What the agent is told (amends 17.4)

The routing block of the brief becomes, in both languages:

- the eight locations, then "this task's work folder is `<work folder>`; this task's
  subfolder name is `<slug>`";
- where process files go (19.2) and that nothing is created under `research/`;
- one deliverable: the result record is the task's report. When the task asks for a
  document (a plan, a review, a write-up), the document goes to the document folder as an
  output and the result record stays short — the conclusion, the key numbers, what to read
  first. A note is only for something the person asked to keep apart from the result; never
  a list of files (the record's 输出文件 list is written by Research OS);
- the table/data rule and readable file names (words, not hashes or run ids);
- every output in `outputs` as now.

The words are fixed by tests for every runner, as now.

### 19.4 After the run: filing

When the after-picture is taken (17.5), Research OS sorts every file the run created into
one class and keeps the result in `run.json` as `filing`: `record` (written by its own
tools), `output` (in this task's catalog), `work` (inside the run's work folder), `cache`
(`__pycache__`, `.pyc`, `.pytest_cache`, files 17.5 already does not count) and
`unregistered` (the rest; at most 500 paths kept, then a count). Filing changes no state and
is not out of bounds; it is what the task row shows (19.6). The runs listing the page polls
carries the counts only; the paths are in the run's detail. A run from before V15 has no
`filing`; the page treats all its created files as unregistered until 19.5 registers them.

### 19.5 A person registers files

- `POST /api/outputs/register` `{project, report, items: [{kind, path, title?}]}` and
  `POST /api/outputs/unregister` `{project, report, path}`: a human write (`write_guard
  human=True`, the single person or the PI in the official project, a member in their working
  copy). The task must have a result record (`report`); the items join its catalog. The
  checks are 17a1527's (visible path, exists, readable locally, the kind's suffixes,
  bibliography and variable contents) except the folder rule: **a person may register a file
  wherever it is**. Kind `work` accepts any suffix. A record file, anything under
  `research/notes/` or `research/results/`, and `.researchos/` are refused. The entry's
  `evidence` is `person`, with `who`; an agent's entries keep `agent_declaration`. In a team,
  a member's changed catalog (and the files it newly names, or names with another fingerprint
  than the official catalog's entry of the same path) is offered in 提交改动 like a changed
  task's result record, even when the task itself did not change (18.3). The official catalog
  is another person's file: an entry of it whose fingerprint is no mapping names no content (its
  path is offered), and a copy that does not read names no entry (every file the member's names
  is offered); neither stops the project home from loading.
  **How many.** A person's call may name up to 1,000 files, a catalog's own count (an agent's
  declaration stays at 200), so the review list registers a task's files in one call. A catalog
  is a record and a record is at most 200 KB: with long paths it holds fewer than 1,000
  entries. A call that would not fit is refused whole, nothing written, in a sentence that
  says to register fewer or remove registrations first; the review list shows it in the task's
  group and keeps the choices.
- **Earlier runs.** `GET /api/outputs/backfill?project=` lists, for every run on this Mac
  that is not undone and whose task still exists with a result record, the files it created
  that are still there, not records, not caches, not inside that run's own work folder (19.4
  files them as `work`) and not yet registered, each with a suggested
  kind, first rule that matches:
  1. a path segment `checks`, `check`, `logs`, `log`, `tmp`, `scratch`, `cache`, `sources`,
     `vendor`, or a name `*_stdout.txt`, `*_stderr.txt`, `*.log`, `report_check.*`,
     `summary_check.*`, `pytest*.xml`, `*sha256*.json` — `work`;
  2. an image suffix, or `.pdf` under a `figures`, `figure`, `plots` or `fig` segment — `figure`;
  3. `.bib` — `literature`; `.toml` under a `variables` segment — `variable`;
  4. a code suffix (17a1527's list) — `code`;
  5. `.csv` / `.tsv` under a `tables` segment — `table`;
  6. a data suffix — `data`;
  7. `.md`, `.docx`, `.tex`, `.html`, `.txt`, `.pdf` — `document`;
  8. otherwise — not registered.
  Nothing is written by the listing. The page shows it grouped by task (task text, run date,
  agent), each file with its kind (a select that also offers 不登记) and a checkbox; 「登记所选」
  registers through the endpoint above, one catalog per task. A refusal names the file it
  is about in both languages (a reference export with no readable reference, a `.toml` that
  is no variable definition, a file changed or gone since the listing), so the person knows
  which box to clear.
- The 输出目录 panel offers 「补登记以往运行的文件…」 when the listing is not empty.

### 19.6 The page (amends 17.9's row, 17a1527's views)

- **The task row's 任务输出.** Under the run controls of a task with a result record:
  the registered files grouped by kind (kind word, count, each file by title, opening in the
  shared file panel; figures as small previews), each with 「移除登记」; then, from the latest
  run's `filing`, 「N 个新文件未登记」 with 「登记…」 (the 19.5 list for that run alone), and the
  work folder's path with its file count. Collapsed with the task's details (a3d7ccc).
  A registration whose file is gone stays listed in the row, marked 「文件已不在」 ("File no
  longer there"), with 「移除登记」, and does not count as registered for a file that later
  appears at that path: a run creates only a file that was not there when it began, so for the
  files a run created, an entry made before the run began (an agent's entry: the time its run id
  carries; a person's: `at`; the run's own declaration always counts) registered another file.
  This is judged to the second, and an entry with no time is judged by its file alone. The views
  leave a gone file out as before. A file that is there but cannot be read on this Mac (only
  in the cloud) is not gone: its row says 「文件只在云端」 ("The file is only in the cloud"),
  offers 「移除登记」, and it counts as registered; the views leave it out as they do today. A
  catalog entry whose `at` cannot be read as a date falls back to its run id's time, and one
  bad entry never empties a view: each catalog is read on its own and its entries one by one,
  so a catalog that cannot be read, an entry without a fingerprint or one with a hidden path
  costs only itself; the error is still said (the views' error line, naming the catalog) and
  the other catalogs' rows show. The row's data does the same per task, and is computed once
  per change of the project's files, not on every poll.
- **The views.** Every registered row on 图表与论断, 数据与代码 and 文献 says which task
  produced it (the task's first line, cut as the record title is) and when. 「打开结果」 there
  switches to 项目首页 and opens the record.
- **Result records link their files.** In a record's preview, an inline code span whose
  whole text is an existing project-relative file path becomes a link to that file, and a
  relative link that does not resolve from the record's folder but does from the project
  root opens that file. The file is never rewritten.
- **The done mark.** Clearing the done mark of a task that has a result record asks first:
  「这项任务已经有结果。改回未完成后可以再次运行，再次运行会改写同一份结果记录。改回未完成吗？」.
  Ticking never asks.

### 19.7 The index

The scan skips the work folder (as configured, else its default) like a noise folder, and
skips files registered as `work`. A file registered as `document` that a run created, and
result records as before, are not paper drafts: no claims are drawn from them. An entry
says so with `created: true`: an agent's declaration of a path absent from its run's
before-picture, or a file a person registers from a run's created files (19.5). A document
that existed before the run — the researcher's own draft that an agent edited and declared —
stays a paper draft. The lineage report skips the work folder as the scan does. The catalogs and
`research/folders.json` travel with the project, so every member's index agrees.

### 19.8 The run store (amends 17.6)

- **One copy of each content.** Each project's run folder has `blobs/<aa>/<sha256>` (read
  only). A run's `before/<path>` is a hard link to its blob; a file whose stamp matches the
  previous picture (`_same_stamp`) is linked from the blob without being read again. Only
  newly stored bytes count toward the 1 GiB a run may copy. Undo already writes the bytes back
  and checks the hash (17.6), so a shared blob is never changed by it.
- **An undone run releases its backup** (`before/`, `before.json`, `after.json`): it cannot be
  undone twice.
- **5 GB per project.** `settings.json` `agent_store_limit_gb` (default 5, 1–100) bounds the
  project's store: blobs counted once, files moved away by undo, and logs. After every run and
  when the space panel opens, while the store is over the limit the oldest run still holding
  a backup (never an active one) releases it and becomes not revertible. Blobs no hard link
  uses any more are deleted. Files moved away by undo (`reverted/`) are never removed
  automatically: past the 200-run rule a run that holds them keeps `run.json` and
  `reverted/`. When they alone pass the limit, the panel says so.
- **Existing stores** are converted on the project's first clean-up after the upgrade: every
  `before/` file is moved into the blobs (identical content once) and linked back; undone runs
  release their backups.
- **The space panel** (「运行占用的空间」, in the AI agent area): for the open project, the
  sizes of undo backups, files moved away by undo, and logs, against the limit; the runs
  holding moved-away files (task text, or 「任务已移除」, date, size, count) with 「删除这些文件」
  (asks first); and 「释放所有撤回备份」 (asks first; active runs keep theirs).
- **The agent programs' own session records** (Claude Code in `~/.claude/projects`, DeepSeek
  Harness in `~/.dsh/sessions`, Codex in `~/.codex/sessions`) belong to those programs.
  Research OS never reads, moves or deletes them; the run log names the session id.
- **As built (task V15 step 5).** What the text above left to the build:
  - *Blobs.* A copy is made through a temporary name inside `blobs/` (hashed while it is
    written) that becomes the blob when the content has none; if the file system will not
    link, that file gets the plain copy of 17.6. The picture's entry of a copied file carries
    its `mode`, and undo puts the mode and the modification time back from the entry (a blob
    is read only, shared, and has the times of whichever file first stored it); an entry
    without a `mode` (a store from before the blobs) keeps the mode of its own copy. `blobs/`
    and its folders are 0o700 like the run folders. `st_nlink == 1` is the test of an unused blob,
    and a temporary name older than a day is a dead run's leftover.
  - *One lock.* Conversion, release and deletion of blobs take one lock per project;
    a run taking its picture does not (it may read for minutes) and is tolerant of a blob
    deleted under it. An undo holds the same lock while it reads the backup, so a release waits
    for it instead of refusing it.
  - *The limit* is whole gigabytes of 1,073,741,824 bytes, each file counted once whatever its
    links. The 20-run rule of 17.6 stays beside it: a run beyond the newest 20 loses its backup
    too. A run that is going, and one waiting for its changes to be compared (`check_pending`),
    keep theirs; when only those and `reverted/` are left the store may stay over the limit (the
    panel's total shows it, and says so in words when the moved-away files alone pass it). The limit is not kept against a store that has not been
    converted yet (its copies are not shared, so it would release backups the conversion is
    about to make small): the first clean-up or panel read starts the conversion, and the
    limit is kept the moment it ends.
  - *Conversion* is one pass in a thread of the engine, started by a run's first clean-up after
    the upgrade and by the space panel when a request with the session reads it (a read
    without the session starts nothing and writes nothing). It records each copied file's
    `mode` in `before.json` first, then links file by file (a replacement is atomic, so an
    interruption leaves a store that undoes), skips runs that are going, releases the undone
    runs' backups, and writes `agent-runs/<project id>/store.json` when it looked at every run.
  - *The panel's sizes* are three: undo backups (blobs, and any copy that is no link of one),
    files moved away (`reverted/`), run logs (everything else: logs and pictures). While the
    conversion goes the panel says 「正在整理撤回备份…」 and shows no sizes, and asks again every
    3 seconds. `GET /api/agents/store` answers it (`?project=` is the project's id or its path);
    `POST /api/agents/store/release` and `/delete-moved` are a person's acts, refused without the
    session, for the served project only, and answer the panel as it stands.
  - *The run log* names the session id: `GET /api/agents/runs/<id>` carries `session_id`
    and the log shows it as 会话编号.

### 19.9 Language

Added to 13.4: 过程文件 "Process files"; 过程文件夹 (a run's work folder; 19.12 item 1) "Process folder";
登记 "Register"; 未登记 "Not registered"; 移除登记 "Remove registration"; 补登记以往运行的文件
"Register files from earlier runs"; 登记所选 "Register selected"; 不登记 "Do not register";
运行占用的空间 "Space used by runs"; 撤回备份 "Undo backups"; 撤回时移走的文件 "Files moved
away by undo"; 释放所有撤回备份 "Release all undo backups"; 删除这些文件 "Delete these
files"; 任务已移除 "Task removed"; 空间上限 "Space limit"; 会话编号 "Session ID". 任务输出 stays
"Task outputs". No arrows or emoji (12.11).

### 19.10 Increments and acceptance

1. **Defects.** 「打开结果」 from the views switches to 项目首页; the done-mark question.
   Accepted by page tests for both.
2. **Filing new runs.** The `work` role and compatibility of saved settings, the run's work
   folder and slug, the brief (19.3), filing (19.4), case rule and work files unprotected
   (19.2), the index (19.7). Accepted by unit tests and a brief-text test per runner.
3. **The page.** 任务输出 in the row, task and date on the views' rows, record links (19.6),
   the person's register/unregister endpoints. Accepted by page and API tests.
4. **Earlier runs.** The backfill listing, its rules and review list (19.5). Accepted by tests
   over a fixture shaped like Quant's dated folders.
5. **The store.** Blobs, release on undo, the limit, conversion, the panel (19.8). Accepted by
   tests that run two runs over the same project and find one stored copy, and by an undo
   after deduplication that restores byte for byte.
6. **On a copy of Quant** (never the real project or the real home): the listing proposes the
   11 dated folders' files with the kinds of 19.5; registering them fills the views with task
   labels; a short Claude Code run writes its probe into `work/…`, registers its figure and
   leaves 0 unregistered files; the store of the copy's runs shrinks and undo still restores.

### 19.12 After acceptance on a copy of Quant (2026-10-26)

Found by registering the copy's 491 earlier files and by a real Claude Code run on it, and
binding like the rest of this section:

1. **One word per thing.** 工作文件夹 already means a script's working directory (15.3:
   「工作文件夹无法确定」) and, in the brief, the project folder. A run's work folder is
   therefore 「过程文件夹」 "Process folder" everywhere it is shown or told (the brief's
   「本任务的过程文件夹是 …」, the task row, 19.9's word), and the brief's own sentences about the
   working directory say 项目文件夹 / "project folder". 15.3's words stay.
2. **The task row stays short.** In 任务输出 the 过程文件 group shows only its count until
   opened; any other group of more than 8 files shows the first 8 and 「另外 N 个」 ("N more")
   to open the rest.
3. **The views group by task.** On 图表与论断, 数据与代码, 文献 and 变量 the registered rows
   stand under one heading per task: the title of its result record (the record's first
   heading), else the task's first line cut as before, with the date of the run that produced
   the files; 「打开结果」 is on the heading, once. A row no longer repeats the task.
   (*Amended by 21.5, B3-2*: 图表与论断 and 数据与代码 draw no such block; a registered
   file is a row there, saying its attempt. 文献 and 变量 keep theirs.)
4. **A file's date is its run's.** An entry a person registers from a run's created files
   (19.5, the earlier-runs list) also records that `run`; every date shown for an entry with a
   run is that run's start (its id), else `at`.
5. **Compressed data and tables.** A data or table suffix followed by one of `.gz`, `.bz2`,
   `.xz`, `.zst` (`prices_long.csv.gz`) is that kind for registration and for 19.5's rules 5
   and 6. Previews stay for plain `.csv` and `.tsv`.
6. **The record has one list of outputs.** Research OS's appended section is
   「已登记的输出文件」 "Registered output files"; the brief tells the agent to name files where
   they matter in its report but not to add a list of its outputs, since Research OS appends it.
7. **A task's runs count together.** The row's 「N 个新文件未登记」 and its 「登记…」 cover every
   run of the task on this Mac that is not undone (a question and its answer are two runs of
   one conversation), not only the latest; the earlier-runs listing takes `&task=` for that.
8. **The space panel says the consequence.** When moved-away files alone pass the limit, the
   panel adds that each new run's undo backup is then released as soon as the run ends, so new
   runs cannot be undone until moved-away files are deleted.
9. 19.9's words are held by `tests/test_glossary.py` like 17.9's.

Seen in the same run and left for its own design: the guard refused two read-only commands of
the agent because they named a protected data file beside `tee`, and beside `df>0` read as a
redirection (19.11); and Research OS's existing data rule does not protect compressed data
files (`.csv.gz`), so an agent could change or delete them unrefused.

### 19.11 Not in V15

Moving the earlier files; one folder per task (declined 2026-10-25); the guard's
whole-word match of protected file names inside commands, which refused an agent's
`rm` of a folder it had just made and led to a 126 MB duplicate (to be designed); the agent
programs' own session stores; a separate 「已验收」 mark.

## 20. Optional research context and handoff (unreleased)

Research context extends the existing result record, task, attempt form,
variable card and proposal paths. It introduces no parallel workflow or
status machine. A person or an AI may write a result; neither every task nor
every result must complete all the actions below. Existing records without
context remain valid, including named Markdown results a person created.

### 20.1 Record and associations

A result `research/results/<name>.md` may have a sibling
`<name>.context.json`, containing `version: 1` and `context`. Five optional
text fields describe the work: `question`, `method`, `finding`, `limits`,
`next_step`. `attempts` names existing attempt IDs; `variables` names explicit
`{id, version}` pairs; `references` and `materials` name project files;
`purposes` maps a linked file path to a short explanation. Missing context
stays empty. An optional `attempt_draft` contains a description, result and
parent attempt IDs, never a verdict or an adopted status.

Saves use the existing human write guard, history and optimistic revision
check. A stale revision refuses the write rather than overwriting another
editor. Associations describe what the author linked: a material link or
purpose does not prove that a run read or produced that file. Literature
links are file-level associations, not per-reference or per-DOI support
claims. Native file and variable views can show these explicit backlinks.

### 20.2 Task and AI handoff

A task may select an existing result using `context: {report: <path>}`;
`null` clears the selection. Task reads validate its shape only. Saving a
new or changed selection requires an existing result, while an older
selection whose file disappeared does not make the whole task list
unreadable or prevent unrelated edits.

An AI run freezes its selected background when it starts, including the
context revision, the first 24,000 characters of the report with its content
hash and truncation flag, explicit variable versions and their definition
file paths, and material availability and metadata. Variable definitions
also carry a read-only text snapshot and full content hash: at most 8,000
characters each and 24,000 in total, with explicit truncation flags. The brief
directs the agent to read the full report and materials as needed. It uses
the pinned definitions, never substitutes the current version, and never
uses shell commands to bypass the protected records folder. An allowed
read tool may supply missing definition text; otherwise the agent asks the
researcher when that text is necessary. Without a separate selection, a task with an existing context
sidecar retains its own earlier record as background. Continuation uses
this same background. Earlier findings, limits and next steps are background, not findings of the new
run. Completion may inherit the question and explicit associations; its
method and findings describe the new work. The completion tool writes the
context with the existing report/output path and attribution; agents do not
edit sidecars directly. File relationships remain distinct from execution
records and output fingerprints. Missing files and variable-version changes
need review, not invented replacement relationships.

### 20.3 Human decisions and team review

A suggested attempt prefills the existing attempt form. Only the form's
save or a member's proposal followed by PI approval registers the attempt;
a sidecar alone creates no attempt node. Importing a registered variable
file uses the native variable draft path and never confirms it. Variable
version proposals retain their existing semantic review and confirmation.
Solo work uses the existing direct writes without team approval.

For a team submission, selecting a result or its sidecar includes the
corresponding result, context and explicitly referenced materials that
need to travel. The preview shows the carried files; unrelated unticked
registered outputs are not added merely because they share the result's
catalog. Unchanged context is carried when needed to keep the review bound
to the author's bytes. Linked variable definitions keep their separate
native approval path. PI proposal details show the research summary,
variable versions and draft attempt without turning suggestions into
accepted research decisions.

The existing proposal engine checks all conflicts and stages the bundle
before applying it. A conflict refuses the whole approval before writes.
Multiple filesystem replacements are not a transaction: interruption can
leave part of a report/context bundle placed. The existing approval journal
records those files, and approving again resumes the remaining work; no new
rollback or parallel acceptance state is introduced.

## Section 21 — The route is the main line: tasks under attempts, the handback, a home that says what is going on (task V16)

The researcher, 2026-10-29: "I think the app's UX and UI have problems; help
me sort them out." A walk through every page on copies of both projects
(Quant, run almost entirely through AI tasks; the thesis, 24 attempts kept by
hand) and five independent reviews found one root problem: **the app holds two
unconnected models of research work.** Tasks, agents, notes and result
documents live on 项目首页; attempts, research assessments, the route map and
the provenance pages know only what is written into the attempt table by
hand. Quant had 16 tasks and 30-odd notes and reports and an empty route map
that said 「年表里还没有尝试」, beside a 待办 column saying there was nothing to
handle; its figures and data were flagged 缺尝试 row after row. The
researcher's own pain, in their words: recording a research assessment, and
not being able to tell from what an agent hands back where the work has got
to.

Decided the same day:

1. **The route is the main line.** A task always belongs to an attempt.
2. **An attempt is one version or one question** ("Sword v0.2 全成员复算",
   "美股历史数据源调查"), with several tasks under it.
3. **An agent's handback may suggest a research assessment; the person
   confirms it.** This amends Section 0 and 17.5 rule 1 as 21.2 states.
4. **The first page stays 项目首页, with 「现在在做什么」 at its top.**
5. **What an agent produced is seen in its handback**; the kind pages list
   by kind, each row saying which attempt it belongs to.
6. **A research assessment is a fixed choice plus its own reason field**;
   the file keeps its present form.
7. **All five parts are done** (21.1–21.8), and the twelve words of 21.6 as
   the researcher approved them.

### 21.1 An attempt holds its tasks

- **The link.** `research/tasks.json` gains two optional fields per task:
  `attempt` (an attempt's node id, as `research_records` already names
  attempts) and `after` (another task's id, the task this one continues).
  Old tasks without `attempt` stay valid and are shown under 「未归入尝试」.
  A new task needs an attempt: saving one without it is refused with a
  sentence that offers both ways in (21.4).
- **Where tasks are written.** In an attempt's panel, 「在这条尝试下加任务」;
  on 项目首页 and in 全部任务, the task form's first field is 「属于哪条尝试」
  — an existing attempt, or 「新开一条尝试…」, which opens the new-attempt
  form and attaches the task once the row is saved. In a member's working
  copy the attempt may be the member's own unproposed row; proposing the
  attempt carries its tasks (12.4, 20.3).
- **On the route map** each card shows, under its title: its research
  assessment, 「任务 n」 with how many are unfinished, 「等你看 n」 when a
  handback is unopened, and the first line of its latest handback. Lines
  between attempts stay 源自 (Section 14); `after` orders tasks inside an
  attempt and is not drawn on the map.
- **The attempt's panel** lists its tasks in `after` order, each with its
  owner, its state in words (未开始, 运行中, 等你回答, 等你看, 已完成, 失败,
  越过了限制) and its handback.
- **What a task reads** (`attempt_tasks.state_of`), the first that holds:
  运行中 and 等你回答 while its latest run goes or asks; 越过了限制 when that
  run went out of bounds (even for a task marked done); 失败 for a task not
  marked done whose latest run failed, was interrupted or ended without a
  report -- beside an older report too; 等你看 while its handback is
  unopened; 已完成 for a task marked done, and for one that is not but has its
  `report` set while its latest run is the one that handed it back (state
  done, not undone) or the store has no run record (older versions did not
  set the done mark, and a copy of a project or another Mac has no run
  records); 未开始 for the rest, a run the person stopped included.
  *Amended (fix batch 3):* a run the person undid (撤回本次运行) has been dealt
  with: it no longer asks, fails or warns, so the task reads as if that run
  were not there -- from its done mark and its handback --, which is what 等你处理
  has always done with an undone run, so the rows and the states are one
  reading again.
  「未完成 m」 counts the tasks that are not marked done and do not read
  已完成, so a project whose tasks all handed back says 「任务 n，全部完成」.
- **Grouping the tasks a project already has** (Quant's 16, later 18). The
  page offers 「把这些任务归入尝试…」: Research OS proposes groups — tasks
  whose text or report title share a version (`Sword v0.2`), tasks that say
  they go on from the step before, or tasks that share a named subject the
  researcher already uses; each proposed group comes with a prefilled new
  attempt (description the version, or the report title of the group's
  earliest task; date from its earliest task, assessment 待定, empty result).
  The researcher keeps, renames, merges, splits or drops each group; nothing
  is written until they confirm a group, and no file moves. Tasks whose text
  is word for word the same as another's, with the same owner or at most one
  report between them, are marked 「和第 n 条一样」 so the researcher can
  remove the duplicates; the same text given to different agents who each
  handed back their own report is one question deliberately asked of several
  agents, and says so instead (「同一个问题，分别交给了 …」, no removal).

**How groups are proposed.** A small rule, so that it can be said in a
sentence and checked by eye (`src/researchos/task_groups.py`). Only tasks
with no `attempt` -- or one the attempt table no longer has, a row removed or
renumbered by hand -- are looked at, and each lands in at most one group.

1. **What is read.** A task's text (whitespace collapsed) and the title of
   the report it points to (the report's first heading); nothing else of it,
   and nothing is sent anywhere.
2. **Version groups.** The first version token — `v` and digits with optional
   `.digits` parts, in either case (`v0`, `v0.2`, `V1.2`) — that has a Latin
   word right before it (`Sword v0.2`; a word like “run” or “the” is no
   subject), in the task's text, else in its report's title, is the task's
   version. A bare version (`四模块量化系统 v0 框架`, `已有研究梳理与 v0
   路线`) is no version and makes no group. Tasks of the same version (the
   subject compared without regard to case) form a group, a group of one
   too: a version is one attempt. Its name is the version as first written
   (`Sword v0.2`) and nothing of any task's text.
3. **Continuation.** A task that rule 2 did not place and whose first 20
   characters refer to the step before (上一份, 上一步, 上一轮, 上一次, 上次,
   前一步; in English “previous”, “last step”, “the above”) joins the group
   of the nearest earlier task in the list whose text differs from its own
   and that is in a group; a task that joined this way counts as in a group
   for the ones after it. Confirming that group sets the task's `after` to
   that earlier task (when the earlier task is still in the group and the
   task has no `after` of its own), and the page says 「接在第 n 条之后」.
4. **Shared-term groups**, over the tasks left. A term is a Latin word of
   three letters or more that is no common English word, or a run of three
   to eight Chinese characters that does not begin with a function character
   (对, 请, 一, 上, 根 and 据 of 根据目前的…, which opens an instruction and
   names no subject, …) or end with one (的, 了, 等, …), taken from the first
   line of the text and the report's title. A term counts once per distinct
   text. The term in the most texts (two at least) goes first, the longer one
   when equal, the one met first when still equal; its tasks become a group
   and leave the pool, and so on until no term is shared. A term in more than
   half of eight texts or more is the project's own name and is skipped. The
   group is named by the report title of its earliest task in the list, else
   that task's first line, cut at 40 characters; 「共同点」 keeps showing the
   shared term, so the name and the term are two things.
5. **The attempt each group carries.** The next free number (the greatest
   whole number in the table plus one — “14a” counts as 14 — and one more for
   each further group, groups in the order of their dates); the date, the
   earliest of the group's tasks' dates (`due`) and the days its reports were
   written, today when it has none, written as the table writes its dates
   (`MM-DD` if any row does); the name; the research assessment 待定 (the
   project's own marker for it, else 🕒) and an empty result.
6. **Same text, marked last.** A task whose text, whitespace collapsed, is
   word for word an earlier task's is a duplicate of it when both have the
   same owner or at most one of them has a report: it is reported with that
   task (`same_as`) and its place in the list (`number`), and the page marks
   it 「和第 n 条一样」. It stays in its group: the person decides to remove
   it. The same text given to different owners who each have their own report
   is not a duplicate: each of those tasks is reported with the agents'
   names (`asked_of`) and the page says 「同一个问题，分别交给了 {agents}」
   on each, without 移除. They are kept together in one group (they are read
   as one text, so rules 2 to 4 place them together; the version, if only
   one of their report titles names one, is that one).

On Quant's 18 tasks this proposes Sword v0 (7, 8, and 9–11 after 8, the one
question given to three agents), Sword v0.2 (12, 13, 16, and 17 after 16,
and 18) and Sword v0.3 (14); the rest fall into shared-term groups named by
a report title (1 and 6, 2 and 3, 4 and 5), or stay in no group (15, the
data-source survey: it shares only 根据目前 with task 1, which is no subject).

The engine answers `GET /api/workspace/task-groups` (a read) with the tasks
(each with `asked_of`, the owners' names when the same question went to
several agents), the groups with their prefilled attempt and their `after`
(task id to the id of the task it continues, from rule 3), the tasks in no
group, the duplicates and `numbers`, the attempt numbers the table holds as
written (the page keeps a group's number while the table does not have it).
`POST /api/workspace/task-groups` takes one group
the person kept — the attempt's number, date and name, the ids of its tasks
and optionally `after` — and writes the row through the write the
new-attempt form uses (`proposals.write_directly`: the dry run, the conflict
rules, the backup, the re-ingest; a member's row lands in the working copy
and is proposed as in 12.4, the tasks travelling with it) and only then sets
`attempt` on those tasks, and `after` on those `after` names. A refused row
— a taken number, no attempt table, a task that has since gone or found an
attempt, an `after` that names a task outside the group or goes round in a
circle, a revision that moved when the page sent one — leaves
`research/tasks.json` as it was; a row written whose tasks then cannot be
moved says so and where to choose the attempt. No file moves.

The panel opened by 「把这些任务归入尝试…」 (in the task header and under
「未归入尝试」) lists the proposed groups: each can be renamed (with its
number and date), have tasks moved in or out, be merged into another or
dropped, and is written only when confirmed; the tasks in no group are
listed below with the same move. A duplicate shows 「和第 n 条一样」 and is
removed by the task list's own removal, with its question; a question given
to several agents shows 「同一个问题，分别交给了 …」 and has no removal. A
task that continues another of its group shows 「接在第 n 条之后」, and
leaves out its `after` when the earlier task is moved out of the group.
A task of several lines is shown by its first line, cut at 90 characters (the
report's title at 50), and its buttons are named by that line cut at 40, with no
line break in the name.
The numbers the groups carry follow the table: once a group is written, a
group left keeps its number while that number is still free — not in the table
(`numbers`) and not carried by another group shown — so confirming the groups
in any order leaves no gaps (on a table with no rows, Sword v0, v0.2 and v0.3
confirmed in that order are #4, #5 and #6 while the three groups shown before
them keep 1, 2 and 3). Only a group whose number is now taken moves, to the
next free one: the greatest whole number among the table's numbers and those
the other groups carry, plus one (“14a” counts as 14) — so confirming the next
group is not refused for a number the one before has just taken. A number the
person typed never changes; of two groups that carry one number, the one the
person did not type moves (the later one when neither was typed; neither when
both were, and the engine then refuses the second as it does any taken number).
Leaving the panel after a change asks first, as the other forms of the home do.
In a project with no attempt table the first group confirmed makes the table
first, as the new-attempt form does.

### 21.2 The handback

When a run ends with `researchos_complete_task`, its report, the report's
context (Section 20) and its registered outputs (Section 19) are the
**handback** (交回). It is listed in 等你处理 and on the attempt's card until
the person who started the run opens it. "Opened" is kept per person on
this Mac under the Research OS home, never in the project.

- **What the agent is asked for.** The brief requires, for a research task,
  these context fields beside Section 20's five:
  - `headline`: the answer to the task's question in one plain sentence
    (at most 200 characters);
  - `key_numbers`: at most five `{what, value, file}`, each value as written
    in the cited file;
  - `undone`: what it did not finish and why;
  - `decide`: at most three questions only the researcher can answer;
  - `suggested_assessment`: `{status, reason}`, `status` one of the
    project's assessment statuses (21.3), `reason` at most 1,000 characters.
  The agent writes no note besides its report unless the task asks for one.
- **What the person sees**, in this order: the headline; the key numbers,
  each with its file (a value that does not occur in the text of its cited
  file, by the number check of 16.1, is marked 「在所引文件里没找到这个数」;
  a value with more than six significant digits is shown to four, 21.8, the
  value as written on hover); 没做完的; 需要你决定的; the attempt it belongs to (its number and title) with
  the research assessment it has now -- what confirming replaces --; 代理建议 —
  the suggested status and reason, with
  「确认这个判断」, 「改一下」 (the chooser of 21.3, prefilled) and 「先不判断」;
  then 产出 by kind (figures as thumbnails, tables, data, code), each by its
  title with the path as a quiet second line; the report in full, folded; the
  run log. The agent and model are named once, at the top. A run that ended out
  of bounds after it handed in (18.1 item 4) is warned of first, in the words of
  its state, 越过了限制, and a task whose run did so reads that on the card and
  in the attempt's panel, not 等你看 or 已完成.
- **A handback without the fields** (a report written before them, or by an
  agent that left them out) shows, when its Section 20 context has them, the
  context's finding in the headline's place under 「发现与解释」, then 「局限与待确认」
  and 「下一步」 when present -- the names the key points editor gives them
  (20.1); these are the report's 要点 (21.6). Only when it holds none of the
  three does it say in one line 「这份交回没有附上要点。」 (This handback has no
  key points.), and never above them. A report with neither the 21.2 fields
  nor a finding, and one whose context cannot be read, is shown unfolded, so
  that the person reads something; any other is folded (one that holds limits
  or a next step but no finding is unfolded below them, with no such line).
- **Confirming** writes the attempt's research assessment through the same
  write as the panel (18.3: only the changed cell, backup, history, conflict
  check). The history entry reads 「由 张三 确认（采纳 Claude Code · sonnet 的
  建议）」, and the report's context records `assessment_confirmed: {by, at,
  status, adopted}`, where `adopted` says whether the person took the
  suggestion unchanged. A member's confirmation is a verdict-change proposal
  (12.4) whose sentence says the same. Choosing 已放弃 still asks for the
  reason and freezes the basis (14.3).
- **After it**, three ways on: 「再派一个任务」 (a new task in the same
  attempt, `after` this one, with 研究上下文 already set to this handback's
  record), 「从这条开新尝试」 (the new-attempt form prefilled from
  `attempt_draft`'s description, else from the context's `next_step` cut to
  500 characters -- the longest description the grouping writes -- 源自 this
  attempt chosen) and 「这条先到这里」.
- **What this amends.** Section 0's "humans own judgment" and 17.5 rule 1
  stand for the act: an agent still cannot write an assessment, and the
  guard is unchanged. What changes is that an agent may *propose* one, in
  its own field, always labelled as its suggestion, and that a person's
  adoption of it is recorded as such.

**How it is kept and read** (`src/researchos/handbacks.py`; the brief in
`agents/brief.py`; the context fields in `research_records.py`).

- **The fields.** `headline` (at most 200 characters), `key_numbers` (at
  most five `{what, value, file}`; `value` is text, written as in the file;
  `file` a visible project path, which need not exist -- the check says so),
  `undone`, `decide` (at most three), `suggested_assessment` `{status, reason}`
  and `assessment_confirmed` `{by, at, status, adopted}` (`by` is the member id; a
  researcher with no `me.toml` has none, and is named in the screen's language
  when it is read, 13.1) are written to the
  context file only when they hold something, so a context from before 21
  reads and saves byte for byte as it was. `status` is one of the project's
  statuses as 21.3 lists them -- the words of the file, `现行` and the rest,
  and the researcher's own from `[verdict_labels]`; an agent writing English
  may name a default status by its English name (`Pending`), and the file
  keeps the word (13.1). A status the researcher has since taken out does not
  stop them saving the rest of the record; the handback shows the suggestion
  as one the project no longer has. `assessment_confirmed` is refused on the
  agent's tool path (`researchos_complete_task` answers `Not written: ...`), and
  the editor's save keeps what is stored and refuses a change to it: the page's
  confirm path writes it, and the page's 撤销 of a confirmation takes it back
  (`unconfirm`, below); nothing else changes it.
- **The brief** asks for the fields in both languages, lists the project's
  statuses (English names in the English brief), says the suggestion is only
  a suggestion and that the agent never writes the attempt table, and that
  it writes no note besides its report unless the task asks for one. The
  guard is unchanged.
- **Opened.** `<home>/handbacks/<project id>/<one file per person>.json`, the
  file named by a hash of the member id, holds for each report the id of the
  run that was opened. A handback waits for the person who started the run
  that handed it back (a run that names nobody is whoever's Mac it is); it is
  opened for that run, so a task sent back and handed in again waits again; a
  run that was undone hands nothing back. `GET /api/workspace/handbacks` lists
  what waits for this person; `POST /api/workspace/handback/opened` marks one.
  Nothing is written in the project, and a store that cannot be read says
  everything waits, not an error. A report that is gone waits for nobody, and
  neither does one whose context holds none of the fields an agent hands back
  (`assessment_confirmed` is the person's, not one of them): it was written
  before they existed, is no handback of this version, and a project's older
  reports do not all wait on its first day. Confirming such a report, or taking
  that back, leaves it so: it does not begin to wait (等你看) for the person who
  has just acted on it, nor for the one whose run it was.
- **The number check** is 16.1's (`semantic/draft.py`): every number in the
  value occurs in the text of its file as written or as a value (0.10 is 0.1;
  a comma is read both as a thousands and a field separator); a value with
  no number must occur as written, case and spacing aside. `missing` is
  marked 「在所引文件里没找到这个数」; a file that is not there, only in the
  cloud, over 200 KB, not UTF-8 or a link cannot be checked and the line says
  so. The check says what the text holds, not that the number is right.
- **Confirming** (`POST /api/workspace/handback/confirm`) checks everything
  first -- who may write, the task and its attempt, the status, the context
  the record will go into -- and only then writes the cell (`proposals.
  write_directly`: for the PI and a single researcher an abandonment needs
  its reason, 14.3), the history note and `assessment_confirmed`. The page
  sends the cell it showed (`was`); a row that no longer reads that way is
  refused (18.3's conflict check), the page reads the attempt again and the
  person confirms against what it holds now. A task whose latest run is still
  going or waits for an answer is refused: that run's completion is checked
  against the context it was started with, and the confirmation would make it
  fail for good. A cell that already holds that status and reason -- whatever
  its marker, spacing or line breaks -- is not written again; the confirmation
  is recorded all the same; when the status is the one the cell has, its own
  marker is written again (21.3). The note is stored in Chinese and said in the
  request's language when it is read. A confirmation checks only the shape of
  the record it adds, not the links the record already holds (a file or an
  attempt removed since does not stop it). A member's abandonment is
  proposed, and the proposal needs the route's result as well as the reason:
  the page asks for the result when the row has none, and the engine refuses
  before writing the working copy when it is missing. The suggestion's status
  and reason, space aside, are what `adopted` compares against. A suggestion
  that abandons the route opens the chooser with the agent's words offered as
  the reason, and asks for it.
- **A member's proposal.** A confirmation made by a member is proposed as the
  panel proposes a change of the same row (12.4): a row of the official table
  as a verdict change, with the report's context named so that the record of
  who confirmed it, and whether they took the suggestion unchanged, travels
  with it; a row of their own that the official version does not have yet as a
  new attempt, replacing the proposal still waiting for it (once that is
  approved there is nothing to change until they sync). The sentence is the
  stored Chinese one on either screen (13.1): it goes into a file on the shared
  drive.
- **The page.** The handback is read in the side panel, from 项目首页's task
  row (`打开结果` on a task whose latest run handed its report back; a report
  written by hand still opens to write in), the attempt's panel (`打开交回`,
  with 返回 to the attempt) and the route map's card (the handback line or
  「等你看 n」; the rest of the card opens the attempt). *Amended (fix batch 3):*
  reading or confirming a handback on 项目首页 reloads only the home, never the
  desk's reading the attempt's panel is drawn from, so 返回 goes through
  `openAttemptAnywhere`, which reads the desk again when the desk is not the page
  on screen: the task that was 等你看 reads 已完成 and the chip is the new
  assessment, as the 研究判断 form under it already was. On 研究路线 the act
  reads the desk itself and 返回 draws at once. A registered output
  row of a run opens its record as a handback too; `编辑报告` in the handback
  goes to the writing area. The outputs are headed 「输出」 (13.4), not the
  retired 产出. Without a suggestion the person writes the assessment in the
  chooser, prefilled with the one the attempt has. The chooser here is
  21.3's. The line that says the assessment was written has 撤销 (18.3) for the
  PI and a single researcher, as the panel's save has it: the cell goes back as
  the file held it, while the row still reads as this write left it. It is not
  offered for an abandonment, whose basis was frozen, nor for a member's write,
  which is proposed. 撤销 takes the confirmation back with the cell: the report's
  context loses `assessment_confirmed`, the version 撤销 wrote in the attempt's
  history carries the note 「撤销了确认」 (stored in Chinese, said in the
  request's language: Withdrew the confirmation), and the handback offers 「确认
  这个判断」, 「改一下」 and 「先不判断」 again -- also when it is opened later --
  under the line 「已撤销，研究判断回到了 {status}」. Only when the record cannot
  be taken back (a later run of the task is going, the file cannot be written)
  does the line say 「报告里仍记着你确认过」, with the reason.
- **Taking a confirmation back** (`POST /api/workspace/handback/unconfirm`
  {report}, `handbacks.unconfirm`) comes after 撤销 has put the cell back
  through the panel's write, so a 撤销 that cannot (the row moved since, the
  route would be abandoned again) takes nothing back. It is for the PI and a
  single researcher, and for the confirmation of the person asking
  (`assessment_confirmed.by`); it is refused while a later run of the task is
  going (as a confirmation is) and when the report holds no confirmation. It
  removes `assessment_confirmed` through the human context write
  (`research_records.unconfirm_assessment`: guard, backup, history; the rest of
  the context byte for byte) and puts the note on the newest version of the
  attempt when that is the scan 撤销's write caused and has no note of its own;
  when the history cannot take it (`history_error`) the confirmation is taken
  back all the same. The two requests are the page's, not one write, so the
  second is sent whenever the cell was put back, also when the panel was closed
  or another opened while the cell was being written; only the drawing depends
  on the panel still being the one shown. A panel opened later then offers the
  three buttons, not a confirmation the cell no longer says.

### 21.3 A research assessment is a choice and a reason

- In the panel, the node page's 编辑这一行 and the new-attempt form, the
  assessment is a **status chooser** — the default statuses (现行, 待定, 不利
  证据, 已放弃, 被否, 归档, 其它) and any the researcher named in
  `[verdict_labels]` — showing which one is chosen, and a **reason** field
  holding the researcher's words.
- The cell is written as today: the status's marker, a space, the reason
  (`🕒 活跃候选(全项目最新文件)`). Reading splits it by `verdict_status`; a
  cell with no known marker reads as 其它 with the whole text as the reason
  and is written back unchanged unless the person picks a status. The raw
  marker never shows in the field (12.11).
- The new-attempt form numbers the row itself (the next free number, which
  the person may change), drops 「日期（日期）」 for 「日期」, keeps the local
  model's draft (Section 16) folded below the main fields, and does not show
  the file it writes to except under 「详情」.

**How it is kept and read** (`RODesk.statusChooser` in `desk.js`; `ingest/
attempts.py`; `research_records.assessment_statuses`).

- **The statuses.** The engine lists them (`assessment_statuses`) in the
  order above — 现行, 待定, 不利证据, 已放弃, 被否, 归档, the researcher's
  own in the order their `[verdict_labels]` names them, 其它 last — each with
  its `marker` (the first that names it) and `markers` (all of them: a
  researcher may write 现行 with two), whether the marker abandons the route
  (14.3) and its `code`. A default word the researcher renamed (✅ named 成立)
  keeps its place. A status is its word as written, so the file is the same
  in either interface language (13.1); the screen says the default words in
  its language and the researcher's own as written. They ride with the
  records the form is drawn for (`GET /api/attempts` answers `statuses`; the
  page keeps them in `state.attemptStatuses`) and in the handback's view.
- **Reading and writing a cell.** `parseAssessment` reads a cell as the
  engine's `verdict_status` does: the first known marker (the earliest; the
  longer when two start together; with or without the emoji variation
  selector, leading or not) is the status and the rest — the words before it
  and the words after, joined by a space — is the reason; the marker the cell
  wrote the status with is kept. A cell with no known marker, an empty one and
  one led by a symbol nobody named read as 其它 with **all of the cell** as
  the reason. `composeAssessment` (`attempts.compose_verdict` in the engine,
  which the handback's confirmation writes with) is the status's marker, a
  space and the reason trimmed; the marker alone with no reason; the reason
  alone for 其它. When the status is the one the cell was read with, the
  cell's own marker is written again, not the status's first. The two are
  held to the same answers by a test over every default marker, a
  `[verdict_labels]` name, two markers for one word, a cell with none, an
  empty reason and a symbol nobody named.
- **Nothing changed, nothing written.** The chooser hands back the cell as it
  was read for as long as neither the status nor the reason differs from what
  was read, and `changed()` is false when what they compose is that cell
  (picking another status and then the first again, or spaces added at an
  end). In an existing row the panel and the form send a cell only when it
  changed, so a cell with no known marker, a decoration or two lines is never
  rewritten by looking at it. After a save or 撤销 it reads the cell again.
- **The chooser.** One component: a button for each status, the chosen one
  pressed (`aria-pressed`, the `selected` class: ink and weight, not the
  clay of a problem, 21.8) and a reason field captioned 理由. Its two
  fields are an `<input type="hidden">` holding the status word and the
  reason's `<textarea>`, so that a half-made choice is guarded like typed
  words (14.3's question before the panel is left). The side panel's 研究判断与结果
  (also a member's proposal and the abandonment's reason and preview, 14.3,
  12.4), the node page's 编辑这一行, the new-attempt form and the handback's
  「改一下」 use it; the verdict chips and the free-text field are gone.
  A status the project no longer has (an old suggestion) leaves nothing
  pressed and the handback's 保存 waits for a choice.
- **Where it starts.** From the cell for an existing row; from 待定 (the
  project's word for it, else 其它) for a new row; from the agent's
  suggestion in the handback, and without one from the attempt's own cell.
- **The new-attempt form.** The number is filled with the next free one and
  stays editable; the date's label is 日期, with the table's own header
  after it only when that names the column differently (日期（时间）); the
  local model's draft is a folded block 「用本地模型辅助填写」 below the main
  fields (it fills the description and the result as 16.1 says); and 「详情」,
  folded at the foot, says which file the row is written to (for a member,
  the copy in their working copy) — the top of the panel no longer does. The
  preview step of a new row (16, 12.4) keeps its own sentence saying where it
  will be saved.

### 21.4 项目首页: what is going on

- **At the top, 「现在在做什么」**, two parts, side by side at desktop width and
  stacked when the window is narrow:
  - **进行中的尝试**: every attempt whose assessment is 现行 or 待定 and that
    holds at least one task (an unopened handback implies a task), as cards
    with the 21.1 progress line (「任务 n，未完成 m」, or 「任务 n，全部完成」),
    「等你看 n」 and the first line of the latest handback, newest activity
    first; a card opens the attempt's panel as the route map's cards do, its
    headline opens that handback and 「等你看 n」 the first that waits.
    *Amended (B2-1):* this said "that has an unfinished task or an unopened
    handback". Batch 1 made a task that has handed back read 已完成, so on
    Quant no attempt would have been listed; a 待定 attempt whose tasks have
    all handed back is waiting for the person's assessment, which is what is
    going on. An attempt assessed 不利证据, 被否, 已放弃 or 归档 (or with a
    status of the researcher's own) is not listed, nor is one with no task.
    A project in which no attempt holds a task says so in one quiet sentence
    with the way in (「还没有挂着任务的尝试。在研究路线里打开一条尝试，选「在这条
    尝试下加任务」。」); one whose attempts hold tasks but none is in progress
    says 「现在没有进行中的尝试。」 *Amended (fix batch 3):* that way in leads
    nowhere for a project that has tasks and no attempt (Quant before its
    grouping: 16 tasks, a route map saying 「尝试表里还没有尝试」), so the sentence
    names the ways in the page has: with tasks that belong to no attempt, 「全部任务
    下的「把这些任务归入尝试…」可以把已有的任务归到尝试里，也可以在任务表单里选「新开
    一条尝试…」」; with no attempt at all and no task, 「在任务表单里选「新开一条尝
    试…」，写下第一个任务」. The sentence above stays for a project that has
    attempts and no loose task.
  - **等你处理**: one list for the project and the person — unopened
    handbacks (theirs: the run was started by them), runs they started that
    wait for an answer, failed or out-of-bounds runs they started, and what
    待办 held (PI: proposals awaiting approval; member: their own proposals
    that wait, a returned one until 「知道了」, and a newer official version;
    待复核 link judgments; lines with a missing end). Each row says in a chip
    what it is, which attempt (opened in the panel) and task it concerns,
    and has the one action that handles it: 打开交回; 回答 (the task's row
    with its reply form open: the page's runs can be older than the list --
    the home polls only while it is on screen, and a first load has none --, so
    when they show no run waiting for an answer they are read once more before
    the row is said to be one that cannot be answered); 运行日志 and 「知道了」; 查看 (a proposal, in
    the panel); 复核 (the attempt's 待复核, or the review panel for the
    judgments that belong to no attempt); 同步. A line with a missing end has
    no action: it is said, never dropped (14.2). Its count shows beside 项目首页
    in the sidebar, in a one-person project too, and is the number of rows
    (the old count beside 研究路线 is gone: one count, not two).

- **Below**, reading on from the top (*amended, B2-2*: this was one sentence;
  it says what the page does now):
  - **研究问题与计划**, under its own title: its first paragraph and 「展开」
    with 12.13's chevron (「收起」 once open). The first paragraph is the first
    block of text that is no heading (a fenced block is one block; a plan of
    headings only gives its first line), drawn as the preview draws it and
    kept to eleven rem, the rest only on unfolding. Unfolded, the plan is the
    writing area itself, opened as every existing document is -- preview, 编辑,
    保存 (18.3) --, not a copy of it: the writing area is one, and stands in the
    plan while the plan is the open document and beside the lists for any
    other. Opening a note or a report folds the plan (its first paragraph stays: the
    plan as last read, read again when the listing says its file changed);
    unfolding it opens it in the writing area, and asks first, as opening any
    document does, when another holds unsaved words. A plan with no words yet
    starts unfolded, to be written in; a folded plan that holds unsaved words
    says 「尚未保存」 beside 「展开」.
  - **笔记 and 报告**, two lists in a column of 15 to 24rem (384px at 1440 wide,
    where the page is 976px) beside the open document; at 960px or less the
    window cannot hold both, and the lists stand above the open document in
    a box of their own that scrolls (the width 研究路线 stacks at). A row is the
    title as written -- never cut, never rewritten, wrapping to a second or
    third line -- and, apart from it, its date as a quiet field. The date is
    the one the file's name begins with (a note named `2026-03-12-meeting.md`:
    the engine names its own files by a hash, so this is for the files the
    researcher names), else the day the file last changed on this Mac, written
    `YYYY-MM-DD`; a date inside a title stays in the title. Notes are one list,
    newest date first (then by last change). Reports stand under the attempt each
    belongs to, each under that attempt's 「#n 描述」, the attempts in
    the order of 进行中的尝试 (newest activity first), 「未归入尝试」 last; where no
    report belongs to an attempt they are one list with no heading. The open
    document's row is told by ink and weight (21.8). The lists' headings are
    笔记 and 报告; the writing area calls a report 报告 too and the button is
    「写报告」 (21.6, B4-1).
  - **全部任务** (the section's heading; English “All tasks”, it was 任务),
    grouped by attempt in the same order, 「未归入尝试」 last (with
    「把这些任务归入尝试…」 under it, as before). A row's summary says the task's
    words (two lines of them while the row is shut), its owner -- the person,
    or the agent and its model -- and its state in words (未开始, 运行中, 等你回答,
    等你看, 已完成, 失败, 越过了限制, in the tones of the attempt's panel: gray,
    ochre, olive or clay), so none needs opening to be read. *Amended:* the newest three
    rows are no longer open by default; none is, and the row opens to read the
    rest -- its date, the run and the actions that were there (运行, 打开交回 /
    打开结果, 编辑, 移除) and 任务输出, the long block, which is therefore shown only when
    the row is opened. A row opened by hand stays open through the page's
    refreshes; 回答 and 运行日志 in 等你处理 open theirs.
- **The task form** (*amended, B2-3*: this was one sentence; the fields stand
  top to bottom as follows, and the attempt comes first, as 21.1 asks):
  属于哪条尝试; the task; 接着哪个任务 (optional); 负责人; 日期 (optional);
  研究上下文 (optional); then the buttons.
  - **The task** is written in a field of three lines at least that grows with
    what is typed, to fifteen rem, and then scrolls; it shrinks when lines are
    taken out. Enter makes a new line (nothing takes it for a save; the button
    saves). The text is saved as written but for the blank space at either end,
    under the engine's own limit (2,000 characters). A task written on one line
    is edited and shown as before; one of several lines comes back whole into
    the field, and its row shows the lines as written.
  - **负责人**: 「不指定」 first, then two labelled groups: 「人员」 (me, the
    team, 「其他人…」) and 「这台电脑上的 AI 执行者」 (each by agent and model,
    「Claude Code · sonnet」, as the rows name it; 「设置 AI 执行者…」 when this
    Mac has none). A name typed by hand stays among the people, and an agent this
    Mac lacks among the agents, while it is the value. Nothing about the owner
    on disk changes. *Amended, B4-3:* in a project of one person with no name set
    (no `me.toml`) the group held only 「其他人…」 and the researcher could not give
    a task to themselves; they are listed as 「我」 (“Me”) once the page knows there
    is no name, and chosen like a named person: the owner written to
    `tasks.json` is the text 「我」 whatever the screen's language is (nothing on
    disk depends on it, 13.1), and a row reads it as “Me” on an English screen. *Amended, fix batch B4:* that is so in a project of
    one person only, on 项目首页 and in an attempt's panel alike. In a team's project (the PI's, a member's, a machine not yet in
    the team) a stored 「我」 is a task of the solo period, and no one of the team is it: it reads as the project's first researcher
    (「最初的研究者」, “Original researcher”), and the owner control no longer offers 「我」.
    With a name set the entry is 「<name>（我）」, written as the name, and there is
    no second one.
  - **日期** is optional and empty by default -- nothing fills it with today.
    It is the task's existing `due`, which stays `""` when no date was given,
    as it always did: no new field on disk, and the engine's validation (empty,
    or a calendar date) is unchanged.
  - **保存并运行** stands beside the save button while the owner is an agent
    (not a name typed by hand) and the task could be run from its row -- it is
    not marked done, and its latest run is neither going nor waiting for the
    person's answer (`runnable`, the row's own condition). It saves, and once the
    engine has answered that the tasks are saved it starts the run by the row's
    own path (`startRun`: the mcp check, and the first-run confirmation of 17.1
    and 18.1 item 5 with the large files named), so nothing is asked twice or
    differently. If the save is refused (no attempt, no words) or fails (a
    conflict included), nothing runs and nothing is asked, and what was typed
    stays. Like the row's 运行 it waits, disabled with 「这个项目里还有一次运行没有结束。」,
    while another run of the project goes, and follows the poll when that run
    ends. The form then says 「任务已保存，运行已开始。」; 「任务已保存，没有开始运行。」
    when the confirmation was declined; or, when the run could not start (the
    mcp component missing, the engine refusing), 「任务已保存，但运行没有开始。原因写在这项任务那一行里。」,
    and that row is opened -- the rows are shut by default -- to show its note.
    When the owner is a person, only the save button shows.
  - **One primary button** (21.8): the save button (添加任务, 保存任务 when
    editing) is drawn as the primary; 保存并运行 and 清空 are the quiet ones.
    *Amended (fix batch 3):* the page has one primary, not one per section: while
    the writing area is on screen in edit mode (a note, a report, or the unfolded
    plan) 保存 is it and 添加任务 is drawn as the quiet button; in preview, with no
    document open, or with the plan folded (its writing area sits in the fold),
    添加任务 is the primary again.
  - **Presets.** Opened by 「在这条尝试下加任务」 the form has the attempt chosen
    and 接着哪个任务 set to the attempt's last task in `after` order (none when
    it has no task); opened by a handback's 「再派一个任务」 it also has 研究上下文
    set to that handback's record. These are choices, not writing: a form that
    holds only them is not unsaved work, and the person may change each.
- **研究路线's right-hand column** is the same 等你处理 list, under that title
  (it was 待办; English “Needs you”); it folds to a narrow strip (its title,
  set upright, and the sentence 「现在没有要处理的事。」 on hover) when the list
  and the notices above it are empty, which gives its width to the map or the
  list, and unfolds when a row arrives. A notice (not in the team yet, a team
  file or a shared folder that cannot be read) keeps it open; so does what a
  sync has just done. *Amended (fix batch 3):* 项目首页's list tells what a sync
  did as well -- the row of the official version goes with the sync, so a done
  row (the version the working copy is at now, and what was written) stands where
  it stood, until the desk is opened afresh; it is no row that waits and is not
  in the count. In a narrow window it is a bar under the map. The
  blocks it had (等你批准, 我的提议, 待复核, 正式版本有更新, 连线的一端找不到了) are
  the chips of its rows; the attempt panel's part of the same name is 等你处理
  too. The PI and member differences of 12.4 and 18.3 stand: a one-person
  project has no proposal and no official version, a PI has the proposals to
  decide, a member their own.

**How the top is worked out** (`src/researchos/now_doing.py`; the page is
`workspace.js`, the list is drawn by `desk.js`).

- **One function and one endpoint.** `GET /api/workspace/now` answers for the
  open project and the person asking `{role, needs, shared, progress, home}`;
  `shared` is `{state}` of the shared folder as the desk gives the column;
  `?only=needs` leaves `progress` and `home` out (the sidebar reads that from any page that is neither
  the desk nor 项目首页, which read the list themselves). `needs` is also in
  `GET /api/desk` — the same function (`now_doing.needs_you`), so the column
  and the home draw one list, and nothing of what 待办 held is worked out
  twice. The endpoint asks `desk.payload` for its `light` form: the cards
  carry only their number, title, date, status and marks, and the version
  store, the git log and the decisions are not read; the shared folder is read
  for at most the desk's few seconds, joining the desk's own job. *Amended
  (fix batch 3):* a first read that was too slow (`shared.state` `slow`, or
  `stale`: what it last read) leaves a notice and none of the PI's proposals in
  the answer, so the home reads again as 研究路线 does (5 s later, three times at
  most, while it is on screen, joining the read still in flight); `unreadable`
  and `not_configured` are not asked again, for a later read would not change
  them.
- **Activity** (what 进行中的尝试 is ordered by; the amendment's choice): the
  latest of the start or end of the latest run of each of the attempt's tasks
  (`ran_at`; a run not started yet counts by when it was created), the time
  each of its handbacks' reports was written (`report_at`, the file's time),
  and the date of its row where the table's days can be read (`date_year`,
  `date_key`). An attempt with none of these comes after the others, in the
  table's order. The figures on a card — `tasks`, `unfinished`, `unopened`,
  `latest.line` — are `attempt_tasks.by_attempt`'s, the route map's, never
  counted again; `ran_at` and `report_at` are two fields it now also gives each
  task. The run store and the unopened handbacks are read once for the cards
  and the list of one answer: one pass over the run store (`attempt_tasks`'s
  `_look`, kept in `shared`) serves the latest run of each task, the handbacks
  that wait and the 交回 rows (`handbacks.list_unopened` takes that reading), for
  `/api/workspace/now` and `/api/desk` alike, so the rows and the states of one
  answer cannot disagree for a handback opened between two readings.
- **The rows**, each `{kind, key, at, attempt, attempts, …}`: `handback`
  (`handbacks.list_unopened`), `answer`, `failed` and `out_of_bounds` (from
  `attempt_tasks.attention`: the tasks whose latest run the person started and
  that read 等你回答, 失败 or 越过了限制 in `state_of` — one notion of a task's
  state — except a run that was undone), `proposal` (the PI's), `mine` (a
  member's own), `official`, `review` (one per attempt that holds a judgment
  to review, and one for those that belong to none) and `line_missing`. They
  stand in this order, and newest first inside a kind (rows with no time keep
  the order they were found in): `answer`, `handback`, `out_of_bounds`, `failed`,
  `proposal`, `mine`, `official`, `review`, `line_missing`. A task run again
  has a new run, so the row of the old one is gone; so is the row of a task
  that was removed.
- **「知道了」 on a failed or out-of-bounds run** is kept per person on this Mac
  under the Research OS home, in `<home>/handbacks/<project id>/<key>.aside.json`
  beside the store of opened handbacks, never in the project; `POST
  /api/workspace/run/aside` takes `{run}`, only for a run the person started
  (`now_doing.set_aside`), and keeps only runs the run store still has.
  Another person on the same Mac is not affected. A returned proposal's
  「知道了」 stays where 18.3 kept it, in the browser.
- **After an act** the page reads again by the paths it already had: opening a
  handback (`hbMarkOpened`) and answering a run (`reloadAfterRun`) reload the
  home, a proposal answered in the panel, 「知道了」 and 同步 read the desk or the
  home again, and the count follows; nothing is reloaded by hand.

**How the lower part is worked out** (B2-2; `now_doing.home`, the page is
`workspace.js`).

- **One more key in the same answer.** `GET /api/workspace/now` also answers
  `home: {attempts, tasks, reports}` (left out by `?only=needs`, as `progress`
  is): `attempts` the ids of the attempts, of any status, that hold at least
  one task or one report, newest activity first; `tasks` every task's state
  code by task id; `reports` the attempt each report belongs to by report path.
  `progress` and `home` take their figures from one `attempt_tasks.by_attempt`
  reading and one reading of the run store (`shared`); nothing about a task's
  state or an attempt's activity is worked out on the page.
- **One order.** `home.attempts` is ordered by the activity rule above
  (`_activity`, `_newest_activity_first`, shared with 进行中的尝试): an attempt
  that holds only a report has the date of its row, or none and comes after
  the others in the table's order. The page puts the attempts of the table the
  answer does not list (a task added a moment ago) after them, in the table's
  order, and what belongs to none under 「未归入尝试」, last.
- **Which attempt a report belongs to** (`placed_reports`): the attempt of the
  task whose `report` it is (the first such task in the list, when that
  attempt is in the table), else the first attempt of its research context's
  `attempts` (20) that the table has. A report in one attempt is not also in
  another: the rows are a partition. The context's sidecar is read as the JSON it
  is, as a handback's headline is, so one with a field this version does not
  know still places its report; a damaged one places nothing and breaks
  nothing; at most 500 are read and none for a report a task placed already.
- **A task's state** is `attempt_tasks.state_of` for every task
  (`attempt_tasks.task_states`: also the tasks that belong to no attempt, which
  the cards do not read), said by the words the attempt's panel uses
  (`RODesk.taskStateWords`, one table). The state is the engine's reading of
  the task's latest run, so when a run starts, ends or changes state after the
  page first read the runs, the page asks `/api/workspace/now` again and the
  rows say the new state.
- **Before the engine has answered** (or when it cannot): reports are one list
  with no headings, 全部任务 stands in the table's order with each owner and no
  state, and nothing is dropped; the answer redraws the lists when it changes
  `home`, not otherwise.

### 21.5 The paper and its provenance

- **A warning appears only where it can be acted on, with the action beside
  it** (*amended, B3-1*: this was one paragraph; it says what the pages do
  now). One rule decides it (`researchos.provenance`): **the warning colour
  (21.8) is for what the paper relies on and is broken.** That is
  - a figure or table the chosen paper includes -- a section of a draft marked
    「设为论文正文」 includes it; while no draft is marked, any draft stands for
    the paper -- that has no source (缺来源) or that no attempt holds (缺尝试);
  - a claim of the paper with nothing behind it, or a result behind it whose
    script or file is not there (a claim whose result no attempt holds is
    quiet: the attempt is filed on the result);
  - any broken link (断链): a script names a file that is not there, whatever
    the file is for.

  Every other state the engine reports for a file is normal: quiet text in no
  colour, with its action beside it, drawn as a button in the outline style,
  where there is one to take. The engine says it as `tone` -- `ok`, `warning`
  or `quiet` -- on every row, and the pages draw it and decide nothing.
  - **A file no script writes that is only read** (a download in `raw/`):
    quiet 「没有生成脚本」 with 「指定生成它的脚本」. It is the warning 缺生成脚本
    -- the tag, the filter and the count of 数据与代码 -- only for a table the
    paper includes; 图表与论断 and a figure's own page say the same about a figure,
    with its cause (below).
  - **A script that belongs to no attempt, a file that has no reader, a script
    with no reads or writes found (未识别到读写关联), a script that cannot be
    read (暂时无法读取), a file that is not there and no script names (文件不存在),
    a figure or table of a draft the paper does not include**: quiet. 缺尝试
    on a script or a data file the paper does not include is quiet and offers
    「归入尝试」 (21.6; the `belongs_to` entry); on a figure or table the paper
    includes it is the warning, and says whether the generating script is the
    one in no attempt (「生成脚本没有归入尝试」) or nothing is (「还没有归入任何尝试」).
    **An attempt holds a file in three ways, and a trace asks every file of its
    chain** (*fix batch B3*): the step files its description names, 「归入尝试」, and
    **a task registered the file as its output** (`trace.Shared.placed`: the attempt of the
    task whose report lists it, `now_doing.placed_reports`; the page words it 「任务登记」).
    A row that names its attempt is never also 缺尝试, and the figure's own page and its
    script's say the same attempt.
    Where a source is missing too, the row says the source's cause only. A table
    of the paper that is a draft or a document (the mapping grammar gives it no
    generating script) is quiet too: a warning is where the page can act.
  - **缺来源 on a figure says which of its causes it is**, on the row and on the
    page, not in a tooltip: 「没有生成脚本」, and 「没有生成脚本：图是从 Word 文件里抽出来的」
    when the picture is named `image<N>` and stands in a folder called assets,
    media, files or images (what a Word file is taken apart into); 「生成它的脚本文件不在磁盘上」;
    「生成它的脚本暂时无法读取」; 「生成它的脚本读的文件不在」. The action
    is 「指定生成它的脚本」 for the first two: a `generates` entry for a figure, a
    `writes` entry for data, the write 补上来源 has always made (history and
    撤销 as for any entry; in a member's copy it travels with the next
    sync, 12.4). The others have no action of their own: the script or the
    file is mended where it is, and a broken link on the script's page offers the
    file it may mean.
  - **断链 is always a warning, on the script that names the file**; the file's
    own row is plain 「文件不存在」, and the count and the filter count scripts, not
    files. When a file of that name exists in the project, 「是这个吗？」 shows
    the first candidate, with one action, 「是」, that writes the entry a person
    would draw: a `reads` entry from that file to the script (data first), a
    `writes` or `generates` entry from the script to it. Candidates, in this
    order: the path the script names, taken from each folder of the script in
    turn, the script's own first and the project's root last, where it is a file
    (`environment/R_packages.lock.csv` named by
    `00_Current/replication_v9/environment/check_R_environment.R` is
    `00_Current/replication_v9/environment/R_packages.lock.csv`: scripts are
    often run from their package's root); then the other files of the same name,
    the nearest first by the folders they share with the script. The rest stand
    under 「其他同名文件 n 个」, folded, each with the same 「是」; nothing is
    written without the click, and 撤销 stays on the page that wrote it. A candidate
    is a file of the project (*fix batch B3*): a symlink, or a path that leaves the
    project through one, is never offered, as the entry would be refused; and a file
    already given to that script as that is not offered again for another path. **The
    engine uses the person's entry**: a script for which the researcher has a
    `reads` (or `writes`, `generates`) entry of a file of the same name is not broken
    -- the machine's link to the missing file stays in the index but is not
    reported (`lineage.superseded`), not in the report, the trace, the counts or
    `researchos lineage`, and the file only that text named is no row once no
    script names it -- nor a card of 关系图, which draws what 列表 lists (*fix batch
    B3*). **One entry answers one missing path**: a script that names a file of one
    name in two folders, neither there, needs an entry for each (two files of that
    name); an entry does not say which path it was for, so with fewer entries than
    paths the paths come in the order of their names and the last ones stay broken,
    with their own offer.
  - **关系图** (the other view of 数据与代码) says the same (*fix batch B3*): a file that
    only a script's text named and the researcher has answered is no card; the socket of an
    orphan input (`orphan_input`, the lineage report's definition, unchanged) is in the warning
    colour only when the chosen paper includes the table (`orphan_tone` `warning`), and a
    download in `raw/` is drawn with the plain socket (`quiet`).
  - **Counts and filters of 数据与代码**: 缺生成脚本 and 断链 count warnings and
    show the warning colour while they count some; 没有生成脚本 (the downloads),
    中间数据 and 同名文件 are neutral facts. A table put out of the paper
    (below) leaves 缺生成脚本 for 没有生成脚本.
  - **Same name.** 「其他同名文件 n 个」 is folded on every row, with 「展开」 (「收起」
    open) and 12.13's chevron. A list of readers, writers, reads or writes that
    names two or more scripts of one name shows each with the fewest nearest
    folders that tell it from the others -- 复核诊断.R（交付_20260927_审查修订）
    --, and a file in the project's root with none.
  - **On a node's own page** the same tones apply to its gaps (a figure of the
    paper: the gap in the warning colour, 缺生成脚本 with the cause under it and
    the action; any other: the same gap in no colour); its broken links are
    drawn under the trace with the candidates; 文件不存在 is plain, except on
    a file a broken link names in the trace and on a figure or table of the paper.
- **The paper is chosen once** (*amended, B3-2*: this was one sentence; it says
  what the pages do now).
  - **One control, a picker.** 图表与论断 has one 「设为论文正文」, not a button on
    each draft. It opens the list of the drafts the paper can still take -- the
    files the index read as drafts, except the attempt table's own --, the one
    changed most recently first (the file's modification time; each with the day
    it changed), **five shown and the rest under 「更多」**; choosing one writes the
    mark through the path the button always had (`POST /api/papers {draft,
    marked}`, `papers.toml`, 12.13; in a member's copy it is written in the
    working copy and travels with the next sync, 12.12). *Fix batch B3*: **a file
    name in the list is text, never the control** (21.8) -- each row has its own
    outline button, 「选这份」 (“Choose this one”), that writes the mark. With no draft
    to choose from (the attempt table is not one) and none marked, the block says
    「还没有读到文稿。」 and no instruction to press a control that is not there. The drafts of the paper
    stand above the control, each with its claim count and 「不再作为论文正文」: a
    paper may be several drafts (the main text and an appendix). A draft's own
    page keeps its 「设为论文正文」.
  - **The paper first.** 图与表 is one row per figure or table, in four lists:
    the paper's (a section of a marked draft includes it; with no draft marked,
    every draft stands for the paper and says so); the figures and registered
    files no draft cites (「没有被文稿引用的图和表」); then, folded with 12.13's
    chevron, 「其他文稿」 (included only by drafts the paper does not include)
    and 「不属于论文」 (below). **A figure that several drafts include is one row
    naming them** (「出现在」, the paper's draft first; two drafts of one name
    carry their folder). A line under the heading counts the paper's open work:
    its rows that are warnings (`counts.open`, in the warning colour; nothing
    is said at none). Claims are listed for the paper's drafts only.
  - **A thumbnail** stands on the row of a figure the asset route can serve: a
    png, jpeg, gif or webp that is there and under 8 MB (`thumb`, said by the
    engine), loaded lazily from `/api/workspace/asset`. A .pdf or .svg, a file
    that is gone and one that is too large have none, and a picture that cannot
    be read removes itself: never a broken image.
- **「不属于论文」 and 「放回论文」** (*B3-2*; the `not_in_paper` of B3-1 is the
  act's name). A figure or table that is not part of the paper -- a .svg or .tif
  copy of a plot, a by-product -- is put out with a button on its row (图表与论断,
  any list), on its own page and, for a table the paper includes, on 数据与代码;
  「放回论文」 puts it back. Each is **one entry in `.researchos/papers.toml`**,
  the event log of 12.13, in its form, **in a table of its own** (*fix batch B3*): a
  `[[paper_file]]` with `id` (`pf-…`), `seq`, `at`, `by` and `via` and `act =
  "not_in_paper"` (or `"back_in_paper"`), the file in `file`; the marks stay
  `[[paper]]` with `act` mark or unmark and `draft`, and the two tables do not touch
  each other, each counting its own `seq`. Why two tables: a Research OS from before
  the acts reads `[[paper]]` and refuses the whole file at an act it does not know
  (the ledger's strictness, 9.3), which on a member's older install, or a second
  machine not yet updated, would have emptied the marked drafts and refused every
  write; `parse_ledger` reads only its own table, so an older reader passes over the
  other, as `mappings.toml` carries `[[removal]]` and `[[received]]` beside
  `[[mapping]]`. The file is one for trust: either table unreadable or invalid
  refuses every write, and a file holding only `[[paper_file]]` entries is not an
  empty one. A member's put-out travels as a `paper_file` packet entry like the mark
  (an older PI engine passes over it; the member's file keeps it). The form of B3-2,
  an act in `[[paper]]`, was never released and is not read. A file is out when its
  newest such entry is
  `not_in_paper`; the file is its own history, and nothing on disk is moved.
  `POST /api/papers {file, not_in_paper}` writes it at once, and 撤销 (18.3)
  on the page it reloads writes the next entry. A member's is written in the
  working copy and carried by the next sync like the mark: the PI's next
  publish appends it with the member's `by`; no approval waits for either (12.4).
  **It is offered for a file that is there**: a file that is gone cannot be put out
  (its row says 文件不存在, and is mended in the draft or the file), but one put out
  while it was there stays listed, to be put back.
  A file put out is not what the paper includes, whatever a draft cites: it is
  quiet (above), not in the open count, and on 数据与代码 a table of the paper
  that is put out leaves 缺生成脚本 and is counted as 没有生成脚本. Its row
  stands under the folded 「不属于论文」 with the day (and, in a team, who), said on
  the row as 「放入「不属于论文」 {day}、{who}」, and the group lists a file that has gone
  from the index too, so that it can be put back. The confirmation and 撤销 stand at
  the top of the page and stay in view while a long list is read (*fix batch B3*:
  sticky).
- **The kind pages list by kind, and each row says its attempt** (21 intro,
  decision 5; *B3-2*). The 任务输出 block that opened 图表与论断 and 数据与代码
  is gone: what an agent produced is seen in its handback (21.2). A file an
  agent or a person registered (19.5) is a row of its kind like any other file --
  a figure or table on 图表与论断, a script or data file on 数据与代码, with the
  title it was registered with and its path beside it, also when the index holds
  no node for it (a document is seen in its handback, not listed by kind). A row
  says its attempt as a quiet field, 「尝试 #n 描述」 (the first two, and
  「等 n 条」), from `researchos.kind_rows`: **the attempt of the task whose output
  it is** (`now_doing.placed_reports`, the one notion of the attempt a report
  belongs to: the task's own, else the first its research context links), else
  **the attempts the researcher filed it under** (`trace.attempt_members`: the
  step files an attempt names and 「归入尝试」) -- for a figure or table, the
  attempts its trace finds, which its own page says too. A row with neither
  says nothing: lacking an attempt is a warning only for what the paper includes,
  and that is the row's state. A catalog that cannot be read costs the titles and
  attempts and is said in one line; the rows still list. **Speed** (*fix batch B3*):
  the catalog and the task list are read once for an answer (`trace.Shared`, which
  `kind_rows.Filed` and the traces share); the researcher's entries are read once, and
  asked of no link while there are none; the walk that finds same-name files is kept for
  20 seconds for the same scan (`lineage.Names`' `stamp`), so that a node page with a
  broken link does not walk the project at each opening; a row's old copies are built
  when its fold is first opened.

### 21.6 Words (amends 13.4)

As approved by the researcher on 2026-10-29:

| Old | New | English |
|---|---|---|
| 结果记录, 记录结果 (button) | 报告, 写报告 | Report, Write a report |
| 研究记录 (a report's context) | 要点 (the opening part of the report) | Key points |
| — | 交回 | Handback |
| 待办 | 等你处理 | Needs you |
| 记录 (sidebar), 人工记录 (panel) | 备份; panel 你的记录与备份 | Backups; Your records and backups |
| 运行记录 | 运行日志 | Run log |
| 关联文件 on a file page | 归入尝试 | File under an attempt |
| 关联文件 on an attempt | 添加文件 | Add files |
| 年表 | 尝试表 | Attempt table |
| 清单 (数据与代码) | 列表 | List |
| 未识别生成脚本, 没有找到生成它的脚本 | 缺生成脚本 (缺来源 stays the figures' umbrella) | No generating script |
| — | 指定生成它的脚本 (the action of 缺生成脚本; was 补上来源 on a figure) | Assign a generating script |
| — | 没有生成脚本 (the quiet text, and the filter of the files only read) | No script writes it |
| — | 是这个吗？ (with 「是」), 其他同名文件 n 个 | Is it this one?, n other same-name files |
| — | 代理建议, 确认这个判断; history 「由 X 确认（采纳 … 的建议）」 | Suggested by the agent, Confirm this assessment |
| — | 现在在做什么: 进行中的尝试, 等你处理 | In progress: Attempts in progress, Needs you |

The row of 等你处理 for a line whose end is missing, which 项目首页 draws as well, and the route map's
own sentences say 尝试表 (fix batch 3); the engine's sentences were renamed with 07bc263. The researcher's own
heading in the map file (「二、尝试途径总年表」) is theirs and is not touched.

In the interface 待办 is gone (21.4): the column of 研究路线, the part of the same name in an
attempt's panel and the count beside 项目首页 say 等你处理 (“Needs you”; the count's
hover is “{n} need you”). 14.6 and 18.3 above keep the word they introduced.

*Fix batch B3*: an attempt's page says 「添加文件」 (“Add files”) -- the button, its failure sentence and the
engine's gap (「这条尝试还没有添加任何文件」) -- and the two guides name it so; no screen says 「关联文件」.

*Fix batch B4-1 (the words on every screen)*: each row above is on every screen, in both languages.

- **报告 / 写报告.** 项目首页's header button is 「写报告」 (“Write a report”); a document of that kind is 报告 (“Report”)
  in the kind label, in the confirm sentence of the done mark, in the PI's footer (「笔记和报告会随下一次发布交给成员。」)
  and as the title the engine gives a report with no heading (`workspace._title`: 报告 / Report; the pre-21.6 words
  stand for "no title typed" too, so a page that read the file earlier writes no heading). The agent's instruction says
  report as well (`agents/brief.py`).
- **研究记录 had three meanings, and each has its word.** (a) A report's Section 20 context is 要点 (“Key points”): the
  disclosure of the report editor, 「保存要点」, 「还没有写要点。」, 「没能读取要点。」, 「重新载入要点」, the notices
  that follow a save, the section of a proposal's page, and the engine's sentences about it (`research_records.py`).
  (b) A reference to one report is 报告: the task form's 「不关联报告」 and the task row's 「报告：<title>」 (both hold
  `task.context.report`). (c) The documents that mention a file are 笔记与报告: 「提到这个文件的笔记与报告」 under a
  reference file of 文献, 「提到它的笔记与报告」 on a node page and a variable card, and the welcome sentence 「相关文件可以
  随时关联到笔记与报告」 (today only reports carry links, so those lists hold reports). The run's out-of-bounds
  word `record_changed` / `record_deleted` is none of the three -- it covers the attempt table, `research/tasks.json`
  and a report's context and catalog -- and says 「改动了尝试表、任务或报告的记录文件」 (“Changed the attempt table, the tasks
  or a report's record files”).
- **备份 / 你的记录与备份.** The sidebar's foot says 备份 (“Backups”), with the hover 「查看你的记录有哪些，以及最近一次备份的
  时间」; the panel it opens is titled 「你的记录与备份」; the commit tick reads 「先把你的记录提交一次」 and the engine's
  sentences about those files (the GitHub plan, the migration refusals) say 你的记录; the panel's lines are 21.7's. What is written to git is not
  renamed: the commit message stays 「Research OS：人工记录」, and the tick, which tells the person what the commit will be
  called, quotes it.
- **运行日志.** The run's button and its name (「运行日志：<task>」), the failure sentence, the 越界 sentence and the line of
  the run store's list in agent settings.
- **尝试表** on every screen: the page's own refusals (「这个编号已经在尝试表里了」), the guide card for a project with no
  table and the records panel. The researcher's own heading in the map file stays theirs.
- **列表.** 「输出列表暂时无法读取。」 (it was 清单) on 图表与论断, 数据与代码, a task's row and the drafts page. 清单 stays where it names
  another thing: the plan of an undo, the catalogue a task registers files in, the list of a run's changes.
- **The attempt form says 研究判断**, as the panel does, whatever the table's own header for the column is (「状态」 in
  the Quant table); the header only finds the column.

*Fix batch B4 (the words, again)*: the English of the retired rows is gone from every sentence a person reads, not from the page's
table alone -- the engine's own English (`outputs`, `research_records`, `workspace`, the refusals of `situation`) and the agent's tools
(`researchos_complete_task` answers 「Saved the report …」 and its description says report) say report and your records, never
"result record" or "human records"; README and the guides under `docs/` say report. 18.2's 产出 (it is 输出) is no screen's
noun any more (the notice of an unreadable output catalog said it); the string 产出 alone stays as the link word "produces". The second
retired sentence of the generating-script row, 没有找到生成它的脚本, is scanned for like the first.

What stays, each with its reason (the test that scans the screens lists them): the commit message above (on disk, 13.1);
`二、尝试途径总年表`, the heading the map file is read by; the header comment `identity` writes into `.researchos/`;
the agent's instruction, whose nouns 产出目录 and 最终产出 are the prompt's own (`agents/brief.py`);
and the command line, which keeps its words (`researchos github push --commit-records` still names the commit message; the help of
`researchos link` still names 补上来源 and 关联文件).

Nothing on disk is renamed: `research/results/`, `tasks.json`'s `report`,
`.context.json`, `judgements.toml` keep their names.

### 21.7 Storage and engine details stay out of the way (amends 8.8)

- **你的记录与备份** (opened by the sidebar's 备份, 21.6) says in words what is kept, one line per record (*B4-2*): 研究判断,
  尝试表 (which file it is and what its columns are), 连线确认 (the link judgments of 13.4), 手画的连线, 研究路线的布局, 变量卡,
  备注, 经 PI 批准的决定, 论文正文与不属于论文 (`papers.toml`), 任务列表 (`research/tasks.json`), Stata 全局变量指向的文件夹
  when the project has the file, and 版本历史 (kept material beside the index, not a record); the marks of the paper and the
  task list are rows of the inventory (`researchos records` lists them too). A line is its title, what the
  file holds (「3 条尝试，其中 2 条有研究判断」), 「最近一次备份：今天 14:02」 and 「在 Finder 中显示」; the file's place, the
  newest backup's name and the engine's own English for a problem are folded under 「详情」 (12.13's chevron).
  - *When it was last backed up* is the time in the newest backup's own name in `.researchos/backups/` (a daily snapshot or
    the copy taken before a write, whichever is later; `inventory.backup_time`, the API's `backed_up_at`) — never a file's
    modified time —, said as the desk says a time (今天 14:02, 10月4日, 2025年3月4日). A record never copied says
    「还没有备份过」; the version store is not copied and says neither. 变量卡 is many files: its time is the newest over the log and
    every version file of every card (`v1.toml`, … -- the text the researcher edits, the only file of a card still a draft), each by
    the later of its daily snapshot and its pre-write copy, so that a draft with a backup does not read 「还没有备份过」.
  - *The panel is drawn again* whenever the project moves (an agent's run changes files every two seconds), and it keeps what was
    done in it: a 「详情」 fold the researcher opened stays open, the answer to 「在 Finder 中显示」 stays on its line, and the scroll
    stays where it was. They are kept by record for as long as the panel is open.
  - *A problem* keeps its sentence in words on its line, in clay (内容无法解析, 旁边有同步冲突副本, …), not behind 「详情」.
  - *「在 Finder 中显示」* is offered for a record the project holds and goes through the existing `/api/open` route, which
    refuses a path outside the project (`..`, an absolute path, a link that leads out) before anything runs. The tick
    「先把你的记录提交一次」 is unchanged.
- **Paths are a quiet second line under a title, never the line itself** (*B4-2*). On 数据与代码 a row is titled by the title
  an agent or a person registered the file with, else by its file name; its path is the small quiet line under the title
  (the filters read the engine's data, so they still count it). 图表与论断's rows put the path under the name. In a handback
  each key number's file and each output is titled by its registered title, else by its file name, and that title opens it as
  a text link, never a box, with the path under it; the agent and model are named once, in the panel's head. The same goes for
  the files a task lists on 项目首页, the files of a report's 要点 (研究材料, 文献文件, 相关输出), the files a document
  mentions on a node page and the files changed in an attempt's panel.
- **A 断链 line** does not say its row's path or its direction twice: on the script's own row or page it reads 「断链：读取 X，
  文件不存在」 (写出 for a write; “Broken link: reads X, file not found”); where it is about another script it names it by its
  file name, 「断链：复核诊断.R 读取 X，文件不存在」, with the script's path as quiet text under the line. (This replaces 8.8's
  glossary row for “(reads, not found)”.)
- **变量 gets 「新建变量卡」** (*B4-3*), which makes the draft through the code `researchos variable new` runs, and the page no
  longer tells anyone to use a terminal. It is a button under the section's heading (the page's one primary button while there
  are no cards and no registered drafts, else the quiet one; a read-only or not-yet-migrated project offers the sentence 9.10
  gives instead) that opens a small form: the id, the command's rules in words (1 to 64 characters; letters, digits, `_`, `-`
  and `.`; not beginning with `_`, `.` or `-`; no `..`; not case-sensitive; not the id of a card that exists, whose log the
  index still holds or whose backups remain), what follows (the new draft opens; its definition is written in the editor and
  confirmed on the card's page), 创建 and 取消. The id is the command's only argument (its other, the project's path, is the
  open project), and is sent trimmed. `POST /api/variables/new {id}` calls `cards.new_card`, the function the command calls:
  the same id validation, the same exclusive creation of `.researchos/variables/<id>/v1.toml` from the commented template, the
  same lack of a log entry (the log begins at the draft's confirmation, 9.11), the same project lock: on the same project the
  command and the route leave the same files, the one `v1.toml` being new. A refusal comes back as a
  sentence in words in the request's language (`variables_api.NEW_REFUSED`: a length, a first character, a character, two
  dots, a taken id up to letter case, an id the index still holds or whose backups remain, an identity or folder that cannot
  be read), the engine's English being the error's own message, behind 「详情」. **A member** makes the card in their working
  copy, as the command does there (12.4: a variable's new definition version is a proposal); the form adds one sentence, that
  it reaches the PI by 提议 of the kind 变量定义. The empty page says what a card is and points at the button; `empty_help`
  (a sentence and the command to type) is gone from `GET /api/variables`, and no string of the 变量 view names a command.
  *Fix batch B4:* the form is a half-typed form and is left alone: the page is drawn again whenever the project moves, and the
  open form, the id typed and the caret in its field (`varState.newCard`) come through the redraw; creating the card or switching
  project ends them. While the form is open its 创建 is the one primary button and the opener is the quiet one (21.8), and 取消
  gives it back. Where no card can be made the sentence stands in the button's place, worded for the cards (「只读打开：不会写入任何东西」;
  before the migration 「迁移后才能新建变量卡」, not the link judgments' sentence), and the empty page's guide card then says the
  project cannot make one now -- it names 「新建变量卡」 only where the button is drawn.
- **One 设置 page** for this Mac, opened by 「设置」 in the sidebar's foot (between 备份 and 停止服务, shown with no project
  too; *B4-3*). It is the side panel, one section each, and needs no project. **AI 执行者**: the form 项目首页 carried --
  the programs and their paths, the agents by model and reasoning effort, the time limit, the spending cap, the run space
  limit and, when a project is open, its run store -- drawn by `workspace.js` into the box the page hands over
  (`ROWorkspace.mountSettings`), with the same keys of `settings.json` and the same `POST /api/settings/agents`. **本地模型
  （辅助填写）**: the address and the model of `POST /api/settings/llm`. **联网查询**: `doi_online_lookup` in
  `~/.researchos/settings.json` -- **a setting of this Mac, not of a project**, so it is here and not in the project menu --,
  read by `GET /api/settings/lookup` and written by the route the 文献 page always used, `POST /api/citations/lookup-setting`,
  which now answers with no project too. **界面语言**: 中文 or English by `POST /api/settings/language`; the foot's switch stays
  as the shortcut, and both run `switchInterfaceLanguage`. Nothing is stored anew. Every setting has this one form; the places
  that held one keep a text link 「在设置里修改」 and, where the setting is read, its state: 项目首页's header (「AI 执行者」 and
  the link; the owner control's 「设置 AI 执行者…」 opens the page too), 文献 (「联网查询：已打开 / 已关闭」 and the link, where a
  fold and a checkbox were) and the attempt form's 辅助填写 (where a draft goes, and the link). Per-project settings stay where
  they were -- 输出目录 on 项目首页, the rest in the project menu -- and the page says so. Opening it over a panel with half-typed words asks first (14.3), and words typed in its
  fields and not saved count for that.
  *Fix batch B4:* (1) 联网查询 is shown on both tabs of 文献, and turning it on from the 设置 page draws whichever tab is showing again.
  (2) The language choice is a reload, which drops what is not written: it asks first about the drafts of 项目首页 and about words
  half-typed in the open panel -- the 设置 page's own fields included (one question when 项目首页 has asked) --, and when the question
  is declined the choice goes back to the language in force. (3) The closing sentence names where a project's own settings are:
  「输出目录在项目首页的「全部任务」里，其余在侧栏顶部的项目菜单里」. (4) 新增尝试's 「在设置里修改」 takes the side panel, so the
  files chosen for 生成草稿 count as unsaved work for the leave guard (14.3), as typed words do. (5) A refusal that tells the researcher to
  write their name (an unreadable `me.toml` when 运行 is pressed) names 「成员」 -- and, for a project of one person, 「开始协作…」 that
  opens it --, not 设置, which has no name field.
- **停止服务 names what it ends** (*B4-3*). Before it asks, the page reads `GET /api/agents/going` -- the runs this engine
  carries out now, in every project (`AgentManager.going`; it answers with no project too) -- and, when there are any, the
  question names each by task (its first line), agent and model, and a run of another project by that project's folder, then
  says what stopping does, which is what `AgentManager.shutdown` does: the programs and everything they started are ended, each
  run is recorded 已中断 (`interrupted`, code `shutdown`), and what they had changed is **not** undone -- after the next start a
  run that can be undone has 「撤回本次运行」 in its task row. With none going the question is the old one; when the list
  cannot be read the question says so (“Could not check whether an AI agent is still running.”) and does not claim there are none.

### 21.8 Presentation (amends 12.11)

- **One primary action per page or panel**, drawn as the primary button;
  other actions are the quiet outline button; links that only navigate are
  text links. Titles, file names and statuses are never drawn as buttons.
- **The clay colour means "something is wrong"**: errors, broken provenance,
  out-of-bounds runs. Selection, the current section and links use ink and
  weight. An assessment's tint is its own (12.11) and is not the error tint.
- **Numbers in tables**: a value with more than six significant digits is
  shown to four significant digits, with the value as written on hover and in
  the file view; integers and values of six digits or fewer as written.
  `RODesk.formatNumber` (desk.js) is the one formatter, for every table and
  list of the app: it reads a plain decimal -- digits, one point, an optional
  exponent -- counts the significant digits as written (from the first that is
  not zero, trailing zeros after the point among them) and rounds those digits
  half up, not a float: `9.509441061106294e-05` is `9.509e-05` (a value
  written with an exponent keeps that form, two digits at least),
  `0.008063173780026744` is `0.008063`, `123456.789` is `1.235e+05` (a plain
  number whose integer part four digits cannot hold is not padded with zeros).
  An integer, a value of six digits or fewer, and anything with a unit, a
  percent sign, a word or a thousands separator is shown as written. The
  caller puts the value as written in the element's `title`; the record and
  the number check of 16.1 only ever have it as written. The handback's key
  numbers use it; the other tables are batch 5's.
- Every disclosure uses 12.13's chevron (文献 included). Every panel that
  covers the page has the same scrim and a 返回 step when it was opened from
  another panel. The route map fits all cards the first time a project's map
  is opened and says how many cards are outside the view when some are.
  Chinese text in the interface is never smaller than 12 px (*amended, fix
  batch 3*: the state word `.status-label` -- task rows, the chips of 等你处理,
  the assessment on a card --, `.own-mark` and `.var-chip` were 0.74rem, 11.84 px
  at the browser's 16 px root, and are 0.75rem).

### 21.9 Acceptance

1. On a copy of Quant: the grouping of 21.1 proposes Sword v0, v0.2 and
   v0.3; the other tasks fall into shared-term groups named by a report title
   or stay in none (the data-source tasks, 4 and 15, are not proposed as one
   attempt: the person moves 15 into the group of 4 if they want one); after
   confirming, the route map shows those attempts with their tasks counted,
   and 项目首页's 进行中的尝试 shows them (every task reads 已完成, each attempt is
   assessed 待定, and they stand v0.2, v0.3, v0 -- the newest report first). Confirmed in that order, on a table
   with no rows, they are written as #4, #5 and #6, the groups still shown
   keeping 1, 2 and 3. Below them the page reads 研究问题与计划 (its first
   paragraph), 笔记 and 报告 (the reports under #5, #6 and #4 in that order, a
   title such as 「Sword v0 审查与回测：…（2026-10-24）」 whole on at most three lines
   at 1440x900, its date beside it) and 全部任务 under the same attempts, every
   row saying its agent and model and 已完成 (or 等你看) with none opened.
2. A real agent run on a task under an attempt hands back a headline, key
   numbers (each found in its file), what is undone, questions and a
   suggested assessment; 「确认这个判断」 writes the cell in the file's form and
   the history says the suggestion was adopted; 「改一下」 to another status
   records `adopted: false`.
3. The panel's chooser shows the chosen status; saving 待定 with a reason
   writes `🕒 <reason>`; a cell with no known marker is written back byte for
   byte when the person changes nothing in it.
4. 等你处理 lists an unopened handback, a run waiting for an answer and, in a
   two-person copy, a proposal; the sidebar count matches; opening a
   handback removes it for that person only.
5. On the thesis copy (*amended, B3-1*): no red warning on a script that only
   lacks an attempt, nor on a download in `raw/` (quiet 「没有生成脚本」, with
   「指定生成它的脚本」); the pictures extracted from the Word file show 缺来源
   with 「没有生成脚本：图是从 Word 文件里抽出来的」 and 「指定生成它的脚本」;
   `check_R_environment.R`'s 断链 offers
   `00_Current/replication_v9/environment/R_packages.lock.csv` under 「是这个吗？」,
   the other copies of the file under 「其他同名文件 n 个」, and one 「是」 makes
   the row stop saying 断链; every row's same-name files are folded (*amended,
   B3-2*): the paper is chosen with one 「设为论文正文」 and its picker (the
   drafts changed most recently first, five shown, 「更多」), and then 图表与论断
   lists its figures first with 其他文稿 folded and a figure of two drafts one
   row naming both, each figure with a small picture and each row of 图表与论断
   and 数据与代码 saying its attempt; 「不属于论文」 on a by-product (a .svg copy of
   a plot) is one entry in the `[[paper_file]]` table of `papers.toml` (an older
   Research OS still reads the file), takes it out of the open count and
   puts it under the folded 「不属于论文」 with the day, and 「放回论文」 reverses it;
   a figure a task registered under an attempt is never both in that attempt and
   缺尝试; the
   任务输出 block is gone from both pages.
6. No screen uses a retired word of 21.6; the glossary test covers the new
   rows; English mode shows the new words (*B4-1*: `tests/test_glossary.py` holds every row of 21.6 to the page's English
   table and scans every string the screens show for the retired words, with a short allowlist, a reason each); *fix batch B4:* the
   scan also covers 18.2's 产出 and 没有找到生成它的脚本, the engine's English sentences (retired English such as "result record" and
   "human records") and README and the guides under `docs/`.
7. One primary button per page; no title or status drawn as a button; the
   clay colour appears only on problems; long numbers are shown to four
   significant digits.
8. The full suite passes on Python 3.11 and 3.13.
