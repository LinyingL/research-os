"""Create a fresh research workspace without importing an existing timeline.

The parent folder is a location, not a project to adopt. Creation always owns
a new child directory; an existing file, directory, or link is never reused.
"""

from __future__ import annotations

import json
import os
import stat
import unicodedata
from pathlib import Path
from typing import Any

from researchos import addproject, paths
from researchos import project as project_identity
from researchos.records import files
from researchos.records.lock import project_lock
from researchos.webapp import registry


class NewProjectError(Exception):
    """A creation refusal, with an English detail and a translated app message."""

    def __init__(
        self, code: str, message: str, status: int = 400, message_zh: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message_zh = message_zh or message


def _invalid(message: str, message_zh: str) -> NewProjectError:
    return NewProjectError("invalid", message, message_zh=message_zh)


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid("Enter a project name.", "请填写项目名称。")
    name = value.strip()
    if (
        not name or len(name) > registry.LABEL_MAX_LENGTH
        or len(name.encode("utf-8", errors="surrogatepass")) > 240
        or name in {".", ".."}
        or name.casefold() in {paths.PROJECT_DIRNAME, paths.LEGACY_PROJECT_DIRNAME, ".git"}
        or "/" in name or "\\" in name
        or any(unicodedata.category(ch).startswith("C") or ch in "\u2028\u2029" for ch in value)
    ):
        raise _invalid(
            "Use a project name of 1–100 characters without path separators or control characters.",
            "项目名称请使用 1–100 个字符，不要包含路径分隔符或控制字符。",
        )
    return name


def _parent(value: Any) -> tuple[Path, Path, tuple[int, int]]:
    if not isinstance(value, str) or not value.strip():
        raise _invalid("Choose where to save the project.", "请选择项目保存位置。")
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise _invalid("The save location contains an invalid character.", "保存位置中包含无效字符。")
    try:
        chosen = Path(value).expanduser()
        if not chosen.is_absolute() or ".." in chosen.parts:
            raise _invalid("Choose an absolute save location.", "请选择完整的保存路径。")
        if any(part.casefold() in {paths.PROJECT_DIRNAME, paths.LEGACY_PROJECT_DIRNAME, ".git"} for part in chosen.parts):
            raise _invalid("Choose a folder outside project internals.", "请选择项目内部配置目录以外的位置。")
        parent = chosen.resolve(strict=True)
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise _invalid("The save location is not a folder.", "保存位置不是文件夹。")
        if any(part.casefold() in {paths.PROJECT_DIRNAME, paths.LEGACY_PROJECT_DIRNAME, ".git"} for part in parent.parts):
            raise _invalid("Choose a folder outside project internals.", "请选择项目内部配置目录以外的位置。")
        # Reading the listing also gives macOS a chance to ask for permission.
        os.listdir(parent)
    except NewProjectError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise NewProjectError(
            "unreadable", f"The save location cannot be read: {exc}",
            message_zh="无法读取保存位置，请检查路径和文件夹访问权限。",
        ) from exc
    return chosen, parent, (info.st_dev, info.st_ino)


def _check_location(root: Path) -> None:
    if os.path.lexists(root):
        raise NewProjectError(
            "exists", f"A file or folder named {root.name!r} already exists. Choose another name.", 409,
            f"「{root.name}」已经存在，请使用其他名称。",
        )
    if str(root.parent) in {root.anchor, "/Users", "/home", "/private", "/private/var", "/Volumes"}:
        raise NewProjectError(
            "top_level", "Choose a personal save folder rather than a system or volume container.",
            message_zh="请选择自己的保存文件夹，不要选择系统根目录或磁盘总目录。",
        )
    # The target does not exist yet. Compare known protected locations without
    # opening every Desktop/Downloads/cloud root merely to get its on-disk case:
    # that can unexpectedly wait for unrelated macOS permission prompts.
    home = Path.home().resolve()
    mobile = home / "Library/Mobile Documents"
    cloud_root = home / "Library/CloudStorage"
    top = {home, *(home / part for part in ("Documents", "Desktop", "Downloads", "Library")),
           mobile, mobile / "com~apple~CloudDocs", cloud_root}
    if root in top or root.parent == cloud_root or root.parent == Path("/Volumes"):
        raise NewProjectError("top_level", "Choose a specific project folder.",
                              message_zh=addproject.TOP_LEVEL_MESSAGE)
    for system in addproject._SYSTEM_FOLDERS:
        if root.is_relative_to(Path(system).resolve()):
            raise NewProjectError("system_folder", f"The save location is inside {system}.",
                                  message_zh=addproject.TOP_LEVEL_MESSAGE)
    engine_homes = [paths.researchos_home()]
    if paths.home_override() is None:
        engine_homes.append(paths.legacy_default_home())
    for engine_home in engine_homes:
        engine_home = engine_home.resolve()
        if root.is_relative_to(engine_home) or engine_home.is_relative_to(root):
            raise NewProjectError("researchos_home", "The save location overlaps Research OS's own data folder.",
                                  message_zh=addproject.REFUSAL_MESSAGES["researchos_home"])
    # Detect projects even when they have never been registered on this Mac.
    # A user's ~/.researchos is also an ancestor's .researchos, but it has no
    # project.toml/attempts.toml/graph.db; its name alone is not a project marker.
    for ancestor in root.parents:
        for dirname in (paths.PROJECT_DIRNAME, paths.LEGACY_PROJECT_DIRNAME):
            record = ancestor / dirname
            if ancestor == home:
                continue  # ~/.researchos (even a link) is the engine home, not a project.
            if record.is_symlink() or any(
                os.path.lexists(record / name)
                for name in ("project.toml", "attempts.toml", "graph.db", "judgements.toml")
            ):
                raise NewProjectError(
                    "inside_project", f"The save location is inside the project {ancestor}.",
                    message_zh=f"保存位置在项目「{ancestor.name}」里面，请选择项目以外的位置。",
                )
    # Legacy projects can have only an index outside their source folder.
    # A failed registry read must not be mistaken for an empty registry.
    for entry in registry._read_entries():
        folder = Path(entry["path"])
        if not folder.is_dir():
            continue
        folder = folder.resolve()
        if root.is_relative_to(folder):
            raise NewProjectError(
                "inside_project", f"The save location is inside the project {folder}.",
                message_zh=f"保存位置在项目「{entry['label']}」里面，请选择项目以外的位置。",
            )
        if folder.is_relative_to(root):
            raise NewProjectError(
                "contains_project", f"The new folder would contain the project {folder}.",
                message_zh=f"这个位置里已经有项目「{entry['label']}」，请选择其他位置。",
            )


def _same_directory(path: Path, pin: tuple[int, int]) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == pin
    except OSError:
        return False


def _attempts(root: Path, root_fd: int, language: str, guard, *, filename: str = "attempts.md") -> None:
    """An empty, configured timeline lets the first real attempt be appended."""
    research = root / "research"
    guard()
    try:
        os.mkdir("research", dir_fd=root_fd)
    except FileExistsError:
        pass
    if research.is_symlink() or not research.is_dir():
        raise NewProjectError("location_changed", "The research folder changed during setup.", 409,
                              "研究目录在创建过程中发生了变化，请重新选择保存位置。")
    if language == "en":
        heading = "Research attempts"
        columns = {"id": "#", "date": "Date", "description": "Description", "variables": "Variables",
                   "result": "Result", "verdict": "Status"}
    else:
        heading = "研究尝试"
        columns = {"id": "#", "date": "日期", "description": "尝试内容", "variables": "变量",
                   "result": "结果", "verdict": "状态"}
    content = f"# {heading}\n\n| " + " | ".join(columns.values()) + " |\n"
    content += "| " + " | ".join("---" for _ in columns) + " |\n"
    config = f'file = "research/{filename}"\nheading = {json.dumps(heading, ensure_ascii=False)}\n\n[columns]\n'
    config += "".join(f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in columns.items())
    for dirname, filename, text in (("research", filename, content),
                                    (paths.project_dir(root).name, "attempts.toml", config)):
        guard()
        # Unlike a replacement, this cannot overwrite a file added by another
        # process after the directory was created. Directory descriptors keep a
        # simultaneous symlink swap from redirecting this write elsewhere.
        directory_fd = os.open(dirname, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o644, dir_fd=directory_fd)
            try:
                data = memoryview(text.encode("utf-8"))
                while data:
                    data = data[os.write(fd, data):]
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.fsync(directory_fd)
            except OSError:
                pass  # Some file systems do not support directory fsync.
        finally:
            os.close(directory_fd)


def create(body: dict[str, Any]) -> dict[str, str]:
    """Create ``parent/name`` and return its registered identity and location.

    Failures after mkdir preserve that directory and identify it in the error;
    setup never rolls back by recursively deleting potentially user-added files.
    """
    if not isinstance(body, dict):
        raise _invalid("Project details must be an object.", "项目资料格式不正确。")
    name = _name(body.get("name"))
    language = body.get("language", "zh")
    if not isinstance(language, str) or language not in {"zh", "en"}:
        raise _invalid("Choose Chinese or English for the project.", "请选择中文或英文。")
    chosen, parent, parent_pin = _parent(body.get("parent"))
    root = parent / name
    created = False
    root_pin: tuple[int, int] | None = None
    parent_fd: int | None = None
    root_fd: int | None = None

    def guard() -> None:
        try:
            same = chosen.resolve(strict=True) == parent
            if parent_fd is not None:
                info = os.fstat(parent_fd)
                same = same and (info.st_dev, info.st_ino) == parent_pin
            same = same and _same_directory(parent, parent_pin)
            if root_pin is not None:
                same = same and _same_directory(root, root_pin) and root.resolve(strict=True) == root
                same = same and not any((root / p).is_symlink() for p in (paths.PROJECT_DIRNAME, "research"))
        except (OSError, RuntimeError, ValueError):
            same = False
        if not same:
            raise NewProjectError(
                "location_changed", "The save location changed during setup. No files will be written through it.",
                409, "保存位置在创建过程中发生了变化，请重新选择位置。",
            )

    try:
        _check_location(root)
        parent_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with project_lock(root, None):
            guard()
            _check_location(root)
            try:
                os.mkdir(name, dir_fd=parent_fd)
            except FileExistsError as exc:
                raise NewProjectError(
                    "exists", f"A file or folder named {name!r} already exists. Choose another name.", 409,
                    f"「{name}」已经存在，请使用其他名称。",
                ) from exc
            created = True
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            root_pin = (info.st_dev, info.st_ino)
            guard()
            root_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            initialized = project_identity.init_project(root, before_write=guard)
            guard()
            from researchos import workspace  # available to the existing-project UI as well

            workspace.initialize(root, language=language)
            guard()
            _attempts(root, root_fd, language, guard)
            guard()
            registry.register(root, initialized.identity.id, label=name)
            if not any(entry.get("id") == initialized.identity.id and entry["path"] == str(root)
                       for entry in registry._read_entries()):
                raise OSError("The project could not be added to the project list.")
            files._fsync_dir(parent)
            return {"path": str(root), "id": initialized.identity.id, "label": name}
    except NewProjectError as exc:
        if not created:
            raise
        raise NewProjectError(
            "incomplete", f"Project setup stopped at {root}: {exc} Files already created were kept; reopen this folder to recover.",
            exc.status, f"项目「{root}」未完成创建：{exc.message_zh} 已生成的文件已保留，可通过“打开已有项目”继续查看。",
        ) from exc
    except Exception as exc:
        if created:
            raise NewProjectError(
                "incomplete", f"Project setup stopped at {root}: {exc} Files already created were kept; reopen this folder to recover.",
                500, f"项目「{root}」未完成创建，已生成的文件已保留。可通过“打开已有项目”继续查看。详情：{exc}",
            ) from exc
        raise NewProjectError(
            "unwritable", f"The project could not be created: {exc}", 400,
            "无法在这个位置创建项目，请检查文件夹访问权限。",
        ) from exc
    finally:
        if root_fd is not None:
            os.close(root_fd)
        if parent_fd is not None:
            os.close(parent_fd)
