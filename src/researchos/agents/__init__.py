"""AI agents carry out tasks (DESIGN.md Section 17).

A task's owner may be an agent installed on this Mac -- a Claude Code model
or a model reached through OpenCode. This package holds everything the
engine needs to start one and keep it inside its bounds: the agent
labels and owner values (`labels`), this Mac's agent list and the programs
(`profiles`), the run store (`runs`), the before-picture and the after-run
check (`snapshot`), Claude Code's PreToolUse guard (`guard`, `hook`), the
command lines and the instruction (`runners`, `brief`) and the run manager
(`manager`). Stdlib only: the core install has no third-party dependency.
"""
