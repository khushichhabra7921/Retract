-- Lineage schema. Guard rule: readable <=> status = 'active' AND hold_op_id IS NULL.

CREATE TABLE records (
    record_id         TEXT PRIMARY KEY,
    agent             TEXT NOT NULL,
    tier              TEXT NOT NULL,
    content           TEXT,  -- NULLed after a verified purge
    status            TEXT NOT NULL
                      CHECK (status IN ('pending', 'active', 'quarantined', 'retracted', 'tombstoned')),
    hold_op_id        TEXT,
    retract_token     TEXT NOT NULL UNIQUE,
    adapter_record_id TEXT,
    fn_op             TEXT,
    fn_version        TEXT,
    supersedes        TEXT REFERENCES records (record_id),
    created_at        TEXT NOT NULL
);
CREATE INDEX records_status ON records (status);
CREATE INDEX records_tier ON records (tier);

-- Parent edges. kind = 'input' (explicit inputs=) or 'read' (rt.step read-set).
-- position orders inputs first, then reads in read order.
CREATE TABLE edges (
    child_id  TEXT NOT NULL REFERENCES records (record_id),
    parent_id TEXT NOT NULL REFERENCES records (record_id),
    kind      TEXT NOT NULL CHECK (kind IN ('input', 'read')),
    position  INTEGER NOT NULL,
    PRIMARY KEY (child_id, parent_id)
);
CREATE INDEX edges_parent ON edges (parent_id);

-- Ownership on primary records; extra source material on derived records.
CREATE TABLE explicit_sources (
    record_id TEXT NOT NULL REFERENCES records (record_id),
    source_id TEXT NOT NULL,
    PRIMARY KEY (record_id, source_id)
);
CREATE INDEX explicit_sources_source ON explicit_sources (source_id);

-- Effective labels = labels of parents at write time UNION explicit_sources. Computed by the SDK.
CREATE TABLE labels (
    record_id TEXT NOT NULL REFERENCES records (record_id),
    source_id TEXT NOT NULL,
    PRIMARY KEY (record_id, source_id)
);
CREATE INDEX labels_source ON labels (source_id);

-- Retracted sources. source_ref = HMAC-SHA256(RETRACT_PSEUDONYM_KEY, source_id); the raw id is not stored.
CREATE TABLE tombstones (
    source_ref TEXT PRIMARY KEY,
    op_id      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
