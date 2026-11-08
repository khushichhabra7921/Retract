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

-- Operation journal. An 'executing' row with a live lease is the durable global lock.
CREATE TABLE operations (
    op_id             TEXT PRIMARY KEY,
    source_ref        TEXT NOT NULL,
    mode              TEXT NOT NULL CHECK (mode IN ('auto', 'delete', 'quarantine')),
    plan_closure_hash TEXT NOT NULL,
    state             TEXT NOT NULL CHECK (state IN ('executing', 'failed', 'finalized')),
    outcome           TEXT CHECK (outcome IN ('completed', 'contained', 'failed')),
    owner_token       TEXT,
    lease_expires_at  REAL,
    error             TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX operations_state ON operations (state);

-- One row per affected record, in topological order (seq).
-- effect: what actually happened (may differ from the planned action, e.g. replay fallback).
CREATE TABLE op_actions (
    op_id             TEXT NOT NULL REFERENCES operations (op_id),
    record_id         TEXT NOT NULL REFERENCES records (record_id),
    seq               INTEGER NOT NULL,
    action            TEXT NOT NULL CHECK (action IN ('delete', 'quarantine', 'rederive')),
    reason            TEXT NOT NULL,
    state             TEXT NOT NULL CHECK (state IN ('pending', 'running', 'done', 'failed')),
    effect            TEXT CHECK (effect IN ('deleted', 'quarantined', 'rederived',
                                             'replacement_quarantined')),
    content_hash      TEXT,  -- HMAC(pseudonym key, content) taken at BLOCK
    new_record_id     TEXT,  -- journaled before the replacement write
    retract_token     TEXT,  -- "op_id:record_id", stamped on the replacement's adapter item
    adapter_record_id TEXT,  -- purged adapter item, re-checked by VERIFY
    detail            TEXT,
    PRIMARY KEY (op_id, record_id)
);

-- Hash-chained, signed receipts. body is canonical JSON.
CREATE TABLE receipts (
    seq       INTEGER PRIMARY KEY,
    op_id     TEXT NOT NULL UNIQUE REFERENCES operations (op_id),
    body      TEXT NOT NULL,
    hash      TEXT NOT NULL,  -- sha256(body)
    signature TEXT NOT NULL   -- HMAC-SHA256(RETRACT_SIGNING_KEY, body)
);
