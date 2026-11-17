"""SSH Deck – gespeicherte SSH-Verbindungen mit Sidebar, Tabs und echtem Terminal."""
import codecs
import ctypes
import json
import math
import os
import re
import socket
import sys
import threading
import urllib.request
import uuid
from pathlib import Path

import keyring
import paramiko
from PySide6.QtCore import (QFile, QIODevice, QObject, QPointF, QPropertyAnimation,
                            QRect, QRectF, QSize, Qt, QUrl, QVariantAnimation,
                            Signal, Slot)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QIntValidator, QKeySequence, QPainter, QPainterPath,
                           QPen, QRadialGradient, QShortcut)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QStackedWidget, QStyle,
                               QStyledItemDelegate, QTabBar, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

APP = "SSHDeck"
VERSION = "0.1"
DATA = Path(os.environ.get("APPDATA") or Path.home()) / APP
DATA.mkdir(parents=True, exist_ok=True)
CONN_FILE = DATA / "connections.json"
HOSTS_FILE = DATA / "known_hosts"
TERM_HTML = DATA / "terminal.html"
VENDOR = DATA / "vendor"
CDN = {
    "xterm.css": "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.css",
    "xterm.js": "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js",
    "addon-fit.js": "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.js",
}

# Farben: Indigo-Schiefer mit einem einzigen periwinkle-blauen Akzent
C = dict(bg="#10131C", side="#0B0E15", surface="#171B27", hover="#1D2230",
         line="#252B3B", text="#DCE1EE", muted="#7B849C", accent="#8B9CFF",
         ok="#5FD38D", danger="#FF7A8A")


def ansi(text, rgb):
    return f"\x1b[38;2;{rgb}m{text}\x1b[0m"


# ---------------------------------------------------------------- Speicher
def load_conns():
    try:
        return json.loads(CONN_FILE.read_text("utf-8"))
    except Exception:
        return []


def save_conns(conns):
    CONN_FILE.write_text(json.dumps(conns, indent=2, ensure_ascii=False), "utf-8")


def get_pw(cid):
    try:
        return keyring.get_password(APP, cid)
    except Exception:
        return None


def set_pw(cid, pw):
    try:
        keyring.set_password(APP, cid, pw)
    except Exception as e:
        QMessageBox.warning(None, "Passwort nicht gespeichert", str(e))


def del_pw(cid):
    try:
        keyring.delete_password(APP, cid)
    except Exception:
        pass


# ---------------------------------------------------------------- Terminal-Seite
TERM_SRC = """<!doctype html><html><head><meta charset="utf-8">
@CSS@
<style>
html,body{margin:0;height:100%;background:@BG@;overflow:hidden}
#t{position:absolute;inset:10px 6px 6px 14px}
.xterm-viewport{background:transparent!important}
.xterm-viewport::-webkit-scrollbar{width:8px}
.xterm-viewport::-webkit-scrollbar-thumb{background:#2b3245;border-radius:4px}
#err{display:none;color:#FF7A8A;font:14px "Segoe UI",sans-serif;padding:24px}
</style></head><body><div id="t"></div><div id="err"></div>
<script>window.__errs=[];window.addEventListener("error",function(e){var t=e.target;__errs.push(t&&t.src?"Laden fehlgeschlagen: "+t.src.split("/").pop():(e.message||"Fehler"));},true);</script>
@SCRIPTS@
<script>
const missing = [];
if (typeof Terminal === 'undefined') missing.push('xterm.js');
if (typeof FitAddon === 'undefined') missing.push('addon-fit');
if (typeof QWebChannel === 'undefined') missing.push('QWebChannel');
if (missing.length) {
  const e = document.getElementById('err'); e.style.display = 'block';
  e.textContent = 'Das Terminal konnte nicht geladen werden. Es fehlt: ' + missing.join(', ')
    + '. Details: ' + (window.__errs.join(' | ') || 'keine') + ' [@INFO@]';
  throw new Error('missing: ' + missing.join(', '));
}
const term = new Terminal({
  fontFamily: '"Cascadia Code","Cascadia Mono",Consolas,monospace',
  fontSize: 14, lineHeight: 1.15, cursorBlink: true, cursorStyle: 'bar',
  scrollback: 10000,
  theme: {background:'@BG@', foreground:'#DCE1EE', cursor:'#8B9CFF', cursorAccent:'#10131C',
    selectionBackground:'#2E3656', black:'#2A3045', red:'#FF7A8A', green:'#5FD38D',
    yellow:'#F2C97D', blue:'#8B9CFF', magenta:'#C79BFF', cyan:'#6FD3E8', white:'#C9D0E0',
    brightBlack:'#4A5370', brightRed:'#FF9AA6', brightGreen:'#86E3AB', brightYellow:'#F7DA9E',
    brightBlue:'#AEBBFF', brightMagenta:'#DAB9FF', brightCyan:'#9BE4F2', brightWhite:'#F2F4FA'}
});
window.term = term;
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById('t'));
fit.fit();
new QWebChannel(qt.webChannelTransport, ch => {
  const b = ch.objects.bridge;
  b.output.connect(d => term.write(d));
  term.onData(d => b.send(d));
  // Wie in PowerShell: Strg+C kopiert bei Auswahl, sonst Abbruch
  term.attachCustomKeyEventHandler(e => {
    if (e.type === 'keydown' && e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === 'c' && term.hasSelection()) {
      b.copy(term.getSelection()); term.clearSelection(); return false;
    }
    return true;
  });
  // Rechtsklick: Auswahl kopieren, sonst einfügen
  document.addEventListener('contextmenu', e => {
    e.preventDefault();
    if (term.hasSelection()) { b.copy(term.getSelection()); term.clearSelection(); }
    else { b.paste(); }
  });
  new ResizeObserver(() => { fit.fit(); b.resize(term.cols, term.rows); }).observe(document.body);
  b.ready(term.cols, term.rows);
  term.focus();
});
</script></body></html>"""


APP_DIR = Path(__file__).resolve().parent
VENDOR_DIRS = [APP_DIR / "vendor", VENDOR]  # zuerst der Ordner neben ssh_deck.py


def find_asset(name):
    for d in VENDOR_DIRS:
        f = d / name
        if f.exists() and f.stat().st_size > 1000:
            return f
    return None


def have_asset(name):
    return find_asset(name) is not None


def use_bundle():
    """xterm-bundle.js enthält xterm.js und das Fit-Addon in einer Datei."""
    return have_asset("xterm-bundle.js") and have_asset("xterm.css")


def missing_assets():
    return [] if use_bundle() else [n for n in CDN if not have_asset(n)]


def fetch_assets():
    """Lädt xterm.js einmalig herunter, falls es nicht lokal vorhanden ist."""
    if use_bundle():
        return
    VENDOR.mkdir(parents=True, exist_ok=True)
    for name, url in CDN.items():
        if have_asset(name):
            continue
        target = VENDOR / name
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                target.write_bytes(r.read())
        except Exception as e:
            print(f"Download von {name} fehlgeschlagen: {e}")


def webchannel_tag():
    """Bettet qwebchannel.js direkt ein, statt es über qrc:// nachzuladen."""
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        js = bytes(f.readAll()).decode("utf-8", "replace")
        f.close()
        return "<script>" + js + "</script>"
    return '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>'


def build_terminal_html():
    fetch_assets()

    def text(name):
        return find_asset(name).read_text("utf-8", "replace").replace("</script", "<\\/script")

    def size(name):
        f = find_asset(name)
        return f"{name} {f.stat().st_size // 1024} KB" if f else f"{name} fehlt"

    names = ("xterm-bundle.js", "xterm.js", "addon-fit.js", "xterm.css")
    if use_bundle():  # Bundle: xterm.js + Fit-Addon in einer Datei
        css = "<style>" + text("xterm.css") + "</style>"
        scripts = "<script>" + text("xterm-bundle.js") + "</script>\n" + webchannel_tag()
    elif not missing_assets():  # einzeln heruntergeladene Dateien, ebenfalls eingebettet
        css = "<style>" + text("xterm.css") + "</style>"
        scripts = ("<script>" + text("xterm.js") + "</script>\n"
                   "<script>" + text("addon-fit.js") + "</script>\n" + webchannel_tag())
    else:  # letzter Ausweg: CDN
        css = f'<link rel="stylesheet" href="{CDN["xterm.css"]}">'
        scripts = (f'<script src="{CDN["xterm.js"]}"></script>\n'
                   f'<script src="{CDN["addon-fit.js"]}"></script>\n' + webchannel_tag())
    info = f"v{VERSION} | " + ", ".join(size(n) for n in names) + f" | gesucht in {VENDOR_DIRS[0]} und {VENDOR}"
    html = TERM_SRC.replace("@BG@", C["bg"]).replace("@INFO@", info.replace("\\", "/").replace("'", "’"))
    html = html.replace("@CSS@", css).replace("@SCRIPTS@", scripts)
    TERM_HTML.write_text(html, "utf-8")


class Session(QObject):
    """Eine SSH-Sitzung, die in einem eigenen Thread läuft."""
    data = Signal(str)
    ended = Signal(str)

    def __init__(self, conn, password, cols, rows):
        super().__init__()
        self.conn, self.password, self.cols, self.rows = conn, password, cols, rows
        self.chan = None
        self._stop = False

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop = True
        self.blockSignals(True)

    def send(self, text):
        try:
            if self.chan:
                self.chan.send(text.encode("utf-8"))
        except Exception:
            pass

    def resize(self, cols, rows):
        try:
            if self.chan:
                self.chan.resize_pty(width=cols, height=rows)
        except Exception:
            pass

    def _run(self):
        client = paramiko.SSHClient()
        if HOSTS_FILE.exists():
            client.load_host_keys(str(HOSTS_FILE))
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        reason = "Verbindung getrennt."
        try:
            client.connect(hostname=self.conn["host"], port=int(self.conn["port"]),
                           username=self.conn["user"], password=self.password,
                           look_for_keys=False, allow_agent=False,
                           timeout=10, banner_timeout=15, auth_timeout=15)
            client.save_host_keys(str(HOSTS_FILE))
            client.get_transport().set_keepalive(30)
            self.chan = client.invoke_shell(term="xterm-256color", width=self.cols, height=self.rows)
            self.chan.settimeout(0.3)
            while not self._stop:
                try:
                    buf = self.chan.recv(65536)
                except socket.timeout:
                    continue
                if not buf:
                    break
                self.data.emit(decoder.decode(buf))
        except paramiko.AuthenticationException:
            reason = "Anmeldung fehlgeschlagen. Prüfe Benutzer und Passwort."
        except paramiko.BadHostKeyException:
            reason = "Der Host-Schlüssel hat sich geändert. Verbindung aus Sicherheitsgründen abgebrochen."
        except Exception as e:
            reason = f"Verbindung fehlgeschlagen: {e}"
        finally:
            client.close()
        if not self._stop:
            self.ended.emit(reason)


class Bridge(QObject):
    """Verbindet xterm.js (JavaScript) mit Python."""
    output = Signal(str)

    def __init__(self, tab):
        super().__init__()
        self.tab = tab

    @Slot(int, int)
    def ready(self, cols, rows):
        self.tab.on_ready(cols, rows)

    @Slot(str)
    def send(self, data):
        self.tab.send(data)

    @Slot(int, int)
    def resize(self, cols, rows):
        self.tab.on_resize(cols, rows)

    @Slot(str)
    def copy(self, text):
        QGuiApplication.clipboard().setText(text)

    @Slot()
    def paste(self):
        text = QGuiApplication.clipboard().text()
        self.tab.send(text.replace("\r\n", "\n").replace("\n", "\r"))


class TerminalTab(QWidget):
    def __init__(self, conn, password):
        super().__init__()
        self.conn, self.password = conn, password
        self.session = None
        self.live = False
        self.size = (120, 30)
        self.on_state = lambda: None

        self.view = QWebEngineView(self)
        self.view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.view.page().setBackgroundColor(QColor(C["bg"]))
        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.view.loadFinished.connect(lambda _ok: self.focus_terminal())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.view.load(QUrl.fromLocalFile(str(TERM_HTML)))

    def focus_terminal(self):
        self.view.setFocus()
        self.view.page().runJavaScript("window.term && window.term.focus()")

    def on_ready(self, cols, rows):
        self.size = (cols, rows)
        self.connect_now()

    def on_resize(self, cols, rows):
        self.size = (cols, rows)
        if self.session and self.live:
            self.session.resize(cols, rows)

    def connect_now(self):
        c = self.conn
        self.say(ansi(f"Verbinde mit {c['user']}@{c['host']} …\r\n", "139;156;255"))
        self.session = Session(c, self.password, *self.size)
        self.session.data.connect(self.say)
        self.session.ended.connect(self.on_ended)
        self.live = True
        self.on_state()
        self.session.start()

    @Slot(str)
    def say(self, text):
        self.bridge.output.emit(text)

    @Slot(str)
    def on_ended(self, reason):
        self.live = False
        self.on_state()
        self.say("\r\n" + ansi(reason, "255;122;138") + "\r\n"
                 + ansi("Enter drücken, um erneut zu verbinden.", "123;132;156") + "\r\n")

    def send(self, data):
        if self.live and self.session:
            self.session.send(data)
        elif self.session is not None and "\r" in data:
            self.say("\r\n")
            self.connect_now()

    def shutdown(self):
        if self.session:
            self.session.stop()


# ---------------------------------------------------------------- Dialog
class ConnDialog(QDialog):
    def __init__(self, parent, conn=None, has_pw=False):
        super().__init__(parent)
        self.setWindowTitle("Verbindung bearbeiten" if conn else "Neue Verbindung")
        self.setMinimumWidth(420)
        conn = conn or {}
        self.name = QLineEdit(conn.get("name", ""))
        self.name.setPlaceholderText("z. B. Webserver")
        self.host = QLineEdit(conn.get("host", ""))
        self.host.setPlaceholderText("192.168.1.10 oder server.example.de")
        self.port = QLineEdit(str(conn.get("port", 22)))
        self.port.setValidator(QIntValidator(1, 65535))
        self.user = QLineEdit(conn.get("user", ""))
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setPlaceholderText("Leer lassen, um das gespeicherte zu behalten" if has_pw
                                   else "Leer = bei jeder Verbindung fragen")
        self.err = QLabel("")
        self.err.setObjectName("err")

        form = QFormLayout()
        form.setVerticalSpacing(12)
        for label, w in (("Name", self.name), ("Host", self.host), ("Port", self.port),
                         ("Benutzer", self.user), ("Passwort", self.pw)):
            form.addRow(label, w)

        save = QPushButton("Speichern")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.err, 1)
        row.addWidget(cancel)
        row.addWidget(save)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        lay.setSpacing(18)
        lay.addLayout(form)
        lay.addLayout(row)

    def accept(self):
        if not self.host.text().strip() or not self.user.text().strip():
            self.err.setText("Host und Benutzer fehlen.")
            return
        super().accept()

    def values(self):
        host = self.host.text().strip()
        data = {"name": self.name.text().strip() or host, "host": host,
                "port": int(self.port.text() or 22), "user": self.user.text().strip()}
        return data, self.pw.text()


# ---------------------------------------------------------------- Sidebar-Liste
class ConnDelegate(QStyledItemDelegate):
    def __init__(self, win):
        super().__init__(win.list)
        self.win = win

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 54)

    def paint(self, p, opt, index):
        c = self.win.by_id(index.data(Qt.ItemDataRole.UserRole))
        if not c:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = opt.rect.adjusted(8, 2, -8, -2)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hovered = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        p.setPen(Qt.PenStyle.NoPen)
        if selected or hovered:
            p.setBrush(QColor(C["hover"] if selected else "#151927"))
            p.drawRoundedRect(r, 8, 8)
        if selected:
            p.setBrush(QColor(C["accent"]))
            p.drawRoundedRect(QRect(r.left(), r.top() + 11, 3, r.height() - 22), 1.5, 1.5)

        live = self.win.live_count(c["id"]) > 0
        p.setBrush(QColor(C["ok"] if live else "#363E54"))
        p.drawEllipse(QRect(r.left() + 14, r.center().y() - 4, 8, 8))

        tx = r.left() + 34
        w = r.right() - tx - 8
        f1 = QFont(opt.font)
        f1.setPixelSize(13)
        f1.setWeight(QFont.Weight.DemiBold)
        p.setFont(f1)
        p.setPen(QColor(C["text"]))
        p.drawText(QRect(tx, r.top() + 8, w, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f1).elidedText(c["name"], Qt.TextElideMode.ElideRight, w))
        f2 = QFont(opt.font)
        f2.setPixelSize(12)
        f2.setWeight(QFont.Weight.Normal)
        p.setFont(f2)
        p.setPen(QColor(C["muted"]))
        sub = f"{c['user']}@{c['host']}" + (f":{c['port']}" if int(c["port"]) != 22 else "")
        p.drawText(QRect(tx, r.top() + 27, w, 16), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f2).elidedText(sub, Qt.TextElideMode.ElideRight, w))
        p.restore()


# ---------------------------------------------------------------- Hauptfenster
QSS = """
* { font-family: "Segoe UI Variable Text","Segoe UI"; font-size: 13px; color: @text; }
QMainWindow, QDialog, QMessageBox, QInputDialog { background: @bg; }
#side { background: @side; border-right: 1px solid @line; }
#brand { font-size: 15px; font-weight: 600; }
#h1 { font-size: 20px; font-weight: 600; }
#muted { color: @muted; }
#err { color: @danger; }
QLineEdit { background: @surface; border: 1px solid @line; border-radius: 8px; padding: 8px 10px;
            selection-background-color: @accent; selection-color: @bg; }
QLineEdit:focus { border-color: @accent; }
QListWidget { background: transparent; border: none; outline: none; }
QPushButton { background: @surface; border: 1px solid @line; border-radius: 8px; padding: 8px 16px; }
QPushButton:hover { background: @hover; }
QPushButton#primary { background: @accent; color: @bg; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #A3B1FF; }
QToolButton { background: transparent; border: none; border-radius: 6px; padding: 2px 9px; color: @muted; font-size: 18px; }
QToolButton:hover { background: @hover; color: @text; }
QToolButton#tabclose { font-size: 15px; padding: 0 5px; margin-right: 6px; }
QTabWidget::pane { border: none; background: @bg; }
QTabBar { background: @side; }
QTabBar::tab { background: transparent; color: @muted; padding: 10px 6px 9px 16px; min-width: 90px;
               border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: @text; background: @bg; border-bottom: 2px solid @accent; }
QTabBar::tab:hover:!selected { color: @text; }
QMenu { background: @surface; border: 1px solid @line; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 7px 20px; border-radius: 5px; }
QMenu::item:selected { background: @hover; }
QScrollBar:vertical { width: 8px; background: transparent; }
QScrollBar::handle:vertical { background: @line; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
for _k, _v in C.items():
    QSS = QSS.replace("@" + _k, _v)

ADHOC = re.compile(r"(?P<user>[^@\s]+)@(?P<host>[^:\s@]+)(?::(?P<port>\d+))?")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"SSH Deck {VERSION}")
        self.conns = load_conns()

        # Sidebar
        side = QWidget()
        side.setObjectName("side")
        side.setFixedWidth(300)
        brand = QLabel("SSH Deck")
        brand.setObjectName("brand")
        add = QToolButton()
        add.setText("+")
        add.setToolTip("Neue Verbindung (Strg+Umschalt+N)")
        add.clicked.connect(self.new_conn)
        head = QHBoxLayout()
        head.addWidget(brand, 1)
        head.addWidget(add)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen oder user@host eingeben")
        self.search.setToolTip("Enter verbindet • Strg+Umschalt+P springt hierher")
        self.search.textChanged.connect(lambda _t: self.refresh())
        self.search.returnPressed.connect(self.quick_connect)

        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setItemDelegate(ConnDelegate(self))
        self.list.itemActivated.connect(lambda it: self.open_conn(self.by_id(it.data(Qt.ItemDataRole.UserRole))))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context_menu)

        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 16, 14, 10)
        sl.setSpacing(12)
        sl.addLayout(head)
        sl.addWidget(self.search)
        sl.addWidget(self.list, 1)

        # Rechte Seite: Leerzustand oder Tabs
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.currentChanged.connect(
            lambda i: i >= 0 and self.tabs.widget(i).focus_terminal())

        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.setSpacing(10)
        self.empty_title = QLabel()
        self.empty_title.setObjectName("h1")
        self.empty_text = QLabel()
        self.empty_text.setObjectName("muted")
        first = QPushButton("Neue Verbindung")
        first.setObjectName("primary")
        first.clicked.connect(self.new_conn)
        for w in (self.empty_title, self.empty_text):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.addWidget(w)
        el.addSpacing(8)
        el.addWidget(first, 0, Qt.AlignmentFlag.AlignHCenter)

        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(self.tabs)

        root = QWidget()
        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(side)
        rl.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        for key, fn in (("Ctrl+Shift+P", self.focus_search), ("Ctrl+Shift+N", self.new_conn),
                        ("Ctrl+Shift+W", self.close_current),
                        ("Ctrl+Tab", lambda: self.step(1)), ("Ctrl+Shift+Tab", lambda: self.step(-1))):
            QShortcut(QKeySequence(key), self).activated.connect(fn)

        self.refresh()

    # ---- Hilfen
    def by_id(self, cid):
        return next((c for c in self.conns if c["id"] == cid), None)

    def live_count(self, cid):
        n = 0
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if t.live and t.conn["id"] == cid:
                n += 1
        return n

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def step(self, d):
        n = self.tabs.count()
        if n:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % n)

    # ---- Liste
    def refresh(self, select_id=None):
        q = self.search.text().strip().lower()
        self.list.clear()
        for c in sorted(self.conns, key=lambda c: c["name"].lower()):
            if q and q not in f"{c['name']} {c['host']} {c['user']}".lower():
                continue
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, c["id"])
            self.list.addItem(it)
            if c["id"] == select_id:
                self.list.setCurrentItem(it)
        if self.list.count() and not self.list.currentItem():
            self.list.setCurrentRow(0)
        if self.conns:
            self.empty_title.setText("Keine Sitzung offen")
            self.empty_text.setText("Doppelklick auf eine Verbindung oder Strg+Umschalt+P zum Suchen.")
        else:
            self.empty_title.setText("Noch keine Verbindungen")
            self.empty_text.setText("Lege deine erste Verbindung an.")

    def current_conn(self):
        it = self.list.currentItem()
        return self.by_id(it.data(Qt.ItemDataRole.UserRole)) if it else None

    def context_menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        c = self.by_id(it.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        menu.addAction("Verbinden", lambda: self.open_conn(c))
        menu.addAction("Bearbeiten", lambda: self.edit(c))
        menu.addAction("Löschen", lambda: self.delete(c))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # ---- Bearbeiten
    def new_conn(self):
        self.edit(None)

    def edit(self, conn):
        dlg = ConnDialog(self, conn, bool(conn and get_pw(conn["id"])))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data, pw = dlg.values()
        if conn:
            conn.update(data)
        else:
            conn = {"id": uuid.uuid4().hex, **data}
            self.conns.append(conn)
        if pw:
            set_pw(conn["id"], pw)
        save_conns(self.conns)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh(conn["id"])

    def delete(self, conn):
        answer = QMessageBox.question(self, "Verbindung löschen",
                                      f"„{conn['name']}“ wirklich löschen?")
        if answer == QMessageBox.StandardButton.Yes:
            self.conns.remove(conn)
            del_pw(conn["id"])
            save_conns(self.conns)
            self.refresh()

    # ---- Verbinden
    def quick_connect(self):
        if self.list.count():
            it = self.list.currentItem() or self.list.item(0)
            self.open_conn(self.by_id(it.data(Qt.ItemDataRole.UserRole)))
            self.search.clear()
            return
        m = ADHOC.fullmatch(self.search.text().strip())
        if m:
            host = m["host"]
            self.open_conn({"id": "", "name": host, "host": host,
                            "port": int(m["port"] or 22), "user": m["user"]})
            self.search.clear()

    def open_conn(self, conn):
        if not conn:
            return
        password = get_pw(conn["id"]) if conn["id"] else None
        if password is None:
            password, ok = QInputDialog.getText(
                self, "Passwort", f"Passwort für {conn['user']}@{conn['host']}",
                QLineEdit.EchoMode.Password)
            if not ok:
                return
        tab = TerminalTab(conn, password)
        tab.on_state = self.list.viewport().update
        idx = self.tabs.addTab(tab, conn["name"])
        self.tabs.setTabToolTip(idx, f"{conn['user']}@{conn['host']}")
        close = QToolButton()
        close.setObjectName("tabclose")
        close.setText("×")
        close.setToolTip("Tab schließen (Strg+Umschalt+W)")
        close.clicked.connect(lambda _=False, t=tab: self.close_tab(t))
        self.tabs.tabBar().setTabButton(idx, QTabBar.ButtonPosition.RightSide, close)
        self.stack.setCurrentIndex(1)
        self.tabs.setCurrentIndex(idx)

    def close_current(self):
        if self.tabs.count():
            self.close_tab(self.tabs.currentWidget())

    def close_tab(self, tab):
        i = self.tabs.indexOf(tab)
        if i < 0:
            return
        tab.shutdown()
        self.tabs.removeTab(i)
        tab.deleteLater()
        if not self.tabs.count():
            self.stack.setCurrentIndex(0)
        self.list.viewport().update()

    def closeEvent(self, event):
        for i in range(self.tabs.count()):
            self.tabs.widget(i).shutdown()
        super().closeEvent(event)


# ---------------------------------------------------------------- Startanimation
def seg(t, a, b):
    """Fortschritt 0..1 eines Abschnitts [a, b] der Gesamtzeit t."""
    return max(0.0, min(1.0, (t - a) / (b - a)))


def ease(x):
    return 1 - (1 - x) ** 3


def rgba(hex_color, alpha):
    c = QColor(hex_color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def draw_stroke(p, path, progress, color, width):
    """Zeichnet einen Pfad, der sich von Anfang bis Ende 'einzeichnet'."""
    if progress <= 0:
        return
    length = path.length()
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    pen.setDashPattern([length / width, length / width])
    pen.setDashOffset((1 - progress) * length / width)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


class Splash(QWidget):
    """Kurze Startanimation: Logo zeichnet sich, Cursor blinkt, Titel blendet ein."""
    finished = Signal()
    DURATION = 1800

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.SplashScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(480, 320)
        frame = self.frameGeometry()
        frame.moveCenter(QGuiApplication.primaryScreen().availableGeometry().center())
        self.move(frame.topLeft())
        self.t = 0.0
        self._done = False
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(self.DURATION)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._tick)
        self.anim.finished.connect(self._end)
        self.anim.start()

    def _tick(self, value):
        self.t = float(value)
        self.update()

    def _end(self):
        if not self._done:
            self._done = True
            self.finished.emit()

    def mousePressEvent(self, _event):  # Klick überspringt die Animation
        self.anim.setCurrentTime(self.DURATION)

    def paintEvent(self, _event):
        t = self.t
        secs = t * self.DURATION / 1000
        w, h = self.width(), self.height()
        cx, cy = w / 2, 120
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Karte
        p.setPen(QPen(QColor(C["line"]), 1))
        p.setBrush(QColor(C["side"]))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 18, 18)

        # sanftes Leuchten hinter dem Logo
        glow = QRadialGradient(cx, cy, 110)
        glow.setColorAt(0.0, rgba(C["accent"], 0.16 * ease(seg(t, 0.05, 0.6))))
        glow.setColorAt(1.0, rgba(C["accent"], 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), 110, 110)

        # Logo: abgerundetes Quadrat, ">" und blinkender Cursor
        box = QRectF(cx - 38, cy - 38, 76, 76)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rgba(C["surface"], ease(seg(t, 0.12, 0.42))))
        p.drawRoundedRect(box, 18, 18)
        outline = QPainterPath()
        outline.addRoundedRect(box, 18, 18)
        draw_stroke(p, outline, ease(seg(t, 0.0, 0.35)), rgba(C["accent"], 0.55), 2.5)

        chevron = QPainterPath(QPointF(cx - 16, cy - 13))
        chevron.lineTo(cx - 3, cy)
        chevron.lineTo(cx - 16, cy + 13)
        draw_stroke(p, chevron, ease(seg(t, 0.25, 0.5)), QColor(C["accent"]), 5)

        appear = seg(t, 0.5, 0.58)
        if appear > 0:
            blink = 0.35 + 0.65 * (0.5 + 0.5 * math.cos(secs * 2 * math.pi * 2.2))
            p.setPen(QPen(rgba(C["accent"], appear * blink), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx + 4, cy + 13), QPointF(cx + 20, cy + 13))

        # Titel und Untertitel
        title = ease(seg(t, 0.42, 0.7))
        f1 = QFont()
        f1.setFamilies(["Segoe UI Variable Display", "Segoe UI"])
        f1.setPixelSize(26)
        f1.setWeight(QFont.Weight.DemiBold)
        p.setFont(f1)
        p.setPen(rgba(C["text"], title))
        p.drawText(QRectF(0, 188 + (1 - title) * 10, w, 36), Qt.AlignmentFlag.AlignCenter, "SSH Deck")

        sub = ease(seg(t, 0.55, 0.78))
        f2 = QFont()
        f2.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
        f2.setPixelSize(13)
        p.setFont(f2)
        p.setPen(rgba(C["muted"], sub))
        p.drawText(QRectF(0, 228 + (1 - sub) * 8, w, 20), Qt.AlignmentFlag.AlignCenter,
                   "Alle Verbindungen an einem Ort")

        # Fortschrittslinie
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["line"]))
        p.drawRoundedRect(QRectF(cx - 60, h - 34, 120, 3), 1.5, 1.5)
        p.setBrush(QColor(C["accent"]))
        p.drawRoundedRect(QRectF(cx - 60, h - 34, 120 * ease(seg(t, 0.0, 0.92)), 3), 1.5, 1.5)
        p.end()


def reveal(win, splash):
    """Splash blendet aus, das Hauptfenster blendet gleichzeitig ein."""
    win.setWindowOpacity(0.0)
    win.show()
    dark_titlebar(win)
    fade_in = QPropertyAnimation(win, b"windowOpacity", win)
    fade_in.setDuration(350)
    fade_in.setStartValue(0.0)
    fade_in.setEndValue(1.0)
    fade_out = QPropertyAnimation(splash, b"windowOpacity", splash)
    fade_out.setDuration(300)
    fade_out.setStartValue(1.0)
    fade_out.setEndValue(0.0)
    fade_out.finished.connect(splash.close)
    win._anims = [fade_in, fade_out]  # Referenzen halten
    fade_in.start()
    fade_out.start()


def dark_titlebar(win):
    """Dunkle Titelleiste unter Windows 10/11 (wird anderswo ignoriert)."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(win.winId())
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(ctypes.c_int(0x00150E0B)), 4)
    except Exception:
        pass


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    splash = Splash()
    splash.show()
    app.processEvents()
    build_terminal_html()

    missing = missing_assets()
    if missing:
        splash.close()
        links = "<br>".join(f'<a href="{CDN[n]}">{n}</a>' for n in missing)
        box = QMessageBox()
        box.setWindowTitle("Terminal-Dateien fehlen")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            "Die Terminal-Dateien wurden nicht gefunden.<br><br>"
            "<b>Einfachster Weg:</b> Kopiere den Ordner „vendor“ (mit xterm-bundle.js "
            "und xterm.css) neben ssh_deck.py, also hierhin:<br>"
            f"<b>{APP_DIR}</b><br><br>"
            "<b>Alternativ</b> lädst du diese Dateien im Browser herunter (Strg+S) und "
            f"legst sie in <b>{VENDOR}</b>:<br>" + links + "<br><br>"
            "Starte SSH Deck danach neu.")
        open_btn = box.addButton("Programmordner öffnen", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Schließen", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn and sys.platform == "win32":
            os.startfile(APP_DIR)
        return

    win = MainWindow()
    win.resize(1200, 760)
    splash.finished.connect(lambda: reveal(win, splash))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
