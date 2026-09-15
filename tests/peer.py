"""An isolated external editor for real X11 integration tests. Synthetic data only."""

import json
import sys

from PyQt5.QtCore import QMimeData, QTimer, QUrl
from PyQt5.QtNetwork import QLocalServer
from PyQt5.QtGui import QColor, QImage, QTextCharFormat
from PyQt5.QtWidgets import QApplication, QLineEdit, QTextEdit, QVBoxLayout, QWidget

from clipboard_app.x11 import Target, X11

app = QApplication([])
window = QWidget()
window.setWindowTitle("Clipboard test editor — synthetic content only")
window.resize(600, 400)
layout = QVBoxLayout(window)
edit = QTextEdit()
chat = QLineEdit()
layout.addWidget(edit)
layout.addWidget(chat)
submissions = []
chat.returnPressed.connect(lambda: submissions.append(chat.text()))
window.show()
backend = X11()
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
            backend.activate(Target(int(window.winId())))
            result = {"window": int(window.winId())}
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
