"""Read-only Git comparisons within a FileStore's startup boundary."""

import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import time
from contextvars import ContextVar
from functools import wraps
from pathlib import Path, PurePosixPath

from .files import TEXT_LIMIT, FileError, decode_text

GIT_TIMEOUT = 10
OUTPUT_LIMIT = 16 * 1024 * 1024
ENTRY_LIMIT = 10_000
CHANGED = "Repository changed. Refresh the diff"
UNSUPPORTED_PLATFORM = "Git diff is not supported on this platform"


def digest(data):
    return hashlib.sha256(data).hexdigest()


class GitFailure(FileError):
    pass


def pinned_operation(method):
    """Keep directory descriptors private to one request/thread context."""

    @wraps(method)
    def pinned(self, *args, **kwargs):
        if self._directories.get() is not None:
            return method(self, *args, **kwargs)
        directories = {}
        token = self._directories.set(directories)
        try:
            result = method(self, *args, **kwargs)
            for path, fd in directories.items():
                self._validate_directory(path, fd)
            return result
        finally:
            self._directories.reset(token)
            for fd in directories.values():
                os.close(fd)

    return pinned


class GitStore:
    def __init__(self, store):
        self.store = store
        info = store.root.stat()
        self._startup_identity = (info.st_dev, info.st_ino)
        self._directories = ContextVar("git_directories", default=None)

    def _open_directory(self, folder):
        """Walk from / without following links, including startup-root ancestors."""
        if (
            os.name != "posix"
            or not all(
                hasattr(os, flag)
                for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
            )
            or os.open not in os.supports_dir_fd
            or os.stat not in os.supports_dir_fd
        ):
            raise GitFailure(UNSUPPORTED_PLATFORM, 503)
        folder = Path(folder)
        if ".." in folder.parts or not folder.is_relative_to(self.store.root):
            raise FileError("Paths must stay inside the root folder.", 403)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open("/", flags)
        current = Path("/")
        try:
            if self.store.root == current:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != self._startup_identity:
                    raise FileError(CHANGED, 409)
            for part in folder.parts[1:]:
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
                current /= part
                if current == self.store.root:
                    info = os.fstat(fd)
                    if (info.st_dev, info.st_ino) != self._startup_identity:
                        raise FileError(CHANGED, 409)
            return fd
        except OSError:
            os.close(fd)
            raise FileError(CHANGED, 409)
        except BaseException:
            os.close(fd)
            raise

    def _validate_directory(self, folder, expected):
        actual = self._open_directory(folder)
        try:
            before, after = os.fstat(expected), os.fstat(actual)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise FileError(CHANGED, 409)
        finally:
            os.close(actual)

    def _pin_directory(self, folder):
        folder = Path(folder)
        directories = self._directories.get()
        if folder not in directories:
            directories[folder] = self._open_directory(folder)
        fd = directories[folder]
        self._validate_directory(folder, fd)
        return fd

    @pinned_operation
    def _run(self, folder, *args, limit=OUTPUT_LIMIT):
        fd = self._pin_directory(folder)
        result = self._execute(fd, *args, limit=limit)
        self._validate_directory(folder, fd)
        return result

    @staticmethod
    def _fd_cwd(fd):
        expected = os.fstat(fd)
        for prefix in ("/proc/self/fd", "/dev/fd"):
            path = f"{prefix}/{fd}"
            try:
                actual = os.stat(path)
            except OSError:
                continue
            if stat.S_ISDIR(actual.st_mode) and (actual.st_dev, actual.st_ino) == (
                expected.st_dev,
                expected.st_ino,
            ):
                return path
        raise GitFailure(UNSUPPORTED_PLATFORM, 503)

    def _execute(self, fd, *args, limit=OUTPUT_LIMIT):
        # Never inherit repository, object, config-injection, or tracing overrides.
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_OPTIONAL_LOCKS="0",
            GIT_LITERAL_PATHSPECS="1",
            GIT_NO_LAZY_FETCH="1",
            GIT_ALLOW_PROTOCOL="",
            GIT_PROTOCOL_FROM_USER="0",
            GIT_PAGER="cat",
            LC_ALL="C",
        )
        command = [
            "git",
            "--no-pager",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "submodule.recurse=false",
            *args,
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self._fd_cwd(fd),
                pass_fds=(fd,),
                start_new_session=True,
            )
        except FileNotFoundError:
            raise GitFailure("Git is not installed", 503)
        output, errors = bytearray(), bytearray()
        deadline = time.monotonic() + GIT_TIMEOUT
        try:
            with selectors.DefaultSelector() as selector:
                for stream, buffer in (
                    (process.stdout, output),
                    (process.stderr, errors),
                ):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, buffer)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise GitFailure("Git read timed out. Try again.", 503)
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        bound = limit if key.data is output else 65536
                        if len(key.data) + len(chunk) > bound:
                            raise GitFailure("Git output exceeds the read limit.", 413)
                        key.data.extend(chunk)
                try:
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    raise GitFailure("Git read timed out. Try again.", 503)
            if process.returncode:
                # Do not return stderr: it can disclose paths outside the workspace.
                message = "Cannot read this repository"
                if b"not a git repository" in errors:
                    message = "Not in a Git repository"
                raise GitFailure(message, 400)
            return bytes(output)
        finally:
            # Kill the group even if Git exited but a helper holds a pipe open.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
            process.stderr.close()

    @pinned_operation
    def _discover(self, relative):
        folder = self.store.path(relative)
        if not folder.is_dir():
            if not folder.exists():
                raise FileNotFoundError()
            raise FileError("This is not a folder.")
        try:
            inside = self._run(folder, "rev-parse", "--is-inside-work-tree").strip()
            if inside != b"true":
                return None, "Not in a Git repository"
            raw = self._run(folder, "rev-parse", "--show-toplevel")
            root = Path(os.fsdecode(raw.removesuffix(b"\n"))).resolve()
            if not root.is_relative_to(self.store.root):
                return None, "Repository is outside the workspace"
            # Apply the same no-symlink rule as ordinary filesystem operations.
            self.store.path(root.relative_to(self.store.root).as_posix())
            self._pin_directory(root)
            return root, None
        except GitFailure as error:
            return None, str(error)

    @pinned_operation
    def context(self, path=""):
        root, reason = self._discover(path)
        return {
            "available": root is not None,
            "reason": reason,
            "repo_path": self._relative(root) if root else None,
        }

    def _relative(self, path):
        relative = path.relative_to(self.store.root).as_posix()
        return "" if relative == "." else relative

    def _root(self, path):
        root, reason = self._discover(path)
        if root is None:
            raise FileError(reason, 503 if reason == "Git is not installed" else 400)
        return root

    @staticmethod
    def _name(raw):
        try:
            name = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise FileError("Git filenames must use UTF-8.", 415)
        if not name or "\x00" in name:
            raise FileError("Invalid Git filename.")
        return name

    @pinned_operation
    def _disk(self, root, name, *, content=False):
        """Read regular files through no-follow directory descriptors, never links."""
        relative = self._relative(root / name)
        parts = PurePosixPath(name).parts
        if not parts or ".." in parts or PurePosixPath(name).is_absolute():
            raise FileError("Paths must stay inside the root folder.", 403)
        # FileStore validates encoding and path rules. Links are allowed only as
        # metadata entries; descriptor traversal below never follows them.
        try:
            self.store.path(relative)
        except FileError as error:
            if str(error) != "Symbolic links are not followed.":
                raise
        parent = os.dup(self._pin_directory(root))
        try:
            for part in parts[:-1]:
                try:
                    child = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent,
                    )
                except FileNotFoundError:
                    return {"kind": "missing"}, None
                except OSError:
                    return {"kind": "unavailable"}, None
                os.close(parent)
                parent = child
            try:
                info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return {"kind": "missing"}, None
            kind = "regular" if stat.S_ISREG(info.st_mode) else "special"
            if stat.S_ISLNK(info.st_mode):
                kind = "symlink"
            metadata = {
                "kind": kind,
                "stat": [
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                ],
            }
            if not content or kind != "regular" or info.st_size > TEXT_LIMIT:
                return metadata, None
            try:
                fd = os.open(
                    parts[-1],
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent,
                )
            except OSError:
                raise FileError(CHANGED, 409)
            with os.fdopen(fd, "rb") as source:
                opened = os.fstat(source.fileno())
                if not stat.S_ISREG(opened.st_mode) or (
                    opened.st_dev,
                    opened.st_ino,
                ) != (info.st_dev, info.st_ino):
                    raise FileError(CHANGED, 409)
                data = source.read(TEXT_LIMIT + 1)
                after = os.fstat(source.fileno())
            if len(data) > TEXT_LIMIT or (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) != (info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                raise FileError(CHANGED, 409)
            metadata["hash"] = digest(data)
            return metadata, data
        finally:
            os.close(parent)

    @pinned_operation
    def _state(self, root, comparison):
        if comparison not in {"all", "unstaged", "staged"}:
            raise FileError("Choose all, unstaged, or staged changes.")
        # --verify --quiet also distinguishes an unborn branch without parsing stderr.
        head = (
            self._run(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
            if self._has_head(root)
            else None
        )
        # hash-object does not write an object. Git recognizes its empty tree for
        # both SHA-1 and SHA-256 repositories, including before the first commit.
        base = (
            head.strip().decode("ascii")
            if head
            else self._run(root, "hash-object", "-t", "tree", "--stdin")
            .strip()
            .decode("ascii")
        )
        index = self._run(root, "ls-files", "--stage", "-z")
        conflicts = set()
        for item in index.split(b"\0"):
            if item:
                fields, name = item.split(b"\t", 1)
                if fields.split()[2] != b"0":
                    conflicts.add(self._name(name))
        # Diff can invoke clean/process filters even with --no-ext-diff and
        # --no-textconv. Override every configured driver before reading disk.
        config_keys = self._run(root, "config", "--null", "--name-only", "--list")
        drivers = set()
        for key in config_keys.split(b"\0"):
            if key.startswith(b"filter.") and key.rsplit(b".", 1)[-1] in {
                b"clean",
                b"process",
                b"required",
            }:
                drivers.add(os.fsdecode(key.rsplit(b".", 1)[0]))
        args = []
        for driver in sorted(drivers):
            for setting, value in (
                ("clean", ""),
                ("process", ""),
                ("required", "false"),
            ):
                args += ["-c", f"{driver}.{setting}={value}"]
        args += [
            "diff",
            "--raw",
            "-z",
            "--no-abbrev",
            "--no-ext-diff",
            "--no-textconv",
            "--find-renames",
            "--ignore-submodules=none",
        ]
        if comparison == "staged":
            args += ["--cached", base]
        elif comparison == "all":
            args += [base]
        raw = self._run(root, *args, "--")
        parts = raw.split(b"\0")
        entries = []
        i = 0
        while i < len(parts) and parts[i]:
            header = parts[i].split()
            if len(header) != 5 or not header[0].startswith(b":"):
                raise FileError("Cannot parse Git changes.")
            old_mode, new_mode, old_oid, new_oid, status = header
            code = status[:1].decode("ascii")
            old_path = new_path = self._name(parts[i + 1])
            i += 2
            if code in {"R", "C"}:
                new_path = self._name(parts[i])
                i += 1
            entries.append(
                {
                    "status": "unmerged"
                    if new_path in conflicts or code == "U"
                    else {
                        "A": "added",
                        "D": "deleted",
                        "M": "modified",
                        "R": "renamed",
                        "C": "copied",
                        "T": "type_changed",
                    }.get(code, "modified"),
                    "old_path": None if code == "A" else old_path,
                    "new_path": None if code == "D" else new_path,
                    "old_mode": old_mode[1:].decode("ascii"),
                    "new_mode": new_mode.decode("ascii"),
                    "_old_oid": old_oid.decode("ascii"),
                    "_new_oid": new_oid.decode("ascii"),
                }
            )
            if len(entries) > ENTRY_LIMIT:
                raise FileError("Too many changed files. The limit is 10000.", 413)
        if comparison != "staged":
            for name in self._run(
                root, "ls-files", "--others", "--exclude-standard", "-z", "--"
            ).split(b"\0"):
                if name:
                    entries.append(
                        {
                            "status": "untracked",
                            "old_path": None,
                            "new_path": self._name(name),
                            "old_mode": "000000",
                            "new_mode": "100644",
                            "_old_oid": "",
                            "_new_oid": "",
                        }
                    )
                    if len(entries) > ENTRY_LIMIT:
                        raise FileError(
                            "Too many changed files. The limit is 10000.", 413
                        )
        if len(entries) > ENTRY_LIMIT:
            raise FileError("Too many changed files. The limit is 10000.", 413)
        # Raw diff can emit multiple records for an unmerged path.
        unique = {}
        for entry in entries:
            key = entry["new_path"] or entry["old_path"]
            if key in conflicts:
                entry["status"] = "unmerged"
            unique[key] = entry
        entries = [unique[key] for key in sorted(unique)]
        disk = []
        for entry in entries:
            if comparison != "staged":
                state, _ = self._disk(root, entry["new_path"] or entry["old_path"])
                disk.append(state)
                if entry["new_mode"] == "160000" and state["kind"] == "special":
                    submodule_path = self._relative(root / entry["new_path"])
                    submodule, _ = self._discover(submodule_path)
                    if submodule == root / entry["new_path"] and self._has_head(
                        submodule
                    ):
                        entry["_new_oid"] = (
                            self._run(submodule, "rev-parse", "--verify", "HEAD")
                            .strip()
                            .decode("ascii")
                        )
                if entry["status"] == "untracked":
                    if state["kind"] == "symlink":
                        entry["new_mode"] = "120000"
                    elif state["kind"] == "regular":
                        entry["new_mode"] = (
                            "100755" if state["stat"][2] & 0o111 else "100644"
                        )
            public = {
                key: value for key, value in entry.items() if not key.startswith("_")
            }
            entry["id"] = digest(json.dumps(public, sort_keys=True).encode())
        token = (
            digest(head or b"")
            + digest(index)
            + digest(raw)
            + digest(
                json.dumps(
                    [
                        str(root),
                        self._directory_identity(root),
                        comparison,
                        entries,
                        disk,
                    ],
                    sort_keys=True,
                ).encode()
            )
        )
        return entries, digest(token.encode())

    def _directory_identity(self, root):
        info = os.fstat(self._pin_directory(root))
        return [info.st_dev, info.st_ino]

    def _has_head(self, root):
        # --revs-only returns no revision for an unborn HEAD; detached HEAD works too.
        refs = self._run(root, "rev-parse", "--revs-only", "HEAD")
        return bool(refs.strip())

    @pinned_operation
    def changes(self, path="", comparison="all"):
        root = self._root(path)
        entries, snapshot = self._state(root, comparison)
        return {
            "repo_path": self._relative(root),
            "comparison": comparison,
            "snapshot": snapshot,
            "entries": [
                {key: value for key, value in entry.items() if not key.startswith("_")}
                for entry in entries
            ],
        }

    @pinned_operation
    def _blob(self, root, oid):
        if not oid or not oid.strip("0"):
            return b"", 0
        if len(oid) not in {40, 64} or any(c not in "0123456789abcdef" for c in oid):
            raise FileError("Invalid Git object identifier.")
        size = int(self._run(root, "cat-file", "-s", oid, limit=128).strip())
        if size > TEXT_LIMIT:
            return None, size
        return self._run(root, "cat-file", "blob", oid, limit=TEXT_LIMIT), size

    @staticmethod
    def _source(label, data, size):
        source = {
            "label": label,
            "content": None,
            "byte_size": size,
            "hash": None,
            "bom": None,
            "line_endings": None,
            "final_newline": None,
        }
        if data is None:
            return source, "File is too large for text diff"
        source["hash"] = digest(data)
        try:
            text = decode_text(data)
        except FileError:
            return source, "Text diff is not available for this file"
        crlf = text.count("\r\n")
        source.update(
            content=text,
            bom=data.startswith(b"\xef\xbb\xbf"),
            line_endings={
                "crlf": crlf,
                "lf": text.count("\n") - crlf,
                "cr": text.count("\r") - crlf,
            },
            final_newline=text.endswith(("\r", "\n")),
        )
        return source, None

    @pinned_operation
    def file(self, path="", comparison="all", entry=None, snapshot=None):
        root = self._root(path)
        entries, current = self._state(root, comparison)
        if not snapshot or current != snapshot:
            raise FileError(CHANGED, 409)
        selected = next((item for item in entries if item["id"] == entry), None)
        if selected is None:
            raise FileError("Choose an entry from the change list.", 404)
        old_mode, new_mode = selected["old_mode"], selected["new_mode"]
        reason = None
        modes = {old_mode, new_mode}
        if selected["status"] == "unmerged":
            reason = "Unmerged file"
        elif "120000" in modes:
            reason = "Symbolic link"
        elif "160000" in modes:
            reason = "Submodule"
        old_label = "Index" if comparison == "unstaged" else "HEAD"
        new_label = "Index" if comparison == "staged" else "Working tree"
        old = new = None
        working_path = None
        version = None
        if reason is None:
            old_data, old_size = self._blob(root, selected["_old_oid"])
            if comparison == "staged":
                new_data, new_size = self._blob(root, selected["_new_oid"])
            elif selected["new_path"] is None:
                new_data, new_size = b"", 0
            else:
                disk, new_data = self._disk(root, selected["new_path"], content=True)
                new_size = disk.get("stat", [0, 0, 0, 0])[3]
                if disk["kind"] == "missing":
                    reason = "File content is not available on disk"
                elif disk["kind"] != "regular":
                    reason = "Text diff is not available for this file"
                working_path = self._relative(root / selected["new_path"])
                version = disk.get("hash")
            old, old_reason = self._source(old_label, old_data, old_size)
            new, new_reason = self._source(new_label, new_data, new_size)
            reason = reason or old_reason or new_reason
        if self._root(path) != root or self._state(root, comparison)[1] != snapshot:
            raise FileError(CHANGED, 409)
        notices = []
        if old and new and not reason:
            normalized = lambda value: value.replace("\r\n", "\n").replace("\r", "\n")
            if normalized(old["content"]) == normalized(new["content"]):
                if old["content"] != new["content"]:
                    notices.append("Line endings differ")
                if old["bom"] != new["bom"]:
                    notices.append("UTF-8 BOM differs")
                if not notices:
                    notices.append("No text changes")
        return {
            "repo_path": self._relative(root),
            "comparison": comparison,
            "snapshot": snapshot,
            "entry": {
                key: value for key, value in selected.items() if not key.startswith("_")
            },
            "old": old,
            "new": new,
            "reason": reason,
            "notices": notices,
            "editable": not reason
            and comparison != "staged"
            and working_path is not None,
            "working_path": working_path,
            "version": version,
            "old_oid": selected["_old_oid"] or None,
            "new_oid": selected["_new_oid"] or None,
        }
