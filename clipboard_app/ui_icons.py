"""Small monochrome drawings for the desktop controls, without font glyphs."""

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def draw_symbol(painter, name, rect, color):
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 20, rect.height() / 20)
    painter.setPen(QPen(QColor(color), 1.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    if name == 'search':
        painter.drawEllipse(QRectF(3, 3, 10, 10))
        painter.drawLine(QPointF(12, 12), QPointF(17, 17))
    elif name == 'preview':
        painter.drawRoundedRect(QRectF(2.5, 3.5, 15, 13), 2, 2)
        painter.drawLine(QPointF(10, 4), QPointF(10, 16))
    elif name == 'settings':
        for y, x in ((5, 7), (10, 13), (15, 8)):
            painter.drawLine(QPointF(3, y), QPointF(x - 2, y))
            painter.drawLine(QPointF(x + 2, y), QPointF(17, y))
            painter.drawEllipse(QPointF(x, y), 2, 2)
    elif name == 'more':
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        for x in (4, 10, 16):
            painter.drawEllipse(QPointF(x, 10), 1.3, 1.3)
    elif name == 'copy':
        painter.drawRoundedRect(QRectF(6, 6, 11, 11), 2, 2)
        painter.drawLine(QPointF(3, 13), QPointF(3, 4))
        painter.drawLine(QPointF(3, 4), QPointF(12, 4))
    else:
        painter.drawRoundedRect(QRectF(4, 2.5, 12, 15), 2, 2)
        for y, end in ((7, 13), (10, 13), (13, 10)):
            painter.drawLine(QPointF(7, y), QPointF(end, y))
    painter.restore()


def symbol_icon(name, color):
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(2)
    painter = QPainter(pixmap)
    draw_symbol(painter, name, QRectF(0, 0, 20, 20), color)
    painter.end()
    return QIcon(pixmap)
