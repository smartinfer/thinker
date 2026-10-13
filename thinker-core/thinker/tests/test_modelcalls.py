"""Offline tests of the public packet-to-provider-to-packet boundary."""

import asyncio
import base64
import json
from copy import deepcopy

import httpx
import pytest

from thinker import model_call
from thinker.registry.schema import RegistryCall
from thinker.thinkerql import parse_response


def route(protocol="chat", **changes):
    provider, adapter, style = {
        "chat": ("openrouter", "openai_compatible", "chat_completions_v1"),
        "responses": ("openai", "openai", "responses_v1"),
        "anthropic": ("anthropic", "anthropic", "messages_v1"),
        "gemini": ("gemini", "gemini", "generateContent_v1beta"),
    }[protocol]
    return RegistryCall.model_validate(
        {
            "call_id": f"{provider}:fixture.chat",
            "provider": provider,
            "model_id": "fixture",
            "kind": "chat",
            "modality": "multimodal",
            "caps": ["tools", "json_schema"],
            "limits": {"max_input_tokens": 10000, "max_output_tokens": 1000},
            "price": {"input_per_1k": 0, "output_per_1k": 0},
            "adapter": adapter,
            "payload_style": style,
            "endpoint": "https://provider.invalid",
            **changes,
        }
    )


def packet(**changes):
    return {
        "version": "0.4",
        "request_id": "r1",
        "job_id": "j1",
        "turn_id": "t1",
        "intent": "chat",
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Read the file"}]}],
        **changes,
    }


def response(protocol="chat", text='{"answer":7,}'):
    if protocol == "chat":
        return {
            "model": "fixture",
            "choices": [
                {
                    "message": {
                        "content": text,
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "type": "function",
                                "function": {"name": "read", "arguments": '{"path":"main.c",}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
    if protocol == "responses":
        return {
            "id": "resp1",
            "model": "fixture",
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": text}]},
                {
                    "type": "function_call",
                    "call_id": "tc1",
                    "name": "read",
                    "arguments": '{"path":"main.c",}',
                },
                {"type": "reasoning", "encrypted_content": "opaque"},
            ],
        }
    if protocol == "anthropic":
        return {
            "model": "fixture",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "opaque"},
                {"type": "text", "text": text},
                {"type": "tool_use", "id": "tc1", "name": "read", "input": {"wrong_field": 7}},
            ],
            "stop_reason": "tool_use",
        }
    return {
        "modelVersion": "fixture",
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": text},
                        {
                            "functionCall": {
                                "id": "tc1",
                                "name": "read",
                                "args": {"wrong_field": 7},
                            },
                            "thoughtSignature": "opaque",
                        },
                    ],
                },
                "finishReason": "STOP",
            }
        ],
    }


def invoke(request, *, selected=None, data=None, status=200, headers=None):
    calls = []

    def send(req):
        calls.append(req)
        return httpx.Response(
            status, json=data if data is not None else response(), headers=headers
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            result = await model_call(
                request,
                route=selected or route(),
                credentials=lambda _: "fixture-key",
                client=client,
            )
            assert not client.is_closed
            return result

    return asyncio.run(run()), calls


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic", "gemini"])
def test_native_protocol_preserves_imperfect_content_tools_and_opaque_fields(protocol):
    data = response(protocol)
    request = packet(
        instructions={"json_schema": {"type": "object", "required": ["different"]}},
        tools=[{"name": "read", "input_schema": {"type": "object", "required": ["path"]}}],
    )
    before = deepcopy(request)
    result, calls = invoke(request, selected=route(protocol), data=data)
    assert result["status"] == "completed"
    assert result["content"] == '{"answer":7,}'
    assert result["provider_data"] == data
    assert result["tool_calls"][0]["arguments"] == (
        '{"path":"main.c",}' if protocol in {"chat", "responses"} else {"wrong_field": 7}
    )
    assert request == before
    assert len(calls) == 1
    assert len(result["invocations"]) == 1
    assert result["cost_usd"] is None
    assert result["usage"]["input_tokens"] is None
    assert result["request_id"] == "r1"
    assert result["job_id"] == "j1"
    assert parse_response(result) == result
    sent = json.loads(calls[0].content)
    assert "json_schema" in json.dumps(sent) or "responseJsonSchema" in json.dumps(sent)
    assert calls[0].headers.get(
        "x-api-key"
        if protocol == "anthropic"
        else "x-goog-api-key"
        if protocol == "gemini"
        else "authorization"
    ) == ("fixture-key" if protocol in {"anthropic", "gemini"} else "Bearer fixture-key")
    assert (
        calls[0].url.path
        == {
            "chat": "/v1/chat/completions",
            "responses": "/v1/responses",
            "anthropic": "/v1/messages",
            "gemini": "/v1beta/models/fixture:generateContent",
        }[protocol]
    )


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic", "gemini"])
def test_no_constraint_is_added_to_an_ordinary_model_call(protocol):
    result, calls = invoke(packet(), selected=route(protocol), data=response(protocol))
    assert result["status"] == "completed"
    body = json.loads(calls[0].content)
    assert "response_format" not in body
    assert "text" not in body
    assert "output_config" not in body
    assert "responseJsonSchema" not in body.get("generationConfig", {})
    assert "responseMimeType" not in body.get("generationConfig", {})


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic", "gemini"])
def test_caller_vocabulary_is_forwarded_unchanged_without_enforcing_returned_content(protocol):
    schema = {
        "type": "object",
        "properties": {"decision": {"type": "string", "enum": ["accept", "revise", "ask"]}},
        "required": ["decision"],
        "additionalProperties": False,
    }
    request = packet(instructions={"json_schema": schema})
    before = deepcopy(request)
    result, calls = invoke(
        request,
        selected=route(protocol),
        data=response(protocol, '{"decision":"outside-vocabulary"}'),
    )
    body = json.loads(calls[0].content)
    if protocol == "chat":
        config = body["response_format"]["json_schema"]
    elif protocol == "responses":
        config = body["text"]["format"]
    elif protocol == "anthropic":
        config = body["output_config"]["format"]
    else:
        config = {"schema": body["generationConfig"]["responseJsonSchema"]}
    assert config["schema"] == schema
    assert "strict" not in config
    assert request == before
    assert result["content"] == '{"decision":"outside-vocabulary"}'
    assert result["status"] == "completed"
    assert len(calls) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("strict", [True, False])
def test_native_strict_flag_is_explicit_and_does_not_enable_local_output_validation(
    protocol, strict
):
    result, calls = invoke(
        packet(instructions={"json_schema": {"type": "object"}, "json_schema_strict": strict}),
        selected=route(protocol),
        data=response(protocol, "not JSON"),
    )
    body = json.loads(calls[0].content)
    config = (
        body["response_format"]["json_schema"] if protocol == "chat" else body["text"]["format"]
    )
    assert config["strict"] is strict
    assert result["content"] == "not JSON"
    assert result["status"] == "completed"
    assert len(calls) == 1


@pytest.mark.parametrize("protocol", ["anthropic", "gemini"])
@pytest.mark.parametrize("strict", [True, False])
def test_unmapped_strict_flag_is_not_silently_dropped(protocol, strict):
    result, calls = invoke(
        packet(instructions={"json_schema": {"type": "object"}, "json_schema_strict": strict}),
        selected=route(protocol),
    )
    assert result["error"]["code"] == "UNSUPPORTED_REQUEST"
    assert result["invocations"] == []
    assert calls == []


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic", "gemini"])
def test_provider_schema_rejection_is_returned_without_downgrade_or_retry(protocol):
    result, calls = invoke(
        packet(instructions={"json_schema": {"type": "object", "customKeyword": "caller-value"}}),
        selected=route(protocol),
        data={"error": {"message": "Unsupported schema keyword", "code": "invalid_request_error"}},
        status=400,
    )
    assert result["error"]["code"] == "REQUEST_REJECTED"
    assert result["status"] == "failed"
    assert b'"customKeyword":"caller-value"' in calls[0].content
    assert len(calls) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic", "gemini"])
def test_host_correction_replays_original_arguments_and_error_result(protocol):
    first, _ = invoke(packet(), selected=route(protocol), data=response(protocol))
    followup = packet()
    followup["messages"].append({"role": "assistant", "tool_calls": first["tool_calls"]})
    followup["tool_results"] = [
        {"call_id": "tc1", "output": "Bad arguments. Correct the path field.", "is_error": True}
    ]
    if protocol == "responses":
        followup["continuation"] = first["continuation"]
    result, sent = invoke(followup, selected=route(protocol), data=response(protocol, "corrected"))
    assert result["content"] == "corrected"
    body = json.loads(sent[0].content)
    assert "Bad arguments. Correct the path field." in sent[0].content.decode()
    if protocol == "chat":
        assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"path":"main.c",}'
    elif protocol == "responses":
        assert body["input"][1]["arguments"] == '{"path":"main.c",}'
        assert body["previous_response_id"] == "resp1"
    elif protocol == "gemini":
        assert body["contents"][1]["parts"][0]["thoughtSignature"] == "opaque"
        assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "read"


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "REQUEST_REJECTED"),
        (401, "AUTHENTICATION_FAILED"),
        (403, "ACCESS_DENIED"),
        (408, "TIMEOUT"),
        (409, "CONFLICT"),
        (429, "RATE_LIMITED"),
        (500, "PROVIDER_ERROR"),
        (529, "PROVIDER_OVERLOADED"),
    ],
)
def test_http_errors_return_one_attempt_without_retries(status, code):
    result, calls = invoke(
        packet(),
        data={"error": {"message": "provider failure"}},
        status=status,
        headers={"Retry-After": "75"},
    )
    assert result["status"] == "failed"
    assert result["error"]["code"] == code
    assert result["error"]["retry_after_seconds"] == 75
    assert len(calls) == 1


@pytest.mark.parametrize(
    "native,expected",
    [
        ("ECONNRESET", "CONNECTION_ERROR"),
        ("ETIMEDOUT", "TIMEOUT"),
        ("ERR_STREAM_PREMATURE_CLOSE", "BROKEN_STREAM"),
        ("server_is_overloaded", "PROVIDER_OVERLOADED"),
    ],
)
def test_forgecode_native_codes_even_in_http_200_error_envelope(native, expected):
    result, calls = invoke(packet(), data={"error": {"code": native}})
    assert result["error"]["code"] == expected
    assert result["error"]["provider_code"] == native
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"version": "0.5"},
        {"routing": {"route_id": "other:model.chat"}},
        {"routing": {"local_mode": "assist"}},
        {"provider_options": {"stream": True}},
        {"provider_options": {"model": "other-model"}},
        {"intent": "embed"},
        {"continuation": {"unsupported_state": 7}},
    ],
)
def test_invalid_or_unsupported_requests_return_packets_without_sending(change):
    result, calls = invoke(packet(**change))
    assert result["status"] == "failed"
    assert result["error"]["code"] in {"UNSUPPORTED_REQUEST", "REQUEST_REJECTED"}
    assert result["invocations"] == []
    assert calls == []


def test_missing_credentials_do_not_start_a_model_invocation():
    result = asyncio.run(model_call(packet(), route=route(), credentials=lambda _: None))
    assert result["error"]["code"] == "AUTHENTICATION_FAILED"
    assert result["invocations"] == []


def test_usage_is_reported_without_fabricating_totals_or_cost():
    data = response()
    data["usage"] = {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "prompt_tokens_details": {"cached_tokens": 3},
    }
    result, _ = invoke(packet(), data=data)
    assert result["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": None,
        "cached_input_tokens": 3,
        "reasoning_tokens": None,
        "provider_data": data["usage"],
    }
    assert result["cost_usd"] is None


@pytest.mark.parametrize(
    "error,code",
    [
        (httpx.ConnectError("offline"), "CONNECTION_ERROR"),
        (httpx.ReadTimeout("timeout"), "TIMEOUT"),
        (BrokenPipeError("broken socket"), "CONNECTION_ERROR"),
    ],
)
def test_transport_errors(error, code):
    async def run():
        def send(_):
            raise error

        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            return await model_call(
                packet(), route=route(), client=client, credentials=lambda _: "k"
            )

    result = asyncio.run(run())
    assert result["error"]["code"] == code
    assert result["status"] == "failed"


class BrokenBody(httpx.AsyncByteStream):
    def __init__(self, error):
        self.error = error
        self.closed = False

    async def __aiter__(self):
        yield b'{"choices":[{"message":{"content":"partial\xe2'
        raise self.error

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize(
    "error,code,status",
    [
        (httpx.ReadError("socket reset"), "BROKEN_STREAM", "incomplete"),
        (httpx.ReadTimeout("timeout"), "TIMEOUT", "incomplete"),
        (asyncio.CancelledError(), "CANCELLED", "cancelled"),
    ],
)
def test_broken_body_preserves_exact_partial_bytes_and_closes_stream(error, code, status):
    stream = BrokenBody(error)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
        ) as client:
            return await model_call(
                packet(), route=route(), client=client, credentials=lambda _: "k"
            )

    result = asyncio.run(run())
    assert result["error"]["code"] == code
    assert result["error"]["partial_response"] is True
    assert result["status"] == status
    assert base64.b64decode(result["provider_data"]["raw_body_base64"]).endswith(b"partial\xe2")
    assert stream.closed


def test_deadline_cancels_actual_pending_transport():
    async def run():
        stopped = asyncio.Event()

        async def send(_):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            result = await model_call(
                packet(timeout_ms=25), route=route(), client=client, credentials=lambda _: "k"
            )
            assert stopped.is_set()
            return result

    assert asyncio.run(run())["error"]["code"] == "TIMEOUT"


def test_host_task_cancellation_returns_packet_and_aborts_transport():
    async def run():
        started, stopped = asyncio.Event(), asyncio.Event()

        async def send(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            task = asyncio.create_task(
                model_call(packet(), route=route(), client=client, credentials=lambda _: "k")
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            result = await task
            assert stopped.is_set()
            return result

    result = asyncio.run(run())
    assert result["status"] == "cancelled"
    assert result["error"]["code"] == "CANCELLED"


def test_concurrent_calls_do_not_share_packet_or_route_state():
    async def run():
        arrived, ready = [], asyncio.Event()

        async def send(req):
            body = json.loads(req.content)
            arrived.append(body)
            if len(arrived) == 12:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=1)
            return httpx.Response(
                200, json=response(text=body["messages"][0]["content"][0]["text"])
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            return await asyncio.gather(
                *(
                    model_call(
                        packet(
                            request_id=f"r{i}",
                            messages=[
                                {"role": "user", "parts": [{"type": "text", "text": str(i)}]}
                            ],
                        ),
                        route=route(model_id=f"m{i}"),
                        client=client,
                        credentials=lambda _: "k",
                    )
                    for i in range(12)
                )
            )

    results = asyncio.run(run())
    assert [(r["request_id"], r["content"]) for r in results] == [
        (f"r{i}", str(i)) for i in range(12)
    ]
    assert len({r["invocations"][0]["call_id"] for r in results}) == 12


def test_malformed_provider_envelope_is_distinct_from_malformed_model_text():
    result, _ = invoke(packet(), data={"not_a_completion": True})
    assert result["error"]["code"] == "PROTOCOL_ERROR"
    assert result["provider_data"]["response"] == {"not_a_completion": True}


def test_responses_encrypted_reasoning_replays_as_native_input_item():
    opaque = {"type": "reasoning", "id": "rs1", "encrypted_content": "original-bytes"}
    request = packet(
        messages=[
            {
                "role": "assistant",
                "parts": [{"type": "provider", "provider": "openai", "data": opaque}],
            }
        ]
    )
    result, sent = invoke(request, selected=route("responses"), data=response("responses"))
    assert result["status"] == "completed"
    assert json.loads(sent[0].content)["input"] == [opaque]


def test_nonstandard_optional_metadata_preserves_usable_output():
    data = response()
    data["usage"] = {"prompt_tokens": "unknown", "completion_tokens": -1}
    data["choices"][0]["message"]["tool_calls"][0]["id"] = 7
    result, _ = invoke(packet(), data=data)
    assert result["status"] == "completed"
    assert result["content"] == '{"answer":7,}'
    assert result["usage"]["input_tokens"] is None
    assert result["usage"]["output_tokens"] is None
    assert result["tool_calls"][0]["call_id"] is None
    assert result["provider_data"] == data


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic", "gemini"])
def test_incomplete_and_refused_responses_are_not_false_successes(protocol):
    data = response(protocol)
    if protocol == "chat":
        data["choices"][0]["finish_reason"] = "length"
    elif protocol == "responses":
        data["status"] = "incomplete"
    elif protocol == "anthropic":
        data["stop_reason"] = "max_tokens"
    else:
        data["candidates"][0]["finishReason"] = "MAX_TOKENS"
    result, _ = invoke(packet(), selected=route(protocol), data=data)
    assert result["status"] == "incomplete"
    assert result["content"] == '{"answer":7,}'
    if protocol == "chat":
        data["choices"][0]["finish_reason"] = "content_filter"
    elif protocol == "responses":
        data["status"] = "completed"
        data["output"] = [
            {"type": "message", "content": [{"type": "refusal", "refusal": "Unable to comply"}]}
        ]
    elif protocol == "anthropic":
        data["stop_reason"] = "refusal"
    else:
        data["candidates"][0]["finishReason"] = "SAFETY"
    result, _ = invoke(packet(), selected=route(protocol), data=data)
    assert result["status"] == "refused"


def test_multiple_choices_and_tool_calls_are_retained():
    data = response()
    data["choices"].append({"message": {"content": "alternative"}, "finish_reason": "stop"})
    data["choices"][0]["message"]["tool_calls"].append(
        {"id": "tc2", "function": {"name": "read", "arguments": "not json"}}
    )
    result, _ = invoke(packet(provider_options={"n": 2}), data=data)
    assert len(result["tool_calls"]) == 2
    assert result["tool_calls"][1]["arguments"] == "not json"
    assert result["provider_data"]["choices"][1]["message"]["content"] == "alternative"


def test_injected_client_auth_is_not_replayed_on_redirect():
    requests = []

    async def run():
        def send(req):
            requests.append(req)
            return httpx.Response(307, headers={"Location": "https://other.invalid"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(send), follow_redirects=True
        ) as client:
            return await model_call(
                packet(), route=route(), credentials=lambda _: "k", client=client
            )

    result = asyncio.run(run())
    assert len(requests) == 1
    assert result["status"] == "failed"


def test_default_client_is_closed_after_a_call(monkeypatch):
    original = httpx.AsyncClient
    created = []

    def factory(**kwargs):
        client = original(
            **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response()))
        )
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    result = asyncio.run(model_call(packet(), route=route(), credentials=lambda _: "k"))
    assert result["status"] == "completed"
    assert len(created) == 1
    assert created[0].is_closed
