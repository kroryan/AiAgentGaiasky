"""
PyQt6 overlay: a frameless, translucent, always-on-top panel that sits visually
inside Gaia Sky without being part of it or requiring any change to it.

Talks to the Agent only; the Agent talks to Gaia Sky only over REST (gaiasky.py).
Markdown from the model is rendered with QTextDocument's Markdown support, so
replies use real headings, lists and tables in the system font — no monospace
text block, per the maintainer's feedback on the original in-app UI.
"""

from __future__ import annotations

import threading

from PyQt6 import QtCore, QtGui, QtWidgets

from . import store
from .agent import Agent, Listener
from .config import AppConfig
from .gaiasky import GaiaSkyClient
from .llm import ApiType, LLMConfig
from .tools import ToolRegistry

ACCENT = "#5b8def"


# ==================================================================== agent bridge

class AgentSignals(QtCore.QObject):
    """Agent callbacks fire on the agent's worker thread; Qt widgets may only be
    touched from the UI thread. Every callback re-emits through one of these
    signals, which Qt marshals onto the UI thread automatically."""
    assistant_message = QtCore.pyqtSignal(str)
    tool_call = QtCore.pyqtSignal(str, str, bool)
    tool_result = QtCore.pyqtSignal(str, str, bool)
    error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()


class ConnectionWorker(QtCore.QObject):
    status = QtCore.pyqtSignal(bool)
    _stop_requested = False

    def __init__(self, client: GaiaSkyClient):
        super().__init__()
        self.client = client

    @QtCore.pyqtSlot()
    def run(self) -> None:
        while not self._stop_requested:
            try:
                self.status.emit(self.client.ping())
            except Exception:
                self.status.emit(False)
            QtCore.QThread.msleep(4000)

    def stop(self) -> None:
        self._stop_requested = True


# ==================================================================== transcript widgets

class MarkdownBubble(QtWidgets.QTextBrowser):
    """One message, rendered as real Markdown (tables included) rather than plain text."""

    def __init__(self, role: str, text: str, font_size: int = 13, on_resized=None, parent=None):
        super().__init__(parent)
        self._on_resized = on_resized
        self.setOpenExternalLinks(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setReadOnly(True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Minimum)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Break long unbroken tokens (URLs, paths, ids) too, not just at word
        # boundaries, so they can never force the bubble wider than the panel.
        self.setWordWrapMode(QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(10)
        bg = "rgba(91, 141, 239, 0.20)" if role == "user" else "rgba(255, 255, 255, 0.08)"
        self.setStyleSheet(
            f"QTextBrowser {{ background: {bg}; border-radius: 10px; color: #f0f0f0; font-size: {font_size}px; }}"
        )
        self.document().setMarkdown(text)
        self.document().documentLayout().documentSizeChanged.connect(self._resize_to_content)
        self._resize_to_content()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        # Rewrap to the bubble's actual current width on every resize (window
        # resize, panel drag, first layout pass) instead of relying on the
        # timing of Qt's own internal auto-wrap, which is what previously left
        # stale, overflowing text until the window was manually resized.
        self.document().setTextWidth(self.viewport().width())
        self._resize_to_content()

    def _resize_to_content(self, *_args) -> None:
        height = self.document().size().height()
        self.setFixedHeight(int(height) + 20)
        if self._on_resized:
            self._on_resized()

    def set_text(self, text: str) -> None:
        self.document().setMarkdown(text)
        self.document().setTextWidth(self.viewport().width())
        self._resize_to_content()


class ToolLine(QtWidgets.QWidget):
    """A single collapsible line reporting one tool call, expandable to its result."""

    def __init__(self, name: str, summary: str, mutating: bool, on_resized=None, parent=None):
        super().__init__(parent)
        self._on_resized = on_resized
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        icon = "⚙" if mutating else "🔍"
        self._header = QtWidgets.QLabel(f"{icon} {summary}  …")
        self._header.setWordWrap(True)
        self._header.setStyleSheet("color: #9aa4b2; font-size: 11px;")
        self._header.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._header.mousePressEvent = self._toggle
        layout.addWidget(self._header)

        self._detail = QtWidgets.QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: #c7ccd3; font-size: 11px; padding-left: 16px;")
        self._detail.setVisible(False)
        layout.addWidget(self._detail)

        self._summary = summary
        self._icon = icon

    def _toggle(self, _event) -> None:
        self._detail.setVisible(not self._detail.isVisible())
        if self._on_resized:
            self._on_resized()

    def set_result(self, result: str, error: bool) -> None:
        mark = "✗" if error else "✓"
        self._header.setText(f"{self._icon} {self._summary}  {mark}")
        if error:
            self._header.setStyleSheet("color: #e28080; font-size: 11px;")
        self._detail.setText(result)
        if self._on_resized:
            self._on_resized()


class Transcript(QtWidgets.QScrollArea):
    def __init__(self, font_size: int = 13, parent=None):
        super().__init__(parent)
        self.font_size = font_size
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setStyleSheet("background: transparent;")
        self._container = QtWidgets.QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._layout = QtWidgets.QVBoxLayout(self._container)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.setWidget(self._container)
        # "Sticky" autoscroll: stay pinned to the bottom as new content arrives
        # or grows (e.g. the final message bubble settling into its real,
        # wrapped height) as long as the user hasn't scrolled up to read
        # earlier messages. Manually scrolling up disables it; scrolling back
        # down to the bottom re-enables it.
        self._stick_to_bottom = True
        self.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)

    def _on_scroll_value_changed(self, value: int) -> None:
        bar = self.verticalScrollBar()
        self._stick_to_bottom = value >= bar.maximum() - 4

    def _request_scroll(self) -> None:
        if self._stick_to_bottom:
            QtCore.QTimer.singleShot(0, self._scroll_to_bottom)

    def _insert(self, widget: QtWidgets.QWidget) -> None:
        self._layout.insertWidget(self._layout.count() - 1, widget)
        self._request_scroll()

    def add_message(self, role: str, text: str) -> MarkdownBubble:
        bubble = MarkdownBubble(role, text, font_size=self.font_size, on_resized=self._request_scroll)
        self._insert(bubble)
        return bubble

    def add_tool(self, name: str, summary: str, mutating: bool) -> ToolLine:
        line = ToolLine(name, summary, mutating, on_resized=self._request_scroll)
        self._insert(line)
        return line

    def add_status(self, text: str) -> None:
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: #9aa4b2; font-size: 11px; font-style: italic;")
        self._insert(label)

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


# ==================================================================== collapsed bubble

class Bubble(QtWidgets.QWidget):
    """
    A small floating circle the overlay collapses to. A plain `showMinimized()` was
    tried first, but a frameless Qt.WindowType.Tool window is deliberately excluded
    from the taskbar/window list by most window managers, so once minimized there was
    no way to bring it back. This bubble is the "minimized" state instead: always on
    top, draggable, and one click restores the full panel.
    """
    clicked = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__(
            None,
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool,
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(52, 52)
        self._drag_pos: QtCore.QPoint | None = None
        self._dragged = False

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(QtGui.QColor(ACCENT))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, self.width(), self.height())
        painter.setPen(QtGui.QColor("white"))
        font = painter.font()
        font.setPointSize(18)
        painter.setFont(font)
        painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "💬")

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragged = False

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            self._dragged = True

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_pos is not None and not self._dragged:
            self.clicked.emit()
        self._drag_pos = None


class NotificationToast(QtWidgets.QWidget):
    """
    A floating, self-closing banner for show_notification: its own top-level window,
    centered over the screen above Gaia Sky, on top of everything but not drawn inside
    it and not part of the chat transcript either. Closed automatically after
    `duration_ms`; the overlay only ever keeps a couple of these alive at once (see
    OverlayWindow._enqueue_notification), so a model that calls show_notification
    repeatedly cannot flood the screen with them.
    """
    closed = QtCore.pyqtSignal(QtCore.QObject)

    def __init__(self, message: str, duration_ms: int = 4500):
        super().__init__(
            None,
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool,
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QtWidgets.QLabel(f"🔔  {message}")
        label.setWordWrap(True)
        label.setMaximumWidth(420)
        label.setStyleSheet(
            "background: rgba(24, 26, 32, 235); color: #f0f0f0; font-size: 14px; "
            f"border-radius: 12px; padding: 14px 20px; border: 1px solid {ACCENT};"
        )
        layout.addWidget(label)
        self.adjustSize()

        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            x = geometry.left() + (geometry.width() - self.width()) // 2
            y = geometry.top() + int(geometry.height() * 0.18)
            self.move(x, y)

        QtCore.QTimer.singleShot(duration_ms, self._close)

    def _close(self) -> None:
        self.closed.emit(self)
        self.close()


class ChatInput(QtWidgets.QPlainTextEdit):
    """A plain QPlainTextEdit sends nothing on Enter by default (it just inserts a
    newline), which reads as broken in a chat box. Enter submits; Shift+Enter still
    inserts a newline for multi-line messages."""
    submitted = QtCore.pyqtSignal()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        is_enter = event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter)
        if is_enter and not (event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


# ==================================================================== title bar (drag handle)

class TitleBar(QtWidgets.QWidget):
    new_chat = QtCore.pyqtSignal()
    history_requested = QtCore.pyqtSignal()
    settings_requested = QtCore.pyqtSignal()
    minimize_requested = QtCore.pyqtSignal()
    close_requested = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self._drag_pos: QtCore.QPoint | None = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 6, 4)

        self.status_dot = QtWidgets.QLabel("●")
        self.status_dot.setStyleSheet("color: #888; font-size: 14px;")
        layout.addWidget(self.status_dot)

        title = QtWidgets.QLabel("Gaia Sky Assistant")
        title.setStyleSheet("color: #eee; font-weight: 600; font-size: 12px;")
        layout.addWidget(title)
        layout.addStretch(1)

        for symbol, signal, tooltip in (
            ("＋", self.new_chat, "New conversation"),
            ("☰", self.history_requested, "History"),
            ("⚙", self.settings_requested, "Settings"),
            ("—", self.minimize_requested, "Minimize"),
            ("✕", self.close_requested, "Close"),
        ):
            button = QtWidgets.QToolButton()
            button.setText(symbol)
            button.setToolTip(tooltip)
            button.setStyleSheet(
                "QToolButton { color: #ccc; background: transparent; border: none; font-size: 13px; padding: 2px 6px; }"
                "QToolButton:hover { color: white; background: rgba(255,255,255,0.12); border-radius: 4px; }"
            )
            button.clicked.connect(signal.emit)
            layout.addWidget(button)

    def set_connected(self, connected: bool) -> None:
        self.status_dot.setStyleSheet(f"color: {'#5bd97f' if connected else '#e2704f'}; font-size: 14px;")
        self.status_dot.setToolTip("Connected to Gaia Sky" if connected else "Not connected to Gaia Sky")

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_pos = None


# ==================================================================== settings dialog

class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, config: AppConfig, gaiasky: GaiaSkyClient, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.config = config
        self.gaiasky = gaiasky
        self.resize(420, 480)

        form = QtWidgets.QFormLayout()

        self.gaiasky_url = QtWidgets.QLineEdit(config.gaiasky_url)
        form.addRow("Gaia Sky URL", self.gaiasky_url)

        test_row = QtWidgets.QHBoxLayout()
        self.test_button = QtWidgets.QPushButton("Test connection")
        self.test_label = QtWidgets.QLabel("")
        self.test_button.clicked.connect(self._test_connection)
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_label)
        form.addRow("", test_row)

        self.llm_api = QtWidgets.QComboBox()
        self.llm_api.addItems(["ollama", "openai"])
        self.llm_api.setCurrentText(config.llm_api)
        form.addRow("Backend", self.llm_api)

        self.llm_url = QtWidgets.QLineEdit(config.llm_url)
        form.addRow("Server URL", self.llm_url)

        self.api_key = QtWidgets.QLineEdit(config.llm_api_key)
        self.api_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        form.addRow("API key", self.api_key)

        model_row = QtWidgets.QHBoxLayout()
        self.model = QtWidgets.QComboBox()
        self.model.setEditable(True)
        self.model.setCurrentText(config.llm_model)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_models)
        model_row.addWidget(self.model, 1)
        model_row.addWidget(refresh)
        form.addRow("Model", model_row)

        self.temperature = QtWidgets.QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(config.llm_temperature)
        form.addRow("Temperature", self.temperature)

        self.timeout = QtWidgets.QSpinBox()
        self.timeout.setRange(10, 600)
        self.timeout.setValue(int(config.llm_timeout))
        form.addRow("Timeout (s)", self.timeout)

        self.max_tool_calls = QtWidgets.QSpinBox()
        self.max_tool_calls.setRange(0, 500)
        self.max_tool_calls.setValue(config.max_tool_calls)
        self.max_tool_calls.setSpecialValueText("Unlimited")
        form.addRow("Tool call budget", self.max_tool_calls)

        self.system_prompt = QtWidgets.QPlainTextEdit(config.system_prompt)
        self.system_prompt.setFixedHeight(80)
        form.addRow("Extra instructions", self.system_prompt)

        self.opacity = QtWidgets.QDoubleSpinBox()
        self.opacity.setRange(0.2, 1.0)
        self.opacity.setSingleStep(0.05)
        self.opacity.setValue(config.overlay_opacity)
        form.addRow("Overlay opacity", self.opacity)

        self.font_size = QtWidgets.QSpinBox()
        self.font_size.setRange(10, 24)
        self.font_size.setValue(config.font_size)
        form.addRow("Font size", self.font_size)

        self.window_mode = QtWidgets.QCheckBox("Use a normal window instead of a frameless overlay")
        self.window_mode.setChecked(config.window_mode)
        form.addRow("", self.window_mode)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        inner.setLayout(form)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        outer.addWidget(buttons)

    def _test_connection(self) -> None:
        client = GaiaSkyClient(base_url=self.gaiasky_url.text())
        ok = client.ping()
        self.test_label.setText("Reachable" if ok else "Not reachable")
        self.test_label.setStyleSheet(f"color: {'#5bd97f' if ok else '#e2704f'};")

    def _refresh_models(self) -> None:
        from .llm import LLMClient, LLMConfig, ApiType, LLMError
        try:
            client = LLMClient(LLMConfig(api=ApiType(self.llm_api.currentText()), url=self.llm_url.text(),
                                          api_key=self.api_key.text()))
            names = client.list_models()
            current = self.model.currentText()
            self.model.clear()
            self.model.addItems(names)
            if current:
                self.model.setCurrentText(current)
        except LLMError as e:
            QtWidgets.QMessageBox.warning(self, "Could not list models", str(e))

    def apply_to(self, config: AppConfig) -> None:
        config.gaiasky_url = self.gaiasky_url.text().strip()
        config.llm_api = self.llm_api.currentText()
        config.llm_url = self.llm_url.text().strip()
        config.llm_api_key = self.api_key.text()
        config.llm_model = self.model.currentText().strip()
        config.llm_temperature = self.temperature.value()
        config.llm_timeout = float(self.timeout.value())
        config.max_tool_calls = self.max_tool_calls.value()
        config.system_prompt = self.system_prompt.toPlainText()
        config.overlay_opacity = self.opacity.value()
        config.font_size = self.font_size.value()
        config.window_mode = self.window_mode.isChecked()


# ==================================================================== history dialog

class HistoryDialog(QtWidgets.QDialog):
    conversation_selected = QtCore.pyqtSignal(str)
    conversation_deleted = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Conversation history")
        self.resize(360, 420)
        layout = QtWidgets.QVBoxLayout(self)
        self.list_widget = QtWidgets.QListWidget()
        layout.addWidget(self.list_widget)

        for conversation in store.list_all():
            item = QtWidgets.QListWidgetItem(conversation.title or "(untitled)")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, conversation.id)
            self.list_widget.addItem(item)

        row = QtWidgets.QHBoxLayout()
        open_button = QtWidgets.QPushButton("Open")
        delete_button = QtWidgets.QPushButton("Delete")
        row.addWidget(open_button)
        row.addWidget(delete_button)
        layout.addLayout(row)

        open_button.clicked.connect(self._open_selected)
        delete_button.clicked.connect(self._delete_selected)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._open_selected())

    def _open_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is not None:
            self.conversation_selected.emit(item.data(QtCore.Qt.ItemDataRole.UserRole))
            self.accept()

    def _delete_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is not None:
            conversation_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
            store.delete(conversation_id)
            self.conversation_deleted.emit(conversation_id)
            self.list_widget.takeItem(self.list_widget.row(item))


# ==================================================================== main overlay window

class OverlayWindow(QtWidgets.QWidget):
    def __init__(self, config: AppConfig):
        flags = (
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        if config.window_mode:
            flags = QtCore.Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.config = config
        if not config.window_mode:
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(config.overlay_opacity if not config.window_mode else 1.0)
        self.resize(420, 620)
        self.setMinimumSize(300, 300)
        self._position_default()
        self._initial_window_mode = config.window_mode

        self.gaiasky = GaiaSkyClient(base_url=config.gaiasky_url)
        self.registry = ToolRegistry(self.gaiasky)
        self.llm_config = LLMConfig(
            api=ApiType(config.llm_api), url=config.llm_url, model=config.llm_model,
            api_key=config.llm_api_key, temperature=config.llm_temperature, timeout=config.llm_timeout,
            max_tool_calls=config.max_tool_calls,
        )
        self.agent = Agent(self.registry, self.llm_config, system_prompt_extra=config.system_prompt)

        self.signals = AgentSignals()
        self._pending_tool_lines: dict[str, ToolLine] = {}
        self._bubble: Bubble | None = None
        self._active_toasts: list[NotificationToast] = []
        self._notification_queue: list[str] = []
        self._build_ui()
        self._wire_signals()
        self._start_connection_watch()

    # --- layout ---

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        panel = QtWidgets.QFrame()
        panel.setObjectName("panel")
        panel.setStyleSheet(
            "#panel { background: rgba(18, 20, 26, 235); border-radius: 12px; }"
            if not self.config.window_mode else "#panel { background: rgb(24,26,32); }"
        )
        outer.addWidget(panel)
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        self.title_bar = TitleBar()
        panel_layout.addWidget(self.title_bar)

        self.transcript = Transcript(font_size=self.config.font_size)
        panel_layout.addWidget(self.transcript, 1)

        input_row = QtWidgets.QHBoxLayout()
        input_row.setContentsMargins(8, 6, 8, 8)
        self.input_box = ChatInput()
        self.input_box.setFixedHeight(56)
        self.input_box.setPlaceholderText("Ask about what you're looking at, or where to go next… (Enter to send, Shift+Enter for a new line)")
        self.input_box.setStyleSheet(
            "QPlainTextEdit { background: rgba(255,255,255,0.06); color: #eee; border-radius: 8px; "
            "border: 1px solid rgba(255,255,255,0.12); padding: 6px; font-size: 13px; }"
        )
        self.send_button = QtWidgets.QPushButton("Send")
        self.send_button.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: white; border-radius: 8px; padding: 6px 14px; }}"
            "QPushButton:disabled { background: #555; }"
        )
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setStyleSheet(
            "QPushButton { background: #b3453f; color: white; border-radius: 8px; padding: 6px 14px; }"
        )
        self.stop_button.setVisible(False)
        input_row.addWidget(self.input_box, 1)
        input_row.addWidget(self.send_button)
        input_row.addWidget(self.stop_button)
        panel_layout.addLayout(input_row)

        if not self.config.window_mode:
            grip = QtWidgets.QSizeGrip(panel)
            grip_row = QtWidgets.QHBoxLayout()
            grip_row.addStretch(1)
            grip_row.addWidget(grip, 0, QtCore.Qt.AlignmentFlag.AlignBottom | QtCore.Qt.AlignmentFlag.AlignRight)
            panel_layout.addLayout(grip_row)

    def _wire_signals(self) -> None:
        self.title_bar.new_chat.connect(self._on_new_chat)
        self.title_bar.history_requested.connect(self._on_history)
        self.title_bar.settings_requested.connect(self._on_settings)
        self.title_bar.minimize_requested.connect(self._collapse_to_bubble)
        self.title_bar.close_requested.connect(self.close)

        self.send_button.clicked.connect(self._on_send)
        self.input_box.submitted.connect(self._on_send)
        self.stop_button.clicked.connect(self._on_stop)

        self.signals.assistant_message.connect(self._on_assistant_message)
        self.signals.tool_call.connect(self._on_tool_call)
        self.signals.tool_result.connect(self._on_tool_result)
        self.signals.error.connect(self._on_error)
        self.signals.finished.connect(self._on_finished)

    def _start_connection_watch(self) -> None:
        self._conn_thread = QtCore.QThread()
        self._conn_worker = ConnectionWorker(self.gaiasky)
        self._conn_worker.moveToThread(self._conn_thread)
        self._conn_thread.started.connect(self._conn_worker.run)
        self._conn_worker.status.connect(self.title_bar.set_connected)
        self._conn_thread.start()

    # --- sending / receiving ---

    def _on_send(self) -> None:
        text = self.input_box.toPlainText().strip()
        if not text or self.agent.is_running():
            return
        self.input_box.clear()
        self.transcript.add_message("user", text)
        self.send_button.setEnabled(False)
        self.stop_button.setVisible(True)

        listener = Listener(
            on_assistant_message=lambda m: self.signals.assistant_message.emit(m),
            on_tool_call=lambda n, s, mut: self.signals.tool_call.emit(n, s, mut),
            on_tool_result=lambda n, r, e: self.signals.tool_result.emit(n, r, e),
            on_error=lambda m: self.signals.error.emit(m),
            on_finished=lambda: self.signals.finished.emit(),
        )
        self.agent.send(text, listener)

    def _on_stop(self) -> None:
        self.agent.cancel()

    def _on_assistant_message(self, message: str) -> None:
        self.transcript.add_message("assistant", message)

    def _on_tool_call(self, name: str, summary: str, mutating: bool) -> None:
        if name == "show_notification":
            return  # Rendered from the result instead; see _on_tool_result.
        self._pending_tool_lines[name] = self.transcript.add_tool(name, summary, mutating)

    def _on_tool_result(self, name: str, result: str, error: bool) -> None:
        if name == "show_notification" and not error:
            self._enqueue_notification(result)
            return
        line = self._pending_tool_lines.pop(name, None)
        if line is not None:
            line.set_result(result, error)
        else:
            self.transcript.add_status(("declined: " if not error else "error: ") + result)

    def _on_error(self, message: str) -> None:
        self.transcript.add_status(f"⚠ {message}")

    def _on_finished(self) -> None:
        self.send_button.setEnabled(True)
        self.stop_button.setVisible(False)

    # --- menu actions ---

    def _on_new_chat(self) -> None:
        self.agent.reset()
        self.transcript = Transcript(font_size=self.config.font_size)
        self._rebuild_transcript_widget()

    def _rebuild_transcript_widget(self) -> None:
        layout = self.layout().itemAt(0).widget().layout()
        old = layout.itemAt(1).widget()
        layout.replaceWidget(old, self.transcript)
        old.deleteLater()

    def _on_history(self) -> None:
        dialog = HistoryDialog(self)
        dialog.conversation_selected.connect(self._load_conversation)
        dialog.exec()

    def _load_conversation(self, conversation_id: str) -> None:
        conversation = store.load(conversation_id)
        self.agent.load(conversation)
        self.transcript = Transcript(font_size=self.config.font_size)
        self._rebuild_transcript_widget()
        for message in conversation.messages:
            if message.role == "user" and message.content:
                self.transcript.add_message("user", message.content)
            elif message.role == "assistant" and message.content:
                self.transcript.add_message("assistant", message.content)

    def _on_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.gaiasky, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            dialog.apply_to(self.config)
            self.config.save()
            self.gaiasky.base_url = self.config.gaiasky_url
            self.llm_config.api = ApiType(self.config.llm_api)
            self.llm_config.url = self.config.llm_url
            self.llm_config.model = self.config.llm_model
            self.llm_config.api_key = self.config.llm_api_key
            self.llm_config.temperature = self.config.llm_temperature
            self.llm_config.timeout = self.config.llm_timeout
            self.llm_config.max_tool_calls = self.config.max_tool_calls
            self.agent.system_prompt_extra = self.config.system_prompt
            self.setWindowOpacity(self.config.overlay_opacity if not self.config.window_mode else 1.0)
            # Applies at once, no restart needed: new chat messages pick up the size.
            self.transcript.font_size = self.config.font_size
            if self.config.window_mode != self._initial_window_mode:
                QtWidgets.QMessageBox.information(
                    self, "Restart needed",
                    "Switching between overlay and normal window mode needs a restart to take effect."
                )

    def _position_default(self) -> None:
        """
        Gaia Sky keeps its own HUD (time/focus readout, tool icons) along the left and
        top-left of the screen. Rather than leave the initial spot to the window
        manager, which can easily land the panel on top of that, this places it in the
        upper-right area with a wider-than-minimal margin from the right edge, clear
        of Gaia Sky's own widgets.
        """
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        margin_top = 70
        # Anchored by proportion of screen width rather than a fixed right-edge
        # margin, so it lands well clear of the right edge on any monitor size.
        x = geometry.left() + int(geometry.width() * 0.58)
        y = geometry.top() + margin_top
        self.move(max(geometry.left(), x), y)

    def _collapse_to_bubble(self) -> None:
        self.hide()
        if self._bubble is None:
            self._bubble = Bubble()
            self._bubble.clicked.connect(self._restore_from_bubble)
            # Only place it on first collapse; if the user has since dragged the
            # bubble elsewhere, minimizing again should not snap it back.
            screen = self.screen() or QtWidgets.QApplication.primaryScreen()
            geometry = screen.availableGeometry()
            margin_bottom = 24
            margin_right = 24 + 80  # extra ~2cm clear of the very corner/system tray
            x = geometry.right() - self._bubble.width() - margin_right
            y = geometry.bottom() - self._bubble.height() - margin_bottom
            self._bubble.move(x, y)
        self._bubble.show()

    def _restore_from_bubble(self) -> None:
        if self._bubble is not None:
            self._bubble.hide()
        self.show()
        self.raise_()
        self.activateWindow()

    # At most one notification is ever shown at a time, with one more allowed to
    # wait its turn — so a model that calls show_notification several times in a
    # row cannot flood the screen with a stack of toasts; a newer message bumps a
    # stale queued one rather than piling up behind it.
    _MAX_ACTIVE_NOTIFICATIONS = 1
    _MAX_QUEUED_NOTIFICATIONS = 1

    def _enqueue_notification(self, message: str) -> None:
        capacity = self._MAX_ACTIVE_NOTIFICATIONS + self._MAX_QUEUED_NOTIFICATIONS
        if len(self._active_toasts) + len(self._notification_queue) >= capacity and self._notification_queue:
            self._notification_queue.pop(0)
        self._notification_queue.append(message)
        self._drain_notification_queue()

    def _drain_notification_queue(self) -> None:
        while self._notification_queue and len(self._active_toasts) < self._MAX_ACTIVE_NOTIFICATIONS:
            message = self._notification_queue.pop(0)
            toast = NotificationToast(message)
            toast.closed.connect(self._on_toast_closed)
            self._active_toasts.append(toast)
            toast.show()

    def _on_toast_closed(self, toast: NotificationToast) -> None:
        if toast in self._active_toasts:
            self._active_toasts.remove(toast)
        self._drain_notification_queue()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._bubble is not None:
            self._bubble.close()
        for toast in list(self._active_toasts):
            toast.close()
        self._conn_worker.stop()
        self._conn_thread.quit()
        self._conn_thread.wait(2000)
        super().closeEvent(event)


def run(config: AppConfig) -> int:
    app = QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(True)
    window = OverlayWindow(config)
    window.show()
    window.transcript.add_status(
        "Connecting to Gaia Sky… make sure it is running with the REST API enabled."
    )
    return app.exec()
