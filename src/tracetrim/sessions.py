"""Open runs for an automatic HTTP proxy. All scheduling happens on its event loop."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .auto import Run

if TYPE_CHECKING:
    from .proxy import Live


@dataclass
class Session:
    key: bytes | None
    live: Live
    run: Run = field(default_factory=Run)
    pending: set[asyncio.Future] = field(default_factory=set)
    closing: asyncio.Future | None = None
    activity: float = 0.0


class HttpRuns:
    def __init__(self, engine, live_factory, idle_seconds, warn):
        self.engine = engine
        self.live_factory = live_factory
        self.idle_seconds = idle_seconds
        self.warn = warn
        self.warned = False
        self.sessions: dict[bytes | None, Session] = {}
        # The engine owns one bounded cache. Each entry points to its owner and
        # original run, which can receive late expansion evidence after closure.
        self.owners: dict[str, tuple[bytes | None, Run]] = {}
        self.engine.trimmer.on_forget = lambda cid: self.owners.pop(cid, None)
        self.changed = asyncio.Event()
        self.stopping = False

    def session(self, key: bytes | None) -> Session:
        if key is None and not self.warned:
            self.warned = True
            self.warn("HTTP requests without a transport session ID share one run context")
        if key not in self.sessions:
            live = self.live_factory()
            live.per_run = True
            self.sessions[key] = Session(key, live)
        return self.sessions[key]

    @asynccontextmanager
    async def request(self, session, *, closing):
        """Parallel calls may finish together. A closing waits for all earlier
        calls in its session, and later calls wait for that closing."""
        loop = asyncio.get_running_loop()
        before = list(session.pending) if closing else []
        if not closing and session.closing is not None:
            before.append(session.closing)
        gate = asyncio.gather(*before) if before else None
        ticket = loop.create_future()
        session.pending.add(ticket)
        if closing:
            session.closing = ticket

        def finished(_=None):
            session.pending.remove(ticket)
            if session.closing is ticket:
                session.closing = None
            session.activity = loop.time()
            ticket.set_result(None)
            self.changed.set()

        try:
            if gate is not None:
                await asyncio.shield(gate)
            yield
        finally:
            # Cancelling a queued request must not release its successors early.
            if gate is not None and not gate.done():
                gate.add_done_callback(finished)
            else:
                finished()

    def remember(self, session, call_id):
        if call_id:
            self.owners[call_id] = (session.key, session.run)

    def expand(self, session, call_id, fields):
        owner = self.owners.get(call_id)
        if owner is None or owner[0] != session.key:
            return self.engine.unknown_expand(call_id)
        with self.engine.learner.use_run(owner[1]):
            return self.engine.expand(call_id, fields)

    async def sweep(self, close):
        loop = asyncio.get_running_loop()
        while not self.stopping:
            self.changed.clear()
            timeout = None
            for key, session in list(self.sessions.items()):
                if session.pending:
                    continue
                remaining = session.activity + self.idle_seconds - loop.time()
                if remaining <= 0:
                    if session.run.events:
                        try:
                            async with self.request(session, closing=True):
                                with self.engine.learner.use_run(session.run):
                                    await close(None, session)
                        except Exception as e:
                            self.warn(f"idle closing failed ({type(e).__name__})")
                    if not session.pending and not session.run.events:
                        self.sessions.pop(key)
                elif timeout is None or remaining < timeout:
                    timeout = remaining
            if not self.stopping:
                try:
                    await asyncio.wait_for(self.changed.wait(), timeout)
                except asyncio.TimeoutError:
                    pass

    @asynccontextmanager
    async def lifespan(self, close, flush=None):
        """`flush(session)`: keeps an open run at shutdown (the proxy's, through its relearning coordinator when
        there is one); without it, `Learner.flush` on the event loop, as up to 1.3."""
        task = asyncio.create_task(self.sweep(close))
        try:
            yield
        finally:
            self.stopping = True
            self.changed.set()
            try:
                await task
            finally:
                # Drain every open run even if one of the writes fails.
                pending = [ticket for session in self.sessions.values() for ticket in session.pending]
                if pending:
                    await asyncio.gather(*pending)
                failure = None
                for session in self.sessions.values():
                    try:
                        if flush is not None:
                            await flush(session)
                        else:
                            with self.engine.learner.use_run(session.run):
                                self.engine.learner.flush()
                    except Exception as e:
                        failure = failure or e
                if failure is not None:
                    raise failure
