import html
import json
import sqlite3
import sys
import urllib.error
import urllib.request

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QComboBox, QDialog, QMessageBox

from PySide6.QtCore import QThread, Qt, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.database.chat_history import ChatHistory


class AIWorker(QThread):
    response_ready = Signal(str)
    error_occurred = Signal(str)

    def __init__(
        self,
        prompt,
        mode="chat",
        file_path=None,
        file_paths=None,
    ):
        super().__init__()

        self.prompt = prompt
        self.mode = mode
        self.file_path = file_path
        self.file_paths = list(file_paths or ([file_path] if file_path else []))

    def run(self):
        try:
            if self.mode == "document":
                self._run_document_question()
            else:
                self._run_normal_chat()

        except Exception as error:
            self.error_occurred.emit(str(error))

    def _run_normal_chat(self):
        payload = {
            "model": "qwen2.5:3b",
            "prompt": self.prompt,
            "stream": False,
        }

        request = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(
            request,
            timeout=180,
        ) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )

        answer = result.get("response", "").strip()

        if answer:
            self.response_ready.emit(answer)
        else:
            self.error_occurred.emit(
                "The AI returned an empty response."
            )

    def _run_document_question(self):
        if not self.file_paths:
            raise ValueError(
                "No documents are attached to this conversation. "
                "Attach and index a document first."
            )

        from src.indexing.rag_service import RAGService

        rag = RAGService()
        #debug start
        try:
            print("\n--- WORKER DEBUG ---")
            print("Question:", repr(self.prompt))
            print("Attached files:", self.file_paths)

            result = rag.ask(
                question=self.current_question,
                file_paths=self.file_paths,
                limit=6,
            )

            answer = result.get("answer", "").strip()

            print("RAG answer:", repr(answer))
            print("Sources returned:", len(result.get("sources", [])))
            print("--- END WORKER DEBUG ---\n")

            if answer:
                self.response_ready.emit(answer)
            else:
                self.error_occurred.emit(
                    "The document assistant returned an empty answer."
                )
        finally:
            rag.close()
        #debug end

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("PrivAssist")
        self.resize(1100, 720)
        self.setMinimumSize(800, 550)

        self.history = ChatHistory()
        self.current_conversation = self.history.get_current_conversation()

        self.worker = None
        self.busy = False
        self.refreshing_history = False
        self.selected_document = None
        self.pending_attachment_path = None
        self.indexer = None
        self.document_registry_path = (
            Path(__file__).resolve().parents[2]
            / "data" / "database" / "chat_documents.db"
        )
        self.initialize_document_registry()

        self.build_ui()
        self.refresh_history_list()
        self.load_current_conversation(animate=False)

    def build_ui(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #181818;
                color: #eeeeee;
                font-family: "Segoe UI";
                font-size: 10pt;
            }

            QFrame#Sidebar {
                background-color: #202020;
                border: none;
            }

            QLabel#Brand {
                font-size: 19pt;
                font-weight: bold;
                color: #ffffff;
                padding: 8px 4px;
            }

            QPushButton#NewChatButton {
                background-color: #303030;
                color: #ffffff;
                border: 1px solid #454545;
                border-radius: 9px;
                padding: 12px;
                text-align: left;
                font-weight: bold;
            }

            QPushButton#NewChatButton:hover {
                background-color: #404040;
                border-color: #686868;
            }

            QLabel#HistoryLabel {
                color: #a9a9a9;
                font-size: 9pt;
                padding: 10px 5px 4px 5px;
            }

            QListWidget#ChatHistory {
                background-color: transparent;
                border: none;
                outline: none;
                padding: 2px;
            }

            QListWidget#ChatHistory::item {
                color: #dddddd;
                background-color: transparent;
                border-radius: 7px;
                padding: 11px 9px;
                margin: 2px 0;
            }

            QListWidget#ChatHistory::item:hover {
                background-color: #303030;
            }

            QListWidget#ChatHistory::item:selected {
                background-color: #383838;
                color: #ffffff;
            }

            QFrame#MainPanel {
                background-color: #181818;
                border: none;
            }

            QLabel#ChatTitle {
                color: #ffffff;
                font-size: 15pt;
                font-weight: bold;
            }

            QTextBrowser {
                background-color: #181818;
                color: #eeeeee;
                border: none;
                padding: 12px;
                selection-background-color: #454545;
            }

            QLineEdit {
                background-color: #292929;
                color: #ffffff;
                border: 1px solid #414141;
                border-radius: 13px;
                padding: 14px;
                selection-background-color: #555555;
            }

            QLineEdit:focus {
                border: 1px solid #777777;
            }

            QPushButton#SendButton {
                background-color: #eeeeee;
                color: #171717;
                border: none;
                border-radius: 10px;
                padding: 11px 20px;
                font-weight: bold;
            }

            QPushButton#SendButton:hover {
                background-color: #d2d2d2;
            }

            QPushButton#SendButton:disabled,
            QPushButton#NewChatButton:disabled {
                background-color: #303030;
                color: #777777;
                border-color: #383838;
            }

            QLabel#Status {
                color: #999999;
                font-size: 9pt;
                padding: 5px;
            }

            QFrame#SidebarDivider {
                background-color: #343434;
                max-width: 1px;
            }
        """)

        # Main horizontal layout
        root = QWidget()
        self.setCentralWidget(root)

        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ---------------- Sidebar ----------------
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(255)

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(14, 16, 14, 14)
        sidebar_layout.setSpacing(8)

        brand = QLabel("PrivAssist")
        brand.setObjectName("Brand")
        sidebar_layout.addWidget(brand)

        self.new_chat_button = QPushButton("+  New Chat")
        self.new_chat_button.setObjectName("NewChatButton")
        self.new_chat_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_chat_button.clicked.connect(self.new_chat)
        sidebar_layout.addWidget(self.new_chat_button)

        history_label = QLabel("YOUR CONVERSATIONS")
        history_label.setObjectName("HistoryLabel")
        sidebar_layout.addWidget(history_label)

        self.history_list = QListWidget()
        self.history_list.setObjectName("ChatHistory")
        self.history_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.history_list.setCursor(Qt.CursorShape.PointingHandCursor)
        self.history_list.currentItemChanged.connect(
            self.switch_conversation
        )
        sidebar_layout.addWidget(self.history_list, 1)

        sidebar_footer = QLabel("Local AI  •  Ollama")
        sidebar_footer.setStyleSheet(
            "color: #888888; padding: 8px 4px;"
        )
        sidebar_layout.addWidget(sidebar_footer)

        root_layout.addWidget(self.sidebar)

        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        root_layout.addWidget(divider)

        # ---------------- Main chat panel ----------------
        self.main_panel = QFrame()
        self.main_panel.setObjectName("MainPanel")

        panel_layout = QVBoxLayout(self.main_panel)
        panel_layout.setContentsMargins(24, 18, 24, 18)
        panel_layout.setSpacing(12)

        chat_header = QHBoxLayout()
        self.chat_title = QLabel("New Chat")
        self.chat_title.setObjectName("ChatTitle")
        chat_header.addWidget(self.chat_title, 1)

        self.chat_documents_button = QPushButton("Chat Documents (0)")
        self.chat_documents_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chat_documents_button.clicked.connect(self.open_chat_documents)
        self.chat_documents_button.setStyleSheet(
            "QPushButton { background:#292929; border:1px solid #414141; "
            "border-radius:9px; padding:9px 12px; color:#eeeeee; } "
            "QPushButton:hover { background:#383838; }"
        )
        chat_header.addWidget(self.chat_documents_button)
        panel_layout.addLayout(chat_header)
        # Temporary attachment preview (only shown after selecting a file)
        self.attachment_preview = QFrame()
        self.attachment_preview.setObjectName("AttachmentPreview")
        self.attachment_preview.setStyleSheet(
            "QFrame#AttachmentPreview { background:#242424; border:1px solid #414141; "
            "border-radius:10px; } "
            "QLabel { color:#eeeeee; border:none; } "
            "QPushButton { background:transparent; color:#bbbbbb; border:none; "
            "padding:4px 8px; font-size:12pt; } "
            "QPushButton:hover { color:#ffffff; background:#383838; border-radius:6px; }"
        )
        preview_layout = QHBoxLayout(self.attachment_preview)
        preview_layout.setContentsMargins(12, 6, 8, 6)
        preview_layout.setSpacing(8)
        self.attachment_preview_icon = QLabel("📄")
        preview_layout.addWidget(self.attachment_preview_icon)
        self.attachment_preview_label = QLabel("")
        self.attachment_preview_label.setWordWrap(True)
        preview_layout.addWidget(self.attachment_preview_label, 1)
        self.attachment_preview_status = QLabel("Ready · Indexed locally")
        self.attachment_preview_status.setStyleSheet("color:#9e9e9e; font-size:9pt;")
        preview_layout.addWidget(self.attachment_preview_status)
        self.remove_attachment_button = QPushButton("×")
        self.remove_attachment_button.setToolTip("Dismiss attachment preview")
        self.remove_attachment_button.clicked.connect(self.dismiss_attachment_preview)
        preview_layout.addWidget(self.remove_attachment_button)
        self.attachment_preview.hide()
        panel_layout.addWidget(self.attachment_preview)

        # Retain the existing document-mode widgets internally for compatibility;
        # users now attach files with the + button and use Chat Documents to manage them.
        self.mode_selector = QComboBox()
        self.mode_selector.addItems(["Normal Chat", "Ask Selected Document"])
        self.mode_selector.hide()
        self.document_label = QLabel("No document selected")
        self.document_label.hide()
        self.index_document_button = QPushButton("Index Document")
        self.index_document_button.hide()

        self.chat_display = QTextBrowser()
        self.chat_display.setOpenExternalLinks(True)
        self.chat_display.setPlaceholderText("Your conversation will appear here.")
        panel_layout.addWidget(self.chat_display, 1)

        # Subtle fade animation when opening a conversation
        self.opacity_effect = QGraphicsOpacityEffect(self.chat_display)
        self.opacity_effect.setOpacity(1.0)
        self.chat_display.setGraphicsEffect(self.opacity_effect)

        self.fade_animation = QPropertyAnimation(
            self.opacity_effect, b"opacity", self
        )
        self.fade_animation.setDuration(180)
        self.fade_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )

        input_layout = QHBoxLayout()
        input_layout.setSpacing(10)

        self.select_document_button = QPushButton("+")
        self.select_document_button.setToolTip("Attach documents")
        self.select_document_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_document_button.setFixedSize(46, 46)
        self.select_document_button.setStyleSheet(
            "QPushButton { background:#292929; color:#eeeeee; border:1px solid #414141; "
            "border-radius:13px; font-size:22pt; font-weight:normal; } "
            "QPushButton:hover { background:#383838; }"
        )
        self.select_document_button.clicked.connect(self.select_document)
        input_layout.addWidget(self.select_document_button)

        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText(
            "Message PrivAssist..."
        )
        self.prompt_input.returnPressed.connect(self.send_prompt)
        input_layout.addWidget(self.prompt_input, 1)

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("SendButton")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.send_prompt)
        input_layout.addWidget(self.send_button)

        panel_layout.addLayout(input_layout)

        self.status = QLabel("Ready")
        self.status.setObjectName("Status")
        panel_layout.addWidget(self.status)

        root_layout.addWidget(self.main_panel, 1)

    # ---------------- Chat display ----------------

    def add_message(self, speaker, message, color):
        # Clean common LaTeX delimiters.
        message = message.replace("\\(", "")
        message = message.replace("\\)", "")
        message = message.replace("\\[", "")
        message = message.replace("\\]", "")

        # Convert common LaTeX fractions: \frac{a}{b} -> a/b
        import re

        message = re.sub(
            r"\\frac\{([^{}]+)\}\{([^{}]+)\}",
            r"\1/\2",
            message,
        )

        # Remove leftover escaped punctuation and line-break commands.
        message = message.replace("\\\\", "\n")
        message = message.replace("\\,", " ")
        message = message.replace("\\;", " ")
        message = message.replace("\\!", "")
        message = message.replace("\\ ", " ")

        safe_speaker = html.escape(speaker)
        safe_message = html.escape(message).replace("\n", "<br>")

        self.chat_display.append(
            f"""
            <div style="margin-top: 14px; margin-bottom: 6px;">
                <span style="color: {color}; font-weight: bold;">
                    {safe_speaker}
                </span>
            </div>
            <div style="color: #eeeeee; line-height: 1.5;">
                {safe_message}
            </div>
            """
        )

        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def load_current_conversation(self, animate=True):
        self.chat_display.clear()

        messages = self.history.get_messages(
            self.current_conversation["id"]
        )

        if not messages:
            self.add_message(
                "PrivAssist",
                "Hello! I am PrivAssist, your local AI assistant. "
                "How can I help you?",
                "#8ab4f8",
            )
        else:
            for message in messages:
                if message["role"] == "user":
                    self.add_message(
                        "You", message["content"], "#8ab4f8"
                    )
                else:
                    self.add_message(
                        "PrivAssist", message["content"], "#a7d7a9"
                    )

        self.chat_title.setText(
            self.get_conversation_title(self.current_conversation["id"])
        )
        self.status.setText("Ready")
        self.refresh_chat_documents_button()
        self.restore_selected_document_for_current_chat()

        if animate:
            self.fade_animation.stop()
            self.opacity_effect.setOpacity(0.55)
            self.fade_animation.setStartValue(0.55)
            self.fade_animation.setEndValue(1.0)
            self.fade_animation.start()
        else:
            self.opacity_effect.setOpacity(1.0)

    def get_conversation_title(self, conversation_id):
        for index in range(self.history_list.count()):
            item = self.history_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == conversation_id:
                return item.text().lstrip("● ").strip()

        return self.current_conversation.get("title", "New Chat")

    # ---------------- Conversation sidebar ----------------

    def refresh_history_list(self):
        conversations = self.history.get_conversations()

        self.refreshing_history = True
        self.history_list.blockSignals(True)
        self.history_list.clear()

        selected_row = -1

        for row, conversation in enumerate(conversations):
            title = conversation["title"].strip() or "New Chat"

            if conversation["is_current"]:
                display_title = f"●  {title}"
                selected_row = row
            else:
                display_title = title

            item = QListWidgetItem(display_title)
            item.setData(
                Qt.ItemDataRole.UserRole, conversation["id"]
            )
            item.setToolTip(title)
            self.history_list.addItem(item)

        if selected_row >= 0:
            self.history_list.setCurrentRow(selected_row)

        self.history_list.blockSignals(False)
        self.refreshing_history = False

    def switch_conversation(self, current, previous=None):
        if self.refreshing_history or current is None or self.busy:
            return

        conversation_id = current.data(Qt.ItemDataRole.UserRole)

        if conversation_id == self.current_conversation["id"]:
            return

        self.history.set_current_conversation(conversation_id)
        self.current_conversation = self.history.get_current_conversation()

        self.selected_document = None
        self.pending_attachment_path = None
        self.attachment_preview.hide()
        self.load_current_conversation()
        self.restore_selected_document_for_current_chat()
        self.refresh_history_list()

    def restore_selected_document_for_current_chat(self):
        documents = self.get_conversation_documents(
            self.current_conversation["id"]
        )
        if documents:
            self.selected_document = documents[0]["file_path"]
            self.document_label.setText(documents[0]["file_name"])
            self.document_label.setToolTip(self.selected_document)
            self.index_document_button.setEnabled(
                documents[0]["status"].casefold() != "indexed"
            )
        else:
            self.selected_document = None
            self.document_label.setText("No document selected")
            self.document_label.setToolTip("")
            self.index_document_button.setEnabled(False)

    def new_chat(self):
        if self.busy:
            return

        # Reuse the current conversation if it is already empty.
        existing_messages = self.history.get_messages(
            self.current_conversation["id"]
        )

        if not existing_messages:
            self.load_current_conversation()
            self.prompt_input.setFocus()
            return

        # Otherwise create a separate conversation and keep old chats.
        self.current_conversation = self.history.create_conversation()
        self.pending_attachment_path = None
        self.attachment_preview.hide()

        self.refresh_history_list()
        self.load_current_conversation()
        self.prompt_input.setFocus()

    # ---------------- Chat document registry and panel ----------------

    def initialize_document_registry(self):
        """Create a small local database linking documents to conversations."""
        self.document_registry_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.document_registry_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_documents (
                    conversation_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Selected',
                    attached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (conversation_id, file_path)
                )
                """
            )
            connection.commit()

    def save_conversation_document(self, conversation_id, file_path, status):
        """Add or update a document attachment for one conversation."""
        resolved_path = str(Path(file_path).resolve())
        with sqlite3.connect(self.document_registry_path) as connection:
            connection.execute(
                """
                INSERT INTO conversation_documents
                    (conversation_id, file_path, file_name, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id, file_path) DO UPDATE SET
                    file_name = excluded.file_name,
                    status = excluded.status
                """,
                (
                    str(conversation_id),
                    resolved_path,
                    Path(resolved_path).name,
                    status,
                ),
            )
            connection.commit()

    def get_conversation_documents(self, conversation_id):
        with sqlite3.connect(self.document_registry_path) as connection:
            rows = connection.execute(
                """
                SELECT file_path, file_name, status, attached_at
                FROM conversation_documents
                WHERE conversation_id = ?
                ORDER BY attached_at DESC, file_name COLLATE NOCASE
                """,
                (str(conversation_id),),
            ).fetchall()
        return [
            {
                "file_path": row[0],
                "file_name": row[1],
                "status": row[2],
                "attached_at": row[3],
            }
            for row in rows
        ]

    def remove_conversation_document(self, conversation_id, file_path):
        """Detach a document from this chat without deleting its original file."""
        with sqlite3.connect(self.document_registry_path) as connection:
            connection.execute(
                """
                DELETE FROM conversation_documents
                WHERE conversation_id = ? AND file_path = ?
                """,
                (str(conversation_id), str(Path(file_path).resolve())),
            )
            connection.commit()

    def refresh_chat_documents_button(self):
        conversation_id = self.current_conversation["id"]
        documents = self.get_conversation_documents(conversation_id)
        self.chat_documents_button.setText(
            f"Chat Documents ({len(documents)})"
        )

    def open_chat_documents(self):
        conversation_id = self.current_conversation["id"]
        dialog = QDialog(self)
        dialog.setWindowTitle("Chat Documents")
        dialog.resize(560, 390)
        dialog.setStyleSheet(
            "QDialog { background:#202020; color:#eeeeee; } "
            "QListWidget { background:#181818; color:#eeeeee; "
            "border:1px solid #414141; border-radius:8px; padding:5px; } "
            "QPushButton { background:#303030; color:#eeeeee; "
            "border:1px solid #454545; border-radius:7px; padding:8px 12px; } "
            "QPushButton:hover { background:#404040; } "
            "QPushButton:disabled { color:#777777; }"
        )

        layout = QVBoxLayout(dialog)
        heading = QLabel(
            "Documents attached to this conversation"
        )
        heading.setStyleSheet("font-size:12pt; font-weight:bold;")
        layout.addWidget(heading)

        document_list = QListWidget()
        layout.addWidget(document_list, 1)

        def populate_documents():
            document_list.clear()
            documents = self.get_conversation_documents(conversation_id)
            for document in documents:
                item = QListWidgetItem(
                    f"{document['file_name']}  —  {document['status']}"
                )
                item.setData(
                    Qt.ItemDataRole.UserRole, document["file_path"]
                )
                item.setToolTip(document["file_path"])
                document_list.addItem(item)
            remove_button.setEnabled(bool(documents))
            if not documents:
                empty_item = QListWidgetItem(
                    "No documents are attached to this conversation."
                )
                empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
                document_list.addItem(empty_item)

        button_row = QHBoxLayout()
        remove_button = QPushButton("Remove from chat")
        close_button = QPushButton("Close")
        button_row.addWidget(remove_button)
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        def remove_selected_document():
            item = document_list.currentItem()
            if item is None:
                return

            file_path = item.data(Qt.ItemDataRole.UserRole)
            if not file_path:
                return

            answer = QMessageBox.question(
                dialog,
                "Remove document",
                "Remove this document from the current conversation?\n\n"
                "The original file on your computer and its local index "
                "will not be deleted.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

            self.remove_conversation_document(conversation_id, file_path)
            if self.selected_document == file_path:
                self.selected_document = None
                self.document_label.setText("No document selected")
                self.document_label.setToolTip("")
                self.index_document_button.setEnabled(False)
            self.refresh_chat_documents_button()
            populate_documents()

        remove_button.clicked.connect(remove_selected_document)
        close_button.clicked.connect(dialog.accept)
        populate_documents()
        dialog.exec()

    # ---------------- Document indexing ----------------

    def show_attachment_preview(self, file_path, status="Indexing locally..."):
        self.pending_attachment_path = str(Path(file_path).resolve())
        self.attachment_preview_label.setText(Path(file_path).name)
        self.attachment_preview_label.setToolTip(self.pending_attachment_path)
        self.attachment_preview_status.setText(status)
        self.attachment_preview.show()

    def dismiss_attachment_preview(self):
        file_path = self.pending_attachment_path
        if file_path:
            self.remove_conversation_document(
                self.current_conversation["id"], file_path
            )
            if self.selected_document == file_path:
                self.selected_document = None
        self.pending_attachment_path = None
        self.attachment_preview.hide()
        self.refresh_chat_documents_button()
        self.status.setText("Attachment dismissed")

    def select_document(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select a document",
            "",
            "Documents (*.pdf *.docx *.txt)",
        )

        if not file_path:
            return

        self.selected_document = str(Path(file_path).resolve())
        self.save_conversation_document(
            self.current_conversation["id"], self.selected_document, "Selected"
        )
        self.refresh_chat_documents_button()

        self.document_label.setText(
            Path(file_path).name
        )
        self.document_label.setToolTip(
            self.selected_document
        )
        self.index_document_button.setEnabled(True)
        self.show_attachment_preview(self.selected_document, "Indexing locally...")
        self.status.setText("Document selected; indexing locally...")
        self.index_document()

    def index_document(self):
        if self.busy:
            return

        if not self.selected_document:
            self.status.setText("Select a document first")
            return

        self.busy = True
        self.select_document_button.setEnabled(False)
        self.index_document_button.setEnabled(False)
        self.new_chat_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self.history_list.setEnabled(False)
        self.prompt_input.setEnabled(True)
        self.status.setText("Indexing document locally...")

        from PySide6.QtCore import QThread, Signal

        class IndexWorker(QThread):
            indexing_ready = Signal(str)
            indexing_error = Signal(str)

            def __init__(self, file_path):
                super().__init__()
                self.file_path = file_path

            def run(self):
                from src.indexing.document_indexer import (
                    DocumentIndexer,
                )

                indexer = DocumentIndexer()

                try:
                    result = indexer.index_document(
                        self.file_path
                    )

                    self.indexing_ready.emit(
                        f"Indexed {result['file_name']} "
                        f"({result['chunk_count']} chunks)."
                    )
                except Exception as error:
                    self.indexing_error.emit(str(error))
                finally:
                    indexer.close()

        self.index_worker = IndexWorker(
            self.selected_document
        )

        self.index_worker.indexing_ready.connect(
            self.document_indexing_finished
        )
        self.index_worker.indexing_error.connect(
            self.document_indexing_failed
        )
        self.index_worker.finished.connect(
            self.document_worker_finished
        )
        self.index_worker.start()

    def document_indexing_finished(self, message):
        if self.selected_document:
            self.save_conversation_document(
                self.current_conversation["id"], self.selected_document, "Indexed"
            )
            self.refresh_chat_documents_button()
        self.attachment_preview_status.setText("Ready · Indexed locally")
        self.status.setText(message)

    def document_indexing_failed(self, message):
        if self.selected_document:
            self.save_conversation_document(
                self.current_conversation["id"], self.selected_document, "Index failed"
            )
            self.refresh_chat_documents_button()
        self.attachment_preview_status.setText("Indexing failed")
        self.status.setText("Document indexing failed")
        self.add_message(
            "Error",
            f"Could not index document: {message}",
            "#ff9b9b",
        )

    def document_worker_finished(self):
        self.busy = False
        self.select_document_button.setEnabled(True)
        self.index_document_button.setEnabled(
            self.selected_document is not None
        )
        self.new_chat_button.setEnabled(True)
        self.send_button.setEnabled(True)
        self.history_list.setEnabled(True)
        self.prompt_input.setEnabled(True)
        self.prompt_input.setFocus()
        self.index_worker = None

    def mode_changed(self, index):
        if index == 1:
            documents = self.get_conversation_documents(
                self.current_conversation["id"]
            )
            if not documents:
                self.status.setText("Attach a document to this chat first")
            elif not any(d["status"].casefold() == "indexed" for d in documents):
                self.status.setText("Index the attached document(s) first")
            else:
                self.status.setText("Document Q&A will search this chat's indexed attachments")
        else:
            self.status.setText("Normal chat mode selected")

    # ---------------- AI interaction ----------------

    def answer_document_inventory(self, prompt, conversation_id):
        """Answer attachment metadata questions, not document-content questions."""
        text = prompt.casefold().strip()

        # Content questions must go through RAG, even when they mention
        # "attached documents" or "attached files".
        content_intent_terms = (
            "explain",
            "describe",
            "summarize",
            "summarise",
            "summary",
            "tell me about",
            "tell about",
            "talk about",
            "what does",
            "what do",
            "what is",
            "what are",
            "contents",
            "content of",
            "cover",
            "discuss",
            "details about",
            "information about",
        )

        if any(term in text for term in content_intent_terms):
            return False

        # Handle only questions asking for attachment metadata.
        inventory_terms = (
            "how many",
            "number of",
            "count",
            "list",
            "which files",
            "what files",
            "what documents",
            "documents did i upload",
            "files did i upload",
            "file names",
            "filenames",
            "format",
            "formats",
            "extension",
            "extensions",
            "uploaded files",
            "attached files",
            "attached documents",
        )

        if not any(term in text for term in inventory_terms):
            return False

        documents = self.get_conversation_documents(conversation_id)

        if not documents:
            answer = "There are no documents attached to this conversation."
        else:
            wants_count = any(term in text for term in (
                "how many", "number of", "count",
            ))

            wants_list = any(term in text for term in (
                "list",
                "which files",
                "what files",
                "what documents",
                "documents did i upload",
                "files did i upload",
                "file names",
                "filenames",
                "uploaded files",
                "attached files",
                "attached documents",
            ))

            wants_format = any(term in text for term in (
                "format", "formats", "extension", "extensions",
            ))

            lines = []

            if wants_count:
                lines.append(
                    f"You have {len(documents)} document(s) "
                    "attached to this conversation."
                )

            if wants_list or wants_format:
                lines.append("Documents attached to this conversation:")

                for document in documents:
                    name = document["file_name"]
                    extension = Path(name).suffix.lower()

                    file_format = {
                        ".pdf": "PDF",
                        ".docx": "DOCX (Word document)",
                        ".txt": "plain text",
                    }.get(
                        extension,
                        extension.lstrip(".").upper() or "unknown format",
                    )

                    if wants_format:
                        lines.append(
                            f"- {name} — {file_format} — "
                            f"status: {document['status']}"
                        )
                    else:
                        lines.append(
                            f"- {name} — status: {document['status']}"
                        )

            answer = "\n".join(lines) or (
                f"There are {len(documents)} document(s) "
                "attached to this conversation."
            )

        self.history.add_message("assistant", answer, conversation_id)
        self.add_message("PrivAssist", answer, "#a7d7a9")
        self.status.setText("Attachment information")
        self.refresh_history_list()

        return True

    def send_prompt(self):
        prompt = self.prompt_input.text().strip()

        if not prompt or self.busy:
            return

        conversation_id = self.current_conversation["id"]

        try:
            self.history.add_message(
                "user", prompt, conversation_id
            )
        except Exception as error:
            self.status.setText("Could not save your message")
            print(f"Database error: {error}")
            return

        self.add_message("You", prompt, "#8ab4f8")
        self.prompt_input.clear()
        self.pending_attachment_path = None
        self.attachment_preview.hide()

        if self.answer_document_inventory(prompt, conversation_id):
            self.prompt_input.setFocus()
            return

        self.busy = True
        self.status.setText("PrivAssist is thinking...")
        self.prompt_input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.new_chat_button.setEnabled(False)
        self.history_list.setEnabled(False)

        self.refresh_history_list()
        # Load recent messages from the current conversation.
        messages = self.history.get_messages(conversation_id)

        # Keep the latest 12 messages as context.
        recent_messages = messages[-12:]

        conversation_context = "\n".join(
            f"{'User' if message['role'] == 'user' else 'PrivAssist'}: "
            f"{message['content']}"
            for message in recent_messages
        )

        model_prompt = f"""You are PrivAssist, a helpful offline AI assistant.

Use the conversation history below to understand follow-up questions.
Answer questions based on the previous messages when relevant.
If the user asks how, why, or what about something discussed earlier,
explain that previous topic directly.
Do not ask for clarification when the conversation already provides
enough context.

Conversation history:
{conversation_context}

PrivAssist:"""

        attached_documents = self.get_conversation_documents(conversation_id)
        indexed_documents = [
            document for document in attached_documents
            if document["status"].casefold() == "indexed"
        ]

        if indexed_documents:
            # When this conversation has indexed attachments, use them as context
            # automatically. Conversations without documents remain normal chat.
            self.worker = AIWorker(
                model_prompt + "\n\nUse the attached conversation documents when relevant. "
                "Base document-specific claims on retrieved passages and say when "
                "the documents do not contain the requested information.",
                mode="document",
                file_paths=[document["file_path"] for document in indexed_documents],
            )
        else:
            self.worker = AIWorker(model_prompt, mode="chat")

        self.worker.response_ready.connect(
            lambda response, cid=conversation_id:
                self.show_response(response, cid)
        )
        self.worker.error_occurred.connect(self.show_error)
        self.worker.finished.connect(self.request_finished)
        self.worker.start()

    def show_response(self, response, conversation_id):
        try:
            self.history.add_message(
                "assistant", response, conversation_id
            )
            self.add_message(
                "PrivAssist", response, "#a7d7a9"
            )
            self.status.setText("Response received")
        except Exception as error:
            self.add_message(
                "System",
                "The response was generated but could not be saved.",
                "#ff9b9b",
            )
            self.status.setText("Response received but not saved")
            print(f"Database error: {error}")

        self.refresh_history_list()

    def show_error(self, message):
        self.add_message("Error", message, "#ff9b9b")
        self.status.setText("Something went wrong")

    def request_finished(self):
        self.busy = False
        self.prompt_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.new_chat_button.setEnabled(True)
        self.history_list.setEnabled(True)
        self.prompt_input.setFocus()
        self.worker = None
        self.refresh_history_list()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait()

        self.history.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()