"""Refresh Go's embedded packet schemas and error vocabulary from Python sources.

Development-only: building or running the Go library never invokes Python.
"""

import argparse
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "thinker-core/thinker/thinkerql"
TARGET = ROOT / "thinker-go/contract"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    errors = runpy.run_path(str(SOURCE / "errors.py"))
    resources = {
        name: (SOURCE / name).read_bytes()
        for name in ("request_v0_4.schema.json", "response_v0_4.schema.json")
    }
    resources["errors.json"] = (
        json.dumps(
            {
                "hermes": errors["HERMES_REASON_CODES"],
                "native": errors["_NATIVE_CODES"],
                "providers": errors["_PROVIDER_CODES"],
                "messages": errors["_MESSAGE_RULES"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    for name, data in resources.items():
        path = TARGET / name
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                raise SystemExit(
                    f"Outdated Go contract: {name}; run tools/sync_go_contract.py"
                )
        else:
            TARGET.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


if __name__ == "__main__":
    main()
