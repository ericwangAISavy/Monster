"""Serialized standalone access to the shared ledger, never SQLite on the Qt thread."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from monster.mission.effects import EffectCoordinator
from monster.mission.repository import Repository


@dataclass(frozen=True)
class StandaloneSession:
    task_ref: str
    generation: int
    instruction: str
    approve_each_step: bool
    hands_free: bool


class StandaloneJournal(QObject):
    replied = Signal(object)

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.directory = Path(directory)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='monster-journal')
        self.repository = self.effects = self.session = None
        self.reviewed = {}
        self.failure = False
        self.closed = False

    def _submit(self, operation, generation, nonce=None, *, terminal=False):
        def run():
            try:
                value = operation()
                reply = {'allowed': True, **({'unresolved': bool(value)} if terminal else {'effect_id': value})}
            except (ValueError, TypeError, AttributeError, KeyError, RuntimeError, OSError, sqlite3.Error):
                self.failure = True
                reply = {'allowed': False}
            try:
                self.replied.emit({**reply, 'generation': generation, 'nonce': nonce})
            except RuntimeError:
                pass  # Qt owner retired; queued finalization still closes the ledger.
        self.executor.submit(run)

    def begin(self, session):
        def begin():
            self._finish()
            self.session = session
            self.failure = False
            self.reviewed.clear()
        self._submit(begin, session.generation)

    def _open(self):
        if self.failure or self.session is None:
            raise ValueError('Standalone journal session unavailable')
        if self.repository is None:
            repository = Repository(self.directory)
            repository.__enter__()
            self.repository = repository
            self.effects = EffectCoordinator(repository.connection, live=True)
            self.effects.recover()

    def review(self, proposal, review_id):
        def review():
            self._open()
            if (proposal.task_ref != self.session.task_ref
                    or proposal.frame.controller_generation != self.session.generation):
                raise ValueError('Review belongs to another task')
            self.reviewed[self.effects._proposal_digest(proposal)] = review_id
        self._submit(review, proposal.frame.controller_generation)

    def clarify(self, instruction, generation):
        def clarify():
            if self.session is None or self.session.generation != generation:
                raise ValueError('Clarification belongs to another task')
            self.session = replace(self.session, instruction=instruction)
            self.reviewed.clear()
        self._submit(clarify, generation)

    def intent(self, grounded, nonce, decision):
        def intent():
            self._open()
            session = self.session
            proposal, checked = grounded.proposal, grounded.checked_frame
            if (proposal.task_ref != session.task_ref
                    or checked.controller_generation != session.generation):
                raise ValueError('Changed standalone task')
            ctx = {'mission_id': session.task_ref, 'subtask_id': 'standalone', 'attempt_id': session.task_ref,
                   'workspace_id': 'interactive-desktop', 'generation': 1, 'target_ref': 'standalone-target',
                   'controller_generation': session.generation, 'observation_ref': checked.capture_ref,
                   'scene_ref': self.effects._contract_scene(checked), 'active': True,
                   'lease_valid': True, 'cleanup_healthy': True}
            digest = self.effects._proposal_digest(proposal)
            effect = self.effects.standalone_intent(
                ctx, grounded, instruction=session.instruction, approve_each_step=session.approve_each_step,
                hands_free=session.hands_free, decision=decision, reviewed_digest=digest if digest in self.reviewed else None,
                reviewed_ref=self.reviewed.get(digest))
            self.reviewed.pop(digest, None)
            return effect.effect_id
        self._submit(intent, grounded.checked_frame.controller_generation, nonce)

    def event(self, operation, effect_id, generation, **data):
        def event():
            if self.failure or self.effects is None:
                raise ValueError('Journal unavailable')
            if self.effects.get(effect_id).task_ref != self.session.task_ref:
                raise ValueError('Wrong task effect')
            if operation == 'started':
                self.effects.started(effect_id)
            elif operation == 'receipt':
                self.effects.receipt(effect_id, **data)
            elif operation == 'observe':
                self.effects.observe(effect_id, **data)
            elif operation == 'revoke':
                self.effects.revoke(effect_id)
            else:
                raise ValueError('Unsupported journal event')
        self._submit(event, generation)

    def _finish(self):
        unresolved = False
        try:
            if self.effects is not None and self.session is not None:
                self.effects.finish_attempt(self.session.task_ref)
                unresolved = bool(self.effects.unresolved(self.session.task_ref))
        finally:
            if self.repository is not None:
                self.repository.__exit__()
            self.repository = self.effects = self.session = None
        return unresolved

    def finish(self, generation, nonce):
        if self.closed:
            def retired(future):
                try:
                    unresolved = future.result()
                    allowed = not self.failure
                except (ValueError, TypeError, AttributeError, KeyError, RuntimeError, OSError, sqlite3.Error):
                    allowed, unresolved = False, True
                try:
                    self.replied.emit({'allowed': allowed, 'unresolved': unresolved, 'generation': generation, 'nonce': nonce})
                except RuntimeError:
                    pass
            self.close_future.add_done_callback(retired)
            return
        def finish():
            failed = self.failure
            unresolved = self._finish()
            if failed:
                raise ValueError('Prior journal update failed')
            return unresolved
        self._submit(finish, generation, nonce, terminal=True)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.close_future = self.executor.submit(self._finish)
        self.executor.shutdown(wait=False)
