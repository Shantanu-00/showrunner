"""Re-exports docs/assets/architecture-diagram.jpg from its HTML source.

The diagram is a static infographic with no layered source file (it started life as a one-off
export). This script makes it a reproducible artifact instead: edit
docs/assets/architecture-diagram.html, re-run this, get the JPG back. No network access needed —
the page has no external font/icon/logo dependencies by design (rules §6: no third-party trademarks).

Shells out to the Node Playwright CLI (this repo's frontend already depends on Playwright for its
own screenshot-based smoke tests; there's no Python Playwright install here, and no need to add one
for a one-off diagram render).

Usage: python scripts/render_architecture_diagram.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_HTML = ROOT / "docs" / "assets" / "architecture-diagram.html"
OUTPUT_JPG = ROOT / "docs" / "assets" / "architecture-diagram.jpg"


def main() -> None:
    if not SOURCE_HTML.exists():
        raise SystemExit(f"missing source: {SOURCE_HTML}")

    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    subprocess.run(
        [
            npx,
            "--no-install",
            "playwright",
            "screenshot",
            "--viewport-size=1400,900",
            "--full-page",
            str(SOURCE_HTML),
            str(OUTPUT_JPG),
        ],
        cwd=ROOT,
        check=True,
    )

    print(f"wrote {OUTPUT_JPG}")


if __name__ == "__main__":
    main()
