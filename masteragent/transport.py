"""Explicit local transport; source admission and result sending never execute a model.

Three authorities commit independently: source, platform, receiver. Every
interrupted handoff resumes by its original identity. The two transport lanes
have separate leases and event waits; execution belongs to a separate worker.
"""
from __future__ import annotations

import math
import threading
import time

from .errors import PlatformError
from .routing import _identifier, _json, _source_key
from .source_store import validate_envelope
from .state_waiter import StateWaiter


class _LeaseKeeper:
    """Renew one exact bearer lease during endpoint IO, without holding a DB lock."""

    def __init__(self, renew, seconds):
        self.renew = renew
        self.interval = seconds / 3
        self.done = threading.Event()
        self.failure = None
        self.thread = threading.Thread(target=self._run, name="ma-transport-lease", daemon=True)

    def _run(self):
        while not self.done.wait(self.interval):
            try:
                self.renew()
            except Exception as exc:
                self.failure = exc
                return

    def check(self):
        if self.failure is not None:
            raise PlatformError("transport_lease_lost", "Transport renewal failed; no further handoff is authorized.")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.done.set()
        self.thread.join()


class TransportWorker:
    """One explicitly bound local source and receiver, with no native runtime.

    ``run_once`` is bounded and suitable for manual recovery. ``run`` uses two
    independent Windows notification consumers. Errors report finite codes,
    never endpoint exception messages, text bodies, or bearer tokens.
    """

    def __init__(self, platform, route_id, owner_id, source, receiver, *,
                 waiter_factory=StateWaiter, on_state=None):
        self.platform = platform
        self.route_id, self.owner_id = _identifier(route_id), _identifier(owner_id)
        self.source, self.receiver = source, receiver
        self.waiter_factory, self.on_state = waiter_factory, on_state
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._waiters = []
        self._source_cursor = None
        self._retry = {"source": {}, "delivery": {}}
        self._running = False
        self._wake_errors = []
        self._binding = self.platform.route_endpoints(self.route_id)
        if self._binding is None:
            raise PlatformError("route_endpoints_unbound", "Bind this route before starting transport.")
        self._preflight()

    def _preflight(self):
        if (_json(self.source.descriptor()) != _json(self._binding["source_binding"])
                or _json(self.receiver.descriptor()) != _json(self._binding["destination_binding"])):
            raise PlatformError("route_endpoint_mismatch", "Transport must use the route's original endpoint instances.")

    def _route(self):
        for route in self.platform.routes():
            if route["route_id"] == self.route_id:
                return route
        raise PlatformError("route_not_found", "The selected transport route no longer exists.")

    def _checkpoint(self, keeper=None):
        if keeper is not None:
            keeper.check()
        if self._stop.is_set():
            raise PlatformError("transport_stopped", "Transport has stopped accepting new handoffs.")

    def stop(self):
        """Fence new actions and wake idle lanes; this does not claim IO has exited."""
        self._stop.set()
        with self._lock:
            for waiter in self._waiters:
                try:
                    waiter.wake()
                except Exception:
                    # A failed SetEvent must not skip the other lane or the
                    # join/close phase. Bounded native waits below provide a
                    # fallback stop check without querying either database.
                    self._wake_errors.append("transport_wake_failed")
                    self._wake_errors[:] = self._wake_errors[-2:]

    @staticmethod
    def _admission_summary(admission):
        return {key: admission[key] for key in (
            "admission_id", "task_id", "source_id", "route_id", "route_epoch", "source_confirmed")}

    def transfer_source(self, source_id):
        self._checkpoint()
        record = self.source.get(source_id)
        envelope = record["envelope"]
        validate_envelope(envelope)
        binding = self._binding["source_binding"]
        if (envelope["source_namespace"] != binding["source_namespace"]
                or envelope["source_instance"] != binding["source_instance"]
                or _source_key(envelope["source_id"]) != _source_key(source_id)):
            raise PlatformError("source_scope", "Source envelope does not belong to this bound transport.")
        self._preflight()
        prior = self.platform.source_admission(self.route_id, source_id)
        if prior is not None and any(prior[key] != envelope[key] for key in ("source_namespace", "kind", "text")):
            raise PlatformError("source_conflict", "Source content differs from its immutable platform admission.")
        if record["admission_id"] is not None and (
                prior is None or record["admission_id"] != prior["admission_id"]):
            raise PlatformError("source_confirmation_conflict", "Source confirmation has no matching platform admission.")
        if record["transport_completed"]:
            if prior is None or not prior["source_confirmed"]:
                raise PlatformError("source_confirmation_conflict", "Completed source handoff has no confirmed platform admission.")
            return self._admission_summary(prior)
        route = self._route()
        if (prior or route)["owner_lane"] != "v3":
            raise PlatformError("transport_lane", "This controlled source transport requires the V3 execution lane.")
        self._checkpoint()
        claim = self.platform.claim_source(self.route_id, source_id, self.owner_id)
        args = (self.route_id, source_id, self.owner_id, claim["claim_epoch"], claim["claim_token"])
        try:
            # Read again after claiming: another connector may have completed
            # admission between our first lookup and this lease acquisition.
            prior = self.platform.source_admission(self.route_id, source_id)
            if prior is not None and prior["owner_lane"] != "v3":
                raise PlatformError("transport_lane", "This transport cannot confirm legacy execution ownership.")
            # Use the version whose lane we inspected. A concurrent route
            # edit must reject new admission rather than silently retarget it.
            epoch = prior["route_epoch"] if prior else route["route_epoch"]
            with _LeaseKeeper(lambda: self.platform.renew_source(*args),
                              claim["lease_seconds"]) as keeper:
                self._checkpoint(keeper)
                admission = self.platform.admit_source(
                    self.route_id, epoch, source_id, envelope["text"], self.owner_id,
                    claim["claim_epoch"], claim["claim_token"], kind=envelope["kind"],
                    source=self.source, receiver=self.receiver)
                self._checkpoint(keeper)
                confirmed = self.source.confirm(source_id, admission["admission_id"], envelope["envelope_sha256"])
                if (not confirmed["source_confirmed"]
                        or confirmed["admission_id"] != admission["admission_id"]
                        or confirmed["envelope"] != envelope):
                    raise PlatformError("source_confirmation_conflict", "Source did not confirm this exact handoff.")
                self._checkpoint(keeper)
                admission = self.platform.confirm_source(
                    admission["admission_id"], self.owner_id, claim["claim_epoch"], claim["claim_token"])
                self._checkpoint(keeper)
                self.source.complete(source_id, admission["admission_id"])
                return self._admission_summary(admission)
        finally:
            try:
                self.platform.release_source(*args)
            except PlatformError:
                # An expired or replaced lease belongs to its next owner.
                pass

    def send_result(self, delivery_id):
        self._checkpoint()
        current = self.platform.delivery_status(delivery_id)
        if current["route_id"] != self.route_id:
            raise PlatformError("delivery_scope", "Transport cannot send another route's result.")
        claim = self.platform.claim_delivery(delivery_id, self.owner_id, self.receiver)
        args = (delivery_id, self.owner_id, claim["claim_epoch"], claim["claim_token"])
        policy = self.platform.admission(claim["admission_id"])["policy"]
        with _LeaseKeeper(lambda: self.platform.renew_delivery(*args), policy["lease_seconds"]) as keeper:
            try:
                self._checkpoint(keeper)
                envelope = self.platform.begin_delivery(*args)
                self._checkpoint(keeper)
                receipt = self.receiver.receive(envelope)
                self._checkpoint(keeper)
                return self.platform.complete_delivery(*args, receipt)
            except Exception:
                # Receive may have committed before losing its response. Only
                # the original receiver can establish a later receipt/absence.
                try:
                    return self.platform.hold_delivery(
                        *args, reason="sender_stopped" if self._stop.is_set() else "receive_unconfirmed")
                except PlatformError:
                    return self.platform.delivery_status(delivery_id)

    def _error(self, lane, identity, exc):
        code = exc.code if isinstance(exc, PlatformError) else "transport_io_failed"
        return {"lane": lane, "id": identity, "code": code}

    def _scan(self, lane):
        report = {"sources": [], "deliveries": [], "errors": []}
        now = time.monotonic()
        self._retry[lane] = {key: deadline for key, deadline in self._retry[lane].items() if deadline > now}
        more = False
        if lane == "source":
            records = self.source.pending(limit=100, after_id=self._source_cursor)
            more = len(records) == 100
            self._source_cursor = records[-1]["envelope"]["source_id"] if more else None
            items = [(r["envelope"]["source_id"], self.transfer_source) for r in records]
        else:
            candidates = self.platform.delivery_schedule(self.route_id)["ready"]
            # An uncertain expired send is fenced by claim_delivery; it is
            # never automatically queried, requeued, or sent again.
            items = [(r["delivery_id"], self.send_result) for r in candidates
                     if self._retry[lane].get(r["delivery_id"], 0) <= now][:100]
            more = len(items) == 100
        for identity, action in items:
            if self._stop.is_set():
                break
            key = _source_key(identity) if lane == "source" else identity
            if self._retry[lane].get(key, 0) > now:
                continue
            try:
                value = action(identity)
                report["sources" if lane == "source" else "deliveries"].append(value)
                self._retry[lane].pop(key, None)
            except Exception as exc:
                report["errors"].append(self._error(lane, identity, exc))
                # This is a known failed handoff's retry deadline, not an
                # idle filesystem scan. It also avoids self-notification spin.
                self._retry[lane][key] = time.monotonic() + 5
        return report, more

    def run_once(self):
        if self._running:
            raise PlatformError("transport_busy", "Use a separate instance for a manual transport step.")
        report = {"sources": [], "deliveries": [], "errors": []}
        for lane in ("source", "delivery"):
            if self._stop.is_set():
                break
            try:
                result, _ = self._scan(lane)
            except Exception as exc:
                result = {"sources": [], "deliveries": [], "errors": [self._error(lane, self.route_id, exc)]}
            for key in report:
                report[key].extend(result[key])
        return report

    def _deadline(self, lane):
        now = time.monotonic()
        deadlines = [max(.05, value - now) for value in self._retry[lane].values()]
        if lane == "delivery":
            delay = self.platform.delivery_schedule(self.route_id)["next_wake_seconds"]
            if delay is not None:
                deadlines.append(delay)
        return min(deadlines, default=86400)

    def _wait(self, waiter, timeout):
        deadline = time.monotonic() + timeout
        while not self._stop.is_set():
            remaining = max(0., deadline - time.monotonic())
            if waiter.wait(min(30., remaining)) or remaining <= 30.:
                return
            # This timeout checks shutdown only. It does not rescan a store.

    def run(self, max_seconds=None):
        if max_seconds is not None and (
                type(max_seconds) not in (float, int) or not math.isfinite(max_seconds) or max_seconds <= 0):
            raise PlatformError("invalid_transport_duration", "Run duration must be finite and positive.")
        with self._lock:
            if self._running:
                raise PlatformError("transport_busy", "This transport instance is already running.")
            self._running = True
        totals = {"sources": 0, "deliveries": 0, "errors": [], "stopped": False}
        failures = []

        def consume(lane):
            try:
                extra = (self.source.root,) if lane == "source" else ()
                with self.waiter_factory(self.platform.state_root, additional_roots=extra) as waiter:
                    with self._lock:
                        self._waiters.append(waiter)
                    try:
                        while not self._stop.is_set():
                            waiter.arm()
                            result, more = self._scan(lane)
                            with self._lock:
                                totals["sources"] += len(result["sources"])
                                totals["deliveries"] += len(result["deliveries"])
                                totals["errors"] = (totals["errors"] + result["errors"])[-100:]
                            if self.on_state:
                                self.on_state({"lane": lane, "phase": "scanned", **result})
                            if not self._stop.is_set() and not more:
                                timeout = self._deadline(lane)
                                if self.on_state:
                                    self.on_state({"lane": lane, "phase": "waiting"})
                                self._wait(waiter, timeout)
                    finally:
                        with self._lock:
                            self._waiters.remove(waiter)
            except Exception as exc:
                failures.append(exc)
                self.stop()

        threads = [threading.Thread(target=consume, args=(lane,), name="ma-transport-" + lane)
                   for lane in ("source", "delivery")]
        try:
            for thread in threads:
                thread.start()
            self._stop.wait(max_seconds)
        finally:
            self.stop()
            for thread in threads:
                if thread.ident is not None:
                    thread.join()
            with self._lock:
                self._running = False
        if failures:
            raise PlatformError("transport_worker_failed", "A transport lane stopped; inspect the selected local stores.") from failures[0]
        if self._wake_errors:
            raise PlatformError("transport_stop_failed", "Stop notification failed; transport threads have now exited.")
        totals["stopped"] = True
        return totals
