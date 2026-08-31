"""
Interactive agent CLI for Story Generator example.

Author: Anjan Goswami
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Any, List
import argparse
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from agent_prompts import AGENT_SYSTEM_PROMPT
from story_generator import (
    init_thinker_for_local,
    load_config,
    generate_story,
    save_story,
)
from thinker.core import Thinker

# Ensure OpenAI key is available without manual export by reading secure store
import os
try:
    if not os.getenv("OPENAI_API_KEY"):
        try:
            from thinker.registry.secure_credentials import SecureCredentials
            _k = SecureCredentials().get("openai")
        except Exception:
            _k = None
        if not _k:
            try:
                from thinker.registry.auth import Credentials
                _k = Credentials().get("openai")
            except Exception:
                _k = None
        if _k:
            os.environ["OPENAI_API_KEY"] = _k
except Exception:
    pass


def parse_ready_payload(text: str) -> Dict[str, Any] | None:
    marker = "READY_TO_GENERATE"
    if marker not in text:
        return None
    try:
        after = text.split(marker, 1)[1]
        # Robustly extract the first JSON object after the marker
        import re
        m = re.search(r"\{[\s\S]*\}", after)
        if not m:
            return None
        payload_str = m.group(0)
        return json.loads(payload_str)
    except Exception:
        return None


def infer_requirements_from_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Very simple heuristic extraction from conversation if JSON wasn't provided."""
    text_blob = "\n".join(
        part.get("text", "")
        for msg in messages if msg.get("role") == "user"
        for part in msg.get("parts", []) if part.get("type") == "text"
    ).lower()
    req: Dict[str, Any] = {}
    # Child name (look for 'name is X' or single capitalized token) – fallback
    req["child_name"] = "Friend"
    # Age
    import re
    age_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:year|yr)", text_blob)
    req["age"] = float(age_m.group(1)) if age_m else 5.0
    # Gender
    if " boy" in text_blob:
        req["gender"] = "boy"
    elif " girl" in text_blob:
        req["gender"] = "girl"
    else:
        req["gender"] = "child"
    # Characters
    chars = []
    for animal in ["wolf", "rabbit", "bunny", "bear", "fox", "robot", "princess"]:
        if animal in text_blob:
            chars.append(animal)
    req["characters"] = chars or ["a friendly animal"]
    # Setting
    if "forest" in text_blob:
        req["setting"] = "forest"
    elif "sea" in text_blob or "ocean" in text_blob:
        req["setting"] = "under the sea"
    else:
        req["setting"] = "a friendly park"
    # Morale
    if "friend" in text_blob:
        req["morale"] = "friendship"
    elif "kind" in text_blob:
        req["morale"] = "kindness"
    else:
        req["morale"] = "kindness"
    # Length
    if "short" in text_blob:
        req["length"] = "short"
    elif "long" in text_blob:
        req["length"] = "long"
    else:
        req["length"] = "medium"
    return req


def main():
    console = Console()
    base = Path(__file__).parent
    config = load_config(base / "config.yaml")

    # CLI overrides
    ap = argparse.ArgumentParser(description="Story Generator (Thinker Example)")
    ap.add_argument("--provider", choices=["openai", "ollama"], default="openai", help="Provider to use for both agent and story")
    ap.add_argument("--agent-model", help="Agent model (for ollama)")
    ap.add_argument("--story-model", help="Story model (for ollama)")
    ap.add_argument("--openai-model", default="openai/gpt-4o-mini", help="OpenAI model to use (agent & story)")
    args = ap.parse_args([] if sys.argv[0].endswith("story_agent.py") and len(sys.argv)==1 else None)

    agent_model = args.agent_model or config["agent"]["model"]
    story_model = args.story_model or config["story"]["ollama"]["default"]
    openai_model = args.openai_model
    provider = args.provider
    ollama_endpoint = config["ollama"]["endpoint"]

    # Provider selection
    if provider == "openai":
        thinker = Thinker.from_files("../../spec/registry.yaml")
        agent_route = {"model": openai_model}
        story_model_openai = openai_model
        use_openai = True
    else:
        thinker, ids = init_thinker_for_local(agent_model, story_model, ollama_endpoint)
        agent_route = {"call_id": ids["agent_call_id"]}
        use_openai = False

    console.print(Panel("🌟 Welcome to Story Time! I'll help you craft a bedtime story.", title="Story Generator"))

    messages = [
        {"role": "system", "parts": [{"type": "text", "text": AGENT_SYSTEM_PROMPT}]}
    ]
    last_ready_payload = None
    turns = 0

    while True:
        user_input = Prompt.ask("You")

        # Fast path: allow explicit user request to generate now
        lowered = user_input.strip().lower()
        if any(k in lowered for k in ["generate now", "generate the story", "assume", "use context", "already gave you the context", "just generate", "go ahead and generate", "generate with", "generate story now"]):
            if last_ready_payload:
                payload = dict(last_ready_payload)
                # Sensible defaults for missing fields
                payload.setdefault("gender", "child")
                if not payload.get("characters"):
                    payload["characters"] = ["a friendly animal"]
                payload.setdefault("setting", "a friendly forest")
                payload.setdefault("morale", "kindness")
                payload.setdefault("length", "short")
                if use_openai:
                    result = generate_story(thinker, None, payload, model=story_model_openai)
                else:
                    result = generate_story(thinker, ids["story_call_id"], payload)
                out_dir = base / config["output"]["directory"]
                fp = save_story(result["story"], payload, result, str(out_dir))
                console.print(Panel(f"Saved story to: {fp}", title="Done", style="green"))
                return
            else:
                # If we don't have structured info yet, infer from conversation and proceed
                inferred = infer_requirements_from_messages(messages)
                if use_openai:
                    result = generate_story(thinker, None, inferred, model=story_model_openai)
                else:
                    result = generate_story(thinker, ids["story_call_id"], inferred)
                out_dir = base / config["output"]["directory"]
                fp = save_story(result["story"], inferred, result, str(out_dir))
                console.print(Panel(f"Saved story to: {fp}", title="Done", style="green"))
                return
        messages.append({"role": "user", "parts": [{"type": "text", "text": user_input}]})

        # Quick early-extract: if we already have enough info from conversation, offer one-tap generate
        inferred = infer_requirements_from_messages(messages)
        have_core = bool(inferred.get("characters")) and bool(inferred.get("setting"))
        if have_core and any(k in user_input.lower() for k in ["generate", "go ahead", "looks good", "ok", "okay", "confirm", "ready"]):
            if use_openai:
                result = generate_story(thinker, None, inferred, model=story_model_openai)
            else:
                result = generate_story(thinker, ids["story_call_id"], inferred)
            out_dir = base / config["output"]["directory"]
            fp = save_story(result["story"], inferred, result, str(out_dir))
            console.print(Panel(f"Saved story to: {fp}", title="Done", style="green"))
            return

        ql = {
            "version": "0.3",
            "intent": "chat",
            "messages": messages,
            "routing": agent_route,
            "instructions": {"max_output_tokens": config["agent"]["max_tokens"]},
        }

        try:
            resp = thinker.chat_ql(ql)
            text = resp.text
        except Exception as e:
            console.print(Panel(f"Error calling provider: {e}", title="Error", style="red"))
            return
        payload = parse_ready_payload(text)

        if payload is not None:
            last_ready_payload = payload
            # Confirm
            preview = (
                f"Child: {payload['child_name']} ({payload['age']} yrs, {payload.get('gender','n/a')})\n"
                f"Characters: {', '.join(payload['characters'])}\n"
                f"Setting: {payload['setting']}\n"
                f"Lesson: {payload['morale']}\n"
                f"Length: {payload['length']}"
            )
            console.print(Panel(preview, title="Confirmation"))
            ok = Prompt.ask("Generate story now? (yes/no)", choices=["yes", "no"], default="yes")
            if ok == "yes":
                if use_openai:
                    result = generate_story(thinker, None, payload, model=story_model_openai)
                else:
                    result = generate_story(thinker, ids["story_call_id"], payload)
                out_dir = base / config["output"]["directory"]
                fp = save_story(result["story"], payload, result, str(out_dir))
                console.print(Panel(f"Saved story to: {fp}", title="Done", style="green"))
                return
            else:
                # let user continue conversation to adjust
                continue
        else:
            # Show agent message
            console.print(Panel(text, title="Agent", style="cyan"))
            turns += 1
            if turns > 20:
                console.print(Panel("Stopping after too many turns. Try 'generate now' once you've provided basics.", title="Stopping", style="yellow"))
                return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

