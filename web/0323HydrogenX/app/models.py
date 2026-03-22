from datetime import datetime

from flask_login import UserMixin

from .extensions import db


task_uploads = db.Table(
    "task_uploads",
    db.Column("task_id", db.Integer, db.ForeignKey("chat_tasks.id"), primary_key=True),
    db.Column("upload_id", db.Integer, db.ForeignKey("uploaded_files.id"), primary_key=True),
)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)

    tasks = db.relationship("ChatTask", back_populates="user", lazy="dynamic")
    artifacts = db.relationship("GeneratedArtifact", back_populates="user", lazy="dynamic")
    uploads = db.relationship("UploadedFile", back_populates="user", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<User {self.username}>"


class ChatTask(db.Model):
    __tablename__ = "chat_tasks"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    agent_id = db.Column(db.String(64), nullable=False)
    session_key = db.Column(db.String(128), nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    response_text = db.Column(db.Text, nullable=True)
    raw_response_json = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="queued", index=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", back_populates="tasks")
    artifacts = db.relationship(
        "GeneratedArtifact",
        back_populates="task",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="GeneratedArtifact.created_at.desc()",
    )
    uploads = db.relationship(
        "UploadedFile",
        secondary=task_uploads,
        lazy="selectin",
        order_by="UploadedFile.created_at.desc()",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_key": self.session_key,
            "prompt": self.prompt,
            "response_text": self.response_text,
            "raw_response_json": self.raw_response_json,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "uploads": [upload.to_dict() for upload in self.uploads],
        }


class GeneratedArtifact(db.Model):
    __tablename__ = "generated_artifacts"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("chat_tasks.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    kind = db.Column(db.String(32), nullable=False, default="file", index=True)
    source_type = db.Column(db.String(32), nullable=False, default="inline")
    filename = db.Column(db.String(255), nullable=False)
    storage_name = db.Column(db.String(255), nullable=False, unique=True)
    relative_path = db.Column(db.String(512), nullable=False, unique=True)
    mime_type = db.Column(db.String(255), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)
    sha256 = db.Column(db.String(64), nullable=True)
    source_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    task = db.relationship("ChatTask", back_populates="artifacts")
    user = db.relationship("User", back_populates="artifacts")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "user_id": self.user_id,
            "kind": self.kind,
            "source_type": self.source_type,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "source_url": self.source_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UploadedFile(db.Model):
    __tablename__ = "uploaded_files"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    storage_name = db.Column(db.String(255), nullable=False, unique=True)
    relative_path = db.Column(db.String(512), nullable=False, unique=True)
    mime_type = db.Column(db.String(255), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)
    sha256 = db.Column(db.String(64), nullable=True)
    text_excerpt = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = db.relationship("User", back_populates="uploads")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
