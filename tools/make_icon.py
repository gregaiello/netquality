r"""Regenerate assets/netquality.ico and assets/netquality.png from widgets.paint_logo.

    .venv\Scripts\python tools\make_icon.py
"""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402

from netquality.widgets import logo_image  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(img) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 (needed for QFont/QPainter)
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    pngs = [png_bytes(logo_image(s)) for s in SIZES]
    # ICO container with PNG-compressed entries (supported since Windows Vista)
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    entries, data = b"", b""
    for s, png in zip(SIZES, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    (out / "netquality.ico").write_bytes(header + entries + data)
    logo_image(512).save(str(out / "netquality.png"))
    print("wrote", out / "netquality.ico", "and", out / "netquality.png")


if __name__ == "__main__":
    main()
