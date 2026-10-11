"""Optional semantic enhancement layer (DESIGN.md section 0/7): a local,
vendor-neutral model client. Off by default and never a prerequisite -- the
deterministic layers in researchos.ingest are fully usable without this package.

This package exposes researchos.semantic.backend's LlmBackend/LlmError (an
OpenAI-compatible chat-completions client, stdlib urllib only, zero
third-party dependency) and researchos.semantic.judge's
review_pending_backed_by/EdgeReview/JudgeRunResult (S2): the logic that
reviews deterministic `backed_by` candidates and annotates each with a
model's opinion, wired up as `researchos judge`. Per the constitution, that logic
writes only via `db.set_edge_semantic_review` -- a model's output is
evidence/annotation attached to the candidate's existing `pending` status,
never a `confirmed`/`rejected` write; see researchos.semantic.judge's module
docstring for the full reasoning. `supports` edges remain unimplemented,
schema-only (DESIGN.md section 7, "Next").
"""
