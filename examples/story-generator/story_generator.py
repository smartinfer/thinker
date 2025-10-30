"""
Story generator module for the Story Generator example.

Author: Anjan Goswami
"""

from __future__ import annotations

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Tuple
import re

from thinker.core import Thinker
from thinker.registry.schema import Catalog, RegistryCall, Limits, Price
from thinker.registry.store import RegistryStore
from thinker.pricebook import PriceBook


def _make_local_catalog(agent_model: str, story_model: str, ollama_endpoint: str) -> Catalog:
    """Create an in-memory catalog for local Ollama models."""
    sanitized_agent = agent_model.replace(':','-').replace('.', '-')
    agent_call = RegistryCall(
        call_id=f"local:{sanitized_agent}.chat",
        provider="local",
        model_id=agent_model,
        kind="chat",
        modality="text",
        caps=["json_mode"],
        limits=Limits(max_input_tokens=8192, max_output_tokens=1024),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="ollama",
        payload_style="ollama_chat",
        endpoint=f"{ollama_endpoint}/api/chat",
    )
    sanitized_story = story_model.replace(':','-').replace('.', '-')
    story_call = RegistryCall(
        call_id=f"local:{sanitized_story}.chat",
        provider="local",
        model_id=story_model,
        kind="chat",
        modality="text",
        caps=["json_mode"],
        limits=Limits(max_input_tokens=32768, max_output_tokens=2048),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="ollama",
        payload_style="ollama_chat",
        endpoint=f"{ollama_endpoint}/api/chat",
    )
    return Catalog(calls=[agent_call, story_call])


def init_thinker_for_local(agent_model: str, story_model: str, ollama_endpoint: str) -> Tuple[Thinker, Dict[str, str]]:
    """Initialize a Thinker instance with a local in-memory catalog for Ollama."""
    catalog = _make_local_catalog(agent_model, story_model, ollama_endpoint)
    store = RegistryStore()
    store.apply_catalog(catalog, version_id="local-ollama", source="story-generator")
    pricebook = PriceBook.from_file("dummy_path")
    # Return sanitized call_ids that match those stored in the catalog
    sanitized_agent = agent_model.replace(':','-').replace('.', '-')
    sanitized_story = story_model.replace(':','-').replace('.', '-')
    return Thinker(store, pricebook), {
        "agent_call_id": f"local:{sanitized_agent}.chat",
        "story_call_id": f"local:{sanitized_story}.chat",
    }


def _normalize_requirements(req: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize LLM-provided fields into strict types for prompt building."""
    out = dict(req)
    # Normalize age -> float (accepts strings like "5", "5.5", "5 years")
    age_raw = out.get("age")
    if isinstance(age_raw, (int, float)):
        age_val = float(age_raw)
    elif isinstance(age_raw, str):
        match = re.search(r"\d+(?:\.\d+)?", age_raw)
        age_val = float(match.group(0)) if match else 5.0
    else:
        age_val = 5.0
    out["age"] = age_val

    # Normalize length -> one of {short, medium, long}
    length_raw = str(out.get("length", "short")).lower()
    if "short" in length_raw:
        out["length"] = "short"
    elif "medium" in length_raw or "mid" in length_raw:
        out["length"] = "medium"
    elif "long" in length_raw:
        out["length"] = "long"
    else:
        out["length"] = "short"

    # Normalize characters -> list[str]
    chars = out.get("characters")
    if isinstance(chars, str):
        out["characters"] = [c.strip() for c in chars.split(",") if c.strip()]
    elif isinstance(chars, list):
        out["characters"] = [str(c).strip() for c in chars if str(c).strip()]
    else:
        out["characters"] = []

    # Normalize textual fields
    out["child_name"] = str(out.get("child_name", "Friend")).strip() or "Friend"
    out["gender"] = str(out.get("gender", "child")).strip() or "child"
    out["setting"] = str(out.get("setting", "a friendly park")).strip() or "a friendly park"
    out["morale"] = str(out.get("morale", "kindness")).strip() or "kindness"

    return out


def create_story_prompt(requirements: Dict[str, Any]) -> Dict[str, Any]:
    """Create a ThinkerQL request for story generation."""
    requirements = _normalize_requirements(requirements)
    age = requirements["age"]
    if age <= 2.0:
        vocab_level = "very simple words, 1-2 syllables, repetitive"
    elif age <= 4.0:
        vocab_level = "simple words, 1-2 syllables, some variety"
    else:
        vocab_level = "simple to moderate words, up to 3 syllables"

    length_map = {"short": "3-4", "medium": "5-7", "long": "8-10"}

    prompt = f"""
Create a children's bedtime story with these requirements:

FOR: {requirements['child_name']}, {age} years old, {requirements.get('gender', 'child')}
CHARACTERS: {', '.join(requirements['characters'])}
SETTING: {requirements['setting']}
LESSON: {requirements['morale']}
LENGTH: {length_map[requirements['length']]} paragraphs
VOCABULARY: {vocab_level}

REQUIREMENTS:
- Use age-appropriate language for {age}-year-olds
- Simple, clear sentence structure
- Warm, positive, uplifting tone
- Clear beginning, middle, and end
- The lesson about {requirements['morale']} should be natural, not preachy
- Perfect for reading aloud before bedtime
- Absolutely no scary, sad, or inappropriate elements
- Include the child's name in the story to make it personal

Create a complete, magical story that will help {requirements['child_name']}
drift off to sleep with wonderful dreams.
"""

    return {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {"role": "user", "parts": [{"type": "text", "text": prompt}]}
        ],
        "instructions": {"max_output_tokens": 1000},
    }


def generate_story(thinker: Thinker, call_id: str | None, requirements: Dict[str, Any], *, model: str | None = None) -> Dict[str, Any]:
    """Generate a story using the provided Thinker and routing info.

    Either provide call_id (e.g., local:...chat) or a model (e.g., openai/gpt-4o-mini).
    """
    ql = create_story_prompt(requirements)
    if model:
        ql["routing"] = {"model": model}
    else:
        ql["routing"] = {"call_id": call_id}
    response = thinker.chat_ql(ql)
    return {
        "story": response.text,
        "model": response.model,
        "provider": response.provider,
        "tokens": response.tokens,
        "cost": response.cost_usd,
    }


def validate_story(story: str, requirements: Dict[str, Any]) -> Tuple[bool, list[str]]:
    """Validate story meets simple requirements."""
    issues: list[str] = []
    if len(story.split()) < 100:
        issues.append("Story too short")
    for char in requirements.get("characters", []):
        if char.lower() not in story.lower():
            issues.append(f"Character '{char}' not found in story")
    if requirements["setting"].lower() not in story.lower():
        issues.append(f"Setting '{requirements['setting']}' not clearly described")
    morale_keywords = requirements["morale"].lower().split()
    if not any(kw in story.lower() for kw in morale_keywords):
        issues.append(f"Lesson about '{requirements['morale']}' not evident")
    return len(issues) == 0, issues


def save_story(story: str, requirements: Dict[str, Any], metadata: Dict[str, Any], output_dir: str) -> str:
    """Save story to file with metadata header."""
    from datetime import datetime
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    child = requirements["child_name"].replace(" ", "_")
    fp = os.path.join(output_dir, f"{child}_{ts}.txt")
    with open(fp, "w") as f:
        f.write("=" * 60 + "\n")
        f.write(f"Story for: {requirements['child_name']}\n")
        f.write(f"Age: {requirements['age']} years old\n")
        f.write(f"Characters: {', '.join(requirements['characters'])}\n")
        f.write(f"Setting: {requirements['setting']}\n")
        f.write(f"Lesson: {requirements['morale']}\n")
        f.write("\n")
        f.write(f"Model: {metadata['model']} ({metadata['provider']})\n")
        f.write(f"Tokens: {metadata['tokens']['input']} in, {metadata['tokens']['output']} out\n")
        f.write(f"Cost: ${metadata['cost']:.4f}\n")
        f.write("=" * 60 + "\n\n")
        f.write(story)
    return fp


def load_config(config_path: str | Path) -> Dict[str, Any]:
    return yaml.safe_load(Path(config_path).read_text())


