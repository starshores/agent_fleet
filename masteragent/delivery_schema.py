"""Schema 5 delivery ledger definitions, shared with explicit storage migration."""

DELIVERY_COLUMNS = {
    "result_deliveries": (
        "delivery_id", "admission_id", "route_id", "route_epoch", "installation_id", "role_id",
        "generation", "task_id", "attempt_id", "result_task_id", "destination_namespace",
        "destination_id", "destination_binding", "payload_version", "text", "text_bytes", "text_sha256", "envelope",
        "envelope_bytes", "envelope_sha256", "policy", "state", "revision", "claim_epoch",
        "sender_id", "claim_hash", "expires_at", "send_count", "last_sent_at", "next_send_at",
        "reason", "receipt", "created_at", "updated_at", "received_at"),
    "delivery_evidence": ("evidence_id", "delivery_id", "kind", "detail", "created_at"),
}

DELIVERY_SCHEMA = {
    "result_deliveries": """CREATE TABLE result_deliveries (
        delivery_id TEXT PRIMARY KEY,
        admission_id TEXT NOT NULL REFERENCES source_admissions(admission_id),
        route_id TEXT NOT NULL REFERENCES routes(route_id), route_epoch INTEGER NOT NULL,
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
        result_task_id TEXT NOT NULL REFERENCES results(task_id),
        destination_namespace TEXT NOT NULL, destination_id TEXT NOT NULL, destination_binding TEXT,
        payload_version TEXT NOT NULL CHECK(payload_version='text.v1'),
        text TEXT NOT NULL, text_bytes INTEGER NOT NULL CHECK(text_bytes>=0),
        text_sha256 TEXT NOT NULL, envelope TEXT NOT NULL,
        envelope_bytes INTEGER NOT NULL CHECK(envelope_bytes>=0), envelope_sha256 TEXT NOT NULL,
        policy TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','claimed','sending','received','held','rejected')),
        revision INTEGER NOT NULL CHECK(revision>=1), claim_epoch INTEGER NOT NULL CHECK(claim_epoch>=0),
        sender_id TEXT, claim_hash TEXT, expires_at REAL,
        send_count INTEGER NOT NULL CHECK(send_count>=0), last_sent_at REAL, next_send_at REAL,
        reason TEXT, receipt TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, received_at TEXT,
        UNIQUE(result_task_id,destination_namespace,destination_id))""",
    "delivery_evidence": """CREATE TABLE delivery_evidence (
        evidence_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL REFERENCES result_deliveries(delivery_id),
        kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL)""",
}

DELIVERY_INDEXES = {
    "delivery_pending": "CREATE INDEX delivery_pending ON result_deliveries(state,installation_id,created_at)",
}
