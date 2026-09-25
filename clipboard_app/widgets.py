"""Small widgets shared by the panel and its dialogs."""

from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QLabel, QSizePolicy


class WrappedLabel(QLabel):
    """Keep every wrapped line when layouts change the available width."""

    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        policy = self.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def fit_height(self):
        # QLabel includes its minimum height in heightForWidth. Clear the old
        # constraint so a wider label can shrink back to fewer wrapped lines.
        self.setMinimumHeight(0)
        height = max(0, self.heightForWidth(self.width())) if self.text() else 0
        if height != self.minimumHeight():
            self.setMinimumHeight(height)

    def setText(self, text):
        super().setText(text)
        self.fit_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_height()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self.fit_height()
