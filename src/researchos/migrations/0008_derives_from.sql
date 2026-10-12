-- Migration 0008: Research OS V10 (DESIGN.md 14.2).
--
--   edges.type gains `derives_from`  a hand-drawn line between two attempts
--                                    (8.5's file, `.researchos/mappings.toml`):
--                                    the child attempt --derives_from--> the
--                                    attempt it grew out of, 「源自」. Written
--                                    only by the mappings ingest (extractor
--                                    "mapping"), so it is derived and rebuilt
--                                    like every other row here.
--
-- Same SQLite 12-step CHECK-widening rebuild as migration 0007, carrying
-- every column and index `edges` has after it (0004's scan stamps, 0005's
-- machine_status). No other table changes. `db.migrate` wraps this file in
-- one transaction with foreign keys off, and checks them before committing.

CREATE TABLE edges_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src TEXT NOT NULL REFERENCES nodes(id),
    dst TEXT NOT NULL REFERENCES nodes(id),
    type TEXT NOT NULL CHECK (type IN (
        'implements', 'produces', 'generates', 'includes',
        'cites', 'authored_by', 'backed_by', 'supports', 'uses',
        'reads', 'writes', 'belongs_to', 'derives_from'
    )),
    extractor TEXT NOT NULL,
    evidence TEXT NOT NULL CHECK (evidence NOT IN ('{}', '', 'null')),
    confidence REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('auto', 'pending', 'confirmed', 'rejected')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    scan_basis TEXT,
    scan_seen INTEGER,
    scan_source TEXT,
    scan_appeared INTEGER,
    scan_lost INTEGER,
    machine_status TEXT CHECK (machine_status IS NULL OR machine_status IN ('auto', 'pending')),
    UNIQUE (src, dst, type, extractor)
);

INSERT INTO edges_new (
    id, src, dst, type, extractor, evidence, confidence, status, created_at, updated_at,
    scan_basis, scan_seen, scan_source, scan_appeared, scan_lost, machine_status
)
SELECT
    id, src, dst, type, extractor, evidence, confidence, status, created_at, updated_at,
    scan_basis, scan_seen, scan_source, scan_appeared, scan_lost, machine_status
FROM edges;

DROP TABLE edges;

ALTER TABLE edges_new RENAME TO edges;

CREATE INDEX idx_edges_src ON edges(src);
CREATE INDEX idx_edges_dst ON edges(dst);
CREATE INDEX idx_edges_status ON edges(status);
CREATE INDEX idx_edges_scan_source ON edges(extractor, scan_source);
CREATE INDEX idx_edges_scan_appeared ON edges(scan_appeared);
