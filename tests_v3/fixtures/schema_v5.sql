-- Frozen schema 5 DDL from 3.1.0-dev.3; never generated from current DDL in tests.
PRAGMA application_id=1296118576;
PRAGMA user_version=5;
CREATE TABLE installations (
        installation_id TEXT PRIMARY KEY, package_id TEXT NOT NULL,
        version TEXT NOT NULL, digest TEXT NOT NULL, snapshot TEXT NOT NULL,
        snapshot_digest TEXT NOT NULL, workspace TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('inactive','active','uninstalled')),
        generation INTEGER NOT NULL DEFAULT 0, installed_at TEXT NOT NULL);
CREATE TABLE registrations (
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, registered_at TEXT NOT NULL,
        PRIMARY KEY(installation_id,role_id,generation));
CREATE TABLE actor_tokens (
        token_hash TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
        role_id TEXT NOT NULL, generation INTEGER NOT NULL,
        FOREIGN KEY(installation_id,role_id,generation)
        REFERENCES registrations(installation_id,role_id,generation));
CREATE TABLE tasks (
        task_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, text TEXT NOT NULL,
        client_request_id TEXT, state TEXT NOT NULL CHECK(state IN
        ('queued','running','completed','failed','awaiting_decision','waiting','needs_reconcile','cancelled')),
        created_at TEXT NOT NULL, completed_at TEXT, dispatch_generation INTEGER NOT NULL,
        parent_task_id TEXT REFERENCES tasks(task_id), UNIQUE(installation_id,client_request_id));
CREATE TABLE results (
        task_id TEXT PRIMARY KEY REFERENCES tasks(task_id), installation_id TEXT NOT NULL,
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, tool_id TEXT NOT NULL,
        output TEXT, status TEXT NOT NULL CHECK(status IN ('ok','error')), error TEXT,
        created_at TEXT NOT NULL, attempt_id TEXT REFERENCES attempts(attempt_id));
CREATE TABLE notifications (
        notification_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
        installation_id TEXT NOT NULL, role_id TEXT NOT NULL, generation INTEGER NOT NULL,
        kind TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE attempts (
        attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
        installation_id TEXT NOT NULL REFERENCES installations(installation_id), role_id TEXT NOT NULL,
        generation INTEGER NOT NULL, ordinal INTEGER NOT NULL, tool_id TEXT NOT NULL,
        owner_hash TEXT NOT NULL, lease_hash TEXT NOT NULL, expires_at REAL NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('running','completed','failed','expired','cancelled')),
        created_at TEXT NOT NULL, finished_at TEXT, UNIQUE(task_id,ordinal));
CREATE TABLE attempt_events (
        event_id TEXT PRIMARY KEY, attempt_id TEXT REFERENCES attempts(attempt_id),
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE continuations (
        continuation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
        installation_id TEXT NOT NULL, from_generation INTEGER NOT NULL,
        to_generation INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(task_id,to_generation));
CREATE TABLE decisions (
        decision_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
        task_id TEXT NOT NULL REFERENCES tasks(task_id), generation INTEGER NOT NULL,
        request_id TEXT NOT NULL, title TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected','expired','superseded')),
        revision INTEGER NOT NULL, expires_at REAL, answer_id TEXT, answer TEXT,
        created_at TEXT NOT NULL, answered_at TEXT,
        UNIQUE(installation_id,request_id,generation));
CREATE TABLE outbox (
        event_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        generation INTEGER NOT NULL, task_id TEXT REFERENCES tasks(task_id), recipient_role TEXT NOT NULL,
        kind TEXT NOT NULL, payload TEXT NOT NULL, dedupe_key TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','delivered','held')),
        created_at TEXT NOT NULL, delivered_at TEXT,
        UNIQUE(installation_id,generation,dedupe_key));
CREATE TABLE requests (
        request_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL, generation INTEGER NOT NULL,
        parent_task_id TEXT NOT NULL REFERENCES tasks(task_id),
        child_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
        from_role TEXT NOT NULL, to_role TEXT NOT NULL, client_request_id TEXT NOT NULL,
        text TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('waiting','replied')),
        created_at TEXT NOT NULL, replied_at TEXT,
        UNIQUE(installation_id,parent_task_id,client_request_id));
CREATE TABLE migration_log (
        version INTEGER PRIMARY KEY, previous_version INTEGER NOT NULL,
        applied_at TEXT NOT NULL, backup_path TEXT);
CREATE TABLE member_sessions (
        member_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_hash TEXT NOT NULL,
        lease_hash TEXT NOT NULL, deployment_digest TEXT NOT NULL, tool_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('ready','waiting','busy','stopping','stopped','lost')),
        expires_at REAL NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, stop_reason TEXT);
CREATE TABLE attempt_members (
        attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
        member_id TEXT NOT NULL REFERENCES member_sessions(member_id));
CREATE TABLE role_deployments (
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, workspace TEXT NOT NULL,
        access TEXT NOT NULL CHECK(access IN ('none','read-only','read-write')),
        created_at TEXT NOT NULL, PRIMARY KEY(installation_id,role_id));
CREATE TABLE attempt_inputs (
        input_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL,
        attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
        sequence INTEGER NOT NULL CHECK(sequence>0), request_id TEXT NOT NULL, text TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','sending','accepted','held')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(attempt_id,sequence), UNIQUE(installation_id,generation,request_id));
CREATE TABLE routes (
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
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE route_versions (
        route_id TEXT NOT NULL REFERENCES routes(route_id), route_epoch INTEGER NOT NULL,
        snapshot TEXT NOT NULL, snapshot_digest TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(route_id,route_epoch));
CREATE TABLE source_claims (
        source_namespace TEXT NOT NULL, source_key TEXT NOT NULL,
        route_id TEXT NOT NULL REFERENCES routes(route_id),
        claim_epoch INTEGER NOT NULL CHECK(claim_epoch>0), owner_id TEXT NOT NULL,
        lease_hash TEXT NOT NULL, expires_at REAL NOT NULL, policy TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(source_namespace,source_key));
CREATE TABLE source_admissions (
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
        UNIQUE(source_namespace,source_key), FOREIGN KEY(route_id,route_epoch) REFERENCES route_versions(route_id,route_epoch));
CREATE TABLE legacy_claims (
        admission_id TEXT PRIMARY KEY REFERENCES source_admissions(admission_id),
        claim_epoch INTEGER NOT NULL CHECK(claim_epoch>0), owner_id TEXT NOT NULL,
        lease_hash TEXT NOT NULL, expires_at REAL NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('running','held','retryable','completed')),
        result_digest TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE routing_events (
        event_id TEXT PRIMARY KEY, route_id TEXT NOT NULL REFERENCES routes(route_id),
        admission_id TEXT REFERENCES source_admissions(admission_id),
        kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE result_deliveries (
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
        UNIQUE(result_task_id,destination_namespace,destination_id));
CREATE TABLE delivery_evidence (
        evidence_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL REFERENCES result_deliveries(delivery_id),
        kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX one_active ON installations(state) WHERE state='active';
CREATE UNIQUE INDEX one_running_attempt ON attempts(task_id) WHERE state='running';
CREATE UNIQUE INDEX unique_decision_answer ON decisions(installation_id,answer_id) WHERE answer_id IS NOT NULL;
CREATE UNIQUE INDEX one_live_member ON member_sessions(installation_id,role_id) WHERE state IN ('ready','waiting','busy','stopping');
CREATE INDEX route_admissions_by_route ON source_admissions(route_id,created_at);
CREATE INDEX delivery_pending ON result_deliveries(state,installation_id,created_at);
