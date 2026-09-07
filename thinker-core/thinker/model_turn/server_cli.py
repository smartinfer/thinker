"""CLI entrypoint for the local model-turn service."""

from __future__ import annotations

import argparse
import signal
import threading
from pathlib import Path

from thinker.local_models import apply_local_models
from thinker.pricebook import PriceBook
from thinker.registry.loader import load_catalog
from thinker.registry.store import RegistryStore

from .runtime import ModelTurnRuntime
from .service import ModelTurnHTTPServer


def main() -> None:
    parser = argparse.ArgumentParser("thinker-model-turn-server")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--revision")
    parser.add_argument(
        "--local-models",
        type=Path,
        help="optional local-model alias JSON (defaults to the standard user config when present)",
    )
    parser.add_argument("--no-local-models", action="store_true")
    args = parser.parse_args()

    catalog, checksum = load_catalog(args.registry)
    store = RegistryStore()
    store.apply_catalog(catalog, checksum, args.registry)
    if not args.no_local_models:
        apply_local_models(store, args.local_models)
    runtime = ModelTurnRuntime(store, PriceBook(), thinker_revision=args.revision)
    server = ModelTurnHTTPServer((args.host, args.port), runtime)
    stopping = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
