"""Every name Research OS goes by, in one place (DESIGN.md 12.8).

The product was called RCE until V8. Its hidden folders, its home, its
environment variable, its command, its app bundle and the name its engine
answers with all changed together; the old spellings survive only as the
`LEGACY_*` constants below, which exactly two things read: the rename
migration (`researchos.rename`, which moves `.rce/` and `~/.rce` to their
new names) and the compatibility paths that keep honouring what an older
install left behind (`RCE_HOME`, the `rce` command alias, an old engine
answering on the app's port, an `RCE.app` this tool built).

No other module spells `.rce`, `~/.rce`, `RCE_HOME` or `.researchos`
itself -- they import from here (`researchos.paths` re-exports the folder
names for the many callers that already import those from it).
"""

from __future__ import annotations

#: The product, as people read it (app, docs, Chinese copy alike).
PRODUCT_NAME = "Research OS"

#: The command, and the alias kept for one release (12.8).
COMMAND = "researchos"
LEGACY_COMMAND = "rce"
#: The one line the alias prints on stderr before running the command.
LEGACY_COMMAND_NOTICE = f"{LEGACY_COMMAND} is now {COMMAND}"

#: The researcher-owned folder inside each project.
PROJECT_DIRNAME = ".researchos"
LEGACY_PROJECT_DIRNAME = ".rce"

#: The machine's own folder under the user's home (index, registry, locks).
HOME_DIRNAME = ".researchos"
LEGACY_HOME_DIRNAME = ".rce"

#: The environment variable naming the home folder itself; the old one is
#: still honoured when the new one is unset.
HOME_ENV_VAR = "RESEARCHOS_HOME"
LEGACY_HOME_ENV_VAR = "RCE_HOME"

#: Under the home: one lock file per project (9.7) and the registry's.
LOCKS_DIRNAME = "locks"

#: The macOS app (8.9). A new bundle identifier: macOS treats it as a new
#: application and asks once more for access to the Documents folder.
APP_BUNDLE_NAME = "Research OS.app"
APP_EXECUTABLE_NAME = "ResearchOS"
APP_ICON_NAME = "ResearchOS"
BUNDLE_IDENTIFIER = "dev.researchos.app"
LEGACY_APP_BUNDLE_NAME = "RCE.app"
LEGACY_BUNDLE_IDENTIFIER = "dev.researchos.rce"

#: What `GET /api/engine` answers as `engine`; an older engine answered
#: with the old name and still counts as an engine holding an index.
ENGINE_NAME = "researchos"
LEGACY_ENGINE_NAME = "rce"

#: The MCP server's name.
MCP_SERVER_NAME = "researchos"
