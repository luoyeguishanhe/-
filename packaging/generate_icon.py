"""Render the vector brand mark to a Windows ICO with multiple native sizes."""

from pathlib import Path
import struct

from PyQt6.QtCore import QBuffer, QIODevice, QRectF
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer


def generate_icon(source: Path, destination: Path) -> None:
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG: {source}")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = []
    for size in sizes:
        canvas = QImage(size, size, QImage.Format.Format_ARGB32)
        canvas.fill(0)
        painter = QPainter(canvas)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not canvas.save(buffer, "PNG"):
            raise RuntimeError("Cannot render brand icon")
        images.append(bytes(buffer.data()))
    offset = 6 + 16 * len(sizes)
    entries = []
    for size, data in zip(sizes, images):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256,
                                   0, 0, 1, 32, len(data), offset))
        offset += len(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(struct.pack("<HHH", 0, 1, len(sizes))
                            + b"".join(entries) + b"".join(images))


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    generate_icon(root / "app-icon.svg", root / "app-icon.ico")
