"""
Command-line interface for Thinker Core.

This module provides CLI commands for registry operations and ThinkerQL processing.

Author: Anjan Goswami
"""

import argparse
import glob
import json
import time
from pathlib import Path

import yaml

from thinker.config import get_config
from thinker.core import Thinker
from thinker.local_models import (
    add_local_model,
    load_local_models,
    local_doctor,
    model_cache_status,
    pull_model,
)
from thinker.observability.dashboard import Dashboard
from thinker.primitives import map_chat
from thinker.registry.loader import load_catalog
from thinker.registry.secure_credentials import SecureCredentials, prompt_for_key
from thinker.registry.store import RegistryStore


def main():
    """Main CLI entry point."""
    p = argparse.ArgumentParser("thinker")
    sub = p.add_subparsers(dest="cmd", required=True)

    # Registry commands
    c1 = sub.add_parser("registry-validate")
    c1.add_argument("registry_yaml")
    c2 = sub.add_parser("registry-load")
    c2.add_argument("registry_yaml")
    sub.add_parser("registry-list")
    c4 = sub.add_parser("registry-get")
    c4.add_argument("call_id")

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
    c5.add_argument("--ql", required=True, help="ThinkerQL YAML file")

    c6 = sub.add_parser("map-chat-ql")
    c6.add_argument("--registry", required=True, help="Registry YAML file")
    c6.add_argument("--ql-glob", required=True, help="Glob pattern for ThinkerQL files")
    c6.add_argument("--budget-usd", type=float, help="Budget limit in USD")

    local_parser = sub.add_parser("local", help="Manage direct local model aliases and cache")
    local_sub = local_parser.add_subparsers(dest="local_cmd", required=True)
    local_doctor_parser = local_sub.add_parser("doctor", help="Check Apple-Silicon MLX support")
    local_doctor_parser.add_argument("--config", type=Path)
    local_pull_parser = local_sub.add_parser("pull", help="Download a public MLX model")
    local_pull_parser.add_argument("model")
    local_add_parser = local_sub.add_parser("add", help="Register a portable local alias")
    local_add_parser.add_argument("name")
    local_add_parser.add_argument("--backend", choices=["mlx"], required=True)
    local_add_parser.add_argument("--model", required=True)
    local_add_parser.add_argument("--tools", action="store_true")
    local_add_parser.add_argument("--config", type=Path)
    local_list_parser = local_sub.add_parser("list", help="List registered local aliases")
    local_list_parser.add_argument("--config", type=Path)
    local_inspect_parser = local_sub.add_parser("inspect", help="Inspect one local alias")
    local_inspect_parser.add_argument("name")
    local_inspect_parser.add_argument("--config", type=Path)

    model_turn_parser = sub.add_parser("model-turn", help="Run Model-Turn V1 utilities")
    model_turn_sub = model_turn_parser.add_subparsers(dest="model_turn_cmd", required=True)
    model_turn_smoke = model_turn_sub.add_parser("smoke", help="Run one bounded in-process turn")
    model_turn_smoke.add_argument("route")
    model_turn_smoke.add_argument("--registry", required=True)
    model_turn_smoke.add_argument("--prompt", default="Reply with the word ready.")
    model_turn_smoke.add_argument("--max-output-tokens", type=int, default=32)
    model_turn_smoke.add_argument("--timeout-ms", type=int, default=120_000)
    model_turn_smoke.add_argument("--local-models", type=Path)

    args = p.parse_args()
    store = RegistryStore()

    if args.cmd == "local":
        if args.local_cmd == "doctor":
            print(json.dumps(local_doctor(args.config), indent=2, sort_keys=True))
            return
        if args.local_cmd == "pull":
            cached = pull_model(args.model)
            print(json.dumps({"model": args.model, "available": True, "cache": str(cached)}))
            return
        if args.local_cmd == "add":
            entry = add_local_model(
                args.name,
                args.backend,
                args.model,
                tools=args.tools,
                path=args.config,
            )
            print(json.dumps({"route": entry.route, "model": entry.model_id, "tools": entry.tools}))
            return
        entries = load_local_models(args.config)
        if args.local_cmd == "list":
            print(
                json.dumps(
                    [
                        {
                            "route": entry.route,
                            "backend": entry.backend,
                            "model": entry.model_id,
                            "tools": entry.tools,
                            **model_cache_status(entry.model_id),
                        }
                        for entry in entries
                    ],
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        entry = next(
            (item for item in entries if args.name in {item.name, item.route, item.model_id}),
            None,
        )
        if entry is None:
            raise SystemExit(f"unknown local model alias: {args.name}")
        print(
            json.dumps(
                {
                    "route": entry.route,
                    "backend": entry.backend,
                    "model": entry.model_id,
                    "tools": entry.tools,
                    "capabilities": entry.registry_call().caps,
                    **model_cache_status(entry.model_id),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if args.cmd == "model-turn":
        from uuid import uuid4

        from thinker.local_models import apply_local_models
        from thinker.model_turn.models import GenerationParameters, ModelMessage, ModelTurnRequest
        from thinker.model_turn.runtime import ModelTurnRuntime

        catalog, checksum = load_catalog(args.registry)
        store.apply_catalog(catalog, checksum, source=args.registry)
        apply_local_models(store, args.local_models)
        response = ModelTurnRuntime(store).turn(
            ModelTurnRequest(
                request_id=f"local-smoke-{uuid4().hex}",
                requested_route=args.route,
                messages=(ModelMessage(role="user", content=args.prompt),),
                generation=GenerationParameters(max_output_tokens=args.max_output_tokens),
                timeout_ms=args.timeout_ms,
            )
        )
        print(response.model_dump_json(indent=2))
        raise SystemExit(1 if response.normalized_error else 0)

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
        config = get_config()
        creds = SecureCredentials(
            storage_type=config.credentials_storage_type,
            keystore_path=config.keystore_path,
        )

        if args.keys_cmd == "set":
            provider = (
                input("Provider (openai, anthropic, google, together, mistral): ").strip().lower()
            )
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

                # Validate the candidate without replacing the stored key.
                success, message = creds.test_key(provider, new_key)
                if success:
                    if creds.set(provider, new_key):
                        print(f"✅ Key rotated successfully for {provider}")
                    else:
                        print(f"❌ Failed to store new key for {provider}")
                else:
                    print(f"❌ New key test failed: {message}")
                    print("Original key was not changed")
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

        dashboard.show_stats(provider=args.provider, since=since, live=args.live)
        return

    if args.cmd == "chat-ql":
        # Load ThinkerQL file
        with open(args.ql) as f:
            ql_data = yaml.safe_load(f)

        # Create Thinker instance
        thinker = Thinker.from_files(args.registry)

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
        thinker = Thinker.from_files(args.registry)

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
