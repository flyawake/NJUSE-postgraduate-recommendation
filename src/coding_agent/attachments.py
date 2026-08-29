"""Attachment validation and immutable local binary storage."""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

from .artifacts.store import ArtifactStore

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_TURN = 4
MAX_ATTACHMENTS_TOTAL_BYTES = 20 * 1024 * 1024

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".log",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".htm",
    ".sql",
    ".sh",
    ".ps1",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".go",
    ".rs",
}
_ZIP_OFFICE_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_LEGACY_OFFICE_TYPES = {
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
}


class AttachmentValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedAttachment:
    filename: str
    media_type: str
    kind: str


class AttachmentStore(ArtifactStore):
    def __init__(self, root: Path) -> None:
        super().__init__(
            root,
            enable_compression=False,
            max_blob_bytes=MAX_ATTACHMENT_BYTES,
        )


def validate_attachment(
    filename: str, declared_media_type: str | None, data: bytes
) -> ValidatedAttachment:
    if not isinstance(filename, str):
        raise AttachmentValidationError("invalid_attachment", "附件名无效")
    clean = Path(filename.replace("\\", "/")).name.strip()
    if (
        not clean
        or clean in {".", ".."}
        or len(clean) > 200
        or any(ord(char) < 32 for char in clean)
        or not re.search(r"[^.]", clean)
    ):
        raise AttachmentValidationError("invalid_attachment", "附件名无效")
    if not data:
        raise AttachmentValidationError("empty_attachment", "附件内容为空")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentValidationError(
            "attachment_too_large", "单个附件不能超过 10 MiB"
        )
    extension = Path(clean).suffix.lower()
    if extension in _IMAGE_TYPES:
        expected = _IMAGE_TYPES[extension]
        signatures = {
            "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": data.startswith(b"\xff\xd8\xff"),
            "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": len(data) >= 12
            and data.startswith(b"RIFF")
            and data[8:12] == b"WEBP",
        }
        if not signatures[expected]:
            raise AttachmentValidationError(
                "attachment_type_mismatch", "图片内容与扩展名不一致"
            )
        return ValidatedAttachment(clean, expected, "image")
    if extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise AttachmentValidationError(
                "attachment_type_mismatch", "PDF 内容与扩展名不一致"
            )
        return ValidatedAttachment(clean, "application/pdf", "file")
    if extension in _ZIP_OFFICE_TYPES:
        if not data.startswith(b"PK\x03\x04"):
            raise AttachmentValidationError(
                "attachment_type_mismatch", "Office 文件内容与扩展名不一致"
            )
        return ValidatedAttachment(clean, _ZIP_OFFICE_TYPES[extension], "file")
    if extension in _LEGACY_OFFICE_TYPES:
        if not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise AttachmentValidationError(
                "attachment_type_mismatch", "Office 文件内容与扩展名不一致"
            )
        return ValidatedAttachment(clean, _LEGACY_OFFICE_TYPES[extension], "file")
    if extension == ".rtf":
        if not data.startswith(b"{\\rtf"):
            raise AttachmentValidationError(
                "attachment_type_mismatch", "RTF 内容与扩展名不一致"
            )
        return ValidatedAttachment(clean, "application/rtf", "file")
    if extension in _TEXT_EXTENSIONS:
        sample = data[:65536]
        if b"\x00" in sample:
            raise AttachmentValidationError(
                "attachment_type_mismatch", "文本附件包含二进制内容"
            )
        try:
            sample.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AttachmentValidationError(
                "attachment_encoding", "文本附件必须使用 UTF-8 编码"
            ) from exc
        guessed = mimetypes.guess_type(clean)[0]
        media_type = (
            guessed if guessed and guessed.startswith("text/") else "text/plain"
        )
        if extension in {".json", ".jsonl"}:
            media_type = "application/json"
        return ValidatedAttachment(clean, media_type, "file")
    raise AttachmentValidationError(
        "unsupported_attachment_type",
        "仅支持常见图片、PDF、文本/代码、Word、Excel 和 PowerPoint 文件",
    )
