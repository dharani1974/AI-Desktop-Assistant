
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_DIR = PROJECT_ROOT / "data" / "database"
DATABASE_PATH = DATABASE_DIR / "privassist.db"


def current_time():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    title: Mapped[str] = mapped_column(
        String(100), default="New Chat", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=current_time, nullable=False
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=current_time, nullable=False
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )


class ChatHistory:
    ALLOWED_ROLES = {"user", "assistant"}

    def __init__(self, database_path=None):
        self.database_path = Path(
            database_path or DATABASE_PATH
        ).resolve()

        self.database_path.parent.mkdir(
            parents=True, exist_ok=True
        )

        self.engine = create_engine(
            f"sqlite:///{self.database_path.as_posix()}"
        )

        self.SessionLocal = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        # Create any new tables without deleting existing data.
        Base.metadata.create_all(self.engine)

        # Add conversation support to databases created by the
        # earlier version of PrivAssist.
        self._migrate_database()

    def _migrate_database(self):
        with self.engine.begin() as connection:
            columns = connection.execute(
                text("PRAGMA table_info(chat_messages)")
            ).fetchall()

            column_names = {column[1] for column in columns}

            if "conversation_id" not in column_names:
                connection.execute(
                    text(
                        "ALTER TABLE chat_messages "
                        "ADD COLUMN conversation_id INTEGER "
                        "REFERENCES conversations(id)"
                    )
                )

        with self.SessionLocal.begin() as session:
            conversations = session.scalars(
                select(Conversation).order_by(Conversation.id)
            ).all()

            # Preserve old messages by placing them in a legacy chat.
            if not conversations:
                legacy = Conversation(
                    title="Previous Conversation",
                    is_current=True,
                )
                session.add(legacy)
                session.flush()

                session.execute(
                    text(
                        "UPDATE chat_messages "
                        "SET conversation_id = :conversation_id "
                        "WHERE conversation_id IS NULL"
                    ),
                    {"conversation_id": legacy.id},
                )
            else:
                # Assign any ungrouped messages to an existing chat.
                current = next(
                    (item for item in conversations if item.is_current),
                    conversations[0],
                )

                session.execute(
                    text(
                        "UPDATE chat_messages "
                        "SET conversation_id = :conversation_id "
                        "WHERE conversation_id IS NULL"
                    ),
                    {"conversation_id": current.id},
                )

                # Ensure only one conversation is current.
                for item in conversations:
                    item.is_current = item.id == current.id

            # A database with no messages still needs a current chat.
            if not session.scalars(
                select(Conversation.id).where(
                    Conversation.is_current.is_(True)
                )
            ).first():
                session.add(
                    Conversation(title="New Chat", is_current=True)
                )

    def get_current_conversation(self):
        with self.SessionLocal.begin() as session:
            conversation = session.scalars(
                select(Conversation).where(
                    Conversation.is_current.is_(True)
                )
            ).first()

            if conversation is None:
                conversation = Conversation(
                    title="New Chat",
                    is_current=True,
                )
                session.add(conversation)
                session.flush()

            return {
                "id": conversation.id,
                "title": conversation.title,
            }

    def create_conversation(self):
        with self.SessionLocal.begin() as session:
            session.execute(
                text("UPDATE conversations SET is_current = 0")
            )

            conversation = Conversation(
                title="New Chat",
                is_current=True,
            )
            session.add(conversation)
            session.flush()

            return {
                "id": conversation.id,
                "title": conversation.title,
            }

    def set_current_conversation(self, conversation_id):
        with self.SessionLocal.begin() as session:
            conversation = session.get(
                Conversation, conversation_id
            )

            if conversation is None:
                raise ValueError("Conversation not found.")

            session.execute(
                text("UPDATE conversations SET is_current = 0")
            )
            conversation.is_current = True

    def get_conversations(self):
        with self.SessionLocal() as session:
            conversations = session.scalars(
                select(Conversation).order_by(
                    Conversation.id.desc()
                )
            ).all()

            results = []

            for conversation in conversations:
                first_message = session.scalars(
                    select(ChatMessage)
                    .where(
                        ChatMessage.conversation_id == conversation.id,
                        ChatMessage.role == "user",
                    )
                    .order_by(ChatMessage.id)
                    .limit(1)
                ).first()

                title = conversation.title
                if title == "New Chat" and first_message:
                    title = first_message.content[:45]

                results.append(
                    {
                        "id": conversation.id,
                        "title": title,
                        "is_current": conversation.is_current,
                    }
                )

            return results

    def add_message(self, role, content, conversation_id=None):
        if role not in self.ALLOWED_ROLES:
            raise ValueError(
                "Role must be 'user' or 'assistant'."
            )

        if not isinstance(content, str) or not content.strip():
            raise ValueError("Message content cannot be empty.")

        content = content.strip()

        with self.SessionLocal.begin() as session:
            if conversation_id is None:
                conversation = session.scalars(
                    select(Conversation).where(
                        Conversation.is_current.is_(True)
                    )
                ).first()

                if conversation is None:
                    conversation = Conversation(
                        title="New Chat",
                        is_current=True,
                    )
                    session.add(conversation)
                    session.flush()

                conversation_id = conversation.id

            conversation = session.get(
                Conversation, conversation_id
            )

            if conversation is None:
                raise ValueError("Conversation not found.")

            if role == "user" and conversation.title == "New Chat":
                conversation.title = content[:45]

            message = ChatMessage(
                role=role,
                content=content,
                conversation_id=conversation_id,
            )
            session.add(message)
            session.flush()

            return message.id

    def get_messages(self, conversation_id=None, limit=None):
        if limit is not None and limit < 1:
            raise ValueError("Limit must be positive.")

        if conversation_id is None:
            conversation_id = self.get_current_conversation()["id"]

        statement = select(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id
        )

        if limit is not None:
            statement = (
                statement.order_by(ChatMessage.id.desc())
                .limit(limit)
            )
            reverse_results = True
        else:
            reverse_results = False

        statement = statement.order_by(ChatMessage.id.asc())

        with self.SessionLocal() as session:
            messages = session.scalars(statement).all()

            if reverse_results:
                messages = list(reversed(messages))

            return [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                }
                for message in messages
            ]

    def clear_history(self):
        """Delete all messages, retaining conversation records."""
        with self.SessionLocal.begin() as session:
            return session.query(ChatMessage).delete()

    def close(self):
        self.engine.dispose()
