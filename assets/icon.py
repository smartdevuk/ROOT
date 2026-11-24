"""Render ROOT's folder and branching path launcher mark at native resolution."""

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from pathlib import Path

im = QImage(256, 256, QImage.Format_ARGB32_Premultiplied)
im.fill(Qt.transparent)
p = QPainter(im)
p.setRenderHint(QPainter.Antialiasing)
gradient = QLinearGradient(40, 20, 225, 240)
gradient.setColorAt(0, QColor("#3d2c52"))
gradient.setColorAt(0.65, QColor("#211a2d"))
gradient.setColorAt(1, QColor("#15121d"))
p.setBrush(gradient)
p.setPen(QPen(QColor("#6d567f"), 3))
p.drawRoundedRect(QRectF(8, 8, 240, 240), 53, 53)
outline = QPainterPath()
outline.moveTo(48, 86)
outline.quadTo(48, 77, 57, 77)
outline.lineTo(103, 77)
outline.lineTo(122, 95)
outline.lineTo(198, 95)
outline.quadTo(208, 95, 208, 105)
outline.lineTo(208, 177)
outline.quadTo(208, 187, 198, 187)
outline.lineTo(58, 187)
outline.quadTo(48, 187, 48, 177)
outline.closeSubpath()
p.setBrush(QColor("#3a2b4d"))
p.setPen(QPen(QColor("#c6a7ff"), 8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
p.drawPath(outline)
p.setPen(QPen(QColor("#94e7cf"), 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
for x, y, x2, y2 in [(83, 146, 122, 146), (122, 146, 153, 121), (122, 146, 154, 164)]:
    p.drawLine(QPointF(x, y), QPointF(x2, y2))
p.setBrush(QColor("#b4f5de"))
p.setPen(Qt.NoPen)
for x, y in [(83, 146), (153, 121), (154, 164)]:
    p.drawEllipse(QPointF(x, y), 5, 5)
p.end()
im.save(str(Path(__file__).resolve().parents[1] / "root_explorer" / "root.png"))
