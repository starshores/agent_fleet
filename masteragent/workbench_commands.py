"""Explicit, guarded local commands shared by the workbench HTTP and CLI surfaces.

This service only opens existing schema-compatible state. It never initializes,
migrates, loads runtime configuration, starts a model, or imports the legacy bus.
Scope checks execute in the same transaction as each domain mutation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from .errors import PlatformError
from .service import Platform
from .workbench_query import API_VERSION


MAX_COMMAND_BYTES = 512000
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_FIELDS = {
    "submit": frozenset({"generation", "role_id", "text", "request_id"}),
    "input": frozenset({"generation", "task_id", "attempt_id", "text", "request_id"}),
    "answer": frozenset({"generation", "task_id", "decision_id", "expected_revision", "choice", "answer_id"}),
    "stop": frozenset({"generation", "task_id", "attempt_id", "reason"}),
}
_RESULT_FIELDS = {
    "submit": ("task_id", "role_id", "generation", "dispatch_generation", "state", "created_at", "completed_at", "parent_task_id"),
    "input": ("input_id", "role_id", "generation", "attempt_id", "sequence", "state", "created_at", "updated_at"),
    "answer": ("decision_id", "task_id", "generation", "title", "state", "revision", "expires_at", "answer", "created_at", "answered_at"),
    "stop": ("attempt_id", "stop_requested", "termination_confirmed"),
}


def _identifier(value, field):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise PlatformError("invalid_id", f"{field} must be an explicit portable identifier of at most 64 characters.")


def _text(value, field, limit, *, bytes_limit=False):
    if not isinstance(value, str) or not value.strip():
        raise PlatformError("invalid_payload", f"{field} must contain nonempty text.")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PlatformError("invalid_payload", f"{field} must contain valid UTF-8 text.") from exc
    if (len(encoded) if bytes_limit else len(value)) > limit:
        raise PlatformError("invalid_payload", f"{field} exceeds its permitted length.")


def decode_command_payload(raw: bytes) -> dict:
    """Bounded JSON with no duplicate properties or non-JSON numeric constants."""
    if not isinstance(raw, bytes) or len(raw) > MAX_COMMAND_BYTES:
        raise PlatformError("invalid_payload", "Command JSON exceeds the permitted size.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate property")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("invalid constant")

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlatformError("invalid_payload", "Command payload must be a valid UTF-8 JSON object without duplicate properties.") from exc
    if not isinstance(payload, dict):
        raise PlatformError("invalid_payload", "Command payload must be a JSON object.")
    return payload


def load_command_payload(path: str | Path) -> dict:
    with Path(path).open("rb") as source:
        raw = source.read(MAX_COMMAND_BYTES + 1)
    return decode_command_payload(raw)


class WorkbenchCommands:
    def __init__(self, state_root: str | Path):
        self._platform = Platform(state_root, initialize=False)
        self.state_root = self._platform.state_root

    def execute(self, installation_id, action, payload):
        _identifier(installation_id, "installation_id")
        if not isinstance(action, str) or action not in _FIELDS:
            raise PlatformError("invalid_action", "Choose submit, input, answer, or stop.")
        if not isinstance(payload, dict) or set(payload) != _FIELDS[action]:
            raise PlatformError("invalid_payload", "Command payload must contain exactly the required fields.")
        generation = payload["generation"]
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise PlatformError("invalid_generation", "Generation must be a positive integer.")
        for field in ("role_id", "task_id", "attempt_id", "decision_id"):
            if field in payload:
                _identifier(payload[field], field)
        for field in ("request_id", "answer_id"):
            if field in payload:
                _text(payload[field], field, 200)
        if "text" in payload:
            _text(payload["text"], "text", 64000, bytes_limit=True)
        if "reason" in payload:
            _text(payload["reason"], "reason", 2000)
        guards = {"expected_generation": generation}
        if "task_id" in payload:
            guards["expected_task_id"] = payload["task_id"]
        if action == "submit":
            result = self._platform.submit(installation_id, payload["role_id"], payload["text"], payload["request_id"], **guards)
        elif action == "input":
            result = self._platform.submit_runtime_input(installation_id, payload["attempt_id"], payload["text"], payload["request_id"], **guards)
        elif action == "answer":
            revision = payload["expected_revision"]
            if type(revision) is not int or not 0 <= revision <= 2**63 - 1:
                raise PlatformError("invalid_revision", "Decision revision must be a nonnegative integer.")
            if not isinstance(payload["choice"], str) or payload["choice"] not in ("approve", "reject"):
                raise PlatformError("invalid_answer", "Decision choice must be approve or reject.")
            result = self._platform.answer_decision(installation_id, payload["decision_id"], revision, payload["choice"], payload["answer_id"], **guards)
        else:
            result = self._platform.request_runtime_stop(installation_id, payload["attempt_id"], payload["reason"], **guards)
        public_result = {key: result[key] for key in _RESULT_FIELDS[action]}
        return {"api_version": API_VERSION,
                "source": {"kind": "masteragent", "state_root": str(self.state_root),
                           "installation_id": installation_id, "generation": generation},
                "observed_at": datetime.fromtimestamp(time.time(), timezone.utc).isoformat(),
                "revision": None, "data": {"action": action, "result": public_result}}
