"""Tests for researchos.webapp.macapp: `researchos app`'s two products -- the native shell
Research OS.app (task V4 phase 3, DESIGN.md 8.9) and the V3 launcher-script bundle
it falls back to -- plus the CLI wiring.

The launcher tests run on any platform (pure file writing; its bash source
is asserted textually, plus a `bash -n` syntax check where bash exists).
The native-build tests compile the real Swift sources with the system
swiftc, once per module, and skip cleanly where macOS or swiftc is absent;
the fallback paths are exercised everywhere by simulating a missing or
failing compiler. Nothing here LAUNCHES an app: the shell's runtime
promises (menus, bridge shapes, runtime-only configuration, escaped log
text, stop-only-what-it-spawned) are pinned in the Swift text, the same
way the server tests assert `subprocess` argument lists rather than
really opening Finder.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from researchos import cli, paths
from researchos.webapp import macapp, server
from test_v8_u2 import FORBIDDEN


def _fake_rce(tmp_path: Path, dirname: str = "bin") -> Path:
    """A stand-in entry-point file so generation tests bake in a KNOWN path
    instead of whatever interpreter runs the test suite."""
    bin_dir = tmp_path / dirname
    bin_dir.mkdir(parents=True, exist_ok=True)
    rce = bin_dir / "researchos"
    rce.write_text("#!/bin/sh\n")
    return rce


def _launcher(bundle: Path) -> Path:
    return bundle / "Contents" / "MacOS" / macapp.EXECUTABLE_NAME


# -- generate_bundle: bundle shape --------------------------------------------


def test_generate_bundle_writes_parseable_plist_with_required_keys(tmp_path):
    bundle = macapp.generate_bundle(tmp_path, executable=_fake_rce(tmp_path))
    assert bundle == tmp_path / "Research OS.app"
    with (bundle / "Contents" / "Info.plist").open("rb") as fh:
        plist = plistlib.load(fh)
    assert plist["CFBundleName"] == "Research OS"
    assert plist["CFBundleIdentifier"] == "dev.researchos.app"
    assert plist["CFBundleExecutable"] == macapp.EXECUTABLE_NAME
    # The launcher must never bounce in the Dock -- it runs and exits.
    assert plist["LSUIElement"] is True


def test_generate_bundle_launcher_is_executable(tmp_path):
    bundle = macapp.generate_bundle(tmp_path, executable=_fake_rce(tmp_path))
    launcher = _launcher(bundle)
    assert launcher.stat().st_mode & 0o777 == 0o755
    assert os.access(launcher, os.X_OK)


def test_generate_bundle_is_idempotent_overwrite(tmp_path):
    """Regeneration is the reinstall story: a second run over an existing
    bundle succeeds and leaves the same content, not a duplicate or an
    error."""
    rce = _fake_rce(tmp_path)
    first = macapp.generate_bundle(tmp_path, executable=rce)
    script_before = _launcher(first).read_text()
    second = macapp.generate_bundle(tmp_path, executable=rce)
    assert second == first
    assert _launcher(second).read_text() == script_before


# -- generate_bundle: launcher script content ---------------------------------


def test_launcher_contains_resolved_rce_path_and_port(tmp_path):
    rce = _fake_rce(tmp_path)
    bundle = macapp.generate_bundle(tmp_path, executable=rce)
    script = _launcher(bundle).read_text()
    assert script.startswith("#!/bin/bash\n")
    assert shlex.quote(str(rce)) in script
    # Probe URL and serve invocation come from the same constant (7357).
    assert f"http://127.0.0.1:{macapp.DEFAULT_PORT}" in script
    assert f"serve --port {macapp.DEFAULT_PORT} --no-browser" in script
    assert "/api/summary" in script
    assert "nohup" in script and "serve.log" in script


def test_launcher_quotes_shell_metacharacter_paths(tmp_path):
    """No shell-injection surface: a path carrying quote/semicolon
    metacharacters must appear only in its `shlex.quote`d form -- the raw
    string (whose single quotes would terminate a naive '...'-wrapping)
    must not appear anywhere in the script."""
    rce = _fake_rce(tmp_path, dirname="evil'; touch pwned; 'dir")
    bundle = macapp.generate_bundle(tmp_path, executable=rce)
    script = _launcher(bundle).read_text()
    assert shlex.quote(str(rce)) in script
    assert str(rce) not in script  # only the quoted form, never the raw one
    # ...and the quoted result is still valid bash, not just different.
    if shutil.which("bash"):
        proc = subprocess.run(["bash", "-n", str(_launcher(bundle))], capture_output=True)
        assert proc.returncode == 0, proc.stderr


def test_launcher_expands_baked_path_only_double_quoted(tmp_path):
    """The baked path is assigned once (ENGINE=<quoted>) and every later use
    is the double-quoted expansion -- a bare $ENGINE would re-split a path
    with spaces at execution time even though generation quoted it."""
    rce = _fake_rce(tmp_path, dirname="dir with spaces")
    script = _launcher(macapp.generate_bundle(tmp_path, executable=rce)).read_text()
    assert '"$ENGINE" serve' in script
    assert script.count("$ENGINE") == script.count('"$ENGINE"')  # every expansion is the quoted one


# -- resolve_executable ----------------------------------------------------


def test_resolve_rce_executable_uses_interpreters_own_bin_dir(tmp_path, monkeypatch):
    rce = _fake_rce(tmp_path)
    monkeypatch.setattr(macapp.sys, "executable", str(rce.parent / "python"))
    assert macapp.resolve_executable() == rce


def test_resolve_rce_executable_refuses_when_entry_point_missing(tmp_path, monkeypatch):
    """No `researchos` next to the interpreter is a refusal naming the missing
    path -- never a $PATH-lookup fallback (module docstring)."""
    monkeypatch.setattr(macapp.sys, "executable", str(tmp_path / "python"))
    with pytest.raises(macapp.MacAppError, match="researchos"):
        macapp.resolve_executable()


def test_generate_bundle_defaults_to_current_interpreters_rce(tmp_path, monkeypatch):
    rce = _fake_rce(tmp_path)
    monkeypatch.setattr(macapp.sys, "executable", str(rce.parent / "python"))
    bundle = macapp.generate_bundle(tmp_path)
    assert shlex.quote(str(rce)) in _launcher(bundle).read_text()




# -- build_app: the native shell (task V4 phase 3, DESIGN.md 8.9) -------------
# These compile the real Swift sources with the system swiftc (about ten
# seconds per program), once per module, and skip cleanly on a machine
# without macOS or the toolchain. The build deliberately lands on top of an
# installed V3 launcher bundle, so the same fixture also proves the
# replace-in-place rule.

_HAS_SWIFTC = macapp.is_macos() and shutil.which("swiftc") is not None
native_only = pytest.mark.skipif(not _HAS_SWIFTC, reason="needs macOS with swiftc")

_TRIAL_PORT = 7468
# Spaces, both quote kinds and CJK: the sidecar must carry it byte-exact.
_HOSTILE_DIR = "venv with 'single' \"double\" 中文/bin"


@pytest.fixture(scope="module")
def native_build(tmp_path_factory):
    if not _HAS_SWIFTC:
        pytest.skip("needs macOS with swiftc")
    root = tmp_path_factory.mktemp("native")
    rce = _fake_rce(root, dirname=_HOSTILE_DIR)
    target = root / "apps"
    old = macapp.generate_bundle(target, executable=rce)
    (old / "Contents" / "stale.txt").write_text("left over from the launcher")
    result = macapp.build_app(target, executable=rce, port=_TRIAL_PORT)
    return result, rce, target


def _plist(bundle: Path) -> dict:
    with (bundle / "Contents" / "Info.plist").open("rb") as fh:
        return plistlib.load(fh)


@native_only
def test_native_build_produces_an_executable_mach_o_binary(native_build):
    result, _, _ = native_build
    assert result.native is True
    assert macapp.NOTICE_COMPILE_FAILED not in " ".join(result.notices)
    binary = _launcher(result.bundle)
    assert binary.is_file() and os.access(binary, os.X_OK)
    magic = binary.read_bytes()[:4]
    assert magic in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")  # Mach-O 64 / universal, not a script


@native_only
def test_native_build_info_plist_keys(native_build):
    result, _, _ = native_build
    plist = _plist(result.bundle)
    assert plist["CFBundleExecutable"] == "ResearchOS"
    assert plist["CFBundleIdentifier"] == "dev.researchos.app"
    assert plist["CFBundleName"] == "Research OS"
    assert plist["CFBundleIconFile"] == "ResearchOS"
    assert plist["LSUIElement"] is False  # a windowed app now
    assert plist["NSHighResolutionCapable"] is True
    assert plist["LSMinimumSystemVersion"] == macapp.MIN_MACOS
    assert plist["NSAppTransportSecurity"] == {"NSAllowsLocalNetworking": True}


@native_only
def test_native_build_has_an_icns_icon(native_build):
    result, _, _ = native_build
    assert macapp.NOTICE_NO_ICON not in result.notices
    icns = result.bundle / "Contents" / "Resources" / "ResearchOS.icns"
    assert icns.is_file() and icns.read_bytes()[:4] == b"icns"


@native_only
def test_native_build_sidecars_carry_a_hostile_path_byte_exact(native_build):
    """The rce path is data, never source: written byte-exact (no quoting,
    no newline) and read whole by the shell at runtime."""
    result, rce, _ = native_build
    resources = result.bundle / "Contents" / "Resources"
    assert (resources / "engine-path").read_bytes() == str(rce).encode("utf-8")
    assert (resources / "engine-port").read_text(encoding="utf-8") == str(_TRIAL_PORT)


@native_only
def test_native_build_replaced_the_launcher_bundle_in_place(native_build):
    result, _, target = native_build
    assert result.bundle == target / "Research OS.app"
    assert not (result.bundle / "Contents" / "stale.txt").exists()
    assert not _launcher(result.bundle).read_bytes().startswith(b"#!")
    assert [p.name for p in target.iterdir()] == ["Research OS.app"]  # no staging directory left behind


@native_only
def test_native_build_is_ad_hoc_signed(native_build):
    result, _, _ = native_build
    codesign = shutil.which("codesign")
    if codesign is None:
        pytest.skip("codesign not available")
    proc = subprocess.run([codesign, "--verify", "--strict", str(result.bundle)], capture_output=True)
    assert proc.returncode == 0, proc.stderr


# -- build_app: falling back to the launcher bundle ---------------------------


def test_build_app_without_swiftc_falls_back_to_the_launcher(tmp_path, monkeypatch):
    """No toolchain: the V3 launcher bundle, one notice line, and an
    existing native bundle replaced -- its signature and sidecars must not
    survive into a bundle whose executable is now a script."""
    monkeypatch.setattr(macapp, "find_swiftc", lambda: None)
    rce = _fake_rce(tmp_path)
    old = tmp_path / "apps" / "Research OS.app" / "Contents"
    (old / "_CodeSignature").mkdir(parents=True)
    (old / "_CodeSignature" / "CodeResources").write_text("sig")
    (old / "Resources").mkdir()
    (old / "Resources" / "engine-path").write_text("/old/researchos")

    result = macapp.build_app(tmp_path / "apps", executable=rce, port=_TRIAL_PORT)

    assert result.native is False
    assert result.notices == [macapp.NOTICE_NO_SWIFTC]
    assert "\n" not in macapp.NOTICE_NO_SWIFTC  # "says so in one line"
    script = _launcher(result.bundle).read_text()
    assert script.startswith("#!/bin/bash\n") and f"serve --port {_TRIAL_PORT}" in script
    assert _plist(result.bundle)["LSUIElement"] is True
    assert not (old / "_CodeSignature").exists() and not (old / "Resources").exists()


def test_build_app_falls_back_when_swiftc_cannot_compile(tmp_path, monkeypatch):
    """A swiftc that exists but fails (the command-line-tools shim on a Mac
    without them) is the same fallback, with the compiler's last line."""
    fake = tmp_path / "swiftc"
    fake.write_text("#!/bin/sh\necho 'error: no developer tools were found' >&2\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setattr(macapp, "find_swiftc", lambda: str(fake))

    result = macapp.build_app(tmp_path / "apps", executable=_fake_rce(tmp_path))

    assert result.native is False
    assert len(result.notices) == 1
    assert result.notices[0].startswith(macapp.NOTICE_COMPILE_FAILED)
    assert "no developer tools were found" in result.notices[0]
    assert _launcher(result.bundle).read_text().startswith("#!/bin/bash\n")


def test_build_app_replaces_a_symlinked_bundle_without_following_it(tmp_path, monkeypatch):
    monkeypatch.setattr(macapp, "find_swiftc", lambda: None)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("not ours")
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "Research OS.app").symlink_to(elsewhere, target_is_directory=True)

    result = macapp.build_app(apps, executable=_fake_rce(tmp_path))

    assert (elsewhere / "keep.txt").read_text() == "not ours"
    assert not result.bundle.is_symlink() and _launcher(result.bundle).is_file()


def test_build_app_refuses_a_missing_entry_point_before_compiling(tmp_path, monkeypatch):
    monkeypatch.setattr(macapp.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(macapp, "find_swiftc", lambda: pytest.fail("must not look for swiftc"))
    with pytest.raises(macapp.MacAppError):
        macapp.build_app(tmp_path / "apps")
    assert not (tmp_path / "apps" / "Research OS.app").exists()


def test_native_info_plist_names_the_icon_only_when_it_was_built():
    assert "CFBundleIconFile" not in macapp._native_info_plist(7357, with_icon=False)
    assert macapp._native_info_plist(7357, with_icon=True)["CFBundleIconFile"] == "ResearchOS"


# -- The Swift sources: what the shell promises, checked in the text ----------
# Runs everywhere (no compiling): the menus, the bridge's fixed shapes and
# the runtime-only configuration are part of DESIGN.md 8.9's contract.

_SHELL = macapp.SHELL_SOURCE.read_text(encoding="utf-8")


def test_swift_sources_ship_as_package_data():
    import tomllib

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "webapp/shell/*.swift" in pyproject["tool"]["setuptools"]["package-data"]["researchos"]
    assert macapp.SHELL_SOURCE.is_file() and macapp.ICON_SOURCE.is_file()


def test_shell_menus_match_the_design():
    """V8 U5 (12.9): 文件 holds 添加项目… ⌘O, 提交提议… / 发布 by role, 关闭 ⌘W;
    视图 names the eight sidebar sections on ⌘1-⌘8, then 重新载入 and the
    zoom set. The pre-12.9 views (决策树 / 血缘 / 画布, 适应全部) are gone."""
    for snippet in (
        'submenu("Research OS"', 'item("关于 Research OS"', 'item("退出", #selector(NSApplication.terminate(_:)), "q")',
        'submenu("文件"', 'cmd("添加项目…", "add-project", "o")', 'proposeItem = cmd("提交提议…", "propose")',
        'publishItem = cmd("发布", "publish")',
        'item("关闭", #selector(NSWindow.performClose(_:)), "w")',
        'submenu("编辑"', '"undo:"', '"redo:"', "NSText.cut", "NSText.copy", "NSText.paste", "NSText.selectAll",
        'submenu("视图"', 'cmd("工作台", "desk", "1")', 'cmd("研究路线", "routes", "2")', 'cmd("成果", "results", "3")',
        'cmd("变量", "variables", "4")', 'cmd("文献", "literature", "5")', 'cmd("数据与代码", "data", "6")',
        'cmd("提议", "proposals", "7")', 'cmd("成员", "members", "8")',
        'item("重新载入", #selector(reloadPage(_:)), "r")', 'cmd("放大", "zoom-in", "+")',
        'cmd("缩小", "zoom-out", "-")', 'cmd("实际大小", "zoom-reset", "0")',
        'submenu("窗口"', 'item("最小化", #selector(NSWindow.performMiniaturize(_:)), "m")',
        'item("缩放", #selector(NSWindow.performZoom(_:))',
        'submenu("帮助", [cmd("打开项目地图", "open-map")])',
    ):
        assert snippet in _SHELL, f"menu item missing from RCEShell.swift: {snippet}"
    for gone in ('"决策树"', '"血缘"', '"画布"', '"适应全部"', '"fit"', '"new-attempt"', '"reveal-project"'):
        assert gone not in _SHELL, f"pre-12.9 menu item still in RCEShell.swift: {gone}"
    files = _SHELL[_SHELL.index('submenu("文件"'):]
    files = files[: files.index("]))")]
    order = [files.index(x) for x in ('"add-project"', "proposeItem,", "publishItem,", '"关闭"')]
    assert order == sorted(order)
    view = _SHELL[_SHELL.index('submenu("视图"'):]
    view = view[: view.index("])")]
    keys = [view.index(f'"{k}")') for k in "12345678"]
    assert keys == sorted(keys)


def test_shell_whitelist_and_page_dispatcher_agree():
    """Every command the shell can send is one the page's dispatcher
    accepts, and every section command lands on its 12.9 section."""
    import re

    whitelist = _SHELL[_SHELL.index("let shellCommands"):]
    whitelist = set(re.findall(r'"([a-z-]+)"', whitelist[: whitelist.index("]")]))
    assert whitelist == {
        "workspace", "create-project",
        "desk", "routes", "results", "variables", "literature", "data", "proposals", "members",
        "add-project", "propose", "publish", "reload", "zoom-in", "zoom-out", "zoom-reset", "open-map",
    }
    sent = set(re.findall(r'cmd\("[^"]+", "([a-z-]+)"', _SHELL))
    assert sent <= whitelist and "reload" in _SHELL
    page = (Path(macapp.__file__).parent / "app.html").read_text(encoding="utf-8")
    table = page[page.index("const SHELL_COMMANDS = {"):]
    table = table[: table.index("\n};\n")]
    accepted = set(re.findall(r'^  "([a-z-]+)":', table, re.M))
    assert whitelist <= accepted
    for name, target in (("workspace", 'activateView("workspace")'),
                         ("create-project", "ROWorkspace.openCreateProject()"),
                         ("desk", 'activateView("desk")'), ("routes", 'activateView("tree")'),
                         ("results", 'activateView("results")'), ("variables", 'activateView("variables")'),
                         ("literature", 'activateView("literature")'), ("data", "activateView(lastDataView)"),
                         ("proposals", 'activateView("proposals")'), ("members", 'activateView("members")'),
                         ("propose", 'revealShellAnchor("proposals", "compose")'),
                         ("publish", 'revealShellAnchor("members", "publish")')):
        assert f'"{name}": () => {target}' in table, name
    team = (Path(macapp.__file__).parent / "team.js").read_text(encoding="utf-8")
    assert 's.dataset.anchor = "compose";' in team and 's.dataset.anchor = "publish";' in team


def test_shell_file_menu_shows_propose_or_publish_by_the_pages_mode():
    """V8 U5: the page says the project's role as `{type: "mode", mode}`
    (exactly two keys, mode one of four fixed words; anything else is
    ignored); 提交提议… shows only for a member, 发布 only for the PI,
    neither in a project of one person."""
    assert 'let teamModes: Set<String> = ["single", "pi", "member", "none"]' in _SHELL
    handler = _SHELL[_SHELL.index("didReceive message: WKScriptMessage"):]
    handler = handler[: handler.index("\n    }\n")]
    assert 'body.count == 2, body["type"] as? String == "mode"' in handler
    assert 'let mode = body["mode"] as? String, teamModes.contains(mode)' in handler
    apply = _SHELL[_SHELL.index("func applyTeamMode"):]
    apply = apply[: apply.index("\n    }\n")]
    assert 'proposeItem.isHidden = teamMode != "member"' in apply
    assert 'publishItem.isHidden = teamMode != "pi"' in apply
    assert 'var teamMode = "single"' in _SHELL
    validate = _SHELL[_SHELL.index("func validateMenuItem"):]
    validate = validate[: validate.index("\n    }\n")]
    assert 'menuItem === proposeItem { return appLoaded && teamMode == "member" }' in validate
    assert 'menuItem === publishItem { return appLoaded && teamMode == "pi" }' in validate
    page = (Path(macapp.__file__).parent / "app.html").read_text(encoding="utf-8")
    post = page[page.index("function postShellMode"):]
    post = post[: post.index("\n}\n")]
    assert '["single", "pi", "member", "none"].indexOf(mode) >= 0 ? mode : "single"' in post
    assert 'postMessage({ type: "mode", mode: m })' in post
    desk = (Path(macapp.__file__).parent / "desk.js").read_text(encoding="utf-8")
    assert 'postShellMode(state.noProject ? "single" : ctx.mode)' in desk


def test_shell_choose_folder_answers_through_a_fixed_callback_with_arguments():
    """V5 phase 9 (9.4 「选择新位置…」): `{type: "choose-folder", request}`
    opens a one-directory NSOpenPanel; the answer goes back through the
    fixed callback with the request id and the path passed as arguments --
    never interpolated into script text; any other shape is ignored; the
    page is told it may ask (RCEShellFeatures) at document start."""
    handler = _SHELL[_SHELL.index("didReceive message: WKScriptMessage"):]
    handler = handler[: handler.index("\n    }\n")]
    assert 'body["type"] as? String == "choose-folder"' in handler
    assert 'let request = body["request"] as? String' in handler and "isFolderRequest(request)" in handler
    assert "body.count == 2" in handler and "message.frameInfo.isMainFrame" in handler
    choose = _SHELL[_SHELL.index("func chooseFolder"):]
    choose = choose[: choose.index("\n    }\n")]
    assert "panel.canChooseDirectories = true" in choose and "panel.canChooseFiles = false" in choose
    assert "panel.allowsMultipleSelection = false" in choose
    answer = _SHELL[_SHELL.index("func answerFolder"):]
    answer = answer[: answer.index("\n    }\n")]
    assert 'callAsyncJavaScript("window.RCE && RCE.folderChosen(request, path)", arguments: arguments' in answer
    assert '["request": request, "path": path ?? NSNull()]' in answer
    assert "\\(" not in answer  # no string interpolation anywhere in the callback
    assert ('let shellFeaturesScript = "window.RCEShellFeatures = [\\"choose-folder\\", \\"choose-folder-purpose\\", '
            '\\"choose-folder-share\\", \\"choose-folder-mine\\", \\"choose-file-key\\"];"') in _SHELL
    assert "injectionTime: .atDocumentStart, forMainFrameOnly: true" in _SHELL
    validator = _SHELL[_SHELL.index("func isFolderRequest"):]
    validator = validator[: validator.index("\n}\n")]
    assert "folderRequestLimit" in validator and '$0 == "-"' in validator and "isASCII" in validator


def test_shell_file_menu_adds_a_project_on_cmd_o():
    """DESIGN.md 10.8 #1 (first launch) and 10.7: 文件 → 「添加项目… ⌘O」 is
    the whitelisted command `add-project`, and the page's dispatcher opens
    the add dialog for it."""
    assert 'cmd("添加项目…", "add-project", "o")' in _SHELL
    files = _SHELL[_SHELL.index('submenu("文件"'):]
    files = files[: files.index("]))")]
    assert files.index('"add-project"') < files.index("proposeItem,")
    whitelist = _SHELL[_SHELL.index("let shellCommands"):]
    whitelist = whitelist[: whitelist.index("]")]
    assert '"add-project"' in whitelist
    page = (Path(macapp.__file__).parent / "app.html").read_text(encoding="utf-8")
    assert '"add-project": () => openAddDialog(),' in page


def test_shell_folder_panel_recognizes_create_add_and_locate_purposes():
    """DESIGN.md 10.7 (and 10.8 #1): the bridge takes `{type, request,
    purpose}`; a new project chooses its parent save folder separately
    from adding an existing project. The two-key shape remains the locate
    panel for older pages; an unknown purpose is still ignored."""
    helper = _SHELL[_SHELL.index("func folderPanelMessage"):]
    helper = helper[: helper.index("\n}\n")]
    assert 'case "add": return "选择要加入 Research OS 的项目文件夹"' in helper
    assert 'case "locate": return "选择项目文件夹的新位置"' in helper
    assert 'case "create": return "选择新项目的保存位置"' in helper
    assert "default: return nil" in helper
    handler = _SHELL[_SHELL.index("didReceive message: WKScriptMessage"):]
    handler = handler[: handler.index("\n    }\n")]
    assert 'body.count == 2, body["type"] as? String == "choose-folder"' in handler
    assert 'chooseFolder(request, purpose: "locate")' in handler
    assert 'body.count == 3, body["type"] as? String == "choose-folder"' in handler
    assert 'let purpose = body["purpose"] as? String, folderPanelMessage(purpose) != nil' in handler
    assert 'body.count == 2, body["type"] as? String == "title"' in handler
    choose = _SHELL[_SHELL.index("func chooseFolder"):]
    choose = choose[: choose.index("\n    }\n")]
    assert "let panelMessage = folderPanelMessage(purpose)" in choose
    assert "panel.message = tr(panelMessage)" in choose  # V9 13.3: in the shell's language
    # Both a working copy and a new project's parent may need a new folder.
    assert 'panel.canCreateDirectories = purpose == "working" || purpose == "create"' in choose
    # the page sends the purpose only to a shell that says it takes it
    page = (Path(macapp.__file__).parent / "app.html").read_text(encoding="utf-8")
    assert 'shellCan("choose-folder-purpose")) message.purpose = purpose;' in page
    assert 'const FOLDER_PURPOSES = ["add", "locate", "create"];' in page


def test_u4_shell_takes_the_shared_and_working_folders_and_a_key_file():
    """V8 U4 (12.3, 12.5): two more folder purposes -- "shared" (the shared
    folder) and "working" (a member's new working copy) -- and one file
    panel, `{type: "choose-file", request, purpose: "key"}` for the public
    key file a member sent the PI, answered through the same fixed callback;
    the page sends them only to a shell that names the features."""
    helper = _SHELL[_SHELL.index("func folderPanelMessage"):]
    helper = helper[: helper.index("\n}\n")]
    assert 'case "shared": return' in helper and 'case "working": return' in helper
    files = _SHELL[_SHELL.index("func filePanelMessage"):]
    files = files[: files.index("\n}\n")]
    assert 'case "key": return' in files and "default: return nil" in files
    handler = _SHELL[_SHELL.index("didReceive message: WKScriptMessage"):]
    handler = handler[: handler.index("\n    }\n")]
    assert 'body.count == 3, body["type"] as? String == "choose-file"' in handler
    assert 'let purpose = body["purpose"] as? String, filePanelMessage(purpose) != nil' in handler
    choose = _SHELL[_SHELL.index("func chooseFile"):]
    choose = choose[: choose.index("\n    }\n")]
    assert "panel.canChooseFiles = true" in choose and "panel.canChooseDirectories = false" in choose
    assert "self.answerFolder(request, path)" in choose
    page = (Path(macapp.__file__).parent / "app.html").read_text(encoding="utf-8")
    assert 'shellCan("choose-folder-share")) message.purpose = purpose;' in page
    assert '{ type: "choose-file", request: request, purpose: "key" }' in page
    team = (Path(macapp.__file__).parent / "team.js").read_text(encoding="utf-8")
    assert 'purpose === "key" ? shellCan("choose-file-key") : shellCan("choose-folder-share")' in team


def test_the_v6_shell_changes_the_build_id(tmp_path):
    """10.7: this rebuilds the shell -- the build-id hashes the Swift source,
    so the bundle built before V6 is not "up to date" and `researchos app` rebuilds
    it once."""
    rce = tmp_path / "rce"
    current = macapp.build_id(rce, 7357)
    old_source = subprocess.run(
        ["git", "show", "c98cf5a:src/rce/webapp/shell/RCEShell.swift"],
        cwd=Path(__file__).parents[1], capture_output=True,
    )
    if old_source.returncode != 0:
        pytest.skip("the pre-V6 shell source is not in this checkout's history")
    assert old_source.stdout != macapp.SHELL_SOURCE.read_bytes()
    original = macapp.SHELL_SOURCE
    staged = tmp_path / "RCEShell.swift"
    staged.write_bytes(old_source.stdout)
    try:
        macapp.SHELL_SOURCE = staged
        before = macapp.build_id(rce, 7357)
    finally:
        macapp.SHELL_SOURCE = original
    assert before != current


def test_shell_window_geometry_and_quit_on_close():
    assert "width: 1280, height: 840" in _SHELL
    assert "NSSize(width: 900, height: 600)" in _SHELL
    assert 'setFrameAutosaveName("RCEMain")' in _SHELL
    assert 'window.title = "Research OS"' in _SHELL
    assert "applicationShouldTerminateAfterLastWindowClosed" in _SHELL


def test_shell_bridge_uses_only_fixed_shapes():
    """Native -> page: one fixed JS string whose only variable part is a
    whitelisted name checked right before use. Page -> native: one handler,
    one message shape, main frame of the engine's origin only."""
    send = _SHELL[_SHELL.index("@objc func sendCommand"):]
    send = send[: send.index("\n    }\n")]
    assert "shellCommands.contains(name)" in send
    assert "evaluateJavaScript(\"window.RCE && RCE.command('\\(name)')\"" in send
    assert send.index("shellCommands.contains(name)") < send.index("evaluateJavaScript")
    assert _SHELL.count("evaluateJavaScript(") == 3  # command + reload + a fixed dirty-state probe
    assert 'evaluateJavaScript("!!(window.ROWorkspace && ROWorkspace.isDirty && ROWorkspace.isDirty())")' in _SHELL
    assert 'add(self, name: "rce")' in _SHELL
    handler = _SHELL[_SHELL.index("didReceive message: WKScriptMessage"):]
    assert 'body["type"] as? String == "title"' in handler
    assert "message.frameInfo.isMainFrame" in handler and "origin.port == port" in handler
    assert "sanitizedLabel(text)" in handler and '"Research OS — " + label' in handler


def test_shell_reads_its_configuration_at_runtime_and_never_enables_devtools():
    assert 'resourceText("engine-path")' in _SHELL and 'resourceText("engine-port")' in _SHELL
    assert '["serve", "--port", String(port), "--no-browser"]' in _SHELL
    assert '"api/projects"' in _SHELL and '"api/shutdown"' in _SHELL
    assert "developerExtrasEnabled" not in _SHELL and "isInspectable" not in _SHELL


def test_shell_quit_names_its_own_child_in_the_shutdown_request():
    """DESIGN.md 8.9 "an engine the user started from a terminal is left
    alone": the shutdown carries the spawned child's pid, so an engine
    that holds the port but is NOT our child refuses it (server side:
    `_check_shutdown_target`). Adversarial review of the V4 work."""
    quit_path = _SHELL[_SHELL.index("func stopEngineForQuit()"):]
    quit_path = quit_path[: quit_path.index("\n    }\n")]
    assert 'guard let process = child, process.isRunning' in quit_path
    assert '\\"pid\\": \\(process.processIdentifier)' in quit_path
    assert 'Data("{}".utf8)' not in quit_path


def test_shell_checks_for_unsaved_writing_before_quit_or_window_close():
    quit_path = _SHELL[_SHELL.index("func applicationShouldTerminate(_ sender"):]
    quit_path = quit_path[:quit_path.index("\n    }\n")]
    assert "confirmUnsaved {" in quit_path
    assert "if !allowed { NSApp.reply(toApplicationShouldTerminate: false); return }" in quit_path
    assert quit_path.index("if !allowed") < quit_path.index("self.stopEngineForQuit()")
    close = _SHELL[_SHELL.index("func windowShouldClose"):]
    close = close[:close.index("\n    }\n")]
    assert "confirmUnsaved {" in close
    assert "if allowed { self.discardConfirmed = true; self.window.performClose(nil) }" in close
    assert "return false" in close
    confirmation = _SHELL[_SHELL.index("func confirmUnsaved"):]
    confirmation = confirmation[:confirmation.index("\n    }\n")]
    assert "completion(alert.runModal() == .alertSecondButtonReturn)" in confirmation
    assert 'alert.addButton(withTitle: self.tr("继续编辑"))' in confirmation
    assert 'alert.addButton(withTitle: self.tr("放弃修改"))' in confirmation


def test_shell_placeholder_shows_the_log_only_as_escaped_text():
    assert "正在启动引擎…" in _SHELL and "#F7F2E9" in _SHELL
    page = _SHELL[_SHELL.index("func placeholderPage"):]
    page = page[: page.index("\n}\n")]
    assert "<pre>\\(htmlEscape(logText))</pre>" in page
    # Every interpolation into the markup is escaped text, except the one
    # that inserts `body` -- itself built only from escaped pieces.
    assert sorted(set(re.findall(r"\\\((\w+)", page))) == ["body", "htmlEscape"]
    assert page.count("\\(body)") == 1
    assert "\\(htmlEscape(headline))" in page and "\\(htmlEscape(detail))" in page


def test_shell_stops_only_an_engine_it_spawned():
    quit_ = _SHELL[_SHELL.index("func applicationShouldTerminate"):]
    quit_ = quit_[: quit_.index("// MARK: menus")]
    assert quit_.lstrip().startswith("func applicationShouldTerminate")
    assert "guard let process = child, process.isRunning else { return .terminateNow }" in quit_


# -- The engine session (DESIGN.md 18.1 item 1, task V14 G1-2) --------------------------
# The engine asks every write for the secret it keeps in <home>/engine/<port>.session.
# The shell reads that file once the probe answers, loads /boot?session=... first and
# sends the header on its one POST. Pinned in the Swift text here; the section that
# does the reading is compiled and run against the engine's own file at the end.


def _method(name: str) -> str:
    """One AppDelegate method, from `func name(` to its closing brace."""
    start = _SHELL.index(f"    func {name}(")
    return _SHELL[start:_SHELL.index("\n    }\n", start)]


def _session_section() -> str:
    return _SHELL[_SHELL.index("// -- The engine session"):_SHELL.index("// -- end of the engine session")]


def test_shell_reads_the_session_file_from_its_own_home_once_the_probe_answers():
    section = _session_section()
    # the file researchos.paths.engine_session_path names: <home>/engine/<port>.session
    assert f'let sessionDirectory = "{paths.ENGINE_DIRNAME}"' in section
    assert 'let sessionSuffix = ".session"' in section
    assert '.appendingPathComponent("\\(port)\\(sessionSuffix)", isDirectory: false)' in section
    # in the home the shell already computes for serve.log and settings.json
    assert "var sessionFileURL: URL { engineSessionFile(home: rceHome(), port: port) }" in _SHELL
    assert "engineSession(at: sessionFileURL)" in _method("engineSecret")
    # the page is entered only after a probe has answered
    start = _method("startEngine")
    assert "if ok { self.loadApp(); return }" in start
    poll = _method("pollTick")
    assert poll.index("if ok {") < poll.index("self.loadApp()")
    assert _SHELL.count("loadApp()") == 3  # the definition and those two
    assert _SHELL.count("entryURL()") == 2  # the definition and loadApp


def test_shell_loads_boot_with_the_session_and_the_plain_address_without_one():
    entry = _method("entryURL")
    assert ("guard let secret = engineSecret(), let boot = bootURL(base: baseURL, secret: secret) "
            "else { return baseURL }") in entry
    assert entry.count("return ") == 2 and entry.rstrip().endswith("return boot")
    load = _method("loadApp")
    assert "webView.load(URLRequest(url: entryURL()))" in load and "baseURL" not in load
    # the secret rides in the query, percent-encoded, to the engine's /boot and nowhere else
    section = _session_section()
    assert _SHELL.count('"boot?session="') == 1
    assert 'URL(string: "boot?session=" + encoded, relativeTo: base)' in section
    assert "secret.addingPercentEncoding(withAllowedCharacters: sessionQueryAllowed)" in section
    assert 'charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"' in section
    # the load passes the navigation policy (same host and port; the redirect to / does too)
    assert 'url.scheme == "http" && url.host == "127.0.0.1" && url.port == port' in _method("isEngineURL")


def test_shell_sends_the_session_header_on_its_shutdown_and_on_every_post():
    assert f'let sessionHeader = "{server.SESSION_HEADER}"' in _SHELL
    quit_path = _method("stopEngineForQuit")
    line = "if let secret = engineSecret() { request.setValue(secret, forHTTPHeaderField: sessionHeader) }"
    assert line in quit_path
    assert quit_path.index(line) < quit_path.index("session.dataTask(with: request)")
    # an engine without a file (an older one) is sent the request as before: nothing else changed
    assert 'request.httpMethod = "POST"' in quit_path
    assert '\\"pid\\": \\(process.processIdentifier)' in quit_path
    # the shell makes no other request with a method of its own; whichever it made would need the header
    code = _code_without_comments(_SHELL)
    assert code.count("httpMethod =") == 1 and code.count("forHTTPHeaderField: sessionHeader") == 1
    # and it goes only to the engine's own address
    assert 'baseURL.appendingPathComponent("api/shutdown")' in quit_path


def test_shell_never_logs_prints_or_shows_the_session():
    """The app's serve.log is readable by any program of the user (18.1), and so is a
    waiting page's text: the secret is read where it is used and goes nowhere else."""
    code = _code_without_comments(_SHELL)
    for sink in ("print(", "NSLog(", "debugPrint(", "dump(", "os_log(", "Logger(", "write("):
        assert sink not in code, sink
    # the child's environment gets one addition, and its arguments are pinned elsewhere
    assert re.findall(r'environment\["(\w+)"\] =', code) == ["PYTHONUNBUFFERED"]
    # the secret is read in two places only, and mentioned in no other function
    assert code.count("engineSecret()") == 3  # the definition, entryURL, stopEngineForQuit
    rest = code.replace(_code_without_comments(_session_section()), "")
    rest = rest.replace("    var sessionFileURL: URL { engineSessionFile(home: rceHome(), port: port) }\n", "")  # a path
    for name in ("engineSecret", "entryURL", "stopEngineForQuit"):
        rest = rest.replace(_code_without_comments(_method(name)), "")
    for word in ("secret", "sessionHeader", "engineSession", "bootURL", "sessionFileURL"):
        assert word not in rest, word
    # the page is not handed it by script either: its scripts are fixed text
    assert _SHELL.count("evaluateJavaScript(") == 3 and _SHELL.count("callAsyncJavaScript(") == 1


# -- cli wiring: `researchos app` -----------------------------------------------------


def test_cli_app_with_dir_builds_anywhere_and_prints_the_fallback_notice(tmp_path, monkeypatch, capsys):
    """`--dir` works on every platform; without swiftc the output carries
    the one-line notice, where the bundle went, and the usage hint."""
    rce = _fake_rce(tmp_path)
    monkeypatch.setattr(macapp.sys, "executable", str(rce.parent / "python"))
    monkeypatch.setattr(macapp, "find_swiftc", lambda: None)
    target = tmp_path / "apps"

    assert cli.main(["app", "--dir", str(target)]) == 0

    assert (target / "Research OS.app" / "Contents" / "Info.plist").is_file()
    out = capsys.readouterr().out
    assert macapp.NOTICE_NO_SWIFTC in out
    assert str(target / "Research OS.app") in out
    assert "双击" in out


def test_cli_app_without_dir_on_non_macos_errors_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(macapp, "is_macos", lambda: False)
    assert cli.main(["app"]) == 1
    err = capsys.readouterr().err
    assert "Error" in err and "macOS" in err and "--dir" in err  # names the way out


def _stub_build_app(monkeypatch) -> list[tuple[Path, int]]:
    calls: list[tuple[Path, int]] = []

    def fake(target_dir, executable=None, port=macapp.DEFAULT_PORT, force=False):
        calls.append((target_dir, port))
        return macapp.AppBuild(bundle=target_dir / "Research OS.app", native=True)

    monkeypatch.setattr(cli.macapp, "build_app", fake)
    return calls


def test_cli_app_without_dir_on_macos_targets_home_applications(tmp_path, monkeypatch):
    """The default install location is ~/Applications and the default port
    7357 -- observed via a stubbed build_app (nothing must be written into
    the real home)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(macapp, "is_macos", lambda: True)
    calls = _stub_build_app(monkeypatch)

    assert cli.main(["app"]) == 0

    assert calls == [(home / "Applications", 7357)]


def test_cli_app_hidden_port_reaches_the_build(tmp_path, monkeypatch, capsys):
    calls = _stub_build_app(monkeypatch)
    assert cli.main(["app", "--dir", str(tmp_path), "--port", str(_TRIAL_PORT)]) == 0
    assert calls == [(tmp_path.resolve(), _TRIAL_PORT)]
    assert "引擎没在运行时会自动启动" in capsys.readouterr().out


def test_cli_app_rejects_an_out_of_range_port(tmp_path, monkeypatch, capsys):
    _stub_build_app(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        cli.main(["app", "--dir", str(tmp_path), "--port", "70000"])
    assert exc.value.code == 2
    assert "65535" in capsys.readouterr().err


def test_cli_app_port_is_hidden_from_help(capsys):
    with pytest.raises(SystemExit):
        cli.main(["app", "--help"])
    out = capsys.readouterr().out
    assert "--dir" in out and "--port" not in out


def test_cli_app_reports_missing_entry_point_as_clean_error(tmp_path, monkeypatch, capsys):
    """A MacAppError (no rce next to the interpreter) surfaces as the
    standard 'Error: ...' line and exit 1, never a traceback."""
    monkeypatch.setattr(macapp.sys, "executable", str(tmp_path / "python"))
    assert cli.main(["app", "--dir", str(tmp_path / "apps")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error:") and "researchos" in err


# -- 9.12: an unchanged shell is not rebuilt (macOS would ask for Documents again) ----


def _fake_native_build(monkeypatch) -> list[str]:
    """A stand-in toolchain: `swiftc` "compiles" by writing a file, codesign
    succeeds, no iconutil. Returns the list of programs run, in order."""
    ran: list[str] = []

    def run(args):
        ran.append(Path(args[0]).name)
        if "-o" in args:
            out = Path(args[args.index("-o") + 1])
            out.write_bytes(b"\xcf\xfa\xed\xfe fake binary")
            out.chmod(0o755)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(macapp, "find_swiftc", lambda: "/usr/bin/swiftc")
    monkeypatch.setattr(macapp, "_run", run)
    monkeypatch.setattr(macapp.shutil, "which", lambda name: "/usr/bin/codesign" if name == "codesign" else None)
    return ran


def test_build_id_hashes_every_input_of_a_native_build(tmp_path, monkeypatch):
    rce = _fake_rce(tmp_path)
    base = macapp.build_id(rce, 7357)
    assert base.startswith("sha256:") and base == macapp.build_id(rce, 7357)
    assert macapp.build_id(rce, 7358) != base
    assert macapp.build_id(rce.parent / "other-rce", 7357) != base
    shell = tmp_path / "RCEShell.swift"
    shell.write_text(macapp.SHELL_SOURCE.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
    monkeypatch.setattr(macapp, "SHELL_SOURCE", shell)
    assert macapp.build_id(rce, 7357) != base
    monkeypatch.undo()
    icon = tmp_path / "RCEIcon.swift"
    icon.write_text("// another icon\n", encoding="utf-8")
    monkeypatch.setattr(macapp, "ICON_SOURCE", icon)
    assert macapp.build_id(rce, 7357) != base
    monkeypatch.undo()
    monkeypatch.setattr(macapp, "_package_version", lambda: "99.0")  # a plist field
    assert macapp.build_id(rce, 7357) != base


def test_an_unchanged_bundle_is_left_untouched_and_force_rebuilds_it(tmp_path, monkeypatch, capsys):
    rce = _fake_rce(tmp_path)
    monkeypatch.setattr(macapp.sys, "executable", str(rce.parent / "python"))
    ran = _fake_native_build(monkeypatch)
    target = tmp_path / "apps"
    first = macapp.build_app(target, executable=rce, port=_TRIAL_PORT)
    assert first.native and not first.up_to_date and "swiftc" in ran and "codesign" in ran
    bundle = target / "Research OS.app"
    binary = bundle / "Contents" / "MacOS" / "ResearchOS"
    recorded = (bundle / "Contents" / "Resources" / macapp.BUILD_ID_SIDECAR).read_text(encoding="utf-8")
    assert recorded == macapp.build_id(rce, _TRIAL_PORT)
    stamp = (binary.stat().st_ino, binary.stat().st_mtime_ns)
    ran.clear()
    again = macapp.build_app(target, executable=rce, port=_TRIAL_PORT)
    assert again.up_to_date and again.native and ran == []  # nothing compiled, nothing re-signed
    assert (binary.stat().st_ino, binary.stat().st_mtime_ns) == stamp
    assert cli.main(["app", "--dir", str(target), "--port", str(_TRIAL_PORT)]) == 0
    assert "Research OS.app is up to date" in capsys.readouterr().out and ran == []
    assert cli.main(["app", "--dir", str(target), "--port", str(_TRIAL_PORT), "--force"]) == 0
    assert "up to date" not in capsys.readouterr().out and "swiftc" in ran and "codesign" in ran
    ran.clear()
    # A changed input (here the port) rebuilds; so does a bundle without a build-id (the V3 launcher).
    assert not macapp.build_app(target, executable=rce, port=_TRIAL_PORT + 1).up_to_date and "swiftc" in ran
    launcher = tmp_path / "launcher"
    macapp.generate_bundle(launcher, executable=rce)
    assert not macapp.is_up_to_date(launcher / "Research OS.app", macapp.build_id(rce, macapp.DEFAULT_PORT))


def test_shell_waiting_page_names_the_documents_prompt_and_shows_the_log_only_after_an_exit():
    """9.12: after a few seconds without an answer the waiting page says
    the system may ask for the Documents folder; the log tail is shown only
    once the engine process has EXITED; the child's output is unbuffered."""
    # As Swift source: the quotes inside the string literal are escaped.
    assert r'"正在启动引擎… 如果系统询问是否允许 Research OS 访问\"文稿\"文件夹，请点\"允许\"。"' in _SHELL
    assert 'environment["PYTHONUNBUFFERED"] = "1"' in _SHELL
    poll = _SHELL[_SHELL.index("func pollTick"):_SHELL.index("func showLog")]
    assert "showLog" not in poll and "documentsHint" in poll
    exited = _SHELL[_SHELL.index("func engineExited"):_SHELL.index("func schedulePolling")]
    assert "showLog" in exited


# -- Two languages (DESIGN.md 13.2, 13.3, 13.4, 13.7) ----------------------------------
# The Chinese stays in the Swift source as the key; `englishText` holds the
# English. These read both tables out of the source; the native test below
# compiles the language rules on their own and runs them.

_SWIFT_STRING = r'"(?:[^"\\\n]|\\.)*"'
_CJK = re.compile(r"[　-〿一-鿿＀-￯]")
_DOCUMENTS_HINT = re.search(r"^let documentsHint = (" + _SWIFT_STRING + ")$", _SHELL, re.M).group(1)
_TABLE_START = "let englishText: [String: String] = ["
_ENGLISH_MENUS = {
    "关于 Research OS": "About Research OS", "退出": "Quit Research OS",
    "文件": "File", "添加项目…": "Add Project…", "提交提议…": "Submit Proposal…", "发布": "Publish",
    "新建项目…": "New Project…", "项目首页": "Project Home",
    "关闭": "Close",
    "编辑": "Edit", "撤销": "Undo", "重做": "Redo", "剪切": "Cut", "拷贝": "Copy", "粘贴": "Paste",
    "全选": "Select All",
    "视图": "View", "工作台": "Desk", "研究路线": "Research Routes", "成果": "Outputs", "变量": "Variables",
    "文献": "Literature", "数据与代码": "Data & Code", "提议": "Proposals", "成员": "Members",
    "重新载入": "Reload", "放大": "Zoom In", "缩小": "Zoom Out", "实际大小": "Actual Size",
    "窗口": "Window", "最小化": "Minimize", "缩放": "Zoom",
    "帮助": "Help", "打开项目地图": "Open Project Map",
}


def _swift_value(literal: str) -> str:
    assert not re.search(r'\\[^"]', literal), literal  # the shell's texts escape only quotes
    return literal[1:-1].replace('\\"', '"')


def _code_without_comments(src: str) -> str:
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("//"))


def _english_entries() -> list[tuple[str, str]]:
    block = _SHELL[_SHELL.index(_TABLE_START) + len(_TABLE_START):]
    block = _code_without_comments(block[: block.index("\n]\n")])
    entry = re.compile(r"(documentsHint|" + _SWIFT_STRING + r")\s*:\s*(" + _SWIFT_STRING + "),")
    assert entry.sub("", block).strip() == "", "an entry of englishText was not read"
    return [(_swift_value(_DOCUMENTS_HINT if key == "documentsHint" else key), _swift_value(value))
            for key, value in entry.findall(block)]


def _english_table() -> dict[str, str]:
    return dict(_english_entries())


def test_shell_english_table_covers_every_text_the_shell_writes():
    """13.6: every Chinese text the shell puts on screen -- menu titles, the
    waiting page, the log lines, alert buttons, panel prompt and messages --
    is shown through the table, and the table holds nothing else. A
    duplicate key would stop the app at its first menu (Swift traps)."""
    entries = _english_entries()
    keys = [key for key, _ in entries]
    assert len(keys) == len(set(keys))
    code = _code_without_comments(_SHELL)
    outside = code[: code.index(_TABLE_START)] + code[code.index("\n]\n", code.index(_TABLE_START)):]
    shown = set()
    for match in re.finditer(_SWIFT_STRING, outside):
        if not _CJK.search(match.group(0)):
            continue
        text = _swift_value(match.group(0))
        before = outside[max(0, match.start() - 40): match.start()]
        assert re.search(r'(\btr\(|\b(?:item|cmd|submenu)\(|case "[a-z]+": return |^let documentsHint = )$',
                         before, re.M), f"Chinese shown without the table: {text}"
        shown.add(text)
    assert "self.tr(documentsHint)" in _SHELL
    assert _SHELL.count("panel.message = tr(panelMessage)") == 2 and _SHELL.count('panel.prompt = tr("选择")') == 2
    assert set(keys) == shown


def test_shell_english_follows_the_glossary_and_macos_names():
    """13.4: the menus in macOS's title case and standard names, the eight
    sections in the glossary's words; every English text free of Chinese,
    emoji and arrows (12.11), with “ ” for quotation marks."""
    table = _english_table()
    for zh, en in _ENGLISH_MENUS.items():
        assert table[zh] == en, zh
        assert all(word[0].isupper() or word == "&" for word in en.split()), en
    titles = {t for t in re.findall(r'(?:item|cmd|submenu)\("([^"]+)"', _SHELL) if _CJK.search(t)}
    assert titles == set(_ENGLISH_MENUS)
    design = (Path(__file__).parents[1] / "DESIGN.md").read_text(encoding="utf-8")
    glossary = dict(re.findall(r"^\| (\S+) \| ([^|]+?) \|$", design[design.index("### 13.4"):], re.M))
    for section in ("工作台", "研究路线", "成果", "变量", "文献", "数据与代码", "提议", "成员"):
        title_case = " ".join(word[:1].upper() + word[1:] for word in glossary[section].split(" "))
        assert table[section] == title_case, section
    assert (table["好"], table["取消"], table["选择"]) == ("OK", "Cancel", "Choose")
    for zh, en in table.items():
        assert en.strip() and not _CJK.search(en) and not FORBIDDEN.search(en), en
        assert "..." not in en and '"' not in en and "'" not in en, en
        assert en.count("{path}") == zh.count("{path}") <= 1, en
    assert "“Documents” folder, click “Allow”." in table[_swift_value(_DOCUMENTS_HINT)]


def test_shell_reads_the_language_from_settings_and_otherwise_follows_the_mac():
    """13.2: `interface_language` in settings.json in the Research OS home,
    read at launch before the menus are built; without a valid choice, the
    Mac's first preferred language. The waiting page says its language."""
    assert 'let settingsFilename = "settings.json"' in _SHELL
    assert 'let interfaceLanguageKey = "interface_language"' in _SHELL
    saved = _SHELL[_SHELL.index("func savedInterfaceLanguage"):]
    saved = saved[: saved.index("\n}\n")]
    assert "JSONSerialization.jsonObject(with: data)" in saved and "as? [String: Any]" in saved
    # Only the bytes the engine reads too (SETTINGS_BYTES): UTF-8, never UTF-16 or UTF-32.
    assert saved.index("String(data: data, encoding: .utf8) != nil, !data.contains(0),") < saved.index("JSONSerialization")
    assert 'language == "zh" || language == "en" else { return nil }' in saved
    current = _SHELL[_SHELL.index("func currentInterfaceLanguage"):]
    current = current[: current.index("\n}\n")]
    assert "rceHome().appendingPathComponent(settingsFilename)" in current
    assert "preferred: Locale.preferredLanguages" in current
    launch = _SHELL[_SHELL.index("func applicationDidFinishLaunching"):]
    launch = launch[: launch.index("\n    }\n")]
    assert launch.index("language = currentInterfaceLanguage()") < launch.index("NSApp.mainMenu = buildMainMenu()")
    assert launch.index("NSApp.mainMenu") < launch.index("startEngine()")
    page = _SHELL[_SHELL.index("func placeholderPage"):]
    page = page[: page.index("\n}\n")]
    assert '<html lang="\\(htmlEscape(htmlLang))">' in page and 'lang="zh-CN"' not in page
    assert "htmlLang: htmlLanguage(language)" in _SHELL
    menus = _SHELL[_SHELL.index("func item(_ title"):_SHELL.index("func buildMainMenu")]
    assert "NSMenuItem(title: tr(title), action: action" in menus
    assert 'NSMenuItem(title: tr(title), action: nil, keyEquivalent: "")' in menus
    assert "NSMenu(title: tr(title))" in menus


def test_shell_takes_a_language_message_and_rereads_the_file():
    """13.3: one more bridge shape, `{type: "language"}` with exactly that
    one key; the shell re-reads settings.json instead of trusting the
    message, and rebuilds its menus -- which recreates 提交提议… / 发布 and
    re-applies the team mode. Every other shape is as before."""
    handler = _SHELL[_SHELL.index("didReceive message: WKScriptMessage"):]
    handler = handler[: handler.index("\n    }\n")]
    assert set(re.findall(r'body\["type"\] as\? String == "([a-z-]+)"', handler)) == {
        "title", "mode", "choose-folder", "choose-file", "language"}
    branch = handler[handler.index('body.count == 1, body["type"] as? String == "language" {'):]
    assert branch.split("\n")[1].strip() == "applyLanguage()"
    apply = _SHELL[_SHELL.index("func applyLanguage"):]
    apply = apply[: apply.index("\n    }\n")]
    assert "language = currentInterfaceLanguage()" in apply and "NSApp.mainMenu = buildMainMenu()" in apply
    assert "body" not in apply and "message" not in apply.split("\n", 1)[1]
    build = _SHELL[_SHELL.index("func buildMainMenu"):]
    build = build[: build.index("\n    }\n")]
    assert build.index('publishItem = cmd("发布", "publish")') < build.index("applyTeamMode()")
    assert "NSApp.windowsMenu = windowMenu.submenu" in build and "NSApp.helpMenu = helpMenu.submenu" in build


def test_shell_tells_the_page_the_macs_language_at_document_start():
    """13.2 (amended): inside the shell the page does not trust
    navigator.languages, so the shell injects its own answer from
    Locale.preferredLanguages, beside RCEShellFeatures, at document start in
    the main frame: `window.ROI18N_SYSTEM = "zh"` or `"en"`, from the same
    rule as its menus but with no saved choice (the page reads that from
    /i18n.js on every load). The value is one of two fixed words, never
    text from outside the shell."""
    section = _SHELL[_SHELL.index("// -- Two languages"):_SHELL.index("// -- end of the two languages")]
    script = section[section.index("func systemLanguageScript"):]
    script = script[: script.index("\n}\n")]
    assert script.split("\n")[1:] == [
        '    "window.ROI18N_SYSTEM = \\"" + interfaceLanguage(saved: nil, preferred: preferred) + "\\";"']
    window = _SHELL[_SHELL.index("func buildWindow"):]
    window = window[: window.index("\n    }\n")]
    features = window.index("source: shellFeaturesScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))")
    system = window.index("config.userContentController.addUserScript(WKUserScript(\n"
                          "            source: systemLanguageScript(preferred: Locale.preferredLanguages),\n"
                          "            injectionTime: .atDocumentStart, forMainFrameOnly: true))")
    assert features < system < window.index("webView = WKWebView(frame:")
    assert window.count("addUserScript(") == 2 and _SHELL.count("systemLanguageScript(") == 2


#: settings.json files that the shell (Foundation) and the engine (`researchos.settings.read_json`)
#: must read alike (13.2: one source of truth), as bytes, with the language each must find. The same
#: table runs on both sides: here through the compiled shell section, and in tests/test_v9_language.py
#: through the engine.
SETTINGS_BYTES = {
    "bom-utf8.json": (b"\xef\xbb\xbf" + b'{"interface_language": "en"}', "en"),
    "bom-utf16.json": ('{"interface_language": "en"}'.encode("utf-16"), "none"),
    "utf16le.json": ('{"interface_language": "en"}'.encode("utf-16-le"), "none"),
    "repeated-en-zh.json": (b'{"interface_language": "en", "interface_language": "zh"}', "en"),
    "repeated-zh-en.json": (b'{"interface_language": "zh", "interface_language": "en"}', "zh"),
    "nan.json": (b'{"interface_language": "en", "x": NaN}', "none"),
    "infinity.json": (b'{"interface_language": "en", "x": Infinity}', "none"),
    "surrogate.json": (b'{"interface_language": "en", "x": "\\ud800"}', "none"),
    "deep.json": (b'{"interface_language": "en", "x": ' + b"[" * 100000 + b"]" * 100000 + b"}", "none"),
}

_LANGUAGE_HARNESS = r"""
let dir = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
var out: [String: String] = [:]
for name in NAMES {
    out["saved " + name] = savedInterfaceLanguage(dir.appendingPathComponent(name)) ?? "none"
}
let macs: [String: [String]] = [
    "zh-Hans": ["zh-Hans-CN", "en-US"], "zh-Hant": ["zh-Hant-TW"], "zh": ["zh"],
    "en": ["en-US", "zh-Hans-CN"], "de": ["de-DE"], "empty": [],
]
for (mac, preferred) in macs {
    out["mac " + mac] = interfaceLanguage(saved: nil, preferred: preferred)
    out["page " + mac] = systemLanguageScript(preferred: preferred)
}
out["zh chosen on an English Mac"] = interfaceLanguage(saved: "zh", preferred: ["en-US"])
out["en chosen on a Chinese Mac"] = interfaceLanguage(saved: "en", preferred: ["zh-Hans-CN"])
out["html zh"] = htmlLanguage("zh")
out["html en"] = htmlLanguage("en")
let path = "/a/{path}/日志 \"x\"/serve.log"
for language in ["zh", "en"] {
    out[language + " log"] = shellText("以下是 {path} 的最后几行（⌘R 重试）：", language, path: path)
    out[language + " no log"] = shellText("还没有日志文件：{path}", language, path: path)
    out[language + " desk"] = shellText("工作台", language)
    out[language + " missing"] = shellText("没有这一条", language)
    out[language + " hint"] = shellText(documentsHint, language)
}
out["zh is every key itself"] = englishText.keys.allSatisfy { shellText($0, "zh") == $0 } ? "yes" : "no"
out["entries"] = String(englishText.count)
let data = try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])
FileHandle.standardOutput.write(data)
"""


@native_only
def test_shell_language_rules_run_as_swift(tmp_path):
    """The language section of RCEShell.swift, compiled on its own with a
    small harness: the saved choice and the Mac's languages (13.2), the
    Chinese byte for byte as the old concatenations, the English, the
    fallback to the Chinese, and `{path}` filled once."""
    settings = {
        "en.json": '{"doi_online_lookup": true, "interface_language": "en"}',
        "zh.json": '{"interface_language": "zh"}',
        "fr.json": '{"interface_language": "fr"}',
        "upper.json": '{"interface_language": "EN"}',
        "number.json": '{"interface_language": 1}',
        "absent.json": '{"doi_online_lookup": true}',
        "list.json": '["en"]',
        "broken.json": '{"interface_language": "en"',
        "empty.json": "",
    }
    folder = tmp_path / "settings"
    folder.mkdir()
    for name, text in settings.items():
        (folder / name).write_text(text, encoding="utf-8")
    for name, (raw, _lang) in SETTINGS_BYTES.items():
        (folder / name).write_bytes(raw)
    names = [*settings, *SETTINGS_BYTES, "missing.json"]
    section = _SHELL[_SHELL.index("// -- Two languages"):_SHELL.index("// -- end of the two languages")]
    harness = tmp_path / "main.swift"
    harness.write_text("import Foundation\nlet documentsHint = " + _DOCUMENTS_HINT + "\n" + section
                       + _LANGUAGE_HARNESS.replace("NAMES", json.dumps(names)), encoding="utf-8")
    binary = tmp_path / "language"
    compiled = subprocess.run(["swiftc", "-target", macapp._swift_target(), "-o", str(binary), str(harness)],
                              capture_output=True, text=True, timeout=300)
    assert compiled.returncode == 0, compiled.stderr
    out = json.loads(subprocess.run([str(binary), str(folder)], capture_output=True, text=True,
                                    check=True, timeout=60).stdout)
    assert {name: out["saved " + name] for name in names} == {
        "en.json": "en", "zh.json": "zh", "fr.json": "none", "upper.json": "none", "number.json": "none",
        "absent.json": "none", "list.json": "none", "broken.json": "none", "empty.json": "none",
        "missing.json": "none", **{name: lang for name, (_raw, lang) in SETTINGS_BYTES.items()},
    }
    assert {k[4:]: v for k, v in out.items() if k.startswith("mac ")} == {
        "zh-Hans": "zh", "zh-Hant": "zh", "zh": "zh", "en": "en", "de": "en", "empty": "en"}
    # 13.2: what the page is told is the same answer, as one fixed line of script.
    assert {k[5:]: v for k, v in out.items() if k.startswith("page ")} == {
        mac: f'window.ROI18N_SYSTEM = "{out["mac " + mac]}";' for mac in ("zh-Hans", "zh-Hant", "zh", "en", "de", "empty")}
    assert out["zh chosen on an English Mac"] == "zh" and out["en chosen on a Chinese Mac"] == "en"
    assert (out["html zh"], out["html en"]) == ("zh-CN", "en")
    path = '/a/{path}/日志 "x"/serve.log'
    assert out["zh log"] == "以下是 " + path + " 的最后几行（⌘R 重试）："
    assert out["zh no log"] == "还没有日志文件：" + path
    assert out["en log"] == "The last lines of " + path + " (⌘R to try again):"
    assert out["en no log"] == "No log file yet: " + path
    assert (out["zh desk"], out["en desk"]) == ("工作台", "Desk")
    assert out["zh missing"] == out["en missing"] == "没有这一条"
    assert out["zh hint"] == _swift_value(_DOCUMENTS_HINT)
    assert out["en hint"] == _english_table()[_swift_value(_DOCUMENTS_HINT)]
    assert out["zh is every key itself"] == "yes"
    assert out["entries"] == str(len(_english_entries()))


# -- The engine session, compiled and run (18.1) -------------------------------------------
# The section of RCEShell.swift that reads the session file and builds the /boot address,
# compiled on its own with a small harness, and run against what the engine really writes
# (`server.write_session_file`) and answers (`GET /boot` of a real server).

_SESSION_HARNESS = r"""
let arguments = CommandLine.arguments
let home = URL(fileURLWithPath: arguments[1], isDirectory: true)
let port = Int(arguments[2])!
let base = URL(string: arguments[3])!
let folder = URL(fileURLWithPath: arguments[4], isDirectory: true)
let secrets = try! JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: arguments[5]))) as! [String]
var out: [String: Any] = [:]
let file = engineSessionFile(home: home, port: port)
out["file"] = file.path
out["secret"] = engineSession(at: file) ?? NSNull()
var files: [String: Any] = [:]
for name in try! FileManager.default.contentsOfDirectory(atPath: folder.path) + ["no such file"] {
    files[name] = engineSession(at: folder.appendingPathComponent(name)) ?? NSNull()
}
out["files"] = files
var boots: [String: Any] = [:]
for secret in secrets {
    boots[secret] = bootURL(base: base, secret: secret)?.absoluteString ?? NSNull()
}
out["boots"] = boots
FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys]))
"""

_HEX = "ab" * 32
#: Texts the shell must not take for a secret: it then loads the plain address and sends no header.
_SESSION_FILES = {
    "engine-form": (f"{_HEX}\n".encode(), _HEX),
    "crlf": (f"{_HEX}\r\n".encode(), _HEX),
    "no newline": (_HEX.encode(), _HEX),
    "padded": (f"  {_HEX}  \n".encode(), _HEX),
    "longest": (b"a" * 512 + b"\n", "a" * 512),
    "empty": (b"", None),
    "blank": (b"\n\n", None),
    "two words": (b"ab cd\n", None),
    "two lines": (b"ab\ncd\n", None),
    "tab inside": (b"ab\tcd\n", None),
    "non-ascii": ("sécret\n".encode(), None),
    "too long": (b"a" * 513 + b"\n", None),
    "not utf-8": (b"\xff\xfe\x00ab\n", None),
}
_BOOT_SECRETS = [_HEX, "a&b+c%d#e/f=g?h", "~._-AZaz09", "100%", "a+b"]


@pytest.fixture(scope="module")
def session_harness(tmp_path_factory):
    if not _HAS_SWIFTC:
        pytest.skip("needs macOS with swiftc")
    folder = tmp_path_factory.mktemp("session-harness")
    main = folder / "main.swift"
    main.write_text("import Foundation\n" + _session_section() + _SESSION_HARNESS, encoding="utf-8")
    binary = folder / "session"
    compiled = subprocess.run(["swiftc", "-target", macapp._swift_target(), "-o", str(binary), str(main)],
                              capture_output=True, text=True, timeout=300)
    assert compiled.returncode == 0, compiled.stderr
    return binary


def _run_session_harness(binary: Path, tmp_path: Path, home: Path, port: int, base: str) -> dict:
    files = tmp_path / "session-files"
    files.mkdir(exist_ok=True)
    for name, (raw, _expected) in _SESSION_FILES.items():
        (files / name).write_bytes(raw)
    (files / "a directory").mkdir(exist_ok=True)
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps(_BOOT_SECRETS), encoding="utf-8")
    done = subprocess.run([str(binary), str(home), str(port), base, str(files), str(secrets)],
                          capture_output=True, text=True, check=True, timeout=60)
    return json.loads(done.stdout)


@native_only
def test_shell_reads_the_file_the_engine_writes_and_nothing_it_would_misread(session_harness, tmp_path, monkeypatch):
    """The shell and `researchos serve` name the same file and agree on its text: what
    `write_session_file` wrote (mode 0600, a closing newline) is read back as the secret;
    no file, an empty one, a directory or text that is not one token is no session."""
    home = tmp_path / "home"
    monkeypatch.setenv("RESEARCHOS_HOME", str(home))
    secret = server.new_session()
    written = server.write_session_file(7391, secret)
    out = _run_session_harness(session_harness, tmp_path, home, 7391, "http://127.0.0.1:7391/")
    assert Path(out["file"]) == written == paths.engine_session_path(7391)
    assert out["secret"] == secret and len(secret) == 64
    assert {name: out["files"][name] for name in _SESSION_FILES} == {
        name: expected for name, (_raw, expected) in _SESSION_FILES.items()}
    assert out["files"]["a directory"] is None and out["files"]["no such file"] is None
    # an older engine writes no file: the other port of the same home has none
    other = _run_session_harness(session_harness, tmp_path, home, 7392, "http://127.0.0.1:7392/")
    assert other["secret"] is None and Path(other["file"]) == paths.engine_session_path(7392)


@native_only
def test_shell_boot_address_is_percent_encoded_and_reads_back_whole(session_harness, tmp_path):
    out = _run_session_harness(session_harness, tmp_path, tmp_path / "home", 7391, "http://127.0.0.1:7391/")
    assert out["boots"][_HEX] == f"http://127.0.0.1:7391/boot?session={_HEX}"
    assert out["boots"]["~._-AZaz09"].endswith("?session=~._-AZaz09")
    assert out["boots"]["a&b+c%d#e/f=g?h"].endswith("?session=a%26b%2Bc%25d%23e%2Ff%3Dg%3Fh")
    assert out["boots"]["a+b"].endswith("?session=a%2Bb")  # a literal + would be read as a space
    for secret, url in out["boots"].items():
        parts = urlsplit(url)
        assert (parts.scheme, parts.netloc, parts.path, parts.fragment) == ("http", "127.0.0.1:7391", "/boot", "")
        assert parse_qs(parts.query, strict_parsing=True) == {"session": [secret]}


@contextlib.contextmanager
def _engine_asking_for(session: str):
    httpd = server.build_server(None, 0, session=session)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@native_only
@pytest.mark.parametrize("secret", [_HEX, "a&b+c%d#e/f=g?h", "a+b"])
def test_the_address_the_shell_loads_makes_the_engine_set_its_cookie(session_harness, tmp_path, secret):
    """End to end on the wire: the /boot address the shell builds from a secret is the one a
    real engine that asks for that secret answers with the session cookie and a redirect to /."""
    with _engine_asking_for(secret) as port:
        out = _run_session_harness(session_harness, tmp_path, tmp_path / "home", port, f"http://127.0.0.1:{port}/")
        url = urlsplit(out["boots"][secret])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            conn.request("GET", url.path + "?" + url.query)
            response = conn.getresponse()
            response.read()
            headers = {k.lower(): v for k, v in response.getheaders()}
            assert response.status == 303 and headers["location"] == "/"
            assert headers["set-cookie"] == f"ro_session_{port}={secret}; HttpOnly; SameSite=Strict; Path=/"
            # and a boot address with another secret sets nothing
            conn.request("GET", "/boot?session=" + "0" * 64)
            wrong = conn.getresponse()
            wrong.read()
            assert wrong.status == 303 and wrong.getheader("Set-Cookie") is None
        finally:
            conn.close()
