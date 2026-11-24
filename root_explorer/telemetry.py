"""Payload throughput sampled from the file transfer workers."""

from collections import deque
from math import ceil, log2
import time

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QScrollBar, QWidget

from files import fmt

WINDOW = 12.0


class Throughput:
    def __init__(self):
        self.reset(0.0)

    def reset(self, now):
        self.start = self.last = now
        self.total = self.rate = self.peak = 0
        self.samples = deque()
        self.active = {}
        self.running = True
        self.previous_total = 0
        self.interval = 0.5

    def sample(self, now, total, active, finished=False):
        dt = now - self.last
        self.active = active
        # User actions can tick the UI between timer events. Accumulate those bytes.
        if dt < 0.15 and not finished:
            return
        if dt > 0:
            self.previous_total = self.total
            self.interval = dt
            self.rate = max(0, total - self.total) / dt
            self.peak = max(self.peak, self.rate)
            self.samples.append((now, self.rate))
        while len(self.samples) > 1 and self.samples[1][0] < now - WINDOW:
            self.samples.popleft()
        self.total, self.last = total, max(self.last, now)
        self.running = not finished

    @property
    def average(self):
        return self.total / max(0.001, self.last - self.start)


def number(p, rect, value, size, colour, *, bold=False, align=Qt.AlignLeft):
    f = QFont("DejaVu Sans Mono")
    f.setPixelSize(size)
    f.setBold(bold)
    p.setFont(f)
    p.setPen(QColor(colour))
    p.drawText(rect, align | Qt.AlignVCenter, value)


class Monitor(QWidget):
    """A low-refresh oscilloscope. Geometry, ticks and cursor use actual sample times."""

    def __init__(self, series):
        super().__init__()
        self.series = series
        self.motion = True
        self.direction = "Copy"
        self.outcome = ""
        self.setFixedHeight(169)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.update)
        self.setAccessibleName("Transfer throughput")

    def display(self, direction, outcome, motion):
        self.direction, self.outcome, self.motion = direction, outcome, motion
        self.setVisible(True)
        self.setAccessibleDescription(
            f"{direction}, {outcome or 'running'}, {fmt(int(self.series.rate))} per second"
        )
        self.sync_timer()
        self.update()

    def sync_timer(self):
        animate = (
            self.isVisible()
            and self.motion
            and self.series.running
            and self.series.rate > 0
        )
        if animate and not self.timer.isActive():
            self.timer.start()
        elif not animate:
            self.timer.stop()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_timer()

    def paintEvent(self, event):
        s = self.series
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height() - 31
        outer = QRectF(0.7, 0.7, width - 1.4, self.height() - 1.4)
        bg = QLinearGradient(0, 0, width, height)
        bg.setColorAt(0, QColor("#241b33"))
        bg.setColorAt(0.52, QColor("#15101f"))
        bg.setColorAt(1, QColor("#13111c"))
        p.setBrush(bg)
        p.setPen(QPen(QColor("#544263"), 1))
        p.drawRoundedRect(outer, 10, 10)
        bright = "#acffe1" if self.direction == "Copy" else "#debbff"
        main = s.rate if s.running else s.average
        amount, unit = fmt(int(main)).split(" ")
        split = 205 if width > 540 else (157 if width > 350 else 110)
        top = 17
        number(
            p,
            QRectF(17, top, split - 22, 14),
            "→ " + self.direction.upper(),
            12,
            bright,
            bold=True,
        )
        number(
            p,
            QRectF(15, 36, split - 12, 48),
            amount,
            36 if len(amount) <= 5 else 30,
            "#f7f3ff",
            bold=True,
        )
        number(
            p,
            QRectF(18, 87, split - 22, 17),
            unit + "/s  " + ("LIVE" if s.running else "AVG"),
            12,
            "#d1bbeb",
        )
        caption = f"{len(s.active)} active" if s.running else self.outcome
        number(p, QRectF(18, height - 27, split - 18, 14), caption, 11, bright)
        p.setPen(QPen(QColor("#47354f"), 1))
        p.drawLine(QPointF(split, 14), QPointF(split, height - 14))
        box = QRectF(split + 18, 42, width - split - 36, height - 76)
        number(
            p,
            QRectF(box.left(), top, box.width(), 16),
            "THROUGHPUT",
            11,
            "#d1c2df",
            bold=True,
        )
        if box.width() > 270:
            number(
                p,
                QRectF(box.left(), top, box.width(), 16),
                f"PEAK  {fmt(int(s.peak))}/s",
                10,
                "#b5a5c6",
                align=Qt.AlignRight,
            )
        p.setPen(QPen(QColor("#382d45"), 1, Qt.DotLine))
        for n in range(4):
            y = box.top() + box.height() * n / 3
            p.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
        for n in range(7):
            x = box.left() + box.width() * n / 6
            p.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
        samples = list(s.samples)
        now = s.last
        if self.timer.isActive():
            now += max(0, min(0.55, time.monotonic() - s.last))
        upper = 2 ** ceil(log2(max([1024] + [v for _, v in samples])))
        if samples:
            points = [
                QPointF(
                    box.right() - (now - t) * box.width() / WINDOW,
                    box.bottom() - min(v / upper, 1) * box.height(),
                )
                for t, v in samples
            ]
            p.save()
            p.setClipRect(box.adjusted(-0.5, -4, 4, 4))
            trace = QPainterPath(points[0])
            prev = points[0]
            for nxt in points[1:]:
                # Connect adjacent samples without overshooting either measured value.
                mid = (prev.x() + nxt.x()) / 2
                trace.cubicTo(QPointF(mid, prev.y()), QPointF(mid, nxt.y()), nxt)
                prev = nxt
            fill = QPainterPath(trace)
            fill.lineTo(prev.x(), box.bottom())
            fill.lineTo(points[0].x(), box.bottom())
            fill.closeSubpath()
            gradient = QLinearGradient(0, box.top(), 0, box.bottom())
            tint = QColor(bright)
            tint.setAlpha(66)
            gradient.setColorAt(0, tint)
            tint.setAlpha(4)
            gradient.setColorAt(1, tint)
            p.fillPath(fill, gradient)
            p.setBrush(Qt.NoBrush)
            for line_width, alpha in ((7, 17), (4, 38), (1.9, 240)):
                tint = QColor(bright)
                tint.setAlpha(alpha)
                p.setPen(
                    QPen(tint, line_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                )
                p.drawPath(trace)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(bright))
            p.drawEllipse(prev, 3.1, 3.1)
            p.restore()
        number(
            p, QRectF(box.left(), height - 26, box.width(), 13), "−12 s", 10, "#aa99b9"
        )
        number(
            p,
            QRectF(box.left(), height - 26, box.width(), 13),
            f"{fmt(s.total)} transferred",
            10,
            "#c8b6d7",
            align=Qt.AlignRight,
        )
        self.draw_route(p, bright)
        p.end()

    def draw_route(self, p, colour):
        w, y = self.width(), self.height() - 18
        number(p, QRectF(18, y - 10, 110, 20), "SOURCE", 10, "#c8b9d4")
        number(
            p,
            QRectF(w - 103, y - 10, 85, 20),
            "DESTINATION",
            10,
            "#c8b9d4",
            align=Qt.AlignRight,
        )
        start, end = 103, w - 101
        p.setPen(QPen(QColor("#52405d"), 1))
        p.drawLine(QPointF(start, y), QPointF(end, y))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#9e7cbb"))
        for x in (start, end):
            p.drawEllipse(QPointF(x, y), 2.8, 2.8)
        s = self.series
        if not s.running or not s.rate:
            return
        travel = (
            min(1, max(0, (time.monotonic() - s.last) / max(0.001, s.interval)))
            if self.motion
            else 1
        )
        # The route advances one marker spacing for each 16 MiB of payload.
        amount = s.previous_total + (s.total - s.previous_total) * travel
        phase = (amount / (16 * 1024 * 1024)) % 1
        count = max(1, min(8, len(s.active)))
        reverse = False
        for i in range(count):
            r = (i + phase) / count
            x = start + (end - start) * (1 - r if reverse else r)
            tail = min(32, (end - start) / (count * 2))
            behind = min(end, x + tail) if reverse else max(start, x - tail)
            if abs(behind - x) > 1:
                grad = QLinearGradient(behind, y, x, y)
                clear = QColor(colour)
                clear.setAlpha(0)
                grad.setColorAt(0, clear)
                grad.setColorAt(1, QColor(colour))
                p.setPen(QPen(grad, 2.4))
                p.drawLine(QPointF(behind, y), QPointF(x, y))
            p.setPen(Qt.NoPen)
            tint = QColor(colour)
            tint.setAlpha(36)
            p.setBrush(tint)
            p.drawEllipse(QPointF(x, y), 5, 5)
            p.setBrush(QColor(colour))
            p.drawEllipse(QPointF(x, y), 2.1, 2.1)


class FileLanes(QWidget):
    """A fixed four-row viewport over recent file starts, oldest to newest."""

    def __init__(self):
        super().__init__()
        self.tracks = {}
        self.keys = []
        self.states = {}
        self.setFixedHeight(30 + 27 * 4)
        self.scroll = QScrollBar(Qt.Vertical, self)
        self.scroll.setAccessibleName("File activity history")
        self.scroll.setPageStep(4)
        self.scroll.valueChanged.connect(self.update)
        self.scroll.hide()
        self.sample_time = 0
        self.motion = True
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.animate)

    def set_files(self, active, now, motion):
        tracks = {}
        for key, (name, offset, size, state) in active.items():
            target = 1 if state == "done" else min(1, offset / size) if size else 0
            start = self.fraction(key, now) if key in self.tracks and motion else target
            tracks[key] = (name, start, target, size)
        follow = self.scroll.value() == self.scroll.maximum()
        anchor = self.keys[self.scroll.value()] if self.keys else None
        self.keys = list(tracks)
        self.states = {key: row[3] for key, row in active.items()}
        self.scroll.setRange(0, max(0, len(tracks) - 4))
        self.scroll.setVisible(len(tracks) > 4)
        if follow:
            self.scroll.setValue(self.scroll.maximum())
        elif anchor in tracks:
            self.scroll.setValue(self.keys.index(anchor))
        self.tracks = tracks
        self.sample_time = now
        self.motion = motion
        self.show()
        if (
            self.isVisible()
            and self.motion
            and any(abs(a - b) > 0.0001 for _, a, b, _ in tracks.values())
        ):
            self.timer.start()
        else:
            self.timer.stop()
        self.setAccessibleName("Active files")
        self.setAccessibleDescription(
            ", ".join(
                f"{name}, {target:.0%}"
                for name, _, target, _ in list(tracks.values())[
                    self.scroll.value() : self.scroll.value() + 4
                ]
            )
        )
        self.update()

    def resizeEvent(self, event):
        self.scroll.setGeometry(self.width() - 15, 28, 13, 106)
        super().resizeEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta and self.scroll.maximum():
            self.scroll.setValue(self.scroll.value() + (-1 if delta > 0 else 1))
            event.accept()
        else:
            super().wheelEvent(event)

    def fraction(self, key, now):
        _, start, target, _ = self.tracks[key]
        t = min(1, max(0, (now - self.sample_time) / 0.40))
        return start + (target - start) * t

    def animate(self):
        self.update()
        if time.monotonic() - self.sample_time >= 0.40:
            self.timer.stop()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        left = 12
        status_right = w - (20 if self.scroll.isVisible() else 10)
        right = status_right - 52
        limit = max(60, min(w * 0.50, 345, right - 20))
        p.setBrush(QColor("#18131f"))
        p.setPen(QPen(QColor("#40334e"), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, self.height() - 1), 8, 8)
        number(
            p, QRectF(left, 2, w - 24, 24), "FILE ACTIVITY", 10, "#beadcd", bold=True
        )
        now = time.monotonic()
        if not self.keys:
            number(p, QRectF(left, 28, w - 32, 22), "Waiting for files…", 11, "#ad9bbd")
        start = self.scroll.value()
        for i, key in enumerate(self.keys[start : start + 4]):
            name, _, target, _ = self.tracks[key]
            row = 28 + i * 27
            number(p, QRectF(left, row, 22, 21), f"{start + i + 1:02d}", 10, "#ad8dd4")
            # Use the available label width; long paths retain their basename.
            font = QFont("DejaVu Sans Mono")
            font.setPixelSize(11)
            p.setFont(font)
            text = p.fontMetrics().elidedText(
                name, Qt.ElideLeft, int(limit - left - 39)
            )
            number(
                p, QRectF(left + 29, row, limit - left - 34, 21), text, 11, "#e6deef"
            )
            y = row + 11
            length = right - limit
            fraction = self.fraction(key, now) if self.motion else target
            x = limit + length * fraction
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#34283f"))
            p.drawRoundedRect(QRectF(limit, y - 2, length, 4), 2, 2)
            if fraction:
                glow = QLinearGradient(limit, 0, max(limit + 1, x), 0)
                glow.setColorAt(0, QColor("#70518d"))
                glow.setColorAt(1, QColor("#d4a9fc"))
                p.setBrush(glow)
                p.drawRoundedRect(QRectF(limit, y - 2, max(1, x - limit), 4), 2, 2)
                p.setBrush(QColor("#e7c9ff"))
                p.drawEllipse(QPointF(x, y), 2.8, 2.8)
            number(
                p,
                QRectF(right + 5, row, status_right - right - 5, 21),
                (
                    f"{target:.0%}"
                    if self.states[key] == "working"
                    else self.states[key].capitalize()
                ),
                11,
                "#d7c4e6",
                align=Qt.AlignRight,
            )
        p.end()
