"""Generate the original AI News Desktop app icon using only Python's stdlib."""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "desktop" / "src-tauri" / "icons"
OUT.mkdir(parents=True, exist_ok=True)
SIZE = 128


def chunk(name: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)


rows = []
for y in range(SIZE):
    row = bytearray(b"\x00")
    for x in range(SIZE):
        dx, dy = min(x, SIZE - 1 - x), min(y, SIZE - 1 - y)
        inside = dx >= 22 or dy >= 22 or (dx - 22) ** 2 + (dy - 22) ** 2 <= 22 ** 2
        color = (20, 34, 48, 255) if inside else (0, 0, 0, 0)
        if inside and abs(x - 64) + abs(y - 60) < 29:
            color = (105, 171, 232, 255)
        if inside and abs(x - 64) + abs(y - 60) < 17:
            color = (218, 239, 255, 255)
        if inside and abs(x - 92) + abs(y - 91) < 7:
            color = (96, 207, 169, 255)
        row.extend(color)
    rows.append(bytes(row))
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")
(OUT / "icon.png").write_bytes(png)
ico_header = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", SIZE, SIZE, 0, 0, 1, 32, len(png), 22)
(OUT / "icon.ico").write_bytes(ico_header + png)
