"""Gemini on Vertex AI behind the interface the study's loop already uses.

The loop (experiments/v04.py) talks to a model through
`client.responses.create(...)`: instructions, tool schemas, an input, and on
later turns the id of the previous response with the tool outputs. This
adapter gives that interface to the `google-genai` SDK, which is stateless:
it keeps the conversation and sends it whole at every turn.

What matters for the measure, checked on Google's documentation and on the
SDK installed (google-genai 3.2.0) on 2026-11-11:

- **Several calls in one turn.** The model may return several `function_call`
  parts in one response. Nothing here asks for it or prevents it: the calling
  mode is the default (AUTO), and every call of the response is passed on, in
  the model's order, as one turn.
- **Returning the results.** All the `function_response` parts of a turn go
  back together, in ONE content, in the order of the calls, each with the
  `id` and the name of its call.
- **The model's own turn is sent back untouched**, as the response returned
  it: its parts carry thought signatures that must not be rebuilt or merged.
- Thinking is left at the model's default level; temperature and the like are
  ignored by this model.
- Billed tokens: input is `prompt_token_count` (the cached part is in it),
  output is the answer plus the thinking tokens, as the price list bills them.

The SDK is a dependency of the study, never of the library: nothing under
src/ imports it.
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

from .tools import env

LOCATION = "global"
# A request that fails with one of these is made again, here and not inside the SDK, so
# that every repeated attempt is counted and written down. A failed request returns no
# usage: nothing was generated, and nothing is billed for it.
RETRY_STATUS = (408, 429, 500, 502, 503, 504)
ATTEMPTS = 4


def vertex_client():
    """The SDK's client for Vertex AI, built as the author's other projects build it:
    the service account is found through GOOGLE_APPLICATION_CREDENTIALS and the
    project is the service account's own, which the SDK reads by itself. No project
    is named here. (GCP_PROJECT in .env is not used: on 2026-11-12 its value was not
    the service account's project, and Vertex AI refused it.) The file of the
    credentials is never opened by the study's code."""
    from google import genai
    from google.genai import types
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and env("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = env("GOOGLE_APPLICATION_CREDENTIALS")
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS is needed in .env")
    # One attempt per request inside the SDK: the repeats are made by the adapter, where they are recorded.
    return genai.Client(vertexai=True, location=LOCATION,
                        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))


def function_response_payload(output: str) -> dict:
    """A tool's text as the object a function response carries. JSON objects go
    as they are; anything else (a list, an error text) under one key."""
    try:
        data = json.loads(output)
    except (TypeError, ValueError):
        return {"output": output}
    return data if isinstance(data, dict) else {"result": data}


def access(model: str) -> dict:
    """Can the service account use the model on Vertex AI? Asked with a token count,
    which generates nothing and is not billed. No value of the configuration is
    returned. (A metadata request on the model is not enough: it does not check the project.)"""
    out = {"model": model, "location": LOCATION, "project": "the service account's own, read by the SDK",
           "accepted": False, "error": None}
    try:
        client = vertex_client()                             # kept: a client dropped mid-call closes
        client.models.count_tokens(model=model, contents="ciao")
        out["accepted"] = True
    except Exception as e:                                   # noqa: BLE001
        reason = ""
        try:
            reason = e.details["error"]["details"][0].get("reason", "")
        except Exception:                                    # noqa: BLE001
            pass
        out["error"] = f"{getattr(e, 'code', '')} {getattr(e, 'status', type(e).__name__)} {reason}".strip()
    return out


class Gemini:
    """`client.responses.create(**kwargs)` for a Gemini model on Vertex AI."""

    def __init__(self, client=None, pause: float = 5.0):
        if client is None:
            client = vertex_client()
        self.pause = pause                           # seconds before the first repeat; doubled at each one
        self.client = client
        self.history: dict[str, list] = {}           # response id -> the conversation up to and including it
        self.calls: dict[str, tuple[str, str | None]] = {}     # our call id -> (name, the model's own id)
        self.n = 0
        self.responses = self

    async def create(self, *, model: str, instructions: str, tools: list[dict], input: list[dict],
                     previous_response_id: str | None = None, **_):
        from google.genai import types
        if previous_response_id is None:
            contents = [types.Content(role="user", parts=[types.Part(text=i["content"]) for i in input])]
        else:
            parts = []
            for item in input:                       # the results of one turn, in the order of its calls
                name, model_id = self.calls[item["call_id"]]
                part = types.Part.from_function_response(name=name, response=function_response_payload(item["output"]))
                if model_id:
                    part.function_response.id = model_id
                parts.append(part)
            contents = self.history[previous_response_id] + [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            system_instruction=instructions,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t.get("description") or "",
                                          parameters_json_schema=t.get("parameters") or {"type": "object"})
                for t in tools])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        retries = []
        for attempt in range(ATTEMPTS):
            try:
                r = await self.client.aio.models.generate_content(model=model, contents=contents, config=config)
                break
            except Exception as e:                   # noqa: BLE001
                code = getattr(e, "code", None)
                if code not in RETRY_STATUS or attempt == ATTEMPTS - 1:
                    raise
                retries.append({"attempt": attempt + 1, "code": code, "status": str(getattr(e, "status", ""))})
                await asyncio.sleep(self.pause * 2 ** attempt)
        self.n += 1
        rid = f"gemini-{self.n}"
        candidate = r.candidates[0] if r.candidates else None
        content = candidate.content if candidate is not None else None
        out, text = [], []
        for part in (content.parts if content is not None and content.parts else []):
            if part.function_call is not None:
                cid = f"g{len(self.calls) + 1}"
                self.calls[cid] = (part.function_call.name, part.function_call.id)
                out.append(SimpleNamespace(type="function_call", name=part.function_call.name, call_id=cid,
                                           arguments=json.dumps(dict(part.function_call.args or {}), ensure_ascii=False)))
            elif part.text and not part.thought:
                text.append(part.text)
        if text:
            out.insert(0, SimpleNamespace(type="message", content=[SimpleNamespace(text="".join(text))]))
        # The model's turn goes back as it came: its parts carry the thought signatures.
        self.history[rid] = contents + ([content] if content is not None else [])
        u = r.usage_metadata
        return SimpleNamespace(
            id=rid, output=out, retries=retries,
            finish_reason=str(getattr(candidate, "finish_reason", None)),
            usage=SimpleNamespace(
                input_tokens=(u.prompt_token_count or 0) if u else 0,
                output_tokens=((u.candidates_token_count or 0) + (u.thoughts_token_count or 0)) if u else 0,
                input_tokens_details=SimpleNamespace(cached_tokens=(u.cached_content_token_count or 0) if u else 0)))
