"""Build the macOS `Research OS.app` (task V4 phase 3, DESIGN.md section 8.9), or,
when the Swift toolchain is unavailable, the V3 launcher-script bundle
(task V3 phase 4) it replaces.

THE NATIVE SHELL (`build_app`, the normal path). `researchos app` compiles the
package's own Swift sources (`shell/RCEShell.swift`, `shell/RCEIcon.swift`,
shipped as package-data) with the system `swiftc -O` into a real windowed
app -- Dock icon, menu bar, a WKWebView showing the same `app.html` the
browser shows. Zero third-party dependencies: stdlib Python here, AppKit +
WebKit there, plus the macOS tools `iconutil` (icon) and `codesign`
(ad-hoc signature). Bundle layout:

    Research OS.app/Contents/
      Info.plist          -- plistlib (see `_native_info_plist`):
                             LSUIElement FALSE now -- a windowed app.
      MacOS/ResearchOS    -- the compiled shell.
      Resources/ResearchOS.icns -- drawn by the compiled RCEIcon, folded by
                             iconutil (omitted, with a notice, on failure:
                             an app without its icon still works).
      Resources/engine-path -- the absolute path of this interpreter's
                             `researchos` entry point, written byte-exact
                             (UTF-8, no newline) and read by the shell at
                             RUNTIME.
      Resources/engine-port -- the engine port (default 7357).
      Resources/build-id  -- what the bundle was built from (9.12).

The bundle identifier is `dev.researchos.app` (DESIGN.md 12.8): new with
the name, so macOS treats Research OS.app as a new application and asks
once more for access to the Documents folder. An `RCE.app` beside it is
removed after the build -- but only one this tool built (its identifier is
the old `dev.researchos.rce`); any other app of that name is left alone
and named in a notice (`remove_legacy_bundle`).

Why sidecars instead of baking values into the source: the Swift text is
compiled exactly as shipped, so there is no interpolation and no quoting
problem at all -- a venv path with spaces, quotes or CJK is only ever data
the shell hands to `Process.executableURL`. The same reasoning the V3
launcher applied with `shlex.quote`, made structural.

Build discipline: everything is assembled in a hidden staging directory
beside the target (same filesystem), then swapped in -- an existing
`Research OS.app` (native or the V3 launcher) is replaced in place, never merged
with: a stale `_CodeSignature` or launcher script left inside a new bundle
would make macOS call the app damaged. `swiftc` missing (or present but
unable to compile, e.g. the command-line-tools shim on a Mac without
them) falls back to the launcher bundle with one notice line; it is never
a hard failure, because the launcher still gives the researcher a
double-clickable app.

Pure file writing apart from the subprocess calls to the toolchain, which
go through `_run` (list arguments, never a shell) so tests can observe or
replace them. The macOS-only gate for the DEFAULT install location stays
in `researchos.cli.cmd_app`.

THE LAUNCHER (`generate_bundle`, the fallback; unchanged from V3): an
`Research OS.app` whose executable is a small generated bash script that
(1) checks whether the local server is already up at its fixed port and
just opens the page if so, else (2) starts `researchos serve` detached, waits for
it to answer, and opens the page in the browser.

Launcher notes (V3). Pure-python generation, testable anywhere (spec requirement): nothing in
here calls `open`, checks `sys.platform`, or requires macOS -- it only
writes two files under a caller-chosen directory. The macOS-only gate
lives in `researchos.cli.cmd_app`, and applies solely to the *default* install
location (`~/Applications`); generation into an explicit `--dir` works on
any platform, which is exactly what the test suite uses. `is_macos()`
below exists for that gate (public, unlike `researchos.webapp.server._is_macos`,
because its caller is another module -- same check, different visibility
need).

Bundle layout (the minimum macOS requires to treat a directory as an app):

    Research OS.app/
      Contents/
        Info.plist        -- stdlib plistlib; CFBundleName "Research OS",
                             CFBundleIdentifier "dev.researchos.app",
                             CFBundleExecutable, and LSUIElement true --
                             the bundle is a launcher that runs and exits,
                             so it must never bounce in the Dock or claim
                             app-switcher presence.
        MacOS/
          ResearchOS       -- the generated bash script, chmod 0o755.

The launcher bakes in the ABSOLUTE path of the current interpreter's
`researchos` entry point (`<sys.executable's bin dir>/researchos`), resolved at
generation time -- never `researchos` off `$PATH`, because a double-clicked app
inherits the login session's environment, not the user's shell rc files,
so the venv that owns this install would not be on its PATH. A bin dir
with no `researchos` script is a refusal (`MacAppError`), not a guessed
fallback (DESIGN.md section 0): it means this interpreter never had
researchos installed as a console script, and a launcher pointing at a nonexistent
path would fail only later, silently, on double-click.

Shell-injection surface: none by construction. The one value interpolated
into the script -- the engine path -- goes through `shlex.quote` and is then
only ever expanded as `"$ENGINE"`; `$HOME` and the URL are written by this
module as fixed text, never taken from any input. There is no user- or
request-supplied string anywhere in generation (the target directory
names where the files land; it is never embedded in the script).

The port is `DEFAULT_PORT` (7357) for every real install: the whole
"already running?" check (launcher and shell alike) is a probe of one
known URL, and two apps disagreeing about the port would each start a
second server instead of finding the first. `researchos app`'s hidden `--port`
exists only so tests and trial builds never touch the researcher's own
engine; the serve invocation and the probe URL always come from the same
value, so they cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from researchos import names

DEFAULT_PORT = 7357

BUNDLE_NAME = names.APP_BUNDLE_NAME
BUNDLE_IDENTIFIER = names.BUNDLE_IDENTIFIER
DISPLAY_NAME = names.PRODUCT_NAME
EXECUTABLE_NAME = names.APP_EXECUTABLE_NAME
ICON_NAME = names.APP_ICON_NAME  # Resources/ResearchOS.icns; CFBundleIconFile names it without the extension
ENGINE_PATH_SIDECAR = "engine-path"
ENGINE_PORT_SIDECAR = "engine-port"
#: The app before the rename (12.8), removed only when this tool built it.
LEGACY_BUNDLE_NAME = names.LEGACY_APP_BUNDLE_NAME
LEGACY_BUNDLE_IDENTIFIER = names.LEGACY_BUNDLE_IDENTIFIER
#: Contents/Resources/build-id: a hash of everything a native build is made
#: from (DESIGN.md 9.12, acceptance 2026-10-05). macOS treats a rebuilt,
#: ad-hoc-signed Research OS.app as a new application and asks again for the
#: Documents folder; so `researchos app` leaves a bundle whose inputs have not
#: changed exactly as it is -- the question is asked once per real change
#: of the shell, not per install.
BUILD_ID_SIDECAR = "build-id"

# The lowest macOS the compiled shell targets (swiftc -target), and what
# Info.plist declares -- the two come from this one constant.
MIN_MACOS = "12.0"

SHELL_DIR = Path(__file__).parent / "shell"
SHELL_SOURCE = SHELL_DIR / "RCEShell.swift"
ICON_SOURCE = SHELL_DIR / "RCEIcon.swift"

# One line each (DESIGN.md 8.9: "says so in one line").
NOTICE_NO_SWIFTC = "未找到 swiftc（Xcode 命令行工具），已改为生成启动脚本版 Research OS.app：双击后在浏览器中打开。"
NOTICE_COMPILE_FAILED = "swiftc 编译失败，已改为生成启动脚本版 Research OS.app：双击后在浏览器中打开。"
NOTICE_NO_ICON = "图标生成失败，Research OS.app 将使用系统默认图标。"
NOTICE_LEGACY_KEPT = "{path} 不是 Research OS 生成的旧版 RCE.app（标识不同），没有删除它。"
NOTICE_LEGACY_FAILED = "旧版 RCE.app（{path}）没能删除：{error}"
NOTICE_NO_CODESIGN = "未能对 Research OS.app 做本机签名（codesign 不可用或失败），首次打开时系统可能需要你确认。"


class MacAppError(Exception):
    """Bundle generation cannot proceed (currently: this interpreter has no
    `researchos` entry point to point the launcher at). Message is user-facing;
    `researchos.cli.cmd_app` re-raises it as `CliError`."""


def is_macos() -> bool:
    """Same check as `researchos.webapp.server._is_macos` -- each subsystem owns
    its copy (existing convention); public here because the caller that
    gates on it (`researchos.cli.cmd_app`) lives in another module."""
    return sys.platform == "darwin"


def resolve_executable() -> Path:
    """The ABSOLUTE path of the current interpreter's `researchos` console script:
    `sys.executable`'s own bin directory joined with `researchos` -- the venv (or
    system prefix) this very process runs from, so the generated launcher
    always starts the same install that generated it. Deliberately NOT
    `Path.resolve()`d: `sys.executable` is already absolute, and a venv's
    `bin/python` is typically a symlink to the base interpreter -- resolving
    it would walk out of the venv into a bin dir that has no `researchos` at all
    (a real failure on this repo's own `.venv`, whose python links to a
    conda install). Missing means this interpreter has no researchos entry point;
    refuse rather than fall back to `$PATH` lookup (module docstring)."""
    candidate = Path(sys.executable).parent / names.COMMAND
    if not candidate.is_file():
        raise MacAppError(
            f"no '{names.COMMAND}' entry point next to this interpreter ({candidate} does not exist); "
            f"install researchos into this environment first (e.g. 'pip install -e .' in the repo)"
        )
    return candidate


def launcher_script(executable: Path, port: int = DEFAULT_PORT) -> str:
    """The launcher's bash source. Fixed text except for the one
    `shlex.quote`d engine path (module docstring's injection note); every
    later use expands `"$ENGINE"`/`"$URL"`/`"$LOG"` double-quoted. The
    log goes to the home the engine will use: `$RESEARCHOS_HOME`, else the
    old `$RCE_HOME`, else `~/.researchos` -- or the old home while it is the
    only one there, so the launcher never creates the new home beside the
    old one before the engine renames it (12.8).

    Flow, matching the spec exactly: a `curl -sf` probe of
    `/api/summary` decides between "already running -- just open the
    page" and "start `researchos serve` detached (nohup, log appended to
    ~/.researchos/serve.log), poll the same URL up to ~10s (40 x 0.25s), then
    open it". The one deliberate addition: if the server never answers
    within the budget, the launcher opens the *log* instead of the URL --
    a browser's connection-refused page says nothing, while the log
    carries the server's actual startup error (e.g. the empty-registry
    message a first-ever run prints)."""
    quoted_engine = shlex.quote(str(executable))
    new_home, old_home = names.HOME_DIRNAME, names.LEGACY_HOME_DIRNAME
    new_var, old_var = names.HOME_ENV_VAR, names.LEGACY_HOME_ENV_VAR
    return f"""#!/bin/bash
# Generated by 'researchos app' -- double-clickable launcher for the Research OS web app.
# The engine path below is baked in at generation time; re-run 'researchos app'
# after moving or recreating the environment it points into.
set -u

URL='http://127.0.0.1:{port}'
ENGINE={quoted_engine}
if [ -n "${{{new_var}:-}}" ]; then
  DATA="${new_var}"
elif [ -n "${{{old_var}:-}}" ]; then
  DATA="${old_var}"
elif [ -d "$HOME/{new_home}" ] || [ ! -d "$HOME/{old_home}" ]; then
  DATA="$HOME/{new_home}"
else
  DATA="$HOME/{old_home}"  # renamed by the engine on its first start
fi
LOG="$DATA/serve.log"

# Already running? Just open the page -- never start a second server.
if curl -sf "$URL/api/summary" > /dev/null 2>&1; then
  exec open "$URL"
fi

mkdir -p "$DATA"
nohup "$ENGINE" serve --port {port} --no-browser >> "$LOG" 2>&1 &

# Poll up to ~10s (40 x 0.25s) for the server to come up.
for _ in {{1..40}}; do
  sleep 0.25
  if curl -sf "$URL/api/summary" > /dev/null 2>&1; then
    exec open "$URL"
  fi
done

# Never came up -- surface the log (the real error), not a browser's
# connection-refused page.
open "$LOG"
exit 1
"""


def _info_plist(port: int) -> dict:
    """The minimum Info.plist for macOS to treat the directory as an app.
    `LSUIElement` true because this is a launcher that runs and exits --
    no Dock icon, no bounce, no app-switcher entry (spec requirement).
    `port` is recorded informationally so a human inspecting the bundle
    can see which server it probes without reading the script."""
    return {
        "CFBundleName": DISPLAY_NAME,
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleExecutable": EXECUTABLE_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "NSHumanReadableCopyright": "Copyright © 2026 Linying Li.",
        "LSUIElement": True,
        "ResearchOSServerPort": port,
    }


def generate_bundle(
    target_dir: Path,
    executable: Path | None = None,
    port: int = DEFAULT_PORT,
) -> Path:
    """Write (or overwrite -- regeneration is the reinstall story) the
    `Research OS.app` bundle under `target_dir` and return the bundle's path.
    `executable` defaults to `resolve_executable()` -- the current
    interpreter's own entry point; injectable so tests can bake in a known
    path without monkeypatching `sys.executable`."""
    if executable is None:
        executable = resolve_executable()
    bundle = target_dir / BUNDLE_NAME
    macos_dir = bundle / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    with (bundle / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump(_info_plist(port), fh)

    launcher = macos_dir / EXECUTABLE_NAME
    launcher.write_text(launcher_script(executable, port), encoding="utf-8")
    launcher.chmod(0o755)
    return bundle


# -- The native shell (task V4 phase 3, DESIGN.md 8.9) -------------------------


@dataclass
class AppBuild:
    """What `build_app` produced: the bundle path, whether it is the native
    shell (False: the launcher fallback), and one-line notices for the
    user (fallback reason, icon or signing trouble) -- printed by
    `researchos.cli.cmd_app`, never swallowed."""

    bundle: Path
    native: bool
    notices: list[str] = field(default_factory=list)
    #: True when the bundle in place was built from the same inputs and was
    #: left untouched (nothing compiled, nothing re-signed).
    up_to_date: bool = False
    #: The old `RCE.app` this tool had built, removed beside the new bundle
    #: (12.8); None when there was none, or it was not ours.
    removed_legacy: Path | None = None


def find_swiftc() -> str | None:
    """The system Swift compiler, or None. Its own function so tests can
    simulate a Mac without the toolchain without touching `PATH`."""
    return shutil.which("swiftc")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Every toolchain call: list arguments (never a shell), output
    captured so a failure can be summarized instead of spraying the
    terminal."""
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _last_line(proc: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (proc.stderr or proc.stdout or "").splitlines() if ln.strip()]
    return lines[-1].strip() if lines else f"exit status {proc.returncode}"


def _swift_target() -> str:
    """`<arch>-apple-macos<MIN_MACOS>` for this machine's architecture, so
    the binary and the plist's LSMinimumSystemVersion agree."""
    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    return f"{arch}-apple-macos{MIN_MACOS}"


def _compile(swiftc: str, source: Path, output: Path) -> subprocess.CompletedProcess:
    return _run([
        swiftc, "-O", "-parse-as-library", "-target", _swift_target(),
        "-o", str(output), str(source),
    ])


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version(names.COMMAND)
    except Exception:  # not installed as a distribution (e.g. bare source tree)
        return "0"


def _native_info_plist(port: int, with_icon: bool) -> dict:
    """Info.plist for the windowed app (8.9). `LSUIElement` False: unlike
    the launcher, this app owns a window, a Dock icon and the menu bar.
    `NSAllowsLocalNetworking` is the one App Transport Security exception
    the shell needs -- plain http to 127.0.0.1 -- and nothing broader."""
    plist = {
        "CFBundleName": DISPLAY_NAME,
        "CFBundleDisplayName": DISPLAY_NAME,
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleExecutable": EXECUTABLE_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": _package_version(),
        "CFBundleVersion": _package_version(),
        "NSHumanReadableCopyright": "Copyright © 2026 Linying Li.",
        "LSUIElement": False,
        "LSMinimumSystemVersion": MIN_MACOS,
        "NSHighResolutionCapable": True,
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "ResearchOSServerPort": port,
    }
    if with_icon:
        plist["CFBundleIconFile"] = ICON_NAME
    return plist


def _build_icon(swiftc: str, work: Path, resources: Path) -> bool:
    """Compile and run RCEIcon, then `iconutil -c icns`. False (never an
    exception) on any failure: the icon is the one optional part."""
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        return False
    tool = work / "RCEIcon"
    iconset = work / f"{ICON_NAME}.iconset"
    if _compile(swiftc, ICON_SOURCE, tool).returncode != 0:
        return False
    if _run([str(tool), str(iconset)]).returncode != 0:
        return False
    icns = resources / f"{ICON_NAME}.icns"
    return _run([iconutil, "-c", "icns", "-o", str(icns), str(iconset)]).returncode == 0 and icns.is_file()


def build_id(executable: Path, port: int) -> str:
    """The native bundle's inputs, hashed: the Swift sources (shell and
    icon), the engine path, the port, the Info.plist fields and the compile
    target. Each part is length-prefixed, so no two different sets of
    inputs can run together into the same bytes."""
    digest = hashlib.sha256()
    parts = (
        SHELL_SOURCE.read_bytes(),
        ICON_SOURCE.read_bytes(),
        str(executable).encode("utf-8"),
        str(port).encode("ascii"),
        json.dumps(_native_info_plist(port, True), sort_keys=True, ensure_ascii=False).encode("utf-8"),
        _swift_target().encode("ascii"),
    )
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return "sha256:" + digest.hexdigest()


def is_up_to_date(bundle: Path, wanted: str) -> bool:
    """Whether `bundle` is a native Research OS.app built from exactly these inputs
    (its build-id says so and its executable is there). A symlink, the V3
    launcher (no build-id) or a damaged bundle is not."""
    if bundle.is_symlink() or not bundle.is_dir():
        return False
    contents = bundle / "Contents"
    try:
        recorded = (contents / "Resources" / BUILD_ID_SIDECAR).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return recorded == wanted and (contents / "MacOS" / EXECUTABLE_NAME).is_file()


def _write_sidecars(resources: Path, executable: Path, port: int) -> None:
    """Byte-exact: no trailing newline, no quoting -- the shell reads the
    file whole and uses it only as an executable URL (module docstring).
    The build-id is written here too, before signing: the signature covers
    it."""
    (resources / ENGINE_PATH_SIDECAR).write_bytes(str(executable).encode("utf-8"))
    (resources / ENGINE_PORT_SIDECAR).write_text(str(port), encoding="utf-8")
    (resources / BUILD_ID_SIDECAR).write_text(build_id(executable, port), encoding="utf-8")


def _swap_into_place(staged: Path, final: Path) -> None:
    """Replace `final` with `staged` (both on the same filesystem). A
    symlink at the target is removed, never followed: replacing "Research OS.app"
    must not delete whatever it pointed at."""
    if final.is_symlink() or final.is_file():
        final.unlink()
    elif final.exists():
        shutil.rmtree(final)
    staged.rename(final)


def _build_native(swiftc: str, staged: Path, work: Path, executable: Path, port: int,
                  notices: list[str]) -> bool:
    contents = staged / "Contents"
    macos_dir = contents / "MacOS"
    resources = contents / "Resources"
    macos_dir.mkdir(parents=True)
    resources.mkdir()
    proc = _compile(swiftc, SHELL_SOURCE, macos_dir / EXECUTABLE_NAME)
    if proc.returncode != 0:
        notices.append(NOTICE_COMPILE_FAILED + f"（{_last_line(proc)}）")
        return False
    with_icon = _build_icon(swiftc, work, resources)
    if not with_icon:
        notices.append(NOTICE_NO_ICON)
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump(_native_info_plist(port, with_icon), fh)
    _write_sidecars(resources, executable, port)
    codesign = shutil.which("codesign")
    if codesign is None or _run([codesign, "--force", "-s", "-", str(staged)]).returncode != 0:
        notices.append(NOTICE_NO_CODESIGN)
    return True


def build_app(
    target_dir: Path,
    executable: Path | None = None,
    port: int = DEFAULT_PORT,
    force: bool = False,
) -> AppBuild:
    """Build `<target_dir>/Research OS.app`: the native shell when `swiftc` is
    available and compiles, else the V3 launcher bundle plus a one-line
    notice. Either way the result replaces any existing bundle in place
    (module docstring, "Build discipline"). `executable` defaults to
    `resolve_executable()`; resolved first, so a missing entry point
    refuses before any compiling.

    A native bundle already in place whose build-id matches these inputs
    is left untouched -- not recompiled, not re-signed -- so macOS keeps
    the permission it granted it (9.12); `force` rebuilds anyway."""
    if executable is None:
        executable = resolve_executable()
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / BUNDLE_NAME
    if not force and is_up_to_date(final, build_id(executable, port)):
        notices: list[str] = []
        removed = remove_legacy_bundle(target_dir, notices)
        return AppBuild(bundle=final, native=True, notices=notices, up_to_date=True, removed_legacy=removed)
    notices = []
    swiftc = find_swiftc()
    if swiftc is None:
        notices.append(NOTICE_NO_SWIFTC)
    with tempfile.TemporaryDirectory(dir=target_dir, prefix=".ResearchOS.app.build-") as tmp:
        work = Path(tmp)
        staged = work / BUNDLE_NAME
        native = swiftc is not None and _build_native(swiftc, staged, work, executable, port, notices)
        if not native:
            if staged.exists():
                shutil.rmtree(staged)
            generate_bundle(work, executable=executable, port=port)
        _swap_into_place(staged, final)
    removed = remove_legacy_bundle(target_dir, notices)
    return AppBuild(bundle=final, native=native, notices=notices, removed_legacy=removed)


def bundle_identifier(bundle: Path) -> str | None:
    """The `CFBundleIdentifier` a bundle's Info.plist declares; None when
    it has none or cannot be read (never a guess)."""
    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as fh:
            plist = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    value = plist.get("CFBundleIdentifier") if isinstance(plist, dict) else None
    return value if isinstance(value, str) else None


def remove_legacy_bundle(target_dir: Path, notices: list[str]) -> Path | None:
    """Remove `<target_dir>/RCE.app` -- the app before the rename (12.8) --
    only when it is the one this tool built: a real directory (not a
    symlink) whose Info.plist names the old identifier. Any other
    `RCE.app` is left exactly where it is, with one notice. Returns the
    removed path, or None."""
    legacy = target_dir / LEGACY_BUNDLE_NAME
    if not os.path.lexists(legacy):
        return None
    if legacy.is_symlink() or not legacy.is_dir() or bundle_identifier(legacy) != LEGACY_BUNDLE_IDENTIFIER:
        notices.append(NOTICE_LEGACY_KEPT.format(path=legacy))
        return None
    try:
        shutil.rmtree(legacy)
    except OSError as exc:
        notices.append(NOTICE_LEGACY_FAILED.format(path=legacy, error=exc))
        return None
    return legacy
