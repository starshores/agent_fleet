"""Crash, concurrency and restart checks through public coordinator APIs.

Only synthetic packages, temporary state roots and short-lived Python children
are used. These checks establish coordinator behavior, not an OS sandbox or an
external tool's exactly-once delivery guarantee.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from masteragent.service import Actor, Platform, PlatformError


CHILD = r"""
import json
import os
import sys
from masteragent.service import Actor, Platform, PlatformError

settings = json.loads(sys.stdin.readline())
actor = Actor(**settings['actor'])
mode = settings['mode']

def emit(value):
    print(json.dumps(value), flush=True)

platform = Platform(settings['state'], clock=lambda: settings['now'])
emit({'ready': True})
if sys.stdin.readline().strip() != 'claim':
    raise RuntimeError('missing synthetic claim signal')
try:
    attempt = platform.claim(actor, settings['task_id'], 'echo', ttl_seconds=10)
    if mode == 'effect':
        with open(settings['ledger'], 'a', encoding='utf-8') as ledger:
            ledger.write('synthetic effect\n')
            ledger.flush()
            os.fsync(ledger.fileno())
    emit({'attempt': attempt})
except PlatformError as exc:
    emit({'error': exc.code})
if mode in ('crash', 'effect'):
    sys.stdin.read()
"""


class FaultTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='masteragent-v3-fault-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state = self.root / 'state'
        self.now = 1000.0
        self.platform = Platform(self.state, clock=lambda: self.now)
        self.make_installation('a')
        self.platform.activate('a')
        self.actor = self.platform.bind('a', 'worker')

    def make_installation(self, name):
        workspace = self.root / ('workspace-' + name)
        workspace.mkdir()
        package = self.root / (name + '.json')
        package.write_text(json.dumps({
            'schema_version': 1, 'platform_api': 1,
            'package_id': name, 'version': '1.0.0',
            'roles': [{'role_id': 'worker', 'display_name': 'Worker',
                       'instructions': name + ' synthetic role', 'tools': ['echo']}],
            'knowledge': {'marker': name},
            'tools': {'echo': {'adapter': 'echo', 'description': 'Synthetic text echo'}},
            'workflows': {},
        }), encoding='utf-8')
        self.platform.install(name, package, workspace)

    def submit(self, text='synthetic task'):
        return self.platform.submit('a', 'worker', text)

    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(PlatformError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    @staticmethod
    def cleanup_child(child):
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)

    def child(self, mode, task):
        child = subprocess.Popen(
            [sys.executable, '-B', '-u', '-c', CHILD],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        self.addCleanup(self.cleanup_child, child)
        child.stdin.write(json.dumps({
            'state': str(self.state), 'actor': asdict(self.actor),
            'task_id': task['task_id'], 'now': self.now, 'mode': mode,
            'ledger': str(self.root / 'synthetic-effect-ledger.txt'),
        }) + '\n')
        child.stdin.flush()
        return child

    def read_message(self, child):
        # A bounded read keeps a broken child from hanging the test runner.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(child.stdout.readline)
        try:
            line = future.result(timeout=15)
        except BaseException:
            child.kill()
            child.wait(timeout=10)
            raise
        finally:
            pool.shutdown(wait=True)
        if not line:
            _, stderr = child.communicate(timeout=10)
            self.fail('Synthetic child exited before replying: ' + stderr)
        return json.loads(line)

    @staticmethod
    def signal(child, text):
        child.stdin.write(text + '\n')
        child.stdin.flush()

    def test_killed_claim_requires_reconciliation_and_fences_late_completion(self):
        task = self.submit()
        child = self.child('crash', task)
        self.assertEqual({'ready': True}, self.read_message(child))
        self.signal(child, 'claim')
        abandoned = self.read_message(child)['attempt']
        self.assertEqual('running', self.platform.task_for(self.actor, task['task_id'])['state'])
        child.kill()
        child.communicate(timeout=10)

        self.now += 11
        restarted = Platform(self.state, clock=lambda: self.now)
        expired = restarted.expire_attempts('a')
        self.assertEqual([task['task_id']], [row['task_id'] for row in expired])
        self.assertEqual('needs_reconcile', restarted.task_for(self.actor, task['task_id'])['state'])
        self.assertEqual([], restarted.results('a'))
        self.assert_error('task_not_queued', restarted.claim, self.actor, task['task_id'], 'echo')
        self.assert_error('unfinished_work', restarted.deactivate, 'a')
        with self.assertRaises(PlatformError):
            restarted.reconcile_task('a', task['task_id'], 'retry', 'No proof of safe retry')
        self.assertEqual('needs_reconcile', restarted.task_for(self.actor, task['task_id'])['state'])

        restarted.reconcile_task('a', task['task_id'], 'retry',
                                 'Synthetic child died before executing any tool', safe_to_retry=True)
        replacement = restarted.bind('a', 'worker')
        attempt = restarted.claim(replacement, task['task_id'], 'echo')
        self.assertNotEqual(abandoned['attempt_id'], attempt['attempt_id'])
        self.assert_error('lease_denied', restarted.complete, replacement,
                          attempt['attempt_id'], abandoned['lease_token'], output='old lease under new actor')
        result = restarted.complete(replacement, attempt['attempt_id'], attempt['lease_token'], output='new owner result')
        self.assert_error('invalid_actor', restarted.complete, self.actor,
                          abandoned['attempt_id'], abandoned['lease_token'], output='late old result')
        self.assertEqual([result], restarted.results('a'))
        self.assertEqual(2, len(restarted.attempts('a', task['task_id'])))
        self.assertEqual([], restarted.expire_attempts('a'))

    def test_effect_before_worker_crash_is_not_repeated_or_fabricated_as_success(self):
        task = self.submit('single durable synthetic effect')
        child = self.child('effect', task)
        self.assertEqual({'ready': True}, self.read_message(child))
        self.signal(child, 'claim')
        self.assertIn('attempt', self.read_message(child))
        ledger = self.root / 'synthetic-effect-ledger.txt'
        self.assertEqual('synthetic effect\n', ledger.read_text(encoding='utf-8'))
        child.kill()
        child.communicate(timeout=10)

        self.now += 11
        restarted = Platform(self.state, clock=lambda: self.now)
        restarted.expire_attempts('a')
        self.assertEqual('needs_reconcile', restarted.task_for(self.actor, task['task_id'])['state'])
        self.assert_error('task_not_queued', restarted.claim, self.actor, task['task_id'], 'echo')
        self.assert_error('unsafe_retry', restarted.reconcile_task, 'a', task['task_id'],
                          'retry', 'The effect may already have occurred')
        self.assertEqual([], restarted.expire_attempts('a'))
        self.assertEqual([], restarted.results('a'))
        restarted.reconcile_task('a', task['task_id'], 'cancel',
                                 'Ledger confirms one effect; do not repeat uncertain execution')
        self.assertEqual('cancelled', restarted.task_for(self.actor, task['task_id'])['state'])
        self.assertEqual('synthetic effect\n', ledger.read_text(encoding='utf-8'))
        self.assertEqual([], restarted.results('a'))
        self.assertEqual(1, len(restarted.attempts('a', task['task_id'])))

    def test_independent_processes_can_commit_only_one_claim(self):
        task = self.submit()
        children = [self.child('race', task), self.child('race', task)]
        for child in children:
            self.assertEqual({'ready': True}, self.read_message(child))
        for child in children:
            self.signal(child, 'claim')
        responses = [self.read_message(child) for child in children]
        self.assertEqual(1, sum('attempt' in reply for reply in responses), responses)
        self.assertEqual(['task_not_queued'], [reply['error'] for reply in responses if 'error' in reply])
        self.assertEqual(1, len(self.platform.attempts('a', task['task_id'])))
        self.assertEqual([], self.platform.results('a'))

    def test_terminal_outbox_survives_restart_and_acknowledgement_keeps_evidence(self):
        task = self.submit('durable result')
        attempt = self.platform.claim(self.actor, task['task_id'], 'echo')
        result = self.platform.complete(self.actor, attempt['attempt_id'], attempt['lease_token'], output='durable result')
        before = self.platform.outbox('a')
        self.assertTrue(before)
        restarted = Platform(self.state, clock=lambda: self.now)
        self.assertEqual(before, restarted.outbox('a'))
        for event in before:
            restarted.ack_outbox('a', event['event_id'])
        self.assertEqual([], restarted.outbox('a'))
        self.assertEqual([result], restarted.results('a'))
        delivered = restarted.outbox('a', include_delivered=True)
        self.assertEqual({item['event_id'] for item in before}, {item['event_id'] for item in delivered})
        for event in before:
            restarted.ack_outbox('a', event['event_id'])
        self.assertEqual(delivered, restarted.outbox('a', include_delivered=True))
        again = Platform(self.state, readonly=True, clock=lambda: self.now)
        self.assertEqual(delivered, again.outbox('a', include_delivered=True))
        self.assertEqual([result], again.results('a'))

    def test_business_switch_rejects_old_completion_and_old_decision(self):
        task = self.submit('old approval')
        decision = self.platform.ask_decision(self.actor, task['task_id'], 'Allow synthetic task?', 'decision-1')
        work = self.submit('old attempt')
        attempt = self.platform.claim(self.actor, work['task_id'], 'echo', ttl_seconds=10)
        self.now += 11
        self.platform.expire_attempts('a')
        self.platform.reconcile_task('a', work['task_id'], 'cancel', 'Retire uncertain synthetic work')
        self.make_installation('b')
        self.platform.deactivate('a')
        self.platform.activate('b')
        actor_b = self.platform.bind('b', 'worker')
        self.assertEqual({'marker': 'b'}, self.platform.context_for(actor_b)['knowledge'])
        self.assertEqual([], self.platform.decisions('b'))
        self.platform.deactivate('b')
        self.platform.activate('a')
        fresh = self.platform.bind('a', 'worker')
        before = self.platform.history('a')
        self.assert_error('invalid_actor', self.platform.complete, self.actor,
                          attempt['attempt_id'], attempt['lease_token'], output='old generation output')
        self.assert_error('stale_decision', self.platform.answer_decision, 'a', decision['decision_id'],
                          decision['revision'], 'approve', 'late-answer')
        after = self.platform.history('a')
        # Rejected owner callbacks may append a diagnostic audit event, but
        # cannot change task/result/decision state or enqueue business work.
        for key in ('tasks', 'results', 'notifications', 'decisions', 'outbox'):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual([], self.platform.decisions('a'))
        self.assertEqual([], self.platform.results('a'))
        self.assertGreater(fresh.generation, self.actor.generation)


if __name__ == '__main__':
    unittest.main()
