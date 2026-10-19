"""Builds the Windows download: dist/MedPort-windows.zip

    py -m pip install pyinstaller
    py build_windows.py
"""
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD, DIST = ROOT / "build", ROOT / "dist"

QUICKSTART = """MedPort - your private medical portfolio
=========================================

1. Double-click MedPort.exe. Your browser opens MedPort. Keep the black window open while you use it;
   close it to stop MedPort.
2. Optional, free, recommended: install Ollama from https://ollama.com/download, then open a terminal and run
       ollama pull qwen2.5:3b
   so a local open model reads your reports. Without it MedPort uses its built-in rule parser.
3. Your reports and data stay on this computer, in the .medport folder in your user folder.

Phone app (show or send your portfolio at the clinic, offline): start MedPort from a terminal with
    MedPort.exe --lan
and open Share & backup -> Phone app.

If Windows SmartScreen says "Windows protected your PC", click More info -> Run anyway
(the app is not code-signed).
"""


def icon():
    """A Windows icon from the app logo (an .ico can hold a PNG directly)."""
    sys.path.insert(0, str(ROOT))
    from medport.main import _icon_png
    png = _icon_png(256)  # width/height 0 in the header means 256
    BUILD.mkdir(exist_ok=True)
    header = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    (BUILD / "medport.ico").write_bytes(header + png)


def main():
    icon()
    subprocess.run([sys.executable, "-m", "PyInstaller", "medport.spec", "--noconfirm"], cwd=ROOT, check=True)
    app = DIST / "MedPort"
    (app / "README.txt").write_text(QUICKSTART, encoding="utf-8")
    out = DIST / "MedPort-windows.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in app.rglob("*"):
            z.write(f, Path("MedPort") / f.relative_to(app))
    print(f"Built {out} ({out.stat().st_size // 1_000_000} MB)")


if __name__ == "__main__":
    main()
