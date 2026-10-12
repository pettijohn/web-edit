"""Filesystem operations, confined to one root directory."""

import hashlib
import mimetypes
import os
import re
import shutil
import stat
import tempfile
import threading
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

IMAGE_LIMIT = 2_000_000
TEXT_LIMIT = 10 * 1024 * 1024
IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
    "image/bmp",
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "image/svg+xml",
}


class FileError(Exception):
    def __init__(self, message, status=400, **details):
        super().__init__(message)
        self.status = status
        self.details = details


def version(path):
    # Content hashes ignore metadata-only changes from sync tools like Dropbox.
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def delete_version(info):
    """Identify the item shown in a listing without reading file contents."""
    value = f"{info.st_dev}:{info.st_ino}:{info.st_mtime_ns}:{info.st_size}:{stat.S_IFMT(info.st_mode)}"
    return hashlib.sha256(value.encode()).hexdigest()


def decode_text(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileError(
            "This file is not UTF-8 text. You can download it instead.", 415
        )
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        raise FileError(
            "This looks like a binary file. You can download it instead.", 415
        )
    return text


def preserve_endings(original, edited):
    """Keep the endings of unchanged lines, including files with mixed endings."""
    old_lines = re.split(r"\r\n|\r|\n", original)
    old_endings = re.findall(r"\r\n|\r|\n", original)
    new_lines = re.split(r"\r\n|\r|\n", edited)
    default = Counter(old_endings).most_common(1)[0][0] if old_endings else "\n"
    endings = [default] * (len(new_lines) - 1)
    # SequenceMatcher's autojunk heuristic handles repetitive documents.
    for match in SequenceMatcher(None, old_lines, new_lines).get_matching_blocks():
        for offset in range(match.size):
            old_index, new_index = match.a + offset, match.b + offset
            if old_index < len(old_endings) and new_index < len(endings):
                endings[new_index] = old_endings[old_index]
    return "".join(
        line + (endings[i] if i < len(endings) else "")
        for i, line in enumerate(new_lines)
    )


class FileStore:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("The root must be a directory")
        self.lock = threading.RLock()

    def path(self, relative, *, file=False):
        if not isinstance(relative, str) or "\x00" in relative or "\\" in relative:
            raise FileError("Invalid path.")
        try:
            relative.encode("utf-8")
        except UnicodeEncodeError:
            raise FileError("Invalid path.")
        parts = PurePosixPath(relative)
        if parts.is_absolute() or ".." in parts.parts:
            raise FileError("Paths must stay inside the root folder.", 403)
        if file and (not parts.parts or relative.endswith("/")):
            raise FileError("Choose a filename.")
        result = self.root
        for part in parts.parts:
            result = result / part
            if result.is_symlink():
                raise FileError("Symbolic links are not followed.", 403)
        if not result.resolve().is_relative_to(self.root):
            raise FileError("Paths must stay inside the root folder.", 403)
        return result

    def regular_file(self, relative):
        path = self.path(relative, file=True)
        if not stat.S_ISREG(path.stat().st_mode):
            raise FileError("This is not a regular file.")
        return path

    def image_type(self, path):
        mime = mimetypes.guess_type(path.name)[0]
        return mime if mime in IMAGE_TYPES else None

    def browse(self, relative):
        folder = self.path(relative)
        if not folder.is_dir():
            if not folder.exists():
                raise FileNotFoundError()
            raise FileError("This is not a folder.")
        entries = []
        for child in folder.iterdir():
            if child.name.startswith(".web-edit-"):
                continue
            try:
                info = child.lstat()
            except FileNotFoundError:
                continue
            kind = "directory" if stat.S_ISDIR(info.st_mode) else "file"
            if stat.S_ISLNK(info.st_mode):
                kind = "symlink"
            elif not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                kind = "special"
            entries.append(
                {
                    "name": child.name,
                    "path": child.relative_to(self.root).as_posix(),
                    "kind": kind,
                    "size": info.st_size,
                    "modified": info.st_mtime,
                    "delete_version": delete_version(info),
                    "preview": kind == "file"
                    and info.st_size < IMAGE_LIMIT
                    and bool(self.image_type(child)),
                }
            )
        entries.sort(
            key=lambda item: (
                item["kind"] != "directory",
                item["name"].casefold(),
                item["name"],
            )
        )
        return {
            "path": ""
            if folder == self.root
            else folder.relative_to(self.root).as_posix(),
            "root_name": self.root.name or "/",
            "entries": entries,
        }

    def delete(self, relative, kind, expected):
        with self.lock:
            target = self.path(relative)
            if target == self.root:
                raise FileError("The root folder cannot be deleted.", 403)
            if kind not in {"file", "directory"} or not expected:
                raise FileError(
                    "Provide the item kind and version from the folder listing."
                )
            info = target.lstat()
            actual_kind = "directory" if stat.S_ISDIR(info.st_mode) else "file"
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise FileError("Only regular files and folders can be deleted.", 403)
            if kind != actual_kind or expected != delete_version(info):
                raise FileError(
                    "This item changed since the folder was loaded. Refresh before deleting it.",
                    409,
                )
            if kind == "directory":
                # rmtree removes child symlinks themselves, never their targets.
                shutil.rmtree(target)
            else:
                target.unlink()
            return {"path": relative, "deleted": True}

    def text(self, relative):
        with self.lock:
            path = self.regular_file(relative)
            before = version(path)
            if path.stat().st_size > TEXT_LIMIT:
                raise FileError(
                    "Text editing is limited to 10 MiB. You can download this file.",
                    413,
                )
            data = path.read_bytes()
            if version(path) != before:
                raise FileError(
                    "The file changed while opening it. Please try again.", 409
                )
            return {"path": relative, "content": decode_text(data), "version": before}

    def check_destination(self, relative, expected):
        path = self.path(relative, file=True)
        if path.exists():
            if not path.is_file():
                raise FileError(
                    "A folder or special file already exists at that path.", 409
                )
            current = version(path)
            if expected != current:
                raise FileError(
                    "This file already exists or has changed on disk.",
                    409,
                    version=current,
                    exists=True,
                )
        elif expected is not None:
            raise FileError(
                "The file was removed or moved on disk. Save your work to a new path.",
                409,
            )
        return path

    def write(self, relative, source, expected=None):
        with self.lock:
            target = self.check_destination(relative, expected)
            target.parent.mkdir(parents=True, exist_ok=True)
            self.path(relative, file=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    prefix=".web-edit-", dir=target.parent, delete=False
                ) as output:
                    temporary = Path(output.name)
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                self.check_destination(relative, expected)
                if expected is None:
                    # link is an atomic create-if-absent; never silently replace a file.
                    try:
                        os.link(temporary, target)
                    except FileExistsError:
                        raise FileError(
                            "A file appeared at this path during the upload. Please retry.",
                            409,
                        )
                else:
                    os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
                    os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return {"path": relative, "version": version(target)}

    def save_text(self, relative, content, expected):
        from io import BytesIO

        if not isinstance(content, str) or (
            expected is not None and not isinstance(expected, str)
        ):
            raise FileError(
                "Content must be text and version must be a string or null."
            )
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError:
            raise FileError("Content must be valid UTF-8 text.")
        if len(encoded) > TEXT_LIMIT:
            raise FileError("Text editing is limited to 10 MiB.", 413)
        decode_text(encoded)
        with self.lock:
            path = self.check_destination(relative, expected)
            if expected is not None:
                if path.stat().st_size > TEXT_LIMIT:
                    raise FileError("Text editing is limited to 10 MiB.", 413)
                original = path.read_bytes()
                encoded = preserve_endings(decode_text(original), content).encode(
                    "utf-8"
                )
                if original.startswith(b"\xef\xbb\xbf"):
                    encoded = b"\xef\xbb\xbf" + encoded
            return self.write(relative, BytesIO(encoded), expected)
