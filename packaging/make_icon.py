"""Disegna l'icona dell'applicazione (due pagine sovrapposte in lingue diverse) -> polyglot_pdf.ico"""
import os
import struct
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen

app = QGuiApplication(sys.argv[:1])
S = 256
img = QImage(S, S, QImage.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)


def page(x, y, w, h, fill, text, color):
    path = QPainterPath()
    fold = w * 0.28
    path.moveTo(x, y)
    path.lineTo(x + w - fold, y)
    path.lineTo(x + w, y + fold)
    path.lineTo(x + w, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    p.setPen(QPen(QColor(0, 0, 0, 70), 4))
    p.setBrush(QColor(fill))
    p.drawPath(path)
    corner = QPainterPath()
    corner.moveTo(x + w - fold, y)
    corner.lineTo(x + w - fold, y + fold)
    corner.lineTo(x + w, y + fold)
    corner.closeSubpath()
    p.setBrush(QColor(0, 0, 0, 40))
    p.drawPath(corner)
    p.setPen(QColor(color))
    f = QFont("Segoe UI", 1, QFont.Bold)
    f.setPixelSize(int(h * 0.36))
    p.setFont(f)
    p.drawText(QRectF(x, y + h * 0.22, w, h * 0.5), Qt.AlignCenter, text)
    p.setPen(QPen(QColor(color).lighter(150), 7, Qt.SolidLine, Qt.RoundCap))
    for k in range(2):                          # righe di testo stilizzate
        yy = y + h * (0.76 + 0.11 * k)
        p.drawLine(int(x + w * 0.18), int(yy), int(x + w * (0.82 - 0.2 * k)), int(yy))


page(20, 14, 150, 196, "#E8F1FB", "IT", "#1F5FA8")
page(86, 46, 150, 196, "#FFFFFF", "DE", "#C0392B")
p.end()


def png_bytes(image):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return bytes(ba)


# .ico con più dimensioni (voci PNG, supportate da Windows Vista in poi)
sizes = (16, 24, 32, 48, 64, 128, 256)
pngs = [png_bytes(img.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation)) for s in sizes]
header = struct.pack("<HHH", 0, 1, len(sizes))
offset = 6 + 16 * len(sizes)
entries = b""
for s, data in zip(sizes, pngs):
    entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
    offset += len(data)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polyglot_pdf.ico")
with open(out, "wb") as f:
    f.write(header + entries + b"".join(pngs))
print("icona:", out, "dimensioni:", sizes)
