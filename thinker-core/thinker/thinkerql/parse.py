"""
ThinkerQL parser for Thinker Core.

This module provides parsing and validation of ThinkerQL requests,
extracting routing needs and normalizing the request format for
internal processing.

Author: Anjan Goswami
"""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import jsonschema

@dataclass
class InternalRequest:
    """Internal representation of a ThinkerQL request."""
    messages: List[Dict[str, Any]]
    documents: List[Dict[str, Any]]
    instructions: Dict[str, Any]
    routing: Dict[str, Any]
    need_modality: str
    need_caps: List[str]
    intent: str
    autoshrink_trace: Optional[List[Dict[str, Any]]] = None

def _load_schema(schema_name: str) -> Dict[str, Any]:
    """Load JSON schema from file."""
    schema_path = Path(__file__).parent / f"{schema_name}.json"
    return json.loads(schema_path.read_text())

def _determine_modality_and_caps(messages: List[Dict[str, Any]], instructions: Dict[str, Any], intent: str) -> tuple[str, List[str]]:
    """Determine required modality and capabilities from messages, instructions, and intent."""
    need_modality = "text"
    need_caps = []
    
    # Intent-based modality
    if intent == "embed":
        need_modality = "embed"
    elif intent in ["ocr", "doc_extract", "docai_extract"]:
        need_modality = "text"  # These are text-based operations
    
    # Check for images in messages (overrides intent-based modality)
    for message in messages:
        for part in message.get("parts", []):
            if part.get("type") == "image_url":
                need_modality = "multimodal"
                need_caps.append("images_in")
                break
    
    # Check for JSON schema in instructions
    if instructions.get("json_schema"):
        need_caps.append("json_mode")
    
    return need_modality, need_caps

def parse_thinkerql(ql: Dict[str, Any]) -> InternalRequest:
    """Parse and validate ThinkerQL request."""
    # Load and validate against schema
    schema = _load_schema("request_schema")
    
    try:
        jsonschema.validate(ql, schema)
    except jsonschema.ValidationError as e:
        # Provide more specific error messages for common cases
        error_msg = str(e.message)
        if "'0.2' is not one of ['0.3']" in error_msg or ("version" in error_msg and "not one of" in error_msg):
            raise ValueError(f"Unsupported version: {ql.get('version')}")
        elif "intent" in error_msg and "not one of" in error_msg:
            raise ValueError(f"Invalid intent: {ql.get('intent')}")
        elif "type" in error_msg and "not one of" in error_msg:
            raise ValueError(f"Invalid part type: {error_msg}")
        elif "required property" in error_msg:
            raise ValueError(f"Missing required field: {error_msg}")
        else:
            raise ValueError(f"Schema validation error: {error_msg}")
    
    # Extract fields
    version = ql["version"]
    intent = ql["intent"]
    messages = ql["messages"]
    documents = ql.get("documents", [])
    instructions = ql.get("instructions", {})
    routing = ql.get("routing", {})
    
    # Validate version
    if version != "0.3":
        raise ValueError(f"Unsupported version: {version}")
    
    # Validate intent
    valid_intents = ["chat", "embed", "ocr", "doc_extract", "docai_extract"]
    if intent not in valid_intents:
        raise ValueError(f"Invalid intent: {intent}")
    
    # Validate messages structure
    for message in messages:
        if "role" not in message or "parts" not in message:
            raise ValueError("Missing required field: role or parts")
        
        for part in message["parts"]:
            if "type" not in part:
                raise ValueError("Missing required field: type")
            
            part_type = part["type"]
            if part_type not in ["text", "image_url", "document"]:
                raise ValueError(f"Invalid part type: {part_type}")
            
            # Validate part-specific fields
            if part_type == "text" and "text" not in part:
                raise ValueError("Text part missing 'text' field")
            elif part_type == "image_url" and "image_url" not in part:
                raise ValueError("Image part missing 'image_url' field")
            elif part_type == "document" and "content" not in part:
                raise ValueError("Document part missing 'content' field")
    
    # Determine modality and capabilities
    need_modality, need_caps = _determine_modality_and_caps(messages, instructions, intent)
    
    return InternalRequest(
        messages=messages,
        documents=documents,
        instructions=instructions,
        routing=routing,
        need_modality=need_modality,
        need_caps=need_caps,
        intent=intent
    )
