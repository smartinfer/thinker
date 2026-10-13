"""Provider API provenance manifest: structure is enforced, staleness only warns."""

import datetime
import warnings
from pathlib import Path

import yaml

from thinker.thinkerql.wire import protocol_for
from thinker.tests.test_modelcalls import route

MANIFEST = Path(__file__).parents[3] / "spec" / "thinkerql-provider-apis.yaml"
PROTOCOLS = {"chat", "responses", "anthropic", "gemini"}


def load():
    return yaml.safe_load(MANIFEST.read_text())


def as_date(value):
    return value if isinstance(value, datetime.date) or value is None else datetime.date.fromisoformat(value)


def test_manifest_covers_every_wire_protocol():
    manifest = load()
    assert manifest["format"] == "thinkerql.provider-apis.v1"
    assert set(manifest["protocols"]) == PROTOCOLS
    for name, entry in manifest["protocols"].items():
        assert protocol_for(route(name)) == name
        assert route(name).payload_style == entry["payload_style"]
        assert entry["docs"] and all(d["url"].startswith("https://") for d in entry["docs"])
        assert entry["facts"]
        assert "date" in entry["live_verified"] and entry["live_verified"]["evidence"]


def test_manifest_staleness_warns():
    manifest = load()
    limit = datetime.timedelta(days=manifest["stale_after_days"])
    today = datetime.date.today()
    stale = []
    for name, entry in manifest["protocols"].items():
        for doc in entry["docs"]:
            checked = as_date(doc["checked_at"])
            if checked is None or today - checked > limit:
                stale.append(f"{name}: docs {doc['url']} checked {checked}")
        verified = as_date(entry["live_verified"]["date"])
        if verified is None or today - verified > limit:
            stale.append(f"{name}: live verified {verified}")
    if stale:
        warnings.warn("Provider API provenance is stale or missing:\n  " + "\n  ".join(stale))
