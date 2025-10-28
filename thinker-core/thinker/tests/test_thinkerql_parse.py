"""
Unit tests for ThinkerQL parser.

This module tests the ThinkerQL request parsing including schema validation,
routing needs extraction, and error handling for malformed requests.

Author: Anjan Goswami
"""

import pytest
import json
from pathlib import Path
from thinker.thinkerql.parse import parse_thinkerql, InternalRequest

def test_parse_text_chat():
    """Test parsing a simple text chat request."""
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Hello, world!"}]
            }
        ],
        "routing": {"model": "openai/gpt-4o-mini"}
    }
    
    req = parse_thinkerql(ql)
    
    assert req.intent == "chat"
    assert req.need_modality == "text"
    assert req.need_caps == []
    assert req.routing["model"] == "openai/gpt-4o-mini"
    assert len(req.messages) == 1
    assert req.messages[0]["role"] == "user"
    assert req.messages[0]["parts"][0]["text"] == "Hello, world!"

def test_parse_multimodal_with_image():
    """Test parsing a multimodal request with image."""
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
                ]
            }
        ],
        "routing": {"call_id": "openai:gpt-4o-mini.chat"}
    }
    
    req = parse_thinkerql(ql)
    
    assert req.intent == "chat"
    assert req.need_modality == "multimodal"
    assert "images_in" in req.need_caps
    assert req.routing["call_id"] == "openai:gpt-4o-mini.chat"
    assert len(req.messages[0]["parts"]) == 2
    assert req.messages[0]["parts"][1]["type"] == "image_url"

def test_parse_embed_request():
    """Test parsing an embedding request."""
    ql = {
        "version": "0.3",
        "intent": "embed",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Embed this text"}]
            }
        ],
        "routing": {"model": "openai/text-embedding-3-large"}
    }
    
    req = parse_thinkerql(ql)
    
    assert req.intent == "embed"
    assert req.need_modality == "embed"
    assert req.need_caps == []
    assert req.routing["model"] == "openai/text-embedding-3-large"

def test_parse_with_documents():
    """Test parsing request with documents."""
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Analyze this document"}]
            }
        ],
        "documents": [
            {"content": "Document content here", "mime_type": "text/plain"}
        ],
        "routing": {"alias": "openai:multimodal-cheap"}
    }
    
    req = parse_thinkerql(ql)
    
    assert req.intent == "chat"
    assert req.need_modality == "text"  # Documents don't change modality
    assert len(req.documents) == 1
    assert req.documents[0]["content"] == "Document content here"
    assert req.routing["alias"] == "openai:multimodal-cheap"

def test_parse_with_instructions():
    """Test parsing request with instructions."""
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Generate JSON"}]
            }
        ],
        "instructions": {
            "max_output_tokens": 1000,
            "temperature": 0.7,
            "json_schema": {"type": "object", "properties": {"result": {"type": "string"}}}
        },
        "routing": {"model": "openai/gpt-4o-mini"}
    }
    
    req = parse_thinkerql(ql)
    
    assert req.instructions["max_output_tokens"] == 1000
    assert req.instructions["temperature"] == 0.7
    assert "json_schema" in req.instructions
    assert "json_mode" in req.need_caps  # JSON schema should add json_mode capability

def test_parse_invalid_version():
    """Test parsing with invalid version."""
    ql = {
        "version": "0.2",  # Invalid version
        "intent": "chat",
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Hello"}]}]
    }
    
    with pytest.raises(ValueError, match="Unsupported version"):
        parse_thinkerql(ql)

def test_parse_invalid_intent():
    """Test parsing with invalid intent."""
    ql = {
        "version": "0.3",
        "intent": "invalid_intent",
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Hello"}]}]
    }
    
    with pytest.raises(ValueError, match="Invalid intent"):
        parse_thinkerql(ql)

def test_parse_invalid_part_type():
    """Test parsing with invalid part type."""
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "invalid_type", "text": "Hello"}]
            }
        ]
    }
    
    with pytest.raises(ValueError, match="Invalid part type"):
        parse_thinkerql(ql)

def test_parse_missing_required_fields():
    """Test parsing with missing required fields."""
    ql = {
        "version": "0.3",
        "intent": "chat"
        # Missing messages
    }
    
    with pytest.raises(ValueError, match="Missing required field"):
        parse_thinkerql(ql)

def test_parse_docai_intent():
    """Test parsing docai intent maps to docai kind."""
    ql = {
        "version": "0.3",
        "intent": "ocr",
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "OCR this"}]}]
    }
    
    req = parse_thinkerql(ql)
    
    assert req.intent == "ocr"
    assert req.need_modality == "text"  # OCR is text-based
    assert req.need_caps == []
