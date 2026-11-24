"""tracetrim inside a LangGraph agent: the automatic mode around the graph's `ToolNode`.

    from langgraph.prebuilt import ToolNode
    from tracetrim.langgraph import TraceTrim

    tt = TraceTrim("tracetrim-data/", min_runs=5)
    tools = my_tools + tt.tools                                  # expand and tracetrim_done
    model = model.bind_tools(tools)                              # and tt.instructions in the system prompt
    tools_node = ToolNode(tools, wrap_tool_call=tt.wrap_tool_call, awrap_tool_call=tt.awrap_tool_call)

Nothing else in the graph changes. Every result of the user's tools goes through the same engine as the proxy and
`AutoTrim` (tracetrim.engine); `expand` answers from the original held back, without calling the tool again;
`tracetrim_done` closes the run with the final answer. Each conversation (`thread_id` in the graph's config) has
its own open run, and recovers only the originals of its own calls; the learned policy is shared, as with the
proxy's `--isolate-sessions`. Calls without a `thread_id` share one run.

The rules are the proxy's for isolated sessions (tracetrim.sessions): a closing waits for the calls of its
conversation already running, and later calls wait for the closing; after a closing the conversation starts a new
run, and a late `expand` is evidence of the run its result came from. Engine work (trimming, recovering, closing
and relearning) is serialised, so no result is trimmed half with the old policy and half with the new.

Needs `pip install "tracetrim[langgraph]"`; nothing else in tracetrim imports LangGraph.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.config import get_config

from .auto import DONE, DONE_REPLY, AutoTrim, Run
from .contract import ToolCall, ToolResult
from .engine import expand_tool, requested_fields

__all__ = ["TraceTrim"]

_POLL = 0.02                                     # seconds between checks of a wait on the event loop
_log = logging.getLogger("tracetrim.langgraph")
_warned: set[str] = set()


def _warn(message: str) -> None:
    """Once per message: a failure of tracetrim that the graph goes on without."""
    if message not in _warned:
        _warned.add(message)
        _log.warning("tracetrim: %s", message)


async def _shielded(work):
    """Await `work` (a thread doing disk work) so that cancelling the caller does not abandon it half done: the
    work finishes, and only then the cancellation is raised."""
    task = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():               # a second cancellation must not abandon it either
            try:
                await asyncio.shield(asyncio.wait({task}))
            except asyncio.CancelledError:
                continue
        raise


class _LearningFailed(Exception):
    """The run was recorded; learning from it failed. A retry of the closing relearns (`relearn_owed`)."""


def _schema_tool(spec: dict) -> StructuredTool:
    """A LangChain tool with tracetrim's name, description and JSON schema. Its body never runs: the wrapper
    answers these calls itself, before `ToolNode` would execute them."""
    def unreachable(**_: Any) -> str:
        raise RuntimeError(f"{spec['name']} is answered by TraceTrim.wrap_tool_call: pass it to ToolNode")
    return StructuredTool.from_function(unreachable, name=spec["name"], description=spec["description"],
                                        args_schema=spec["inputSchema"])


@dataclass
class _Thread:
    """One conversation: its open run, the calls running in it, and its model turns."""
    run: Run = field(default_factory=Run)
    active: int = 0                              # calls of the user's tools running now
    closing: bool = False
    turns: dict[Any, int] = field(default_factory=dict)   # AI message -> turn, within the open run
    activity: float = field(default_factory=time.monotonic)


class TraceTrim:
    """`TraceTrim(workdir, **options)`: the options are those of `tracetrim.auto.AutoTrim` (`min_runs`,
    `protect_ids`, `skill_version`, ...), plus:

    `idle_seconds`: a conversation that resumes after this pause starts a new run; the calls of the old one are kept
    for telemetry, as the proxy does when its pause closes a run. None (the default): no pause, close runs with
    `tracetrim_done` or `close`.
    `messages_key`: the key of the messages in the graph's state, as given to `ToolNode` (default `"messages"`)."""

    def __init__(self, workdir: str | Path, *, idle_seconds: float | None = None, messages_key: str = "messages",
                 **options: Any):
        self.auto = AutoTrim(workdir, **options)
        self.idle_seconds = idle_seconds
        self.messages_key = messages_key
        self._cond = threading.Condition()       # the threads, their calls running and their closings
        self._engine = threading.RLock()         # every use of the engine and the learner
        self._learning = threading.Lock()        # one relearning at a time, outside the engine lock
        self._threads: dict[Any, _Thread] = {}
        self._swept = time.monotonic()          # the last look for idle conversations (_sweep)
        # call_id -> (thread, run of the call): an `expand` from another thread is an unknown call_id, and a
        # late `expand` (after the closing) is evidence of the run the result came from.
        self._owners: dict[str, tuple[Any, Run]] = {}
        trimmer = self.auto.engine.trimmer
        trimmer.shared = True                    # an error never names the call_ids of other conversations
        trimmer.on_forget = lambda cid: self._owners.pop(cid, None)
        self.tools = [_schema_tool(expand_tool(self.auto.skill_version)), _schema_tool(DONE)]
        self._names = {t.name for t in self.tools}

    # -- what the model is given -------------------------------------------------------------------------------
    @property
    def instructions(self) -> str:
        """The tracetrim skill, with the part on `tracetrim_done`: add it to the system prompt."""
        from .skill import text
        return text(strategy=self.auto.strategy, done=True, version=self.auto.skill_version)

    @property
    def report(self) -> Path:
        return self.auto.report

    # -- conversations ----------------------------------------------------------------------------------------
    @staticmethod
    def _key() -> Any:
        try:
            return (get_config().get("configurable") or {}).get("thread_id")
        except RuntimeError:                     # outside a graph run
            return None

    def _thread(self, key: Any) -> _Thread:
        with self._cond:
            return self._threads.setdefault(key, _Thread())

    # Waits are checks under `_cond`: the sync path waits on the Condition, the async path polls on the event loop,
    # so that no worker thread is ever held by a wait (a closing waiting for a call whose trimming needs a worker).
    def _try_enter(self, key: Any, t: _Thread):
        """Under `_cond`: count a call in ("in"), or say why it must wait ("wait"), close an idle run first ("idle"),
        or take the conversation again ("stale": `_sweep` forgot it while the call was arriving)."""
        if self._threads.get(key) is not t:
            return "stale"
        if t.closing:
            return "wait"
        if (self.idle_seconds is not None and t.active == 0 and (t.run.events or t.run.notes)
                and time.monotonic() - t.activity > self.idle_seconds):
            t.closing = True
            return "idle"
        t.active += 1
        return "in"

    def _try_close(self, t: _Thread) -> bool:
        """Under `_cond`: start a closing once the conversation's calls running have finished."""
        if t.closing or t.active:
            return False
        t.closing = True
        return True

    def _leave(self, t: _Thread) -> None:
        with self._cond:
            t.active -= 1
            t.activity = time.monotonic()
            self._cond.notify_all()

    def _end_close(self, t: _Thread, closed: Run, before: int) -> None:
        """After a closing: a new run when the old one was recorded, as in the proxy (a late `expand` then goes to the
        run its result came from)."""
        with self._cond:
            if closed.last_number != before:
                t.run = Run(last_number=closed.last_number, answered_at=closed.answered_at)
                t.turns = {}
            t.closing = False
            self._cond.notify_all()

    def _close_work(self, t: _Thread, kind: str, answer: str | None = None) -> None:
        """The closing itself, once started: the run recorded under the engine lock, then the relearning outside it
        (other conversations go on being trimmed meanwhile), then the new policy installed under the lock. One
        relearning at a time. `kind`: "answer", "telemetry" (no answer) or "idle" (the pause; never raises)."""
        closed, before = t.run, t.run.last_number
        try:
            with self._engine, self.auto.learner.use_run(closed):
                if kind == "answer":
                    try:
                        self.auto.engine.done(answer)
                    except OSError:
                        if closed.last_number != before:
                            raise        # recorded: what is left (a secondary write) is the retry's
                        self.auto.engine.done(answer)      # not recorded: once more at once, as AutoTrim.done
                elif kind == "idle":
                    _, kept = self.auto.learner.close_unattended()
                    if kept is not None:
                        _warn(f"idle run kept without part of its evidence ({type(kept).__name__})")
                else:
                    self.auto.learner.flush(relearn=False)
            if closed.last_number != before or self.auto.learner.relearn_owed:
                try:
                    with self._learning:             # learning and installing together: no older policy over a newer
                        policy = self.auto.engine.learn_policy()
                        with self._engine:
                            self.auto.engine.install(policy)
                except Exception as e:                # noqa: BLE001
                    raise _LearningFailed(type(e).__name__) from e
        except Exception as e:                    # noqa: BLE001
            if kind != "idle":
                raise
            _warn(f"closing an idle run failed ({type(e).__name__}); the call goes on")
        finally:
            self._end_close(t, closed, before)

    def _enter(self, key: Any, state: Any, call_id: str) -> tuple[_Thread, Run, int]:
        """Sync: a call of a user's tool starts, after any closing of its conversation."""
        self._sweep()
        t = self._thread(key)
        while True:
            with self._cond:
                self._cond.wait_for(lambda: not t.closing)
                state_ = self._try_enter(key, t)
                if state_ == "in":
                    return t, t.run, self._turn(t, state, call_id)
            if state_ == "stale":
                t = self._thread(key)
            else:
                self._close_work(t, "idle")

    async def _aenter(self, key: Any, state: Any, call_id: str) -> tuple[_Thread, Run, int]:
        """Async: the same, polling on the loop. The count goes up on the loop, before any await, so a cancelled call
        can never leave it up."""
        self._sweep()
        t = self._thread(key)
        while True:
            with self._cond:
                state_ = self._try_enter(key, t)
                if state_ == "in":
                    return t, t.run, self._turn(t, state, call_id)
            if state_ == "stale":
                t = self._thread(key)
            elif state_ == "idle":
                await _shielded(asyncio.to_thread(self._close_work, t, "idle"))
            else:
                await asyncio.sleep(_POLL)

    def _sweep(self) -> None:
        """With `idle_seconds`: the idle runs of every conversation, abandoned ones above all, closed for telemetry in a
        background thread, and conversations left with nothing open forgotten, so that memory does not grow with
        conversations that never come back. A look at most every quarter of the pause, at the start of a call."""
        if self.idle_seconds is None:
            return
        now = time.monotonic()
        stale = []
        with self._cond:
            if now - self._swept < self.idle_seconds / 4:
                return
            self._swept = now
            for k, t in list(self._threads.items()):
                if t.closing or t.active or now - t.activity <= self.idle_seconds:
                    continue
                if t.run.events or t.run.notes:
                    t.closing = True                 # as `_try_enter` does: its calls wait for the closing
                    stale.append((k, t))
                else:
                    del self._threads[k]
        if stale:
            threading.Thread(target=self._close_idle, args=(stale,), name="tracetrim-idle", daemon=True).start()

    def _close_idle(self, stale: list[tuple[Any, _Thread]]) -> None:
        for k, t in stale:
            self._close_work(t, "idle")              # never raises, and ends the closing
            with self._cond:
                if (self._threads.get(k) is t and not t.active and not t.closing
                        and not (t.run.events or t.run.notes)):
                    del self._threads[k]

    def _closing(self, t: _Thread, kind: str, answer: str | None = None) -> None:
        """Sync: wait for the calls running, then close."""
        with self._cond:
            self._cond.wait_for(lambda: self._try_close(t))
        self._close_work(t, kind, answer)

    async def _aclosing(self, t: _Thread, kind: str, answer: str | None = None) -> None:
        while True:
            with self._cond:
                if self._try_close(t):
                    break
            await asyncio.sleep(_POLL)
        await _shielded(asyncio.to_thread(self._close_work, t, kind, answer))

    # -- the wrapper --------------------------------------------------------------------------------------------
    def _turn(self, t: _Thread, state: Any, call_id: str) -> int:
        """The model turn of the call, within its run: the AI message that holds it. Calls of one turn do not use
        each other's outputs (tracetrim.usage). A call whose message is not found is a turn of its own: it may then
        be counted as using an earlier result, which keeps more fields, never fewer. Called under `_cond`."""
        holder: Any = ("call", call_id)
        m = self._message_of(state, call_id)
        if m is not None:
            holder = m.id or ("message", id(m))
        return t.turns.setdefault(holder, len(t.turns))

    def _message_of(self, state: Any, call_id: str) -> AIMessage | None:
        messages = state if isinstance(state, list) else (
            state.get(self.messages_key) if isinstance(state, dict) else getattr(state, self.messages_key, None))
        for m in reversed(messages if isinstance(messages, list) else []):
            if isinstance(m, AIMessage) and any(c.get("id") == call_id for c in m.tool_calls):
                return m
        return None

    # -- the wrapper --------------------------------------------------------------------------------------------
    def with_tools(self, tools: list) -> list:
        """`tools + self.tools`, refusing at once a user's tool named like one of tracetrim's own."""
        clash = sorted({getattr(t, "name", None) for t in tools} & self._names)
        if clash:
            raise ValueError(f"tools named {clash} collide with tracetrim's own: rename them")
        return list(tools) + self.tools

    def _check_name(self, request) -> bool:
        """Is this call one of tracetrim's own? A user's tool with the same name is an error, not intercepted."""
        name = request.tool_call["name"]
        if name not in self._names:
            return False
        if request.tool is not None and all(request.tool is not t for t in self.tools):
            raise ValueError(f"a tool named {name!r} collides with tracetrim's own: rename it")
        return True

    def _done_check(self, call: dict, state: Any) -> tuple[ToolMessage | None, str]:
        """The answer of a closing, and an error when it cannot be recorded as it is (a refused closing: the run
        stays open). The call is not modified: it belongs to the AI message in the graph's state."""
        name, cid = call["name"], call["id"]
        answer = (call.get("args") or {}).get("answer")
        if not isinstance(answer, str):
            answer = "" if answer is None else json.dumps(answer, ensure_ascii=False)
        if not answer.strip():
            return ToolMessage("Error: tracetrim_done needs your final answer in `answer`. Nothing was recorded.",
                               tool_call_id=cid, name=name, status="error"), answer
        m = self._message_of(state, cid)
        if m is not None and any(c.get("name") not in self._names for c in m.tool_calls):
            # Its sibling calls may not have started yet: the closing would leave them to the next task.
            return ToolMessage("Error: call tracetrim_done on its own, after reading the results of your other tool "
                               "calls. Nothing was recorded.", tool_call_id=cid, name=name, status="error"), answer
        return None, answer

    def _refused(self, key: Any) -> None:
        t = self._thread(key)
        with self._engine, self.auto.learner.use_run(t.run):
            self.auto.engine.refuse()

    def _expand(self, call: dict, key: Any) -> ToolMessage:
        args, cid = call.get("args") or {}, call["id"]
        call_id = str(args.get("call_id", ""))
        t = self._thread(key)
        with self._cond:
            owner = self._owners.get(call_id)
            t.activity = time.monotonic()          # an expand is activity: the pause starts again, as in the proxy
        with self._engine:
            if owner is None or owner[0] != key:
                with self.auto.learner.use_run(t.run):
                    text = self.auto.engine.unknown_expand(call_id)
            else:
                with self.auto.learner.use_run(owner[1]):
                    text = self.auto.engine.expand(call_id, requested_fields(args.get("fields")))
        return ToolMessage(text, tool_call_id=cid, name=call["name"])

    @staticmethod
    def _closing_failed(call: dict, e: Exception) -> ToolMessage:
        what = (f"the run was recorded, but learning from it failed ({e})" if isinstance(e, _LearningFailed)
                else f"the run could not be recorded ({type(e).__name__})")
        return ToolMessage(f"Error: {what}. Call tracetrim_done again with the same answer.",
                           tool_call_id=call["id"], name=call["name"], status="error")

    def _trim(self, call: dict, result: Any, key: Any, run: Run, turn: int) -> Any:
        """The result of a user's tool, as the model should receive it."""
        if not isinstance(result, ToolMessage) or not isinstance(result.content, str):
            return result                        # a Command, or content that is not text: untouched, not recorded
        args = call.get("args") or {}
        # An error goes back exactly as the tool sent it, recorded and never trimmed, as in the proxy.
        verbatim = result.status == "error"
        try:
            with self._engine, self.auto.learner.use_run(run):
                obs = self.auto.engine.observe(ToolCall(call["name"], args if isinstance(args, dict) else {}, turn),
                                               ToolResult(result.content, verbatim=verbatim))
                if obs.call_id:
                    with self._cond:
                        self._owners[obs.call_id] = (key, run)
        except Exception as e:                    # noqa: BLE001  (tracetrim failed: the model gets the whole result)
            _warn(f"a result could not be trimmed ({type(e).__name__}); the model receives it whole")
            return result
        return result if verbatim else result.model_copy(update={"content": obs.text})

    def wrap_tool_call(self, request, execute):
        """For `ToolNode(..., wrap_tool_call=tt.wrap_tool_call)`."""
        call, key = request.tool_call, self._key()
        if self._check_name(request):
            if call["name"] == "expand":
                return self._expand(call, key)
            error, answer = self._done_check(call, request.state)
            if error is not None:
                self._refused(key)
                return error
            try:
                self._closing(self._thread(key), "answer", answer)
            except Exception as e:                # noqa: BLE001  (a disk error: the model can try again)
                return self._closing_failed(call, e)
            return ToolMessage(DONE_REPLY, tool_call_id=call["id"], name=call["name"])
        t, run, turn = self._enter(key, request.state, call["id"])
        try:
            return self._trim(call, execute(request), key, run, turn)
        finally:
            self._leave(t)

    async def awrap_tool_call(self, request, execute):
        """For `ToolNode(..., awrap_tool_call=tt.awrap_tool_call)`: the same, with waits on the event loop and
        tracetrim's disk work (the closing and its relearning above all) in threads."""
        call, key = request.tool_call, self._key()
        if self._check_name(request):
            if call["name"] == "expand":
                return await _shielded(asyncio.to_thread(self._expand, call, key))
            error, answer = self._done_check(call, request.state)
            if error is not None:
                await asyncio.to_thread(self._refused, key)
                return error
            try:
                await self._aclosing(self._thread(key), "answer", answer)
            except asyncio.CancelledError:
                raise
            except Exception as e:                # noqa: BLE001
                return self._closing_failed(call, e)
            return ToolMessage(DONE_REPLY, tool_call_id=call["id"], name=call["name"])
        t, run, turn = await self._aenter(key, request.state, call["id"])
        try:
            result = await execute(request)
            return await _shielded(asyncio.to_thread(self._trim, call, result, key, run, turn))
        finally:
            self._leave(t)

    # -- closing from code --------------------------------------------------------------------------------------
    def close(self, thread_id: Any, answer: str | None) -> None:
        """Close a conversation's run from code (the model did not call tracetrim_done): `answer` is its final
        answer, or None for telemetry only. It waits for the conversation's calls running: never call it from inside
        a tool of the same conversation."""
        with self._cond:
            if thread_id not in self._threads:
                return                           # nothing was ever recorded for it
        self._closing(self._thread(thread_id), "answer" if answer and answer.strip() else "telemetry", answer)

    async def aclose(self, thread_id: Any, answer: str | None) -> None:
        with self._cond:
            if thread_id not in self._threads:
                return
        await self._aclosing(self._thread(thread_id), "answer" if answer and answer.strip() else "telemetry", answer)

    def flush(self) -> None:
        """On shutdown, with no call running: the open runs of every conversation are kept for telemetry, then one
        relearning. Every run is tried; the first error, if any, is raised at the end."""
        with self._cond:
            threads = [t for t in self._threads.values() if t.run.events or t.run.notes]
        first: BaseException | None = None
        for t in threads:
            try:
                with self._cond:
                    self._cond.wait_for(lambda: self._try_close(t))
                closed, before = t.run, t.run.last_number
                try:
                    with self._engine, self.auto.learner.use_run(closed):
                        self.auto.learner.flush(relearn=False)
                finally:
                    self._end_close(t, closed, before)
            except Exception as e:                # noqa: BLE001
                first = first or e
        with self._learning:
            policy = self.auto.engine.learn_policy()
            with self._engine:
                self.auto.engine.install(policy)
        if first is not None:
            raise first

    async def aflush(self) -> None:
        await asyncio.to_thread(self.flush)
