"""ThinkerQL packet/native-envelope translation; no output validation or repair."""

import json
from copy import deepcopy
from urllib.parse import quote

from ..model_turn.inference_adapters import usage_report


class UnsupportedRequest(ValueError):
    """An option cannot be represented by this one-shot transport."""


def protocol_for(route):
    if route.payload_style == "chat_completions_v1":
        return "chat"
    if route.payload_style in {"responses_v1", "openai_responses_v1"}:
        return "responses"
    if route.payload_style == "messages_v1":
        return "anthropic"
    if route.payload_style == "generateContent_v1beta":
        return "gemini"
    if route.payload_style == "model_turn_v1" and route.adapter in {
        "openai",
        "anthropic",
        "gemini",
    }:
        return {"openai": "responses", "anthropic": "anthropic", "gemini": "gemini"}[route.adapter]
    raise UnsupportedRequest(f"Unsupported payload_style: {route.payload_style}")


def _string(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _parts(message, protocol, provider):
    parts = []
    for part in message.get("parts", []):
        if part["type"] == "provider":
            if part["provider"] != provider:
                raise UnsupportedRequest("Opaque content belongs to another provider")
            parts.append(deepcopy(part["data"]))
        elif part["type"] == "text":
            kind = "output_text" if message["role"] == "assistant" else "input_text"
            parts.append(
                {"type": kind if protocol == "responses" else "text", "text": part["text"]}
                if protocol != "gemini"
                else {"text": part["text"]}
            )
        elif part["type"] == "image_url" and protocol in {"responses", "chat"}:
            image = part["image_url"]
            parts.append(
                {
                    "type": "input_image",
                    "image_url": image["url"],
                    "detail": image.get("detail", "auto"),
                }
                if protocol == "responses"
                else deepcopy(part)
            )
        else:
            raise UnsupportedRequest("Use native provider parts for this media type/protocol")
    return parts


def prepare(packet, route):
    """Return protocol, URL, and native JSON body for one explicit registry route."""
    protocol = protocol_for(route)
    if packet["intent"] != "chat" or route.kind != "chat":
        raise UnsupportedRequest("model_call currently supports chat intent")
    if packet.get("documents"):
        raise UnsupportedRequest("Use native provider parts for documents")
    routing = packet.get("routing", {})
    if routing.get("local_mode", "off") != "off" or routing.get("local_route_id"):
        raise UnsupportedRequest("Local-only and local-assist execution require localcalls")
    if (
        routing.get("route_id", route.call_id) != route.call_id
        or routing.get("model", f"{route.provider}/{route.model_id}")
        != f"{route.provider}/{route.model_id}"
        or (routing.get("alias") and routing["alias"] not in route.aliases)
    ):
        raise UnsupportedRequest("Request routing does not match supplied route")
    options = deepcopy(packet.get("provider_options", {}))
    if options.get("stream") not in (None, False):
        raise UnsupportedRequest("SSE event streaming is not implemented by model_call")
    reserved = {
        "model",
        "messages",
        "input",
        "contents",
        "tools",
        "system",
        "systemInstruction",
        "instructions",
        "previous_response_id",
    }
    if reserved.intersection(options):
        raise UnsupportedRequest(
            "provider_options cannot replace packet identity, transcript, tools, or continuation"
        )

    messages = deepcopy(packet["messages"])
    if packet.get("tool_results"):
        messages.append({"role": "tool", "tool_results": packet["tool_results"]})
    call_names = {
        c.get("call_id"): c.get("name")
        for m in messages
        for c in m.get("tool_calls", [])
        if c.get("call_id")
    }
    native, systems = [], []
    for message in messages:
        role = message["role"]
        parts = _parts(message, protocol, route.provider)
        if role == "system" and protocol in {"anthropic", "gemini"}:
            systems.extend(parts)
            continue
        calls = message.get("tool_calls", [])
        results = message.get("tool_results", [])
        if protocol == "responses":
            content = []
            for part in parts:
                if isinstance(part, dict) and part.get("type") in {
                    "reasoning",
                    "function_call",
                    "function_call_output",
                    "message",
                }:
                    if content:
                        native.append({"role": role, "content": content})
                        content = []
                    native.append(part)
                else:
                    content.append(part)
            if content:
                native.append({"role": role, "content": content})
            for call in calls:
                native.append(
                    dict(
                        call.get("provider_data") or {},
                        type="function_call",
                        call_id=call.get("call_id"),
                        name=call.get("name"),
                        arguments=_string(call.get("arguments")),
                    )
                )
            for result in results:
                native.append(
                    {
                        "type": "function_call_output",
                        "call_id": result["call_id"],
                        "output": _string(
                            {"error": result["output"]}
                            if result.get("is_error")
                            else result["output"]
                        ),
                    }
                )
        elif protocol == "chat":
            if parts or calls:
                item = dict(message.get("provider_data") or {}, role=role, content=parts or None)
                if calls:
                    item["tool_calls"] = [
                        dict(
                            c.get("provider_data") or {},
                            id=c.get("call_id"),
                            type="function",
                            function={
                                "name": c.get("name"),
                                "arguments": _string(c.get("arguments")),
                            },
                        )
                        for c in calls
                    ]
                native.append(item)
            for result in results:
                native.append(
                    {
                        "role": "tool",
                        "tool_call_id": result["call_id"],
                        "content": _string(
                            {"error": result["output"]}
                            if result.get("is_error")
                            else result["output"]
                        ),
                    }
                )
        elif protocol == "anthropic":
            parts.extend(
                dict(
                    c.get("provider_data") or {},
                    type="tool_use",
                    id=c.get("call_id"),
                    name=c.get("name"),
                    input=c.get("arguments"),
                )
                for c in calls
            )
            parts.extend(
                {
                    "type": "tool_result",
                    "tool_use_id": r["call_id"],
                    "content": _string(r["output"]),
                    "is_error": r.get("is_error", False),
                }
                for r in results
            )
            native.append({"role": "user" if role == "tool" else role, "content": parts})
        else:
            parts.extend(
                dict(
                    c.get("provider_data") or {},
                    functionCall={
                        "name": c.get("name"),
                        "args": c.get("arguments"),
                        **({"id": c["call_id"]} if c.get("call_id") else {}),
                    },
                )
                for c in calls
            )
            for result in results:
                name = call_names.get(result["call_id"])
                if not name:
                    raise UnsupportedRequest(
                        "Gemini tool results require the matching named proposal in messages"
                    )
                parts.append(
                    {
                        "functionResponse": {
                            "name": name,
                            "id": result["call_id"],
                            "response": {
                                "error" if result.get("is_error") else "output": result["output"]
                            },
                        }
                    }
                )
            native.append({"role": "model" if role == "assistant" else "user", "parts": parts})

    settings = packet.get("instructions", {})
    schema = settings.get("json_schema")
    limit = settings.get("max_output_tokens", 256)
    tools = packet.get("tools", [])
    if protocol in {"chat", "responses"}:
        body = {
            "model": route.model_id,
            "messages" if protocol == "chat" else "input": native,
            "max_tokens" if protocol == "chat" else "max_output_tokens": limit,
            "stream": False,
        }
        if tools:
            definitions = [
                dict(
                    type="function",
                    name=t["name"],
                    description=t.get("description", ""),
                    parameters=t["input_schema"],
                    **({"strict": t["strict"]} if "strict" in t else {}),
                )
                for t in tools
            ]
            body["tools"] = (
                [
                    {"type": "function", "function": {k: v for k, v in t.items() if k != "type"}}
                    for t in definitions
                ]
                if protocol == "chat"
                else definitions
            )
        if schema is not None:
            fmt = {
                "type": "json_schema",
                "name": "thinker_response",
                "schema": schema,
                "strict": True,
            }
            body.update(
                {
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {k: v for k, v in fmt.items() if k != "type"},
                    }
                }
                if protocol == "chat"
                else {"text": {"format": fmt}}
            )
        body.update(
            {k: v for k, v in settings.items() if k in {"temperature", "top_p", "seed", "stop"}}
        )
        reasoning = routing.get("reasoning")
        if reasoning:
            body["reasoning"] = reasoning
        base = route.endpoint or ("https://api.openai.com" if route.provider == "openai" else None)
        suffix = "/chat/completions" if protocol == "chat" else "/responses"
    elif protocol == "anthropic":
        if "seed" in settings:
            raise UnsupportedRequest("Anthropic seed is unsupported")
        if any("strict" in t for t in tools):
            raise UnsupportedRequest("Anthropic tool strict flag is not mapped by this adapter")
        body = {"model": route.model_id, "messages": native, "max_tokens": limit, "stream": False}
        if systems:
            body["system"] = systems
        if tools:
            body["tools"] = [{k: v for k, v in t.items() if k != "strict"} for t in tools]
        if schema is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        body.update({k: v for k, v in settings.items() if k in {"temperature", "top_p"}})
        if "stop" in settings:
            body["stop_sequences"] = settings["stop"]
        if routing.get("reasoning"):
            body["thinking"] = routing["reasoning"]
        base, suffix = route.endpoint or "https://api.anthropic.com", "/messages"
    else:
        if any("strict" in t for t in tools):
            raise UnsupportedRequest("Gemini tool strict flag is unsupported")
        generation = {"maxOutputTokens": limit}
        for field, native_field in {
            "temperature": "temperature",
            "top_p": "topP",
            "seed": "seed",
            "stop": "stopSequences",
        }.items():
            if field in settings:
                generation[native_field] = settings[field]
        if schema is not None:
            generation.update(responseMimeType="application/json", responseJsonSchema=schema)
        if routing.get("reasoning"):
            generation["thinkingConfig"] = routing["reasoning"]
        body = {"contents": native, "generationConfig": generation}
        if systems:
            body["systemInstruction"] = {"parts": systems}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": t["input_schema"],
                        }
                        for t in tools
                    ]
                }
            ]
        base = route.endpoint or "https://generativelanguage.googleapis.com"
        suffix = f"/models/{quote(route.model_id, safe='')}:generateContent"
    continuation = packet.get("continuation")
    if continuation is not None:
        if (
            protocol != "responses"
            or not isinstance(continuation, dict)
            or set(continuation) != {"response_id"}
        ):
            raise UnsupportedRequest("Replay native provider parts for this continuation format")
        body["previous_response_id"] = continuation["response_id"]
    if not base:
        raise UnsupportedRequest("Provider endpoint is required")
    base = base.rstrip("/")
    version = "/v1beta" if protocol == "gemini" else "/v1"
    url = (
        base
        if base.endswith(suffix)
        else base + ("" if base.endswith(version) else version) + suffix
    )
    # Explicit native options can tune generation but cannot silently replace a
    # common field. The caller must choose one representation for each setting.
    if (set(body) & set(options)) - {"stream"}:
        raise UnsupportedRequest("Duplicate common and native provider settings")
    if protocol == "gemini":
        options.pop("stream", None)
    body.update(options)
    return protocol, url, body


def lift(data, protocol):
    """Extract useful fields while retaining the complete native response."""
    if not isinstance(data, dict):
        raise TypeError("Provider response must be an object")
    result = {"status": "completed", "provider_data": data, "cost_usd": None}
    calls, texts = [], []
    if protocol == "responses":
        for item in data["output"]:
            if item.get("type") == "function_call":
                calls.append(
                    {
                        "call_id": item.get("call_id"),
                        "name": item.get("name"),
                        "arguments": item.get("arguments"),
                        "provider_data": item,
                    }
                )
            elif item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "refusal":
                        result["status"] = "refused"
                        texts.append(part.get("refusal", ""))
                    elif part.get("type") == "output_text":
                        texts.append(part.get("text", ""))
        reason = (data.get("incomplete_details") or {}).get("reason") or data.get("status")
        if data.get("status") in {"incomplete", "queued", "in_progress"}:
            result["status"] = "incomplete"
        elif data.get("status") == "cancelled":
            result["status"] = "cancelled"
        if data.get("id"):
            result["continuation"] = {"response_id": data["id"]}
        provider = "openai"
    elif protocol == "chat":
        choices = data["choices"]
        if not choices:
            raise ValueError("Provider returned no choices")
        choice = choices[0]
        message = choice["message"]
        result["content"] = message.get("content")
        if message.get("refusal"):
            result.update(status="refused", content=message["refusal"])
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            calls.append(
                {
                    "call_id": call.get("id"),
                    "name": function.get("name"),
                    "arguments": function.get("arguments"),
                    "provider_data": call,
                }
            )
        reason = choice.get("finish_reason")
        result["status"] = (
            "incomplete"
            if reason == "length"
            else "refused"
            if reason == "content_filter"
            else result["status"]
        )
        raw = data.get("usage") or {}
        result["usage"] = {
            "input_tokens": raw.get("prompt_tokens"),
            "output_tokens": raw.get("completion_tokens"),
            "total_tokens": raw.get("total_tokens"),
            "cached_input_tokens": (raw.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "reasoning_tokens": (raw.get("completion_tokens_details") or {}).get(
                "reasoning_tokens"
            ),
            "provider_data": raw,
        }
        provider = None
    elif protocol == "anthropic":
        for item in data["content"]:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif item.get("type") == "tool_use":
                calls.append(
                    {
                        "call_id": item.get("id"),
                        "name": item.get("name"),
                        "arguments": item.get("input"),
                        "provider_data": item,
                    }
                )
        reason = data.get("stop_reason")
        result["status"] = (
            "incomplete"
            if reason in {"max_tokens", "pause_turn"}
            else "refused"
            if reason == "refusal"
            else "completed"
        )
        provider = "anthropic"
    else:
        candidates = data.get("candidates", [])
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if not candidates and not blocked:
            raise ValueError("Provider returned no candidates")
        candidate = candidates[0] if candidates else {}
        for part in candidate.get("content", {}).get("parts", []):
            if "text" in part and not part.get("thought"):
                texts.append(part["text"])
            if "functionCall" in part:
                call = part["functionCall"]
                calls.append(
                    {
                        "call_id": call.get("id"),
                        "name": call.get("name"),
                        "arguments": call.get("args"),
                        "provider_data": part,
                    }
                )
        reason = blocked or candidate.get("finishReason")
        result["status"] = (
            "incomplete"
            if reason == "MAX_TOKENS"
            else "refused"
            if blocked
            or reason
            in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
            else "completed"
        )
        provider = "gemini"
    if protocol != "chat":
        result["content"] = "".join(texts)
    if provider:
        usage = usage_report(provider, data).model_dump()
        result["usage"] = {
            k: v for k, v in usage.items() if k not in {"raw", "visible_output_tokens"}
        }
        result["usage"]["provider_data"] = usage["raw"]
        if provider == "anthropic":
            result["usage"]["cache_creation_input_tokens"] = usage["raw"].get(
                "cache_creation_input_tokens"
            )
    # Optional provider metadata must not turn useful generated output into a
    # transport failure. Preserve nonstandard values in the native envelope.
    for call in calls:
        for field in ("name", "call_id"):
            if not isinstance(call[field], str):
                call[field] = None
    for field, value in result.get("usage", {}).items():
        if field != "provider_data" and (type(value) is not int or value < 0):
            result["usage"][field] = None
    result.update(tool_calls=calls, finish_reason=reason if isinstance(reason, str) else None)
    return result
