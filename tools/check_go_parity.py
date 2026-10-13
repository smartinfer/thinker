"""Compare Go against Python packet, native-wire, usage, and error behavior offline.

Run with the Python development environment installed. Go must be on PATH.
No provider calls, cloud writes, or dependency downloads are performed.
"""

import json
import os
import runpy
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path

from thinker.thinkerql import classify_error, parse_request
from thinker.thinkerql.wire import UnsupportedRequest, lift, prepare

ROOT = Path(__file__).resolve().parents[1]


def cases():
    helpers = runpy.run_path(
        str(ROOT / "thinker-core/thinker/tests/test_modelcalls.py")
    )
    packet, route, response = (helpers[k] for k in ("packet", "route", "response"))
    fixtures = []
    for protocol in ("chat", "responses", "anthropic", "gemini"):
        for variant in (
            "plain",
            "schema",
            "tools",
            "replay",
            "usage",
            "odd_usage",
            "opaque",
            "empty_reasoning",
            "escaped_model",
            "portable_reasoning",
            "portable_high",
            "token_limit",
            "options_token_limit",
            "usage_no_thoughts",
        ):
            selected = route(protocol)
            request = packet()
            native = response(protocol)
            native["exact_large_id"] = 90071992547409931234567
            if variant == "empty_reasoning":
                request["routing"] = {"reasoning": {}}
            if variant == "portable_reasoning":
                request["routing"] = {"reasoning": {"effort": "none"}}
            if variant == "portable_high":
                request["routing"] = {"reasoning": {"effort": "high"}}
            if variant == "token_limit":
                selected = route(protocol, token_limit_field="max_completion_tokens")
            if variant == "options_token_limit":
                request["provider_options"] = {"max_tokens": 9}
            if variant == "escaped_model":
                selected = route(protocol, model_id="test/model:with space+")
            if variant == "schema":
                request["instructions"] = {
                    "json_schema": {
                        "type": "object",
                        "properties": {
                            "decision": {"enum": ["accept", "revise", "ask"]}
                        },
                        "required": ["decision"],
                    }
                }
                if protocol in {"chat", "responses"}:
                    request["instructions"]["json_schema_strict"] = False
            if variant == "tools":
                request["tools"] = [
                    {"name": "read", "input_schema": {"type": "object"}}
                ]
                if protocol in {"chat", "responses"}:
                    request["tools"][0]["strict"] = True
            if variant == "replay":
                request["messages"].append(
                    {
                        "role": "assistant",
                        "tool_calls": lift(native, protocol)["tool_calls"],
                    }
                )
                request["tool_results"] = [
                    {"call_id": "tc1", "output": {"bad": 7}, "is_error": True}
                ]
                if protocol == "responses":
                    request["continuation"] = {"response_id": "resp1"}
            if variant == "opaque":
                part = {
                    "responses": {"type": "reasoning", "encrypted_content": "opaque"},
                    "anthropic": {
                        "type": "thinking",
                        "thinking": "",
                        "signature": "opaque",
                    },
                    "gemini": {"text": "", "thoughtSignature": "opaque"},
                    "chat": {"type": "text", "text": "native part"},
                }[protocol]
                request["messages"].append(
                    {
                        "role": "assistant",
                        "parts": [
                            {
                                "type": "provider",
                                "provider": selected.provider,
                                "data": part,
                            }
                        ],
                    }
                )
            if variant == "usage_no_thoughts":
                # Live gemini-2.5-flash shape with thinking off (2026-10-11):
                # zero-valued counts are omitted.
                native["usageMetadata"] = {
                    "promptTokenCount": 8478,
                    "candidatesTokenCount": 136,
                    "totalTokenCount": 8614,
                }
            if variant in {"usage", "odd_usage"}:
                native["usage"] = {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "total_tokens": 18,
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "input_tokens_details": {"cached_tokens": 3},
                    "output_tokens_details": {"reasoning_tokens": 2},
                    "prompt_tokens_details": {"cached_tokens": 3},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 4,
                }
                native["usageMetadata"] = {
                    "promptTokenCount": 11,
                    "candidatesTokenCount": 5,
                    "thoughtsTokenCount": 2,
                    "totalTokenCount": 18,
                    "cachedContentTokenCount": 3,
                }
                if variant == "odd_usage":
                    for field in ("input_tokens", "prompt_tokens"):
                        native["usage"][field] = 11.0
                    native["usageMetadata"]["thoughtsTokenCount"] = "unknown"
            try:
                p, url, body = prepare(parse_request(request), selected)
            except UnsupportedRequest:
                # Go must refuse exactly the requests Python refuses.
                fixtures.append(
                    {
                        "name": f"{protocol}/{variant}",
                        "kind": "wire_reject",
                        "request": request,
                        "route": selected.model_dump(),
                    }
                )
                continue
            fixtures.append(
                {
                    "name": f"{protocol}/{variant}",
                    "kind": "wire",
                    "request": request,
                    "route": selected.model_dump(),
                    "protocol": p,
                    "url": url,
                    "body": body,
                    "response": native,
                    "result": lift(native, p),
                }
            )

    errors = json.loads(
        (ROOT / "thinker-core/thinker/tests/fixtures/thinkerql/errors.json").read_text()
    )
    for item in errors:
        fixtures.append(
            {
                "name": "error/" + item["name"],
                "kind": "error",
                "input": item["input"],
                "expected": classify_error(**item["input"]),
            }
        )
    mutations = [
        {},
        {"version": "0.3"},
        {"extra": True},
        {"request_id": ""},
        {"job_id": 3},
        {"timeout_ms": 0},
        {"timeout_ms": 0.5},
        {"timeout_ms": 1.0},
        {"instructions": {"json_schema_strict": True}},
        {
            "instructions": {
                "json_schema": {"unsupported": 7},
                "json_schema_strict": False,
            }
        },
        {"instructions": {"json_schema": {}, "json_schema_strict": "true"}},
        {"routing": {"route_id": "x", "model": "y"}},
        {"routing": {"alias": "x", "model": "y"}},
        {"routing": {"route_id": "x", "alias": "y"}},
        {"messages": []},
        {"messages": [{"role": "user", "parts": []}]},
        {"messages": [{"role": "assistant", "tool_calls": [{}]}]},
        {"messages": [{"role": "user", "tool_calls": [{}]}]},
        {
            "messages": [
                {"role": "tool", "tool_results": [{"call_id": "tc", "output": None}]}
            ]
        },
        {
            "messages": [
                {
                    "role": "assistant",
                    "tool_results": [{"call_id": "tc", "output": None}],
                }
            ]
        },
        {
            "messages": [
                {
                    "role": "user",
                    "parts": [{"type": "text", "text": "ok", "extra": True}],
                }
            ]
        },
        {
            "messages": [
                {
                    "role": "user",
                    "parts": [{"type": "provider", "provider": "openai", "data": None}],
                }
            ]
        },
        {"instructions": {"max_output_tokens": -1}},
    ]
    for index, changes in enumerate(mutations):
        request = packet(**deepcopy(changes))
        try:
            parse_request(request)
            valid = True
        except ValueError:
            valid = False
        fixtures.append(
            {
                "name": f"packet/{index}",
                "kind": "packet",
                "request": request,
                "valid": valid,
            }
        )
    return fixtures


def main():
    fixtures = cases()
    with tempfile.TemporaryDirectory(prefix="thinker-go-parity-") as directory:
        path = Path(directory) / "parity.json"
        path.write_text(json.dumps(fixtures))
        subprocess.run(
            ["go", "test", "-count=1", "-run", "^TestPythonParity$", "./..."],
            cwd=ROOT / "thinker-go",
            env={**os.environ, "THINKER_GO_PARITY_FILE": str(path)},
            check=True,
        )
    print(f"Compared {len(fixtures)} Python/Go cases without live calls.")


if __name__ == "__main__":
    main()
