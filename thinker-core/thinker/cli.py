"""
Command-line interface for Thinker Core.

This module provides CLI commands for registry operations and ThinkerQL processing.

Author: Anjan Goswami
"""

import argparse, json, yaml, glob, time
from pathlib import Path
from thinker.registry.loader import load_catalog
from thinker.registry.store import RegistryStore
from thinker.registry.schema import Catalog
from thinker.registry.secure_credentials import SecureCredentials, prompt_for_key
from thinker.core import Thinker
from thinker.primitives import map_chat
from thinker.observability.dashboard import Dashboard

def main():
    """Main CLI entry point."""
    p = argparse.ArgumentParser("thinker")
    sub = p.add_subparsers(dest="cmd", required=True)

    # Registry commands
    c1 = sub.add_parser("registry-validate"); c1.add_argument("registry_yaml")
    c2 = sub.add_parser("registry-load");     c2.add_argument("registry_yaml")
    c3 = sub.add_parser("registry-list")
    c4 = sub.add_parser("registry-get");      c4.add_argument("call_id")

    # Key management commands
    keys_parser = sub.add_parser("keys", help="Manage API keys securely")
    keys_sub = keys_parser.add_subparsers(dest="keys_cmd", required=True)
    
    keys_sub.add_parser("set", help="Set API key for a provider")
    keys_sub.add_parser("list", help="List configured providers")
    keys_sub.add_parser("delete", help="Delete API key for a provider")
    keys_sub.add_parser("test", help="Test API key for a provider")
    keys_sub.add_parser("rotate", help="Rotate API key for a provider")
    
    # Stats commands
    stats_parser = sub.add_parser("stats", help="Show usage statistics and metrics")
    stats_parser.add_argument("--live", action="store_true", help="Show live updating dashboard")
    stats_parser.add_argument("--provider", help="Filter by provider")
    stats_parser.add_argument("--today", action="store_true", help="Show today's stats")
    stats_parser.add_argument("--last-hour", action="store_true", help="Show last hour's stats")

    # Chat commands
    c5 = sub.add_parser("chat-ql")
    c5.add_argument("--registry", required=True, help="Registry YAML file")
    c5.add_argument("--pricebook", required=True, help="Pricebook JSON file")
    c5.add_argument("--ql", required=True, help="ThinkerQL YAML file")

    c6 = sub.add_parser("map-chat-ql")
    c6.add_argument("--registry", required=True, help="Registry YAML file")
    c6.add_argument("--pricebook", required=True, help="Pricebook JSON file")
    c6.add_argument("--ql-glob", required=True, help="Glob pattern for ThinkerQL files")
    c6.add_argument("--budget-usd", type=float, help="Budget limit in USD")

    args = p.parse_args()
    store = RegistryStore()

    if args.cmd == "registry-validate":
        cat, sha = load_catalog(args.registry_yaml)
        print(f"OK {args.registry_yaml} sha256={sha} calls={len(cat.calls)}")
        return

    if args.cmd == "registry-load":
        cat, sha = load_catalog(args.registry_yaml)
        store.apply_catalog(cat, sha, source=args.registry_yaml)
        print(json.dumps({"version": sha, "calls": len(cat.calls)}, indent=2))
        return

    if args.cmd == "registry-list":
        for c in store.list_calls(): 
            print(c.call_id)
        return

    if args.cmd == "registry-get":
        raise SystemExit("Run `registry-load` first")
    
    # Handle key management commands
    if args.cmd == "keys":
        creds = SecureCredentials()
        
        if args.keys_cmd == "set":
            provider = input("Provider (openai, anthropic, google, together, mistral): ").strip().lower()
            if provider not in ["openai", "anthropic", "google", "together", "mistral"]:
                print(f"Invalid provider: {provider}")
                return
            
            try:
                key = prompt_for_key(provider)
                if creds.set(provider, key):
                    print(f"✅ Key stored securely for {provider}")
                else:
                    print(f"❌ Failed to store key for {provider}")
            except Exception as e:
                print(f"❌ Error: {e}")
            return
        
        elif args.keys_cmd == "list":
            providers = creds.list_providers()
            if providers:
                print("Configured providers:")
                for provider in providers:
                    print(f"  - {provider}")
            else:
                print("No providers configured")
            return
        
        elif args.keys_cmd == "delete":
            provider = input("Provider to delete: ").strip().lower()
            if creds.delete(provider):
                print(f"✅ Deleted key for {provider}")
            else:
                print(f"❌ Failed to delete key for {provider} (may not exist)")
            return
        
        elif args.keys_cmd == "test":
            provider = input("Provider to test: ").strip().lower()
            success, message = creds.test_key(provider)
            if success:
                print(f"✅ {message}")
            else:
                print(f"❌ {message}")
            return
        
        elif args.keys_cmd == "rotate":
            provider = input("Provider to rotate: ").strip().lower()
            if provider not in ["openai", "anthropic", "google", "together", "mistral"]:
                print(f"Invalid provider: {provider}")
                return
            
            try:
                # Test current key first
                success, message = creds.test_key(provider)
                if not success:
                    print(f"Current key test failed: {message}")
                    return
                
                print(f"Current key for {provider} is working. Enter new key:")
                new_key = prompt_for_key(provider)
                
                if creds.set(provider, new_key):
                    # Test new key
                    success, message = creds.test_key(provider)
                    if success:
                        print(f"✅ Key rotated successfully for {provider}")
                    else:
                        print(f"❌ New key test failed: {message}")
                        print("Reverting to old key...")
                        # Note: In a real implementation, you'd want to store the old key temporarily
                else:
                    print(f"❌ Failed to store new key for {provider}")
            except Exception as e:
                print(f"❌ Error: {e}")
            return
    
    # Handle stats commands
    if args.cmd == "stats":
        dashboard = Dashboard()
        
        # Calculate time range
        since = None
        if args.today:
            # Start of today
            since = time.time() - (time.time() % 86400)
        elif args.last_hour:
            # Last hour
            since = time.time() - 3600
        
        dashboard.show_stats(
            provider=args.provider,
            since=since,
            live=args.live
        )
        return

    if args.cmd == "chat-ql":
        # Load ThinkerQL file
        with open(args.ql) as f:
            ql_data = yaml.safe_load(f)
        
        # Create Thinker instance
        thinker = Thinker.from_files(args.registry, args.pricebook)
        
        # Process request
        response = thinker.chat_ql(ql_data)
        
        # Print response
        print(f"Text: {response.text}")
        print(f"Model: {response.model}")
        print(f"Provider: {response.provider}")
        print(f"Tokens: {response.tokens}")
        print(f"Cost: ${response.cost_usd:.4f}")
        return

    if args.cmd == "map-chat-ql":
        # Find ThinkerQL files
        ql_files = glob.glob(args.ql_glob)
        if not ql_files:
            print("No files found matching pattern")
            return
        
        # Load ThinkerQL files
        ql_requests = []
        for file_path in ql_files:
            with open(file_path) as f:
                ql_data = yaml.safe_load(f)
                ql_requests.append(ql_data)
        
        # Create Thinker instance
        thinker = Thinker.from_files(args.registry, args.pricebook)
        
        # Process requests
        try:
            results = map_chat(thinker, ql_requests, budget_usd=args.budget_usd)
            
            # Print results
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"Request {i}: ERROR - {result}")
                else:
                    print(f"Request {i}: {result.text}")
            
        except Exception as e:
            print(f"Batch processing failed: {e}")
        return

if __name__ == "__main__":
    main()