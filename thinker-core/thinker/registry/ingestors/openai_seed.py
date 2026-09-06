"""
OpenAI seed ingestor for Thinker Core.

This module provides static model definitions for OpenAI models including
GPT-4o-mini and text-embedding-3-large. These hand-curated definitions
serve as the foundation for the registry system in the MVP.

Author: Anjan Goswami
"""

from ..schema import Catalog, Limits, Price, RegistryCall


def ingest() -> Catalog:
    calls = [
        RegistryCall(
            call_id="openai:gpt-4o-mini.chat",
            provider="openai",
            model_id="gpt-4o-mini",
            kind="chat",
            modality="multimodal",
            caps=[
                "model_turn_v1",
                "json_mode",
                "tools",
                "multiple_tool_calls",
                "tool_result_continuation",
                "provider_continuation",
                "images_in",
            ],
            limits=Limits(max_input_tokens=128000, max_output_tokens=16384),
            price=Price(input_per_1k=0.00015, output_per_1k=0.00060),
            adapter="openai",
            payload_style="chat_completions_v1",
            aliases=["openai:multimodal-cheap"],
        ),
        RegistryCall(
            call_id="openai:text-embedding-3-large.embed",
            provider="openai",
            model_id="text-embedding-3-large",
            kind="embed",
            modality="embed",
            caps=[],
            limits=Limits(max_input_tokens=8192, max_output_tokens=0),
            price=Price(input_per_1k=0.00002, output_per_1k=0.0),
            adapter="openai",
            payload_style="embeddings_v1",
            aliases=["openai:embed-cheap"],
        ),
    ]
    return Catalog(calls=calls)
