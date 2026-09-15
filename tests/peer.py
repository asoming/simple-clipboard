"""An isolated external editor for real X11 integration tests. Synthetic data only."""

import json
import itertools
import sys

from PyQt5.QtCore import QMimeData, QTimer, QUrl
from PyQt5.QtNetwork import QLocalServer
from PyQt5.QtGui import QColor, QImage, QPixmap, QTextCharFormat, QTextDocument
from PyQt5.QtWidgets import QApplication, QLineEdit, QTextEdit, QVBoxLayout, QWidget

from clipboard_app.platforms import Target, create_backend

app = QApplication([])


class ImageEditor(QTextEdit):
    """An image-capable receiver; stock QTextEdit does not accept raster MIME."""
    image_ids = itertools.count()

    def canInsertFromMimeData(self, source):
        return source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QPixmap):
                image = image.toImage()
            if isinstance(image, QImage) and not image.isNull():
                name = 'synthetic-image-' + str(next(self.image_ids))
                self.document().addResource(QTextDocument.ImageResource, QUrl(name), image)
                self.textCursor().insertImage(name)
                return
        super().insertFromMimeData(source)


window = QWidget()
window.setWindowTitle("Clipboard test editor — synthetic content only")
window.resize(600, 400)
layout = QVBoxLayout(window)
edit = ImageEditor()
chat = QLineEdit()
layout.addWidget(edit)
layout.addWidget(chat)
submissions = []
chat.returnPressed.connect(lambda: submissions.append(chat.text()))
window.show()
backend = create_backend()
server = QLocalServer()
QLocalServer.removeServer(sys.argv[1])
assert server.listen(sys.argv[1])
clients = []


def connect():
    socket = server.nextPendingConnection()
    clients.append(socket)
    buffer = bytearray()

    def read():
        buffer.extend(bytes(socket.readAll()))
        if b"\n" not in buffer:
            return
        request = json.loads(buffer.split(b"\n")[0])
        action = request["action"]
        result = {}
        if action == "copy":
            mime = QMimeData()
            mime.setText(request["text"])
            if request.get("html"):
                mime.setHtml(request["html"])
            if request.get("image"):
                image = QImage(480, 300, QImage.Format_ARGB32)
                image.fill(QColor('#47617e'))
                mime.setImageData(image)
            if request.get("file"):
                mime.setUrls([QUrl.fromLocalFile("/tmp/synthetic-file.txt")])
            if request.get("secret"):
                mime.setData("x-kde-passwordManagerHint", b"secret")
            app.clipboard().setMimeData(mime)
        elif action == "focus":
            edit.setPlainText(request.get("text", ""))
            edit.setCurrentCharFormat(QTextCharFormat())
            chat.setText("")
            (chat if request.get("chat") else edit).setFocus()
            window.raise_()
            window.activateWindow()
            backend.activate(Target(backend.window_id(window)))
            result = {"window": backend.window_id(window)}
        elif action == "read":
            result = {"text": edit.toPlainText(), "html": edit.toHtml(), "chat": chat.text(), "submissions": submissions}
        elif action == "clipboard":
            mime = app.clipboard().mimeData()
            image = mime.imageData() if mime.hasImage() else None
            result = {"text": mime.text(), "html": mime.html(), "has_image": mime.hasImage(),
                      "dimensions": [image.width(), image.height()] if isinstance(image, QImage) else []}
        elif action == "quit":
            QTimer.singleShot(100, app.quit)
        socket.write(json.dumps(result).encode() + b"\n")
        socket.flush()
        socket.disconnectFromServer()
    socket.readyRead.connect(read)


server.newConnection.connect(connect)
app.exec_()
backend.close()
