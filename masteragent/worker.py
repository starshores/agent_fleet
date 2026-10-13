"""A foreground deployment-scoped member with event-driven task reception.

This controller owns no desktop session and performs no automatic registration.
Each task still uses its own runtime process and immutable Attempt lease.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time

from .errors import PlatformError
from .runtime import CodexRunner, prepare_deployment
from .state_waiter import StateWaiter


class MemberWorker:
    def __init__(self, platform, config, actor, tool_id, *, runner=None,
                 waiter_factory=StateWaiter, on_state=None):
        self.platform, self.config, self.actor, self.tool_id = platform, config, actor, tool_id
        self.runner = runner if runner is not None else CodexRunner(platform, config)
        self.waiter_factory, self.on_state = waiter_factory, on_state
        self._stop = threading.Event()
        self._waiter = None
        self._member = self._token = None
        self._deadline = None
        self._used = False

    def stop(self, reason="Local member stop requested"):
        """Persist the stop fence before asking a running supervisor to stop."""
        self._stop.set()
        try:
            if self._member is not None:
                self.platform.request_member_stop(self.actor.installation_id,
                                                   self._member, reason)
        finally:
            if self._waiter is not None:
                self._waiter.wake()

    def _observe(self, view):
        if self.on_state is not None:
            self.on_state(view)

    def _heartbeat(self):
        return self.platform.renew_member(self.actor, self._member, self._token,
                                          self.config.lease_seconds)

    def _should_stop(self):
        if self._deadline is not None and time.monotonic() >= self._deadline and not self._stop.is_set():
            self.stop("Member serving time limit reached")
        if self._stop.is_set():
            return True
        try:
            view = self.platform.member_status(self.actor, self._member, self._token)
            return view["state"] in {"stopping", "stopped", "lost"}
        except PlatformError:
            return True

    def run(self, *, max_tasks=None, max_seconds=None):
        if self._used:
            raise PlatformError("member_reuse", "Create a fresh member controller for each serving session.")
        if max_tasks is not None and (type(max_tasks) is not int or max_tasks < 1):
            raise PlatformError("invalid_member_limit", "max_tasks must be a positive integer.")
        if max_seconds is not None and (type(max_seconds) not in (int, float)
                or not math.isfinite(max_seconds) or max_seconds <= 0):
            raise PlatformError("invalid_member_limit", "max_seconds must be finite and positive.")
        if self.on_state is not None and not callable(self.on_state):
            raise PlatformError("invalid_member_observer", "The member observer must be callable.")
        self._used = True
        if self._stop.is_set():
            return {"member_id": None, "state": "not_started", "attempted_tasks": 0,
                    "completed_tasks": 0, "reason": "stop_requested"}
        config, platform, actor = self.config, self.platform, self.actor
        config.validate_scope(actor.installation_id, actor.role_id, actor.generation)
        context = platform.context_for(actor)
        tool = context["tools"].get(self.tool_id)
        if tool is None or tool["adapter"] != config.adapter_id or config.adapter_id not in platform.external_adapters:
            raise PlatformError("runtime_binding_mismatch", "The explicit member tool must match its deployment adapter.")
        # Fail before becoming ready if deployment or the actual watcher cannot
        # be prepared. A successful version probe is not model connectivity.
        prepare_deployment(platform, config, bind=True)
        self.runner.probe(config)
        digest = hashlib.sha256(json.dumps({"runtime": config.as_public_dict(), "tool_id": self.tool_id},
                                          sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        attempted = completed = 0
        termination_confirmed = True
        reason, last_outcome, final = "stopped", None, None
        active = None
        self._deadline = time.monotonic() + max_seconds if max_seconds is not None else None
        with self.waiter_factory(platform.state_root) as waiter:
            self._waiter = waiter
            try:
                waiter.arm()
                issued = platform.start_member(actor, digest, self.tool_id, config.lease_seconds)
                self._member, self._token = issued["member_id"], issued["member_token"]
                self._observe({key: value for key, value in issued.items() if key != "member_token"})
                next_heartbeat = time.monotonic() + config.lease_seconds / 3
                while True:
                    # Reset the change notification BEFORE checking state. Any
                    # subsequent write remains visible even before wait starts.
                    waiter.arm()
                    if self._should_stop():
                        reason = "stop_requested"
                        break
                    if time.monotonic() >= next_heartbeat:
                        self._heartbeat()
                        next_heartbeat = time.monotonic() + config.lease_seconds / 3
                    active = platform.claim_member_task(actor, self._member, self._token,
                                                         config.lease_seconds)
                    if active is None:
                        self._observe(platform.member_waiting(actor, self._member, self._token, True))
                        timeout = max(0.001, next_heartbeat - time.monotonic())
                        if self._deadline is not None:
                            timeout = min(timeout, max(0.001, self._deadline - time.monotonic()))
                        waiter.wait(timeout)
                        continue
                    self._observe(platform.member_status(actor, self._member, self._token))
                    attempted += 1
                    termination_confirmed = False
                    last_outcome = self.runner.run_claimed(actor, active, cancelled=self._should_stop,
                                                            heartbeat_extra=self._heartbeat)
                    termination_confirmed = (isinstance(last_outcome, dict)
                                             and last_outcome.get("termination_confirmed") is True)
                    if not termination_confirmed:
                        reason = "runtime_termination_unconfirmed"
                        break
                    if last_outcome.get("task_state") != "completed":
                        reason = "runtime_requires_attention"
                        break
                    completed += 1
                    active = None
                    next_heartbeat = time.monotonic()  # Refresh before another task.
                    if max_tasks is not None and attempted >= max_tasks:
                        reason = "task_limit"
                        break
            except KeyboardInterrupt:
                reason = "interrupted"
                if self._member is not None:
                    self.stop("Member interrupted by local operator")
            except Exception as exc:
                reason = exc.code if isinstance(exc, PlatformError) else "member_failed"
            finally:
                if self._member is not None:
                    if active is not None:
                        try:
                            platform.fence_uncertain(actor, active["attempt_id"], active["lease_token"],
                                                     "member_controller_stopped")
                        except PlatformError:
                            pass  # A stop/lease fence may already own the transition.
                    try:
                        final = platform.finish_member(actor, self._member, self._token,
                                                       termination_confirmed=termination_confirmed)
                    except PlatformError:
                        final = {"state": "unconfirmed"}
                self._waiter = None
        if self._member is None:
            raise PlatformError(reason, "The member did not start; no runtime task was launched.")
        return {"member_id": self._member, "state": final["state"],
                "attempted_tasks": attempted, "completed_tasks": completed,
                "reason": reason, "last_outcome": last_outcome,
                "model_connectivity": "not_independently_verified"}
