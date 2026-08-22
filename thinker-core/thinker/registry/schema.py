"""
Registry data models for Thinker Core.

This module defines the Pydantic models for the registry system, including
RegistryCall, Limits, Price, Catalog, and AliasRule. These models provide
strict validation and type safety for the registry-driven LLM routing system.

Author: Anjan Goswami
"""

from typing import Literal, Optional, List, Dict
from pydantic import BaseModel, Field, field_validator

Modality = Literal["text","multimodal","vision","audio","video","embed","image_output"]
Kind     = Literal["chat","embed","docai","image_generation"]

class Limits(BaseModel):
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    qps: Optional[int] = Field(default=None, ge=1)
    tokens_per_sec: Optional[int] = Field(default=None, ge=1)

class Price(BaseModel):
    input_per_1k: float = Field(ge=0.0)
    output_per_1k: float = Field(ge=0.0)

class ImageLimits(BaseModel):
    supported_sizes: List[str] = []
    supported_aspect_ratios: List[str] = []
    max_n: int = Field(default=1, ge=1)
    seed_supported: bool = False
    quality_supported: bool = False
    supported_qualities: List[str] = []
    reference_images_supported: bool = False

class ImagePrice(BaseModel):
    known: bool = False
    per_image: Optional[float] = Field(default=None, ge=0.0)
    per_image_by_size: Dict[str, float] = {}
    per_image_by_quality: Dict[str, float] = {}
    per_image_by_size_quality: Dict[str, float] = {}
    prompt_input_per_1m: float = Field(default=0.0, ge=0.0)
    image_output_per_1m: Optional[float] = Field(default=None, ge=0.0)
    currency: str = "USD"

class RegistryCall(BaseModel):
    call_id: str                                  # "provider:model.kind", e.g., "openai:gpt-4o-mini.chat"
    provider: str                                  # "openai"
    model_id: str                                  # "gpt-4o-mini"
    kind: Kind                                     # chat|embed|docai
    modality: Modality
    caps: List[str] = []                           # e.g., ["json_mode","tools","images_in"]
    limits: Limits
    price: Price
    adapter: str                                   # "openai" | "local" | ...
    payload_style: str                             # "chat_completions_v1" | ...
    endpoint: Optional[str] = None
    aliases: List[str] = []                        # ["openai:multimodal-cheap"]
    image_limits: Optional[ImageLimits] = None
    image_price: Optional[ImagePrice] = None

    @field_validator("call_id")
    @classmethod
    def _format(cls, v):
        # Very light sanity check: provider:model.kind
        assert v.count(":")==1 and v.count(".")==1, "call_id must be provider:model.kind"
        return v

class Catalog(BaseModel):
    version: str = "1"
    calls: List[RegistryCall] = []
    meta: Dict[str, str] = {}


# Optional: AliasRule if you want rule-based aliases:

class AliasRule(BaseModel):
    name: str                        # "any:multimodal-cheap"
    match: Dict[str, List[str]] = {} # {"modality":["multimodal","vision"], "caps_all":["images_in"]}
    order_by: List[str] = []         # ["price.input_per_1k", "-limits.max_input_tokens"]
