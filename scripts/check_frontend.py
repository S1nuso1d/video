"""Extract inline browser scripts and validate their JavaScript syntax with Node.js."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HTML_FILE = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"
SCRIPT_PATTERN = re.compile(
    r"<script\b[^>]*>(.*?)</script\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)


def main() -> int:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is required to validate the inline browser scripts.")

    html = HTML_FILE.read_text(encoding="utf-8")
    scripts = SCRIPT_PATTERN.findall(html)
    if not scripts:
        raise SystemExit(f"No inline JavaScript scripts found in {HTML_FILE}")

    source = "\n;\n".join(scripts)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".js",
            encoding="utf-8",
            newline="\n",
            delete=False,
        ) as temp_file:
            temp_file.write(source)
            temp_path = Path(temp_file.name)

        subprocess.run([node, "--check", str(temp_path)], check=True)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    print(f"Validated {len(scripts)} inline JavaScript block(s) in {HTML_FILE.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
