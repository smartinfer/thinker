"""CLI entrypoint for the local model-turn service."""

from __future__ import annotations

import argparse
import signal
import threading

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
    args = parser.parse_args()

    catalog, checksum = load_catalog(args.registry)
    store = RegistryStore()
    store.apply_catalog(catalog, checksum, args.registry)
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
