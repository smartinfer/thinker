"""
Command-line interface for Thinker Core.

This module provides CLI commands for registry operations and ThinkerQL processing.

Author: Anjan Goswami
"""

import argparse, json, yaml, glob
from pathlib import Path
from thinker.registry.loader import load_catalog
from thinker.registry.store import RegistryStore
from thinker.registry.schema import Catalog
from thinker.core import Thinker
from thinker.primitives import map_chat

def main():
    """Main CLI entry point."""
    p = argparse.ArgumentParser("thinker")
    sub = p.add_subparsers(dest="cmd", required=True)

    # Registry commands
    c1 = sub.add_parser("registry-validate"); c1.add_argument("registry_yaml")
    c2 = sub.add_parser("registry-load");     c2.add_argument("registry_yaml")
    c3 = sub.add_parser("registry-list")
    c4 = sub.add_parser("registry-get");      c4.add_argument("call_id")

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