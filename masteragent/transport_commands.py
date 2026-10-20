"""Explicit local source and transport commands; never load model configuration."""
from __future__ import annotations

import json
import math
from pathlib import Path
import signal

from .errors import PlatformError
from .paths import _check_directory_boundary, _explicit_path, _overlap
from .receipt_store import ReceiptStore
from .routing import _identifier, _source_key
from .service import Platform
from .source_store import SourceStore
from .workbench_commands import load_command_payload


SOURCE_COMMANDS = frozenset({"source-init", "source-publish", "source-query"})
TRANSPORT_COMMANDS = frozenset({"transport-bind", "transport-once", "transport-serve"})
_CONFIG_FIELDS = frozenset({
    "route_id", "owner_id", "source_root", "source_namespace",
    "receipt_root", "destination_namespace", "destination_id",
})


def load_transport_config(path):
    """Read one strict configuration; endpoints are opened only by the caller."""
    value = load_command_payload(path)
    if set(value) != _CONFIG_FIELDS or any(type(item) is not str for item in value.values()):
        raise PlatformError("invalid_transport_config", "Transport configuration requires exactly the seven documented text fields.")
    for key in _CONFIG_FIELDS - {"source_root", "receipt_root"}:
        _identifier(value[key])
    result = dict(value)
    for key in ("source_root", "receipt_root"):
        root = _explicit_path(value[key])
        _check_directory_boundary(root)
        result[key] = str(root)
    if _overlap(Path(result["source_root"]), Path(result["receipt_root"])):
        raise PlatformError("transport_path_overlap", "Source and receipt stores require separate dedicated directories.")
    return result


def load_source_id(path):
    """Preserve the JSON scalar type; never stringify identifiers."""
    def nonfinite(_value):
        raise ValueError("nonfinite JSON constant")

    try:
        with Path(path).open("rb") as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ValueError("too large")
        value = json.loads(raw.decode("utf-8"), parse_constant=nonfinite)
        _source_key(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlatformError("invalid_source_id", "Source ID file must contain one valid JSON string or integer.") from exc
    return value


def execute_source(command, source_root, source_namespace, *, input_file=None,
                   source_id_file=None, limit=None):
    if command not in SOURCE_COMMANDS:
        raise PlatformError("invalid_source_command", "Select an explicit source operation.")
    payload = None
    source_id = None
    if command == "source-publish":
        if not input_file or source_id_file is not None or limit is not None:
            raise PlatformError("invalid_source_command", "Publish requires only an input JSON file.")
        payload = load_command_payload(input_file)
        if not {"source_id", "text"} <= set(payload) or set(payload) - {"source_id", "text", "kind"}:
            raise PlatformError("invalid_source_command", "Publish JSON requires source_id and text, with optional kind.")
    elif command == "source-query":
        if input_file is not None or (source_id_file is not None and limit is not None):
            raise PlatformError("invalid_source_command", "Query selects one typed source ID or a pending limit.")
        if source_id_file is not None:
            source_id = load_source_id(source_id_file)
        elif limit is not None and (type(limit) is not int or not 1 <= limit <= 1000):
            raise PlatformError("invalid_source_limit", "Pending limit must be an integer from 1 through 1000.")
    elif input_file is not None or source_id_file is not None or limit is not None:
        raise PlatformError("invalid_source_command", "Initialization does not accept message arguments.")
    store = SourceStore(source_root, source_namespace, initialize=command == "source-init")
    if command == "source-init":
        return {"initialized": True, "source": store.descriptor()}
    if command == "source-publish":
        return store.publish(**payload)
    return store.get(source_id) if source_id_file is not None else store.pending(100 if limit is None else limit)


def _serve(worker, max_seconds):
    """Let Ctrl-C request an orderly stop; the worker owns cleanup and statistics."""
    previous = signal.getsignal(signal.SIGINT)

    def interrupted(_signum, _frame):
        worker.stop()

    signal.signal(signal.SIGINT, interrupted)
    try:
        return worker.run(max_seconds=max_seconds)
    finally:
        try:
            worker.stop()
        finally:
            signal.signal(signal.SIGINT, previous)


def execute_transport(command, state_root, config, *, expected_epoch=None, max_seconds=None):
    if command not in TRANSPORT_COMMANDS:
        raise PlatformError("invalid_transport_command", "Select an explicit transport operation.")
    if command == "transport-bind":
        if type(expected_epoch) is not int or not 1 <= expected_epoch <= 2**53 - 1 or max_seconds is not None:
            raise PlatformError("invalid_transport_command", "Binding requires the current positive route epoch only.")
    elif expected_epoch is not None:
        raise PlatformError("invalid_transport_command", "Only binding accepts a route epoch.")
    if max_seconds is not None and (command != "transport-serve" or type(max_seconds) not in (int, float)
                                   or not math.isfinite(max_seconds) or max_seconds <= 0):
        raise PlatformError("invalid_transport_limit", "Serving duration must be finite and positive.")
    # Validate again for callers supplying a dictionary rather than the CLI loader.
    if (type(config) is not dict or set(config) != _CONFIG_FIELDS
            or any(type(item) is not str for item in config.values())):
        raise PlatformError("invalid_transport_config", "Transport configuration has missing or unsupported fields.")
    for key in _CONFIG_FIELDS - {"source_root", "receipt_root"}:
        _identifier(config[key])
    state = _explicit_path(state_root)
    source_root = _explicit_path(config["source_root"])
    receipt_root = _explicit_path(config["receipt_root"])
    for root in (state, source_root, receipt_root):
        _check_directory_boundary(root)
    if any(_overlap(left, right) for left, right in (
            (state, source_root), (state, receipt_root), (source_root, receipt_root))):
        raise PlatformError("transport_path_overlap", "Platform state, source and receipt stores require separate directories.")
    platform = Platform(state, initialize=False)
    source = SourceStore(source_root, config["source_namespace"], initialize=False)
    receiver = ReceiptStore(receipt_root, config["destination_namespace"], config["destination_id"], initialize=False)
    if command == "transport-bind":
        return platform.bind_route_endpoints(config["route_id"], expected_epoch, source, receiver)
    from .transport import TransportWorker
    worker = TransportWorker(platform, config["route_id"], config["owner_id"], source, receiver)
    if command == "transport-once":
        return worker.run_once()
    return _serve(worker, max_seconds)
