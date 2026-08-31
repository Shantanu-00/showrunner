"""Builds every architecture artifact from its checked-in HTML source.

    python scripts/render_architecture.py

Outputs, all under `docs/assets/`:

  architecture.pdf              7 pages — p1 the one-glance overview, p2-7 the LLD.
                                This is the file that goes in the Devpost
                                "architecture diagram" upload field (it accepts
                                pdf/ppt/pptx/png/jpg up to 35 MB).
  architecture-overview.png     page 1 at 200 dpi, for the README and the gallery.
  architecture-diagram.jpg      the same page 1 as JPG, because README.md and the
                                blog draft already link that filename.

Sources: `docs/assets/architecture-overview.html` (page 1, a data-driven diagram —
boxes and edges are declared as arrays and share one coordinate space, so a
connector can never point at a stale box) and `docs/assets/architecture-lld.html`
(pages 2-7), both over `docs/assets/arch.css`.

**Page 1 of the PDF and the PNG are rasterized from the same PDF page**, so the
one-glance artwork a judge sees in the README is byte-for-byte the artwork on
page 1 of the upload. That is deliberate: `docs/context/architecture-review-2026-08-28.md`
§7 requires page 1 to be complete on its own and identical to the README image.

Toolchain, both already on this machine and neither a new project dependency:
  * headless Chrome for layout and print (`--print-to-pdf` honours the CSS
    `@page` size exactly — each page renders at 1200x750 pt with no shrink);
  * Ghostscript to concatenate the two PDFs and to rasterize page 1.

No network access is needed and no third-party font, icon or logo is fetched, by
design — hackathon rules §6 bars third-party trademarks and endorsement
implications in submitted content.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"

OVERVIEW_HTML = ASSETS / "architecture-overview.html"
LLD_HTML = ASSETS / "architecture-lld.html"

OUT_PDF = ASSETS / "architecture.pdf"
OUT_PNG = ASSETS / "architecture-overview.png"
OUT_JPG = ASSETS / "architecture-diagram.jpg"

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
)
GS_CANDIDATES = ("gswin64c", "gswin32c", "gs")


def _find(candidates: tuple[str, ...], what: str) -> str:
    for c in candidates:
        if Path(c).exists():
            return c
        found = shutil.which(c)
        if found:
            return found
    raise SystemExit(
        f"could not find {what}. Tried: {', '.join(candidates)}\n"
        f"Install it, or add it to PATH, and re-run."
    )


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)


def _file_url(p: Path) -> str:
    return "file:///" + str(p.resolve()).replace("\\", "/")


def main() -> None:
    for src in (OVERVIEW_HTML, LLD_HTML, ASSETS / "arch.css"):
        if not src.exists():
            raise SystemExit(f"missing source: {src}")

    chrome = _find(CHROME_CANDIDATES, "headless Chrome or Edge")
    gs = _find(GS_CANDIDATES, "Ghostscript")

    tmp_overview = ASSETS / "_overview.tmp.pdf"
    tmp_lld = ASSETS / "_lld.tmp.pdf"

    try:
        # 1. Layout and print each source. A throwaway profile keeps a stale disk
        #    cache from serving an older arch.css.
        for src, out in ((OVERVIEW_HTML, tmp_overview), (LLD_HTML, tmp_lld)):
            _run([
                chrome,
                "--headless",
                "--disable-gpu",
                f"--user-data-dir={ASSETS / '_chrome.tmp'}",
                "--no-pdf-header-footer",
                f"--print-to-pdf={out.resolve()}",
                _file_url(src),
            ])
            print(f"  printed {src.name}")

        # 2. Concatenate: page 1 is the overview, pages 2-7 are the LLD.
        _run([
            gs, "-dNOPAUSE", "-dBATCH", "-dQUIET",
            "-sDEVICE=pdfwrite",
            "-dPDFSETTINGS=/prepress",
            "-dCompatibilityLevel=1.7",
            f"-sOutputFile={OUT_PDF.resolve()}",
            str(tmp_overview.resolve()), str(tmp_lld.resolve()),
        ])
        print(f"  wrote {OUT_PDF.relative_to(ROOT)}")

        # 3. Rasterize page 1 of the *merged* PDF, so the README image and the
        #    upload's first page cannot drift apart.
        for device, dpi, out, extra in (
            ("png16m", "200", OUT_PNG, []),
            ("jpeg", "150", OUT_JPG, ["-dJPEGQ=94"]),
        ):
            _run([
                gs, "-dNOPAUSE", "-dBATCH", "-dQUIET",
                f"-sDEVICE={device}", f"-r{dpi}",
                "-dFirstPage=1", "-dLastPage=1",
                "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
                *extra,
                f"-sOutputFile={out.resolve()}",
                str(OUT_PDF.resolve()),
            ])
            print(f"  wrote {out.relative_to(ROOT)}")

    finally:
        tmp_overview.unlink(missing_ok=True)
        tmp_lld.unlink(missing_ok=True)
        shutil.rmtree(ASSETS / "_chrome.tmp", ignore_errors=True)

    print("\nDone. Devpost architecture upload: docs/assets/architecture.pdf")


if __name__ == "__main__":
    sys.exit(main())
