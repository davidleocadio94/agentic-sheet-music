"""One-time fetch of vendored JS + SoundFont for the playalong site.

Run: `uv run python -m agentic_sheet_music.playalong.fetch_vendor`

Downloads to ~/.cache/agentic-sheet-music/vendor/ so it's shared across
repeated builds. No automatic fetch from `build_playalong` — the network call
must be an explicit user action. Sources are documented here; the user can
override by placing files under the cache manually.
"""

from __future__ import annotations

import logging
import sys
import urllib.request
from pathlib import Path

from agentic_sheet_music.playalong.build import DEFAULT_VENDOR_CACHE

logger = logging.getLogger(__name__)


# SpessaSynth: Apache-2.0. Version pin chosen for current (2026-04) stability.
# We fetch the self-contained UMD-ish browser bundle so no import map is needed.
# The jsDelivr URLs resolve to immutable content per version; swap `@4.2.15`
# in both URLs to upgrade.
_DOWNLOADS: tuple[tuple[str, str], ...] = (
    (
        "spessasynth_lib.js",
        "https://cdn.jsdelivr.net/npm/spessasynth_lib@4.2.15/dist/index.js",
    ),
    (
        # spessasynth_lib imports from "spessasynth_core" as a bare specifier,
        # which only resolves via an import map in the browser. Ship the core
        # bundle alongside and map the name in index.html.
        "spessasynth_core.js",
        "https://cdn.jsdelivr.net/npm/spessasynth_core@4.2.12/dist/index.js",
    ),
    (
        "spessasynth_processor.js",
        "https://cdn.jsdelivr.net/npm/spessasynth_lib@4.2.15/dist/spessasynth_processor.min.js",
    ),
    # GeneralUser GS — GM-compatible SoundFont, MIT-licensed, lives in the
    # SpessaSynth repo. ~8 MB; larger than ideal but the full bank sounds good
    # and works offline. User can override by placing their own .sf2/.sf3 at
    # this path in the cache.
    (
        "piano.sf3",
        "https://raw.githubusercontent.com/spessasus/SpessaSynth/master/soundfonts/GeneralUserGS.sf3",
    ),
)


def fetch(cache: Path | None = None) -> Path:
    dest = cache or DEFAULT_VENDOR_CACHE
    dest.mkdir(parents=True, exist_ok=True)
    for name, url in _DOWNLOADS:
        target = dest / name
        if target.exists() and target.stat().st_size > 0:
            print(f"[skip] {name} (already present, {target.stat().st_size} bytes)")
            continue
        print(f"[fetch] {name} from {url}")
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
                target.write_bytes(resp.read())
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED: {e}", file=sys.stderr)
            print(
                f"  Place a replacement at {target} manually to proceed.",
                file=sys.stderr,
            )
            continue
        print(f"  wrote {target} ({target.stat().st_size} bytes)")
    return dest


if __name__ == "__main__":
    fetch()
