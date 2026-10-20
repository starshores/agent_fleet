"""Explicit local administrative transport CLI; no model or legacy discovery."""
from __future__ import annotations

import inspect
import json

from .errors import PlatformError
from .paths import _explicit_path, _check_directory_boundary
from .service import Platform
from .receipt_store import ReceiptStore
from .workbench_commands import load_command_payload


ACTIONS = {
    "configure": "configure_route", "controls": "set_route_controls",
    "claim-source": "claim_source", "renew-source": "renew_source",
    "admit-source": "admit_source", "confirm-source": "confirm_source",
    "claim-legacy": "claim_legacy", "renew-legacy": "renew_legacy",
    "complete-legacy": "complete_legacy", "reconcile-legacy": "reconcile_legacy",
}
QUERY_ACTIONS = ("routes", "admissions", "admission", "deliveries", "delivery")
_CLAIMS = {"claim-source", "claim-legacy"}
_LEASES = {"renew-source", "admit-source", "confirm-source", "renew-legacy", "complete-legacy"}


def execute_command(state_root, action, payload, *, lease_file=None, lease_output=None):
    if action not in ACTIONS or type(payload) is not dict:
        raise PlatformError("invalid_route_command", "Select an explicit routing action and JSON object.")
    if (action in _CLAIMS) != bool(lease_output):
        raise PlatformError("invalid_route_command", "Claim commands require a new --lease-output file; other commands cannot use it.")
    if action in _LEASES:
        if not lease_file:
            raise PlatformError("invalid_route_command", "This action requires the exact --lease-file.")
        lease = load_command_payload(lease_file)
        if set(payload) & {"claim_token", "claim_epoch", "source_claim_token", "source_claim_epoch"}:
            raise PlatformError("invalid_route_command", "Lease credentials must come only from the selected lease file.")
        try:
            prefix = "source_" if action in {"admit-source", "confirm-source"} else ""
            payload = {**payload, prefix + "claim_epoch": lease["claim_epoch"], prefix + "claim_token": lease["claim_token"]}
        except KeyError:
            raise PlatformError("invalid_route_lease", "The selected file does not contain an issued transport lease.") from None
    elif lease_file:
        raise PlatformError("invalid_route_command", "This action does not accept a lease file.")
    platform = Platform(state_root, initialize=False)
    method = getattr(platform, ACTIONS[action])
    try:
        inspect.signature(method).bind(**payload)
    except TypeError:
        raise PlatformError("invalid_route_command", "The routing payload has missing or unsupported fields.") from None
    if action not in _CLAIMS:
        return method(**payload)
    path = _explicit_path(lease_output)
    _check_directory_boundary(path.parent)
    # Reserve a NEW file before obtaining a lease; never overwrite credentials.
    try:
        target = path.open("x", encoding="utf-8")
    except OSError:
        raise PlatformError("lease_output_unavailable", "Choose a new lease file in an existing dedicated local directory.") from None
    try:
        with target:
            issued = method(**payload)
            json.dump(issued, target, ensure_ascii=False, sort_keys=True)
            target.write("\n")
    except BaseException:
        # This invocation created the file exclusively; existing files are untouched.
        path.unlink(missing_ok=True)
        raise
    return {key: value for key, value in {**issued, "lease_file": str(path)}.items()
            if key not in {"claim_token", "text"}}


def query(state_root, resource, *, route_id=None, admission_id=None, installation_id=None, delivery_id=None):
    values = {key: value for key, value in {"route_id": route_id, "admission_id": admission_id,
              "installation_id": installation_id, "delivery_id": delivery_id}.items() if value is not None}
    permitted = {"routes": set(), "admissions": {"route_id"}, "admission": {"admission_id"},
                 "deliveries": {"installation_id"}, "delivery": {"delivery_id"}}
    if resource not in permitted or set(values) - permitted[resource]:
        raise PlatformError("invalid_route_query", "This routing query does not accept the selected filters.")
    if resource in {"admission", "delivery"} and set(values) != permitted[resource]:
        raise PlatformError("invalid_route_query", "This query requires an explicit record identifier.")
    platform = Platform(state_root, readonly=True, initialize=False)
    return getattr(platform, "delivery_status" if resource == "delivery" else resource)(**values)


def receiver_for(platform, delivery_id, receipt_root):
    delivery = platform.delivery_status(delivery_id)
    return ReceiptStore(receipt_root, delivery["destination_namespace"], delivery["destination_id"], initialize=False)


def deliver(state_root, delivery_id, receipt_root, *, sender_id=None, retry_absent=False, operation="send"):
    platform = Platform(state_root, readonly=operation == "query", initialize=False)
    receiver = receiver_for(platform, delivery_id, receipt_root)
    if operation == "query":
        return receiver.query(platform.delivery_envelope(delivery_id))
    if operation == "reconcile":
        return platform.reconcile_delivery(delivery_id, receiver, retry_absent=retry_absent)
    if operation != "send" or not sender_id or retry_absent:
        raise PlatformError("invalid_route_command", "A single send requires an explicit sender; retries require reconciliation.")
    return platform.send_delivery_once(delivery_id, sender_id, receiver)
