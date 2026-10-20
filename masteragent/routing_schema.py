"""Routing declarations; schema 6 adds immutable route endpoint bindings."""

ROUTING_COLUMNS = {
    "routes": ("route_id", "source_namespace", "route_epoch", "owner_lane", "installation_id", "role_id", "generation", "destination_namespace", "destination_id", "admission_open", "claim_open", "delivery_open", "policy", "policy_digest", "created_at", "updated_at"),
    "route_versions": ("route_id", "route_epoch", "snapshot", "snapshot_digest", "created_at"),
    "source_claims": ("source_namespace", "source_key", "route_id", "claim_epoch", "owner_id", "lease_hash", "expires_at", "policy", "updated_at"),
    "source_admissions": ("admission_id", "route_id", "route_epoch", "source_namespace", "source_key", "kind", "text", "text_bytes", "text_sha256", "envelope_sha256", "owner_lane", "installation_id", "role_id", "generation", "task_id", "destination_namespace", "destination_id", "policy", "source_confirmed", "created_at", "confirmed_at"),
    "legacy_claims": ("admission_id", "claim_epoch", "owner_id", "lease_hash", "expires_at", "state", "result_digest", "created_at", "updated_at"),
    "routing_events": ("event_id", "route_id", "admission_id", "kind", "detail", "created_at"),
}

ROUTING_SCHEMA = {
    "routes": """CREATE TABLE routes (
        route_id TEXT PRIMARY KEY, source_namespace TEXT NOT NULL UNIQUE,
        route_epoch INTEGER NOT NULL CHECK(route_epoch>0),
        owner_lane TEXT NOT NULL CHECK(owner_lane IN ('legacy','v3')),
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
        destination_namespace TEXT NOT NULL, destination_id TEXT NOT NULL,
        admission_open INTEGER NOT NULL CHECK(admission_open IN (0,1)),
        claim_open INTEGER NOT NULL CHECK(claim_open IN (0,1)),
        delivery_open INTEGER NOT NULL CHECK(delivery_open IN (0,1)),
        policy TEXT NOT NULL, policy_digest TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "route_versions": """CREATE TABLE route_versions (
        route_id TEXT NOT NULL REFERENCES routes(route_id), route_epoch INTEGER NOT NULL,
        snapshot TEXT NOT NULL, snapshot_digest TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(route_id,route_epoch))""",
    "source_claims": """CREATE TABLE source_claims (
        source_namespace TEXT NOT NULL, source_key TEXT NOT NULL,
        route_id TEXT NOT NULL REFERENCES routes(route_id),
        claim_epoch INTEGER NOT NULL CHECK(claim_epoch>0), owner_id TEXT NOT NULL,
        lease_hash TEXT NOT NULL, expires_at REAL NOT NULL, policy TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(source_namespace,source_key))""",
    "source_admissions": """CREATE TABLE source_admissions (
        admission_id TEXT PRIMARY KEY, route_id TEXT NOT NULL REFERENCES routes(route_id),
        route_epoch INTEGER NOT NULL, source_namespace TEXT NOT NULL, source_key TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind='task'), text TEXT NOT NULL,
        text_bytes INTEGER NOT NULL CHECK(text_bytes>=0), text_sha256 TEXT NOT NULL,
        envelope_sha256 TEXT NOT NULL, owner_lane TEXT NOT NULL CHECK(owner_lane IN ('legacy','v3')),
        installation_id TEXT NOT NULL REFERENCES installations(installation_id), role_id TEXT NOT NULL,
        generation INTEGER NOT NULL, task_id TEXT UNIQUE REFERENCES tasks(task_id),
        destination_namespace TEXT NOT NULL, destination_id TEXT NOT NULL, policy TEXT NOT NULL,
        source_confirmed INTEGER NOT NULL CHECK(source_confirmed IN (0,1)),
        created_at TEXT NOT NULL, confirmed_at TEXT,
        CHECK((owner_lane='v3' AND task_id IS NOT NULL) OR (owner_lane='legacy' AND task_id IS NULL)),
        UNIQUE(source_namespace,source_key), FOREIGN KEY(route_id,route_epoch) REFERENCES route_versions(route_id,route_epoch))""",
    "legacy_claims": """CREATE TABLE legacy_claims (
        admission_id TEXT PRIMARY KEY REFERENCES source_admissions(admission_id),
        claim_epoch INTEGER NOT NULL CHECK(claim_epoch>0), owner_id TEXT NOT NULL,
        lease_hash TEXT NOT NULL, expires_at REAL NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('running','held','retryable','completed')),
        result_digest TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "routing_events": """CREATE TABLE routing_events (
        event_id TEXT PRIMARY KEY, route_id TEXT NOT NULL REFERENCES routes(route_id),
        admission_id TEXT REFERENCES source_admissions(admission_id),
        kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL)""",
}

ROUTING_INDEXES = {
    "route_admissions_by_route": "CREATE INDEX route_admissions_by_route ON source_admissions(route_id,created_at)",
}

# Keep schema 5 declarations independent so migrations validate the actual old
# contract rather than accepting whatever the current schema happens to contain.
V5_ROUTING_COLUMNS = dict(ROUTING_COLUMNS)
V5_ROUTING_SCHEMA = dict(ROUTING_SCHEMA)
ROUTING_COLUMNS["route_endpoints"] = (
    "route_id", "source_binding", "destination_binding", "created_at")
ROUTING_SCHEMA["route_endpoints"] = """CREATE TABLE route_endpoints (
    route_id TEXT PRIMARY KEY REFERENCES routes(route_id),
    source_binding TEXT NOT NULL, destination_binding TEXT NOT NULL,
    created_at TEXT NOT NULL)"""
