#!/usr/bin/env python3
"""
Example script demonstrating Thinker Core package usage.

This script shows how to use the Thinker package for LLM access
with registry-driven routing and ThinkerQL specification.

Author: Anjan Goswami
"""

from thinker import Thinker
from thinker.registry.store import RegistryStore
from thinker.pricebook import PriceBook
from thinker.registry.schema import RegistryCall, Limits, Price
from thinker.primitives import map_chat

def main():
    """Demonstrate Thinker Core functionality."""
    print("🚀 Thinker Core Package Demo")
    print("=" * 40)
    
    # 1. Create registry store
    print("\n1. Setting up registry store...")
    store = RegistryStore()
    
    # Add a local echo model
    echo_call = RegistryCall(
        call_id="local:echo.chat",
        provider="local",
        model_id="echo",
        kind="chat",
        modality="text",
        caps=["json_mode"],
        limits=Limits(max_input_tokens=8192, max_output_tokens=1024),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="local",
        payload_style="echo"
    )
    store.put_call(echo_call)
    print(f"✅ Added model: {echo_call.call_id}")
    
    # 2. Create Thinker instance
    print("\n2. Creating Thinker instance...")
    pricebook = PriceBook()
    thinker = Thinker(store, pricebook)
    print("✅ Thinker instance created")
    
    # 3. Single request
    print("\n3. Processing single ThinkerQL request...")
    ql_request = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Hello from Thinker Core!"}]
            }
        ],
        "routing": {"call_id": "local:echo.chat"}
    }
    
    response = thinker.chat_ql(ql_request)
    print(f"✅ Response: {response.text}")
    print(f"   Model: {response.model}")
    print(f"   Provider: {response.provider}")
    print(f"   Tokens: {response.tokens}")
    print(f"   Cost: ${response.cost_usd:.4f}")
    
    # 4. Batch processing
    print("\n4. Processing batch requests...")
    batch_requests = [
        {
            "version": "0.3",
            "intent": "chat",
            "messages": [
                {
                    "role": "user",
                    "parts": [{"type": "text", "text": f"Request {i}"}]
                }
            ],
            "routing": {"call_id": "local:echo.chat"}
        }
        for i in range(1, 4)
    ]
    
    batch_responses = map_chat(thinker, batch_requests)
    print(f"✅ Processed {len(batch_responses)} requests:")
    for i, resp in enumerate(batch_responses):
        print(f"   Request {i+1}: {resp.text}")
    
    # 5. JSON mode example
    print("\n5. Testing JSON mode...")
    json_request = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Generate a JSON object with a 'status' field"}]
            }
        ],
        "instructions": {
            "json_schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"}
                }
            }
        },
        "routing": {"call_id": "local:echo.chat"}
    }
    
    json_response = thinker.chat_ql(json_request)
    print(f"✅ JSON response: {json_response.text}")
    
    print("\n🎉 Demo completed successfully!")
    print("\nPackage is ready for import and use!")

if __name__ == "__main__":
    main()
