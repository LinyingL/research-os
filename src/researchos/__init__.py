"""Research OS (`researchos`; called RCE until V8): a local record of how a
research project came to be -- a provenance graph over the tools researchers
already use, and the judgments they make on it.

See DESIGN.md at the repo root for product scope and architecture
decisions. This package currently exposes only the SQLite storage layer
(researchos.db); ingestion, the 7B semantic layer, and the MCP server land in later
phases per DESIGN.md section 7.
"""

__version__ = "0.1.0"
